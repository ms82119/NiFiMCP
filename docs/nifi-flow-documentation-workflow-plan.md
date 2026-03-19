# NiFi Flow Documentation Workflow - Master Implementation Plan

## Overview

This master plan implements a guided, iterative documentation workflow using PocketFlow's `AsyncFlow`. The implementation is divided into 5 phases, each with a dedicated sub-plan document in `docs/flow-doc-workflow/`.

---

## PocketFlow Pattern Reference (from source analysis)

Based on analysis of installed `pocketflow==0.0.2`:

```python
# Node wiring syntax
node.next(other_node, action="default")  # Method
node >> other_node                        # Default action shorthand
node - "action" >> other_node             # Conditional action shorthand

# Flow orchestration
# - Calls post_async() which returns action string
# - Looks up action in node.successors dict
# - Continues until get_next_node() returns None (no matching successor)

# Self-loop pattern (valid - flow uses copy.copy())
discovery_node - "continue" >> discovery_node
discovery_node - "complete" >> analysis_node
```

---

## Sub-Plan Documents

| Phase | Document | Description |

|-------|----------|-------------|

| 1 | `docs/flow-doc-workflow/phase1-infrastructure.md` | Logging, events, configuration |

| 2 | `docs/flow-doc-workflow/phase2-tools.md` | Tool enhancements, batch retrieval |

| 3 | `docs/flow-doc-workflow/phase3-nodes.md` | Node implementation, LLM algorithms |

| 4 | `docs/flow-doc-workflow/phase4-integration.md` | App changes, workflow wiring |

| 5 | `docs/flow-doc-workflow/phase5-testing.md` | Hybrid test strategy |

---

## Phase 1: Infrastructure & Configuration

**Sub-Plan**: [`docs/flow-doc-workflow/phase1-infrastructure.md`](docs/flow-doc-workflow/phase1-infrastructure.md)

### Scope

- Extend existing logging infrastructure (not replace)
- Add documentation-specific event types to [`event_system.py`](nifi_mcp_server/workflows/core/event_system.py)
- Add `phase` field to workflow log format in [`logging_setup.py`](config/logging_setup.py)
- Add `documentation_workflow` config section to [`config.yaml`](config.yaml)

### Key Files

- `nifi_mcp_server/workflows/core/event_system.py` - New EventTypes
- `config/logging_setup.py` - Enhanced workflow format with `phase` tracking
- `config/settings.py` - New accessors for documentation_workflow config
- `logging_config.yaml` - Updated format string (if needed)

### Integration Points

- Existing `interface_logger_middleware` handles workflow logs
- Existing `workflow_filter` in `logging_setup.py` routes to `workflow.log`
- New `extra[phase]` field added to workflow log records

---

## Phase 2: Tool Enhancements

**Sub-Plan**: [`docs/flow-doc-workflow/phase2-tools.md`](docs/flow-doc-workflow/phase2-tools.md)

### Scope

- Processor categorization with expandable category registry
- **Extend `get_nifi_object_details`** to accept list of IDs (batch mode)
- Port-to-port connection traversal improvements
- Token-efficient output formatting

### Key Decisions

- **Batch Retrieval**: Extend existing `get_nifi_object_details` rather than new tool
- **Category Registry**: Externalize to config/code for easy expansion
- **Unclassified Tracking**: Return list of unclassified processor types for review

### New Functionality

```python
# Extended get_nifi_object_details signature
async def get_nifi_object_details(
    object_type: Literal[...],
    object_id: str | None = None,      # Single ID (existing)
    object_ids: List[str] | None = None,  # Batch mode (new)
    output_format: Literal["full", "summary", "doc_optimized"] = "full"
) -> Dict | List[Dict]:
```

### Processor Category Registry

```python
# In flow_documenter_improved.py or separate categories.py
PROCESSOR_CATEGORIES = {
    "IO_READ": [...],
    "IO_WRITE": [...],
    "LOGIC": [...],
    "TRANSFORM": [...],
    "VALIDATION": [...]
}
# + function to report unclassified types
```

---

## Phase 3: Node Implementation

**Sub-Plan**: [`docs/flow-doc-workflow/phase3-nodes.md`](docs/flow-doc-workflow/phase3-nodes.md)

### Scope

- 4 node classes: Initialize, Discovery, Analysis, Documentation
- LLM navigation algorithms for complex multi-PG flows
- Prompt engineering for business logic extraction
- Metrics collection per node

### Node Transition Map

```
InitializeDocNode
    │
    ▼ "default"
DiscoveryNode ◄──┐
    │            │ "continue" (pagination)
    ├────────────┘
    │
    ▼ "complete"
AnalysisNode
    │
    ▼ "default"
DocumentationNode
    │
    ▼ (end - empty successors)
```

