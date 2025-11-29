"""
Pydantic models for API requests and responses.

This module defines the data models used for API communication,
ensuring type safety and validation.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


class ChatMessage(BaseModel):
    """Model for chat message submission."""
    content: str = Field(..., description="The message content")
    objective: Optional[str] = Field(None, description="Optional objective or context for the conversation")
    provider: Optional[str] = Field("openai", description="LLM provider to use")
    model_name: Optional[str] = Field("gpt-4o-mini", description="Model name to use")
    selected_nifi_server_id: Optional[str] = Field(None, description="NiFi server ID to use")
    # Smart purging settings
    auto_prune_history: Optional[bool] = Field(True, description="Whether to automatically prune conversation history")
    max_tokens_limit: Optional[int] = Field(32000, description="Maximum tokens limit for conversation context")
    max_loop_iterations: Optional[int] = Field(10, description="Maximum number of workflow iterations")
    # Workflow selection
    workflow_name: Optional[str] = Field("unguided", description="Workflow to use: 'unguided' or 'flow_documentation'")
    # Documentation workflow specific
    process_group_id: Optional[str] = Field(None, description="Process group ID for flow_documentation workflow")


class ChatResponse(BaseModel):
    """Model for chat response."""
    status: str = Field(..., description="Response status")
    request_id: str = Field(..., description="Unique request ID")
    message: Optional[str] = Field(None, description="Response message")


class WebSocketMessage(BaseModel):
    """Model for WebSocket messages."""
    type: str = Field(..., description="Message type")
    data: Optional[Dict[str, Any]] = Field(None, description="Message data")
    request_id: Optional[str] = Field(None, description="Request ID for tracking")


class WorkflowStartMessage(BaseModel):
    """Model for workflow start notification."""
    type: str = Field("workflow_start", description="Message type")
    request_id: str = Field(..., description="Request ID")
    workflow_name: str = Field(..., description="Workflow name")
    timestamp: datetime = Field(default_factory=datetime.now, description="Event timestamp")


class WorkflowCompleteMessage(BaseModel):
    """Model for workflow completion notification."""
    type: str = Field("workflow_complete", description="Message type")
    request_id: str = Field(..., description="Request ID")
    result: Dict[str, Any] = Field(..., description="Workflow result")
    timestamp: datetime = Field(default_factory=datetime.now, description="Event timestamp")


class WorkflowEventMessage(BaseModel):
    """Model for workflow event notifications."""
    type: str = Field("event", description="Message type")
    event_type: str = Field(..., description="Event type")
    request_id: str = Field(..., description="Request ID")
    data: Dict[str, Any] = Field(..., description="Event data")
    timestamp: datetime = Field(default_factory=datetime.now, description="Event timestamp")


class StopWorkflowMessage(BaseModel):
    """Model for stop workflow request."""
    type: str = Field("stop_workflow", description="Message type")
    request_id: str = Field(..., description="Request ID to stop")


class ChatHistoryResponse(BaseModel):
    """Model for chat history response."""
    messages: List[Dict[str, Any]] = Field(..., description="List of chat messages")
    total_count: int = Field(..., description="Total number of messages")


class ErrorResponse(BaseModel):
    """Model for error responses."""
    error: str = Field(..., description="Error message")
    error_type: Optional[str] = Field(None, description="Error type")
    request_id: Optional[str] = Field(None, description="Request ID if available")

