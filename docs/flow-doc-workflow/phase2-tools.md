# Phase 2: Tool Enhancements

## Overview

**Goal**: Enhance API tools to efficiently gather and categorize NiFi flow data for documentation, with focus on token efficiency and batch operations.

**Duration Estimate**: 2 days

**Key Deliverables**:
1. Expandable processor categorization system
2. Batch mode for `get_nifi_object_details`
3. Port-to-port connection traversal
4. Token-optimized output formats

**Source Files**:
- [`src/tools/processor_categories.py`](src/tools/processor_categories.py) → Copy to `nifi_mcp_server/processor_categories.py`
- [`src/tools/batch_tool_extensions.py`](src/tools/batch_tool_extensions.py) → Apply to `nifi_mcp_server/api_tools/review.py`
- [`src/tools/port_traversal.py`](src/tools/port_traversal.py) → Add to `nifi_mcp_server/flow_documenter_improved.py`
- [`src/tools/token_counter_extensions.py`](src/tools/token_counter_extensions.py) → Apply to `nifi_chat_ui/llm/utils/token_counter.py`
- [`src/tools/output_profiles.py`](src/tools/output_profiles.py) → Add to `nifi_mcp_server/flow_documenter_improved.py`

---

## 1. Processor Categorization System

### 1.1 Design Goals

- **Expandable**: Easy to add new processor types as flows evolve
- **Heuristic-based**: No LLM needed for initial categorization
- **Unclassified tracking**: Report unknown types for review
- **Pattern matching**: Support wildcards (`Get*`) and regex patterns

### 1.2 Category Definitions

9 categories: `IO_READ`, `IO_WRITE`, `LOGIC`, `TRANSFORM`, `VALIDATION`, `ENRICHMENT`, `CONTROL`, `MONITORING`, `OTHER`

Pattern matching supports:
- Exact: `"GetFile"`
- Wildcard: `"Get*"`, `"*Record"`
- Regex: `"r:Execute(Script|Groovy|Python)"`

**See**: [`src/tools/processor_categories.py`](src/tools/processor_categories.py) - `PROCESSOR_CATEGORY_PATTERNS`

### 1.3 ProcessorCategorizer Class

Key methods:
- `categorize(processor_type)` - Categorize single processor
- `categorize_batch(processor_types)` - Batch categorization
- `get_unclassified()` - Get unknown types for review
- `needs_llm_analysis(processor_type)` - Check if needs deep analysis
- `should_always_document(processor_type)` - Check if always in docs

**See**: [`src/tools/processor_categories.py`](src/tools/processor_categories.py) - `ProcessorCategorizer` class

### 1.4 Unclassified Reporting

`report_unclassified_processors()` function identifies and logs unknown processor types for category expansion.

**See**: [`src/tools/processor_categories.py`](src/tools/processor_categories.py) - `report_unclassified_processors()`

---

## 2. Batch Mode for get_nifi_object_details

### 2.1 Extended Signature

Add support for:
- `object_ids: List[str]` - Batch retrieval (new)
- `output_format: Literal["full", "summary", "doc_optimized"]` - Format selection
- `include_properties: bool` - Toggle properties inclusion
- `max_parallel: int` - Limit concurrent requests (default 5)

**See**: [`src/tools/batch_tool_extensions.py`](src/tools/batch_tool_extensions.py) - `EXTENDED_SIGNATURE`

### 2.2 Implementation

- Single ID mode: Backward compatible (existing behavior)
- Batch mode: Parallel requests with semaphore limiting
- Returns: `List[Dict]` with `{"id", "status", "data"}` or `{"id", "status", "error"}`

**See**: [`src/tools/batch_tool_extensions.py`](src/tools/batch_tool_extensions.py) - `BATCH_IMPLEMENTATION`

### 2.3 Output Formats

Three output formats:
- `"full"` - Complete details (backward compatible)
- `"summary"` - Essential fields only (name, type, state, relationships)
- `"doc_optimized"` - Business-relevant properties with expressions, routing info

**See**: [`src/tools/batch_tool_extensions.py`](src/tools/batch_tool_extensions.py) - `OUTPUT_FORMATTERS`

### 2.4 Business Property Extraction

`_extract_business_properties()` extracts processor-type-specific properties:
- RouteOnAttribute: All routing conditions
- ExecuteScript: Script preview (truncated)
- JoltTransformJSON: Jolt spec
- QueryRecord: SQL queries
- InvokeHTTP: URL and method
- File processors: Directory and filters
- Kafka processors: Topic and bootstrap servers

