# AGENTS.md — Codebase Operational Guide for AI Agents

> **Context, architecture, file map, and execution commands for AI coding agents (Claude Code, Cursor, Codex, Gemini, Antigravity, OpenCode, Aider) working on `google-search-console-mcp`.**

---

## 1. Codebase Overview

- **Language & Runtime**: Python 3.10+ (`mcp` FastMCP, `google-api-python-client`, `google-auth`, `httpx`).
- **Package Name**: `google-search-console-mcp` (PyPI) / `google-search-console-mcp` (NPM thin wrapper).
- **Core Function**: Connects LLMs to Google Search Console (Search Console API v1 / Webmasters API) to query clicks, impressions, CTR, average keyword rankings, live URL indexing inspection, and sitemaps.

---

## 2. Directory & File Map

```
google-search-console-mcp/
├── gsc_mcp_server.py          # Primary entry point: FastMCP server, tools registration, API client
├── gsc_setup_flow.py          # Interactive authentication and setup helper
├── gsc_telemetry.py           # Edge Schema v2 telemetry relay
├── gsc_dimensions.json        # Cached list of supported GSC dimensions (query, page, country, device, date)
├── gsc_metrics.json           # Supported metrics (clicks, impressions, ctr, position)
├── gsc_filters.json           # Filter operator definitions
├── skills/                    # Domain-specific SEO analysis skills (Markdown playbooks)
│   ├── brand_visibility.md   # Brand vs non-brand search split analysis
│   ├── citation_opportunities.md # Content citation & SERP ranking optimization
│   ├── intent_efficiency.md  # High impression / low CTR query optimization
│   ├── intent_segmentation.md# Informational vs transactional intent parsing
│   └── search_appearance_audit.md # Rich snippets and search appearance tracking
├── npm/                       # Thin Node.js CLI launcher
│   ├── bin/index.js           # Subprocess wrapper spawning uvx google-search-console-mcp
│   └── package.json           # NPM package metadata
├── tests/                     # Automated test suite
│   ├── test_gsc.py            # Tool execution and query parameter tests
│   └── e2e/test_e2e.py        # End-to-end API integration tests
├── pyproject.toml             # Python packaging, dependencies, and CLI entrypoint
├── smithery.yaml              # Smithery.ai marketplace configuration
├── server.json                # Official MCP registry specification
├── gemini-extension.json      # Google Gemini / Antigravity extension manifest
├── .claude-plugin/            # Claude Code plugin manifests (plugin.json, marketplace.json)
└── .well-known/ai-plugin.json # OpenAI / ChatGPT Actions manifest
```

---

## 3. Environment Variables & Auth

| Variable | Description | Required |
|---|---|---|
| `GSC_SITE_URL` | Verified site URL in Search Console (e.g. `https://example.com/` or `sc-domain:example.com`). | Yes (or passed per tool call) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to Google Cloud Service Account JSON key file with GSC permissions. | Yes (or via ADC / gcloud auth) |
| `DO_NOT_TRACK` / `MCP_TELEMETRY_OPT_OUT` | Set to `1` to disable anonymous telemetry. | Optional |

---

## 4. Development & Testing Commands

```bash
# Install dependencies in editable mode
uv sync || pip install -e ".[dev]"

# Run the MCP server in stdio mode locally
uv run python gsc_mcp_server.py

# Run the test suite
uv run pytest tests/ -v

# Run linting
uv run ruff check .
```

---

## 5. Tool Implementation Invariants & Gotchas

1. **Site URL Formats (`gsc_mcp_server.py`)**:
   - GSC property URLs can be either URL-prefix properties (`https://example.com/`) or Domain properties (`sc-domain:example.com`). Handle both without stripping the prefix.
2. **Dimension Constraints**:
   - `get_search_analytics` allows grouping by `['query', 'page', 'country', 'device', 'date', 'searchAppearance']`.
   - Max `row_limit` supported by GSC API is 25,000. Default to 1,000 for token efficiency.
3. **Inspect URL API Quotas**:
   - `inspect_url` calls the Search Console URL Inspection API which has strict daily quotas (2,000 calls/day). Return clear error messages if quota is exceeded.
