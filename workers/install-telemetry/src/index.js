/**
 * Telemetry gateway for GSC MCP.
 * /e ingests events per the MCP Telemetry Standard contract:
 * event-name regex gate, allowlist == registry, IP stripped, UA guard,
 * geo from CF-IPCountry, traffic_class from the server's internal flag.
 */

const GATEWAY_VERSION = "1";

// Allowlist == the Standard event registry as emitted by this server.
const KNOWN_EVENTS = new Set([
  "mcp_started", "tools_listed", "tool_executed", "session_end",
  "server_first_install", "package_download", "skill_tip_shown", "skill_read",
]);

const EVENT_NAME_RE = /^[a-z_][a-z0-9_]{0,63}$/;

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

    const eventName = typeof body.event === "string" ? body.event : "";
    if (!EVENT_NAME_RE.test(eventName)) {
      return new Response(JSON.stringify({ recorded: false, reason: "invalid_event_name" }), {
        status: 400, headers: { "content-type": "application/json" },
      });
    }
    if (!KNOWN_EVENTS.has(eventName)) {
      return new Response(JSON.stringify({ recorded: false, reason: "unregistered_event" }), {
        status: 400, headers: { "content-type": "application/json" },
      });
    }

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
    if (props.internal_run === true || internal) props.traffic_class = "internal";
    else props.traffic_class = "external";
    if (!body.distinct_id) props.missing_distinct_id = true;

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
