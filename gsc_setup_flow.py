# SPDX-License-Identifier: MIT

"""Interactive setup recovery (Protocol Surfaces v1, S7 — the gsc headline).

When config is broken, uses MCP elicitation to collect the missing value —
GOOGLE_APPLICATION_CREDENTIALS (a file PATH, never the key's contents) and/or
GSC_SITE_URL (a property URL) — validates it, applies it for THIS SESSION
ONLY, re-initializes, and lets the failing call retry without a client
restart. Ported from ga4's setup_flow.py (field-proven prior art), adapted to
gsc's two config values. Clients that don't declare elicitation support get
the guided S3 brief exactly as before.

Privacy: elicited values are applied to the process env and echoed back to
the chat only. They are NEVER sent to telemetry (setup_flow events carry
branch/action/outcome enums only) and never persisted to disk.
"""

import os
import json

from pydantic import BaseModel, Field
from mcp.server.mcpserver import Context

# Import the module (not names) so reads always see the CURRENT init state —
# reinitialize() rebinds these globals mid-session.
import gsc_mcp_server as server
from gsc_telemetry import send_telemetry, request_supports_elicitation


class _CredentialsPath(BaseModel):
    credentials_path: str = Field(
        description="Absolute path to your Google service-account JSON key")


class _SiteUrl(BaseModel):
    site_url: str = Field(
        description="Your Search Console property, exactly as shown in the property "
                    "selector: 'https://example.com/' or 'sc-domain:example.com'")


class _Confirm(BaseModel):
    done: bool = Field(description="Set true once you have completed the step")


def _emit_flow(branch, action, outcome, reinit_category=None, elicit_supported=True):
    """Recovery-funnel telemetry (ga4's setup_flow schema, reused verbatim):
    which branch ran, what the user chose, how it ended. Outcomes only —
    elicited values are never sent."""
    send_telemetry("setup_flow", {
        "flow_branch": branch,
        "elicit_action": str(action) if action is not None else None,
        "flow_outcome": outcome,
        "reinit_category": reinit_category,
        "error_category_at_entry": server.SERVER_INIT_ERROR_CATEGORY,
        "elicit_supported": elicit_supported,
    })


def _inspect_credentials_file(path):
    """('missing'|'invalid'|'ok', detail). Validation per spec: file exists and
    the JSON has a 'type' field. Reads ONLY that one field — a service-account
    key contains a private key, and its contents must never leave this check."""
    try:
        if not os.path.isfile(path):
            return "missing", None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "type" not in data:
            return "invalid", "no 'type' field — not a Google credentials JSON"
        return "ok", str(data.get("type"))
    except json.JSONDecodeError:
        return "invalid", "not valid JSON"
    except Exception as e:
        return "invalid", e.__class__.__name__


def _valid_site_url(value):
    v = (value or "").strip()
    return v.startswith("http://") or v.startswith("https://") or v.startswith("sc-domain:")


_PERSIST_HINT = (
    "These values are set for the current session only. To make them permanent, add "
    'GOOGLE_APPLICATION_CREDENTIALS and GSC_SITE_URL to the "env" block of this '
    "server in your MCP client config."
)


