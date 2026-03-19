# Phase 4: Workflow Integration

## Overview

**Goal**: Wire up the multi-node documentation workflow with the existing system, ensuring proper registration, execution, event handling, and UI integration.

**Duration Estimate**: 1 day

**Key Challenges**:
1. The existing `unguided` workflow is a **single-node** workflow
2. The documentation workflow is **multi-node** with self-loops
3. Config must propagate to **all nodes**, not just the start node
4. New event types need to be handled by the **EventBridge**
5. May need new API endpoint or workflow selection mechanism

---

## 1. Current Architecture Analysis

### Existing Workflow Flow

```
chat_api.py → WorkflowRegistry.create_async_executor() → AsyncWorkflowExecutor
                                                              │
                                                              ▼
                                                      PocketFlow AsyncFlow.run_async()
                                                              │
                                                              ▼
                                                      Node execution → EventEmitter
                                                              │
                                                              ▼
                                                      EventBridge → WebSocket → UI
```

### Key Components

| Component | File | Role |
|-----------|------|------|
| `WorkflowRegistry` | `registry.py` | Registers workflows, creates executors |
| `AsyncWorkflowExecutor` | `async_executor.py` | Executes async workflows |
| `chat_api.py` | `api/chat_api.py` | API endpoint for chat/workflow execution |
| `EventBridge` | `api/event_bridge.py` | Maps workflow events to WebSocket messages |
| `ConnectionManager` | `api/websocket_manager.py` | Manages WebSocket connections |

### Current Unguided Workflow Pattern

```python
# Single node workflow - all logic in one node
class AsyncInitializeExecutionNode(AsyncNiFiWorkflowNode):
    def __init__(self):
        self.successors = {}  # No successors - terminal node
    
def create_unguided_workflow() -> AsyncFlow:
    node = AsyncInitializeExecutionNode()
    return AsyncFlow(start=node)
```

### New Documentation Workflow Pattern

```python
# Multi-node workflow with transitions
def create_flow_documentation_workflow() -> AsyncFlow:
    init = InitializeDocNode()
    discovery = DiscoveryNode()
    analysis = HierarchicalAnalysisNode()
    doc = DocumentationNode()
    
    # PocketFlow wiring
    init >> discovery
    discovery - "continue" >> discovery  # Self-loop
    discovery - "complete" >> analysis
    analysis - "next_level" >> analysis  # Self-loop
    analysis - "complete" >> doc
    # doc has empty successors (terminal)
    
    return AsyncFlow(start=init)
```

---

## 2. Workflow Definition

**Source File**: [`src/integration/workflow_definition.py`](src/integration/workflow_definition.py)

### Create New Workflow Definition

```python
"""
Flow Documentation Workflow Definition

Copy to: nifi_mcp_server/workflows/definitions/flow_documentation.py
"""

from pocketflow import AsyncFlow

from ..nodes.doc_nodes import (
    InitializeDocNode,
    DiscoveryNode,
    HierarchicalAnalysisNode,
    DocumentationNode
)
from ..registry import WorkflowDefinition, register_workflow


def create_flow_documentation_workflow() -> AsyncFlow:
    """Create the multi-node flow documentation workflow."""
    # Create nodes
    init = InitializeDocNode()
    discovery = DiscoveryNode()
    analysis = HierarchicalAnalysisNode()
    doc = DocumentationNode()
    
    # Wire up transitions using PocketFlow syntax
    init >> discovery  # default action
    
    discovery - "continue" >> discovery  # Self-loop for pagination
    discovery - "complete" >> analysis
    
    analysis - "next_level" >> analysis  # Self-loop for hierarchical processing
    analysis - "complete" >> doc
    
    # doc has no successors (terminal node)
    
    return AsyncFlow(start=init)


# Register the workflow
flow_doc_definition = WorkflowDefinition(
    name="flow_documentation",
    description="Generate comprehensive documentation for NiFi flows using hierarchical analysis",
    create_workflow_func=create_flow_documentation_workflow,
    display_name="Flow Documentation",
    category="Analysis",
    phases=["Review"],
    enabled=True,
    is_async=True
)

register_workflow(flow_doc_definition)
```

### Update `__init__.py`

```python
# In nifi_mcp_server/workflows/__init__.py
from .definitions import unguided
from .definitions import flow_documentation  # Add this line
```

---

## 3. AsyncWorkflowExecutor Changes

**Issue**: The current executor passes config only to the start node or nodes directly accessible. For multi-node workflows, we need to ensure all nodes receive config.