**See**: [`src/tools/batch_tool_extensions.py`](src/tools/batch_tool_extensions.py) - `_extract_business_properties()`

---

## 3. Port-to-Port Connection Traversal

### 3.1 Problem

Flows spanning multiple PGs use:
- OUTPUT_PORT (source PG) → Connection → INPUT_PORT (target PG)

Current tools don't resolve the full chain. Documentation needs logical flow across PG boundaries.

### 3.2 Functions

- `resolve_port_connections()` - Enriches connections with `cross_pg`, `source_pg_name`, `dest_pg_name`
- `build_cross_pg_flow_map()` - Builds map of data flow between process groups

**See**: [`src/tools/port_traversal.py`](src/tools/port_traversal.py)

---

## 4. Token-Optimized Output Formats

### 4.1 Existing Token Infrastructure

The app already has `TokenCounter` class in `nifi_chat_ui/llm/utils/token_counter.py` with provider-specific counting.

### 4.2 TokenCounter Extensions

Add two new methods:
- `count_tokens_json(data, provider, model_name)` - Count tokens for JSON-serializable data
- `batch_by_token_limit(items, max_tokens, ...)` - Split items into token-limited batches

**See**: [`src/tools/token_counter_extensions.py`](src/tools/token_counter_extensions.py) - `TOKEN_COUNTER_EXTENSIONS`

### 4.3 Output Profiles

Four standard profiles:
- `"full"` - Complete details, no truncation
- `"summary"` - Essential fields only
- `"doc_optimized"` - Business-relevant, 500 char truncation
- `"llm_analysis"` - For LLM calls, 1000 char truncation

**See**: [`src/tools/output_profiles.py`](src/tools/output_profiles.py)

---

## 5. Files to Create/Modify

### New Files

| Source | Target |
|--------|--------|
| `src/tools/processor_categories.py` | `nifi_mcp_server/processor_categories.py` |
| `src/tools/output_profiles.py` | `nifi_mcp_server/flow_documenter_improved.py` (or separate) |

### Modified Files

| File | Changes | Source |
|------|---------|--------|
| `nifi_mcp_server/api_tools/review.py` | Extend `get_nifi_object_details` for batch mode | `src/tools/batch_tool_extensions.py` |
| `nifi_mcp_server/flow_documenter_improved.py` | Add port traversal functions | `src/tools/port_traversal.py` |
| `nifi_chat_ui/llm/utils/token_counter.py` | Add `count_tokens_json()`, `batch_by_token_limit()` | `src/tools/token_counter_extensions.py` |

---

## 6. Testing Verification

Before moving to Phase 3:

- [ ] Categorizer correctly classifies common processor types
- [ ] Unclassified types are tracked and reported
- [ ] Batch mode returns results for all IDs
- [ ] Batch mode respects max_parallel limit
- [ ] doc_optimized format reduces token count by 50%+
- [ ] Cross-PG connections are properly identified
- [ ] `count_tokens_json()` returns consistent counts for same data
- [ ] `batch_by_token_limit()` respects token limits accurately
- [ ] Existing `smart_prune_messages()` still works (regression check)

### Example Usage

```python
# Categorization
from nifi_mcp_server.processor_categories import get_categorizer
categorizer = get_categorizer()
category = categorizer.categorize("org.apache.nifi.processors.standard.RouteOnAttribute")
# Returns: ProcessorCategory.LOGIC

# Batch retrieval
results = await get_nifi_object_details(
    object_type="processor",
    object_ids=["id1", "id2", "id3"],
    output_format="doc_optimized",
    max_parallel=5
)

# Token counting
from nifi_chat_ui.llm.utils.token_counter import TokenCounter
counter = TokenCounter()
batches = counter.batch_by_token_limit(
    items=processor_list,
    max_tokens=8000,
    provider="openai",
    model_name="gpt-4",
    reserved_tokens=1000
)
```

---

## 7. Success Criteria

Phase 2 is complete when:

1. Categorizer correctly handles all processor types in test flows
2. `get_nifi_object_details` batch mode works with 50+ IDs
3. doc_optimized format reduces payload size by 40%+
4. Cross-PG connections are tracked in flow documentation
5. Token estimation enables proper LLM batching

---

## Next Phase

Proceed to [Phase 3: Node Implementation](./phase3-nodes.md) after Phase 2 verification.
