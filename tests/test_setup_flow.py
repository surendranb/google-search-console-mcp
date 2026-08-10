# SPDX-License-Identifier: MIT

"""Unit tests for the S7 setup-recovery engine (gsc_setup_flow).

Drives run_inline_recovery with a fake elicitation ctx and a monkeypatched
reinitialize, covering the branches the offline e2e suite cannot reach (the
'fixed' outcome needs a live GSC API). Telemetry is captured in-process to
assert the funnel events and that elicited VALUES never ride them."""

import os
import sys
import json
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# NOTE: never mutate os.environ at import time here (e.g. DISABLE_TELEMETRY) —
# pytest imports every test module during collection, and the e2e suite spawns
# server subprocesses that inherit this process's env. Telemetry in these unit
# tests is neutralized per-test by monkeypatching flow.send_telemetry instead.

import gsc_mcp_server as server  # noqa: E402
import gsc_setup_flow as flow  # noqa: E402


class FakeCtx:
    """Answers ctx.elicit() from a canned mapping keyed by schema field name."""

    def __init__(self, answers, action="accept"):
        self.answers = answers
        self.action = action
        self.elicited = []

    async def elicit(self, message, schema):
        self.elicited.append(message)
        fields = list(schema.model_fields.keys())
        if self.action != "accept":
            return types.SimpleNamespace(action=self.action, data=None)
        kwargs = {f: self.answers[f] for f in fields if f in self.answers}
        return types.SimpleNamespace(action="accept", data=schema(**kwargs))


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GSC_SITE_URL", raising=False)
    return monkeypatch


@pytest.fixture
def captured_flow(monkeypatch):
    events = []
    monkeypatch.setattr(flow, "send_telemetry",
                        lambda event, props=None: events.append((event, props or {})))
    return events


def _break_config(monkeypatch):
    err, cat, ver = server._compute_init_state()
    monkeypatch.setattr(server, "SERVER_INIT_ERROR", err)
    monkeypatch.setattr(server, "SERVER_INIT_ERROR_CATEGORY", cat)
    monkeypatch.setattr(server, "SERVER_INIT_ERROR_BRIEF_VERSION", ver)


async def test_fixed_outcome_applies_both_values(clean_env, captured_flow, tmp_path):
    key = tmp_path / "sa.json"
    key.write_text(json.dumps({"type": "service_account"}))
    _break_config(clean_env)
    clean_env.setattr(server, "reinitialize", lambda: (True, "ok", "initialized"))

    ctx = FakeCtx({"credentials_path": str(key), "site_url": "sc-domain:example.com"})
    recovered, message = await flow.run_inline_recovery(ctx)

    assert recovered is True
    assert "now working" in message and "current session only" in message
    assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == str(key)
    assert os.environ["GSC_SITE_URL"] == "sc-domain:example.com"
    assert len(ctx.elicited) == 2  # creds then site — both collected in one pass
    assert captured_flow == [("setup_flow", {
        "flow_branch": "site_url", "elicit_action": "accept", "flow_outcome": "fixed",
        "reinit_category": "ok", "error_category_at_entry": "InternalError",
        "elicit_supported": True,
    })]
    # Elicited values never ride telemetry.
    blob = json.dumps([p for _, p in captured_flow])
    assert str(key) not in blob and "example.com" not in blob


async def test_declined_elicit_pauses(clean_env, captured_flow, tmp_path):
    _break_config(clean_env)
    ctx = FakeCtx({}, action="decline")
    recovered, message = await flow.run_inline_recovery(ctx)
    assert recovered is False
    assert "Setup paused" in message and "setup_gsc_access" in message
    assert captured_flow[-1][1]["flow_outcome"] == "paused"
    assert captured_flow[-1][1]["elicit_action"] == "decline"


async def test_credentials_json_without_type_rejected(clean_env, captured_flow, tmp_path):
    bad = tmp_path / "not_a_key.json"
    bad.write_text('{"kind": "something-else"}')
    _break_config(clean_env)
    ctx = FakeCtx({"credentials_path": str(bad), "site_url": "https://example.com/"})
    recovered, message = await flow.run_inline_recovery(ctx)
    assert recovered is False
    assert "not a Google credentials JSON" in message
    assert captured_flow[-1][1]["flow_outcome"] == "invalid_creds"
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ  # never applied


async def test_missing_credentials_path_rejected(clean_env, captured_flow, tmp_path):
    _break_config(clean_env)
    ctx = FakeCtx({"credentials_path": str(tmp_path / "nope.json"),
                   "site_url": "https://example.com/"})
    recovered, message = await flow.run_inline_recovery(ctx)
    assert recovered is False
    assert "No file exists" in message
    assert captured_flow[-1][1]["flow_outcome"] == "invalid_path"


async def test_site_url_format_validated(clean_env, captured_flow, tmp_path):
    key = tmp_path / "sa.json"
    key.write_text(json.dumps({"type": "service_account"}))
    _break_config(clean_env)
    ctx = FakeCtx({"credentials_path": str(key), "site_url": "example.com"})
    recovered, message = await flow.run_inline_recovery(ctx)
    assert recovered is False
    assert "sc-domain:" in message and "https://" in message
    assert captured_flow[-1][1]["flow_outcome"] == "invalid_site"
    assert "GSC_SITE_URL" not in os.environ  # invalid value never applied


async def test_iam_entry_confirm_branch(clean_env, captured_flow, tmp_path):
    key = tmp_path / "sa.json"
    key.write_text(json.dumps({"type": "service_account"}))
    clean_env.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(key))
    clean_env.setenv("GSC_SITE_URL", "https://example.com/")
    clean_env.setattr(server, "SERVER_INIT_ERROR", None)
    clean_env.setattr(server, "reinitialize", lambda: (True, "ok", "initialized"))

    ctx = FakeCtx({"done": True})
    recovered, message = await flow.run_inline_recovery(ctx, entry_category="IAMError")
    assert recovered is True
    assert len(ctx.elicited) == 1
    assert "Users and permissions" in ctx.elicited[0]
    assert captured_flow[-1][1]["flow_branch"] == "property_access"
    assert captured_flow[-1][1]["flow_outcome"] == "fixed"


async def test_elicit_failure_falls_back_to_guided_text(clean_env, captured_flow):
    _break_config(clean_env)

    class BrokenCtx:
        async def elicit(self, message, schema):
            raise RuntimeError("client rejected elicitation")

    recovered, message = await flow.run_inline_recovery(BrokenCtx())
    assert recovered is False
    assert "can't prompt interactively" in message
    assert captured_flow[-1][1]["flow_outcome"] == "elicit_unsupported"
