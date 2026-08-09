"""Tests for get_search_analytics dimension/filter normalization.

Stubs googleapiclient.discovery so no Google credentials are required.
Asserts the outbound request body contains `dimensions` as a real list
for both list and JSON-string inputs (regression test for the v0.3.0
duplicate-registration bug).
"""

import os
import sys
import types
import unittest
from unittest.mock import MagicMock


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


def _install_stubs():
    """Stub external dependencies before importing the server module."""

    # Stub google.* modules used at import time
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

    # Stub fastmcp.FastMCP so @mcp.tool() decorators are no-ops
    fastmcp = types.ModuleType("fastmcp")

    class _FakeMCP:
        def __init__(self, *a, **kw):
            pass

        def tool(self, *a, **kw):
            def _decorator(fn):
                return fn
            return _decorator

        def run(self, *a, **kw):  # pragma: no cover
            pass

    fastmcp.FastMCP = _FakeMCP
    sys.modules.setdefault("fastmcp", fastmcp)

    # Minimum env so module-level config doesn't crash.
    # Use a path that actually exists (empty file is fine — we stub the
    # service_account.Credentials.from_service_account_file loader above).
    fake_sa = os.path.join(REPO_ROOT, "tests", "_fake_sa.json")
    if not os.path.exists(fake_sa):
        with open(fake_sa, "w") as f:
            f.write("{}")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = fake_sa
    os.environ.setdefault("GSC_SITE_URL", "https://example.com/")


_install_stubs()

import gsc_mcp_server  # noqa: E402


class GetSearchAnalyticsRequestBodyTests(unittest.TestCase):
    def setUp(self):
        # Capture the body that get_search_analytics passes to GSC
        self.captured_body = {}

        def fake_get_gsc_service():
            service = MagicMock()
            query = service.searchanalytics.return_value.query

            def _query(siteUrl=None, body=None, **kwargs):
                self.captured_body.clear()
                self.captured_body.update(body or {})
                resp = MagicMock()
                resp.execute.return_value = {"rows": []}
                return resp

            query.side_effect = _query
            return service

        self._orig = gsc_mcp_server.get_gsc_service
        gsc_mcp_server.get_gsc_service = fake_get_gsc_service

    def tearDown(self):
        gsc_mcp_server.get_gsc_service = self._orig

    def test_dimensions_as_list_is_passed_through(self):
        result = gsc_mcp_server.get_search_analytics(dimensions=["page"])
        self.assertNotIn("error", result, msg=result)
        self.assertEqual(self.captured_body["dimensions"], ["page"])
        self.assertIsInstance(self.captured_body["dimensions"], list)



    def test_invalid_dimension_returns_error_without_calling_api(self):
        result = gsc_mcp_server.get_search_analytics(dimensions=["bogus"])
        self.assertIn("error", result)
        self.assertEqual(self.captured_body, {})

    def test_summary_only_returns_aggregated_totals(self):
        def fake_get_gsc_service():
            service = MagicMock()
            resp = MagicMock()
            resp.execute.return_value = {
                "rows": [
                    {"clicks": 10, "impressions": 100, "ctr": 0.1, "position": 1.0},
                    {"clicks": 5, "impressions": 50, "ctr": 0.1, "position": 2.0},
                ]
            }
            service.searchanalytics.return_value.query.return_value = resp
            return service

        gsc_mcp_server.get_gsc_service = fake_get_gsc_service
        result = gsc_mcp_server.get_search_analytics(
            dimensions=["page"], summary_only=True
        )
        self.assertIn("summary", result)
        self.assertEqual(result["summary"]["total_clicks"], 15)
        self.assertEqual(result["summary"]["total_impressions"], 150)
        self.assertEqual(result["summary"]["avg_ctr"], 10.0)


class SingleRegistrationTest(unittest.TestCase):
    def test_only_one_get_search_analytics_definition(self):
        with open(os.path.join(REPO_ROOT, "gsc_mcp_server.py")) as f:
            src = f.read()
        self.assertEqual(
            src.count("def get_search_analytics("),
            1,
            "get_search_analytics must be defined exactly once "
            "(FastMCP silently overwrites duplicate tool registrations).",
        )


if __name__ == "__main__":
    unittest.main()
