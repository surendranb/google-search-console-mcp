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
                "TimeoutError", "RateLimitError", "NotFoundError", "SourceUnavailable",
                "MissingApiKey", "InternalError", "Cancelled",
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
