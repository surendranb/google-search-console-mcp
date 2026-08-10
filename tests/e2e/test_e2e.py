# SPDX-License-Identifier: MIT

"""End-to-end user-flow tests: spawn the real server, speak MCP over stdio,
verify tools + telemetry reaches the gateway boundary PII-free.

Self-contained copy of the music-mcp tests/e2e pattern (MCP Telemetry
Standard checklist item 10). Offline: local capture server only.
Run: uv run --extra dev pytest -m "e2e and not live"."""

import json
import os
import sys
import time
import threading
import subprocess
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mcp.client.stdio import StdioServerParameters

pytestmark = pytest.mark.e2e

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Events this client emits. (The worker is accept-and-tag, never a gate —
# this set documents the client's own registry, not an edge allowlist.)
KNOWN_EVENTS = {
    "mcp_started", "tools_listed", "tool_executed", "session_end",
    "server_first_install", "package_download", "skill_tip_shown", "skill_read",
    # Protocol Surfaces v1: S5 resource mirror, S6 prompts, S7 setup recovery.
    "resource_read", "prompt_used", "setup_flow",
}
OPT_OUT_VARS = ("GSC_MCP_TELEMETRY", "DISABLE_TELEMETRY", "DO_NOT_TRACK", "NO_TELEMETRY")

# Verbatim intent string asserted end-to-end (capture-then-curate: the client
# sends it as-is; the gateway/query layer owns curation).
INTENT_TEXT = "which queries drive clicks to the pricing page"


class CaptureServer:
    """Local stand-in for the Cloudflare gateway: records every telemetry
    POST so tests can assert what actually left the server."""

    def __init__(self):
        self.payloads = []
        lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                with lock:
                    self.server.payloads.append(json.loads(body))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"recorded":true}')

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.payloads = self.payloads
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_port}/e"

    def event_names(self):
        return [p["event"] for p in self.payloads]

    def wait_for_events(self, names, timeout=25):
        want = set(names)
        end = time.time() + timeout
        while time.time() < end:
            if want <= set(self.event_names()):
                return True
            time.sleep(0.2)
        return False

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _spawn(env_extra=None):
    """Spawn the real server over stdio from the repo root; return params."""
    env = {k: "" for k in OPT_OUT_VARS}
    env.update(os.environ)
    env_extra = env_extra or {}
    env.update(env_extra)
    if "GSC_MCP_TELEMETRY" not in env_extra:
        env.pop("GSC_MCP_TELEMETRY", None)
    # Offline determinism: with GSC config missing, the instrument wrapper
    # short-circuits EVERY tool (offline ones included) to a config-error
    # string. Fake the config so tests never depend on the developer's real
    # GSC env; offline tools never touch the credentials file.
    home = env.get("HOME")
    if home and not env.get("GOOGLE_APPLICATION_CREDENTIALS"):
        fake_creds = Path(home) / "fake_service_account.json"
        fake_creds.write_text("{}", encoding="utf-8")
        env["GOOGLE_APPLICATION_CREDENTIALS"] = str(fake_creds)
    if not env.get("GSC_SITE_URL"):
        env["GSC_SITE_URL"] = "https://example.com/"
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "gsc_mcp_server"],
        env=env,
        cwd=REPO_ROOT,
    )


def _extract_text(result):
    """CallToolResult -> the JSON the agent sees. mcp 2.x puts dict returns in
    structured_content and splits list returns into one TextContent per item."""
    raw = getattr(result, "structured_content", None)
    if raw is not None:
        return raw.get("result", raw)
    texts = [getattr(c, "text", None) for c in result.content]
    texts = [t for t in texts if t]
    assert texts, "result content has neither structured_content nor text"
    if len(texts) == 1:
        return json.loads(texts[0])
    return [json.loads(t) for t in texts]


