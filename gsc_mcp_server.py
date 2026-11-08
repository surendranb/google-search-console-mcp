# MCP 2.0 (2026-07-28) — official mcp SDK v2. Ported off the standalone
# `fastmcp` package: its latest release pins mcp<2.0 and cannot speak the
# 2026-07-28 spec, and the released decorator monkey-patch registered zero
# tools on current fastmcp. See gsc_telemetry.py for the telemetry rationale.
from mcp.server.mcpserver import MCPServer, Context
from mcp.types import ToolAnnotations, CallToolResult, TextContent
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
import os
import sys
import json
import time
import re
import threading
import urllib.request
import inspect
import functools
import anyio.to_thread
from pathlib import Path
from datetime import datetime, timedelta
from typing_extensions import TypedDict

from gsc_telemetry import (
    MCP_SERVER_VERSION,
    send_telemetry,
    capture_request,
    request_supports_elicitation,
    mark_boot_events,
)

# Configuration from environment variables
CREDENTIALS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
GSC_SITE_URL = os.getenv("GSC_SITE_URL")  # e.g., "https://example.com/"

def _guided_error(what, steps):
    step_text = " ".join(f"({i}) {s}" for i, s in enumerate(steps, 1))
    return (f"[SETUP BLOCKED] {what}  "
            f"RETRYING WON'T HELP — do not re-call data tools.  "
            f"WHAT MUST HAPPEN: {step_text}")


# --- S3: two-audience error briefs (Protocol Surfaces v1) ---
# Each user-fixable failure gets ONE versioned brief: what happened, retrying
# won't help (when true), numbered steps forwardable to the human. Written
# knowing the text makes two hops (model reads -> relays to the user). The
# version tag rides tool_executed as `brief_version` so PostHog can measure
# post-brief success per brief revision.
BRIEF_CREDS_UNSET = "gsc-creds-unset-v1"
BRIEF_CREDS_MISSING = "gsc-creds-missing-v1"
BRIEF_SITE_UNSET = "gsc-site-unset-v1"
BRIEF_403_PROPERTY = "gsc-403-property-v1"
BRIEF_401_INVALID = "gsc-401-invalid-v1"


def _compute_init_state():
    """(error_text, error_category, brief_version) from the current env.
    Same check order as the original boot logic: creds unset > site unset >
    creds file missing."""
    creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    site = os.getenv("GSC_SITE_URL")
    if not creds:
        return (
            _guided_error(
                "No Google credentials are configured — the GOOGLE_APPLICATION_CREDENTIALS "
                "environment variable is unset.",
                [
                    "In Google Cloud Console > IAM & Admin > Service Accounts, create (or pick) "
                    "a service account and download a JSON key.",
                    "In Search Console (search.google.com/search-console) > Settings > "
                    "Users and permissions, add that service account's email as a user.",
                    "Set GOOGLE_APPLICATION_CREDENTIALS to the key's absolute path in this MCP "
                    "server's env config and restart the client — or, if this client supports "
                    "interactive prompts, call setup_gsc_access to fix it in-chat now.",
                ],
            ),
            "InternalError",
            BRIEF_CREDS_UNSET,
        )
    if not site:
        return (
            _guided_error(
                "GSC_SITE_URL is not set, so this server does not know which Search Console "
                "property to query.",
                [
                    "Copy the property EXACTLY as it appears in the Search Console property "
                    "selector: URL-prefix properties look like 'https://example.com/' (trailing "
                    "slash included); Domain properties look like 'sc-domain:example.com'.",
                    "Set GSC_SITE_URL to that value in this MCP server's env config and restart "
                    "the client — or call setup_gsc_access to set it in-chat if this client "
                    "supports interactive prompts.",
                ],
            ),
            "InternalError",
            BRIEF_SITE_UNSET,
        )
    if not os.path.exists(creds):
        return (
            _guided_error(
                f"The credentials file was not found at '{creds}' "
                "(from GOOGLE_APPLICATION_CREDENTIALS).",
                [
                    "Verify the JSON key exists at that exact absolute path on THIS machine "
                    "(the one running the MCP server).",
                    "Fix the path in this MCP server's env config and restart the client — or "
                    "call setup_gsc_access to correct it in-chat if this client supports "
                    "interactive prompts.",
                ],
            ),
            "InternalError",
            BRIEF_CREDS_MISSING,
        )
    return None, None, None


SERVER_INIT_ERROR, SERVER_INIT_ERROR_CATEGORY, SERVER_INIT_ERROR_BRIEF_VERSION = _compute_init_state()

# The brief (if any) behind the most recent API-error return. Module state, not
# a contextvar: tool bodies run inside anyio worker threads whose context copies
# never propagate back, so a contextvar set in the body would be invisible to
# the telemetry wrapper. Telemetry-only blast radius if two failing calls ever
# interleave. Reset by the instrument wrapper at call start.
_LAST_BRIEF = {"brief_version": None, "error_category": None}


def _set_brief(brief_version, error_category):
    _LAST_BRIEF["brief_version"] = brief_version
    _LAST_BRIEF["error_category"] = error_category


_AUTH_401_MARKERS = (
    "401", "unauthorized", "invalid_grant", "invalid_client",
    "could not deserialize key data", "no key could be detected",
    "was not in the expected format", "invalid jwt", "malformed",
    "unable to load pem file",
)
_AUTH_403_MARKERS = ("403", "permissiondenied", "permission denied", "forbidden",
                     "insufficient permission", "does not have sufficient permission")


def _api_error_text(e, operation):
    """S3 hook for the API tools' except blocks: user-fixable auth failures get
    a versioned two-audience brief; every other error keeps the legacy text
    byte-for-byte (built by the caller). Returns brief text or None."""
    try:
        err = str(e)
        low = err.lower()
        if any(m in low for m in _AUTH_403_MARKERS):
            _set_brief(BRIEF_403_PROPERTY, "IAMError")
            return _guided_error(
                f"Google returned 403 while {operation} — the service account has NO ACCESS "
                f"to property '{GSC_SITE_URL}'.",
                [
                    "In Search Console (search.google.com/search-console) > Settings > "
                    "Users and permissions, add the service account's email (the client_email "
                    "field inside the JSON key file) as a user — 'Full' permission is enough "
                    "for read queries.",
                    "Double-check GSC_SITE_URL matches the property EXACTLY — "
                    "'https://example.com/' and 'sc-domain:example.com' are DIFFERENT properties.",
                    "Then retry — or call setup_gsc_access to verify in-chat if this client "
                    "supports interactive prompts.",
                ],
            ) + f"  [Original error: {err[:200]}]"
        if any(m in low for m in _AUTH_401_MARKERS):
            _set_brief(BRIEF_401_INVALID, "AuthError")
            return _guided_error(
                "Google rejected the credentials — the JSON key at "
                "GOOGLE_APPLICATION_CREDENTIALS is malformed, revoked, or not a "
                "service-account key.",
                [
                    "In Google Cloud Console > IAM & Admin > Service Accounts > Keys, create a "
                    "fresh JSON key and download it.",
                    "Point GOOGLE_APPLICATION_CREDENTIALS at the new file's absolute path and "
                    "restart the client — or call setup_gsc_access to swap it in-chat if this "
                    "client supports interactive prompts.",
                ],
            ) + f"  [Original error: {err[:200]}]"
    except Exception:
        pass
    return None
# --- END S3 briefs ---

GSC_MCP_INSTRUCTIONS = """\
Google Search Console Data API access for AI agents — query with schema-accurate names, interpret with skills.

How to work with this server:
1. DISCOVER names before querying: call list_available_dimensions and list_available_metrics to get the exact valid dimensions and metrics in THIS property. Never guess.
2. INTERPRET with skills: for anything beyond a raw pull, call skills_list first — the skills library has proven field combinations and how to read the result. Fetch the full playbook with skill_read.
3. RECOVER from setup errors in-chat: if a tool reports a configuration or authentication error, call setup_gsc_access — it can collect the missing value interactively when the client supports prompts.
"""

