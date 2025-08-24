"""
Unified event handling for workflow events and message management.
"""

import streamlit as st
import uuid
from typing import Dict, Any, List
from loguru import logger

from models.message import MessageData, EventParser
from workflow_state import WorkflowStateManager


class EventHandler:
    """Unified handler for all workflow events and message management."""
    
    def __init__(self):
        self.session_state = st.session_state
        self.workflow_states = WorkflowStateManager()
        self.parser = EventParser()
    
    def handle_event(self, event) -> MessageData:
        """Main entry point for all events - parse, display, and store."""
        logger.debug(f"Handling event: {event.event_type}")
        
        # Parse event into MessageData
        message_data = self.parse_event(event)
        
        # Try to extract user_request_id from event data if not present
        if not message_data.user_request_id:
            # Look for user_request_id in the event data
            event_data = event.data if hasattr(event, 'data') else {}
            if 'user_request_id' in event_data:
                message_data.user_request_id = event_data['user_request_id']
            else:
                # Try to get it from the current execution context
                current_execution = self.session_state.get("current_async_execution", {})
                if current_execution.get("executing", False):
                    message_data.user_request_id = current_execution.get("user_request_id")
        logger.info(f"EventHandler: parsed {event.event_type} -> role={message_data.role}, content_len={len(str(message_data.content or ''))}, ureq={message_data.user_request_id}")
        
        # Update workflow state
        user_request_id = message_data.user_request_id
        if user_request_id:
            self.workflow_states.update_workflow_state(user_request_id, event)
        
        # Store in session state
        self.store_message(message_data)
        
        # Update aggregated content for real-time display
        self.update_aggregated_content(user_request_id, message_data)

        # Update compact execution trail (aggregated_steps) for live bubble
        try:
            if user_request_id:
                if "aggregated_steps" not in self.session_state:
                    self.session_state.aggregated_steps = {}
                if user_request_id not in self.session_state.aggregated_steps:
                    self.session_state.aggregated_steps[user_request_id] = []
                steps = self.session_state.aggregated_steps[user_request_id]

                et = getattr(message_data, 'event_type', None) or getattr(event, 'event_type', None)
                data = getattr(event, 'data', {}) if hasattr(event, 'data') else {}

                step_line = None
                if et == "tool_start":
                    tool_name = data.get("tool_name", "unknown")
                    tool_args = data.get("arguments") or data.get("tool_args") or {}
                    args_preview = str(tool_args)
                    if len(args_preview) > 120:
                        args_preview = args_preview[:117] + "..."
                    step_line = f"⚙️ Executing: `{tool_name}`" + (f" — args: `{args_preview}`" if tool_args else "")
                elif et == "tool_complete":
                    tool_name = data.get("tool_name", "unknown")
                    result_len = data.get("result_length")
                    if result_len is None:
                        result_len = len(str(data.get("tool_result", "")))
                    step_line = f"✅ Tool Completed: `{tool_name}` — result: {result_len} chars"
                elif et == "llm_complete":
                    tokens_in = data.get("tokens_in", 0)
                    tokens_out = data.get("tokens_out", 0)
                    tool_calls = data.get("tool_calls", [])
                    tool_summary = ""
                    if isinstance(tool_calls, list) and tool_calls:
                        names = [tc.get('function', {}).get('name', 'unknown') for tc in tool_calls]
                        tool_summary = f" — tools: {', '.join(names)}"
                    elif isinstance(tool_calls, int) and tool_calls > 0:
                        tool_summary = f" — tools: {tool_calls} call(s)"
                    step_line = f"🤔 LLM step: {tokens_in:,} in, {tokens_out:,} out{tool_summary}"

                if step_line:
                    if not steps or steps[-1] != step_line:
                        steps.append(step_line)
                        logger.info(f"EventHandler: aggregated_steps[{user_request_id}] size={len(steps)}")
        except Exception as e:
            logger.warning(f"Failed to update aggregated_steps: {e}")
        # Ensure LLM user-facing content is visible even if flags are off
        try:
            if event.event_type == "llm_complete" and message_data.content and str(message_data.content).strip():
                if "aggregated_content" not in self.session_state:
                    self.session_state.aggregated_content = {}
                if user_request_id not in self.session_state.aggregated_content:
                    self.session_state.aggregated_content[user_request_id] = []
                # Avoid duplicate contiguous appends
                agg_list = self.session_state.aggregated_content[user_request_id]
                if not agg_list or agg_list[-1] != message_data.content:
                    agg_list.append(message_data.content)
        except Exception:
            pass
        
        # Signal UI that new events arrived for polling rerender
        try:
            self.session_state.has_new_events = True
        except Exception:
            pass
        return message_data
    
    def update_aggregated_content(self, user_request_id: str, message_data: MessageData) -> None:
        """Update aggregated content for real-time display."""
        if not user_request_id:
            return
        
        # Get or create aggregated content for this workflow
        if "aggregated_content" not in self.session_state:
            self.session_state.aggregated_content = {}
        
        if user_request_id not in self.session_state.aggregated_content:
            self.session_state.aggregated_content[user_request_id] = []
        
        # Add content to aggregated list for display-only items:
        # - Skip transient workflow events (llm_start/tool_start/tool_complete banners)
        # - Include status reports and real assistant/tool content
        if message_data.content and str(message_data.content).strip():
            should_append = False
            if getattr(message_data, 'is_status_report', False):
                should_append = True
            elif not getattr(message_data, 'workflow_event', False):
                # Non-transient assistant content (e.g., LLM responses)
                should_append = True
            # else: transient workflow_event -> don't append to aggregated parts
            if should_append:
                self.session_state.aggregated_content[user_request_id].append(message_data.content)
                logger.info(f"EventHandler: aggregated_content[{user_request_id}] size={len(self.session_state.aggregated_content[user_request_id])}")
        
        # Store workflow completion data for token extraction
        if hasattr(message_data, 'event_type') and message_data.event_type == "workflow_complete":
            current_execution = self.session_state.get("current_async_execution", {})
            if current_execution.get("user_request_id") == user_request_id:
                current_execution["workflow_complete_data"] = message_data.to_dict()
                self.session_state.current_async_execution = current_execution
    
    def parse_event(self, event) -> MessageData:
        """Parse event into standardized MessageData."""
        event_type = event.event_type
        event_data = event.data
        
        logger.debug(f"Parsing event: {event_type}")
        
        if event_type == "workflow_start":
            md = self.parser.parse_workflow_start_event(event_data)
        elif event_type == "llm_start":
            md = self.parser.parse_llm_start_event(event_data)
        elif event_type == "llm_complete":
            md = self.parser.parse_llm_complete_event(event_data)
            # If LLM provided user-facing response_content, ensure it's captured as assistant content
            try:
                response_content = event_data.get("response_content")
                if response_content and not md.content:
                    md.content = response_content
                    md.workflow_event = False
            except Exception:
                pass
        elif event_type == "tool_start":
            md = self.parser.parse_tool_start_event(event_data)
        elif event_type == "tool_complete":
            md = self.parser.parse_tool_complete_event(event_data)
        elif event_type == "status_report_start":
            md = self.parser.parse_status_report_start_event(event_data)
        elif event_type == "status_report_complete":
            md = self.parser.parse_status_report_complete_event(event_data)
        elif event_type == "status_report_error":
            md = self.parser.parse_status_report_error_event(event_data)
        elif event_type == "message_added":
            md = self.parser.parse_message_added_event(event_data)
        elif event_type == "workflow_complete":
            md = self.parser.parse_workflow_complete_event(event_data)
        else:
            logger.warning(f"Unknown event type: {event_type}")
            md = MessageData(
                role="assistant",
                content=f"⚠️ Unknown event: {event_type}",
                user_request_id=event_data.get("user_request_id"),
                workflow_event=True
            )
        # annotate event type on message
        md.event_type = event_type
        return md
    
    def store_message(self, message_data: MessageData) -> None:
        """Store message in session state."""
        if not message_data.user_request_id:
            logger.warning("Message has no user_request_id, skipping storage")
            return
        
        # Convert to dict for session state storage
        message_dict = message_data.to_dict()
        
        # Handle status report placeholders
        if message_data.status_report_placeholder:
            # Remove any existing placeholders for this request
            self.session_state.messages = [
                m for m in self.session_state.messages 
                if not (m.get("status_report_placeholder") and 
                       m.get("user_request_id") == message_data.user_request_id)
            ]
        
        # Add to session state
        self.session_state.messages.append(message_dict)
        
        logger.debug(f"Stored message for request {message_data.user_request_id}: {message_data.role}")
    

    
    def get_workflow_summary(self, user_request_id: str) -> Dict[str, Any]:
        """Get current summary for a workflow."""
        return self.workflow_states.get_workflow_summary(user_request_id)
    
    def cleanup_old_workflows(self) -> None:
        """Clean up old completed workflows."""
        self.workflow_states.cleanup_completed_workflows()
    
    def add_user_message(self, content: str, user_request_id: str) -> None:
        """Add a user message to the chat history."""
        user_message = MessageData(
            role="user",
            content=content,
            user_request_id=user_request_id
        )
        
        self.store_message(user_message)
        logger.info(f"Added user message for request {user_request_id}")
    
    def get_messages_for_request(self, user_request_id: str) -> List[Dict[str, Any]]:
        """Get all messages for a specific request."""
        return [
            msg for msg in self.session_state.messages 
            if msg.get("user_request_id") == user_request_id
        ]
    
    def remove_workflow_events(self, user_request_id: str) -> None:
        """Remove workflow events for a request (keep only final messages)."""
        self.session_state.messages = [
            msg for msg in self.session_state.messages 
            if not (msg.get("workflow_event") and 
                   msg.get("user_request_id") == user_request_id)
        ]
        logger.debug(f"Removed workflow events for request {user_request_id}")
    
    def get_all_workflow_summaries(self) -> Dict[str, Dict[str, Any]]:
        """Get summaries for all workflows."""
        return self.workflow_states.get_all_summaries()
