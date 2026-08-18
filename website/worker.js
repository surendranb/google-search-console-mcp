/**
 * Installer + Telemetry Gateway + Static Assets for Google Search Console MCP
 * Unified Edge Relay (Schema v2)
 */

const GATEWAY_VERSION = "2";
const SERVER_NAME = "google-search-console";
const PACKAGE_NAME = "google-search-console-mcp";
const CLI_CMD = "google-search-console-mcp";

const KNOWN_EVENTS = new Set([
  "mcp_started", "tools_listed", "tool_executed", "session_end",
  "server_first_install", "package_download", "server_discovered",
  "setup_flow", "skill_tip_shown", "skill_read", "prompt_used",
  "resource_read", "install_intent", "install_completed", "surface_click",
]);

const GO_TARGETS = {
  cursor: "cursor://anysphere.cursor-deeplink/mcp/install?name=gsc-analytics&config=eyJjb21tYW5kIjogInV2eCIsICJhcmdzIjogWyItLWZyb20iLCAiZ29vZ2xlLXNlYXJjaC1jb25zb2xlLW1jcCIsICJnb29nbGUtc2VhcmNoLWNvbnNvbGUtbWNwIl19",
};

const KNOWN_SRC = new Set([
  "readme", "glama", "mcpso", "pulsemcp", "gscmcp", "setup", "cursor_button",
  "vscode_button", "installer",
]);

function bucketSrc(raw) {
  if (!raw) return null;
  const s = String(raw).toLowerCase().slice(0, 64);
  return KNOWN_SRC.has(s) ? s : "other";
}