async def _connect_and_run(params):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            dims = await session.call_tool("list_available_dimensions", {})
            skills = await session.call_tool("skills_list", {})
            skill = await session.call_tool("skill_read", {"skill_id": "brand_visibility.md"})
            # Intent capture: one call WITH intent, one WITHOUT (fake creds —
            # the tool returns an error dict, but telemetry still captures the
            # request shape, which is what these calls exercise).
            await session.call_tool("get_search_analytics", {
                "dimensions": ["query"],
                "intent": INTENT_TEXT,
            })
            await session.call_tool("get_search_analytics", {
                "dimensions": ["query"],
            })
            return names, _extract_text(dims), _extract_text(skills), _extract_text(skill)


async def test_end_user_tools(tmp_path):
    """Boot, list tools, use offline-capable tools, read a skill."""
    capture = CaptureServer()
    try:
        params = _spawn({"HOME": str(tmp_path), "GSC_MCP_TELEMETRY_URL": capture.url})
        names, dims, skills, skill = await _connect_and_run(params)

        assert "list_gsc_sites" in names
        assert "get_search_analytics" in names
        assert "skills_list" in names and "skill_read" in names
        assert "search_skills" not in names
        assert len(dims) > 0
        assert all("api_name" in d for d in dims)
        assert all("id" in s and "content" not in s for s in skills["skills"])
        assert skill["id"] == "brand_visibility.md" and skill["content"]
    finally:
        capture.close()


async def test_telemetry_events_flow(tmp_path):
    """Fresh install: boot events + tool events reach the gateway, PII-free."""
    capture = CaptureServer()
    try:
        params = _spawn({"HOME": str(tmp_path), "GSC_MCP_TELEMETRY_URL": capture.url})
        names, dims, skills, skill = await _connect_and_run(params)
        assert "get_search_analytics" in names

        assert capture.wait_for_events([
            "server_first_install", "package_download", "mcp_started",
            "tools_listed", "tool_executed", "skill_read",
        ]), f"missing events, saw: {capture.event_names()}"

        skill_events = [p for p in capture.payloads if p["event"] == "skill_read"]
        assert skill_events, f"skill_read event missing, saw: {capture.event_names()}"
        for payload in skill_events:
            props = payload["properties"]
            assert props["skill_name"] == "brand_visibility.md"
            assert props["fetch_ok"] is True

        tool_events = [p for p in capture.payloads if p["event"] == "tool_executed"]
        assert len(tool_events) >= 2, f"expected tool events, saw: {capture.event_names()}"
        for payload in tool_events:
            props = payload["properties"]
            assert props["status"] in ("success", "warning", "error", "exception", "cancelled")
            assert isinstance(props["latency_ms"], int)
            assert isinstance(props["result_chars"], int) and props["result_chars"] >= 0
            assert "error_category" not in props or props["error_category"] in (
                "APIError", "ValidationError", "SchemaHallucination", "IAMError",
                "AuthError", "TimeoutError", "RateLimitError", "NotFoundError",
                "SourceUnavailable", "MissingApiKey", "InternalError", "Cancelled",
            )
            assert "tool_sequence" in props and "calls_total" in props

        # Intent capture: verbatim when supplied, absent when not.
        gsa_events = [
            p for p in tool_events
            if p["properties"]["tool_name"] == "get_search_analytics"
        ]
        assert len(gsa_events) == 2, f"expected 2 get_search_analytics events, saw: {len(gsa_events)}"
        with_intent = [p for p in gsa_events if "intent" in p["properties"]]
        without_intent = [p for p in gsa_events if "intent" not in p["properties"]]
        assert len(with_intent) == 1 and len(without_intent) == 1, (
            f"expected exactly one event with intent and one without, "
            f"got {len(with_intent)} with / {len(without_intent)} without"
        )
        assert with_intent[0]["properties"]["intent"] == INTENT_TEXT

        blob = json.dumps(capture.payloads)
        for payload in capture.payloads:
            props = payload["properties"]
            assert payload["event"] in KNOWN_EVENTS, f"unregistered event: {payload['event']}"
            assert props["mcp_server_name"] == "google-search-console-mcp"
            assert props.get("session_id", "").startswith("sess_")
            assert props.get("schema_version") == 2
            assert "launch_channel" not in props
            assert "has_ever_worked" not in props
            assert payload["distinct_id"].startswith("inst_")
            assert props.get("$process_person_profile") is False
            assert props.get("agent_name") not in (None, "unknown")
            assert props.get("discovery_channel") in ("uvx", "homebrew", "pip_venv", "direct_python")
            assert props.get("run_context") in ("ci", "cloud", "terminal", "desktop", "headless")
        assert str(tmp_path) not in blob, "local path leaked into telemetry"
        assert "Users/" not in blob, "home path leaked into telemetry"
        assert "127.0.0.1" not in blob, "gateway URL leaked into telemetry"
        assert "reachsuren@" not in blob, "contact email leaked"
    finally:
        capture.close()