**Source File**: [`src/integration/executor_updates.py`](src/integration/executor_updates.py)

### Current Limitation

```python
# Current set_config() in async_executor.py
def set_config(self, config_dict: Dict[str, Any]):
    if self.is_async_workflow:
        if hasattr(self.workflow, 'start_node') and hasattr(self.workflow.start_node, 'set_config'):
            self.workflow.start_node.set_config(config_dict)
        # ... only start node gets config
```

### Proposed Fix: Traverse All Wired Nodes

```python
def set_config(self, config_dict: Dict[str, Any]):
    """Set config dictionary to ALL nodes in the workflow."""
    self._config_dict = config_dict
    
    if self.is_async_workflow:
        # Collect all nodes by traversing the graph
        all_nodes = self._collect_all_nodes()
        
        for node in all_nodes:
            if hasattr(node, 'set_config'):
                node.set_config(config_dict)
                self.bound_logger.debug(f"Config passed to node: {node.name}")
    
    self.bound_logger.info(f"Config distributed to {len(all_nodes)} nodes")

def _collect_all_nodes(self) -> List:
    """Traverse workflow to collect all nodes (handles self-loops)."""
    visited = set()
    nodes = []
    
    def traverse(node):
        if node is None or id(node) in visited:
            return
        visited.add(id(node))
        nodes.append(node)
        
        # Traverse successors
        if hasattr(node, 'successors'):
            for action, successor in node.successors.items():
                traverse(successor)
    
    if hasattr(self.workflow, 'start_node'):
        traverse(self.workflow.start_node)
    
    return nodes
```

### Alternative: Pass Config via Shared State

The current implementation already does this as a backup:

```python
# In execute_async()
if self._config_dict:
    shared_state["_config_dict"] = self._config_dict
```

Each node can access config from shared state:

```python
# In node.prep_async()
config = shared.get("_config_dict", {})
```

**Recommendation**: Use both approaches for robustness.

---

## 4. API Changes

**Source File**: [`src/integration/api_updates.py`](src/integration/api_updates.py)

### Option A: Extend Existing Chat API

Modify `chat_api.py` to support workflow selection:

```python
class ChatMessage(BaseModel):
    content: str
    objective: Optional[str] = None
    provider: Optional[str] = "openai"
    model_name: Optional[str] = "gpt-4o-mini"
    selected_nifi_server_id: Optional[str] = None
    auto_prune_history: bool = True
    max_tokens_limit: int = 32000
    max_loop_iterations: int = 10
    
    # NEW: Workflow selection
    workflow_name: Optional[str] = None  # "unguided" | "flow_documentation"
    
    # NEW: Documentation-specific options
    process_group_id: Optional[str] = None  # For flow_documentation workflow
```

```python
async def execute_workflow(..., workflow_name: str = "unguided"):
    """Execute workflow using existing PocketFlow workflow system."""
    
    # Select workflow based on parameter
    workflow_def = workflow_registry.get_workflow(workflow_name or "unguided")
    if not workflow_def:
        raise Exception(f"Workflow '{workflow_name}' not found in registry")
    
    # ... rest of execution
```

### Option B: Dedicated Documentation Endpoint

Create a new endpoint specifically for documentation:

```python
@router.post("/document-flow", response_model=ChatResponse)
async def document_flow(
    process_group_id: str,
    provider: Optional[str] = "openai",
    model_name: Optional[str] = "gpt-4",
    nifi_server_id: Optional[str] = None
):
    """Generate documentation for a NiFi flow."""
    request_id = str(uuid.uuid4())
    
    shared_state = {
        "user_request_id": request_id,
        "process_group_id": process_group_id,
        "provider": provider,
        "model_name": model_name,
        "selected_nifi_server_id": nifi_server_id,
        "workflow_name": "flow_documentation"
    }
    
    workflow_executor = workflow_registry.create_async_executor("flow_documentation")
    result = await workflow_executor.execute_async(initial_context=shared_state)
    
    return ChatResponse(
        status="completed",
        request_id=request_id,
        message="Flow documentation generated",
        result=result.get("shared_state", {}).get("final_document")
    )
```

**Recommendation**: Start with Option A (extend existing) for consistency, then add Option B later for direct API access.

---

## 5. EventBridge Updates

**Source File**: [`src/integration/event_bridge_updates.py`](src/integration/event_bridge_updates.py)

The EventBridge needs to handle new documentation-specific event types:

### Add Event Mappings

