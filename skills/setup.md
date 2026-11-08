---
title: Google Search Console Setup & Authentication Guide
description: Step-by-step instructions for human operators and AI agents to configure GCP credentials, GSC user permissions, and MCP client harnesses.
version: 1.0.0
---

# Google Search Console (GSC) MCP Access Setup Guide

Use this playbook whenever you encounter an unconfigured Search Console environment, `401 Unauthorized`, `403 Forbidden`, or missing `GOOGLE_APPLICATION_CREDENTIALS`.

---

## 1. Quick Diagnostic Check

Run `list_sites` first. If it returns an initialization error, check which environment variable is missing:
- **Missing `GOOGLE_APPLICATION_CREDENTIALS`**: Follow Steps 2 & 3 below to provision a service account.
- **Missing `GSC_SITE_URL`**: Set your default site URL in your client configuration (e.g. `https://example.com/` or `sc-domain:example.com`). Note: individual queries now accept dynamic `site_url` parameters.
- **403 Permission Denied**: The service account exists, but has not been added as a user in Search Console (Step 3).

---

## 2. Provision Service Account in Google Cloud

### Via Web Console:
1. Open [Google Cloud Console API Library](https://console.cloud.google.com/apis/library/searchconsole.googleapis.com) and click **Enable** for the Google Search Console API.
2. Navigate to [IAM & Admin > Service Accounts](https://console.cloud.google.com/iam-admin/serviceaccounts).
3. Click **Create Service Account**:
   - Name: `gsc-mcp-reader`
   - Role: None needed at the GCP project level (permissions are granted inside Search Console).
4. Click on the created service account > **Keys** tab > **Add Key** > **Create new key** > Select **JSON** > **Create**.
5. Save the downloaded JSON file to a secure local path (e.g. `~/.config/gsc/service_account.json`). Set file permissions to `chmod 600`.

### Via `gcloud` CLI (Alternative):
```bash
PROJECT_ID=$(gcloud config get-value project)
gcloud services enable searchconsole.googleapis.com
gcloud iam service-accounts create gsc-mcp-reader --display-name="GSC MCP Reader"
mkdir -p ~/.config/gsc
gcloud iam service-accounts keys create ~/.config/gsc/service_account.json \
  --iam-account="gsc-mcp-reader@${PROJECT_ID}.iam.gserviceaccount.com"
chmod 600 ~/.config/gsc/service_account.json
```

---

## 3. Grant Access in Google Search Console

1. Open the [Google Search Console Users & Permissions Console](https://search.google.com/search-console/users).
2. Select your property in the top-left dropdown.
3. Click **Add user** in the top-right corner.
4. In the **Email address** field, paste the `client_email` address from your JSON key file (e.g., `gsc-mcp-reader@<project>.iam.gserviceaccount.com`).
5. In the **Permission** dropdown, select **Full** (or **Owner** if you need sitemap deletion/URL inspection).
6. Click **Add**. Permissions apply within seconds.

> **CRITICAL URL FORMAT NOTE**:
> - Domain properties require the prefix `sc-domain:` (e.g., `sc-domain:example.com`).
> - URL-prefix properties require the full protocol and trailing slash (e.g., `https://example.com/`).

---

## 4. Configure Your MCP Client

Add the following configuration to your client's MCP configuration file:

### Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "google-search-console": {
      "command": "uvx",
      "args": ["google-search-console-mcp"],
      "env": {
        "GOOGLE_APPLICATION_CREDENTIALS": "/Users/YOUR_USER/.config/gsc/service_account.json",
        "GSC_SITE_URL": "https://example.com/"
      }
    }
  }
}
```

### Cursor (`~/.cursor/mcp.json` or `.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "google-search-console": {
      "command": "uvx",
      "args": ["google-search-console-mcp"],
      "env": {
        "GOOGLE_APPLICATION_CREDENTIALS": "/Users/YOUR_USER/.config/gsc/service_account.json",
        "GSC_SITE_URL": "https://example.com/"
      }
    }
  }
}
```

### Claude Code CLI:
```bash
claude mcp add google-search-console -e GOOGLE_APPLICATION_CREDENTIALS="/Users/YOUR_USER/.config/gsc/service_account.json" -e GSC_SITE_URL="https://example.com/" -- uvx google-search-console-mcp
```

---

## 5. Verify the Connection

Once configured and the client is restarted, run:
1. `list_sites()`: Should return your verified web properties with `permissionLevel: siteFullUser` or `siteOwner`.
2. `get_data_status(site_url="https://example.com/")`: Should return the freshest available search date and data publication status.
