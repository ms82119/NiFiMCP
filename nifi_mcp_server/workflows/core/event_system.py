"""
Event System for Real-Time Workflow Updates

This module provides the event emission and handling infrastructure
for real-time workflow progress updates to the UI.
"""

import time
import uuid
import asyncio
import threading
from typing import Dict, Any, List, Callable, Optional
from dataclasses import dataclass
from loguru import logger


@dataclass
class WorkflowEvent:
    """Represents a workflow event."""
    id: str
    timestamp: float
    event_type: str
    workflow_id: str
    step_id: str
    data: Dict[str, Any]
    user_request_id: Optional[str] = None


class EventEmitter:
    """Event emitter for workflow events."""
    
    def __init__(self):
        self.events: List[WorkflowEvent] = []
        self.callbacks: List[Callable[[WorkflowEvent], None]] = []
        self._lock = threading.Lock()  # Use threading.Lock for thread safety
    
    async def emit(self, event_type: str, data: Dict[str, Any], 
                   workflow_id: str, step_id: str, user_request_id: Optional[str] = None):
        """Emit a workflow event."""
        event = WorkflowEvent(
            id=str(uuid.uuid4()),
            timestamp=time.time(),
            event_type=event_type,
            workflow_id=workflow_id,
            step_id=step_id,
            data=data,
            user_request_id=user_request_id
        )
        
        with self._lock:  # Use threading.Lock for thread safety
            self.events.append(event)
        
        # Call registered callbacks
        for callback in self.callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                logger.error(f"Event callback error: {e}", exc_info=True)
        
        # Log the event
        try:
            # Serialize event data for logging
            import json
            if data:
                # Debug: Check if data contains problematic fields
                if 'direction' in data:
                    logger.warning(f"Event data contains 'direction' field: {data}")
                json_data = json.dumps(data, indent=2, default=str)
            else:
                json_data = "{}"
        except Exception as e:
            logger.error(
                f"Event data serialization failed for event_type={event_type}, "
                f"data keys={list(data.keys()) if data else 'None'}: {e}"
            )
            json_data = (
                f'{{"error": "Failed to serialize data: {e}", '
                f'"event_type": "{event_type}", '
                f'"data_keys": {list(data.keys()) if data else []}}}'
            )

        # Extract phase / progress fields for workflow.log format
        # NOTE: Many documentation events include these keys in `data`
        phase = None
        progress_message = None
        if isinstance(data, dict):
            phase = data.get("phase")
            # Prefer an explicit progress_message field if present, otherwise fall back to "message"
            progress_message = data.get("progress_message") or data.get("message")

        # Ensure we always have values for the workflow.log format
        event_type_str = str(event_type) if event_type else "-"
        phase_str = str(phase) if phase else "-"
        progress_str = str(progress_message) if progress_message else ""

        # CRITICAL: The interface_logger_middleware expects fields in extra["data"]
        # So we need to put them there, AND also bind them directly to extra as fallback
        # The middleware will extract from data first, then fall back to extra
        log_data = dict(data) if isinstance(data, dict) else {}
        log_data["event_type"] = event_type_str
        log_data["phase"] = phase_str
        log_data["progress_message"] = progress_str

        # Log to workflow.log with all required fields for the workflow format
        # Put fields in both data (for middleware) and directly in extra (as fallback)
        workflow_logger = logger.bind(
            user_request_id=user_request_id or "-",
            workflow_id=workflow_id or "-",
            step_id=step_id or "-",
            interface="workflow",        # Route to workflow.log sink
            data=log_data,               # Middleware extracts phase/event_type/progress_message from here
            # Also bind directly to extra as fallback (middleware checks both)
            event_type=event_type_str,
            phase=phase_str,
            progress_message=progress_str
        )
        
        # Use info level to ensure it's captured by workflow.log (which uses DEBUG level by default)
        workflow_logger.info(f"Workflow event: {event_type_str}")
    
    def on(self, callback: Callable[[WorkflowEvent], None]):
        """Register an event callback."""
        self.callbacks.append(callback)
    
    def remove_callback(self, callback: Callable[[WorkflowEvent], None]):
        """Remove an event callback."""
        if callback in self.callbacks:
            self.callbacks.remove(callback)
    
    async def get_events_since(self, timestamp: float) -> List[WorkflowEvent]:
        """Get events since a specific timestamp."""
        with self._lock:  # Use threading.Lock for thread safety
            return [event for event in self.events if event.timestamp > timestamp]
    
    async def get_events_for_workflow(self, workflow_id: str) -> List[WorkflowEvent]:
        """Get all events for a specific workflow."""
        with self._lock:  # Use threading.Lock for thread safety
            return [event for event in self.events if event.workflow_id == workflow_id]
    
    async def clear_old_events(self, max_age_seconds: int = 3600):
        """Clear events older than max_age_seconds."""
        cutoff_time = time.time() - max_age_seconds
        with self._lock:  # Use threading.Lock for thread safety
            self.events = [event for event in self.events if event.timestamp > cutoff_time]


# Global event emitter instance
_global_event_emitter: Optional[EventEmitter] = None


def get_event_emitter() -> EventEmitter:
    """Get the global event emitter instance."""
    global _global_event_emitter
    if _global_event_emitter is None:
        _global_event_emitter = EventEmitter()
    return _global_event_emitter


