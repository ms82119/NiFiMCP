"""
Event system additions for documentation workflow.

Add these additions to: nifi_mcp_server/workflows/core/event_system.py

1. Add new EventTypes constants to EventTypes class
2. Add convenience functions at module level
"""

import time
from typing import Dict, Any, Optional
from .event_system import get_event_emitter, EventTypes


# =============================================================================
# NEW EVENT TYPES (add to EventTypes class)
# =============================================================================

DOC_EVENT_TYPES = """
# === Documentation Workflow Events ===

# Phase-level events (high-level workflow progress)
DOC_PHASE_START = "doc_phase_start"
DOC_PHASE_COMPLETE = "doc_phase_complete"
DOC_PHASE_ERROR = "doc_phase_error"

# Discovery phase events
DOC_DISCOVERY_CHUNK = "doc_discovery_chunk"      # Each pagination chunk processed
DOC_DISCOVERY_TIMEOUT = "doc_discovery_timeout"  # Timeout with continuation token
DOC_DISCOVERY_RETRY = "doc_discovery_retry"      # Retry after failure

# Analysis phase events  
DOC_ANALYSIS_CATEGORIZE = "doc_analysis_categorize"  # Heuristic categorization complete
DOC_ANALYSIS_BATCH = "doc_analysis_batch"            # Each LLM analysis batch
DOC_ANALYSIS_SUBFLOW = "doc_analysis_subflow"        # Sub-flow identification

# Generation phase events
DOC_GENERATION_SECTION = "doc_generation_section"    # Each doc section generated
DOC_GENERATION_DIAGRAM = "doc_generation_diagram"    # Mermaid diagram generated
DOC_GENERATION_VALIDATE = "doc_generation_validate"  # Output validation results

# Progress events (user-friendly status updates)
DOC_PROGRESS_UPDATE = "doc_progress_update"
"""


# =============================================================================
# CONVENIENCE FUNCTIONS (add to event_system.py module)
# =============================================================================

async def emit_doc_phase_start(
    workflow_id: str, 
    step_id: str, 
    phase: str,
    user_request_id: Optional[str] = None
):
    """Emit a documentation phase start event."""
    emitter = get_event_emitter()
    await emitter.emit(EventTypes.DOC_PHASE_START, {
        "phase": phase,
        "started_at": time.time()
    }, workflow_id, step_id, user_request_id)


async def emit_doc_phase_complete(
    workflow_id: str,
    step_id: str,
    phase: str,
    started_at: float,
    metrics: Dict[str, Any],
    user_request_id: Optional[str] = None
):
    """Emit a documentation phase complete event with duration."""
    emitter = get_event_emitter()
    await emitter.emit(EventTypes.DOC_PHASE_COMPLETE, {
        "phase": phase,
        "started_at": started_at,
        "duration_ms": int((time.time() - started_at) * 1000),
        "metrics": metrics
    }, workflow_id, step_id, user_request_id)


async def emit_doc_progress(
    workflow_id: str,
    step_id: str,
    phase: str,
    message: str,
    progress_pct: Optional[int] = None,
    details: Optional[Dict] = None,
    user_request_id: Optional[str] = None
):
    """Emit a human-readable progress update."""
    emitter = get_event_emitter()
    await emitter.emit(EventTypes.DOC_PROGRESS_UPDATE, {
        "phase": phase,
        "message": message,
        "progress_pct": progress_pct,
        "details": details
    }, workflow_id, step_id, user_request_id)

