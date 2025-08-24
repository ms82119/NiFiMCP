"""
Message data models and event parsing utilities.
"""

import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from loguru import logger


@dataclass
class MessageData:
    """Standardized message data structure."""
    role: str
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    action_id: Optional[str] = None
    token_count_in: int = 0
    token_count_out: int = 0
    user_request_id: Optional[str] = None
    workflow_event: bool = False
    is_status_report: bool = False
    status_report_placeholder: bool = False
    real_time_event: bool = False
    event_type: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for session state storage."""
        result = {
            "role": self.role,
            "token_count_in": self.token_count_in,
            "token_count_out": self.token_count_out,
        }
        
        if self.content is not None:
            result["content"] = self.content
        if self.tool_calls is not None:
            result["tool_calls"] = self.tool_calls
        if self.action_id is not None:
            result["action_id"] = self.action_id
        if self.user_request_id is not None:
            result["user_request_id"] = self.user_request_id
        if self.workflow_event:
            result["workflow_event"] = True
        if self.is_status_report:
            result["is_status_report"] = True
        if self.status_report_placeholder:
            result["status_report_placeholder"] = True
            
        return result


class EventParser:
    """Parse workflow events into standardized MessageData."""
    
    @staticmethod
    def parse_llm_start_event(event_data: Dict[str, Any]) -> MessageData:
        """Parse LLM_START event into MessageData."""
        return MessageData(
            role="assistant",
            content=f"🤔 **LLM Processing** | **Provider:** {event_data.get('provider', 'unknown')} | **Model:** {event_data.get('model', 'unknown')}\n\n📊 **Context:** {event_data.get('message_count', 0)} messages, {event_data.get('tool_count', 0)} tools available",
            action_id=event_data.get("action_id"),
            user_request_id=event_data.get("user_request_id"),
            workflow_event=True
        )
    
    @staticmethod
    def parse_llm_complete_event(event_data: Dict[str, Any]) -> MessageData:
        """Parse LLM_COMPLETE event into MessageData."""
        response_content = event_data.get("response_content", "")
        tool_calls = event_data.get("tool_calls", [])
        
        # Handle tool_calls as either integer count or list of tool calls
        tool_calls_list = []
        if isinstance(tool_calls, list):
            tool_calls_list = tool_calls
        elif isinstance(tool_calls, int) and tool_calls > 0:
            # Convert count to empty list - actual tool calls will be in separate events
            tool_calls_list = []
        
        return MessageData(
            role="assistant",
            content=response_content if response_content else None,
            tool_calls=tool_calls_list if tool_calls_list else None,
            action_id=event_data.get("action_id"),
            token_count_in=event_data.get("tokens_in", 0),
            token_count_out=event_data.get("tokens_out", 0),
            user_request_id=event_data.get("user_request_id"),
            workflow_event=not bool(response_content)  # Only mark as workflow_event if no content
        )
    
    @staticmethod
    def parse_tool_start_event(event_data: Dict[str, Any]) -> MessageData:
        """Parse TOOL_START event into MessageData."""
        tool_name = event_data.get("tool_name", "unknown")
        tool_args = event_data.get("tool_args", {})
        
        content_parts = [f"⚙️ **Executing Tool:** `{tool_name}`"]
        if tool_args:
            arg_str = str(tool_args)
            content_parts.append(f"📋 **Arguments:** `{arg_str[:100]}{'...' if len(arg_str) > 100 else ''}`")
        
        return MessageData(
            role="assistant",
            content="\n\n".join(content_parts),
            action_id=event_data.get("action_id"),
            user_request_id=event_data.get("user_request_id"),
            workflow_event=True
        )
    
    @staticmethod
    def parse_tool_complete_event(event_data: Dict[str, Any]) -> MessageData:
        """Parse TOOL_COMPLETE event into MessageData."""
        tool_name = event_data.get("tool_name", "unknown")
        tool_result = event_data.get("tool_result", {})
        
        content_lines = [f"✅ **Tool Completed:** `{tool_name}`"]
        if tool_result:
            result_preview = str(tool_result)[:200]
            content_lines.append(f"📄 **Result:** `{result_preview}{'...' if len(str(tool_result)) > 200 else ''}`")
        
        return MessageData(
            role="assistant",
            content="\n\n".join(content_lines),
            action_id=event_data.get("action_id"),
            user_request_id=event_data.get("user_request_id"),
            workflow_event=True
        )
    
    @staticmethod
    def parse_status_report_start_event(event_data: Dict[str, Any]) -> MessageData:
        """Parse STATUS_REPORT_START event into MessageData."""
        return MessageData(
            role="assistant",
            content="📝 Preparing status report…",
            user_request_id=event_data.get("user_request_id"),
            workflow_event=True,
            status_report_placeholder=True
        )
    
    @staticmethod
    def parse_status_report_complete_event(event_data: Dict[str, Any]) -> MessageData:
        """Parse STATUS_REPORT_COMPLETE event into MessageData."""
        status_content = event_data.get("status_content", "")
        
        return MessageData(
            role="assistant",
            content=f"### 📋 Status Report\n\n{status_content}",
            action_id=event_data.get("action_id"),
            token_count_in=event_data.get("token_count_in", 0),
            token_count_out=event_data.get("token_count_out", 0),
            user_request_id=event_data.get("user_request_id"),
            is_status_report=True
        )
    
    @staticmethod
    def parse_status_report_error_event(event_data: Dict[str, Any]) -> MessageData:
        """Parse STATUS_REPORT_ERROR event into MessageData."""
        error_msg = event_data.get("error", "Unknown error")
        
        return MessageData(
            role="assistant",
            content=f"❌ **Status report failed:** {error_msg}",
            user_request_id=event_data.get("user_request_id"),
            workflow_event=True
        )
    
    @staticmethod
    def parse_workflow_start_event(event_data: Dict[str, Any]) -> MessageData:
        """Parse WORKFLOW_START event into MessageData."""
        workflow_name = event_data.get("workflow_name", "unknown")
        
        return MessageData(
            role="assistant",
            content=f"🚀 **Starting async workflow:** {workflow_name}\n\n*Real-time updates will appear below...*",
            user_request_id=event_data.get("user_request_id"),
            workflow_event=True
        )
    
    @staticmethod
    def parse_workflow_complete_event(event_data: Dict[str, Any]) -> MessageData:
        """Parse WORKFLOW_COMPLETE event into MessageData. No user-facing content."""
        return MessageData(
            role="assistant",
            content=None,
            user_request_id=event_data.get("user_request_id"),
            workflow_event=True
        )
    
    @staticmethod
    def parse_message_added_event(event_data: Dict[str, Any]) -> MessageData:
        """Parse MESSAGE_ADDED event into MessageData."""
        message_role = event_data.get("message_role", "assistant")
        message_type = event_data.get("message_type", "content")
        content_length = event_data.get("content_length", 0)
        tool_calls = event_data.get("tool_calls", 0)
        action_id = event_data.get("action_id")
        
        # For message_added events, we don't store them as they represent internal workflow state
        # We just return a minimal MessageData to avoid warnings
        return MessageData(
            role=message_role,
            content=None,  # No content for message_added events
            action_id=action_id,
            user_request_id=None,  # These events don't have user_request_id
            workflow_event=True  # Mark as workflow event to avoid storage
        )
