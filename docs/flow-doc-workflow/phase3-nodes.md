# Phase 3: Node Implementation

## Overview

**Goal**: Implement the workflow nodes that form the documentation pipeline, using a **bottom-up hierarchical approach** that processes nested process groups from leaves to root.

**Duration Estimate**: 3 days

**Source Files**:
- [`src/nodes/doc_nodes.py`](src/nodes/doc_nodes.py) → Copy to `nifi_mcp_server/workflows/nodes/`
- [`src/prompts/documentation.py`](src/prompts/documentation.py) → Copy to `nifi_mcp_server/workflows/prompts/`

**Key Features**:
- Bottom-up hierarchical analysis
- Virtual sub-flow detection for large PGs
- **Detailed IO endpoint extraction** (files, URLs, topics, databases)
- **Lightweight error handling analysis** (handled vs ignored errors)

---

## 1. Key Design Principles

### Bottom-Up Hierarchical Approach

```
┌─────────────────────────────────────────────────────────────────┐
│  NiFi Process Group Hierarchy          Analysis Order           │
│                                                                 │
│  Root PG (depth 0)                      ┌─── 4. Analyzed last   │
│    ├── PG-A (depth 1)                   │    (uses summaries    │
│    │     ├── PG-A1 (depth 2) ──────────►│     from A, B, C)     │
│    │     └── PG-A2 (depth 2) ──────────►│                       │
│    ├── PG-B (depth 1)                   │                       │
│    │     └── [45 processors] ──────────►│    Virtual groups     │
│    │         (large, flat)              │    created first      │
│    └── PG-C (depth 1) ─────────────────►│                       │
│                                         │                       │
│  Processing: depth 2 → depth 1 → depth 0                        │
│              (leaves)            (root)                         │
└─────────────────────────────────────────────────────────────────┘
```

- Process deepest (leaf) process groups first
- Generate summaries for each PG
- Parent PGs use child summaries (not raw processors)
- Final documentation emerges from aggregated summaries

### Virtual Sub-Flow Detection

When a PG has more than `large_pg_threshold` processors (regardless of whether it has children):
1. LLM identifies logical groupings (3-7 groups)
2. Each virtual group gets its own summary
3. Parent PG analysis uses virtual group summaries + real child summaries
4. Results in consistent abstraction level at all parents

---

## 2. Node Transition Map

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  InitializeDocNode                                              │
│    │                                                            │
│    │ "default"                                                  │
│    ▼                                                            │
│  DiscoveryNode ◄─────────┐                                      │
│    │                     │ "continue" (pagination)              │
│    ├─────────────────────┘                                      │
│    │                                                            │
│    │ "complete"                                                 │
│    ▼                                                            │
│  HierarchicalAnalysisNode ◄──────┐                              │
│    │                             │ "next_level" (move up tree)  │
│    ├─────────────────────────────┘                              │
│    │                                                            │
│    │ "complete" (root processed)                                │
│    ▼                                                            │
│  DocumentationNode                                              │
│    │                                                            │
│    │ "default" (empty successors = end)                         │
│    ▼                                                            │
│  [WORKFLOW COMPLETE]                                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Node Summaries

### 3.1 InitializeDocNode

**Purpose**: Setup state structures for hierarchical processing.

**Key State Initialized**:
- `pg_tree`: {} - PG hierarchy with depth tracking
- `pg_summaries`: {} - Accumulated summaries (key output)
- `virtual_groups`: {} - Virtual groupings for large PGs
- `current_depth`: None - Set after discovery

**Transition**: `"default"` → DiscoveryNode

### 3.1.1 IO Endpoint Tracking (CRITICAL)

**IMPORTANT**: Each PG summary must include detailed `io_endpoints` with:
- File paths and directories (for file-based processors)
- URLs and HTTP methods (for HTTP processors)
- Kafka topics and bootstrap servers (for Kafka processors)
- Database connections and tables (for database processors)
- SFTP hostnames, ports, directories (for SFTP processors)
- Any other external system connection details

This information is **essential** for documentation and must be captured during analysis phase.

### 3.2 DiscoveryNode  

**Purpose**: Discover components and build PG hierarchy tree.

**Key Responsibilities**:
1. Fetch all processors with `_parent_pg_id` tracking
2. Fetch all process groups
3. Build `pg_tree` with parent/child relationships
4. Calculate depth for each PG
5. Track `processor_count` per PG (for virtual grouping)
6. Set `current_depth = max_depth` for bottom-up processing

