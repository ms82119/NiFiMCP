# Documentation Workflow: Prompt Data Flow Guide

This guide shows exactly what data flows from shared state through to LLM prompts, helping identify where information is lost or missing.

## Overview

The documentation workflow has three main phases:
1. **Discovery**: Gathers all flow components into `flow_graph`
2. **Analysis**: Creates `pg_summaries` for each process group
3. **Documentation**: Generates final document from `pg_summaries`

## Data Flow: Discovery → Analysis → Documentation

### Phase 1: Discovery (`DiscoveryNode`)

**Output in `shared_state`:**
- `flow_graph.processors`: All processors with full details (properties, relationships, etc.)
- `flow_graph.connections`: All connections between components
- `flow_graph.process_groups`: All process groups
- `flow_graph.ports`: All input/output ports
- `pg_tree`: Hierarchical structure with processor counts

**Example for "Narrative Extraction API" (0c7d69a8-2820-3747-98ed-1b227e144aa1):**
- 13 processors discovered in `flow_graph.processors` with `_parent_pg_id: "0c7d69a8-2820-3747-98ed-1b227e144aa1"`
- Processors include: "Run Spacy Python script", "ControlRate", "POST old payload to Pre Index", "GET updated payload", "Call Transaction API", "Execute Delay Interval", "JoltTransformJSON", etc.
- All have full properties in `flow_graph.processors[proc_id].properties`

### Phase 2: Analysis (`HierarchicalAnalysisNode`)

**Decision Logic (line 974-981):**
```python
if all_child_summaries:
    summary = await self._analyze_pg_with_summaries(pg, all_child_summaries, prep_res)
else:
    summary = await self._analyze_pg_direct(pg, direct_processors, prep_res)
```

**Problem Identified:**
- If a PG has children, it uses `_analyze_pg_with_summaries` which **ONLY** includes child summaries
- It does **NOT** include the parent PG's own processors, business logic, or IO endpoints
- This causes parent PGs with both children AND their own processors to lose all their processor details

**Example: "Narrative Extraction API"**
- Has 1 child: "Response Handler"
- Has 13 of its own processors
- Uses `_analyze_pg_with_summaries` → **Loses all 13 processors' details**

#### Method 1: `_analyze_pg_direct` (for leaf PGs)

**Input from shared state:**
- `direct_processors`: Processors directly in this PG (from `flow_graph.processors`)
- `prep_res["flow_graph"]`: Full flow graph for connections/error handling

**Data extracted:**
1. **Categories**: Processor names grouped by category (LOGIC, IO_READ, IO_WRITE, TRANSFORM, etc.)
2. **Business Properties**: Fetched via `get_nifi_object_details` with `doc_optimized` format:
   - RouteOnAttribute: Routing rules (e.g., `MT7xx`, `NonMT7xx` expressions)
   - EvaluateJsonPath: JSONPath expressions (e.g., `$["MessageType"]`, `$["Transaction ID"]`)
   - UpdateAttribute: Attribute update expressions (e.g., `${http.headers.Authorization}`)
3. **IO Endpoints**: Detailed endpoint information (URLs, ports, methods, etc.)
4. **Error Handling**: Unhandled error relationships

**Prompt used:** `PG_SUMMARY_PROMPT`

**Prompt includes:**
- `categories_json`: Processor names by category
- `io_endpoints_json`: Detailed IO endpoint information
- `business_logic_json`: Routing rules, JSONPath expressions, attribute updates
- `child_summaries_json`: Empty for leaf PGs

**Example from `llm_debug.log` (Top Level Listener):**
```json
{
  "categories_json": {
    "LOGIC": ["RouteOnAttribute", "Get Token", "EvaluateJsonPath", ...],
    "IO_READ": ["Listen for incoming requests"],
    "TRANSFORM": ["Default body result"]
  },
  "business_logic_json": [
    {
      "processor": "RouteOnAttribute",
      "type": "RouteOnAttribute",
      "properties": {
        "MT7xx": "${http.method:equals(\"POST\"):and(${messageType:startsWith(\"MT7\")})}",
        "NonMT7xx": "${http.method:equals(\"POST\"):and(${messageType:startsWith(\"MT7\"):equals(\"false\")})}"
      }
    },
    {
      "processor": "EvaluateJsonPath",
      "type": "EvaluateJsonPath",
      "properties": {
        "messageType": "$[\"MessageType\"]",
        "transactionID": "$[\"Transaction ID\"]"
      }
    }
  ]
}
```

