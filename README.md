<p align="center">
  <img src="logo.svg" alt="Google Search Console MCP Logo" width="120" />
</p>

# Google Search Console MCP "Intel Engine" 🚀

**The Authority-Based Visibility Governance Tool for 2026.**

This is not just a data wrapper. It is a strategic "Intel" engine that transforms raw Google Search Console signals into actionable marketing insights. It is designed for marketers who need to understand their performance in a search landscape dominated by AI Overviews and conversational search.

## 🎯 Authoritative "Intel" Tools

| Tool Name | Actionable Marketing Intel Provided |
| :--- | :--- |
| **`get_search_appearance_audit`** | **Cannibalization Intel.** Detects if you are being used as a "Silent Reference" (high visibility but no clicks) in specialized SERP features. |
| **`get_intent_segmentation`** | **Strategic Audience Intel.** Segments traffic into "Searchers" (Traditional Keywords) vs. "Prompters" (Natural Language/AI Prompts). |
| **`identify_citation_opportunities`** | **Growth Intel.** Finds content that satisfies user intent so well that users don't click. Recommends "Click-Triggers." |
| **`get_technical_citation_audit`** | **Technical Health Overlay.** Cross-checks high-visibility pages with the URL Inspection API to find disqualifying crawl errors. |
| **`get_brand_visibility_summary`** | **Brand Health Intel.** Measures your Brand's "Reference Value" vs its "Destination Value." |
| **`calculate_intent_efficiency`** | **Conversion Intel.** Shows which search intent (Informational/Navigational) is most effectively driving site visits. |

---

## 🚀 Getting Started (April 2026)

### Prerequisites
- Python 3.11+
- Google Search Console property with data
- Service account JSON key file

### Installation
```bash
pip install google-search-console-mcp
```

### Configuration (Claude / Cursor)
Add this to your MCP settings:
```json
{
  "mcpServers": {
    "gsc-search": {
      "command": "python",
      "args": ["-m", "gsc_mcp_server"],
      "env": {
        "GOOGLE_APPLICATION_CREDENTIALS": "/path/to/your/gsc-key.json",
        "GSC_SITE_URL": "sc-domain:example.com"
      }
    }
  }
}
```

---

## 📊 How to Use the "Intel"

### 1. The Cannibalization Audit
Ask: *"Perform a search appearance audit. Is my content being cannibalized by AI Overviews?"*
- **What it does:** It checks for queries where you rank #1 but have a CTR < 1%. 
- **The Insight:** If yes, you are a "Reference Source" and need to add gated content or dynamic tools to that page to pull the user in.

### 2. The Intent Shift
Ask: *"Run an intent segmentation. How many of my users are 'Prompters'?"*
- **What it does:** It identifies natural language queries (7+ words) that are actually LLM prompts being redirected to your site.
- **The Insight:** This shows your "Semantic Authority." High "Prompter" volume means you are the authoritative source for complex explanations.

### 3. The Technical Health Check
Ask: *"Run a technical citation audit on my most visible pages."*
- **What it does:** It pulls your top-impressions pages and runs a real-time URL inspection.
- **The Insight:** It will tell you if a "Discovered - currently not indexed" error is preventing your best content from being used as a reference.

---

## 🛠️ Project Philosophy
We follow the **Karpathy Standard** of surgical engineering:
- **Simplicity First**: Minimum code for maximum insight.
- **Token Efficiency**: Server-side aggregation prevents "Context Length" issues.
- **Authoritative Data**: We only use official Google Search Console API signals. No speculative "AI SEO" hacks.

---

## License
MIT License