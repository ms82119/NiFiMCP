# Analysis Phase: Prompt Call Sequence and Shared State

## Overview

The analysis phase processes process groups (PGs) hierarchically from **leaves to root** (bottom-up), analyzing each PG at each depth level before moving to the parent level.

## Execution Flow

### Entry Point: `HierarchicalAnalysisNode.exec_async()`

**Location**: `nifi_mcp_server/workflows/nodes/doc_nodes.py:990`

**Shared State Input**:
- `shared["pg_tree"]` - Hierarchical structure of all PGs with depth information
- `shared["flow_graph"]` - All processors, connections, process groups
- `shared["current_depth"]` - Current depth being processed (starts at max_depth)
- `shared["pg_summaries"]` - Accumulated summaries (initially empty, populated as we go up)

**Processing Loop**:
1. Get all PGs at `current_depth`
2. For each PG at this depth:
   - Check if it's large (>threshold processors)
   - If large: Create virtual groups first
   - Analyze the PG (or each virtual group)
   - Store summary in `new_summaries`
3. After all PGs at depth are processed:
   - Merge `new_summaries` into `shared["pg_summaries"]`
   - Decrement `current_depth`
   - Self-loop if `current_depth > 0`
   - Transition to GENERATION phase when `current_depth == 0`

---

## Prompt Call Sequence

### For Each Process Group at Current Depth:

#### Step 1: Virtual Group Creation (if needed)

**Condition**: `proc_count > threshold` (default: 20 processors)

**Function**: `create_virtual_groups()`  
**Location**: `nifi_mcp_server/workflows/nodes/analysis_helpers.py:30`

**Prompt Used**: `VIRTUAL_SUBFLOW_PROMPT`  
**Prompt Location**: `nifi_mcp_server/workflows/prompts/documentation.py:15`

**Input Data**:
- Processors list with categories
- Connections between processors
- Min/max groups (from config: 3-7)

**Output**: List of virtual group definitions (JSON array)
```json
[
  {
    "name": "Data Ingestion",
    "purpose": "Fetches records from SFTP...",
    "processor_ids": ["abc12345", "def67890"]
  }
]
```

**Shared State**: Stored in `new_virtual_groups[pg_id]` → merged into `shared["virtual_groups"][pg_id]`

---

#### Step 2: Virtual Group Analysis (if virtual groups were created)

**For each virtual group**:

**Function**: `analyze_virtual_group()`  
**Location**: `nifi_mcp_server/workflows/nodes/analysis_helpers.py:104`

**Prompt Used**: `PG_SUMMARY_PROMPT`  
**Prompt Location**: `nifi_mcp_server/workflows/prompts/documentation.py:56`

**Input Data**:
- Virtual group definition (name, purpose, processor_ids)
- Processors in the virtual group
- Connections between processors in the group
- IO endpoints (extracted from processors)
- Business logic (routing rules, JSONPath expressions, etc.)
- Error handling relationships

**Output**: Summary dict
```python
{
    "name": "Data Ingestion",
    "purpose": "Fetches records from SFTP...",
    "summary": "Comprehensive technical analysis...",  # LLM-generated text
    "processor_count": 5,
    "categories": {...},
    "io_endpoints": [...],
    "error_handling": [...],
    "virtual": True
}
```

**Shared State**: Added to `virtual_group_summaries` list → used as child summaries for parent PG

---

#### Step 3: Parent PG Analysis

**Decision Logic** (in `HierarchicalAnalysisNode.exec_async()` line 1173-1183):

```python
if all_child_summaries:
    # Has children (real PGs or virtual groups)
    summary = await analyze_pg_with_summaries(...)
else:
    # Leaf PG - no children
    summary = await analyze_pg_direct(...)
```

---

##### Option A: Leaf PG (No Children)

**Function**: `analyze_pg_direct()`  
**Location**: `nifi_mcp_server/workflows/nodes/analysis_helpers.py:415`

**Prompt Used**: `PG_SUMMARY_PROMPT`  
**Prompt Location**: `nifi_mcp_server/workflows/prompts/documentation.py:56`

**Input Data**:
- PG name
- Processors in this PG (categorized with states)
- Connections between processors
- IO endpoints (extracted from processors)
- Business logic (routing rules, JSONPath expressions, etc.)
- Error handling relationships
- Empty child summaries (`[]`)

**Output**: Summary dict
```python
{
    "name": "Response Handler",
    "purpose": "",
    "summary": "Comprehensive technical analysis...",  # LLM-generated text
    "processor_count": 3,
    "categories": {...},
    "io_endpoints": [...],
    "error_handling": [...],
    "virtual": False
}
```

**Shared State**: Stored in `new_summaries[pg_id]` → merged into `shared["pg_summaries"][pg_id]`

---

##### Option B: Parent PG with Children Only (No Own Processors)

**Function**: `analyze_pg_with_summaries()`  
**Location**: `nifi_mcp_server/workflows/nodes/analysis_helpers.py:249`

**Prompt Used**: `PG_WITH_CHILDREN_PROMPT`  
**Prompt Location**: `nifi_mcp_server/workflows/prompts/documentation.py:133`

