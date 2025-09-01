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
from .storage import storage
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
            
            # Save messages to database if this is a message event
            await self._save_message_to_database(event)
            
            # Map PocketFlow events to WebSocket messages
            websocket_message = self._map_event_to_websocket(event)
            
            if websocket_message:
                # Broadcast to all connected WebSocket clients
                await manager.broadcast_json(websocket_message)
                self.bound_logger.debug(f"Forwarded event to WebSocket: {event.event_type}")
            
        except Exception as e:
            self.bound_logger.error(f"Error handling PocketFlow event: {e}", exc_info=True)
    
    async def _save_message_to_database(self, event: WorkflowEvent):
        """Save messages to database when they're created during workflow execution."""
        try:
            user_request_id = event.user_request_id
            event_data = event.data or {}
            
            # Save user messages when they're added to context
            if event.event_type == EventTypes.MESSAGE_ADDED:
                message_role = event_data.get("message_role")
                content_length = event_data.get("content_length", 0)
                
                # Only save if this is a real message with content
                if message_role in ["user", "assistant"] and content_length > 0:
                    # Note: We don't have the full content here, just metadata
                    # The actual content will be saved via the save_complete_response endpoint
                    self.bound_logger.debug(f"Message event received: {message_role}, {content_length} chars")
            
            # Note: UI display format messages are now saved via the UI's saveCompleteResponse
            # This prevents duplicate saves that were causing triple message rendering
            # The workflow still saves LLM conversation format for history purposes
                    
        except Exception as e:
            self.bound_logger.error(f"Error saving message to database: {e}", exc_info=True)
    
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
                "message": f"🚀 Workflow execution started: {workflow_name} (Req: {user_request_id})",
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
                "data": event_data,  # Pass all the enhanced data to the UI
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
            
            # Use LLM's tool_call_id as the primary Action ID for correlation
            tool_call_id = event_data.get("tool_call_id")
            action_id_display = tool_call_id if tool_call_id else "unknown"
            
            # Show result preview if available
            tool_result = event_data.get("tool_result")
            result_preview = ""
            if tool_result:
                result_str = str(tool_result)
                if len(result_str) > 50:
                    result_preview = f" — preview: {result_str[:47]}..."
                else:
                    result_preview = f" — result: {result_str}"
            
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": f"Tool Completed: `{tool_name}` — {result_len:,} chars{result_preview} (Act: {action_id_display})",
                "timestamp": str(event.id)
            }
            
        elif event.event_type == EventTypes.TOOL_ERROR:
            tool_name = event_data.get("tool_name", "Unknown")
            error_msg = event_data.get("error", "Unknown error")
            
            # Use LLM's tool_call_id as the primary Action ID for correlation
            tool_call_id = event_data.get("tool_call_id")
            action_id_display = tool_call_id if tool_call_id else "unknown"
            
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "error",
                "message": f"❌ Tool Failed: `{tool_name}` — {error_msg} (Act: {action_id_display})",
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
