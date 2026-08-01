# SPDX-License-Identifier: MIT

"""Anonymous usage telemetry for the Google Search Console MCP server.

Dual-era identity capture (MCP 2.0 stateless _meta + legacy handshake), opt-out
honored, no PII / no GSC data / no paths, product UA. Transport: direct to
PostHog (the released v0.5.0 contract, shared project keyed by mcp_server_name).

There is currently NO dedicated gsc.builditwithai.xyz gateway (only the GA4
worker exists), so we keep the released direct-to-PostHog POST rather than guess
a host. To move behind a gateway later, point _POST_URL at the gateway's /e
endpoint and drop the api_key from the payload — see the seam below.
"""

import os
import sys
import time
import json
import uuid
import atexit
import platform
import threading
import urllib.request

# --- Transport seam -------------------------------------------------------
# Released v0.5.0 posts straight to PostHog. If/when a gsc gateway exists,
# set _POST_URL = "https://<host>/e" and _USE_GATEWAY = True (the gateway
# injects the key server-side, so the payload drops api_key).
POSTHOG_API_KEY = "phc_Aik6H3pf5P9dPBrWLjd6N3wzsVAD6tJnmmEhFwW8Pzsi"
POSTHOG_HOST = "https://us.i.posthog.com"
_POST_URL = f"{POSTHOG_HOST}/capture/"
_USE_GATEWAY = False
# --------------------------------------------------------------------------

SCHEMA_VERSION = 1

try:
    import importlib.metadata
    MCP_SERVER_VERSION = importlib.metadata.version("google-search-console-mcp")
except Exception:
    MCP_SERVER_VERSION = "unknown"


def _telemetry_disabled() -> bool:
    """Any disable flag wins. Keeps the released GSC_MCP_TELEMETRY=false and
    adds the standard opt-out set (DISABLE_TELEMETRY / DO_NOT_TRACK / NO_TELEMETRY)."""
    if os.getenv("GSC_MCP_TELEMETRY", "true").lower() in ("false", "0", "off"):
        return True
    for var in ("DISABLE_TELEMETRY", "DO_NOT_TRACK", "NO_TELEMETRY"):
        if os.getenv(var, "").lower() in ("1", "true", "yes", "on"):
            return True
    return False


TELEMETRY_DISABLED = _telemetry_disabled()

SESSION_ID = str(uuid.uuid4())
IN_VIRTUAL_ENV = sys.prefix != sys.base_prefix
CPU_ARCH = platform.machine()
TIMEZONE_OFFSET = -time.timezone if (time.localtime().tm_isdst == 0) else -time.altzone


# Handshake / _meta clientInfo, captured on the first tool call that carries a
# ctx (the handshake is post-boot; identity is not known at server start).
_RUNTIME_CLIENT = {
    "name": None, "version": None, "protocol_version": None, "caps": None,
}


def _meta_as_dict(meta):
    """Per-request _meta may be a plain dict (2026 stateless clients) or a
    pydantic model. Normalize to a dict, preserving io.modelcontextprotocol/* keys."""
    if meta is None:
        return {}
    if isinstance(meta, dict):
        return meta
    extra = getattr(meta, "__pydantic_extra__", None) or getattr(meta, "model_extra", None)
    if isinstance(extra, dict) and extra:
        return extra
    try:
        return meta.model_dump(by_alias=True)
    except Exception:
        return {}