async def test_telemetry_opt_out(tmp_path):
    """Opt-out env var: the server boots and works, but nothing is sent."""
    capture = CaptureServer()
    try:
        params = _spawn({
            "HOME": str(tmp_path),
            "GSC_MCP_TELEMETRY_URL": capture.url,
            "GSC_MCP_TELEMETRY": "false",
        })
        names, dims, skills, skill = await _connect_and_run(params)
        assert "get_search_analytics" in names
        time.sleep(3)
        assert capture.payloads == [], f"expected no telemetry, got: {capture.event_names()}"
        # Opt-out gates ALL side effects (Standard §0.4): no identity file,
        # no calls_total counter — the config dir must never be created.
        assert not (tmp_path / ".gsc_mcp").exists(), "opt-out still wrote ~/.gsc_mcp/"
    finally:
        capture.close()


# --- Protocol Surfaces v1 e2e ---

async def test_protocol_surfaces_legacy_era(tmp_path):
    """Legacy initialize client: S1 annotations on every tool, S2 outputSchema
    on the primary data tool ONLY, S5 skill resources + resource_read, S6
    prompts + prompt_used, S3 brief_version on an auth-failure tool_executed.
    Text content of dict returns must stay plain json.dumps(indent=2)."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    capture = CaptureServer()
    try:
        params = _spawn({"HOME": str(tmp_path), "GSC_MCP_TELEMETRY_URL": capture.url})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # S1: every tool carries honest annotations.
                tools = (await session.list_tools()).tools
                by_name = {t.name: t for t in tools}
                assert "setup_gsc_access" in by_name  # S7 tool registered
                for t in tools:
                    assert t.annotations is not None, f"{t.name} missing annotations"
                assert by_name["get_search_analytics"].annotations.read_only_hint is True
                assert by_name["skills_list"].annotations.open_world_hint is False
                assert by_name["delete_sitemap"].annotations.read_only_hint is False
                assert by_name["delete_sitemap"].annotations.destructive_hint is True
                assert by_name["submit_sitemap"].annotations.read_only_hint is False

                # S2: outputSchema on the primary data tool only.
                assert by_name["get_search_analytics"].output_schema is not None
                schema_str = json.dumps(by_name["get_search_analytics"].output_schema)
                assert "SearchAnalyticsResult" in schema_str
                for name, t in by_name.items():
                    if name != "get_search_analytics":
                        assert t.output_schema is None, f"unexpected outputSchema on {name}"

                # Wire format: dict returns are still plain indent-2 JSON text;
                # structuredContent is additive alongside.
                r = await session.call_tool("get_search_analytics", {"dimensions": ["bogus"]})
                text = r.content[0].text
                assert text == json.dumps(
                    {"error": "Invalid dimension 'bogus'. Valid dimensions: "
                              "['country', 'device', 'page', 'query', 'searchAppearance', 'date']"},
                    indent=2)
                assert r.structured_content == {"result": json.loads(text)}

                # S3: auth failure (fake `{}` creds) returns the versioned brief.
                r = await session.call_tool("get_search_analytics", {"dimensions": ["query"]})
                brief_text = json.loads(r.content[0].text)["error"]
                assert "[SETUP BLOCKED]" in brief_text and "WHAT MUST HAPPEN" in brief_text

                # S5: skills mirrored as resources; reading one emits resource_read.
                resources = (await session.list_resources()).resources
                uris = {str(res.uri) for res in resources}
                assert "skill://brand_visibility" in uris
                assert len(uris) >= 5
                content = await session.read_resource("skill://brand_visibility")
                skills_dir = Path(REPO_ROOT) / "skills"
                assert content.contents[0].text == (skills_dir / "brand_visibility.md").read_text()

                # S6: prompts registered and fetchable.
                prompts = (await session.list_prompts()).prompts
                prompt_names = {p.name for p in prompts}
                assert prompt_names == {
                    "analyze-brand-visibility", "content-opportunities", "diagnose-traffic-drop"}
                got = await session.get_prompt("analyze-brand-visibility", {"brand_terms": "acme"})
                assert "brand_visibility.md" in got.messages[0].content.text
                assert "acme" in got.messages[0].content.text
                got2 = await session.get_prompt("diagnose-traffic-drop", {})
                assert "dimensions=[\"date\"]" in got2.messages[0].content.text

        assert capture.wait_for_events(["tool_executed", "resource_read", "prompt_used"]), (
            f"missing events, saw: {capture.event_names()}")

        # Telemetry payloads for the new surfaces.
        rr = [p for p in capture.payloads if p["event"] == "resource_read"]
        assert rr and rr[0]["properties"]["resource_uri"] == "skill://brand_visibility"
        pu = [p for p in capture.payloads if p["event"] == "prompt_used"]
        assert {p["properties"]["prompt_name"] for p in pu} == {
            "analyze-brand-visibility", "diagnose-traffic-drop"}
        assert [p["properties"]["has_args"] for p in pu
                if p["properties"]["prompt_name"] == "analyze-brand-visibility"] == [True]
        briefed = [p for p in capture.payloads if p["event"] == "tool_executed"
                   and p["properties"].get("brief_version")]
        assert briefed, "brief_version missing from the auth-failure tool_executed"
        assert briefed[0]["properties"]["brief_version"] == "gsc-401-invalid-v1"
        assert briefed[0]["properties"]["error_category"] == "AuthError"
    finally:
        capture.close()


async def test_protocol_surfaces_2026_era(tmp_path):
    """2026-era (stateless discover) client: same tools, same text content for
    an unchanged path — the dual-era guarantee."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    capture = CaptureServer()
    try:
        params = _spawn({"HOME": str(tmp_path), "GSC_MCP_TELEMETRY_URL": capture.url})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.discover()
                tools = (await session.list_tools()).tools
                assert {t.name for t in tools} >= {
                    "get_search_analytics", "setup_gsc_access", "skills_list"}
                r = await session.call_tool("get_search_analytics", {"dimensions": ["bogus"]})
                assert json.loads(r.content[0].text)["error"].startswith("Invalid dimension 'bogus'")
                dims = await session.call_tool("list_available_dimensions", {})
                assert "api_name" in dims.content[0].text
    finally:
        capture.close()


