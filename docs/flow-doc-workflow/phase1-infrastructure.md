# Phase 1: Infrastructure & Configuration

## Overview

**Goal**: Establish the foundational infrastructure for the Flow Documentation Workflow by extending existing logging, events, and configuration systems.

**Duration Estimate**: 1 day

**Key Principle**: Build on existing infrastructure, don't replace it.

**Source Files**:
- [`src/infrastructure/event_system_additions.py`](src/infrastructure/event_system_additions.py) → Apply to `nifi_mcp_server/workflows/core/event_system.py`
- [`src/infrastructure/logging_updates.py`](src/infrastructure/logging_updates.py) → Apply to `config/logging_setup.py`
- [`src/infrastructure/config_additions.py`](src/infrastructure/config_additions.py) → Apply to `config/settings.py`

---

## 1. Event System Enhancements

### 1.1 New Event Types

Add 12 new event types to `EventTypes` class:
- Phase-level: `DOC_PHASE_START`, `DOC_PHASE_COMPLETE`, `DOC_PHASE_ERROR`
- Discovery: `DOC_DISCOVERY_CHUNK`, `DOC_DISCOVERY_TIMEOUT`, `DOC_DISCOVERY_RETRY`
- Analysis: `DOC_ANALYSIS_CATEGORIZE`, `DOC_ANALYSIS_BATCH`, `DOC_ANALYSIS_SUBFLOW`
- Generation: `DOC_GENERATION_SECTION`, `DOC_GENERATION_DIAGRAM`, `DOC_GENERATION_VALIDATE`
- Progress: `DOC_PROGRESS_UPDATE`

**See**: [`src/infrastructure/event_system_additions.py`](src/infrastructure/event_system_additions.py)

### 1.2 Convenience Functions

Add three helper functions for common emission patterns:
- `emit_doc_phase_start()` - Phase start events
- `emit_doc_phase_complete()` - Phase complete with duration
- `emit_doc_progress()` - Human-readable progress updates

**See**: [`src/infrastructure/event_system_additions.py`](src/infrastructure/event_system_additions.py)

### 1.3 Event Data Schemas

Each event type has a defined schema with consistent fields:
- Phase events: `phase`, `started_at`, `duration_ms`, `metrics`
- Discovery chunk: `chunk_number`, `processors_found`, `continuation_token`, etc.
- Analysis batch: `batch_number`, `processors_in_batch`, `tokens_in/out`
- Progress: `message`, `progress_pct`, `details`

**See**: [`src/infrastructure/event_system_additions.py`](src/infrastructure/event_system_additions.py) for full schemas

---

## 2. Logging Infrastructure Enhancements

### 2.1 Enhanced Workflow Log Format

Update workflow log format to include:
- `Phase:{extra[phase]}` field
- `Event: {extra[event_type]}` line
- `Progress: {extra[progress_message]}` line

**See**: [`src/infrastructure/logging_updates.py`](src/infrastructure/logging_updates.py) - `WORKFLOW_FORMAT_UPDATE`

### 2.2 Context Patcher Updates

Add phase tracking to `context_patcher()`:
- Extract `phase` from context
- Set `record["extra"]["phase"]`

**See**: [`src/infrastructure/logging_updates.py`](src/infrastructure/logging_updates.py) - `CONTEXT_PATCHER_UPDATE`

### 2.3 Interface Logger Middleware Updates

Enhance middleware to extract phase/event data from workflow events:
- Extract `phase` from event data
- Extract `event_type` for log display
- Extract `progress_message` for user-friendly status

**See**: [`src/infrastructure/logging_updates.py`](src/infrastructure/logging_updates.py) - `INTERFACE_MIDDLEWARE_UPDATE`

### 2.4 Logger Defaults

Add new default extra fields:
- `"phase": "-"`
- `"event_type": "-"`
- `"progress_message": ""`

**See**: [`src/infrastructure/logging_updates.py`](src/infrastructure/logging_updates.py) - `LOGGER_DEFAULTS_UPDATE`

---

## 3. Configuration Additions

### 3.1 New Configuration Section

Add `documentation_workflow` section to `DEFAULT_APP_CONFIG` with nested structure:
- `discovery`: timeout, max_depth, batch_size, max_retries
- `analysis`: large_pg_threshold, max_processors_per_llm_call, max_tokens_per_analysis, virtual group settings
- `generation`: max_mermaid_nodes, summary_max_words, include_all_io
- `output`: format, include_raw_data, validate_mermaid

**See**: [`src/infrastructure/config_additions.py`](src/infrastructure/config_additions.py) - `DOC_WORKFLOW_CONFIG`

### 3.2 Configuration Accessors

Add accessor functions:
- `get_documentation_workflow_config()` - Full config
- `get_doc_discovery_config()` - Discovery settings
- `get_doc_analysis_config()` - Analysis settings
- `get_doc_generation_config()` - Generation settings
- `get_doc_output_config()` - Output settings
- Convenience accessors: `get_doc_discovery_timeout()`, `get_doc_max_processors_per_llm()`, etc.

**See**: [`src/infrastructure/config_additions.py`](src/infrastructure/config_additions.py) - `CONFIG_ACCESSORS`

### 3.3 Configuration Validation

Add `validate_documentation_workflow_config()` function that:
- Checks timeout values (warn if < 30s)
- Checks max_depth (warn if > 20)
- Checks LLM batch sizes (warn if > 50)
- Checks virtual group thresholds
- Returns list of warnings

**See**: [`src/infrastructure/config_additions.py`](src/infrastructure/config_additions.py) - `CONFIG_VALIDATION`

---

## 4. Files to Modify

| File | Changes | Source |
|------|---------|--------|
| `nifi_mcp_server/workflows/core/event_system.py` | Add 12 EventTypes, 3 helper functions | `src/infrastructure/event_system_additions.py` |
| `config/logging_setup.py` | Update format, patcher, middleware, defaults | `src/infrastructure/logging_updates.py` |
| `config/settings.py` | Add config section, accessors, validation | `src/infrastructure/config_additions.py` |

---

## 5. Testing Verification

Before moving to Phase 2, verify:

- [ ] New events emit correctly with phase data
- [ ] Workflow log shows phase field
- [ ] Config accessors return expected values
- [ ] Config validation catches invalid settings
- [ ] Existing workflow logging still works (regression check)

### Example Log Output

After implementation, workflow.log should look like:

```
2025-11-27 10:15:32.456 | INFO     | workflow | Phase:DISCOVERY   | Req:abc123 | Wf:flow_documentation | Step:discovery_node
Event: doc_discovery_chunk
Progress: Discovering flow structure... (found 47 processors in 3 groups)
{
  "phase": "DISCOVERY",
  "chunk_number": 1,
  "processors_found": 47,
  "connections_found": 52,
  ...
}
--------
```

---

## 6. Success Criteria

Phase 1 is complete when:

1. All 12 new event types are defined and documented
2. Helper functions emit events with correct schema
3. Workflow log format includes phase tracking
4. Config section exists with all settings and accessors
5. Existing unguided workflow still logs correctly
6. Manual test confirms log output matches expected format

---

## Next Phase

Proceed to [Phase 2: Tool Enhancements](./phase2-tools.md) after Phase 1 verification.