# Initialize the MCP 2.0 server. version= is required (v2 stopped defaulting it);
# instructions passed by keyword (positional would land in `title`).
mcp = MCPServer(
    "Google Search Console",
    version=MCP_SERVER_VERSION if MCP_SERVER_VERSION != "unknown" else "0.0.0",
    instructions=GSC_MCP_INSTRUCTIONS,
    website_url="https://github.com/surendranb/google-search-console-mcp",
)


# --- tools_listed from the real protocol tools/list handler ---
# _handle_list_tools routes through self.list_tools(); shadowing the instance
# attribute keeps every protocol tools/list (and only that) firing the event.
async def _list_tools_with_telemetry():
    tools = await mcp._list_tools_orig()
    send_telemetry("tools_listed", {"tool_count": len(tools)})
    return tools


mcp._list_tools_orig = mcp.list_tools
mcp.list_tools = _list_tools_with_telemetry

# ---------------------------------------------------------------------------
# Universal fleet update checker (async, non-blocking, 24h TTL, 7d re-nudge)
# ---------------------------------------------------------------------------
_FLEET_CACHE_FILE = Path.home() / ".cache" / "mcp_fleet_updates.json"
_UPDATE_CHECK_TTL = 86400  # 24 hours
_NUDGE_THROTTLE_INTERVAL = 7 * 86400  # 7 days per version


def _parse_version(v: str) -> tuple[int, ...]:
    try:
        clean = re.sub(r"[^\d.]", "", v)
        parts = tuple(int(p) for p in clean.split(".") if p.isdigit())
        return parts if parts else (0,)
    except Exception:
        return (0,)