async def test_setup_recovery_elicitation(tmp_path):
    """S7 headline: born-broken config + elicitation-capable client. The failing
    get_search_analytics call elicits the credentials PATH and the property
    string, applies them for the session, and re-verifies. With a fake key the
    verification fails closed — flow_outcome=still_broken — and the elicited
    values never appear in telemetry."""
    import mcp.types as mtypes
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    fake_key = tmp_path / "sa_key.json"
    fake_key.write_text('{"type": "service_account"}', encoding="utf-8")
    site_value = "https://elicited-site.example/"

    async def elicitation_callback(context, params):
        props = params.requested_schema["properties"]
        if "credentials_path" in props:
            return mtypes.ElicitResult(action="accept", content={"credentials_path": str(fake_key)})
        if "site_url" in props:
            return mtypes.ElicitResult(action="accept", content={"site_url": site_value})
        return mtypes.ElicitResult(action="accept", content={"done": True})

    capture = CaptureServer()
    try:
        # Broken on purpose: neither creds nor site configured.
        env = {k: "" for k in OPT_OUT_VARS}
        env.update(os.environ)
        env["HOME"] = str(tmp_path)
        env["GSC_MCP_TELEMETRY_URL"] = capture.url
        env.pop("GSC_MCP_TELEMETRY", None)
        env.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
        env.pop("GSC_SITE_URL", None)
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "gsc_mcp_server"], env=env, cwd=REPO_ROOT)

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, elicitation_callback=elicitation_callback) as session:
                await session.initialize()
                r = await session.call_tool("get_search_analytics", {"dimensions": ["query"]})
                text = r.content[0].text
                # Both values were collected and applied; the fake key then
                # fails Google-side validation — fail closed, honestly.
                assert "Still not connected" in text, text

                # Standalone tool exists and runs the same engine.
                r2 = await session.call_tool("setup_gsc_access", {})
                assert "Still not connected" in r2.content[0].text or "paused" in r2.content[0].text

        assert capture.wait_for_events(["setup_flow"]), (
            f"missing setup_flow, saw: {capture.event_names()}")
        flows = [p["properties"] for p in capture.payloads if p["event"] == "setup_flow"]
        assert any(f["flow_branch"] == "site_url" and f["flow_outcome"] == "still_broken"
                   for f in flows), flows
        assert all(f.get("elicit_supported") is True for f in flows)

        # Elicited values NEVER reach telemetry (spec S7).
        blob = json.dumps(capture.payloads)
        assert "sa_key.json" not in blob, "elicited credentials path leaked into telemetry"
        assert "elicited-site" not in blob, "elicited site URL leaked into telemetry"
    finally:
        capture.close()


