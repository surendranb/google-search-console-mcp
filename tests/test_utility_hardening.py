"""Unit tests for GSC MCP Utility Hardening (SUR-471).

Tests:
1. site_url parameterization and auto-normalization (sc-domain: vs https://).
2. data_state / fresh_data parameter support (dataState='all' vs 'final').
3. get_data_status tool returning latest available date and lag guidance.
4. inspect_url tool parsing Google URL Inspection API verdicts and coverage.
5. compare_performance tool calculating server-side Period-over-Period deltas.
"""

import asyncio
import os
import sys
import types
import unittest
from unittest.mock import MagicMock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


def _install_stubs():
    # Stub google.* modules
    google_pkg = types.ModuleType("google")
    google_oauth2 = types.ModuleType("google.oauth2")
    google_oauth2_sa = types.ModuleType("google.oauth2.service_account")

    class _Creds:
        @classmethod
        def from_service_account_file(cls, *args, **kwargs):
            return cls()

    google_oauth2_sa.Credentials = _Creds
    google_oauth2.service_account = google_oauth2_sa
    google_pkg.oauth2 = google_oauth2

    sys.modules.setdefault("google", google_pkg)
    sys.modules["google.oauth2"] = google_oauth2
    sys.modules["google.oauth2.service_account"] = google_oauth2_sa

    googleapiclient = types.ModuleType("googleapiclient")
    googleapiclient_discovery = types.ModuleType("googleapiclient.discovery")
    googleapiclient_discovery.build = lambda *a, **kw: MagicMock()
    googleapiclient.discovery = googleapiclient_discovery
    sys.modules.setdefault("googleapiclient", googleapiclient)
    sys.modules["googleapiclient.discovery"] = googleapiclient_discovery

    fastmcp = types.ModuleType("fastmcp")

    class _FakeMCP:
        def __init__(self, *a, **kw):
            pass

        def tool(self, *a, **kw):
            def _decorator(fn):
                fn._mcp_annotations = kw.get("annotations", {})
                return fn
            return _decorator

    class _FakeContext:
        def __init__(self):
            self.session = None

    fastmcp.FastMCP = _FakeMCP
    fastmcp.Context = _FakeContext
    sys.modules["fastmcp"] = fastmcp


_install_stubs()
os.environ.setdefault("GA_CREDENTIALS_PATH", "/tmp/fake_sa.json")
os.environ.setdefault("GSC_SITE_URL", "https://default.example.com/")

import gsc_mcp_server as gsc


