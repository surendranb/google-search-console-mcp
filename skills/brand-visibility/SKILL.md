---
name: brand-visibility
description: "Analyzes Branded vs Non-Branded performance from the native Search Console classification to see if you are a Reference or a Destination."
---

# Brand Visibility Intel

**When to use:** Use this pattern to evaluate how much traffic comes from branded searches versus generic category searches. High branded traffic indicates strong brand trust (you are a Destination), while low branded traffic indicates you are competing purely on generic intent (you are a Reference).

**How to execute — native, one call:**
1. Call `get_search_analytics` with `dimensions=["brandedClassification"]`. Google returns one row per bucket, `BRANDED` and `NON_BRANDED`, each with clicks, impressions, ctr, and position. No brand term list, no regex.
2. Share of branded impressions = BRANDED impressions / (BRANDED + NON_BRANDED impressions) * 100. Same for clicks.
3. To slice one bucket, add `page`, `device`, `country`, or `date` to `dimensions`. To filter instead, use `filters=[{"dimension": "brandedClassification", "operator": "equals", "expression": "BRANDED"}]` — only `equals` is supported, and it cannot be combined with a `query` filter in the same request.
4. Coverage: data exists from 2026-02-21 onward, and only for top-level properties with enough query and impression volume.

**Zero rows is not zero branded traffic.** Properties outside that set get the rows omitted entirely, so an empty response says nothing about branded share. The tool returns a `caveat` field on an empty branded result and states which case applies. If the caveat says the share is UNKNOWN, report it as unknown — never as 0% and never as "no branded traffic".

**Fallback, only when the caveat says the property is not eligible:** approximate with `dimension="query"`, `operator="contains"`, `expression="<brand name>"` for branded and `operator="notContains"` for the rest, at `row_limit=1000`. Label the result as an approximation: it misses misspellings and brand mentions inside longer queries that Google's classifier does catch.
