# AGENTS.md — Agent Operational Manual

> **Canonical context and operating procedures for AI agents (Claude Code, Cursor, Codex, Gemini, Antigravity, OpenCode, Aider) operating on or integrating `google-search-console-mcp`.**

---

## 1. System Identity & Mission

- **Repository**: `surendranb/google-search-console-mcp`
- **Package Name**: `google-search-console-mcp` (PyPI) / `google-search-console-mcp` (NPM)
- **Live Portal**: [https://gsc.builditwithai.xyz](https://gsc.builditwithai.xyz)
- **Purpose**: Google Search Console MCP server for search performance analytics, keyword rankings, URL inspection, and sitemaps.

---

## 2. Quickstart & Invocation for Agents

Agents integrating or executing this server should use stdio transport via either runtime:

```bash
# Python runtime (FastMCP / stdio)
uvx google-search-console-mcp

# Universal 1-line auto-installer
curl -fsSL "https://gsc.builditwithai.xyz/install" | bash
```

### Required Environment Variables
- `GSC_SITE_URL`: Verified Search Console property URL (e.g. https://example.com/) (Required)
- `GOOGLE_APPLICATION_CREDENTIALS`: Path to service account JSON key file (Required)


---

## 3. Tool Reference & Capabilities

| Tool | Capability Summary |
|---|---|
| `get_search_analytics` | Queries clicks, impressions, CTR, and average position by query/page/country/device. |
| `list_sites` | Lists all verified web properties in Google Search Console. |
| `inspect_url` | Real-time URL inspection for index status and mobile usability. |
| `list_sitemaps` | Lists submitted XML sitemaps and indexing status. |
| `submit_sitemap` | Submits a new XML sitemap to Google Search Console. |
| `delete_sitemap` | Removes an obsolete sitemap. |
| `skill_read` | Loads SEO diagnostic playbooks dynamically from GitHub. |
| `skills_list` | Lists all available GSC analytical skills. |

---

## 4. Agent Working Laws (Operational Rules)

When contributing code, diagnosing bugs, or modifying this repository, all visiting agents must adhere strictly to these rules:

1. **Truth Over Guessing**: Never fabricate responses, schema types, or error reasons. Run native verification scripts before asserting completion.
2. **Shortest Working Diff (Lazy Senior Dev)**: Do not introduce unrequested abstractions, extra dependencies, or architectural bloat. Standard library and native platform features first.
3. **Preserve Schema Stability**: Never remove or rename existing MCP tool parameters without strict backwards-compatibility layers.
4. **Strict Telemetry Boundaries**: Diagnostic telemetry is non-PII and strictly opt-out. Never log user queries, credentials, file contents, or environment variables. Honor `DO_NOT_TRACK=1` and `MCP_TELEMETRY_OPT_OUT=1`.
5. **No Direct Main Commits**: Always create a feature or fix branch before modifying code.

---

## 5. Verification & Test Protocol

Before marking any task as complete in this repository, run the test suite:

```bash
# Run automated verification suite
uv run pytest -v || python3 -m unittest
```

---

## 6. Plugin & Marketplace Discovery Pointers

- **Claude Code**: `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`
- **Gemini CLI / Antigravity**: `gemini-extension.json`
- **Smithery.ai**: `smithery.yaml`
- **Official MCP Registry & Glama**: `server.json`
- **OpenAI / ChatGPT Actions**: `.well-known/ai-plugin.json`
- **AI Search Crawlers (GEO)**: `llms.txt`
