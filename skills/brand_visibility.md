title: Brand Visibility Summary
description: Analyzes 'Branded' vs 'Non-Branded' performance to see if you are a 'Reference' or a 'Destination'.

# Brand Visibility Intel

**When to use:** Use this pattern to evaluate how much traffic comes from branded searches versus generic category searches. High branded traffic indicates strong brand trust (you are a Destination), while low branded traffic indicates you are competing purely on generic intent (you are a Reference).

**How to execute:**
1. Call `get_search_analytics` with `dimensions=["query"]`, `row_limit=1000`.
2. Apply a filter where `dimension="query"`, `operator="contains"`, and `expression="<your_brand_name>"`. This gives you Branded traffic.
3. Call `get_search_analytics` again with `operator="notContains"` for Non-Branded traffic.
4. Calculate the Share of Branded Traffic = (Branded Impressions) / (Total Impressions) * 100.