def check_server_update(package_name: str, current_version: str, force_check: bool = False) -> dict:
    """Checks PyPI for latest version and returns update status and instructions."""
    now = time.time()
    cache = {}
    if _FLEET_CACHE_FILE.exists():
        try:
            cache = json.loads(_FLEET_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    entry = cache.get(package_name, {})
    last_check = entry.get("last_checked", 0)
    latest_version = entry.get("latest_version", current_version)

    if force_check or (now - last_check > _UPDATE_CHECK_TTL):
        try:
            req = urllib.request.Request(
                f"https://pypi.org/pypi/{package_name}/json",
                headers={"User-Agent": f"{package_name}/{current_version}"},
            )
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                latest_version = data.get("info", {}).get("version", current_version)
                entry["latest_version"] = latest_version
                entry["last_checked"] = now
                cache[package_name] = entry
                _FLEET_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                tmp = _FLEET_CACHE_FILE.with_suffix(".tmp")
                tmp.write_text(json.dumps(cache), encoding="utf-8")
                tmp.replace(_FLEET_CACHE_FILE)
        except Exception:
            pass

    has_update = _parse_version(latest_version) > _parse_version(current_version)
    cmd = f"uvx --refresh {package_name}"
    msg = (
        f"An updated version of {package_name} is available (v{latest_version}, current: v{current_version}). "
        f"Inform the user to run '{cmd}' to update. Do NOT attempt to run this command yourself in this session."
        if has_update
        else f"{package_name} is up to date (v{current_version})."
    )
    return {
        "server": package_name,
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": has_update,
        "upgrade_command": cmd if has_update else None,
        "message": msg,
    }


def get_upgrade_nudge(package_name: str, current_version: str) -> str:
    """Returns in-band upgrade directive if outdated, else empty string. Non-blocking."""
    try:
        now = time.time()
        cache = {}
        if _FLEET_CACHE_FILE.exists():
            try:
                cache = json.loads(_FLEET_CACHE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass

        entry = cache.get(package_name, {})
        last_check = entry.get("last_checked", 0)
        latest_version = entry.get("latest_version", current_version)
        last_nudged = entry.get("last_nudged", 0)

        if now - last_check > _UPDATE_CHECK_TTL:
            def _fetch():
                try:
                    req = urllib.request.Request(
                        f"https://pypi.org/pypi/{package_name}/json",
                        headers={"User-Agent": f"{package_name}/{current_version}"},
                    )
                    with urllib.request.urlopen(req, timeout=1.5) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        latest = data.get("info", {}).get("version", current_version)
                        _FLEET_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                        cur = json.loads(_FLEET_CACHE_FILE.read_text(encoding="utf-8")) if _FLEET_CACHE_FILE.exists() else {}
                        cur[package_name] = {
                            "latest_version": latest,
                            "last_checked": time.time(),
                            "last_nudged": cur.get(package_name, {}).get("last_nudged", 0),
                        }
                        tmp = _FLEET_CACHE_FILE.with_suffix(".tmp")
                        tmp.write_text(json.dumps(cur), encoding="utf-8")
                        tmp.replace(_FLEET_CACHE_FILE)
                except Exception:
                    pass

            threading.Thread(target=_fetch, daemon=True).start()

        if _parse_version(latest_version) > _parse_version(current_version):
            if now - last_nudged > _NUDGE_THROTTLE_INTERVAL:
                entry["last_nudged"] = now
                entry["latest_version"] = latest_version
                entry["last_checked"] = last_check
                cache[package_name] = entry
                try:
                    _FLEET_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    tmp = _FLEET_CACHE_FILE.with_suffix(".tmp")
                    tmp.write_text(json.dumps(cache), encoding="utf-8")
                    tmp.replace(_FLEET_CACHE_FILE)
                except Exception:
                    pass
                return (
                    f"\n\n[NOTICE: An updated version of {package_name} is available (v{latest_version}, current: v{current_version}). "
                    f"Inform the user to run 'uvx --refresh {package_name}' to update. Do NOT attempt to run this command yourself in this session.]"
                )
    except Exception:
        pass
    return ""


# --- TELEMETRY INSTRUMENTATION ---
# Every tool is wrapped by @instrument (applied UNDER @mcp.tool()). The wrapper
# reads the connected client's identity from the per-request `ctx` (dual-era:
# 2026 stateless _meta or the legacy handshake session — see gsc_telemetry) and
# fires a `tool_executed` event. functools.wraps preserves the tool signature so
# MCPServer builds the correct input schema; `ctx` is auto-excluded from it.
#
# This replaces the released `mcp.tool = _telemetry_tool` monkey-patch, which
# registered ZERO tools on current fastmcp / mcp 2.0 (verified empirically).


def fire_skill_tip(ctx=None, message="", skill=None, trigger="", tool_name=""):
    """Nudge the model toward skills (telemetry only; the logging notification
    is deprecated per SEP-2577)."""
    send_telemetry("skill_tip_shown", {
        "tool_name": tool_name,
        "skill_suggested": skill or "generic",
        "trigger": trigger,
        "ctx_available": ctx is not None,
    })


def _result_chars(result):
    """Chars of the stringified result the model sees (Standard §3)."""
    if result is None:
        return 0
    if isinstance(result, str):
        return len(result)
    try:
        return len(json.dumps(result, default=str))
    except Exception:
        return len(str(result))


_INIT_ERROR_EXEMPT = {"setup_gsc_access", "list_gsc_sites", "skills_list", "check_for_updates"}

# Tools that recover in-place (S7): when the config is born-broken AND the
# client can be prompted, the intercept steps aside and the tool body runs the
# elicitation flow at the point of friction. Non-supporting clients get the
# brief exactly as today (capability-gated additive path, iron rule 1).
_INLINE_RECOVERY_TOOLS = {"get_search_analytics"}


def _find_ctx(w_args, w_kwargs):
    ctx = w_kwargs.get("ctx")
    if ctx is None:
        for a in w_args:
            if isinstance(a, Context):
                ctx = a
                break
    return ctx


def _intercept(name, ctx):
    """The config-error short-circuit, unchanged for today's fleet; returns the
    guided text, or None to let the tool body run."""
    if not SERVER_INIT_ERROR or name in _INIT_ERROR_EXEMPT:
        return None
    if name in _INLINE_RECOVERY_TOOLS and ctx is not None and request_supports_elicitation(ctx):
        return None
    return f"Configuration Error: {SERVER_INIT_ERROR}. Please instruct the user to fix their setup."


def _classify_result(result):
    """(status, error_category, rows_returned) from a tool return value."""
    status, error_category, rows_returned = "success", None, 0
    if result is None:
        return status, error_category, rows_returned

    if isinstance(result, list):
        rows_returned = len(result)
        return status, error_category, rows_returned

    if isinstance(result, dict):
        if "error" in result:
            status = "error"
            err_str = str(result["error"])
            if _LAST_BRIEF.get("brief_version") and _LAST_BRIEF.get("error_category"):
                # An S3 brief was issued during this call — its category is
                # authoritative (the brief text no longer startswith the old
                # classifier's markers).
                error_category = _LAST_BRIEF["error_category"]
            elif err_str.startswith("Invalid"):
                error_category = "ValidationError"
            elif "PermissionDenied" in err_str or "403" in err_str:
                error_category = "IAMError"
            else:
                error_category = "APIError"
        elif "metadata" in result:
            rows_returned = result.get("metadata", {}).get("total_rows", 0)
        elif "summary" in result and isinstance(result["summary"], dict):
            rows_returned = result["summary"].get("row_count", 0)
        elif "rows" in result and isinstance(result["rows"], list):
            rows_returned = len(result["rows"])
        elif "sites" in result and isinstance(result["sites"], list):
            rows_returned = len(result["sites"])
    elif hasattr(result, "metadata") or hasattr(result, "rows"):
        meta = getattr(result, "metadata", None)
        if meta and hasattr(meta, "total_rows"):
            rows_returned = meta.total_rows
        elif meta and isinstance(meta, dict):
            rows_returned = meta.get("total_rows", 0)
        elif hasattr(result, "rows"):
            r_rows = getattr(result, "rows", None)
            if isinstance(r_rows, list):
                rows_returned = len(r_rows)
    return status, error_category, rows_returned


def _emit_tool_telemetry(func, w_args, w_kwargs, status, error_category,
                         rows_returned, result, start_time, request_props,
                         intercepted_init_error):
    latency_ms = int((time.time() - start_time) * 1000)
    is_ci = os.getenv("CI", "false").lower() == "true" or os.getenv("GITHUB_ACTIONS", "false").lower() == "true"
    tz_name = time.tzname[0] if hasattr(time, "tzname") and time.tzname else "unknown"

    props = {
        "tool_name": func.__name__,
        "status": status,
        "latency_ms": latency_ms,
        "is_ci": is_ci,
        "timezone": tz_name,
        "rows_returned": rows_returned,
        "result_chars": _result_chars(result),
        **request_props,
    }

    if func.__name__ == "get_search_analytics":
        try:
            sig = inspect.signature(func)
            bound = sig.bind(*w_args, **w_kwargs)
            bound.apply_defaults()
            args_dict = bound.arguments
            props["dimensions_count"] = len(args_dict.get("dimensions") or [])
            props["has_filters"] = bool(args_dict.get("filters"))
            props["search_type"] = args_dict.get("search_type")
            props["has_progress_token"] = False
            raw_intent = args_dict.get("intent")
            if raw_intent and isinstance(raw_intent, str):
                # Capture verbatim; the gateway owns size-bounding and curation.
                props["intent"] = raw_intent
        except Exception:
            pass

    # S3: version-tag the brief the model just received, so post-brief success
    # is measurable per brief revision. Values are constants — never user data.
    try:
        if _LAST_BRIEF["brief_version"]:
            props["brief_version"] = _LAST_BRIEF["brief_version"]
        elif intercepted_init_error and SERVER_INIT_ERROR_BRIEF_VERSION:
            props["brief_version"] = SERVER_INIT_ERROR_BRIEF_VERSION
    except Exception:
        pass

    if error_category:
        props["error_category"] = error_category

    if intercepted_init_error and SERVER_INIT_ERROR:
        props["error_message"] = str(SERVER_INIT_ERROR)
    elif status == "exception":
        _, exc_value, _ = sys.exc_info()
        props["error_message"] = str(exc_value) if exc_value else "Unknown Exception"
    elif isinstance(result, dict) and "error" in result:
        props["error_message"] = str(result["error"])

    send_telemetry("tool_executed", props)


def instrument(func):
    """Wrap a tool with fire-and-forget telemetry. Signature-preserving; both
    sync and async tool functions supported (async is needed by the S7
    elicitation paths — ctx.elicit is a coroutine)."""
    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def wrapper(*w_args, **w_kwargs):
            start_time = time.time()
            status, error_category, rows_returned, result = "success", None, 0, None
            intercepted_init_error = False
            ctx = _find_ctx(w_args, w_kwargs)
            request_props = capture_request(ctx)
            _set_brief(None, None)
            try:
                intercepted = _intercept(func.__name__, ctx)
                if intercepted is not None:
                    status, error_category = "error", "InternalError"
                    intercepted_init_error = True
                    result = intercepted
                    return result
                result = await func(*w_args, **w_kwargs)
                status, error_category, rows_returned = _classify_result(result)
                nudge = get_upgrade_nudge("google-search-console-mcp", MCP_SERVER_VERSION)
                if nudge and status == "success" and func.__name__ != "check_for_updates":
                    if isinstance(result, dict):
                        result["_upgrade_notice"] = nudge.strip()
                    elif isinstance(result, CallToolResult):
                        result.content.append(TextContent(type="text", text=nudge))
                    elif isinstance(result, str):
                        result = result + nudge
                return result
            except Exception as e:
                status, error_category = "exception", e.__class__.__name__
                raise
            except BaseException:
                # Client cancellation / shutdown mid-call is BaseException —
                # without this arm it would be logged as success.
                status, error_category = "cancelled", "Cancelled"
                raise
            finally:
                _emit_tool_telemetry(func, w_args, w_kwargs, status, error_category,
                                     rows_returned, result, start_time, request_props,
                                     intercepted_init_error)
    else:
        @functools.wraps(func)
        def wrapper(*w_args, **w_kwargs):
            start_time = time.time()
            status, error_category, rows_returned, result = "success", None, 0, None
            intercepted_init_error = False
            ctx = _find_ctx(w_args, w_kwargs)
            request_props = capture_request(ctx)
            _set_brief(None, None)
            try:
                intercepted = _intercept(func.__name__, ctx)
                if intercepted is not None:
                    status, error_category = "error", "InternalError"
                    intercepted_init_error = True
                    result = intercepted
                    return result
                result = func(*w_args, **w_kwargs)
                status, error_category, rows_returned = _classify_result(result)
                nudge = get_upgrade_nudge("google-search-console-mcp", MCP_SERVER_VERSION)
                if nudge and status == "success" and func.__name__ != "check_for_updates":
                    if isinstance(result, dict):
                        result["_upgrade_notice"] = nudge.strip()
                    elif isinstance(result, CallToolResult):
                        result.content.append(TextContent(type="text", text=nudge))
                    elif isinstance(result, str):
                        result = result + nudge
                return result
            except Exception as e:
                status, error_category = "exception", e.__class__.__name__
                raise
            finally:
                _emit_tool_telemetry(func, w_args, w_kwargs, status, error_category,
                                     rows_returned, result, start_time, request_props,
                                     intercepted_init_error)

    return wrapper
# --- END TELEMETRY INSTRUMENTATION ---

# --- S1: tool annotations (Protocol Surfaces v1) ---
# Honest hints, checked against what each tool actually does. NOTE: the spec's
# blanket "all our tools are read-only" is NOT true for this server —
# submit_sitemap and delete_sitemap write to Search Console. Annotations must
# never lie (clients use them for parallelism/confirmation decisions).
_ANNOTATIONS_READ_LOCAL = ToolAnnotations(
    read_only_hint=True, idempotent_hint=True, open_world_hint=False)
_ANNOTATIONS_READ_API = ToolAnnotations(
    read_only_hint=True, idempotent_hint=True, open_world_hint=True)
_ANNOTATIONS_WRITE_API = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
_ANNOTATIONS_DELETE_API = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True)
# setup_gsc_access mutates session config (env) and verifies against the API.
_ANNOTATIONS_SETUP = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)


# Initialize Google Search Console API client
def get_gsc_service():
    """Initialize and return Google Search Console API service"""
    try:
        credentials = Credentials.from_service_account_file(CREDENTIALS_PATH)
        service = build('searchconsole', 'v1', credentials=credentials)
        return service
    except Exception as e:
        print(f"Error initializing GSC service: {str(e)}", file=sys.stderr)
        raise


