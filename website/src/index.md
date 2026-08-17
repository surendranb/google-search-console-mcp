---
layout: layout.njk
title: "Google Search Console MCP Server"
description: "Model Context Protocol server for Google Search Console: search performance analytics, keyword rankings, sitemap inspection, and indexing health for AI agents."
kicker: "ORGANIC SEARCH MCP"
subkicker: "Google Search Console Bridge"
header_badge: "GSC API v3 · Performance Analytics · Keyword Tracking · Zero Cloud"
lede: "How do you give an AI agent direct access to your site's Google Search Console performance? This MCP server connects Claude, Cursor, and autonomous agents directly to the Google Search Console API—enabling automated query analysis, CTR anomaly detection, indexing inspection, and sitemap audits."
chips:
  - "MCP 2.0"
  - "Google Search Console API"
  - "Python & TypeScript"
  - "Service Account & OAuth"
  - "Zero Data Retention"
toc:
  - id: "quickstart"
    title: "1. 1-Line Quickstart"
  - id: "the-bridge"
    title: "2. The Search Console Bridge"
  - id: "agent-setup"
    title: "3. Agent Configuration"
  - id: "tools-reference"
    title: "4. Tool & Parameter Reference"
---

<section id="quickstart" class="space-y-6">
<div class="kicker">01 / Getting Started</div>

## 1-Line Quickstart

```bash
# ⚡ 1-Line Universal Installer
curl -fsSL https://gsc.builditwithai.xyz/install | bash

# 📦 Run directly via Python (uvx)
uvx google-search-console-mcp

# 📦 Run directly via Node (npx)
npx -y google-search-console-mcp
```

</section>

---

<section id="the-bridge" class="space-y-6">
<div class="kicker">02 / Capabilities</div>

## The Search Console Bridge

<div class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
<div class="p-4 bg-[#fbfbfa] rounded-lg border border-[#e5e6e4] space-y-1.5">
<b>1. 📈 Query &amp; Keyword Performance</b>
<p class="text-[#747982] leading-relaxed !mb-0">Fetches clicks, impressions, CTR, and average position metrics broken down by query, page URL, device type, and country.</p>
</div>
<div class="p-4 bg-[#fbfbfa] rounded-lg border border-[#e5e6e4] space-y-1.5">
<b>2. 🔍 Real-Time URL Inspection</b>
<p class="text-[#747982] leading-relaxed !mb-0">Checks Googlebot crawl state, canonical URL selection, mobile usability, and index coverage for any specific page.</p>
</div>
<div class="p-4 bg-[#fbfbfa] rounded-lg border border-[#e5e6e4] space-y-1.5">
<b>3. 🗺️ Sitemap Health &amp; Submissions</b>
<p class="text-[#747982] leading-relaxed !mb-0">Lists submitted sitemaps, inspects XML parse errors, and submits fresh sitemap endpoints programmatically.</p>
</div>
<div class="p-4 bg-[#fbfbfa] rounded-lg border border-[#e5e6e4] space-y-1.5">
<b>4. 🔒 Enterprise Privacy</b>
<p class="text-[#747982] leading-relaxed !mb-0">Uses standard Google Cloud Service Account JSON credentials or OAuth2 tokens stored strictly on your local machine.</p>
</div>
</div>

</section>

---

<section id="agent-setup" class="space-y-6">
<div class="kicker">03 / Configuration</div>

## Agent Configuration

### Claude Code CLI
```bash
claude mcp add gsc -- uvx google-search-console-mcp
```

### Cursor &amp; Google Antigravity (`mcp.json`)
```json
{
  "mcpServers": {
    "google-search-console": {
      "command": "uvx",
      "args": ["google-search-console-mcp"],
      "env": {
        "GSC_CREDENTIALS_PATH": "/path/to/service-account.json"
      }
    }
  }
}
```

</section>

---

<section id="tools-reference" class="space-y-6">
<div class="kicker">04 / Tools</div>

## Tool &amp; Parameter Reference

| Tool Name | Parameters | Description | Return Type |
|:---|:---|:---|:---|
| `get_search_analytics` | `site_url`, `start_date`, `end_date`, `dimensions` | Fetches clicks, impressions, CTR, and average position across query/page/device. | `JSON` |
| `inspect_url` | `site_url`, `inspection_url` | Checks index status, crawl issues, and canonical URL compliance. | `JSON` |
| `list_sites` | *(none)* | Lists all verified web properties in the connected GSC account. | `JSON` |
| `list_sitemaps` | `site_url` | Retrieves all submitted sitemaps and their last crawl timestamp. | `JSON` |
| `submit_sitemap` | `site_url`, `feedpath` | Submits a fresh XML sitemap endpoint to Googlebot. | `JSON` |

</section>
