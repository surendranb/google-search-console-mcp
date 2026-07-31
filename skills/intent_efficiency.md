title: Intent Efficiency
description: Aggregates metrics by 'User Intent' (Informational vs. Transactional).

# Strategic Intent Intel

**When to use:** Use this pattern to understand what phase of the buyer's journey your traffic is coming from.

**How to execute:**
1. Call `get_search_analytics` with `dimensions=["query"]`, `row_limit=5000`.
2. Client-side processing: Iterate over the rows and classify queries:
   - **Informational**: Contains words like 'how', 'why', 'what', 'guide', 'tutorial', 'vs', 'compare'.
   - **Transactional**: Contains words like 'buy', 'price', 'tool', 'service', 'app', 'download'.
   - **Navigational**: All other queries (typically brand or direct product searches).
3. Sum the clicks and impressions for each bucket to find the 'Share of Traffic' per intent type.