**Transitions**:
- `"continue"` → self (pagination)
- `"complete"` → HierarchicalAnalysisNode

### 3.3 HierarchicalAnalysisNode

**Purpose**: Analyze PGs bottom-up, generating summaries.

**Algorithm**:
```
for depth in range(max_depth, -1, -1):  # Bottom to top
    for each PG at this depth:
        1. Get direct processors
        2. Get child PG summaries (already computed)
        
        3. If processor_count > threshold:
           - Create virtual groups (LLM)
           - Generate summary for each virtual group
        
        4. Combine: child_summaries + virtual_group_summaries
        
        5. If has summaries:
             Analyze using summaries (not raw processors)
           Else:
             Analyze directly (small leaf PG)
        
6. **CRITICAL: Extract detailed IO endpoints**
   - Fetch processor details using batch mode
   - Extract endpoint-specific properties (paths, URLs, topics, etc.)
   - Store in summary.io_endpoints with full details

7. **Extract error handling information** (lightweight)
   - Analyze processor relationships for error/failure connections
   - Identify handled vs ignored errors
   - Store in summary.error_handling
        
        7. Store summary for parent to use
```

**Key Methods**:
- `_extract_io_endpoints_detailed()` - Fetches detailed processor info for all IO processors
  - Extracts endpoint-specific properties based on processor type
  - Returns structured endpoint data with paths, URLs, topics, etc.
  - **This is called for every PG and virtual group**
- `_extract_error_handling()` - Lightweight error handling analysis
  - Identifies processors with error/failure relationships
  - Determines if errors are handled (connected) or ignored (auto-terminated)
  - Tracks where errors are routed to
  - **This is a lightweight initial implementation - can be expanded later**

**Transitions**:
- `"next_level"` → self (process next depth level)
- `"complete"` → DocumentationNode

### 3.4 DocumentationNode

**Purpose**: Generate final document from hierarchical summaries.

**Sections Generated**:
1. Executive summary (from root summary + aggregation)
   - **Must explicitly mention all IO endpoints** (paths, URLs, topics, etc.)
2. Mermaid diagram (showing PG hierarchy)
3. Hierarchical breakdown (nested sections)
   - Each section includes IO endpoints for that PG
4. **Detailed IO Endpoints Table** (CRITICAL SECTION)
   - Separate sections for Inputs and Outputs
   - For each endpoint, displays:
     - Processor name and type
     - Process group
     - **Full endpoint details** (paths, URLs, topics, connection strings, etc.)
     - Formatted by endpoint type (file_system, http, kafka, database, etc.)
5. **Error Handling Table** (Lightweight Analysis)
   - Shows where errors are handled (routed to processors)
   - Shows where errors are ignored (auto-terminated)
   - Highlights unhandled errors (potential issues)
   - Grouped by: Handled / Ignored / Unhandled

**Key Methods**:
- `_build_aggregated_io_table()` - Aggregates and formats IO endpoints
- `_build_error_handling_table()` - Creates error handling analysis table

**Transition**: `"default"` → end (empty successors)

---

## 4. Prompts Summary

| Prompt | Purpose | Used By | IO Emphasis |
|--------|---------|---------|-------------|
| `VIRTUAL_SUBFLOW_PROMPT` | Group processors in large flat PGs | HierarchicalAnalysisNode | - |
| `PG_SUMMARY_PROMPT` | Summarize leaf PG or virtual group | HierarchicalAnalysisNode | **YES** - Includes IO endpoints, emphasizes external interactions |
| `PG_WITH_CHILDREN_PROMPT` | Summarize parent using child summaries | HierarchicalAnalysisNode | - |
| `HIERARCHICAL_SUMMARY_PROMPT` | Generate executive summary | DocumentationNode | **YES** - Must explicitly mention all endpoint details (paths, URLs, topics) |
| `HIERARCHICAL_DIAGRAM_PROMPT` | Generate Mermaid flowchart | DocumentationNode | - |
| `BUSINESS_LOGIC_EXTRACTION_PROMPT` | Extract rules from LOGIC processors | Optional enhancement | - |

**Note**: Prompts are updated to emphasize IO endpoint details. The LLM is explicitly instructed to mention specific file paths, URLs, topics, databases, etc. in summaries.

