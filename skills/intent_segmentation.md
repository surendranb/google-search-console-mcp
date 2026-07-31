title: Intent Segmentation (Searchers vs Prompters)
description: Segments traffic into 'Searchers' (Keyword-based) vs. 'Prompters' (Natural Language).

# Prompter vs Searcher Segmentation

**When to use:** Use this pattern to detect if your audience is shifting from traditional keyword searches to conversational AI-style prompts.

**How to execute:**
1. Call `get_search_analytics` with `dimensions=["query"]`, `row_limit=5000`.
2. Client-side processing: Iterate over the queries and count the number of words in each query string.
3. **Prompters**: Queries with 7 or more words, OR containing conversational triggers ('how', 'why', 'vs', 'compare', 'best', 'summary', 'explain').
4. **Searchers**: All other shorter, keyword-centric queries.
5. Compare the total clicks, impressions, and average query length of both segments.