# Event type constants
class EventTypes:
    """Constants for workflow event types."""
    WORKFLOW_START = "workflow_start"
    WORKFLOW_COMPLETE = "workflow_complete"
    WORKFLOW_ERROR = "workflow_error"
    
    STEP_START = "step_start"
    STEP_COMPLETE = "step_complete"
    STEP_ERROR = "step_error"
    
    LLM_START = "llm_start"
    LLM_COMPLETE = "llm_complete"
    LLM_ERROR = "llm_error"
    
    TOOL_START = "tool_start"
    TOOL_COMPLETE = "tool_complete"
    TOOL_ERROR = "tool_error"
    
    MESSAGE_ADDED = "message_added"
    PROGRESS_UPDATE = "progress_update"
    
    # Status report events
    STATUS_REPORT_START = "status_report_start"
    STATUS_REPORT_COMPLETE = "status_report_complete"
    STATUS_REPORT_ERROR = "status_report_error"
    
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


# Convenience functions for common event types
async def emit_workflow_start(workflow_id: str, step_id: str, data: Dict[str, Any], 
                            user_request_id: Optional[str] = None):
    """Emit a workflow start event."""
    emitter = get_event_emitter()
    await emitter.emit(EventTypes.WORKFLOW_START, data, workflow_id, step_id, user_request_id)


async def emit_workflow_complete(workflow_id: str, step_id: str, data: Dict[str, Any], 
                                user_request_id: Optional[str] = None):
    """Emit a workflow complete event."""
    emitter = get_event_emitter()
    await emitter.emit(EventTypes.WORKFLOW_COMPLETE, data, workflow_id, step_id, user_request_id)


async def emit_workflow_error(workflow_id: str, step_id: str, data: Dict[str, Any], 
                              user_request_id: Optional[str] = None):
    """Emit a workflow error event."""
    emitter = get_event_emitter()
    await emitter.emit(EventTypes.WORKFLOW_ERROR, data, workflow_id, step_id, user_request_id)


async def emit_llm_start(workflow_id: str, step_id: str, data: Dict[str, Any], 
                       user_request_id: Optional[str] = None):
    """Emit an LLM start event."""
    emitter = get_event_emitter()
    await emitter.emit(EventTypes.LLM_START, data, workflow_id, step_id, user_request_id)


async def emit_llm_complete(workflow_id: str, step_id: str, data: Dict[str, Any], 
                          user_request_id: Optional[str] = None):
    """Emit an LLM complete event."""
    emitter = get_event_emitter()
    await emitter.emit(EventTypes.LLM_COMPLETE, data, workflow_id, step_id, user_request_id)


async def emit_tool_start(workflow_id: str, step_id: str, data: Dict[str, Any], 
                        user_request_id: Optional[str] = None):
    """Emit a tool start event."""
    emitter = get_event_emitter()
    await emitter.emit(EventTypes.TOOL_START, data, workflow_id, step_id, user_request_id)


async def emit_tool_complete(workflow_id: str, step_id: str, data: Dict[str, Any], 
                           user_request_id: Optional[str] = None):
    """Emit a tool complete event."""
    emitter = get_event_emitter()
    await emitter.emit(EventTypes.TOOL_COMPLETE, data, workflow_id, step_id, user_request_id)


async def emit_message_added(workflow_id: str, step_id: str, data: Dict[str, Any], 
                           user_request_id: Optional[str] = None):
    """Emit a message added event."""
    emitter = get_event_emitter()
    await emitter.emit(EventTypes.MESSAGE_ADDED, data, workflow_id, step_id, user_request_id)


# === Documentation Workflow Convenience Functions ===

async def emit_doc_phase_start(
    workflow_id: str,
    step_id: str,
    phase: str,
    progress_message: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    user_request_id: Optional[str] = None,
):
    """
    Emit a documentation phase start event.

    Args:
        workflow_id: Workflow identifier
        step_id: Current step / node name
        phase: Phase name (e.g. INIT, DISCOVERY, ANALYSIS, GENERATION)
        progress_message: Human-readable message for workflow.log
        details: Optional extra details blob (e.g. shared_state snapshot)
        user_request_id: Optional user request ID for correlation
    """
    emitter = get_event_emitter()
    await emitter.emit(
        EventTypes.DOC_PHASE_START,
        {
            "phase": phase,
            "started_at": time.time(),
            "progress_message": progress_message,
            "details": details or {},
        },
        workflow_id,
        step_id,
        user_request_id,
    )


async def emit_doc_phase_complete(
    workflow_id: str,
    step_id: str,
    phase: str,
    started_at: float,
    metrics: Dict[str, Any],
    progress_message: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    user_request_id: Optional[str] = None,
):
    """
    Emit a documentation phase complete event with duration and metrics.

    Args:
        workflow_id: Workflow identifier
        step_id: Current step / node name
        phase: Phase name
        started_at: Phase start timestamp
        metrics: Metrics dict (counts, durations, etc.)
        progress_message: Human-readable summary for workflow.log
        details: Optional extra details blob (e.g. shared_state snapshot)
        user_request_id: Optional user request ID for correlation
    """
    emitter = get_event_emitter()
    await emitter.emit(
        EventTypes.DOC_PHASE_COMPLETE,
        {
            "phase": phase,
            "started_at": started_at,
            "duration_ms": int((time.time() - started_at) * 1000),
            "metrics": metrics,
            "progress_message": progress_message,
            "details": details or {},
        },
        workflow_id,
        step_id,
        user_request_id,
    )


async def emit_doc_progress(
    workflow_id: str,
    step_id: str,
    phase: str,
    message: str,
    progress_pct: Optional[int] = None,
    details: Optional[Dict] = None,
    user_request_id: Optional[str] = None,
):
    """Emit a human-readable progress update."""
    emitter = get_event_emitter()
    await emitter.emit(
        EventTypes.DOC_PROGRESS_UPDATE,
        {
            "phase": phase,
            # Keep "message" for backward compatibility with UI mapping
            "message": message,
            # Also expose as progress_message for workflow.log formatting
            "progress_message": message,
            "progress_pct": progress_pct,
            "details": details or {},
        },
        workflow_id,
        step_id,
        user_request_id,
    )


 