"""
Workflow execution manager for async workflows with event handling integration.
"""

import streamlit as st
import asyncio
import threading
import time
from typing import Dict, Any, Optional
from loguru import logger

from event_handler import EventHandler
from chat_display import ChatDisplay


class WorkflowExecutor:
    """Manages async workflow execution with integrated event handling."""
    
    def __init__(self, event_handler: EventHandler, chat_display: ChatDisplay):
        self.event_handler = event_handler
        self.chat_display = chat_display
        self.session_state = st.session_state
        self.live_response_container = None
    
    def run_async_workflow(self, workflow_name: str, provider: str, model_name: str, 
                          base_sys_prompt: str, user_req_id: str, user_input: str) -> None:
        """Run an async workflow with integrated real-time UI updates."""
        bound_logger = logger.bind(user_request_id=user_req_id)
        execution_start_time = time.time()
        
        # Check for duplicate execution
        if self._is_already_executing(workflow_name, user_req_id):
            bound_logger.warning(f"Workflow '{workflow_name}' already executing for request {user_req_id}, skipping duplicate call")
            return
        
        # Set execution state
        self._set_execution_state(workflow_name, user_req_id, execution_start_time)
        
        try:
            # Display workflow start message
            self._display_workflow_start(workflow_name, user_req_id)
            
            # Prepare execution context
            context = self._prepare_execution_context(
                provider, model_name, base_sys_prompt, user_req_id, user_input
            )
            
            # Create async executor
            async_executor = self._create_async_executor(workflow_name)
            if not async_executor:
                st.error(f"Failed to create async executor for workflow: {workflow_name}")
                return
            
            # Pre-initialize components
            self._pre_initialize_components()
            
            # Set up event handling
            events_received = []
            event_emitter = self._setup_event_handling(events_received, bound_logger)
            
            # Execute workflow asynchronously
            result_container = self._execute_workflow_async(async_executor, context)
            
            # Wait for completion with real-time updates
            self._wait_for_completion_with_updates(
                result_container, events_received, event_emitter, bound_logger
            )
            
            # Process final results
            self._process_final_results(result_container, bound_logger)
            
        except Exception as e:
            bound_logger.error(f"Async workflow execution error: {e}", exc_info=True)
            st.error(f"Failed to execute async workflow: {str(e)}")
        finally:
            # Clean up execution state
            self._cleanup_execution_state(workflow_name, user_req_id)
    
    def _is_already_executing(self, workflow_name: str, user_req_id: str) -> bool:
        """Check if workflow is already executing for this request."""
        current_execution = self.session_state.get("current_async_execution", {})
        
        # Check for stale execution state (older than 5 minutes)
        if current_execution.get("executing", False):
            start_time = current_execution.get("start_time", 0)
            if time.time() - start_time > 300:  # 5 minutes timeout
                logger.warning(f"Clearing stale execution state for workflow '{current_execution.get('workflow_name')}'")
                self.session_state.current_async_execution = {}
                current_execution = {}
        
        return (current_execution.get("workflow_name") == workflow_name and 
                current_execution.get("user_request_id") == user_req_id and
                current_execution.get("executing", False))
    
    def _set_execution_state(self, workflow_name: str, user_req_id: str, start_time: float) -> None:
        """Set execution state for this workflow and request."""
        self.session_state.current_async_execution = {
            "workflow_name": workflow_name,
            "user_request_id": user_req_id,
            "executing": True,
            "start_time": start_time
        }
    
    def _display_workflow_start(self, workflow_name: str, user_req_id: str) -> None:
        """Add workflow start message to session state via EventHandler and init live container."""
        from models.message import MessageData
        start_message = MessageData(
            role="assistant",
            content=f"🚀 **Starting async workflow:** {workflow_name}\n\n*Real-time updates will appear below...*",
            user_request_id=user_req_id,
            workflow_event=True  # Treat as transient banner; don't persist as normal assistant content
        )
        self.event_handler.store_message(start_message)
        logger.info(f"Workflow start message added for {user_req_id}")
        # Initialize a live response container to render aggregated updates during execution
        self.live_response_container = st.empty()
    
    def _prepare_execution_context(self, provider: str, model_name: str, base_sys_prompt: str, 
                                 user_req_id: str, user_input: str) -> Dict[str, Any]:
        """Prepare execution context for the workflow."""
        current_objective = self.session_state.get("current_objective", "")
        if current_objective and current_objective.strip():
            effective_system_prompt = f"{base_sys_prompt}\n\n## Current Objective\n{current_objective.strip()}"
        else:
            effective_system_prompt = base_sys_prompt
        
        return {
            "provider": provider,
            "model_name": model_name,
            "system_prompt": effective_system_prompt,
            "user_request_id": user_req_id,
            "user_prompt": user_input,
            "selected_nifi_server_id": self.session_state.get("selected_nifi_server_id"),
            "selected_phase": self.session_state.get("selected_phase", "All"),
            "max_loop_iterations": self.session_state.get("max_loop_iterations", 10),
            "max_tokens_limit": self.session_state.get("max_tokens_limit", 8000),
            "auto_prune_history": self.session_state.get("auto_prune_history", False),
            "current_objective": current_objective
        }
    
    def _create_async_executor(self, workflow_name: str):
        """Create async executor for the workflow."""
        try:
            # Ensure the parent directory is in sys.path for imports
            import sys
            import os
            current_dir = os.path.dirname(os.path.abspath(__file__))
            parent_dir = os.path.dirname(current_dir)
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            
            from nifi_mcp_server.workflows.registry import get_workflow_registry
            registry = get_workflow_registry()
            async_executor = registry.create_async_executor(workflow_name)
            
            if not async_executor:
                logger.error(f"Failed to create async executor for workflow: {workflow_name}")
                return None
            
            # Pass config to async executor
            self._pass_config_to_executor(async_executor)
            
            return async_executor
        except Exception as e:
            logger.error(f"Error creating async executor: {e}", exc_info=True)
            return None
    
    def _pass_config_to_executor(self, async_executor) -> None:
        """Pass configuration to the async executor."""
        try:
            # Import config
            import sys
            import os
            current_dir = os.path.dirname(os.path.abspath(__file__))
            parent_dir = os.path.dirname(current_dir)
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            
            from config import settings as config
            
            config_dict = {
                'openai': {
                    'api_key': config.OPENAI_API_KEY,
                    'models': config.OPENAI_MODELS
                },
                'gemini': {
                    'api_key': config.GOOGLE_API_KEY,
                    'models': config.GEMINI_MODELS
                },
                'anthropic': {
                    'api_key': config.ANTHROPIC_API_KEY,
                    'models': config.ANTHROPIC_MODELS
                },
                'perplexity': {
                    'api_key': config.PERPLEXITY_API_KEY,
                    'models': config.PERPLEXITY_MODELS
                }
            }
            
            if hasattr(async_executor, 'set_config'):
                async_executor.set_config(config_dict)
                logger.info("Passed config to async executor")
        except Exception as e:
            logger.warning(f"Failed to pass config to executor: {e}")
    
    def _pre_initialize_components(self) -> None:
        """Pre-initialize components in main thread to avoid thread context warnings."""
        try:
            from app import get_chat_manager, get_token_counter, get_mcp_client
            
            chat_manager = get_chat_manager()
            token_counter = get_token_counter()
            mcp_client = get_mcp_client()
            
            logger.info("Pre-initialized ChatManager, TokenCounter, and MCPClient in main thread")
        except Exception as e:
            logger.warning(f"Failed to pre-initialize components: {e}")
    
    def _setup_event_handling(self, events_received: list, bound_logger) -> Any:
        """Set up event handling for real-time updates."""
        # Ensure the parent directory is in sys.path for imports
        import sys
        import os
        current_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(current_dir)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        
        from nifi_mcp_server.workflows.core.event_system import get_event_emitter
        
        event_emitter = get_event_emitter()
        
        def handle_workflow_event(event):
            """Handle workflow events - collect them for main thread processing."""
            events_received.append(event)
            bound_logger.info(f"Received workflow event: {event.event_type} with data: {event.data}")
        
        # Register event handler
        event_emitter.on(handle_workflow_event)
        bound_logger.info("Event handler registered for async workflow")
        
        return event_emitter
    
    def _execute_workflow_async(self, async_executor, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute workflow asynchronously in background thread."""
        result_container = {"result": None, "error": None, "completed": False, "thread": None}
        
        def run_async_workflow():
            """Run the async workflow in a separate thread."""
            try:
                # Ensure the parent directory is in sys.path for imports in this thread
                import sys
                import os
                current_dir = os.path.dirname(os.path.abspath(__file__))
                parent_dir = os.path.dirname(current_dir)
                if parent_dir not in sys.path:
                    sys.path.insert(0, parent_dir)
                
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(async_executor.execute_async(context))
                result_container["result"] = result
                result_container["completed"] = True
            except Exception as e:
                result_container["error"] = str(e)
                result_container["completed"] = True
                logger.error(f"Async workflow execution failed: {e}", exc_info=True)
            finally:
                loop.close()
        
        # Start async execution in background thread
        workflow_thread = threading.Thread(target=run_async_workflow)
        workflow_thread.start()
        result_container["thread"] = workflow_thread
        
        return result_container
    
    def _wait_for_completion_with_updates(self, result_container: Dict[str, Any], 
                                        events_received: list, event_emitter, bound_logger) -> None:
        """Wait for completion with real-time event processing."""
        start_time = time.time()
        max_wait_time = 300  # 5 minutes max
        last_event_count = 0
        
        while not result_container["completed"] and (time.time() - start_time) < max_wait_time:
            # Check for new events and process them
            if len(events_received) > last_event_count:
                bound_logger.info(f"Processing {len(events_received) - last_event_count} new events")
                
                # Process new events since last check
                for event in events_received[last_event_count:]:
                    self._process_event_real_time(event, bound_logger)
                
                last_event_count = len(events_received)
                # Update the live aggregated response bubble in the main thread
                try:
                    current_execution = self.session_state.get("current_async_execution", {})
                    user_request_id = current_execution.get("user_request_id")
                    if user_request_id:
                        logger.info(f"Executor: updating live response for {user_request_id}")
                        self._update_live_response(user_request_id)
                except Exception as ui_err:
                    bound_logger.warning(f"Live response update failed: {ui_err}")
            else:
                # Add periodic logging to show the loop is still running
                if int(time.time() - start_time) % 5 == 0 and int(time.time() - start_time) > 0:
                    bound_logger.debug(f"Waiting for events... (received: {len(events_received)}, completed: {result_container['completed']})")
            
            time.sleep(0.1)  # Check more frequently for real-time updates
        
        # Wait for thread to complete
        if result_container.get("thread"):
            result_container["thread"].join(timeout=10)
        
        # Process any remaining events that arrived just before/after completion
        try:
            if last_event_count < len(events_received):
                bound_logger.info(f"Processing {len(events_received) - last_event_count} remaining events after completion")
                for event in events_received[last_event_count:]:
                    self._process_event_real_time(event, bound_logger)
                current_execution = self.session_state.get("current_async_execution", {})
                user_request_id = current_execution.get("user_request_id")
                if user_request_id:
                    self._update_live_response(user_request_id)
        except Exception as ui_err:
            bound_logger.warning(f"Post-completion event processing failed: {ui_err}")

        # Remove event handler
        event_emitter.remove_callback(lambda event: None)  # Remove the handler

        # Final live update after completion
        try:
            current_execution = self.session_state.get("current_async_execution", {})
            user_request_id = current_execution.get("user_request_id")
            if user_request_id:
                self._update_live_response(user_request_id)
        except Exception as ui_err:
            bound_logger.warning(f"Final live response update failed: {ui_err}")
    
    def _process_event(self, event, bound_logger) -> None:
        """Process a single event through our event handler (for storage only)."""
        try:
            # Handle the event through our unified event handler
            message_data = self.event_handler.handle_event(event)
            
            # Store real-time events for display (we'll show them in the main UI)
            if message_data and (message_data.content or message_data.tool_calls):
                # Mark this as a real-time event for immediate display
                message_data.real_time_event = True
                bound_logger.info(f"Real-time event stored: {event.event_type} - {len(str(message_data.content or ''))} chars")
                
        except Exception as e:
            bound_logger.error(f"Error processing event {event.event_type}: {e}", exc_info=True)
    
    def _process_event_real_time(self, event, bound_logger) -> None:
        """Process a single event and trigger UI update."""
        try:
            bound_logger.info(f"Processing real-time event: {event.event_type} with data: {event.data}")
            
            # Pass event to EventHandler for processing
            self.event_handler.handle_event(event)
            
            bound_logger.info(f"Event processed by EventHandler: {event.event_type}")
                
        except Exception as e:
            bound_logger.error(f"Error processing real-time event {event.event_type}: {e}", exc_info=True)
    
    def _process_final_results(self, result_container: Dict[str, Any], bound_logger) -> None:
        """Process final workflow results."""
        if result_container["error"]:
            st.error(f"Async workflow execution failed: {result_container['error']}")
        elif result_container["result"]:
            result = result_container["result"]
            
            # Update session state tracking
            execution_duration = time.time() - self.session_state.current_async_execution.get("start_time", time.time())
            self.session_state.last_request_duration = execution_duration
            self.session_state.conversation_total_duration = self.session_state.get("conversation_total_duration", 0) + execution_duration
            
            # Extract execution statistics from the result
            if isinstance(result, dict):
                # Update token counts from multiple sources
                total_tokens_in = 0
                total_tokens_out = 0
                
                # Try direct result keys first
                if "total_tokens_in" in result:
                    total_tokens_in = result.get("total_tokens_in", 0)
                    total_tokens_out = result.get("total_tokens_out", 0)
                # Then try shared_state
                elif "shared_state" in result:
                    shared_state = result["shared_state"]
                    total_tokens_in = shared_state.get("total_tokens_in", 0)
                    total_tokens_out = shared_state.get("total_tokens_out", 0)
                # Finally try workflow-specific result
                else:
                    workflow_result = result.get("unguided_mimic_result", {})
                    total_tokens_in = workflow_result.get("total_tokens_in", 0)
                    total_tokens_out = workflow_result.get("total_tokens_out", 0)
                
                # Also check if we have token counts from the workflow_complete event
                current_execution = self.session_state.get("current_async_execution", {})
                if current_execution.get("workflow_complete_data"):
                    workflow_data = current_execution["workflow_complete_data"]
                    if workflow_data.get("total_tokens_in"):
                        total_tokens_in = workflow_data.get("total_tokens_in", 0)
                        total_tokens_out = workflow_data.get("total_tokens_out", 0)
                        bound_logger.info(f"Using token counts from workflow_complete event: in={total_tokens_in}, out={total_tokens_out}")
                
                self.session_state.last_request_tokens_in = total_tokens_in
                self.session_state.last_request_tokens_out = total_tokens_out
                self.session_state.conversation_total_tokens_in = self.session_state.get("conversation_total_tokens_in", 0) + total_tokens_in
                self.session_state.conversation_total_tokens_out = self.session_state.get("conversation_total_tokens_out", 0) + total_tokens_out
                
                bound_logger.info(f"Updated token counts: in={total_tokens_in}, out={total_tokens_out}")
            
            # Extract the final response from the workflow result
            if isinstance(result, dict):
                # Prefer the last assistant message from final_messages/execution_state.messages
                final_message = ""
                try:
                    def pick_last_assistant(msgs):
                        if isinstance(msgs, list):
                            for m in reversed(msgs):
                                if isinstance(m, dict) and m.get("role") == "assistant" and m.get("content"):
                                    return m.get("content")
                        return None
                    # 1) direct final_messages from result
                    fm = pick_last_assistant(result.get("final_messages"))
                    # 2) messages inside execution_state
                    if not fm:
                        exec_state = result.get("execution_state") or {}
                        fm = pick_last_assistant(exec_state.get("messages"))
                    # 3) shared_state fallback containers if any
                    if not fm and isinstance(result.get("shared_state"), dict):
                        fm = pick_last_assistant(result["shared_state"].get("final_messages"))
                    # 4) last resort: generic message key if it actually contains text
                    if not fm:
                        generic = result.get("message")
                        if isinstance(generic, str) and generic.strip():
                            fm = generic
                    final_message = fm or ""
                except Exception:
                    final_message = result.get("message", "")
                if final_message:
                    # Get the current user_request_id from the execution state
                    current_execution = self.session_state.get("current_async_execution", {})
                    user_request_id = current_execution.get("user_request_id")
                    
                    if user_request_id:
                        # Add the final response to the chat history
                        from models.message import MessageData
                        final_response = MessageData(
                            role="assistant",
                            content=final_message,
                            user_request_id=user_request_id,
                            workflow_event=False  # This is the final response, not a workflow event
                        )
                        self.event_handler.store_message(final_response)
                        bound_logger.info(f"Added final workflow response to chat history")
                        # Also ensure aggregated_content includes the final response for full bubble display
                        try:
                            agg_map = self.session_state.get("aggregated_content", {})
                            if user_request_id not in agg_map:
                                agg_map[user_request_id] = []
                            # Remove earlier partials that are substrings/prefixes of the final message to avoid duplication
                            cleaned_parts = []
                            for part in agg_map[user_request_id]:
                                if isinstance(part, str) and part.strip():
                                    if part.strip() == final_message.strip() or part.strip() in final_message:
                                        continue
                                    cleaned_parts.append(part)
                            # Append final message if not already present
                            if final_message and (not cleaned_parts or cleaned_parts[-1] != final_message):
                                cleaned_parts.append(final_message)
                            agg_map[user_request_id] = cleaned_parts
                            self.session_state.aggregated_content = agg_map
                            # Update live response to reflect the final content
                            self._update_live_response(user_request_id)
                            # Signal UI to rerun on next cycle
                            self.session_state.has_new_events = True
                        except Exception as agg_err:
                            bound_logger.warning(f"Failed to append final response to aggregated_content: {agg_err}")
                        
                        bound_logger.info(f"Async workflow completed successfully in {execution_duration:.1f}s")
        else:
            st.error("Async workflow execution failed or returned unexpected result")
    
    def _cleanup_execution_state(self, workflow_name: str, user_req_id: str) -> None:
        """Clean up execution state for this workflow and request."""
        current_execution = self.session_state.get("current_async_execution", {})
        if (current_execution.get("workflow_name") == workflow_name and 
            current_execution.get("user_request_id") == user_req_id):
            self.session_state.current_async_execution = {
                "workflow_name": workflow_name,
                "user_request_id": user_req_id,
                "executing": False,
                "completed": True
            }
        
        self.session_state.stop_requested = False
        # Allow prompt box to reappear immediately after completion
        self.session_state.llm_executing = False
        # Trigger a UI rerun to restore input box promptly
        try:
            self.session_state.has_new_events = True
        except Exception:
            pass

    def _update_live_response(self, user_request_id: str) -> None:
        """Render the live aggregated response bubble from session state."""
        if not self.live_response_container:
            return
        # Get aggregated parts and summary
        agg_map = self.session_state.get("aggregated_content", {})
        parts = agg_map.get(user_request_id, []) or []
        # Filter empty/noise
        parts = [p for p in parts if isinstance(p, str) and p.strip()]
        # Build content (assistant-only narrative). If no parts yet, still show status-only bubble.
        # Do not truncate; let markdown render full content.
        full_content = "\n\n".join(parts) if parts else ""
        logger.info(f"Executor: agg parts count={len(parts)} for {user_request_id}")
        # Also get compact execution steps
        steps_map = self.session_state.get("aggregated_steps", {})
        steps = steps_map.get(user_request_id, []) or []
        summary = self.event_handler.get_workflow_summary(user_request_id) or {}
        # Build a compact status line
        status_line_bits = []
        if summary.get("current_event"):
            status_line_bits.append(f"🔄 {summary['current_event']}")
        if summary.get("total_tokens", 0) > 0:
            status_line_bits.append(f"📊 {summary['total_tokens']:,} tokens")
        if summary.get("tools_executed", 0) > 0:
            status_line_bits.append(f"🔧 {summary['tools_executed']} tools")
        if summary.get("messages_in_context", 0) > 0:
            status_line_bits.append(f"💬 {summary['messages_in_context']} ctx msgs")
        if summary.get("provider") and summary.get("model"):
            status_line_bits.append(f"🤖 {summary['provider']}/{summary['model']}")
        if summary.get("duration", 0) > 0:
            status_line_bits.append(f"⏱️ {summary['duration']:.1f}s")
        status_md = " | ".join(status_line_bits)
        
        # Render inside a single assistant bubble
        with self.live_response_container.container():
            with st.chat_message("assistant"):
                # Ordered execution steps first
                if steps:
                    st.markdown("\n".join([f"- {s}" for s in steps]))
                    st.markdown("---")
                # Then assistant narrative parts
                if full_content.strip():
                    st.markdown(full_content)
                if status_md:
                    st.markdown("---")
                    st.caption(status_md)