#### Method 2: `_analyze_pg_with_summaries` (for parent PGs)

**Input from shared state:**
- `child_summaries`: Summaries of child PGs (already computed)
- **Missing**: Parent PG's own processors!

**Data extracted:**
- Only aggregates from children:
  - Child summaries (purpose, IO endpoints, error handling)
  - No business logic from parent's processors
  - No categories from parent's processors
  - No IO endpoints from parent's processors

**Prompt used:** `PG_WITH_CHILDREN_PROMPT`

**Prompt includes:**
- `children_digest`: Child summaries with purpose, IO, error handling
- **Missing**: Parent's own processors, business logic, categories

**Example from `llm_debug.log` (Narrative Extraction API):**
```json
{
  "children_digest": [
    {
      "name": "Response Handler",
      "purpose": "...",
      "io": [...],
      "error_handling": [...]
    }
  ]
}
```

**What's missing:**
- 13 processors in "Narrative Extraction API" itself
- Business logic from those processors (e.g., "Execute Delay Interval" RouteOnAttribute with delay logic)
- IO endpoints from those processors (e.g., "Run Spacy Python script" InvokeHTTP to `http://nifi:8910`)
- Categories of those processors

### Phase 3: Documentation (`DocumentationNode`)

**Input from shared state:**
- `pg_summaries`: All PG summaries (output from Analysis phase)
- Uses summaries to generate:
  - Executive Summary
  - Mermaid Diagram
  - Hierarchical Documentation
  - IO Tables
  - Error Handling Tables

**Problem:** If analysis phase didn't capture parent PG's processors, documentation phase can't include them.

## Current Issues

### Issue 1: Parent PGs with Own Processors Lose Details

**Example: "Narrative Extraction API"**
- **Has:** 13 processors including:
  - "Run Spacy Python script" (InvokeHTTP to `http://nifi:8910`)
  - "ControlRate" (rate limiting)
  - "POST old payload to Pre Index" (InvokeHTTP to Elasticsearch)
  - "GET updated payload" (InvokeHTTP to Elasticsearch)
  - "Call Transaction API" (InvokeHTTP to `https://d.proserve.napier.cloud/api/transactions/score`)
  - "Execute Delay Interval" (RouteOnAttribute with delay logic)
  - "JoltTransformJSON" (data transformation)
  - "Set Delay Interval" (UpdateAttribute)
- **Analysis includes:** Only child "Response Handler" summary
- **LLM sees:** Only child summary, no parent processors
- **Result:** Generic documentation that doesn't mention the 13 processors or their business logic

### Issue 2: Business Properties Extraction is Hardcoded

**Current approach:**
- `_extract_business_properties` in `review.py` has hardcoded processor types:
  - RouteOnAttribute
  - EvaluateJsonPath
  - UpdateAttribute
  - InvokeHTTP
  - ExecuteScript
  - JoltTransformJSON
  - QueryRecord
  - GetFile/PutFile
  - Kafka processors
  - etc.

**Problem:**
- If a processor type isn't in the hardcoded list, its properties aren't extracted
- Need to manually add each processor type
- Not sustainable for all NiFi processor types

**Alternative approach needed:**
- Extract ALL properties that contain expressions (`${...}` or `$[...]`)
- Extract ALL properties that look like business logic (URLs, paths, queries, etc.)
- Use heuristics rather than hardcoded lists

### Issue 3: Processor Properties in `flow_graph` vs `doc_optimized`

**Discovery phase stores:**
- `flow_graph.processors[proc_id].properties`: Full properties dict

**Analysis phase fetches:**
- `get_nifi_object_details` with `doc_optimized` format
- This may not include all properties (only "business_properties")

**Question:** Should we use `flow_graph.processors` directly instead of re-fetching?

