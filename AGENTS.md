# AGENTS.md — Agent Operating Manual

> **For AI Agents (Claude Code, Cursor, Codex, Gemini, Antigravity, OpenCode, Aider) connecting to or executing `google-search-console-mcp`.**

---

## ⚡ 1. Fast Install & Client Wiring (Start Here)

In 90% of cases, you just need to install and configure this MCP server for your user:

```bash
# 1-Line Universal Auto-Installer (Configures Claude Code, Cursor, Claude Desktop & Antigravity)
curl -fsSL "https://gsc.builditwithai.xyz/install" | bash

# Or launch directly via stdio:
uvx google-search-console-mcp
npx -y google-search-console-mcp
```

### Client Config Snippet (`mcp.json` / `claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "google-search-console": {
      "command": "uvx",
      "args": ["google-search-console-mcp"],
      "env": {
        "GSC_SITE_URL": "https://example.com/",
        "GOOGLE_APPLICATION_CREDENTIALS": "/path/to/service_account.json"
      }
    }
  }
}
```

---

## 📖 2. What This Repo Is

A production MCP server for **Google Search Console (GSC)**. It provides direct query access to the Google Search Console API v1 / Webmasters API for organic search metrics, keyword rankings, live URL indexing inspection, and sitemaps.

### Primary Tools Available:
- `get_search_analytics`: Query organic clicks, impressions, CTR, and average position grouped by query, page, country, device, and date.
- `list_sites`: List all verified properties accessible to the service account.
- `inspect_url`: Real-time URL indexing status, crawl issues, canonicalization, and mobile usability check.
- `list_sitemaps`, `submit_sitemap`, `delete_sitemap`: Manage XML sitemaps.
- `skill_read` / `skills_list`: Load built-in SEO diagnostic playbooks.

---

## 👤 3. How to Handle the Human (Auth & Setup)

If credentials are not yet configured, guide the human with these steps:

1. **Ask for their verified Site URL (`GSC_SITE_URL`)**:
   - Must match Search Console exactly: e.g. `https://example.com/` (URL-prefix) or `sc-domain:example.com` (Domain property).
2. **Ask for a Google Cloud Service Account JSON Key**:
   - The Service Account email must be added as a **Full** or **Restricted** user in *Search Console → Settings → Users and permissions*.
   - Once they have the JSON key file, set:
     ```bash
     export GSC_SITE_URL="https://example.com/"
     export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service_account.json"
     ```

---

## ⚠️ 4. Quirks & API Landmines (Zero-Hallucination Rules)

1. **Site URL Formats**:
   - URL-prefix properties **MUST include the trailing slash** (e.g. `https://example.com/`).
   - Domain properties **MUST include the `sc-domain:` prefix** (e.g. `sc-domain:example.com`).
2. **Dimension Constraints**:
   - Allowed dimensions in `get_search_analytics`: `['query', 'page', 'country', 'device', 'date', 'searchAppearance']`.
   - Max row limit is 25,000. Default to 1,000 to conserve context tokens.
3. **URL Inspection Quota**:
   - `inspect_url` calls the live Inspection API which has a strict daily quota of 2,000 calls per day. Use it surgically for single URLs, not in a broad loop.

---

## 🎯 5. Playbooks & Skills (How to Answer User Questions)

When your human user asks SEO and search performance questions:

- **"Which queries have high impressions but low CTR?"** → Call `skill_read(skill_name="intent_efficiency")`
- **"What is our brand vs. non-brand search split?"** → Call `skill_read(skill_name="brand_visibility")`
- **"How do I find rich snippet opportunities?"** → Call `skill_read(skill_name="search_appearance_audit")`
- **"Which pages rank on page 2 (positions 11–20)?"** → Query `get_search_analytics` with `dimensions=['query', 'page']` and filter for average position between 10 and 20.