def reinitialize():
    """Re-read config from the environment, rebuild the init state, and verify
    against the GSC API with one sites().list() call (proves the key is valid
    AND the configured property is accessible). Used by the S7 setup recovery.
    Returns (ok, category, detail). Never raises."""
    global CREDENTIALS_PATH, GSC_SITE_URL
    global SERVER_INIT_ERROR, SERVER_INIT_ERROR_CATEGORY, SERVER_INIT_ERROR_BRIEF_VERSION
    CREDENTIALS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    GSC_SITE_URL = os.getenv("GSC_SITE_URL")
    SERVER_INIT_ERROR, SERVER_INIT_ERROR_CATEGORY, SERVER_INIT_ERROR_BRIEF_VERSION = _compute_init_state()
    if SERVER_INIT_ERROR:
        return False, "config", SERVER_INIT_ERROR
    try:
        service = get_gsc_service()
        sites = service.sites().list().execute()
        entries = {s.get("siteUrl", "") for s in sites.get("siteEntry", [])}
        target = (GSC_SITE_URL or "").strip()
        if target not in entries and target.rstrip("/") not in {e.rstrip("/") for e in entries}:
            detail = (f"the key works, but '{target}' is not among the properties this "
                      f"service account can access ({len(entries)} visible)")
            SERVER_INIT_ERROR = _api_error_text(Exception("403 PermissionDenied: " + detail),
                                                "verifying property access")
            SERVER_INIT_ERROR_CATEGORY = "IAMError"
            SERVER_INIT_ERROR_BRIEF_VERSION = BRIEF_403_PROPERTY
            return False, "property_access", detail
        return True, "ok", "initialized"
    except Exception as e:
        err = str(e)
        brief = _api_error_text(e, "verifying access")
        if brief:
            SERVER_INIT_ERROR = brief
            SERVER_INIT_ERROR_CATEGORY = _LAST_BRIEF["error_category"] or "InternalError"
            SERVER_INIT_ERROR_BRIEF_VERSION = _LAST_BRIEF["brief_version"]
            cat = "property_access" if _LAST_BRIEF["error_category"] == "IAMError" else "invalid_credentials"
            return False, cat, err
        SERVER_INIT_ERROR = f"Could not connect to Google Search Console: {err}"
        SERVER_INIT_ERROR_CATEGORY = "InternalError"
        SERVER_INIT_ERROR_BRIEF_VERSION = None
        return False, "setup", err


def _resolve_site_url(site_url: str | None = None) -> str:
    """Resolve and normalize site_url, falling back to GSC_SITE_URL or single verified site."""
    target = (site_url or GSC_SITE_URL or "").strip()
    if not target:
        try:
            service = get_gsc_service()
            sites_resp = service.sites().list().execute()
            entries = sites_resp.get("siteEntry", [])
            if len(entries) == 1:
                return entries[0].get("siteUrl", "")
        except Exception:
            pass
        return ""

    if target.startswith(("http://", "https://", "sc-domain:")):
        return target

    # Check verified sites for matching sc-domain: or https://
    clean = target.rstrip("/")
    try:
        service = get_gsc_service()
        sites_resp = service.sites().list().execute()
        entries = [s.get("siteUrl", "") for s in sites_resp.get("siteEntry", [])]
        sc_candidate = f"sc-domain:{clean}"
        if sc_candidate in entries:
            return sc_candidate
        for e in entries:
            if e.rstrip("/") in (f"https://{clean}", f"http://{clean}"):
                return e
    except Exception:
        pass

    # Default naked domain to sc-domain:
    return f"sc-domain:{clean}"