```python
# In event_bridge.py - _map_event_to_websocket()

# Documentation Phase Events
elif event.event_type == EventTypes.DOC_PHASE_START:
    phase = event_data.get("phase", "Unknown")
    return {
        "type": "workflow_status",
        "request_id": user_request_id,
        "status": "processing",
        "message": f"📄 Documentation phase: {phase}",
        "data": {
            "phase": phase,
            "workflow": "flow_documentation"
        },
        "timestamp": str(event.id)
    }

elif event.event_type == EventTypes.DOC_PHASE_COMPLETE:
    phase = event_data.get("phase", "Unknown")
    metrics = event_data.get("metrics", {})
    progress_message = event_data.get("progress_message", "")
    return {
        "type": "workflow_status",
        "request_id": user_request_id,
        "status": "processing",
        "message": f"✅ {phase} complete: {progress_message}",
        "data": {
            "phase": phase,
            "metrics": metrics,
            "progress_message": progress_message
        },
        "timestamp": str(event.id)
    }

# Discovery Events
elif event.event_type == EventTypes.DOC_DISCOVERY_CHUNK:
    metrics = event_data.get("metrics", {})
    progress = event_data.get("progress_message", "")
    return {
        "type": "workflow_status",
        "request_id": user_request_id,
        "status": "processing",
        "message": f"🔍 Discovery: {progress}",
        "data": metrics,
        "timestamp": str(event.id)
    }

# Analysis Events
elif event.event_type == EventTypes.DOC_ANALYSIS_BATCH:
    pg_name = event_data.get("metrics", {}).get("pg_name", "")
    depth = event_data.get("metrics", {}).get("depth", 0)
    return {
        "type": "workflow_status",
        "request_id": user_request_id,
        "status": "processing",
        "message": f"🧠 Analyzing: {pg_name} (depth {depth})",
        "data": event_data.get("metrics", {}),
        "timestamp": str(event.id)
    }

# Generation Events
elif event.event_type == EventTypes.DOC_GENERATION_SECTION:
    section = event_data.get("metrics", {}).get("section", "")
    return {
        "type": "workflow_status",
        "request_id": user_request_id,
        "status": "processing",
        "message": f"📝 Generating: {section}",
        "data": {"section": section},
        "timestamp": str(event.id)
    }

# Progress Updates
elif event.event_type == EventTypes.DOC_PROGRESS_UPDATE:
    progress = event_data.get("progress_message", "")
    return {
        "type": "workflow_status",
        "request_id": user_request_id,
        "status": "processing",
        "message": progress,
        "data": event_data,
        "timestamp": str(event.id)
    }
```

### Documentation Workflow Completion

```python
elif event.event_type == EventTypes.WORKFLOW_COMPLETE:
    # Check if this is a documentation workflow
    workflow_name = event_data.get("workflow_name", "unguided")
    
    if workflow_name == "flow_documentation":
        # Documentation-specific completion
        return {
            "type": "workflow_complete",
            "request_id": user_request_id,
            "result": {
                "content": event_data.get("final_document", ""),
                "document_type": "markdown",
                "metrics": event_data.get("metrics", {}),
                "metadata": {
                    "workflow": "flow_documentation",
                    "pgs_documented": event_data.get("pgs_documented", 0),
                    "total_duration_ms": event_data.get("total_duration_ms", 0),
                    "validation_issues": event_data.get("validation_issues", [])
                }
            },
            "timestamp": str(event.id)
        }
    else:
        # Existing unguided completion logic
        ...
```

---

## 6. Configuration Updates

**Source File**: [`src/integration/config_updates.py`](src/integration/config_updates.py)

### Add to `config.yaml`

```yaml
workflows:
  execution_mode: 'unguided'
  default_action_limit: 10
  retry_attempts: 3
  enabled_workflows:
    - 'unguided'
    - 'flow_documentation'  # Add this
  
  # NEW: Documentation workflow configuration
  documentation_workflow:
    discovery:
      timeout_seconds: 120
      max_depth: 10
      batch_size: 50
    analysis:
      large_pg_threshold: 25
      max_tokens_per_analysis: 8000
      min_virtual_groups: 3
      max_virtual_groups: 7
    generation:
      max_mermaid_nodes: 50
      summary_max_words: 500
      include_all_io: true
```

### Update `settings.py`

