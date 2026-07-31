title: Citation Opportunities
description: Identifies 'Information Gaps' where your site ranks #1 but has low CTR.

# Citation Opportunity Audit

**When to use:** Find high-value pages that are serving as "Silent References" (e.g. for AI Overviews) without earning clicks, and recommend UI changes to capture that traffic.

**How to execute:**
1. Call `get_search_analytics` with `dimensions=["query", "page"]`, `row_limit=5000`.
2. Filter the results in-memory: Look for rows where Position <= 1.5 AND CTR < 1.0% AND Impressions > 20.
3. Analysis: These are your "Citation Opportunities". The user's intent is being satisfied directly on Google.
4. Recommendation: Instruct the user to add a 'Click-Trigger' to these specific pages (like a dynamic tool, template download, or gated resource) to force the click.
