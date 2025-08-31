"""
Event Bridge for PocketFlow to FastAPI WebSocket Integration

This module bridges PocketFlow workflow events to the FastAPI WebSocket system
so that the UI can receive real-time workflow updates.

Based on the existing Streamlit event handling pattern from:
- async_workflow_ui.py
- event_handler.py  
- workflow_executor.py
"""

import asyncio
from typing import Dict, Any, Optional
from loguru import logger

from .websocket_manager import manager
from nifi_mcp_server.workflows.core.event_system import get_event_emitter, WorkflowEvent, EventTypes


class EventBridge:
    """Bridge PocketFlow events to FastAPI WebSocket."""
    
    def __init__(self):
        self.event_emitter = get_event_emitter()
        self.bound_logger = logger.bind(component="EventBridge")
        
    async def start(self):
        """Start the event bridge."""
        self.bound_logger.info("Starting event bridge")
        
        # Register callback for PocketFlow events
        self.event_emitter.on(self._handle_pocketflow_event)
        
        self.bound_logger.info("Event bridge started - listening for PocketFlow events")
    
    async def _handle_pocketflow_event(self, event: WorkflowEvent):
        """Handle PocketFlow events and forward to WebSocket."""
        try:
            self.bound_logger.debug(f"Received PocketFlow event: {event.event_type}")
            
            # Map PocketFlow events to WebSocket messages
            websocket_message = self._map_event_to_websocket(event)
            
            if websocket_message:
                # Broadcast to all connected WebSocket clients
                await manager.broadcast_json(websocket_message)
                self.bound_logger.debug(f"Forwarded event to WebSocket: {event.event_type}")
            
        except Exception as e:
            self.bound_logger.error(f"Error handling PocketFlow event: {e}", exc_info=True)
    
    def _map_event_to_websocket(self, event: WorkflowEvent) -> Optional[Dict[str, Any]]:
        """Map PocketFlow events to WebSocket message format.
        
        Based on the existing event handling pattern from event_handler.py
        """
        
        # Extract user_request_id from event data
        user_request_id = event.user_request_id
        event_data = event.data or {}
        
        # Map based on EventTypes constants and existing event_handler.py patterns
        if event.event_type == EventTypes.WORKFLOW_START:
            workflow_name = event_data.get("workflow_name", "unguided")
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "started",
                "message": f"🚀 Workflow execution started: {workflow_name}",
                "timestamp": str(event.id)
            }
            
        elif event.event_type == EventTypes.WORKFLOW_COMPLETE:
            # Don't send a separate completion message - the LLM response will handle completion
            return None
            
        elif event.event_type == EventTypes.WORKFLOW_ERROR:
            return {
                "type": "workflow_complete",
                "request_id": user_request_id,
                "result": {"error": event_data.get("error", "Unknown error")},
                "timestamp": str(event.id)
            }
            
        elif event.event_type == EventTypes.LLM_START:
            provider = event_data.get("provider", "Unknown")
            model = event_data.get("model", "Unknown")
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": f"LLM Call Started - {provider} {model}",
                "timestamp": str(event.id)
            }
            
        elif event.event_type == EventTypes.LLM_COMPLETE:
            tokens_in = event_data.get("tokens_in", 0)
            tokens_out = event_data.get("tokens_out", 0)
            tool_calls = event_data.get("tool_calls", [])
            response_content = event_data.get("response_content")
            
            # Check if this is a final LLM response with content
            if response_content:
                # This is the final assistant response - send as workflow_complete
                return {
                    "type": "workflow_complete",
                    "request_id": user_request_id,
                    "result": {
                        "content": response_content,
                        "tokens_in": tokens_in,
                        "tokens_out": tokens_out,
                        "tool_calls": tool_calls,
                        "metadata": {
                            "token_count_in": tokens_in,
                            "token_count_out": tokens_out,
                            "tool_calls_count": len(tool_calls) if tool_calls else 0
                        }
                    },
                    "timestamp": str(event.id)
                }
            else:
                # This is an intermediate LLM step - send as status update
                tool_summary = ""
                if isinstance(tool_calls, list) and tool_calls:
                    names = [tc.get('function', {}).get('name', 'unknown') for tc in tool_calls]
                    tool_summary = f" — tools: {', '.join(names)}"
                elif isinstance(tool_calls, int) and tool_calls > 0:
                    tool_summary = f" — tools: {tool_calls} call(s)"
                
                return {
                    "type": "workflow_status",
                    "request_id": user_request_id,
                    "status": "processing",
                    "message": f"LLM step: {tokens_in:,} in, {tokens_out:,} out{tool_summary}",
                    "timestamp": str(event.id)
                }
            
        elif event.event_type == EventTypes.TOOL_START:
            tool_name = event_data.get("tool_name", "Unknown")
            tool_index = event_data.get("tool_index", 1)
            total_tools = event_data.get("total_tools", 1)
            tool_args = event_data.get("arguments") or event_data.get("tool_args") or {}
            
            args_preview = str(tool_args)
            if len(args_preview) > 120:
                args_preview = args_preview[:117] + "..."
            
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "executing_tools",
                "message": f"Executing: `{tool_name}` ({tool_index}/{total_tools})" + (f" — args: `{args_preview}`" if tool_args else ""),
                "timestamp": str(event.id)
            }
            
        elif event.event_type == EventTypes.TOOL_COMPLETE:
            tool_name = event_data.get("tool_name", "Unknown")
            result_len = event_data.get("result_length")
            if result_len is None:
                result_len = len(str(event_data.get("tool_result", "")))
            
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": f"Tool Completed: `{tool_name}` — result: {result_len} chars",
                "timestamp": str(event.id)
            }
            
        elif event.event_type == EventTypes.STEP_START:
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": f"Processing step: {event.step_id}",
                "timestamp": str(event.id)
            }
            
        elif event.event_type == EventTypes.STEP_COMPLETE:
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": f"✅ Completed step: {event.step_id}",
                "timestamp": str(event.id)
            }
            
        elif event.event_type == EventTypes.STATUS_REPORT_START:
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": "📊 Generating status report...",
                "timestamp": str(event.id)
            }
            
        elif event.event_type == EventTypes.STATUS_REPORT_COMPLETE:
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": "📊 Status report complete",
                "timestamp": str(event.id)
            }
            
        elif event.event_type == EventTypes.MESSAGE_ADDED:
            # This is for internal message tracking - don't send to UI
            return None
        
        # For other event types, log but don't forward
        self.bound_logger.debug(f"Unmapped event type: {event.event_type}")
        return None


# Global event bridge instance
event_bridge = EventBridge()
