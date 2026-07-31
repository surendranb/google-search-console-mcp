title: Search Appearance Audit
description: Contrast standard vs. featured results bypassing GSC API limitations.

# Search Appearance Audit

**When to use:** The GSC API restricts grouping by `searchAppearance` alongside other dimensions (like `query`). Use this pattern to perform a server-side join and find which queries trigger featured snippets, AI overviews, or rich results.

**How to execute:**
1. Call `get_search_analytics` with `dimensions=["searchAppearance"]`, `row_limit=50` to find what special appearances your site triggers overall.
2. Call `get_search_analytics` with `dimensions=["query"]`, `row_limit=1000` to get your query performance.
3. Client-side heuristic join: Look for queries where Position <= 1.5 but CTR is < 1.5%. This is the "Silent Reference" pattern indicating your content is powering a featured snippet or AI overview but the user is satisfied on the SERP without clicking.
