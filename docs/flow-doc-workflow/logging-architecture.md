# Workflow Logging Architecture

## Overview

The workflow logging system routes events to `workflow.log` with rich metadata including phases, event types, progress messages, and shared state snapshots.

## Logging Paths

### 1. Event System Events (Documentation Workflow)

**Location**: `nifi_mcp_server/workflows/core/event_system.py`

**Flow**:
1. Nodes call `emit_doc_phase_event()` → 
2. Which calls `emit_doc_phase_start/complete/progress()` → 
3. Which calls `EventEmitter.emit()` → 
4. Which logs via `logger.bind(interface="workflow", data={...})`

**Key Fields**:
- `event_type`: Event type (e.g., `doc_phase_complete`)
- `phase`: Phase name (INIT, DISCOVERY, ANALYSIS, GENERATION)
- `progress_message`: Human-readable progress text
- `data`: Full event payload with metrics and details

### 2. Node Execution Logging (Async Nodes)

**Location**: `nifi_mcp_server/workflows/nodes/async_nifi_node.py`

**Flow**:
1. `prep_async()` / `post_async()` called → 
2. Creates shared state snapshot → 
3. Logs via `workflow_logger.bind(interface="workflow", data={...})`

**Key Fields**:
- `node_name`: Node identifier
- `action`: "prep" or "post"
- `shared_state_snapshot`: Snapshot of shared state (keys, sizes, sample values)

## Middleware Processing

**Location**: `config/logging_setup.py` → `interface_logger_middleware()`

**What it does**:
1. Extracts `phase`, `event_type`, `progress_message` from `extra["data"]` (or falls back to `extra`)
2. Serializes `data` to JSON for `json_data` field
3. Formats message for workflow interface

**Critical**: The middleware expects fields in `extra["data"]` first, then falls back to `extra` directly.

## Workflow Log Format

```
{time} | {level} | {interface} | Phase:{phase} | Req:{user_request_id} | Wf:{workflow_id} | Step:{step_id}
Event: {event_type}
Progress: {progress_message}
{json_data}
--------
```

## Shared State Snapshots

The `_create_shared_state_snapshot()` helper creates:
- `keys`: List of all keys in shared state
- `sizes`: Size of important keys (pg_tree, pg_summaries, doc_sections, final_document, etc.)
- `sample_values`: Truncated values of key fields (current_phase, workflow_name, etc.)

## Debugging Tips

1. **Events not appearing**: Check that `interface="workflow"` is set in logger.bind()
2. **Empty Event/Progress**: Verify fields are in `extra["data"]` OR directly in `extra`
3. **No shared state**: Ensure `prep_async()` / `post_async()` are calling the snapshot helper
4. **Duplication**: server.log and mcp_debug.log both log MCP operations (by design)

## Recent Fixes (2025-11-29)

1. **Event System**: Now puts fields in both `extra["data"]` (for middleware) and `extra` (as fallback)
2. **Middleware**: Updated to check both `data` dict and direct `extra` fields
3. **Shared State**: Added snapshot tracking to async node prep/post methods
4. **Node Logging**: Async nodes now log shared state changes at prep/post boundaries