```python
# Add to DEFAULT_APP_CONFIG
DEFAULT_APP_CONFIG = {
    # ... existing ...
    'workflows': {
        'execution_mode': 'unguided',
        'default_action_limit': 10,
        'retry_attempts': 3,
        'enabled_workflows': ['unguided', 'flow_documentation'],
        'documentation_workflow': {
            'discovery': {
                'timeout_seconds': 120,
                'max_depth': 10,
                'batch_size': 50
            },
            'analysis': {
                'large_pg_threshold': 25,
                'max_tokens_per_analysis': 8000,
                'min_virtual_groups': 3,
                'max_virtual_groups': 7
            },
            'generation': {
                'max_mermaid_nodes': 50,
                'summary_max_words': 500,
                'include_all_io': True
            }
        }
    }
}

# Add accessor function
def get_documentation_workflow_config() -> dict:
    """Get documentation workflow configuration."""
    app_config = get_app_config()
    return app_config.get('workflows', {}).get('documentation_workflow', {})
```

---

## 7. UI Considerations

### Progress Display

The documentation workflow has distinct phases that should be clearly visible:

```
📄 Phase: INIT → Setting up workflow state
   ↓
🔍 Phase: DISCOVERY → Finding 15 processors in 3 groups...
   ↓
🧠 Phase: ANALYSIS → Analyzing "Main Pipeline" (depth 2)
   ↓
📝 Phase: GENERATION → Generating Executive Summary...
   ↓
✅ Complete → Documentation ready (12.5KB, 4 PGs documented)
```

### WebSocket Message Types for UI

The UI should handle these message types:

| `type` | When | UI Action |
|--------|------|-----------|
| `workflow_status` | During execution | Show progress bar, phase indicator |
| `workflow_complete` | Finished | Display final Markdown document |
| `workflow_error` | On failure | Show error with recovery options |

### Document Rendering

The final `result.content` will be Markdown containing:
- Executive Summary
- Mermaid diagram (render with library)
- Hierarchical sections
- IO endpoints table
- Error handling table

**UI Requirement**: Render Markdown and Mermaid diagrams properly.

---

## 8. Files to Create/Modify

### New Files

| File | Purpose |
|------|---------|
| `nifi_mcp_server/workflows/definitions/flow_documentation.py` | Workflow definition and wiring |
| `nifi_mcp_server/workflows/nodes/doc_nodes.py` | Node implementations (from Phase 3) |
| `nifi_mcp_server/workflows/nodes/component_formatter.py` | Component formatting utilities |
| `nifi_mcp_server/workflows/prompts/documentation.py` | LLM prompts (from Phase 3) |

### Modified Files

| File | Changes |
|------|---------|
| `nifi_mcp_server/workflows/__init__.py` | Import `flow_documentation` |
| `nifi_mcp_server/workflows/core/async_executor.py` | Config propagation to all nodes |
| `nifi_mcp_server/workflows/core/event_system.py` | New EventTypes (from Phase 1) |
| `api/event_bridge.py` | Handle documentation events |
| `api/chat_api.py` | Workflow selection parameter |
| `api/models.py` | Updated ChatMessage model |
| `config/settings.py` | Documentation workflow config accessor |
| `config.yaml` | Documentation workflow settings |

---

## 9. Integration Testing Checklist

Before moving to Phase 5 (full testing), verify:

- [ ] Workflow registration succeeds (`get_workflow_registry().list_workflows()`)
- [ ] Workflow creation doesn't error (`create_flow_documentation_workflow()`)
- [ ] Node wiring is correct (all transitions resolve)
- [ ] Config propagates to all 4 nodes
- [ ] Events emit correctly for each phase
- [ ] EventBridge maps documentation events to WebSocket
- [ ] API can invoke documentation workflow
- [ ] Basic execution completes without errors

### Quick Smoke Test

```python
# In Python REPL or test script
from nifi_mcp_server.workflows.registry import get_workflow_registry

registry = get_workflow_registry()

# Check registration
workflow = registry.get_workflow("flow_documentation")
assert workflow is not None, "Workflow not registered"
assert workflow.is_async, "Should be async workflow"

# Check workflow creation
from nifi_mcp_server.workflows.definitions.flow_documentation import create_flow_documentation_workflow
flow = create_flow_documentation_workflow()
assert flow.start_node is not None, "No start node"
assert flow.start_node.name == "initialize_doc", "Wrong start node"

print("✅ Basic integration checks passed")
```

---

## 10. Migration Path

### Step 1: Add Infrastructure (Phase 1)
- Event types already added
- Logging enhancements in place

### Step 2: Add Tools (Phase 2)
- Batch retrieval ready
- Categorization ready

### Step 3: Add Nodes (Phase 3)
- Copy node files to workflows/nodes/
- Copy prompts to workflows/prompts/

### Step 4: Wire Up (This Phase)