async def run_inline_recovery(ctx, entry_category=None):
    """The setup-recovery engine, callable from any tool that hits a broken
    config with an elicitation-capable client. Asks the user for the missing
    value through the client, validates, applies in-session, re-initializes
    (one sites().list() verification call), and returns (recovered, message).
    recovered=True only when GSC is now reachable AND the configured property
    is accessible. get_search_analytics calls this at the point of friction;
    setup_gsc_access wraps it as a standalone tool."""
    # Entry without a known broken state: verify for real before claiming OK
    # (a present-but-invalid key passes the boot checks, which are env-only).
    if not server.SERVER_INIT_ERROR and entry_category is None:
        ok, cat, _detail = server.reinitialize()
        if ok:
            _emit_flow("none_needed", None, "already_ok")
            return True, ("GSC access is already configured and working — property "
                          f"'{os.getenv('GSC_SITE_URL')}' is reachable. No setup needed.")
        entry_category = server.SERVER_INIT_ERROR_CATEGORY

    branch = "other"
    category = entry_category or server.SERVER_INIT_ERROR_CATEGORY
    try:
        # 1. Credentials path missing or dangling — collect a path (not a secret).
        creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if not creds or not os.path.exists(creds):
            branch = "credentials"
            r = await ctx.elicit(
                "Where is your Google service-account JSON key on this machine? "
                "Paste its absolute path. (No key yet? Create one at Google Cloud "
                "Console > IAM & Admin > Service Accounts > Keys, then add that "
                "service account's email as a user in Search Console > Settings > "
                "Users and permissions.)",
                _CredentialsPath,
            )
            if r.action != "accept" or not r.data:
                _emit_flow(branch, r.action, "paused")
                return False, ("Setup paused — no credentials path provided. "
                               "Call setup_gsc_access when ready.")
            path = r.data.credentials_path.strip()
            state, detail = _inspect_credentials_file(path)
            if state == "missing":
                _emit_flow(branch, r.action, "invalid_path")
                return False, (f"No file exists at '{path}'. Ask the user for the correct "
                               "absolute path to their Google service-account JSON key, "
                               "then call setup_gsc_access again.")
            if state == "invalid":
                _emit_flow(branch, r.action, "invalid_creds")
                return False, (f"The file at '{path}' is not a Google credentials JSON "
                               f"({detail}). Ask the user to re-download the service-account "
                               "key from Google Cloud Console > IAM & Admin > Service Accounts "
                               "> Keys, then call setup_gsc_access again.")
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path

        # 2. Property not configured — collect the exact GSC property string.
        if not os.getenv("GSC_SITE_URL"):
            branch = "site_url"
            r = await ctx.elicit(
                "Which Search Console property should I query? Paste it EXACTLY as it "
                "appears in the Search Console property selector — either a URL-prefix "
                "property like 'https://example.com/' (trailing slash included) or a "
                "Domain property like 'sc-domain:example.com'.",
                _SiteUrl,
            )
            if r.action != "accept" or not r.data:
                _emit_flow(branch, r.action, "paused")
                return False, ("Setup paused — no property provided. "
                               "Call setup_gsc_access when ready.")
            site = r.data.site_url.strip()
            if not _valid_site_url(site):
                _emit_flow(branch, r.action, "invalid_site")
                return False, (f"'{site}' does not look like a Search Console property — it "
                               "must start with 'https://' (URL-prefix property) or "
                               "'sc-domain:' (Domain property). Ask the user to copy it from "
                               "the Search Console property selector, then call "
                               "setup_gsc_access again.")
            os.environ["GSC_SITE_URL"] = site

        # 3. Config present but rejected by Google — terminal fixes, confirmed.
        if branch == "other" and category == "IAMError":
            branch = "property_access"
            r = await ctx.elicit(
                "The service account has no access to property "
                f"'{os.getenv('GSC_SITE_URL')}'. In Search Console "
                "(search.google.com/search-console) > Settings > Users and permissions, "
                "add the service account's email (the client_email inside the JSON key "
                "file) as a user — 'Full' permission is enough. Also check the property "
                "string matches EXACTLY ('https://example.com/' and "
                "'sc-domain:example.com' are different properties). Set 'done' to true "
                "once added and I'll reconnect.",
                _Confirm,
            )
            if r.action != "accept" or not r.data or not r.data.done:
                _emit_flow(branch, r.action, "paused")
                return False, ("Setup paused — grant the service account access in Search "
                               "Console, then call setup_gsc_access.")

        elif branch == "other" and category == "AuthError":
            branch = "invalid_credentials"
            r = await ctx.elicit(
                "Google rejected the current JSON key (malformed, revoked, or the wrong "
                "kind of file). Download a fresh service-account key from Google Cloud "
                "Console > IAM & Admin > Service Accounts > Keys, then paste its "
                "absolute path here.",
                _CredentialsPath,
            )
            if r.action != "accept" or not r.data:
                _emit_flow(branch, r.action, "paused")
                return False, ("Setup paused — no replacement key provided. "
                               "Call setup_gsc_access when ready.")
            path = r.data.credentials_path.strip()
            state, detail = _inspect_credentials_file(path)
            if state != "ok":
                _emit_flow(branch, r.action, "invalid_creds")
                return False, (f"The file at '{path}' is not a usable Google credentials "
                               f"JSON ({detail or 'missing'}). Ask the user to re-download "
                               "the key, then call setup_gsc_access again.")
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path

        elif branch == "other" and server.SERVER_INIT_ERROR:
            r = await ctx.elicit(
                f"Setup issue: {server.SERVER_INIT_ERROR} Fix it, then set 'done' to "
                "true to retry.",
                _Confirm,
            )
            if r.action != "accept" or not r.data or not r.data.done:
                _emit_flow(branch, r.action, "paused")
                return False, "Setup paused. Call setup_gsc_access after fixing the issue above."

    except Exception:
        # The client advertised elicitation but the prompt failed anyway —
        # fall back to guided text (ga4-proven safety net).
        _emit_flow(branch, None, "elicit_unsupported")
        manual = server.SERVER_INIT_ERROR or "check GOOGLE_APPLICATION_CREDENTIALS and GSC_SITE_URL"
        return False, ("This client can't prompt interactively. To fix setup manually: "
                       f"{manual} Then restart your MCP client.")

    # Retry init with the updated environment (verifies against the GSC API).
    ok, cat, detail = server.reinitialize()
    if ok:
        _emit_flow(branch, "accept", "fixed", cat)
        return True, ("GSC access is now working — property "
                      f"'{os.getenv('GSC_SITE_URL')}' is reachable. " + _PERSIST_HINT)
    _emit_flow(branch, "accept", "still_broken", cat)
    followup = server.SERVER_INIT_ERROR or detail
    return False, (f"Still not connected ({cat}). {followup} "
                   "Call setup_gsc_access to try again.")


# structured_output=False: the SDK auto-derives an output schema from the
# `-> str` annotation otherwise, and S2 scopes outputSchema to primary data
# tools only.
@server.mcp.tool(annotations=server._ANNOTATIONS_SETUP, structured_output=False)
@server.instrument
async def setup_gsc_access(ctx: Context) -> str:
    """
    Interactively fix a broken Google Search Console MCP setup (missing or
    wrong credentials path, missing GSC_SITE_URL, invalid key, or missing
    property access) by asking the user for the needed value through the
    client, then re-initializing without a restart. Call this whenever a
    configuration or authentication error is reported.
    """
    if not request_supports_elicitation(ctx):
        # Gate on the declared capability (spec S7): non-supporting clients
        # get guided text — never a prompt they cannot render.
        _emit_flow("gate", None, "elicit_unsupported", elicit_supported=False)
        manual = server.SERVER_INIT_ERROR or "check GOOGLE_APPLICATION_CREDENTIALS and GSC_SITE_URL"
        return ("This client can't prompt interactively. To fix setup manually: "
                f"{manual} Update the env block of this server in your MCP client "
                "config, then restart the client.")
    _recovered, message = await run_inline_recovery(ctx)
    return message
