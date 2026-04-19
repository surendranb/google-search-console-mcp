# GSC MCP "Intel Engine" Roadmap

**Objective:** Transform the MCP from a raw data wrapper into a "Visibility Governance" tool for marketers, grounded strictly in authoritative Google signals (Search Appearance, URL Inspection, and Query Intent).

## Phase 1: Foundational Signal Segmentation [CURRENT]
- [x] **Branch Setup:** Create `feat/intel-engine` for safe iteration.
- [x] **Signal: Search Appearance Contrasting:**
    - Implement `get_search_appearance_audit()` to compare CTR/Position for standard vs. featured results.
    - *Authoritative Basis:* Google Search Console `searchAppearance` API dimension.
- [x] **Signal: Query Intent Clustering:**
    - Implement `get_intent_segmentation()` to group queries by length (7+ words = Conversational/LLM-style).
    - *Authoritative Basis:* Google's guidance on shifting search behavior.

## Phase 2: Citation & Reference Intelligence
- [x] **Signal: Citation Attribution Audit:**
    - Implement `identify_citation_opportunities()` identifying "Reference-Only" pages (High Pos / Low CTR).
- [x] **Signal: Technical Citation Health:**
    - Integrate `inspect_url()` and `get_technical_citation_audit()` to provide technical health overlays.

## Phase 3: Brand & Authority Governance
- [x] **Signal: Brand Retention Audit:**
    - Implement `get_brand_visibility_summary()` using branded filters.
- [x] **Efficiency Layer:**
    - Implement server-side aggregation to prevent "Context Length" issues by summarizing data before sending to Agents.

## Phase 4: Verification & Testing
- [x] **Validation:** Run "Cannibalization Audit" on live data.
- [ ] **Refinement:** Adjust thresholds based on actual CTR benchmarks for the evolving SERP.
