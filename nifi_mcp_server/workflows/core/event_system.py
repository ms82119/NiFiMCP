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
            logger.error(f"Event data serialization failed for event_type={event_type}, data keys={list(data.keys()) if data else 'None'}: {e}")
            json_data = f'{{"error": "Failed to serialize data: {e}", "event_type": "{event_type}", "data_keys": {list(data.keys()) if data else []}}}'
        
        logger.bind(
            user_request_id=user_request_id,
            workflow_id=workflow_id,
            step_id=step_id,
            interface="workflow",  # Add interface field for workflow logging
            direction="event",     # Ensure 'direction' exists for log formatter
            json_data=json_data  # Add serialized data for workflow format
        ).info(f"Workflow event emitted: {event_type}")
    
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


# Convenience functions for common event types
async def emit_workflow_start(workflow_id: str, step_id: str, data: Dict[str, Any], 
                            user_request_id: Optional[str] = None):
    """Emit a workflow start event."""
    emitter = get_event_emitter()
    await emitter.emit(EventTypes.WORKFLOW_START, data, workflow_id, step_id, user_request_id)


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