async def test_setup_brief_unchanged_without_elicitation(tmp_path):
    """Clients that do NOT declare elicitation get the guided config brief on a
    born-broken boot — the S3 path, never a prompt."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    capture = CaptureServer()
    try:
        env = {k: "" for k in OPT_OUT_VARS}
        env.update(os.environ)
        env["HOME"] = str(tmp_path)
        env["GSC_MCP_TELEMETRY_URL"] = capture.url
        env.pop("GSC_MCP_TELEMETRY", None)
        env.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
        env.pop("GSC_SITE_URL", None)
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "gsc_mcp_server"], env=env, cwd=REPO_ROOT)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:  # no elicitation_callback
                await session.initialize()
                r = await session.call_tool("get_search_analytics", {"dimensions": ["query"]})
                text = r.content[0].text
                assert text.startswith("Configuration Error: [SETUP BLOCKED]")
                assert text.endswith("Please instruct the user to fix their setup.")

        assert capture.wait_for_events(["tool_executed"])
        te = [p["properties"] for p in capture.payloads
              if p["event"] == "tool_executed"
              and p["properties"]["tool_name"] == "get_search_analytics"]
        assert te and te[0]["brief_version"] == "gsc-creds-unset-v1"
        assert te[0]["error_category"] == "InternalError"
    finally:
        capture.close()


async def test_first_run_disclosure(tmp_path):
    """First boot prints the telemetry disclosure before any event is sent."""
    capture = CaptureServer()
    try:
        env = {k: "" for k in OPT_OUT_VARS}
        env.update(os.environ)
        env["HOME"] = str(tmp_path)
        env["GSC_MCP_TELEMETRY_URL"] = capture.url
        env.pop("GSC_MCP_TELEMETRY", None)
        proc = subprocess.Popen(
            [sys.executable, "-m", "gsc_mcp_server"],
            stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env, text=True,
            cwd=REPO_ROOT,
        )
        time.sleep(4)
        proc.terminate()
        err = proc.communicate(timeout=5)[1]
        assert "anonymous usage telemetry" in err
    finally:
        capture.close()
