/**
 * Telemetry gateway for GSC MCP.
 * /e ingests events per the MCP Telemetry Standard contract:
 * accept-and-tag (malformed/unknown names forwarded, tagged), IP stripped,
 * UA guard, geo from CF-IPCountry, traffic_class from the server's internal flag.
 */

const GATEWAY_VERSION = "1";
const SERVER_NAME = "google-search-console-mcp";

// Standard event registry union. Unknown events are still forwarded, just tagged.
const KNOWN_EVENTS = new Set([
  "server_first_install", "package_download", "mcp_started", "tools_listed",
  "tool_executed", "session_end", "skill_tip_shown", "skill_read",
  "resource_read", "server_discovered", "install_intent", "install_completed",
  "surface_click", "prompt_used", "setup_flow",
]);

const EVENT_NAME_RE = /^[a-z_][a-z0-9_]{0,63}$/;

const DISTINCT_ID_RE = /^(inst_|anon_)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Default-library UAs are rejected unless the caller marks itself internal.
const REJECTED_UAS = [
  "python-requests", "python-urllib", "go-http-client", "node-fetch",
  "axios/", "curl/", "wget/",
];

// Technical ceiling only: PostHog rejects capture payloads near 1MB.
const MAX_PROPS_BYTES = 900000;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const userAgent = request.headers.get("user-agent") || "";
    const uaLower = userAgent.toLowerCase();
    const internal = request.headers.get("x-gsc-mcp-internal") === "1";

    if (request.method !== "POST" || url.pathname !== "/e") {
      return new Response(JSON.stringify({ error: "not_found" }), {
        status: 404, headers: { "content-type": "application/json" },
      });
    }

    // Honor the Do-Not-Track convention (consoledonottrack.com / Scarf precedent).
    const dnt = request.headers.get("dnt") === "1" || request.headers.get("sec-gpc") === "1";
    if (dnt) {
      return new Response(JSON.stringify({ recorded: false, reason: "dnt" }), {
        headers: { "content-type": "application/json" },
      });
    }

    let body;
    try {
      body = await request.json();
    } catch (e) {
      return new Response(JSON.stringify({ recorded: false, reason: "invalid_json" }), {
        status: 400, headers: { "content-type": "application/json" },
      });
    }

    // Accept-and-tag: malformed/unknown names are forwarded with tags, never dropped.
    const rawEventName = typeof body.event === "string" ? body.event : "";
    const eventName = EVENT_NAME_RE.test(rawEventName) ? rawEventName : "malformed_event";

    const isDefaultLibUA = REJECTED_UAS.some((ua) => uaLower.includes(ua));
    if (isDefaultLibUA && !internal) {
      return new Response(JSON.stringify({ recorded: false, reason: "rejected_ua" }), {
        status: 403, headers: { "content-type": "application/json" },
      });
    }

    let props = (body.properties && typeof body.properties === "object") ? body.properties : {};
    const propsSize = JSON.stringify(props).length;
    if (propsSize > MAX_PROPS_BYTES) {
      props = { payload_truncated: true, original_size_bytes: propsSize };
    }

    // Edge stamps: drop IP, coarse geo from Cloudflare, tag traffic class.
    const cfCountry = request.headers.get("cf-ipcountry") || (request.cf && request.cf.country) || "unknown";
    props.$ip = null;
    props.$geoip_disable = true;
    props.geo_country = cfCountry;
    props.via_gateway = true;
    props.gateway_version = GATEWAY_VERSION;
    if (eventName === "malformed_event") props.raw_event_name = String(body.event ?? "").slice(0, 200);
    else if (!KNOWN_EVENTS.has(eventName)) props.unregistered_event = true;
    if (props.mcp_server_name !== undefined && props.mcp_server_name !== SERVER_NAME) {
      props.client_reported_server_name = props.mcp_server_name;
    }
    props.mcp_server_name = SERVER_NAME;
    if (props.internal_run === true || internal) props.traffic_class = "internal";
    else props.traffic_class = "standard";
    if (!body.distinct_id) props.missing_distinct_id = true;
    else if (!DISTINCT_ID_RE.test(String(body.distinct_id))) props.nonstandard_distinct_id = true;

    ctx.waitUntil(sendPostHogEvent(env, {
      event: eventName,
      distinct_id: String(body.distinct_id || `anon_${crypto.randomUUID()}`).slice(0, 200),
      properties: props,
    }));
    return new Response(JSON.stringify({ recorded: true }), {
      headers: { "content-type": "application/json" },
    });
  },
};

async function sendPostHogEvent(env, payload) {
  try {
    await fetch(`${env.POSTHOG_HOST}/capture/`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        api_key: env.POSTHOG_API_KEY,
        event: payload.event,
        distinct_id: payload.distinct_id,
        properties: payload.properties,
        timestamp: new Date().toISOString(),
      }),
    });
  } catch (err) {
    // Fail silently
  }
}