def capture_client_info(ctx):
    """Populate _RUNTIME_CLIENT once, from whichever era the client speaks.

    MCP 2.0 (2026-07-28) is stateless: identity rides in per-request _meta and
    there is no initialize handshake. Older clients (today's fleet) still do the
    handshake, so identity lives on ctx.session.client_params. Idempotent."""
    if _RUNTIME_CLIENT["name"] is not None or ctx is None:
        return
    try:
        info = None
        caps_raw = None
        proto = getattr(ctx, "protocol_version", None)

        # 2026-07-28 stateless: identity in per-request _meta.
        req_ctx = getattr(ctx, "request_context", None)
        meta = _meta_as_dict(getattr(req_ctx, "meta", None) if req_ctx else None)
        if meta:
            mci = meta.get("io.modelcontextprotocol/clientInfo")
            if isinstance(mci, dict) and mci.get("name"):
                info = mci
                caps_raw = (meta.get("io.modelcontextprotocol/clientCapabilities")
                            or meta.get("io.modelcontextprotocol/capabilities"))
                proto = proto or meta.get("io.modelcontextprotocol/protocolVersion")

        # Legacy handshake on the session. MCP 2.0 official SDK exposes
        # client_info (snake) while mcp 1.x / fastmcp uses clientInfo (camel);
        # try snake first, fall back to camel.
        if info is None:
            sess = getattr(ctx, "session", None)
            params = getattr(sess, "client_params", None) if sess else None
            ci = None
            if params is not None:
                ci = getattr(params, "client_info", None) or getattr(params, "clientInfo", None)
            if ci is not None and getattr(ci, "name", None):
                info = {"name": ci.name, "version": getattr(ci, "version", None)}
                proto = (proto or getattr(params, "protocol_version", None)
                         or getattr(params, "protocolVersion", None))
                caps_obj = getattr(params, "capabilities", None)
                if caps_obj is not None:
                    try:
                        caps_raw = caps_obj.model_dump(mode="json", exclude_none=True)
                    except Exception:
                        caps_raw = None

        if not info or not info.get("name"):
            # Fall back to client_capabilities on the ctx if present (mcp 2.0).
            caps_obj = getattr(ctx, "client_capabilities", None)
            if caps_obj is not None:
                try:
                    caps_raw = caps_obj.model_dump(mode="json", exclude_none=True)
                    _RUNTIME_CLIENT["caps"] = caps_raw
                except Exception:
                    pass
            if proto and not _RUNTIME_CLIENT["protocol_version"]:
                _RUNTIME_CLIENT["protocol_version"] = str(proto)
            return

        _RUNTIME_CLIENT["name"] = str(info.get("name"))
        _RUNTIME_CLIENT["version"] = str(info.get("version")) if info.get("version") else None
        _RUNTIME_CLIENT["protocol_version"] = str(proto) if proto else None
        if isinstance(caps_raw, dict):
            _RUNTIME_CLIENT["caps"] = caps_raw
    except Exception:
        pass


_PENDING_SENDS = []


def _drain_pending_sends(deadline_seconds=2.0):
    end = time.time() + deadline_seconds
    for th in list(_PENDING_SENDS):
        remaining = end - time.time()
        if remaining <= 0:
            break
        try:
            th.join(remaining)
        except Exception:
            pass


atexit.register(_drain_pending_sends)


def send_telemetry(event: str, properties: dict = None):
    """Fire-and-forget anonymous telemetry on a daemon thread (joined briefly at
    exit). No-op when opted out; never raises."""
    if TELEMETRY_DISABLED:
        return

    def _send():
        try:
            props = {
                "schema_version": SCHEMA_VERSION,
                "mcp_server_name": "google-search-console-mcp",
                "$os": platform.system(),
                "python_version": platform.python_version(),
                "mcp_server_version": MCP_SERVER_VERSION,
                "cpu_arch": CPU_ARCH,
                "in_virtual_env": IN_VIRTUAL_ENV,
                "timezone_offset": TIMEZONE_OFFSET,
                "session_id": SESSION_ID,
                **(properties or {}),
            }
            if _RUNTIME_CLIENT["name"]:
                props.setdefault("mcp_client_name", _RUNTIME_CLIENT["name"])
                props.setdefault("mcp_client_version", _RUNTIME_CLIENT["version"])
            if _RUNTIME_CLIENT["protocol_version"]:
                props.setdefault("mcp_protocol_version", _RUNTIME_CLIENT["protocol_version"])
            if _RUNTIME_CLIENT["caps"] is not None:
                props.setdefault("client_capabilities", _RUNTIME_CLIENT["caps"])
            props["$process_person_profile"] = False  # no person profiles

            if _USE_GATEWAY:
                payload = {"event": event, "distinct_id": SESSION_ID, "properties": props}
            else:
                payload = {"api_key": POSTHOG_API_KEY, "event": event,
                           "distinct_id": SESSION_ID, "properties": props}

            req = urllib.request.Request(
                _POST_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    # Product UA: gateway 403s default library UAs. Harmless direct.
                    "User-Agent": f"google-search-console-mcp/{MCP_SERVER_VERSION}",
                },
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            pass

    th = threading.Thread(target=_send, daemon=True)
    th.start()
    _PENDING_SENDS.append(th)
    if len(_PENDING_SENDS) > 8:
        _PENDING_SENDS[:] = [t for t in _PENDING_SENDS if t.is_alive()]


def announce_first_run():
    """One-time stderr disclosure of anonymous telemetry + opt-out, matching the
    GA4 sibling's privacy posture. Best-effort marker in ~/.gsc_mcp/."""
    if TELEMETRY_DISABLED:
        return
    try:
        from pathlib import Path
        d = Path.home() / ".gsc_mcp"
        d.mkdir(parents=True, exist_ok=True)
        marker = d / "announced_v1"
        if marker.exists():
            return
        print(
            "google-search-console-mcp collects anonymous usage telemetry (no PII, "
            "no GSC data, no paths). Opt out any time with DISABLE_TELEMETRY=1 or "
            "DO_NOT_TRACK=1 or GSC_MCP_TELEMETRY=false.",
            file=sys.stderr,
        )
        marker.write_text("1", encoding="utf-8")
    except Exception:
        pass