**Input Data**:
- PG name
- Child summaries digest (name, purpose, IO, error_handling from each child)
- No own processors

**Output**: Summary dict
```python
{
    "name": "Parent PG",
    "purpose": "",
    "summary": "Comprehensive technical analysis...",  # LLM-generated text
    "processor_count": 0,
    "categories": {},
    "io_endpoints": [...],  # Aggregated from children
    "error_handling": [...],  # Aggregated from children
    "virtual": False
}
```

**Shared State**: Stored in `new_summaries[pg_id]` → merged into `shared["pg_summaries"][pg_id]`

---

##### Option C: Parent PG with Children AND Own Processors

**Function**: `analyze_pg_with_summaries()` (with `has_parent_processors = True`)  
**Location**: `nifi_mcp_server/workflows/nodes/analysis_helpers.py:249`

**Prompt Used**: `PG_WITH_CHILDREN_AND_PROCESSORS_PROMPT`  
**Prompt Location**: `nifi_mcp_server/workflows/prompts/documentation.py:177`

**Input Data**:
- PG name
- Child summaries digest (name, purpose, IO, error_handling from each child)
- Own processors (categorized with states)
- Connections between own processors
- IO endpoints from own processors
- Business logic from own processors
- Error handling from own processors

**Output**: Summary dict
```python
{
    "name": "Parent PG",
    "purpose": "",
    "summary": "Comprehensive technical analysis...",  # LLM-generated text
    "processor_count": 5,  # Own processors
    "categories": {...},  # From own processors
    "io_endpoints": [...],  # From own processors + aggregated from children
    "error_handling": [...],  # From own processors + aggregated from children
    "virtual": False
}
```

**Shared State**: Stored in `new_summaries[pg_id]` → merged into `shared["pg_summaries"][pg_id]`

---

## Shared State Keys

### Input (Read from `shared`):
- `shared["pg_tree"]` - PG hierarchy with depth information
- `shared["flow_graph"]` - All processors, connections, process groups
- `shared["current_depth"]` - Current depth being processed
- `shared["pg_summaries"]` - Previously computed summaries (for child lookups)
- `shared["virtual_groups"]` - Previously created virtual groups
- `shared["config"]` - Configuration (thresholds, min/max groups, etc.)

### Output (Written to `shared`):
- `shared["pg_summaries"][pg_id]` - Summary dict for each PG
  - Structure: `{"name": str, "purpose": str, "summary": str, "processor_count": int, "categories": dict, "io_endpoints": list, "error_handling": list, "virtual": bool}`
- `shared["virtual_groups"][pg_id]` - List of virtual group definitions for large PGs
- `shared["current_depth"]` - Decremented after each depth level
- `shared["current_phase"]` - Set to "GENERATION" when `current_depth == 0`
- `shared["metrics"]["analysis"]` - Analysis metrics (pgs_analyzed, virtual_groups_created, etc.)

---

## Example Execution Flow

For a flow with 3 levels (depth 2 = leaves, depth 1 = middle, depth 0 = root):

### Depth 2 (Leaves):
1. PG-A (leaf, 5 processors) → `analyze_pg_direct()` → `PG_SUMMARY_PROMPT`
2. PG-B (leaf, 8 processors) → `analyze_pg_direct()` → `PG_SUMMARY_PROMPT`
3. PG-C (leaf, 25 processors) → 
   - `create_virtual_groups()` → `VIRTUAL_SUBFLOW_PROMPT`
   - For each virtual group: `analyze_virtual_group()` → `PG_SUMMARY_PROMPT`
4. Store all summaries in `shared["pg_summaries"]`
5. Decrement depth: `current_depth = 1`

### Depth 1 (Middle):
1. PG-D (parent, has children: PG-A, PG-B, no own processors) → 
   - `analyze_pg_with_summaries()` → `PG_WITH_CHILDREN_PROMPT`
2. PG-E (parent, has children: PG-C, has 3 own processors) → 
   - `analyze_pg_with_summaries()` → `PG_WITH_CHILDREN_AND_PROCESSORS_PROMPT`
3. Store all summaries in `shared["pg_summaries"]`
4. Decrement depth: `current_depth = 0`

### Depth 0 (Root):
1. PG-ROOT (parent, has children: PG-D, PG-E, no own processors) → 
   - `analyze_pg_with_summaries()` → `PG_WITH_CHILDREN_PROMPT`
2. Store summary in `shared["pg_summaries"]`
3. Set `shared["current_phase"] = "GENERATION"`
4. Transition to `DocumentationNode`

---

## Key Points

1. **Bottom-Up Processing**: Children are always analyzed before parents
2. **Virtual Groups**: Created only for large PGs (>threshold processors)
3. **Summary Accumulation**: Each PG's summary is stored immediately and used by its parent
4. **Three Analysis Paths**: 
   - Leaf PG → `PG_SUMMARY_PROMPT`
   - Parent with children only → `PG_WITH_CHILDREN_PROMPT`
   - Parent with children + own processors → `PG_WITH_CHILDREN_AND_PROCESSORS_PROMPT`
5. **Shared State Persistence**: All summaries are stored in `shared["pg_summaries"]` and persist through the workflow