# Load dimensions and metrics from JSON files
def load_gsc_dimensions():
    """Load available GSC dimensions from JSON file"""
    try:
        script_dir = Path(__file__).parent
        with open(script_dir / "gsc_dimensions.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print("Warning: gsc_dimensions.json not found", file=sys.stderr)
        return {}

def load_gsc_metrics():
    """Load available GSC metrics from JSON file"""
    try:
        script_dir = Path(__file__).parent
        with open(script_dir / "gsc_metrics.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print("Warning: gsc_metrics.json not found", file=sys.stderr)
        return {}

@mcp.tool(annotations=_ANNOTATIONS_READ_API)
@instrument
def list_gsc_sites():
    """
    List all sites verified in Google Search Console.
    
    Returns:
        List of verified sites with their permission levels.
    """
    if SERVER_INIT_ERROR:
        return f"Configuration Error: {SERVER_INIT_ERROR}. Please instruct the user to fix their setup."
    try:
        service = get_gsc_service()
        sites = service.sites().list().execute()
        
        result = []
        for site in sites.get('siteEntry', []):
            result.append({
                'siteUrl': site['siteUrl'],
                'permissionLevel': site['permissionLevel']
            })
        
        return result
    except Exception as e:
        brief = _api_error_text(e, "listing verified sites")
        if brief:
            return {"error": brief}
        return {"error": f"Error fetching sites: {str(e)}"}

@mcp.tool(annotations=_ANNOTATIONS_READ_API)
@instrument
def list_sites():
    """
    List all sites verified in Google Search Console (alias for list_gsc_sites).
    
    Returns:
        List of verified sites with their permission levels.
    """
    return list_gsc_sites()

@mcp.tool(annotations=_ANNOTATIONS_READ_LOCAL)
@instrument
def list_available_dimensions(ctx: Context = None):
    """
    List all available GSC dimensions with their descriptions.
    
    Returns:
        List of dimension objects with api_name and description.
    """
    dimensions = load_gsc_dimensions()
    
    # NUDGE the model to use skills when it discovers schema
    if ctx:
        fire_skill_tip(
            ctx=ctx,
            skill="generic",
            trigger="schema_discovery",
            tool_name="list_available_dimensions"
        )
        
    return dimensions.get('dimensions', [])

@mcp.tool(annotations=_ANNOTATIONS_READ_LOCAL)
@instrument
def list_available_metrics():
    """
    List all available GSC metrics with their descriptions.
    
    Returns:
        List of metric objects with api_name and description.
    """
    metrics = load_gsc_metrics()
    return metrics.get('metrics', [])

@mcp.tool(annotations=_ANNOTATIONS_READ_LOCAL)
@instrument
def skills_list():
    """
    List available analytical skills (playbooks) for Google Search Console.
    Use this to learn proven field combinations and how to interpret GSC data for specific SEO tasks. Fetch the full playbook with skill_read.
    """
    skills_dir = Path(__file__).parent / "skills"
    if not skills_dir.exists():
        return {"skills": [], "message": "No skills directory found."}
        
    available_skills = []
    for md_file in skills_dir.glob("*.md"):
        try:
            content = md_file.read_text()
            # Simple frontmatter parsing
            title = md_file.stem
            desc = ""
            for line in content.splitlines():
                if line.startswith("title:"): title = line.split(":", 1)[1].strip()
                if line.startswith("description:"): desc = line.split(":", 1)[1].strip()
            
            available_skills.append({
                "id": md_file.name,
                "title": title,
                "description": desc,
            })
        except Exception:
            pass
            
    return {"skills": available_skills}

@mcp.tool(annotations=_ANNOTATIONS_READ_LOCAL)
@instrument
def skill_read(skill_id: str):
    """
    Fetch the full content of one analytical skill (playbook) for Google Search Console.
    
    Args:
        skill_id: The skill id from skills_list (e.g., "brand_visibility.md")
        
    Returns:
        The full skill content with title, description, and playbook steps.
    """
    # Standard §6: skill_read EVENT (which skill, did the fetch work) —
    # additive to the tool_executed capture from @instrument.
    skills_dir = Path(__file__).parent / "skills"
    if not skills_dir.exists():
        send_telemetry("skill_read", {"skill_name": skill_id, "fetch_ok": False})
        return {"error": "No skills directory found."}

    skill_file = (skills_dir / skill_id).resolve()
    if not skill_file.is_relative_to(skills_dir.resolve()):
        send_telemetry("skill_read", {"skill_name": skill_id, "fetch_ok": False})
        return {"error": f"Skill '{skill_id}' not found. Call skills_list to see available skills."}
    if not skill_file.exists() or not skill_file.is_file():
        send_telemetry("skill_read", {"skill_name": skill_id, "fetch_ok": False})
        return {"error": f"Skill '{skill_id}' not found. Call skills_list to see available skills."}

    try:
        content = skill_file.read_text()
        title = skill_file.stem
        desc = ""
        for line in content.splitlines():
            if line.startswith("title:"): title = line.split(":", 1)[1].strip()
            if line.startswith("description:"): desc = line.split(":", 1)[1].strip()
        send_telemetry("skill_read", {"skill_name": skill_file.name, "fetch_ok": True})
        return {"id": skill_file.name, "title": title, "description": desc, "content": content}
    except Exception as e:
        send_telemetry("skill_read", {"skill_name": skill_id, "fetch_ok": False})
        return {"error": f"Error reading skill: {str(e)}"}

# --- S2: output schema for the primary data tool ---
# Declared via the SDK's structured-output mechanism (return annotation +
# structured_output=True on @mcp.tool). Verified empirically against
# mcp==2.0.0: the SDK builds the text content through the SAME
# _convert_to_content path with or without an output schema, so legacy-era
# clients see byte-identical text; structuredContent is emitted alongside
# (additive on the wire). The `| str` arm covers the config-error intercept
# string — without it the SDK's output validation would raise on that path.
class SearchAnalyticsResult(TypedDict, total=False):
    metadata: dict
    data: list[dict]
    summary: dict
    error: str


def _coerce_int(val, default: int, min_val: int | None = None, max_val: int | None = None) -> int:
    """Safely coerces integer parameters passed by LLMs as strings, floats, or None."""
    try:
        if val is None or val == "":
            res = default
        elif isinstance(val, (int, float)):
            res = int(val)
        else:
            res = int(float(str(val).strip().replace(",", "")))
    except (ValueError, TypeError):
        res = default
    if min_val is not None:
        res = max(min_val, res)
    if max_val is not None:
        res = min(max_val, res)
    return res


def _coerce_bool(val, default: bool = False) -> bool:
    """Safely coerces boolean parameters passed by LLMs as strings, numbers, or None."""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    s = str(val).strip().lower()
    if s in ("true", "1", "yes", "y", "t"):
        return True
    if s in ("false", "0", "no", "n", "f"):
        return False
    return default


def _get_search_analytics_impl(
    dimensions: list[str] = ["query"],
    start_date: str | None = None,
    end_date: str | None = None,
    filters: list[dict] | None = None,
    search_type: str = "web",
    row_limit: int = 1000,
    start_row: int = 0,
    summary_only: bool = False,
    intent: str = None,
    site_url: str | None = None,
    data_state: str = "final",
    fresh_data: bool = False,
    ctx: Context = None
):
    """The original get_search_analytics body, unchanged except the S3 brief
    hook in the except block. Sync (blocking Google API client); the async
    tool wrapper runs it in a worker thread exactly as the SDK used to."""
    try:
        row_limit = _coerce_int(row_limit, default=1000, min_val=1, max_val=25000)
        start_row = _coerce_int(start_row, default=0, min_val=0)
        summary_only = _coerce_bool(summary_only, default=False)
        
        if ctx and row_limit > 5000:
            fire_skill_tip(
                ctx=ctx,
                skill="brand_visibility",
                trigger="large_query",
                tool_name="get_search_analytics"
            )
            
        # Handle string input for dimensions
        if isinstance(dimensions, str):
            try:
                dimensions = json.loads(dimensions)
                if not isinstance(dimensions, list):
                    dimensions = [str(dimensions)]
            except json.JSONDecodeError:
                dimensions = [d.strip() for d in dimensions.split(',')]

        # Validate dimensions (case-insensitive & snake_case friendly)
        valid_dimensions = ["country", "device", "page", "query", "searchAppearance", "date"]
        dim_map = {d.lower(): d for d in valid_dimensions}
        dim_map["search_appearance"] = "searchAppearance"
        dim_map["searchappearance"] = "searchAppearance"
        if not dimensions:
            dimensions = ["query"]
        normalized_dims = []
        for dim in dimensions:
            canonical = dim_map.get(str(dim).strip().lower())
            if not canonical:
                return {"error": f"Invalid dimension '{dim}'. Valid dimensions: {valid_dimensions}"}
            normalized_dims.append(canonical)
        dimensions = normalized_dims
        
        # Set default dates if not provided
        if not start_date:
            start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        if not end_date:
            end_date = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
        
        # Handle filters
        request_filters = []
        if filters:
            if isinstance(filters, str):
                try:
                    filters = json.loads(filters)
                except json.JSONDecodeError:
                    return {"error": "Invalid filters format. Expected JSON array."}

            for filter_item in filters:
                # Validate filter dimension
                raw_dim = filter_item.get('dimension', '')
                filter_dim = dim_map.get(str(raw_dim).strip().lower())
                if not filter_dim:
                    return {"error": f"Invalid filter dimension '{raw_dim}'. Valid dimensions: {valid_dimensions}"}
                
                request_filters.append({
                    'dimension': filter_dim,
                    'operator': str(filter_item.get('operator', 'equals')).lower(),
                    'expression': filter_item.get('expression')
                })
        
        # Validate search type (case-insensitive)
        valid_search_types = ["web", "image", "video", "news", "discover", "googleNews"]
        type_map = {t.lower(): t for t in valid_search_types}
        type_map["googlenews"] = "googleNews"
        type_map["google_news"] = "googleNews"
        canonical_type = type_map.get(str(search_type or "web").strip().lower())
        if not canonical_type:
            return {"error": f"Invalid search_type '{search_type}'. Valid types: {valid_search_types}"}
        search_type = canonical_type
        
        target_site = _resolve_site_url(site_url)
        if not target_site:
            return {"error": "No site_url provided and no default GSC_SITE_URL configured."}

        # Build the request
        request = {
            'startDate': start_date,
            'endDate': end_date,
            'dimensions': dimensions,
            'searchType': search_type,
            'rowLimit': min(row_limit, 25000),  # GSC API limit
            'startRow': start_row
        }
        
        # S1: dataState support (fresh preliminary data vs finalized)
        if fresh_data or str(data_state or "").strip().lower() == "all":
            request['dataState'] = 'all'
        else:
            request['dataState'] = 'final'

        if request_filters:
            request['dimensionFilterGroups'] = [{
                'filters': request_filters
            }]
        
        # Execute the request
        service = get_gsc_service()
        response = service.searchanalytics().query(
            siteUrl=target_site,
            body=request
        ).execute()
        
        rows = response.get('rows', [])
        
        if summary_only:
            return {
                "summary": {
                    "site_url": target_site,
                    "total_clicks": sum(r.get('clicks', 0) for r in rows),
                    "total_impressions": sum(r.get('impressions', 0) for r in rows),
                    "avg_ctr": round((sum(r.get('clicks', 0) for r in rows) / sum(r.get('impressions', 0) for r in rows)) * 100, 2) if sum(r.get('impressions', 0) for r in rows) > 0 else 0,
                    "row_count": len(rows)
                }
            }
        
        # Format the response
        result = {
            'metadata': {
                'site_url': target_site,
                'start_date': start_date,
                'end_date': end_date,
                'dimensions': dimensions,
                'search_type': search_type,
                'data_state': request['dataState'],
                'total_rows': len(rows),
                'row_limit': row_limit,
                'start_row': start_row
            },
            'data': []
        }
        
        for row in rows:
            data_row = {}
            
            # Add dimension values
            if 'keys' in row:
                for i, dimension in enumerate(dimensions):
                    if i < len(row['keys']):
                        data_row[dimension] = str(row['keys'][i])
            
            # Add metric values (all GSC metrics are always returned)
            data_row['clicks'] = row.get('clicks', 0)
            data_row['impressions'] = row.get('impressions', 0)
            data_row['ctr'] = round(row.get('ctr', 0.0) * 100, 2)  # Convert to percentage
            data_row['position'] = round(row.get('position', 0.0), 1)
            
            result['data'].append(data_row)
        
        return result
        
    except Exception as e:
        # S3: user-fixable auth failures get a versioned brief; everything
        # else keeps the legacy error text byte-for-byte.
        brief = _api_error_text(e, "fetching search analytics")
        if brief:
            return {"error": brief}
        error_message = f"Error fetching GSC data: {str(e)}"
        print(error_message, file=sys.stderr)
        return {"error": error_message}


@mcp.tool(annotations=_ANNOTATIONS_READ_API, structured_output=True)
@instrument
async def get_search_analytics(
    dimensions: list[str] = ["query"],
    start_date: str | None = None,
    end_date: str | None = None,
    filters: list[dict] | None = None,
    search_type: str = "web",
    row_limit: int = 1000,
    start_row: int = 0,
    summary_only: bool = False,
    intent: str = None,
    site_url: str | None = None,
    data_state: str = "final",
    fresh_data: bool = False,
    ctx: Context = None
) -> SearchAnalyticsResult | str:
    """
    Retrieve Google Search Console search analytics data.
    
    Args:
        dimensions: List of dimensions from: country, device, page, query, searchAppearance, date
        start_date: Start date in YYYY-MM-DD format (defaults to 30 days ago)
        end_date: End date in YYYY-MM-DD format (defaults to 3 days ago)
        filters: List of filter objects (e.g., [{"dimension": "country", "operator": "equals", "expression": "usa"}])
        search_type: Type of search ('web', 'image', 'video', 'news', 'discover', 'googleNews')
        row_limit: Maximum number of rows to return (max 25000)
        start_row: Starting row for pagination (0-based)
        summary_only: If True, returns only aggregated totals (Token Efficient)
        intent: Short plain-English description of what the user is trying to learn/accomplish.
        site_url: Target site URL (e.g. 'sc-domain:example.com' or 'https://example.com/'). If omitted, uses configured site.
        data_state: 'final' (default, verified data ~72h delay) or 'all' (includes preliminary data from last 24-48h).
        fresh_data: If True, shorthand for data_state='all' to inspect recent preliminary search data.

    Returns:
        Dictionary containing search analytics data with clicks, impressions, ctr, and position metrics.
    """
    if SERVER_INIT_ERROR and ctx is not None and request_supports_elicitation(ctx):
        try:
            from gsc_setup_flow import run_inline_recovery
            recovered, message = await run_inline_recovery(ctx)
            if not recovered:
                return {"error": message}
        except Exception:
            if SERVER_INIT_ERROR:
                return f"Configuration Error: {SERVER_INIT_ERROR}. Please instruct the user to fix their setup."

    call = functools.partial(
        _get_search_analytics_impl,
        dimensions=dimensions, start_date=start_date, end_date=end_date,
        filters=filters, search_type=search_type, row_limit=row_limit,
        start_row=start_row, summary_only=summary_only, intent=intent,
        site_url=site_url, data_state=data_state, fresh_data=fresh_data,
        ctx=ctx,
    )
    result = await anyio.to_thread.run_sync(call)

    # S7: one retry at the wall — a mid-session auth failure (401/403) is
    # recoverable without a client restart when the user can be prompted.
    try:
        if (isinstance(result, dict) and "error" in result and ctx is not None
                and _LAST_BRIEF["error_category"] in ("IAMError", "AuthError")
                and request_supports_elicitation(ctx)):
            from gsc_setup_flow import run_inline_recovery
            recovered, _message = await run_inline_recovery(
                ctx, entry_category=_LAST_BRIEF["error_category"])
            if recovered:
                _set_brief(None, None)
                result = await anyio.to_thread.run_sync(call)
    except Exception:
        pass

    return result


@mcp.tool(annotations=_ANNOTATIONS_READ_API)
@instrument
def get_data_status(site_url: str | None = None, ctx: Context = None) -> dict:
    """Inspect the publication frontier of Google Search Console data for a property.
    
    Eliminates date-probing loops by returning the exact latest dates published by Google,
    the lag from wall clock, and whether fresh/preliminary data exists.
    
    Args:
        site_url: Target site URL (defaults to configured site or verified property)
        
    Returns:
        Dictionary with latest_final_date, latest_available_date, lag_days, and plain-English guidance.
    """
    target_site = _resolve_site_url(site_url)
    if not target_site:
        return {"error": "No site_url provided and no default GSC_SITE_URL configured."}

    try:
        service = get_gsc_service()
        today = datetime.now()
        start_date = (today - timedelta(days=14)).strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")

        # Query with dataState='all' to get the full frontier
        req = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": ["date"],
            "dataState": "all",
            "rowLimit": 30
        }
        resp = service.searchanalytics().query(siteUrl=target_site, body=req).execute()
        rows = resp.get("rows", [])
        
        dates = sorted([r["keys"][0] for r in rows if "keys" in r and r["keys"]])
        if not dates:
            # Fallback check with dataState='final'
            req["dataState"] = "final"
            resp2 = service.searchanalytics().query(siteUrl=target_site, body=req).execute()
            rows2 = resp2.get("rows", [])
            dates = sorted([r["keys"][0] for r in rows2 if "keys" in r and r["keys"]])

        if not dates:
            return {
                "site_url": target_site,
                "status": "no_data_found",
                "guidance": "No search impressions recorded in the trailing 14 days for this property."
            }

        latest_date = dates[-1]
        try:
            latest_dt = datetime.strptime(latest_date, "%Y-%m-%d")
            lag_days = (today - latest_dt).days
        except Exception:
            lag_days = None

        guidance = (
            f"Google Search Console data for {target_site} is published up to {latest_date} "
            f"({lag_days} days lag from wall clock). To avoid empty 0-row results, always set end_date <= '{latest_date}'."
        )

        return {
            "site_url": target_site,
            "latest_available_date": latest_date,
            "lag_days": lag_days,
            "days_returned_count": len(dates),
            "date_range_available": f"{dates[0]} to {latest_date}",
            "guidance": guidance
        }
    except Exception as e:
        brief = _api_error_text(e, "checking data status")
        if brief:
            return {"error": brief}
        return {"error": f"Error fetching GSC data status: {str(e)}"}


@mcp.tool(annotations=_ANNOTATIONS_READ_API)
@instrument
def inspect_url(url: str, site_url: str | None = None, ctx: Context = None) -> dict:
    """Inspect index status, canonical URLs, and crawl health of a specific URL via Google URL Inspection API.
    
    Args:
        url: Absolute URL to inspect (must belong to the verified site)
        site_url: Verified property URL (defaults to configured site or inferred from url host)
        
    Returns:
        Structured inspection result including indexing verdict, coverageState, lastCrawlTime, canonical URLs, and mobile usability.
    """
    target_site = _resolve_site_url(site_url)
    if not target_site:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            if parsed.hostname:
                target_site = _resolve_site_url(parsed.hostname)
        except Exception:
            pass

    if not target_site:
        return {"error": "Could not determine site_url for inspection. Please provide site_url explicitly."}

    try:
        service = get_gsc_service()
        body = {
            "inspectionUrl": url,
            "siteUrl": target_site
        }
        resp = service.urlInspection().index().inspect(body=body).execute()
        result = resp.get("inspectionResult", {})
        idx = result.get("indexStatusResult", {})
        mobile = result.get("mobileUsabilityResult", {})
        rich = result.get("richResultsResult", {})

        return {
            "inspection_url": url,
            "site_url": target_site,
            "verdict": idx.get("verdict", "NEUTRAL"),
            "coverage_state": idx.get("coverageState", "Unknown"),
            "indexing_state": idx.get("indexingState", "Unknown"),
            "robots_txt_state": idx.get("robotsTxtState", "Unknown"),
            "crawled_as": idx.get("crawledAs", "Unknown"),
            "last_crawl_time": idx.get("lastCrawlTime"),
            "page_fetch_state": idx.get("pageFetchState"),
            "google_canonical": idx.get("googleCanonical"),
            "user_canonical": idx.get("userCanonical"),
            "referring_urls": idx.get("referringUrls", []),
            "mobile_usability_verdict": mobile.get("verdict", "NEUTRAL"),
            "rich_results_verdict": rich.get("verdict", "NEUTRAL")
        }
    except Exception as e:
        brief = _api_error_text(e, f"inspecting URL {url}")
        if brief:
            return {"error": brief}
        return {"error": f"Error inspecting URL: {str(e)}"}


@mcp.tool(annotations=_ANNOTATIONS_READ_API)
@instrument
def compare_performance(
    current_start: str,
    current_end: str,
    prior_start: str | None = None,
    prior_end: str | None = None,
    dimensions: list[str] = ["query"],
    site_url: str | None = None,
    row_limit: int = 50,
    search_type: str = "web",
    ctx: Context = None
) -> dict:
    """Compare search performance between two date ranges (Period-over-Period) with server-side delta calculations.
    
    If prior_start/prior_end are omitted, the prior period is automatically computed with matching duration.
    
    Args:
        current_start: Start of current period (YYYY-MM-DD)
        current_end: End of current period (YYYY-MM-DD)
        prior_start: Start of prior period (YYYY-MM-DD) (optional)
        prior_end: End of prior period (YYYY-MM-DD) (optional)
        dimensions: List of dimensions to compare (default: ['query'])
        site_url: Target site URL (optional)
        row_limit: Max items to return in comparative breakdown (default: 50)
        search_type: Type of search ('web', 'image', 'video', 'news', 'discover', 'googleNews')
        
    Returns:
        Structured comparative summary, delta percentages, and top movers.
    """
    target_site = _resolve_site_url(site_url)
    
    # Auto-calculate prior dates if omitted
    curr_s_dt = datetime.strptime(current_start, "%Y-%m-%d")
    curr_e_dt = datetime.strptime(current_end, "%Y-%m-%d")
    duration = (curr_e_dt - curr_s_dt).days + 1
    
    if not prior_start or not prior_end:
        prior_e_dt = curr_s_dt - timedelta(days=1)
        prior_s_dt = prior_e_dt - timedelta(days=duration - 1)
        prior_start = prior_s_dt.strftime("%Y-%m-%d")
        prior_end = prior_e_dt.strftime("%Y-%m-%d")

    # Fetch current
    curr_res = _get_search_analytics_impl(
        dimensions=dimensions,
        start_date=current_start,
        end_date=current_end,
        site_url=target_site,
        search_type=search_type,
        row_limit=max(row_limit * 2, 500),
        ctx=ctx
    )
    if isinstance(curr_res, dict) and "error" in curr_res:
        return curr_res

    # Fetch prior
    prior_res = _get_search_analytics_impl(
        dimensions=dimensions,
        start_date=prior_start,
        end_date=prior_end,
        site_url=target_site,
        search_type=search_type,
        row_limit=max(row_limit * 2, 500),
        ctx=ctx
    )
    if isinstance(prior_res, dict) and "error" in prior_res:
        return prior_res

    curr_data = curr_res.get("data", []) if isinstance(curr_res, dict) else []
    prior_data = prior_res.get("data", []) if isinstance(prior_res, dict) else []

    curr_clicks = sum(r.get("clicks", 0) for r in curr_data)
    curr_impr = sum(r.get("impressions", 0) for r in curr_data)
    prior_clicks = sum(r.get("clicks", 0) for r in prior_data)
    prior_impr = sum(r.get("impressions", 0) for r in prior_data)

    clicks_delta = curr_clicks - prior_clicks
    clicks_pct = round((clicks_delta / prior_clicks) * 100, 2) if prior_clicks > 0 else (100.0 if curr_clicks > 0 else 0.0)
    impr_delta = curr_impr - prior_impr
    impr_pct = round((impr_delta / prior_impr) * 100, 2) if prior_impr > 0 else (100.0 if curr_impr > 0 else 0.0)

    def _key(row):
        return tuple(str(row.get(d, "")) for d in dimensions)

    prior_map = {_key(r): r for r in prior_data}
    comparison = []

    for r in curr_data:
        k = _key(r)
        p = prior_map.get(k, {})
        
        c_clicks = r.get("clicks", 0)
        p_clicks = p.get("clicks", 0)
        d_clicks = c_clicks - p_clicks
        pct_clicks = round((d_clicks / p_clicks) * 100, 2) if p_clicks > 0 else (100.0 if c_clicks > 0 else 0.0)

        c_impr = r.get("impressions", 0)
        p_impr = p.get("impressions", 0)
        d_impr = c_impr - p_impr

        row_dict = {d: r.get(d) for d in dimensions}
        row_dict.update({
            "clicks_current": c_clicks,
            "clicks_prior": p_clicks,
            "clicks_delta": d_clicks,
            "clicks_pct_change": pct_clicks,
            "impressions_current": c_impr,
            "impressions_prior": p_impr,
            "impressions_delta": d_impr,
            "position_current": r.get("position", 0.0),
            "position_prior": p.get("position", 0.0),
            "position_delta": round(r.get("position", 0.0) - p.get("position", 0.0), 1)
        })
        comparison.append(row_dict)

    comparison.sort(key=lambda x: abs(x["clicks_delta"]), reverse=True)

    return {
        "site_url": target_site,
        "current_period": f"{current_start} to {current_end}",
        "prior_period": f"{prior_start} to {prior_end}",
        "summary": {
            "current_clicks": curr_clicks,
            "prior_clicks": prior_clicks,
            "clicks_delta": clicks_delta,
            "clicks_pct_change": clicks_pct,
            "current_impressions": curr_impr,
            "prior_impressions": prior_impr,
            "impressions_delta": impr_delta,
            "impressions_pct_change": impr_pct,
        },
        "comparison": comparison[:row_limit]
    }


@mcp.tool(annotations=_ANNOTATIONS_READ_API)
@instrument
def get_sitemaps(site_url: str | None = None):
    """
    Get all sitemaps for the verified site.
    
    Args:
        site_url: Target site URL (defaults to configured site)
        
    Returns:
        List of sitemaps with their status and details.
    """
    try:
        target_site = _resolve_site_url(site_url)
        if not target_site:
            return {"error": "No site_url provided and no default GSC_SITE_URL configured."}
        service = get_gsc_service()
        sitemaps = service.sitemaps().list(siteUrl=target_site).execute()
        
        result = []
        for sitemap in sitemaps.get('sitemap', []):
            result.append({
                'path': sitemap.get('path'),
                'lastSubmitted': sitemap.get('lastSubmitted'),
                'isPending': sitemap.get('isPending', False),
                'isSitemapsIndex': sitemap.get('isSitemapsIndex', False),
                'type': sitemap.get('type'),
                'lastDownloaded': sitemap.get('lastDownloaded'),
                'warnings': sitemap.get('warnings', 0),
                'errors': sitemap.get('errors', 0)
            })
        
        return result
        
    except Exception as e:
        brief = _api_error_text(e, "fetching sitemaps")
        if brief:
            return {"error": brief}
        return {"error": f"Error fetching sitemaps: {str(e)}"}


@mcp.tool(annotations=_ANNOTATIONS_WRITE_API)
@instrument
def submit_sitemap(sitemap_url: str, site_url: str | None = None):
    """
    Submit a sitemap to Google Search Console.
    
    Args:
        sitemap_url: Full URL of the sitemap to submit
        site_url: Target site URL (defaults to configured site)
        
    Returns:
        Success message or error details.
    """
    try:
        target_site = _resolve_site_url(site_url)
        if not target_site:
            return {"error": "No site_url provided and no default GSC_SITE_URL configured."}
        service = get_gsc_service()
        service.sitemaps().submit(
            siteUrl=target_site,
            feedpath=sitemap_url
        ).execute()
        
        return {"success": f"Sitemap submitted successfully: {sitemap_url}"}
        
    except Exception as e:
        brief = _api_error_text(e, "submitting a sitemap")
        if brief:
            return {"error": brief}
        return {"error": f"Error submitting sitemap: {str(e)}"}


@mcp.tool(annotations=_ANNOTATIONS_DELETE_API)
@instrument
def delete_sitemap(sitemap_url: str, site_url: str | None = None):
    """
    Delete a sitemap from Google Search Console.
    
    Args:
        sitemap_url: Full URL of the sitemap to delete
        site_url: Target site URL (defaults to configured site)
        
    Returns:
        Success message or error details.
    """
    try:
        target_site = _resolve_site_url(site_url)
        if not target_site:
            return {"error": "No site_url provided and no default GSC_SITE_URL configured."}
        service = get_gsc_service()
        service.sitemaps().delete(
            siteUrl=target_site,
            feedpath=sitemap_url
        ).execute()
        
        return {"success": f"Sitemap deleted successfully: {sitemap_url}"}
        
    except Exception as e:
        brief = _api_error_text(e, "deleting a sitemap")
        if brief:
            return {"error": brief}
        return {"error": f"Error deleting sitemap: {str(e)}"}


@mcp.tool(
    name="check_for_updates",
    description="Check PyPI for newer versions of this MCP server and get upgrade instructions.",
    annotations=_ANNOTATIONS_READ_LOCAL,
)
@instrument
def check_for_updates() -> dict:
    """Check PyPI for newer versions of google-search-console-mcp."""
    return check_server_update("google-search-console-mcp", MCP_SERVER_VERSION, force_check=True)


# --- S5: skills mirrored as MCP resources (Protocol Surfaces v1) ---
# Same content as skill_read (local packaged file), discoverable without a
# tool call. Pull-only: costs nothing until a client actually reads one.
def _register_skill_resources():
    try:
        skills_dir = Path(__file__).parent / "skills"
        if not skills_dir.exists():
            return
        for md_file in sorted(skills_dir.glob("*.md")):
            stem = md_file.stem
            uri = f"skill://{stem}"
            title, desc = stem, ""
            try:
                for line in md_file.read_text().splitlines():
                    if line.startswith("title:"):
                        title = line.split(":", 1)[1].strip()
                    if line.startswith("description:"):
                        desc = line.split(":", 1)[1].strip()
            except Exception:
                pass

            def _make_reader(path=md_file, resource_uri=uri):
                def _skill_resource():
                    content = path.read_text()
                    # Standard §4/§8: resource_read is a registered event that
                    # finally gets an emitter; resource_uri is its prop.
                    send_telemetry("resource_read", {"resource_uri": resource_uri})
                    return content
                _skill_resource.__name__ = f"skill_resource_{path.stem}"
                return _skill_resource

            mcp.resource(
                uri,
                name=f"skill-{stem}",
                title=title,
                description=desc or f"GSC analysis skill: {stem}",
                mime_type="text/markdown",
            )(_make_reader())
    except Exception as e:
        # Resources are additive — a registration failure must never stop boot.
        print(f"Warning: skill resources not registered: {e}", file=sys.stderr)


_register_skill_resources()


# --- S6: workflow prompts (Protocol Surfaces v1) ---
# User-invokable packaged workflows (client UIs surface these). Each teaches
# the model the server's quirks: discover names first, read the skill, pass
# intent. Pull-only — no token cost until a user invokes one.
def _prompt_used(prompt_name, has_args):
    try:
        send_telemetry("prompt_used", {"prompt_name": prompt_name, "has_args": bool(has_args)})
    except Exception:
        pass


@mcp.prompt(name="analyze-brand-visibility", title="Analyze brand visibility",
            description="How visible is a brand in Google Search? Branded vs non-branded share, CTR, positions.")
def analyze_brand_visibility(brand_terms: str = "") -> str:
    _prompt_used("analyze-brand-visibility", bool(brand_terms))
    terms = brand_terms.strip() or "(ask the user for their brand name plus common variants and misspellings)"
    return f"""Analyze brand visibility in Google Search using the Google Search Console tools.
Brand terms: {terms}

Work this way:
1. Call skill_read("brand_visibility.md") first and follow its playbook — it has the proven field combinations.
2. Call list_available_dimensions before querying; never guess dimension names.
3. Query get_search_analytics with dimensions=["query"] and a filter {{"dimension": "query", "operator": "contains", "expression": "<brand term>"}} — one call per brand variant. Always pass intent (e.g. intent="brand visibility analysis for <term>").
4. Run one unfiltered call for site totals, then compute branded vs non-branded share.
5. Report: branded share of clicks/impressions, branded CTR vs overall CTR, average position for exact brand queries (should be near 1 — higher means a problem), and any misspellings ranking poorly.

Quirks to respect: ctr in results is already a percentage; position is an average where LOWER is better; dates default to the last 30 days ending 3 days ago because GSC data lags about 3 days."""


@mcp.prompt(name="content-opportunities", title="Find content opportunities",
            description="Striking-distance queries, low-CTR pages, and content gaps worth acting on.")
def content_opportunities(focus_area: str = "") -> str:
    _prompt_used("content-opportunities", bool(focus_area))
    focus = f"Focus area: {focus_area.strip()}\n" if focus_area.strip() else ""
    return f"""Find concrete content opportunities from Google Search Console data.
{focus}
Work this way:
1. Call skill_read("citation_opportunities.md") and skill_read("intent_efficiency.md") — follow their playbooks.
2. Call list_available_dimensions first; then get_search_analytics with dimensions=["query", "page"] and a generous row_limit. Always pass intent="find content opportunities".
3. Striking distance: queries at position 5-20 with real impressions but few clicks — improving those pages moves them to page one.
4. CTR gaps: queries at position < 10 with CTR under ~1% — title/meta rewrites, not new content.
5. Content gaps: recurring queries with no dedicated page — new content candidates.
6. Check dimensions=["searchAppearance"] (see skill_read("search_appearance_audit.md")) for AI Overview and rich-result exposure worth auditing.

Quirks to respect: use summary_only=true for cheap totals before pulling rows; ctr is already a percentage; position is an average where lower is better."""


@mcp.prompt(name="diagnose-traffic-drop", title="Diagnose a traffic drop",
            description="Compare two periods and localize a search traffic drop: which segment, impressions vs CTR, likely cause.")
def diagnose_traffic_drop(drop_period: str = "") -> str:
    _prompt_used("diagnose-traffic-drop", bool(drop_period))
    period = drop_period.strip() or "(ask the user roughly when the drop started; default to comparing the last 14 days against the 14 days before)"
    return f"""Diagnose a Google Search traffic drop using the Search Console tools.
Drop period: {period}

Work this way:
1. Call list_available_dimensions first. Pass intent="diagnose traffic drop" on every get_search_analytics call.
2. Shape first: get_search_analytics with dimensions=["date"] across a window covering both the drop and the baseline — confirm when it actually started and whether clicks, impressions, or both fell.
3. Then two matched-length pulls (drop window vs prior window) segmented one dimension at a time: ["query"], ["page"], ["device"], ["country"], ["searchAppearance"].
4. Localize: is the loss concentrated in brand or non-brand queries, specific pages, one device, one country, or one search appearance type?
5. Distinguish the failure class: impressions fell at stable position = indexing/coverage issue; position fell = ranking loss; CTR fell at stable position = SERP feature change stealing clicks.
6. Check get_sitemaps for errors or warnings while you are at it.
7. Report the drop's shape, the losing segment, and the most likely cause class — with the numbers that support it.

Quirks to respect: GSC data lags about 3 days — never include the last 3 days in either window; ctr is already a percentage; position is an average where lower is better."""


# S7: the interactive setup-recovery tool (registers setup_gsc_access).
# Imported last so every name it needs from this module already exists.
# The sys.modules alias matters: under `python -m gsc_mcp_server` this module
# is "__main__", and without the alias gsc_setup_flow's `import gsc_mcp_server`
# would build a SECOND module instance with its own MCPServer — registering
# the setup tool on a server that never runs (verified empirically).
sys.modules.setdefault("gsc_mcp_server", sys.modules[__name__])
import gsc_setup_flow  # noqa: E402,F401


def main():
    """Main entry point for the MCP server"""
    # Use stdio transport ONLY - this is critical for MCP with Claude
    print("Starting GSC MCP server...", file=sys.stderr)
    mark_boot_events()
    send_telemetry("mcp_started", {"config_status": "error" if SERVER_INIT_ERROR else "success"})
    # mcp.run() defaults to stdio; pass explicitly for clarity/parity with v1.
    mcp.run(transport="stdio")

# Start the server when run directly
if __name__ == "__main__":
    main()