## 4.1 Error Handling Analysis (Lightweight)

**Implementation**: `_extract_error_handling()` method in HierarchicalAnalysisNode

**Analysis Process**:
1. Build connection map: `source_id -> {relationship -> dest_id}`
2. For each processor, examine relationships with error keywords:
   - `failure`, `error`, `retry`, `invalid`, `unmatched`, `exception`
3. For each error relationship:
   - Check if connected → **Handled** (routed to destination)
   - Check if auto-terminated → **Ignored** (errors discarded)
   - Neither → **Unhandled** (potential issue)

**Output Structure**:
```python
{
    "processor": "ProcessorName",
    "processor_type": "GetFile",
    "error_relationship": "failure",
    "handled": True/False,
    "destination": "ErrorHandlerProcessor" | "IGNORED" | "NOT HANDLED",
    "auto_terminated": True/False
}
```

**Table Format**:
- **Handled Errors**: Shows processor → error relationship → destination
- **Ignored Errors**: Shows processor → error relationship (auto-terminated)
- **Unhandled Errors**: Shows processor → error relationship (warning section)

**Future Enhancements** (not in initial implementation):
- RetryFlowFile processor analysis
- Dead letter queue detection
- Error handling pattern recommendations
- Runtime error rate correlation

**Lightweight Implementation** (no LLM required):
- Uses processor relationship data (already fetched)
- Analyzes connections to determine error routing
- Identifies three categories:
  - **Handled**: Error relationship connected to another processor
  - **Ignored**: Error relationship auto-terminated
  - **Unhandled**: Error relationship exists but not connected/terminated (potential issue)

**Data Source**: Processor details include `relationships` array with `autoTerminate` flags and connection data shows `selectedRelationships`.

**Future Expansion**: Can add retry analysis, error pattern detection, dead letter queue tracking, etc.

---

## 5. Configuration

Add to `documentation_workflow` config section:

```yaml
documentation_workflow:
  analysis:
    large_pg_threshold: 25        # Trigger virtual grouping
    min_virtual_groups: 3         # Minimum groups to create
    max_virtual_groups: 7         # Maximum groups to create
    max_tokens_per_analysis: 8000 # Token budget per LLM call
```

---

## 6. Files to Create/Modify

### New Files

| Source | Target |
|--------|--------|
| `src/nodes/doc_nodes.py` | `nifi_mcp_server/workflows/nodes/doc_nodes.py` |
| `src/prompts/documentation.py` | `nifi_mcp_server/workflows/prompts/documentation.py` |
| | `nifi_mcp_server/workflows/prompts/__init__.py` |

### Modified Files

| File | Changes |
|------|---------|
| `nifi_mcp_server/workflows/nodes/async_nifi_node.py` | Add `emit_doc_phase_event()`, `call_nifi_tool()` |

---

## 7. Testing Verification

Before moving to Phase 4:

- [ ] InitializeDocNode creates proper hierarchical state structures
- [ ] DiscoveryNode builds correct `pg_tree` with depths
- [ ] DiscoveryNode self-loop handles pagination
- [ ] HierarchicalAnalysisNode processes leaves before parents
- [ ] Virtual grouping triggers for large PGs (>threshold)
- [ ] Virtual groups used even when PG has real children
- [ ] Parent analysis uses summaries, not raw processors
- [ ] DocumentationNode generates valid Mermaid with hierarchy
- [ ] All nodes emit appropriate events with metrics

---

## 8. Success Criteria

Phase 3 is complete when:

1. All 4 nodes execute without errors on test flow
2. Discovery builds correct PG tree with depths
3. Hierarchical analysis processes depth levels in order
4. Large PGs get virtual groupings
5. Parent summaries reference child summaries (not re-analyzing)
6. **IO endpoints are extracted with full details** (paths, URLs, topics, etc.)
7. **IO endpoint details appear in all summaries** (PG summaries, executive summary)
8. **IO table includes all endpoint specifics** formatted by type
9. **Error handling is analyzed and documented** (handled, ignored, unhandled)
10. **Error handling table shows all error relationships** and their destinations
11. Final document reflects hierarchical structure
12. Mermaid diagram shows PG hierarchy
13. **Documentation explicitly mentions all external systems and their connection details**

---

## Next Phase

Proceed to [Phase 4: Integration](./phase4-integration.md) after Phase 3 verification.
