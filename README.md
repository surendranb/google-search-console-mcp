<p align="center">
  <img src="logo.svg" alt="Google Search Console MCP Logo" width="120" />
</p>

# Google Search Console MCP "Intel Engine" 🚀

**The Authority-Based Visibility Governance Tool for the Evolving Search Landscape.**

This is not just a data wrapper. It is a strategic "Intel" engine that transforms raw Google Search Console signals into actionable marketing insights. It is designed for marketers who need to understand their performance in a search landscape increasingly defined by AI Overviews and conversational search. **Compatible with any MCP-compliant AI Agent.**

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

## 🚀 Getting Started

### 1. Google Search Console Setup
Before installing the MCP server, you must configure Google Cloud and Search Console access:

**A. Create Service Account:**
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project and enable the **Google Search Console API**.
3. Go to **APIs & Services > Credentials** and create a **Service Account**.
4. Create a **JSON Key** for the service account and download it (save as `gsc-key.json`).

**B. Grant Access in Search Console:**
1. Open your JSON key file and copy the `client_email` address.
2. Go to [Google Search Console](https://search.google.com/search-console).
3. Select your property and go to **Settings > Users and Permissions**.
4. Click **Add User**, paste the service account email, and select **Full** permissions.

**C. Identify Your Property URL:**
- For **Domain properties**, use the format: `sc-domain:example.com`
- For **URL-prefix properties**, use the full URL: `https://example.com/`

### 2. Installation
```bash
pip install google-search-console-mcp
```

### 3. Configuration (Universal AI Agent)
Add this to your agent's MCP settings file:
```json
{
  "mcpServers": {
    "gsc-search": {
      "command": "gsc-mcp",
      "env": {
        "GOOGLE_APPLICATION_CREDENTIALS": "/path/to/your/gsc-key.json",
        "GSC_SITE_URL": "sc-domain:example.com"
      }
    }
  }
}
```

---

## 🛠️ Project Philosophy
This project focuses on **high-leverage data analysis** for modern search:
- **Simplicity First**: Minimum code for maximum insight.
- **Token Efficiency**: Server-side aggregation prevents "Context Length" issues.
- **Authoritative Data**: We only use official Google Search Console API signals. No speculative "AI SEO" hacks.

---

## License
MIT License