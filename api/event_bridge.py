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
        self._started = False
        
    async def start(self):
        """Start the event bridge."""
        if self._started:
            self.bound_logger.warning("Event bridge already started, skipping")
            return
            
        self.bound_logger.info("Starting event bridge")
        
        # Register callback for PocketFlow events
        self.event_emitter.on(self._handle_pocketflow_event)
        self._started = True
        
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
        
        # === Documentation Workflow Events (check first) ===
        doc_message = self._map_documentation_events(event, user_request_id, event_data)
        if doc_message is not None:
            return doc_message
        
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
            # Check if this is a documentation workflow
            workflow_name = event_data.get("workflow_name", "unguided")
            
            if workflow_name == "flow_documentation":
                # Documentation workflow completion
                final_document = event_data.get("final_document", "")
                metrics = event_data.get("metrics", {})
                validation_issues = event_data.get("validation_issues", [])
                
                return {
                    "type": "workflow_complete",
                    "request_id": user_request_id,
                    "result": {
                        "content": final_document,
                        "content_type": "markdown",
                        "metadata": {
                            "workflow": "flow_documentation",
                            "pgs_documented": metrics.get("generation", {}).get("pgs_documented", 0),
                            "total_duration_ms": metrics.get("total_duration_ms", 0),
                            "discovery_duration_ms": metrics.get("discovery", {}).get("total_duration_ms", 0),
                            "analysis_duration_ms": metrics.get("analysis", {}).get("total_duration_ms", 0),
                            "generation_duration_ms": metrics.get("generation", {}).get("duration_ms", 0),
                            "document_size_bytes": metrics.get("generation", {}).get("document_size_bytes", 0),
                            "validation_issues": validation_issues,
                            "virtual_groups_created": metrics.get("analysis", {}).get("virtual_groups_created", 0),
                            "error_handling_entries": metrics.get("generation", {}).get("error_handling_entries", 0)
                        }
                    },
                    "timestamp": str(event.id)
                }
            else:
                # Standard unguided workflow completion
                final_content = event_data.get("final_content", None)
                final_tokens_in = event_data.get("final_tokens_in", 0)
                final_tokens_out = event_data.get("final_tokens_out", 0)
                
                # Determine if this includes a status report
                is_status_report = final_content and "### 📋 Status Report" in final_content
                
                return {
                    "type": "workflow_complete",
                    "request_id": user_request_id,
                    "result": {
                        "content": final_content,  # Include final content (status report or normal response)
                        "tokens_in": event_data.get("total_tokens_in", 0),  # Use cumulative total
                        "tokens_out": event_data.get("total_tokens_out", 0),  # Use cumulative total
                        "tool_calls": [],
                        "metadata": {
                            "token_count_in": event_data.get("total_tokens_in", 0),  # Use cumulative total
                            "token_count_out": event_data.get("total_tokens_out", 0),  # Use cumulative total
                            "tool_calls_count": event_data.get("tool_calls_executed", 0),
                            "loop_count": event_data.get("loop_count", 0),
                            "is_status_report": is_status_report,  # Determine based on content
                            "model_name": event_data.get("model_name", "unknown"),
                            "provider": event_data.get("provider", "unknown"),
                            "messages_in_request": event_data.get("messages_in_request", 0),
                            "elapsed_seconds": event_data.get("elapsed_seconds", 0)
                        }
                    },
                    "timestamp": str(event.id)
                }
            
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
            loop_count = event_data.get("loop_count", 0)
            message = f"LLM Call Started - {provider} {model}"
            if loop_count > 0:
                message += f" (Iteration {loop_count})"
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": message,
                "data": event_data,  # Pass all the enhanced data to the UI
                "timestamp": str(event.id)
            }
            
        elif event.event_type == EventTypes.LLM_COMPLETE:
            tokens_in = event_data.get("tokens_in", 0)
            tokens_out = event_data.get("tokens_out", 0)
            tool_calls = event_data.get("tool_calls", [])
            response_content = event_data.get("response_content")
            is_status_report = event_data.get("is_status_report", False)
            
            # Add detailed logging for debugging
            self.bound_logger.debug(f"LLM_COMPLETE event: content_length={len(response_content) if response_content else 0}, is_status_report={is_status_report}")
            
            # LLM_COMPLETE events are now only for intermediate responses
            # Final responses are handled by WORKFLOW_COMPLETE events
            if response_content and not is_status_report:
                # This is an intermediate LLM response - send as status update
                self.bound_logger.debug(f"LLM response received, sending as status update: {len(response_content)} chars")
                return {
                    "type": "workflow_status",
                    "request_id": user_request_id,
                    "status": "processing",
                    "message": f"LLM response received: {len(response_content)} chars",
                    "data": event_data,
                    "timestamp": str(event.id)
                }
            elif response_content and is_status_report:
                # This is a status report - send in the same shape the UI renders final responses
                self.bound_logger.info(f"Emitting status report as workflow_complete: {len(response_content)} chars")
                
                # Use cumulative token counts for status reports to match normal workflow completion
                total_tokens_in = event_data.get("total_tokens_in", tokens_in)
                total_tokens_out = event_data.get("total_tokens_out", tokens_out)
                tool_calls_executed = event_data.get("tool_calls_executed", len(tool_calls) if tool_calls else 0)
                
                return {
                    "type": "workflow_complete",
                    "request_id": user_request_id,
                    "result": {
                        "content": response_content,
                        "tokens_in": total_tokens_in,
                        "tokens_out": total_tokens_out,
                        "tool_calls": tool_calls,
                        "metadata": {
                            "token_count_in": total_tokens_in,
                            "token_count_out": total_tokens_out,
                            "tool_calls_count": tool_calls_executed,
                            "loop_count": event_data.get("loop_count", 0),
                            "is_status_report": True,
                            "model_name": event_data.get("model_name", event_data.get("model", "unknown")),
                            "provider": event_data.get("provider", "unknown"),
                            "messages_in_request": event_data.get("messages_in_request", 0),
                            "elapsed_seconds": event_data.get("elapsed_seconds", 0)
                        }
                    },
                    "timestamp": str(event.id)
                }
            else:
                # This is an intermediate LLM step - send as status update
                self.bound_logger.debug(f"Emitting intermediate LLM step as workflow_status: {tokens_in} in, {tokens_out} out")
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
                    "data": event_data,  # Pass all the enhanced data to the UI
                    "timestamp": str(event.id)
                }
            
        elif event.event_type == EventTypes.TOOL_START:
            tool_name = event_data.get("tool_name", "Unknown")
            tool_index = event_data.get("tool_index", 1)
            total_tools = event_data.get("total_tools", 1)
            tool_args = event_data.get("arguments") or event_data.get("tool_args") or {}
            loop_count = event_data.get("loop_count", 0)
            
            args_preview = str(tool_args)
            if len(args_preview) > 120:
                args_preview = args_preview[:117] + "..."
            
            message = f"Executing: `{tool_name}` ({tool_index}/{total_tools})"
            if loop_count > 0:
                message += f" (Iteration {loop_count})"
            message += (f" — args: `{args_preview}`" if tool_args else "")
            
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "executing_tools",
                "message": message,
                "timestamp": str(event.id)
            }
            
        elif event.event_type == EventTypes.TOOL_COMPLETE:
            tool_name = event_data.get("tool_name", "Unknown")
            result_len = event_data.get("result_length")
            if result_len is None:
                result_len = len(str(event_data.get("tool_result", "")))
            loop_count = event_data.get("loop_count", 0)
            
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
            
            message = f"Tool Completed: `{tool_name}` — {result_len:,} chars{result_preview} (Act: {action_id_display})"
            if loop_count > 0:
                message += f" (Iteration {loop_count})"
            
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": message,
                "timestamp": str(event.id)
            }
            
        elif event.event_type == EventTypes.TOOL_ERROR:
            tool_name = event_data.get("tool_name", "Unknown")
            error_msg = event_data.get("error", "Unknown error")
            loop_count = event_data.get("loop_count", 0)
            
            # Use LLM's tool_call_id as the primary Action ID for correlation
            tool_call_id = event_data.get("tool_call_id")
            action_id_display = tool_call_id if tool_call_id else "unknown"
            
            message = f"❌ Tool Failed: `{tool_name}` — {error_msg} (Act: {action_id_display})"
            if loop_count > 0:
                message += f" (Iteration {loop_count})"
            
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "error",
                "message": message,
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
            
            
        elif event.event_type == EventTypes.MESSAGE_ADDED:
            # This is for internal message tracking - don't send to UI
            return None
        
        # For other event types, log but don't forward
        self.bound_logger.debug(f"Unmapped event type: {event.event_type}")
        return None
    
    def _map_documentation_events(
        self,
        event: WorkflowEvent,
        user_request_id: str,
        event_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Map documentation workflow events to WebSocket message format.
        
        Args:
            event: The workflow event
            user_request_id: User's request ID
            event_data: Event data dictionary
        
        Returns:
            WebSocket message dict or None if event should not be forwarded
        """
        
        # === Documentation Phase Events ===
        
        if event.event_type == EventTypes.DOC_PHASE_START:
            phase = event_data.get("phase", "Unknown")
            progress_message = event_data.get("progress_message", f"Starting {phase} phase...")
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": f"📄 {progress_message}",
                "data": {
                    "phase": phase,
                    "workflow": "flow_documentation",
                    "metrics": event_data.get("metrics", {})
                },
                "timestamp": str(event.id)
            }
        
        elif event.event_type == EventTypes.DOC_PHASE_COMPLETE:
            phase = event_data.get("phase", "Unknown")
            metrics = event_data.get("metrics", {})
            progress_message = event_data.get("progress_message", f"{phase} complete")
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": f"✅ {progress_message}",
                "data": {
                    "phase": phase,
                    "metrics": metrics,
                    "progress_message": progress_message
                },
                "timestamp": str(event.id)
            }
        
        elif event.event_type == EventTypes.DOC_PHASE_ERROR:
            phase = event_data.get("phase", "Unknown")
            error = event_data.get("error", "Unknown error")
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "error",
                "message": f"❌ Error in {phase}: {error}",
                "data": {
                    "phase": phase,
                    "error": error
                },
                "timestamp": str(event.id)
            }
        
        # === Discovery Phase Events ===
        
        elif event.event_type == EventTypes.DOC_DISCOVERY_CHUNK:
            metrics = event_data.get("metrics", {})
            progress_message = event_data.get("progress_message", "Discovering components...")
            processors_found = metrics.get("processors_found", 0)
            pgs_found = metrics.get("process_groups_found", 0)
            
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": f"🔍 {progress_message}",
                "data": {
                    "phase": "DISCOVERY",
                    "processors": processors_found,
                    "process_groups": pgs_found,
                    "chunk_elapsed_ms": metrics.get("chunk_elapsed_ms", 0)
                },
                "timestamp": str(event.id)
            }
        
        elif event.event_type == EventTypes.DOC_DISCOVERY_TIMEOUT:
            continuation_token = event_data.get("continuation_token")
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": "⏱️ Discovery timeout, will continue from checkpoint...",
                "data": {
                    "phase": "DISCOVERY",
                    "has_continuation": bool(continuation_token)
                },
                "timestamp": str(event.id)
            }
        
        elif event.event_type == EventTypes.DOC_DISCOVERY_RETRY:
            attempt = event_data.get("attempt", 1)
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": f"🔄 Retrying discovery (attempt {attempt})...",
                "data": {
                    "phase": "DISCOVERY",
                    "attempt": attempt
                },
                "timestamp": str(event.id)
            }
        
        # === Analysis Phase Events ===
        
        elif event.event_type == EventTypes.DOC_ANALYSIS_CATEGORIZE:
            metrics = event_data.get("metrics", {})
            categorized_count = metrics.get("categorized_count", 0)
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": f"📊 Categorized {categorized_count} processors",
                "data": {
                    "phase": "ANALYSIS",
                    "categorized": categorized_count,
                    "categories": metrics.get("categories", {})
                },
                "timestamp": str(event.id)
            }
        
        elif event.event_type == EventTypes.DOC_ANALYSIS_BATCH:
            metrics = event_data.get("metrics", {})
            pg_name = metrics.get("pg_name", "Unknown")
            depth = metrics.get("depth", 0)
            progress_message = event_data.get(
                "progress_message", 
                f"Analyzing '{pg_name}' at depth {depth}"
            )
            
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": f"🧠 {progress_message}",
                "data": {
                    "phase": "ANALYSIS",
                    "pg_name": pg_name,
                    "depth": depth
                },
                "timestamp": str(event.id)
            }
        
        elif event.event_type == EventTypes.DOC_ANALYSIS_SUBFLOW:
            pg_name = event_data.get("pg_name", "Unknown")
            subflow_count = event_data.get("subflow_count", 0)
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": f"🔗 Identified {subflow_count} virtual sub-flows in '{pg_name}'",
                "data": {
                    "phase": "ANALYSIS",
                    "pg_name": pg_name,
                    "subflow_count": subflow_count
                },
                "timestamp": str(event.id)
            }
        
        # === Generation Phase Events ===
        
        elif event.event_type == EventTypes.DOC_GENERATION_SECTION:
            section = event_data.get("metrics", {}).get("section", "Unknown")
            section_labels = {
                "summary": "Executive Summary",
                "diagram": "Flow Diagram",
                "hierarchy_doc": "Detailed Breakdown",
                "io_table": "External Interactions",
                "error_handling_table": "Error Handling"
            }
            label = section_labels.get(section, section)
            
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": f"📝 Generating: {label}",
                "data": {
                    "phase": "GENERATION",
                    "section": section,
                    "section_label": label
                },
                "timestamp": str(event.id)
            }
        
        elif event.event_type == EventTypes.DOC_GENERATION_DIAGRAM:
            node_count = event_data.get("node_count", 0)
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": f"📊 Generated Mermaid diagram with {node_count} nodes",
                "data": {
                    "phase": "GENERATION",
                    "node_count": node_count
                },
                "timestamp": str(event.id)
            }
        
        elif event.event_type == EventTypes.DOC_GENERATION_VALIDATE:
            issues = event_data.get("issues", [])
            issue_count = len(issues)
            if issue_count == 0:
                message = "✅ Documentation validated successfully"
            else:
                message = f"⚠️ Documentation has {issue_count} validation warning(s)"
            
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": message,
                "data": {
                    "phase": "GENERATION",
                    "issues": issues,
                    "valid": issue_count == 0
                },
                "timestamp": str(event.id)
            }
        
        # === Progress Updates ===
        
        elif event.event_type == EventTypes.DOC_PROGRESS_UPDATE:
            progress_message = event_data.get("progress_message", "Processing...")
            phase = event_data.get("phase", "Unknown")
            return {
                "type": "workflow_status",
                "request_id": user_request_id,
                "status": "processing",
                "message": progress_message,
                "data": {
                    "phase": phase,
                    "metrics": event_data.get("metrics", {})
                },
                "timestamp": str(event.id)
            }
        
        # Not a documentation event
        return None


# Global event bridge instance
event_bridge = EventBridge()