1. **Create workflow definition**:
   ```bash
   cp docs/flow-doc-workflow/src/integration/workflow_definition.py \
      nifi_mcp_server/workflows/definitions/flow_documentation.py
   ```

2. **Update imports**:
   ```python
   # In __init__.py
   from .definitions import flow_documentation
   ```

3. **Update AsyncWorkflowExecutor**:
   Apply config propagation changes

4. **Update EventBridge**:
   Add documentation event handlers

5. **Update API**:
   Add workflow_name parameter

6. **Update config.yaml**:
   Add documentation_workflow section

7. **Test**:
   Run smoke test, verify no errors

---

## 11. Rollback Plan

If issues arise:

1. **Remove workflow registration**:
   - Comment out `from .definitions import flow_documentation` in `__init__.py`

2. **Keep existing behavior**:
   - Unguided workflow continues to work unchanged
   - No breaking changes to existing API

3. **Gradual enablement**:
   - Use `enabled_workflows` config to control availability
   - Can disable `flow_documentation` without code changes

---

## Next Steps

After completing Phase 4 integration:

1. Run smoke tests to verify basic wiring
2. Proceed to Phase 5 for comprehensive testing
3. Iterate on prompts based on output quality

---

## 12. Future Enhancements (Not for MVP)

### Context Persistence for Follow-up Questions

**Current MVP Behavior**:
- Documentation workflow executes autonomously, no user input during run
- `shared` dict contains rich analysis data (pg_tree, pg_summaries, io_endpoints, etc.)
- Only `final_document` (markdown) is saved as a message
- After completion, analysis context is lost (in-memory only)
- Follow-up questions would only see the markdown in chat history

**Gap Identified**:
If user asks "Tell me more about the GetFile processor" after documentation completes:
- LLM sees markdown document in history
- LLM does NOT have access to raw analysis data
- Would need to make new NiFi API calls (slow, loses context)

**Potential Future Solutions**:

| Option | Description | Complexity |
|--------|-------------|------------|
| **Condensed Context Message** | Save key facts (PG list, IO summary, error summary) as a message alongside the document | Low |
| **Artifact Storage** | New DB table for workflow artifacts, load on follow-up requests | Medium |
| **Documentation Q&A Mode** | Specialized follow-up workflow that loads cached analysis | High |

**Recommendation for Future**: Start with "condensed context message" approach - extract key facts from `shared` dict and save as a structured message that LLM can reference.

### Auto Workflow Selection

**Future Vision**: An "Auto" workflow that analyzes user intent and routes to the most appropriate workflow:

```
User: "Document the transaction flow"
         │
         ▼
   ┌─────────────┐
   │ Auto Router │ → Detects documentation request
   └─────────────┘
         │
         ▼
   flow_documentation workflow
```

```
User: "Why is my processor failing?"
         │
         ▼
   ┌─────────────┐
   │ Auto Router │ → Detects troubleshooting request
   └─────────────┘
         │
         ▼
   unguided workflow (or future troubleshooting workflow)
```

**Implementation Ideas**:
- Simple keyword/pattern matching for obvious cases
- LLM-based intent classification for ambiguous requests
- User can always override by selecting workflow manually

**Note**: UI already has workflow selection dropdown - Auto would just be another option.

### Differences: Documentation vs Unguided

| Aspect | Unguided | Documentation |
|--------|----------|---------------|
| User interaction during run | LLM iterates, user waits | User waits |
| State persistence | Messages saved to DB | Only final doc saved |
| Follow-up capability | Full history available | Limited (just markdown) |
| Objective handling | Sent with every prompt | N/A (single purpose) |
| Purpose | General NiFi assistance | Single deliverable |

The documentation workflow is a **task execution** pattern rather than a **conversation** pattern. Enabling conversational follow-ups would require context persistence (future enhancement).

---

## Source Files

Create these files to implement the integration:

| Source | Target |
|--------|--------|
| [`src/integration/workflow_definition.py`](src/integration/workflow_definition.py) | `nifi_mcp_server/workflows/definitions/flow_documentation.py` |
| [`src/integration/executor_updates.py`](src/integration/executor_updates.py) | Updates to `async_executor.py` |
| [`src/integration/event_bridge_updates.py`](src/integration/event_bridge_updates.py) | Updates to `event_bridge.py` |
| [`src/integration/api_updates.py`](src/integration/api_updates.py) | Updates to `chat_api.py` |
| [`src/integration/config_updates.py`](src/integration/config_updates.py) | Updates to `settings.py` |