### LLM Navigation Strategy

1. **Breadth-first discovery** - Get full component inventory first
2. **Categorization pass** - Heuristic categorization (no LLM)
3. **Connectivity analysis** - Build adjacency graph from connections
4. **Targeted deep-dive** - LLM analyzes only LOGIC/TRANSFORM processors
5. **Sub-flow inference** - LLM groups connected components by purpose

### Key Algorithms

- **Port traversal**: Follow INPUT_PORT → connections → OUTPUT_PORT chains
- **Decision point detection**: Processors with multiple outgoing relationships
- **Data lineage**: Trace paths from IO_READ to IO_WRITE processors

---

## Phase 4: Workflow Integration

**Sub-Plan**: [`docs/flow-doc-workflow/phase4-integration.md`](docs/flow-doc-workflow/phase4-integration.md)

### Scope

- Wire up AsyncFlow with proper node transitions
- Register workflow with existing registry
- App/API changes to invoke multi-node workflow
- UI considerations for progress display

### Key Changes Needed

1. **Workflow Registry** ([`registry.py`](nifi_mcp_server/workflows/registry.py))

   - Already supports async workflows via `create_async_executor()`
   - Need to ensure multi-node async flows work correctly

2. **AsyncWorkflowExecutor** ([`async_executor.py`](nifi_mcp_server/workflows/core/async_executor.py))

   - Currently passes config only to `start_node`
   - Multi-node flows need config passed to ALL nodes

3. **Workflow Definition**
```python
def create_flow_documentation_workflow() -> AsyncFlow:
    init = InitializeDocNode()
    discovery = DiscoveryNode()
    analysis = AnalysisNode()
    doc = DocumentationNode()
    
    # PocketFlow wiring (using discovered syntax)
    init >> discovery
    discovery - "continue" >> discovery  # Self-loop
    discovery - "complete" >> analysis
    analysis >> doc
    
    return AsyncFlow(start=init)
```

4. **API Endpoint** (if needed)

   - May need new endpoint or parameter to invoke documentation workflow
   - Progress events streamed via existing event system

---

## Phase 5: Testing & Refinement

**Sub-Plan**: [`docs/flow-doc-workflow/phase5-testing.md`](docs/flow-doc-workflow/phase5-testing.md)

### Scope

- **Hybrid approach**: Mocks for unit tests, real NiFi for integration
- Workflow testing infrastructure (new capability)
- Test fixtures representing various flow patterns
- Output validation automation

### Test Categories

| Type | NiFi | Purpose |

|------|------|---------|

| Unit | Mock | Test individual nodes in isolation |

| Node Integration | Mock | Test node transitions work correctly |

| Tool Integration | Real | Test batch retrieval, categorization |

| End-to-End | Real | Full workflow against test flows |

### Mock Infrastructure

```python
# tests/workflows/mocks/nifi_mock.py
class MockNiFiData:
    SMALL_FLOW = {...}      # 5 processors, simple linear
    BRANCHING_FLOW = {...}  # RouteOnAttribute with branches
    NESTED_PG_FLOW = {...}  # Multiple process groups
    LARGE_FLOW = {...}      # 100+ processors, pagination test
```

### Key Test Cases

1. `test_discovery_pagination` - Continuation token handling
2. `test_discovery_self_loop` - PocketFlow self-loop works
3. `test_categorization_accuracy` - Heuristics classify correctly
4. `test_unclassified_reporting` - Unknown types reported
5. `test_analysis_batching` - Large flows batched for LLM
6. `test_mermaid_validity` - Generated diagram parses
7. `test_no_raw_uuids` - User-facing text uses names
8. `test_port_traversal` - Connections through ports resolved

---

## Implementation Order

1. **Phase 1** (1 day) - Foundation: events, logging, config
2. **Phase 2** (2 days) - Tools: batch retrieval, categorization
3. **Phase 3** (3 days) - Nodes: core logic, prompts, algorithms
4. **Phase 4** (1 day) - Integration: wiring, registration, app changes
5. **Phase 5** (2 days) - Testing: mocks, fixtures, test cases

**Total Estimate**: 9 days

---

## Success Criteria

- [ ] Workflow completes on 100+ processor flow without timeout
- [ ] Generated Mermaid diagram renders correctly
- [ ] Business logic extracted for all LOGIC processors
- [ ] No raw UUIDs in user-facing output
- [ ] Token usage stays under configured limits per LLM call
- [ ] All phases emit proper events with metrics
- [ ] Self-loop pagination handles >1000 components
- [ ] Unclassified processor types reported for expansion

---

## Next Step

Create the 5 sub-plan markdown files with detailed implementation specifications.