const MAX_PROPS_BYTES = 900000;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const pathname = url.pathname.toLowerCase();
    const userAgent = request.headers.get("user-agent") || "";
    const clientIp = request.headers.get("cf-connecting-ip") || request.headers.get("x-real-ip") || "";
    const isCurl = userAgent.toLowerCase().includes("curl") || userAgent.toLowerCase().includes("wget");
    const dnt = request.headers.get("dnt") === "1" || request.headers.get("sec-gpc") === "1";

    const cf = request.cf || {};
    const country = cf.country || "unknown";
    const city = cf.city || "unknown";
    const continent = cf.continent || "unknown";
    const timezone = cf.timezone || "unknown";
    const asn = cf.asn || 0;
    const asOrganization = cf.asOrganization || "unknown";

    const edgeParsed = parseUserAgent(userAgent);

    if (pathname === "/health") {
      return new Response(JSON.stringify({
        status: "ok",
        server: SERVER_NAME,
        gateway_version: GATEWAY_VERSION,
        timestamp: new Date().toISOString(),
      }), {
        headers: { "content-type": "application/json" },
      });
    }

    // Route 1: /e telemetry ingest
    if (request.method === "POST" && pathname === "/e") {
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

      const eventName = typeof body.event === "string" && body.event ? body.event.slice(0, 200) : "malformed_event";
      let props = (body.properties && typeof body.properties === "object") ? body.properties : {};
      if (eventName === "malformed_event") props.raw_event_name = String(body.event ?? "").slice(0, 200);

      const propsSize = JSON.stringify(props).length;
      if (propsSize > MAX_PROPS_BYTES) {
        props = { payload_truncated: true, original_size_bytes: propsSize };
      }

      if (clientIp) props.$ip = clientIp;
      props.as_organization = asOrganization;
      props.asn = asn;
      props.cf_country = country;
      props.cf_timezone = timezone;
      props.via_gateway = true;
      props.gateway_version = GATEWAY_VERSION;

      if (props.mcp_server_name && props.mcp_server_name !== SERVER_NAME) {
        props.client_reported_server_name = props.mcp_server_name;
      }
      props.mcp_server_name = SERVER_NAME;

      if (!KNOWN_EVENTS.has(eventName)) props.unregistered_event = true;
      if (!body.distinct_id) props.missing_distinct_id = true;
      else if (!/^(inst_|anon_)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(String(body.distinct_id))) {
        props.nonstandard_distinct_id = true;
      }

      if (props.internal_run === true) props.traffic_class = "internal";
      else if (props.run_context === "ci" || props.agent_name === "ci_runner") props.traffic_class = "ci";
      else props.traffic_class = "standard";

      if (asOrganization === "Anthropic, PBC") props.managed_agent = "claude_managed";

      ctx.waitUntil(sendPostHogEvent(env, {
        event: eventName,
        distinct_id: String(body.distinct_id || `anon_${crypto.randomUUID()}`).slice(0, 200),
        properties: props,
      }));
      return new Response(JSON.stringify({ recorded: true }), {
        headers: { "content-type": "application/json" },
      });
    }

    // Route 2: /go/<surface> click redirect
    if (pathname.startsWith("/go/")) {
      const surface = pathname.slice(4);
      const target = GO_TARGETS[surface];
      if (!dnt) {
        ctx.waitUntil(sendPostHogEvent(env, {
          event: "surface_click",
          distinct_id: `anon_${crypto.randomUUID()}`,
          properties: {
            $ip: clientIp || null,
            as_organization: asOrganization,
            asn: asn,
            cf_country: country,
            cf_timezone: timezone,
            via_gateway: true,
            gateway_version: GATEWAY_VERSION,
            mcp_server_name: SERVER_NAME,
            surface: surface.slice(0, 32),
            known_surface: Boolean(target),
            user_agent: userAgent,
            referer: (request.headers.get("referer") || "direct").slice(0, 200),
          },
        }));
      }
      return Response.redirect(target || env.GITHUB_REPO, 302);
    }

    // Route 3: Post-install completion telemetry ping
    if (request.method === "POST" && pathname === "/telemetry") {
      try {
        const body = await request.json();
        if (dnt) {
          return new Response(JSON.stringify({ recorded: false, reason: "dnt" }), {
            headers: { "content-type": "application/json" },
          });
        }
        ctx.waitUntil(
          sendPostHogEvent(env, {
            event: "install_completed",
            distinct_id: body.anonymous_id || `anon_${crypto.randomUUID()}`,
            properties: {
              $ip: clientIp || null,
              as_organization: asOrganization,
              asn: asn,
              cf_country: country,
              cf_timezone: timezone,
              via_gateway: true,
              gateway_version: GATEWAY_VERSION,
              mcp_server_name: SERVER_NAME,
              install_source: bucketSrc(body.src),
              install_source_raw: body.src ? String(body.src).slice(0, 64) : null,
              execution_mode: body.execution_mode || "unknown",
              harnesses_detected: body.harnesses_detected || [],
              configured_harnesses: body.configured_harnesses || [],
              terminal_app: body.terminal_app || "unknown",
              shell_type: body.shell_type || "unknown",
              os_name: body.os_name || edgeParsed.os,
              arch: body.arch || edgeParsed.arch,
              python_version: body.python_version || "none",
              has_uv: body.has_uv || false,
              install_outcome: body.install_outcome || "success",
            },
          })
        );
        return new Response(JSON.stringify({ recorded: true }), {
          headers: { "content-type": "application/json" },
        });
      } catch (e) {
        return new Response(JSON.stringify({ error: e.message }), { status: 400 });
      }
    }

    // Route 4: Install script serving (curl -fsSL https://<host>/install | bash)
    const isInstallerRequest = isCurl || pathname === "/install" || pathname === "/install.sh" || (pathname === "/" && isCurl);
    if (isInstallerRequest) {
      if (!dnt) {
        ctx.waitUntil(
          sendPostHogEvent(env, {
            event: "install_intent",
            distinct_id: `anon_${crypto.randomUUID()}`,
            properties: {
              $ip: clientIp || null,
              via_gateway: true,
              gateway_version: GATEWAY_VERSION,
              mcp_server_name: SERVER_NAME,
              install_source: bucketSrc(url.searchParams.get("src")),
              install_source_raw: url.searchParams.get("src") ? String(url.searchParams.get("src")).slice(0, 64) : null,
              referer: (request.headers.get("referer") || "direct").slice(0, 200),
              path: pathname,
              is_curl: isCurl,
              user_agent: userAgent,
              os_family: edgeParsed.os,
              arch_family: edgeParsed.arch,
              client_tool: edgeParsed.clientTool,
              is_ai_agent_ua: edgeParsed.isAiAgent,
              cf_country: country,
              cf_city: city,
              cf_continent: continent,
              cf_timezone: timezone,
              as_organization: asOrganization,
              asn: asn,
            },
          })
        );
      }
      return new Response(getInstallerScript(url.hostname, bucketSrc(url.searchParams.get("src")), SERVER_NAME, PACKAGE_NAME, CLI_CMD), {
        headers: {
          "content-type": "text/plain; charset=utf-8",
          "cache-control": "no-cache",
        },
      });
    }

    // Route 5: Static Assets / 11ty Web Pages fallback
    if (env.ASSETS) {
      return env.ASSETS.fetch(request);
    }

    return Response.redirect(env.DOCS_URL || env.GITHUB_REPO, 302);
  },
};

function parseUserAgent(ua) {
  const lower = ua.toLowerCase();
  let os = "Unknown";
  let arch = "x86_64";
  let clientTool = "Browser";
  let isAiAgent = false;

  if (lower.includes("darwin") || lower.includes("macintosh") || lower.includes("mac os")) os = "macOS";
  else if (lower.includes("linux")) os = "Linux";
  else if (lower.includes("windows")) os = "Windows";

  if (lower.includes("arm64") || lower.includes("aarch64")) arch = "arm64";

  if (lower.includes("curl")) clientTool = "curl";
  else if (lower.includes("wget")) clientTool = "wget";
  else if (lower.includes("python")) clientTool = "python-requests";

  if (lower.includes("claude") || lower.includes("cursor") || lower.includes("antigravity") || lower.includes("gpt") || lower.includes("ai")) {
    isAiAgent = true;
  }

  return { os, arch, clientTool, isAiAgent };
}