## Recommendations

### Fix 1: Include Parent PG's Processors When It Has Children

**Change `_analyze_pg_with_summaries` to also include parent's processors:**

```python
async def _analyze_pg_with_summaries(
    self,
    pg: Dict,
    child_summaries: List[Dict],
    prep_res: Dict[str, Any],
    direct_processors: List[Dict]  # ADD: Parent's own processors
) -> Dict:
    # Get parent's own processor details
    if direct_processors:
        # Extract categories, business logic, IO endpoints from parent's processors
        parent_categories = self._categorize_processors(direct_processors)
        # ... fetch business properties ...
        # ... extract IO endpoints ...
    
    # Combine parent's data with child summaries
    prompt = PG_WITH_CHILDREN_AND_PROCESSORS_PROMPT.format(
        # ... include parent's processors, business logic, IO endpoints ...
        # ... include child summaries ...
    )
```

### Fix 2: Make Business Properties Extraction More Generic

**Instead of hardcoded processor types, extract:**
- All properties with Expression Language (`${...}`)
- All properties with JSONPath (`$[...]`)
- All properties that look like URLs, paths, queries
- All properties that are non-empty and non-default

### Fix 3: Use `flow_graph.processors` Directly

**Instead of re-fetching via `get_nifi_object_details`:**
- Use `flow_graph.processors[proc_id].properties` directly
- Only fetch additional details if needed (e.g., for IO endpoint extraction)

## Example: "Narrative Extraction API" Data Flow

### What's in Shared State (`analysis_shared_state.json`)

**`flow_graph.processors` contains 13 processors:**
1. "Run Spacy Python script" (InvokeHTTP) - `properties: {}` (empty in shared state, but should have URL)
2. "ControlRate" (ControlRate) - `properties: {}`
3. "POST old payload to Pre Index" (InvokeHTTP) - `properties: {}`
4. "GET updated payload" (InvokeHTTP) - `properties: {}`
5. "Call Transaction API" (InvokeHTTP) - `properties: {}`
6. "Execute Delay Interval" (RouteOnAttribute) - `properties: {}`
7. "JoltTransformJSON" (JoltTransformJSON) - `properties: {}`
8. "Set Delay Interval" (UpdateAttribute) - `properties: {}`
9. "LogMessage" (LogMessage) - `properties: {}`
10. "LogAttribute" (LogAttribute) - `properties: {}`
11. "GenerateFlowFile" (GenerateFlowFile) - `properties: {}`
12. Plus 2 more...

**Note:** Properties are empty `{}` in shared state! This suggests they weren't fetched during discovery or were filtered out.

**`pg_summaries["0c7d69a8-2820-3747-98ed-1b227e144aa1"]` contains:**
- `summary`: Generic text about managing responses
- `child_count`: 1
- `children`: ["Response Handler"]
- `io_endpoints`: Only from child "Response Handler"
- `error_handling`: Includes errors from parent's processors (so they exist!)
- **Missing:** `categories`, `business_logic`, parent's own `io_endpoints`

### What's Sent to LLM (`llm_debug.log` line 95-122)

**Prompt:** `PG_WITH_CHILDREN_PROMPT`

**Includes:**
- Child summary for "Response Handler"
- Child's IO endpoints
- Child's error handling

**Missing:**
- Parent's 13 processors
- Parent's business logic
- Parent's IO endpoints (e.g., "Call Transaction API" to `https://d.proserve.napier.cloud/api/transactions/score`)
- Parent's categories

### What LLM Produces

Generic summary that only mentions:
- Managing client responses
- Response Handler sub-component
- HTTP status codes (200/500)

**Missing:**
- The actual processing logic (Spacy script, Elasticsearch operations, transaction API calls)
- The delay/retry logic
- The data transformation steps
- The external system interactions

## Next Steps

1. **Fix `_analyze_pg_with_summaries`** to include parent's processors when present
2. **Enhance business properties extraction** to be more generic
3. **Verify properties are captured** in discovery phase (they appear empty in shared state)
4. **Create hybrid prompt** that includes both parent processors AND child summaries