class TestGSCUtilityHardening(unittest.TestCase):
    def setUp(self):
        self.mock_service = MagicMock()
        gsc.get_gsc_service = lambda: self.mock_service

    def test_site_url_override_and_normalization(self):
        """get_search_analytics accepts site_url override and normalizes naked domain."""
        query_mock = MagicMock()
        self.mock_service.searchanalytics().query = query_mock
        query_mock.return_value.execute.return_value = {"rows": []}

        # Mock list_gsc_sites to return sc-domain
        self.mock_service.sites().list().execute.return_value = {
            "siteEntry": [
                {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"},
                {"siteUrl": "https://other.com/", "permissionLevel": "siteOwner"}
            ]
        }

        # 1. Custom explicit site_url
        asyncio.run(gsc.get_search_analytics(site_url="https://other.com/"))
        self.assertEqual(query_mock.call_args[1]["siteUrl"], "https://other.com/")

        # 2. Naked domain that matches sc-domain: in verified list
        asyncio.run(gsc.get_search_analytics(site_url="example.com"))
        self.assertEqual(query_mock.call_args[1]["siteUrl"], "sc-domain:example.com")

    def test_fresh_data_state_parameter(self):
        """get_search_analytics supports data_state='all' / fresh_data=True."""
        query_mock = MagicMock()
        self.mock_service.searchanalytics().query = query_mock
        query_mock.return_value.execute.return_value = {"rows": []}

        asyncio.run(gsc.get_search_analytics(data_state="all"))
        req_body = query_mock.call_args[1]["body"]
        self.assertEqual(req_body.get("dataState"), "all")

        asyncio.run(gsc.get_search_analytics(fresh_data=True))
        req_body2 = query_mock.call_args[1]["body"]
        self.assertEqual(req_body2.get("dataState"), "all")

    def test_get_data_status(self):
        """get_data_status returns frontier date and guidance to prevent date hunting."""
        query_mock = MagicMock()
        self.mock_service.searchanalytics().query = query_mock
        # Return mock date rows
        query_mock.return_value.execute.return_value = {
            "rows": [
                {"keys": ["2026-11-03"], "clicks": 100, "impressions": 1000},
                {"keys": ["2026-11-04"], "clicks": 120, "impressions": 1200},
                {"keys": ["2026-11-05"], "clicks": 90, "impressions": 950},
            ]
        }

        res = gsc.get_data_status(site_url="https://default.example.com/")
        self.assertIn("latest_available_date", res)
        self.assertEqual(res["latest_available_date"], "2026-11-05")
        self.assertIn("guidance", res)
        self.assertIn("2026-11-05", res["guidance"])

    def test_inspect_url(self):
        """inspect_url queries Google URL Inspection API and formats response."""
        inspect_mock = MagicMock()
        self.mock_service.urlInspection().index().inspect = inspect_mock
        inspect_mock.return_value.execute.return_value = {
            "inspectionResult": {
                "indexStatusResult": {
                    "verdict": "PASS",
                    "coverageState": "Submitted and indexed",
                    "indexingState": "INDEXING_ALLOWED",
                    "robotsTxtState": "ALLOWED",
                    "crawledAs": "MOBILE",
                    "lastCrawlTime": "2026-11-05T12:00:00Z",
                    "pageFetchState": "SUCCESSFUL",
                    "googleCanonical": "https://default.example.com/page",
                    "userCanonical": "https://default.example.com/page"
                },
                "mobileUsabilityResult": {
                    "verdict": "PASS"
                }
            }
        }

        res = gsc.inspect_url(url="https://default.example.com/page")
        self.assertIsInstance(res, dict)
        self.assertEqual(res["verdict"], "PASS")
        self.assertEqual(res["coverage_state"], "Submitted and indexed")
        self.assertEqual(res["indexing_state"], "INDEXING_ALLOWED")
        self.assertEqual(res["crawled_as"], "MOBILE")

    def test_compare_performance_period_over_period(self):
        """compare_performance computes server-side Period-over-Period deltas."""
        query_mock = MagicMock()
        self.mock_service.searchanalytics().query = query_mock

        # Call 1: Current window
        curr_resp = {
            "rows": [
                {"keys": ["kw1"], "clicks": 100, "impressions": 1000, "ctr": 0.10, "position": 3.0},
                {"keys": ["kw2"], "clicks": 50, "impressions": 500, "ctr": 0.10, "position": 5.0},
            ]
        }
        # Call 2: Prior window
        prior_resp = {
            "rows": [
                {"keys": ["kw1"], "clicks": 80, "impressions": 800, "ctr": 0.10, "position": 4.0},
                {"keys": ["kw2"], "clicks": 70, "impressions": 700, "ctr": 0.10, "position": 4.0},
            ]
        }
        query_mock.return_value.execute.side_effect = [curr_resp, prior_resp]

        res = gsc.compare_performance(
            current_start="2026-10-01",
            current_end="2026-10-31",
            prior_start="2026-09-01",
            prior_end="2026-09-30",
            dimensions=["query"],
            site_url="https://default.example.com/"
        )

        self.assertIn("summary", res)
        self.assertEqual(res["summary"]["current_clicks"], 150)
        self.assertEqual(res["summary"]["prior_clicks"], 150)
        self.assertEqual(res["summary"]["clicks_delta"], 0)

        data = res["comparison"]
        # kw1 had +20 clicks (+25.0%)
        kw1 = next(r for r in data if r["query"] == "kw1")
        self.assertEqual(kw1["clicks_current"], 100)
        self.assertEqual(kw1["clicks_prior"], 80)
        self.assertEqual(kw1["clicks_delta"], 20)
        self.assertEqual(kw1["clicks_pct_change"], 25.0)


if __name__ == "__main__":
    unittest.main()