function getInstallerScript(hostname, src, serverName, packageName, cliCmd) {
  const host = hostname || `${serverName}.builditwithai.xyz`;
  const srcValue = src || "installer";
  return `#!/usr/bin/env bash
# ${serverName} Universal AI Installer & Telemetry Collector
MCP_SRC="${srcValue}"
MCP_SERVER_NAME="${serverName}"
MCP_PACKAGE_NAME="${packageName}"
MCP_CLI_CMD="${cliCmd}"

set -e

GREEN='\\033[0;32m'
BLUE='\\033[0;34m'
YELLOW='\\033[1;33m'
CYAN='\\033[0;36m'
RED='\\033[0;31m'
NC='\\033[0m'

IS_INTERACTIVE=false
EXEC_MODE="agent_headless"
if [ -t 0 ] && [ -t 1 ]; then 
  IS_INTERACTIVE=true
  EXEC_MODE="human_interactive"
fi

OS="$(uname -s 2>/dev/null || echo 'Unknown')"
ARCH="$(uname -m 2>/dev/null || echo 'Unknown')"
TERM_APP="\${TERM_PROGRAM:-terminal}"
SHELL_TYPE="$(basename "\${SHELL:-bash}")"

ANON_ID=""
if [ -z "\${DO_NOT_TRACK:-}" ] && [ -z "\${DISABLE_TELEMETRY:-}" ] && [ -z "\${NO_TELEMETRY:-}" ]; then
  ID_DIR="$HOME/.\${MCP_SERVER_NAME//-/_}"
  mkdir -p "$ID_DIR" 2>/dev/null || true
  if [ -f "$ID_DIR/installation_id" ]; then
    ANON_ID="$(cat "$ID_DIR/installation_id" 2>/dev/null || true)"
  else
    RAW_UUID="$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "$(date +%s)-$RANDOM")"
    ANON_ID="inst_$(echo "$RAW_UUID" | tr '[:upper:]' '[:lower:]')"
    printf '%s' "$ANON_ID" > "$ID_DIR/installation_id" 2>/dev/null || ANON_ID=""
  fi
fi

HARNESSES=()
CONFIGURED=()

if [ -d "$HOME/Library/Application Support/Claude" ] || [ -d "$HOME/.config/Claude" ]; then HARNESSES+=("claude"); fi
if [ -d "$HOME/.cursor" ]; then HARNESSES+=("cursor"); fi
if [ -d "$HOME/.vscode" ] || command -v code &> /dev/null; then HARNESSES+=("vscode"); fi

HAS_UV=false
if command -v uv &> /dev/null || command -v uvx &> /dev/null; then HAS_UV=true; fi
PY_VER="$(python3 --version 2>/dev/null || echo 'None')"

if [ "$IS_INTERACTIVE" = true ]; then
  echo -e "\${BLUE}=====================================================\${NC}"
  echo -e "\${BLUE}🚀 \${MCP_SERVER_NAME} Universal AI Installer\${NC}"
  echo -e "\${BLUE}=====================================================\${NC}"
fi

echo -e "\${YELLOW}➡ Add to your MCP client config under \\"mcpServers\\":\${NC}"
echo "  \\"\${MCP_SERVER_NAME}\\": { \\"command\\": \\"uvx\\", \\"args\\": [\\"\${MCP_PACKAGE_NAME}\\"] }"
CONFIGURED+=("mcp_json_snippet")

if [ -n "$ANON_ID" ]; then
  TELEMETRY_PAYLOAD=$(cat <<JSONEOF
{
  "anonymous_id": "$ANON_ID",
  "src": "$MCP_SRC",
  "execution_mode": "$EXEC_MODE",
  "harnesses_detected": [$(printf '"%s",' "\${HARNESSES[@]}" 2>/dev/null | sed 's/,$//')],
  "configured_harnesses": [$(printf '"%s",' "\${CONFIGURED[@]}" 2>/dev/null | sed 's/,$//')],
  "terminal_app": "$TERM_APP",
  "shell_type": "$SHELL_TYPE",
  "os_name": "$OS",
  "arch": "$ARCH",
  "python_version": "$PY_VER",
  "has_uv": $HAS_UV,
  "install_outcome": "success"
}
JSONEOF
)
  curl -s -m 5 -X POST "https://${host}/telemetry" \\
    -H "Content-Type: application/json" \\
    -d "$TELEMETRY_PAYLOAD" &> /dev/null || true
fi

if [ "$IS_INTERACTIVE" = true ]; then
  echo -e "\${GREEN}🎉 Setup Complete!\${NC}"
else
  echo '{"status": "success", "mode": "agent_headless", "ready": true}'
fi
`;
}

async function sendPostHogEvent(env, payload) {
  try {
    const posthogHost = env.POSTHOG_HOST || "https://us.i.posthog.com";
    await fetch(`${posthogHost}/capture/`, {
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
