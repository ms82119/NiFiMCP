"""
Workflow state tracking and execution summary.
"""

import time
from typing import Dict, Any, Optional
from loguru import logger


class WorkflowState:
    """Track execution state for a single workflow."""
    
    def __init__(self):
        self.status = "idle"  # idle, running, completed, error
        self.current_event = None
        self.total_tokens_in = 0
        self.total_tokens_out = 0
        self.tool_calls_made = 0
        self.tools_executed = 0
        self.messages_in_context = 0
        self.model = None
        self.provider = None
        self.workflow_name = None
        self.start_time = None
        self.last_update = None
        self.executed_tools = []  # List of tool names executed
    
    def update_from_event(self, event) -> None:
        """Update state based on incoming event."""
        self.last_update = time.time()
        event_type = event.event_type
        event_data = event.data
        
        logger.debug(f"Updating workflow state from event: {event_type}")
        
        if event_type == "workflow_start":
            self.status = "running"
            self.start_time = time.time()
            self.workflow_name = event_data.get("workflow_name")
            self.model = event_data.get("model")
            self.provider = event_data.get("provider")
            logger.debug(f"Workflow started: {self.workflow_name}")
        
        elif event_type == "llm_start":
            self.current_event = "LLM Processing"
            self.messages_in_context = event_data.get("message_count", 0)
            self.model = event_data.get("model", self.model)
            self.provider = event_data.get("provider", self.provider)
            logger.debug(f"LLM started with {self.messages_in_context} messages in context")
        
        elif event_type == "llm_complete":
            self.total_tokens_in += event_data.get("tokens_in", 0)
            self.total_tokens_out += event_data.get("tokens_out", 0)
            tool_calls = event_data.get("tool_calls", [])
            if isinstance(tool_calls, list):
                self.tool_calls_made += len(tool_calls)
            elif isinstance(tool_calls, int):
                self.tool_calls_made += tool_calls
            self.current_event = None
            logger.debug(f"LLM completed: {self.total_tokens_in} in, {self.total_tokens_out} out, {self.tool_calls_made} tool calls")
        
        elif event_type == "tool_start":
            tool_name = event_data.get("tool_name", "unknown")
            self.current_event = f"Executing {tool_name}"
            logger.debug(f"Tool started: {tool_name}")
        
        elif event_type == "tool_complete":
            tool_name = event_data.get("tool_name", "unknown")
            self.tools_executed += 1
            if tool_name not in self.executed_tools:
                self.executed_tools.append(tool_name)
            self.current_event = None
            logger.debug(f"Tool completed: {tool_name} (total: {self.tools_executed})")
        
        elif event_type == "workflow_complete":
            self.status = "completed"
            self.current_event = None
            logger.debug(f"Workflow completed: {self.tools_executed} tools executed")
        
        elif event_type == "workflow_error":
            self.status = "error"
            self.current_event = f"Error: {event_data.get('error', 'Unknown error')}"
            logger.error(f"Workflow error: {event_data.get('error', 'Unknown error')}")
    
    def get_summary(self) -> Dict[str, Any]:
        """Get current summary for display."""
        duration = time.time() - self.start_time if self.start_time else 0
        
        return {
            "status": self.status,
            "current_event": self.current_event,
            "total_tokens": self.total_tokens_in + self.total_tokens_out,
            "tokens_in": self.total_tokens_in,
            "tokens_out": self.total_tokens_out,
            "tool_calls": self.tool_calls_made,
            "tools_executed": self.tools_executed,
            "executed_tools": self.executed_tools.copy(),
            "messages_in_context": self.messages_in_context,
            "model": self.model,
            "provider": self.provider,
            "workflow_name": self.workflow_name,
            "duration": duration,
            "is_running": self.status == "running",
            "is_completed": self.status == "completed",
            "is_error": self.status == "error"
        }
    
    def reset(self) -> None:
        """Reset state to idle."""
        self.__init__()


class WorkflowStateManager:
    """Manage workflow states for multiple workflows."""
    
    def __init__(self):
        self.workflow_states: Dict[str, WorkflowState] = {}
    
    def get_workflow_state(self, user_request_id: str) -> WorkflowState:
        """Get or create workflow state for a user request."""
        if user_request_id not in self.workflow_states:
            self.workflow_states[user_request_id] = WorkflowState()
        return self.workflow_states[user_request_id]
    
    def update_workflow_state(self, user_request_id: str, event) -> None:
        """Update workflow state from an event."""
        state = self.get_workflow_state(user_request_id)
        state.update_from_event(event)
    
    def get_workflow_summary(self, user_request_id: str) -> Dict[str, Any]:
        """Get summary for a specific workflow."""
        state = self.workflow_states.get(user_request_id)
        if state:
            return state.get_summary()
        return {}
    
    def cleanup_completed_workflows(self, max_age_hours: int = 24) -> None:
        """Clean up old completed workflows to prevent memory leaks."""
        current_time = time.time()
        max_age_seconds = max_age_hours * 3600
        
        workflows_to_remove = []
        for user_request_id, state in self.workflow_states.items():
            if (state.status in ["completed", "error"] and 
                state.last_update and 
                current_time - state.last_update > max_age_seconds):
                workflows_to_remove.append(user_request_id)
        
        for user_request_id in workflows_to_remove:
            del self.workflow_states[user_request_id]
            logger.debug(f"Cleaned up old workflow state: {user_request_id}")
    
    def get_all_summaries(self) -> Dict[str, Dict[str, Any]]:
        """Get summaries for all workflows."""
        return {
            user_request_id: state.get_summary()
            for user_request_id, state in self.workflow_states.items()
        }
