/**
 * Installer + Telemetry Gateway + Static Assets for Google Search Console MCP
 * Unified Edge Relay (Schema v2)
 */

const GATEWAY_VERSION = "2";
const SERVER_NAME = "google-search-console";
const PACKAGE_NAME = "google-search-console-mcp";
const CLI_CMD = "google-search-console-mcp";
const DISPLAY_NAME = "Google Search Console";
const DESCRIPTION = "Surgical search intelligence, indexing status & query analytics for AI";
const DOCS_URL = "https://gsc.builditwithai.xyz";
const RUNTIME_PREF = "uvx";
const DEFAULT_ENV = "{}";

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
      return new Response(getInstallerScript(url.hostname, bucketSrc(url.searchParams.get("src")), SERVER_NAME, PACKAGE_NAME, CLI_CMD, DISPLAY_NAME, DESCRIPTION, DOCS_URL, RUNTIME_PREF, DEFAULT_ENV), {
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

function getInstallerScript(hostname, src, serverName, packageName, cliCmd, displayName, description, docsUrl, runtimePref, defaultEnv) {
  const host = hostname || `${serverName}.builditwithai.xyz`;
  const srcValue = src || "installer";
  const dispName = (displayName && displayName !== "undefined") ? displayName : (serverName ? serverName.split("-").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ") : "MCP Server");
  const desc = (description && description !== "undefined") ? description : "Model Context Protocol Server for AI Agents";
  const docUrl = (docsUrl && docsUrl !== "undefined") ? docsUrl : `https://${host}`;
  const rtPref = (runtimePref && runtimePref !== "undefined") ? runtimePref : "uvx";
  const envValue = (defaultEnv && defaultEnv !== "undefined") ? defaultEnv : "{}";
  return `#!/usr/bin/env bash
# ==============================================================================
# Universal AI Model Context Protocol (MCP) Installer
# Standard Fleet Architecture (Schema v2)
# Supports: Claude Desktop, Cursor, Claude Code, Antigravity, VS Code, Zed, Windsurf
# ==============================================================================

# --- Injected Template Variables (Replaced at edge per server) ---
MCP_SERVER_NAME="${serverName}"
MCP_PACKAGE_NAME="${packageName}"
MCP_CLI_CMD="${cliCmd}"
MCP_DISPLAY_NAME="${dispName}"
MCP_DESCRIPTION="${desc}"
MCP_DOCS_URL="${docUrl}"
MCP_RUNTIME_PREF="${rtPref}"
MCP_DEFAULT_ENV="${envValue}"
if [ "$MCP_DEFAULT_ENV" = "undefined" ] || [ -z "$MCP_DEFAULT_ENV" ]; then
  MCP_DEFAULT_ENV="{}"
fi
MCP_SRC="${srcValue}"
GATEWAY_HOST="${host}"

set -e

# --- Terminal Styling ---
if [ -t 1 ] && [ "\${TERM:-}" != "dumb" ]; then
  BOLD='\\033[1m'
  DIM='\\033[2m'
  GREEN='\\033[0;32m'
  BLUE='\\033[0;34m'
  CYAN='\\033[0;36m'
  YELLOW='\\033[1;33m'
  RED='\\033[0;31m'
  MAGENTA='\\033[0;35m'
  NC='\\033[0m'
else
  BOLD=''
  DIM=''
  GREEN=''
  BLUE=''
  CYAN=''
  YELLOW=''
  RED=''
  MAGENTA=''
  NC=''
fi

# --- Mode & Environment Detection ---
IS_INTERACTIVE=false
EXEC_MODE="agent_headless"
OUTPUT_JSON=false
AUTO_ALL=false

# Check CLI arguments
for arg in "$@"; do
  case "$arg" in
    --json) OUTPUT_JSON=true ;;
    --auto|--all|-y) AUTO_ALL=true ;;
    --headless) IS_INTERACTIVE=false; EXEC_MODE="agent_headless" ;;
  esac
done

# If stdout is a TTY and either stdin is a TTY or /dev/tty is available, enable interactive mode
if [ -t 1 ] && [ "$OUTPUT_JSON" = false ] && [ "$AUTO_ALL" = false ]; then
  if [ -t 0 ] || [ -c /dev/tty ]; then
    IS_INTERACTIVE=true
    EXEC_MODE="human_interactive"
  fi
fi

OS="$(uname -s 2>/dev/null || echo 'Unknown')"
ARCH="$(uname -m 2>/dev/null || echo 'Unknown')"
TERM_APP="\${TERM_PROGRAM:-terminal}"
SHELL_TYPE="$(basename "\${SHELL:-bash}")"

# --- Anonymous Telemetry ID ---
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

# --- Runtime Discovery ---
HAS_UV=false
HAS_UVX=false
HAS_NODE=false
HAS_NPX=false
HAS_PYTHON=false

if command -v uv &>/dev/null; then HAS_UV=true; fi
if command -v uvx &>/dev/null; then HAS_UVX=true; fi
if command -v node &>/dev/null; then HAS_NODE=true; fi
if command -v npx &>/dev/null; then HAS_NPX=true; fi
if command -v python3 &>/dev/null; then HAS_PYTHON=true; fi

# Select best execution command and args
CHOSEN_COMMAND="uvx"
CHOSEN_ARGS="[\\"\${MCP_PACKAGE_NAME}\\"]"

if [ "$MCP_RUNTIME_PREF" = "npx" ]; then
  CHOSEN_COMMAND="npx"
  CHOSEN_ARGS="[\\"-y\\", \\"\${MCP_PACKAGE_NAME}\\"]"
elif [ "$HAS_UVX" = false ] && [ "$HAS_NPX" = true ]; then
  # Fallback to npx wrapper if uvx is missing
  CHOSEN_COMMAND="npx"
  CHOSEN_ARGS="[\\"-y\\", \\"@surendranb/\${MCP_PACKAGE_NAME}\\"]"
fi

# --- JSON Mutation Helper Script (Python/Node) ---
patch_mcp_config() {
  local target_file="$1"
  local srv_name="$2"
  local srv_cmd="$3"
  local srv_args="$4"
  local srv_env="$5"
  local target_type="\${6:-mcpServers}"

  if [ "$HAS_PYTHON" = true ]; then
    python3 - <<PYEOF
import json, sys, os, shutil, time

target_path = "$target_file"
srv_name = "$srv_name"
srv_cmd = "$srv_cmd"
srv_args = json.loads('''$srv_args''')
srv_env = json.loads('''$srv_env''')
target_type = "$target_type"

# Expand user path
target_path = os.path.expanduser(target_path)
parent_dir = os.path.dirname(target_path)

if parent_dir and not os.path.exists(parent_dir):
    try:
        os.makedirs(parent_dir, exist_ok=True)
    except Exception as e:
        print(f"ERROR: Cannot create directory {parent_dir}: {e}", file=sys.stderr)
        sys.exit(1)

data = {}
if os.path.exists(target_path):
    # Create timestamped backup
    bak_path = f"{target_path}.bak.{int(time.time())}"
    try:
        shutil.copy2(target_path, bak_path)
    except Exception as e:
        print(f"WARN: Could not create backup: {e}", file=sys.stderr)
    
    try:
        with open(target_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if content:
                data = json.loads(content)
    except Exception as e:
        print(f"ERROR: Failed to parse existing JSON in {target_path}: {e}", file=sys.stderr)
        sys.exit(2)

# Ensure container structure exists
if target_type == "context_servers": # Zed format
    if "context_servers" not in data or not isinstance(data["context_servers"], dict):
        data["context_servers"] = {}
    container = data["context_servers"]
    container[srv_name] = {
        "command": srv_cmd,
        "args": srv_args
    }
    if srv_env:
        container[srv_name]["env"] = srv_env
elif target_type == "continue": # Continue.dev format
    if "mcpServers" not in data or not isinstance(data["mcpServers"], list):
        data["mcpServers"] = []
    # Replace existing or append
    data["mcpServers"] = [s for s in data["mcpServers"] if s.get("name") != srv_name]
    entry = {"name": srv_name, "command": srv_cmd, "args": srv_args}
    if srv_env:
        entry["env"] = srv_env
    data["mcpServers"].append(entry)
else: # Standard mcpServers dict format (Cursor, Claude Desktop, Antigravity, VS Code)
    if "mcpServers" not in data or not isinstance(data["mcpServers"], dict):
        data["mcpServers"] = {}
    
    existing_env = {}
    if srv_name in data["mcpServers"] and isinstance(data["mcpServers"][srv_name], dict):
        existing_env = data["mcpServers"][srv_name].get("env", {})
    
    merged_env = {**existing_env, **srv_env}
    
    server_block = {
        "command": srv_cmd,
        "args": srv_args
    }
    if merged_env:
        server_block["env"] = merged_env
        
    data["mcpServers"][srv_name] = server_block

# Atomic write
tmp_path = f"{target_path}.tmp.{os.getpid()}"
try:
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
        f.write('\\n')
    os.replace(tmp_path, target_path)
except Exception as e:
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    print(f"ERROR: Failed writing {target_path}: {e}", file=sys.stderr)
    sys.exit(1)

print("SUCCESS")
PYEOF
  elif [ "$HAS_NODE" = true ]; then
    node - <<JSEOF
const fs = require('fs');
const path = require('path');

const targetPath = path.resolve("$target_file".replace(/^~(?=$|\\/|\\\\\\\\)/, process.env.HOME || ''));
const srvName = "$srv_name";
const srvCmd = "$srv_cmd";
const srvArgs = JSON.parse('$srv_args');
const srvEnv = JSON.parse('$srv_env');
const targetType = "$target_type";

const parentDir = path.dirname(targetPath);
if (!fs.existsSync(parentDir)) {
  fs.mkdirSync(parentDir, { recursive: true });
}

let data = {};
if (fs.existsSync(targetPath)) {
  const bakPath = targetPath + '.bak.' + Math.floor(Date.now() / 1000);
  try { fs.copyFileSync(targetPath, bakPath); } catch (e) {}
  try {
    const raw = fs.readFileSync(targetPath, 'utf8').trim();
    if (raw) data = JSON.parse(raw);
  } catch (e) {
    console.error('ERROR: Failed parsing JSON:', e.message);
    process.exit(2);
  }
}

if (!data.mcpServers || typeof data.mcpServers !== 'object') data.mcpServers = {};
const existingEnv = (data.mcpServers[srvName] && data.mcpServers[srvName].env) || {};
const mergedEnv = Object.assign({}, existingEnv, srvEnv);

const block = { command: srvCmd, args: srvArgs };
if (Object.keys(mergedEnv).length > 0) block.env = mergedEnv;
data.mcpServers[srvName] = block;

const tmpPath = targetPath + '.tmp.' + process.pid;
fs.writeFileSync(tmpPath, JSON.stringify(data, null, 2) + '\\n', 'utf8');
fs.renameSync(tmpPath, targetPath);
console.log("SUCCESS");
JSEOF
  else
    echo "ERROR: Neither python3 nor node found to process JSON safely." >&2
    return 1
  fi
}

# --- Harness Locations & Detection ---
DETECTED_NAMES=()
DETECTED_PATHS=()
DETECTED_TYPES=()

add_harness() {
  local name="$1"
  local path="$2"
  local type="\${3:-mcpServers}"
  local dir_check="$4"

  local expanded_path="\${path/#\\~/$HOME}"
  local expanded_dir="\${dir_check/#\\~/$HOME}"

  # If file exists OR parent app directory exists
  if [ -f "$expanded_path" ] || ([ -n "$dir_check" ] && [ -d "$expanded_dir" ]); then
    DETECTED_NAMES+=("$name")
    DETECTED_PATHS+=("$path")
    DETECTED_TYPES+=("$type")
  fi
}

# 1. Claude Desktop
if [ "$OS" = "Darwin" ]; then
  add_harness "Claude Desktop" "~/Library/Application Support/Claude/claude_desktop_config.json" "mcpServers" "~/Library/Application Support/Claude"
elif [ "$OS" = "Linux" ]; then
  add_harness "Claude Desktop" "~/.config/Claude/claude_desktop_config.json" "mcpServers" "~/.config/Claude"
fi

# 2. Cursor (Global & Workspace)
add_harness "Cursor (Global)" "~/.cursor/mcp.json" "mcpServers" "~/.cursor"
if [ -d "$PWD/.cursor" ]; then
  add_harness "Cursor (Workspace)" "$PWD/.cursor/mcp.json" "mcpServers" "$PWD/.cursor"
fi

# 3. Claude Code CLI
if command -v claude &>/dev/null || [ -f "$HOME/.claude.json" ]; then
  add_harness "Claude Code CLI" "~/.claude.json" "mcpServers" ""
fi

# 4. Google Antigravity
add_harness "Google Antigravity" "~/.gemini/antigravity/mcp_servers.json" "mcpServers" "~/.gemini/antigravity"

# 5. VS Code Extensions (Cline, Roo Code, Continue)
if [ "$OS" = "Darwin" ]; then
  add_harness "VS Code (Cline)" "~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json" "mcpServers" "~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev"
  add_harness "VS Code (Roo Code)" "~/Library/Application Support/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/cline_mcp_settings.json" "mcpServers" "~/Library/Application Support/Code/User/globalStorage/rooveterinaryinc.roo-cline"
elif [ "$OS" = "Linux" ]; then
  add_harness "VS Code (Cline)" "~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json" "mcpServers" "~/.config/Code/User/globalStorage/saoudrizwan.claude-dev"
  add_harness "VS Code (Roo Code)" "~/.config/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/cline_mcp_settings.json" "mcpServers" "~/.config/Code/User/globalStorage/rooveterinaryinc.roo-cline"
fi
add_harness "Continue.dev" "~/.continue/config.json" "continue" "~/.continue"

# 6. Windsurf & Zed
add_harness "Windsurf" "~/.codeium/windsurf/mcp_config.json" "mcpServers" "~/.codeium/windsurf"
add_harness "Zed Editor" "~/.config/zed/settings.json" "context_servers" "~/.config/zed"

CONFIGURED_HARNESSES=()
CONFIG_FILES_TOUCHED=()

# ==============================================================================
# EXECUTION FLOW: HUMAN INTERACTIVE MODE
# ==============================================================================
if [ "$IS_INTERACTIVE" = true ]; then
  # Re-open stdin from /dev/tty for interactive input when piped via curl | bash
  if [ ! -t 0 ] && [ -c /dev/tty ]; then
    exec < /dev/tty 2>/dev/null || true
  fi

  clear 2>/dev/null || true
  echo -e "\${BLUE}\${BOLD}┌──────────────────────────────────────────────────────────────┐\${NC}"
  echo -e "\${BLUE}\${BOLD}│\${NC}  \${CYAN}\${BOLD}⚡ \${MCP_DISPLAY_NAME} Universal AI Installer\${NC}"
  echo -e "\${BLUE}\${BOLD}│\${NC}  \${DIM}\${MCP_DESCRIPTION}\${NC}"
  echo -e "\${BLUE}\${BOLD}└──────────────────────────────────────────────────────────────┘\${NC}"
  echo ""
  echo -e "  \${GREEN}✔\${NC} System  : \${BOLD}\${OS} (\${ARCH})\${NC}"
  if [ "$HAS_UVX" = true ]; then
    echo -e "  \${GREEN}✔\${NC} Runtime : \${BOLD}uvx detected\${NC} (\${GREEN}Recommended\${NC})"
  elif [ "$HAS_NPX" = true ]; then
    echo -e "  \${GREEN}✔\${NC} Runtime : \${BOLD}npx detected\${NC}"
  else
    echo -e "  \${YELLOW}⚠\${NC} Runtime : Neither uvx nor npx detected in PATH."
  fi
  echo ""
  echo -e "  \${BOLD}🔍 Scanning installed MCP harnesses on your system...\${NC}"
  echo -e "  \${DIM}──────────────────────────────────────────────────────────────\${NC}"

  if [ \${#DETECTED_NAMES[@]} -eq 0 ]; then
    echo -e "  \${YELLOW}ℹ No active client harness configurations detected automatically.\${NC}"
  else
    for i in "\${!DETECTED_NAMES[@]}"; do
      echo -e "  \${GREEN}✔\${NC} [Found] \${BOLD}\${DETECTED_NAMES[$i]}\${NC} \${DIM}(\${DETECTED_PATHS[$i]})\${NC}"
    done
  fi
  echo -e "  \${DIM}──────────────────────────────────────────────────────────────\${NC}"
  echo ""

  # Prompt Menu
  echo -e "  \${BOLD}Where would you like to install \${CYAN}\${MCP_SERVER_NAME}\${NC}\${BOLD}?\${NC}"
  echo ""
  if [ \${#DETECTED_NAMES[@]} -gt 0 ]; then
    echo -e "  \${CYAN}[1]\${NC} \${BOLD}All detected harnesses\${NC} (\${GREEN}Recommended\${NC})"
    echo -e "  \${CYAN}[2]\${NC} Select specific harnesses"
    echo -e "  \${CYAN}[3]\${NC} Specify a custom config file path"
    echo -e "  \${CYAN}[4]\${NC} Print configuration snippets only (Manual setup)"
    echo ""
    printf "  Select an option [1-4] (Default: 1): "
    read -r user_choice || user_choice="1"
    [ -z "$user_choice" ] && user_choice="1"
  else
    echo -e "  \${CYAN}[1]\${NC} Specify a custom config file path"
    echo -e "  \${CYAN}[2]\${NC} Print configuration snippets only (Manual setup)"
    echo ""
    printf "  Select an option [1-2] (Default: 2): "
    read -r user_choice || user_choice="2"
    if [ "$user_choice" = "1" ]; then user_choice="3"; else user_choice="4"; fi
  fi

  echo ""

  TARGETS_TO_CONFIGURE=()
  TARGET_PATHS=()
  TARGET_TYPES=()

  case "$user_choice" in
    1)
      for i in "\${!DETECTED_NAMES[@]}"; do
        TARGETS_TO_CONFIGURE+=("\${DETECTED_NAMES[$i]}")
        TARGET_PATHS+=("\${DETECTED_PATHS[$i]}")
        TARGET_TYPES+=("\${DETECTED_TYPES[$i]}")
      done
      ;;
    2)
      echo -e "  \${BOLD}Select harnesses to configure (e.g. 1,3 or 'all'):\${NC}"
      for i in "\${!DETECTED_NAMES[@]}"; do
        idx=$((i + 1))
        echo -e "    \${CYAN}[$idx]\${NC} \${DETECTED_NAMES[$i]}"
      done
      echo ""
      printf "  Enter numbers: "
      read -r selections
      if [ "$selections" = "all" ]; then
        for i in "\${!DETECTED_NAMES[@]}"; do
          TARGETS_TO_CONFIGURE+=("\${DETECTED_NAMES[$i]}")
          TARGET_PATHS+=("\${DETECTED_PATHS[$i]}")
          TARGET_TYPES+=("\${DETECTED_TYPES[$i]}")
        done
      else
        IFS=',' read -ra ADDR <<< "$selections"
        for s in "\${ADDR[@]}"; do
          clean_idx="$(echo "$s" | tr -d ' ')"
          if [ -n "$clean_idx" ] && [ "$clean_idx" -ge 1 ] && [ "$clean_idx" -le "\${#DETECTED_NAMES[@]}" ]; then
            actual_idx=$((clean_idx - 1))
            TARGETS_TO_CONFIGURE+=("\${DETECTED_NAMES[$actual_idx]}")
            TARGET_PATHS+=("\${DETECTED_PATHS[$actual_idx]}")
            TARGET_TYPES+=("\${DETECTED_TYPES[$actual_idx]}")
          fi
        done
      fi
      ;;
    3)
      printf "  📁 Enter absolute or relative path to your config file: "
      read -r custom_path
      if [ -n "$custom_path" ]; then
        TARGETS_TO_CONFIGURE+=("Custom Config")
        TARGET_PATHS+=("$custom_path")
        TARGET_TYPES+=("mcpServers")
      fi
      ;;
    *)
      # Manual snippet display handled below
      ;;
  esac

  # Apply mutations
  if [ \${#TARGETS_TO_CONFIGURE[@]} -gt 0 ]; then
    echo -e "  \${BOLD}⚙️  Configuring MCP harnesses...\${NC}"
    for i in "\${!TARGETS_TO_CONFIGURE[@]}"; do
      tname="\${TARGETS_TO_CONFIGURE[$i]}"
      tpath="\${TARGET_PATHS[$i]}"
      ttype="\${TARGET_TYPES[$i]}"
      expanded_path="\${tpath/#\\~/$HOME}"

      printf "  %-30s " "• $tname..."
      res="$(patch_mcp_config "$expanded_path" "$MCP_SERVER_NAME" "$CHOSEN_COMMAND" "$CHOSEN_ARGS" "$MCP_DEFAULT_ENV" "$ttype" 2>&1 || true)"
      if [ "$res" = "SUCCESS" ]; then
        echo -e "\${GREEN}[OK]\${NC}"
        CONFIGURED_HARNESSES+=("$tname")
        CONFIG_FILES_TOUCHED+=("$expanded_path")
      else
        echo -e "\${RED}[FAILED]\${NC} \${DIM}($res)\${NC}"
      fi
    done
    echo ""
  fi

  # Completion banner
  echo -e "  \${DIM}──────────────────────────────────────────────────────────────\${NC}"
  echo -e "  \${GREEN}\${BOLD}🎉 Setup Complete!\${NC}"
  echo -e "  \${DIM}──────────────────────────────────────────────────────────────\${NC}"
  echo ""
  echo -e "  \${BOLD}Next Steps:\${NC}"
  echo -e "  • \${BOLD}Claude Desktop\${NC} : Restart Claude Desktop app."
  echo -e "  • \${BOLD}Cursor\${NC}         : Open Settings -> Features -> MCP."
  echo -e "  • \${BOLD}Claude Code\${NC}    : Run \${CYAN}claude\${NC} in your project."
  echo ""
  echo -e "  📖 Documentation: \${CYAN}\${MCP_DOCS_URL}\${NC}"
  echo ""

# ==============================================================================
# EXECUTION FLOW: AGENT / HEADLESS / JSON MODE
# ==============================================================================
else
  # In headless mode: auto-configure all detected harnesses safely
  for i in "\${!DETECTED_NAMES[@]}"; do
    tname="\${DETECTED_NAMES[$i]}"
    tpath="\${DETECTED_PATHS[$i]}"
    ttype="\${DETECTED_TYPES[$i]}"
    expanded_path="\${tpath/#\\~/$HOME}"

    res="$(patch_mcp_config "$expanded_path" "$MCP_SERVER_NAME" "$CHOSEN_COMMAND" "$CHOSEN_ARGS" "$MCP_DEFAULT_ENV" "$ttype" 2>&1 || true)"
    if [ "$res" = "SUCCESS" ]; then
      CONFIGURED_HARNESSES+=("$tname")
      CONFIG_FILES_TOUCHED+=("$expanded_path")
    fi
  done

  # If no harnesses were configured, note manual fallback
  if [ \${#CONFIGURED_HARNESSES[@]} -eq 0 ]; then
    CONFIGURED_HARNESSES+=("mcp_json_snippet")
  fi

  # Output machine-readable JSON if requested or in agent mode
  cat <<JSONEOF
{
  "status": "success",
  "server": "\${MCP_SERVER_NAME}",
  "package": "\${MCP_PACKAGE_NAME}",
  "runtime": "\${CHOSEN_COMMAND}",
  "execution_mode": "\${EXEC_MODE}",
  "detected_harnesses": [$(printf '"%s",' "\${DETECTED_NAMES[@]}" 2>/dev/null | sed 's/,$//')],
  "configured_harnesses": [$(printf '"%s",' "\${CONFIGURED_HARNESSES[@]}" 2>/dev/null | sed 's/,$//')],
  "config_files_modified": [$(printf '"%s",' "\${CONFIG_FILES_TOUCHED[@]}" 2>/dev/null | sed 's/,$//')],
  "mcp_server_config": {
    "command": "\${CHOSEN_COMMAND}",
    "args": \${CHOSEN_ARGS},
    "env": \${MCP_DEFAULT_ENV}
  },
  "cli_command": "claude mcp add \${MCP_SERVER_NAME} -- \${CHOSEN_COMMAND} \${MCP_PACKAGE_NAME}"
}
JSONEOF
fi

# ==============================================================================
# TELEMETRY DISPATCH (Background)
# ==============================================================================
if [ -n "$ANON_ID" ]; then
  TELEMETRY_PAYLOAD=$(cat <<JSONEOF
{
  "anonymous_id": "$ANON_ID",
  "src": "$MCP_SRC",
  "execution_mode": "$EXEC_MODE",
  "harnesses_detected": [$(printf '"%s",' "\${DETECTED_NAMES[@]}" 2>/dev/null | sed 's/,$//')],
  "configured_harnesses": [$(printf '"%s",' "\${CONFIGURED_HARNESSES[@]}" 2>/dev/null | sed 's/,$//')],
  "terminal_app": "$TERM_APP",
  "shell_type": "$SHELL_TYPE",
  "os_name": "$OS",
  "arch": "$ARCH",
  "has_uv": $HAS_UV,
  "has_npx": $HAS_NPX,
  "install_outcome": "success"
}
JSONEOF
)
  curl -s -m 4 -X POST "https://\${GATEWAY_HOST}/telemetry" \\
    -H "Content-Type: application/json" \\
    -d "$TELEMETRY_PAYLOAD" &>/dev/null || true
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
