"""
Async Unguided Mimic Workflow for NiFi MCP.

This workflow replicates the existing unguided LLM execution loop behavior
using async nodes with real-time event emission for UI updates.
"""

import json
import time
import uuid
import sys
import os
from typing import Dict, Any, List, Optional, TYPE_CHECKING

# Import PocketFlow async classes from the installed package
try:
    from pocketflow import AsyncFlow
except ImportError:
    # Fallback for development - try to import from local examples
    pocketflow_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'docs', 'libdocs', 'pocketflow examples')
    if os.path.exists(pocketflow_path):
        sys.path.append(pocketflow_path)
        import importlib.util
        spec = importlib.util.spec_from_file_location("pocketflow_init", os.path.join(pocketflow_path, "__init__.py"))
        pocketflow_init = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pocketflow_init)
        AsyncFlow = pocketflow_init.AsyncFlow
    else:
        raise ImportError("PocketFlow package not found and local examples not available")

from loguru import logger
from ..nodes.async_nifi_node import AsyncNiFiWorkflowNode
from ..registry import WorkflowDefinition, register_workflow
from ..core.event_system import emit_workflow_start, EventTypes
from nifi_chat_ui.mcp_handler import get_available_tools

# Type checking imports
if TYPE_CHECKING:
    from pocketflow import AsyncFlow as AsyncFlowType
else:
    AsyncFlowType = Any


class AsyncInitializeExecutionNode(AsyncNiFiWorkflowNode):
    """
    Async version of initialize execution node with real-time events.
    """
    
    def __init__(self):
        super().__init__(
            name="async_initialize_execution",
            description="Set up execution context and run async LLM iterations with real-time updates",
            allowed_phases=["Review", "Creation", "Modification", "Operation"]
        )
        # Single node workflow - no successors needed
        self.successors = {}
        
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare execution context with clean golden context only."""
        # First, call parent prep_async to initialize Golden Context from shared state
        await super().prep_async(shared)
        
        # Get current user prompt (only new input)
        user_prompt = shared.get("user_prompt", "")
        
        # Use the messages that were already prepared in the API
        context_messages = shared.get("messages", [])
        self.bound_logger.info(f"Using {len(context_messages)} messages from API preparation")
        
        # Save user message to database (LLM conversation format) if it's not already saved
        if user_prompt:
            try:
                from api.storage import storage
                storage.save_message(
                    request_id=shared.get("user_request_id"),
                    role="user",
                    content=user_prompt,
                    provider=shared.get("provider", "unknown"),
                    model_name=shared.get("model_name", "unknown"),
                    nifi_server_id=shared.get("selected_nifi_server_id"),
                    metadata={
                        "workflow_id": "unguided",
                        "step_id": "async_initialize_execution",
                        "event_type": "user_input",
                        "format": "llm_conversation"  # Mark as LLM conversation format
                    }
                )
                self.bound_logger.info(f"Saved user message (LLM format) to database for request {shared.get('user_request_id')}")
            except Exception as e:
                self.bound_logger.error(f"Failed to save user message to database: {e}")
        
        # Add user prompt to context if provided
        if user_prompt:
            context_messages = context_messages + [{"role": "user", "content": user_prompt}]
            self.bound_logger.info(f"Added user prompt to context messages ({len(context_messages)} total)")
        
        # Extract required context from shared state
        return {
            "provider": shared.get("provider"),
            "model_name": shared.get("model_name"),
            "system_prompt": shared.get("system_prompt"),
            "user_request_id": shared.get("user_request_id"),
            "messages": context_messages,  # Use clean LLM context only
            "selected_nifi_server_id": shared.get("selected_nifi_server_id"),
            "max_loop_iterations": shared.get("max_loop_iterations", 10),
            "workflow_id": "unguided",
            "step_id": "async_initialize_execution",
            "max_tokens_limit": shared.get("max_tokens_limit", 32000),
            "auto_prune_history": shared.get("auto_prune_history", True)  # Pass through auto-prune setting
        }
        
    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        """Execute async LLM workflow with real-time events."""
        try:
            # Validate required fields
            required_fields = ["provider", "model_name", "system_prompt", "user_request_id", "messages", "selected_nifi_server_id"]
            missing_fields = [field for field in required_fields if not prep_res.get(field)]
            
            if missing_fields:
                raise ValueError(f"Missing required context fields: {missing_fields}")
            
            # Handle messages parsing (in case they're serialized)
            initial_messages = prep_res["messages"]
            if isinstance(initial_messages, str):
                try:
                    initial_messages = json.loads(initial_messages)
                    self.bound_logger.info(f"Successfully parsed messages string to list with {len(initial_messages)} items")
                except json.JSONDecodeError as e:
                    self.bound_logger.error(f"Failed to parse messages string as JSON: {e}")
                    raise ValueError(f"Messages field is a string but not valid JSON: {e}")
            
            # Initialize execution state
            execution_state = {
                "provider": prep_res["provider"],
                "model_name": prep_res["model_name"],
                "system_prompt": prep_res["system_prompt"],
                "user_request_id": prep_res["user_request_id"],
                "nifi_server_id": prep_res["selected_nifi_server_id"],
                "messages": initial_messages.copy() if initial_messages else [],
                "loop_count": 0,
                "max_iterations": prep_res.get("max_loop_iterations", 10),
                "tool_results": [],
                "request_tokens_in": 0,
                "request_tokens_out": 0,
                "execution_complete": False,
                "workflow_id": "unguided",
                "step_id": "async_initialize_execution",
                "auto_prune_history": prep_res.get("auto_prune_history", True),  # Add auto-prune setting
                "max_tokens_limit": prep_res.get("max_tokens_limit", 32000),  # Add max tokens limit setting
                "start_time": time.time(),  # Track start time for elapsed seconds calculation
                "messages_in_request": len(initial_messages) if initial_messages else 0  # Track message count
            }
            
            # Apply smart pruning to initial context if auto-prune is enabled
            if execution_state.get("auto_prune_history", True):
                pruned_messages = self.apply_smart_pruning_to_messages(
                    execution_state["messages"], 
                    execution_state
                )
                execution_state["messages"] = pruned_messages
                self.bound_logger.info(f"Applied smart pruning to initial context: {len(initial_messages)} -> {len(pruned_messages)} messages")
            
            # Emit workflow start event
            await emit_workflow_start(
                execution_state["workflow_id"], 
                execution_state["step_id"], 
                {
                    "provider": execution_state["provider"],
                    "model": execution_state["model_name"],
                    "initial_message_count": len(execution_state["messages"]),
                    "max_iterations": execution_state["max_iterations"]
                },
                execution_state["user_request_id"]
            )
            
            self.bound_logger.info(f"Starting async LLM execution loop with provider: {execution_state['provider']}, model: {execution_state['model_name']}")
            
            # Run the async execution loop
            final_results = await self._run_async_execution_loop(execution_state)
            
            return {
                "status": "success",
                "execution_state": final_results["execution_state"],
                "final_messages": final_results["final_messages"],  # Use final_messages key
                "total_tokens_in": final_results["total_tokens_in"],
                "total_tokens_out": final_results["total_tokens_out"],
                "loop_count": final_results["loop_count"],
                "task_complete": final_results["task_complete"],
                "max_iterations_reached": final_results["max_iterations_reached"],
                "tool_calls_executed": final_results["tool_calls_executed"],
                "executed_tools": final_results["executed_tools"],
                "message": f"Async execution completed with {final_results['loop_count']} iterations"
            }
            
        except Exception as e:
            # Emit workflow error event
            await self.event_emitter.emit(EventTypes.WORKFLOW_ERROR, {
                "error": str(e),
                "step": "async_initialize_execution"
            }, "unguided", "async_initialize_execution", prep_res.get("user_request_id"))
            
            self.bound_logger.error(f"Error in async execution: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": "Failed to execute async LLM workflow"
            }
    
    async def _run_async_execution_loop(self, execution_state: Dict[str, Any]) -> Dict[str, Any]:
        """Run the complete async LLM execution loop with real-time events."""
        task_complete = False
        max_iterations_reached = False
        tool_calls_executed = 0
        executed_tools = set()
        consecutive_failures = 0
        max_consecutive_failures = 3
        
        self.bound_logger.info("Starting async LLM execution loop")
        
        # Emit workflow start event
        await self.event_emitter.emit(EventTypes.WORKFLOW_START, {
            "workflow_id": execution_state.get("workflow_id", "unguided"),
            "step_id": "async_initialize_execution",
            "status": "started"
        }, execution_state.get("workflow_id", "unguided"), "async_initialize_execution", execution_state.get("user_request_id"))
        
        # Prepare tools once
        formatted_tools = self.prepare_tools(execution_state)
        
        while not task_complete and not max_iterations_reached:
            execution_state["loop_count"] += 1
            loop_count = execution_state["loop_count"]
            max_iterations = execution_state.get("max_iterations", 10)
            
            self.bound_logger.info(f"Async LLM iteration {loop_count}/{max_iterations}")
            
            # Check iteration limit
            if loop_count >= max_iterations:
                max_iterations_reached = True
                self.bound_logger.info(f"Maximum iterations ({max_iterations}) reached")
                break
            
            # Generate workflow context action ID
            workflow_id = execution_state.get("workflow_id", "unguided")
            step_id = f"async_initialize_execution_iter_{loop_count}"
            llm_action_id = f"wf-{workflow_id}-{step_id}-llm-{uuid.uuid4()}"
            
            # Update step_id in execution state for events
            execution_state["step_id"] = step_id
            
            # Apply smart pruning to messages before LLM call
            pruned_messages = self.apply_smart_pruning_to_messages(
                execution_state["messages"], 
                execution_state
            )
            execution_state["messages"] = pruned_messages
            
            # Call LLM asynchronously with events
            response_data = await self.call_llm_async(
                pruned_messages, 
                formatted_tools, 
                execution_state, 
                llm_action_id
            )
            
            # Check if we got a valid response
            if response_data.get("error"):
                error_msg = response_data["error"]
                self.bound_logger.error(f"Async LLM call failed: {error_msg}")
                execution_state["error_occurred"] = True
                break
            
            # Update token counts
            execution_state["request_tokens_in"] += response_data.get("token_count_in", 0)
            execution_state["request_tokens_out"] += response_data.get("token_count_out", 0)
            
            # Add assistant message to context
            llm_content = response_data.get("content")
            tool_calls = response_data.get("tool_calls")
            

            
            assistant_message = {"role": "assistant"}
            if llm_content:
                assistant_message["content"] = llm_content
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            
            # Add workflow context information for UI display
            assistant_message.update({
                "token_count_in": response_data.get("token_count_in", 0),
                "token_count_out": response_data.get("token_count_out", 0),
                "workflow_id": execution_state.get("workflow_id", "unguided"),
                "step_id": step_id,
                "action_id": llm_action_id  # Use the workflow context action ID
            })
            
            # Add assistant message to context with automatic golden context management
            await self.add_workflow_message(assistant_message, execution_state)
            
            # Save assistant message to database (LLM conversation format)
            try:
                from api.storage import storage
                storage.save_message(
                    request_id=execution_state["user_request_id"],
                    role="assistant",
                    content=llm_content or "",
                    provider=execution_state.get("provider", "unknown"),
                    model_name=execution_state.get("model_name", "unknown"),
                    nifi_server_id=execution_state.get("nifi_server_id"),
                    metadata={
                        "tool_calls": tool_calls or [],
                        "token_count_in": response_data.get("token_count_in", 0),
                        "token_count_out": response_data.get("token_count_out", 0),
                        "workflow_id": execution_state.get("workflow_id"),
                        "step_id": step_id,
                        "action_id": llm_action_id,
                        "format": "llm_conversation"  # Mark as LLM conversation format
                    }
                )
                self.bound_logger.info(f"Saved assistant message (LLM format) to database for request {execution_state['user_request_id']}")
            except Exception as e:
                self.bound_logger.error(f"Failed to save assistant message to database: {e}")
            
            # Process tool calls if present
            if tool_calls:
                self.bound_logger.info(f"Processing {len(tool_calls)} tool calls asynchronously")
                
                # Log tool call details for debugging
                tool_names = [tc.get("function", {}).get("name", "unknown") for tc in tool_calls]
                self.bound_logger.info(f"Tool calls: {tool_names}")
                
                # Execute tool calls asynchronously with events, passing LLM action_id for correlation
                tool_results = await self.execute_tool_calls_async(tool_calls, execution_state, llm_action_id)
                
                # Check if all tool calls failed
                failed_tools = 0
                for tool_result in tool_results:
                    # Check if the tool result indicates an error
                    content = str(tool_result.get("content", ""))
                    if (content.lower().startswith("error:") or 
                        "error" in tool_result.get("name", "").lower() or
                        tool_result.get("content") == "Error"):
                        failed_tools += 1
                    await self.add_workflow_message(tool_result, execution_state)
                    
                    # Save tool result to database (LLM conversation format)
                    try:
                        from api.storage import storage
                        storage.save_message(
                            request_id=execution_state["user_request_id"],
                            role="tool",
                            content=str(tool_result.get("content", "")),
                            provider=execution_state.get("provider", "unknown"),
                            model_name=execution_state.get("model_name", "unknown"),
                            nifi_server_id=execution_state.get("nifi_server_id"),
                            metadata={
                                "tool_name": tool_result.get("name"),
                                "tool_call_id": tool_result.get("tool_call_id"),
                                "workflow_id": execution_state.get("workflow_id"),
                                "step_id": step_id,
                                "action_id": llm_action_id,
                                "format": "llm_conversation"  # Mark as LLM conversation format
                            }
                        )
                        self.bound_logger.info(f"Saved tool result (LLM format) to database for request {execution_state['user_request_id']}")
                    except Exception as e:
                        self.bound_logger.error(f"Failed to save tool result to database: {e}")
                    
                    # Track consecutive failures to prevent infinite loops
                if failed_tools == len(tool_calls):
                    consecutive_failures += 1
                    self.bound_logger.warning(f"All {len(tool_calls)} tool calls failed. Consecutive failures: {consecutive_failures}")
                    
                    if consecutive_failures >= max_consecutive_failures:
                        self.bound_logger.error(f"Too many consecutive tool failures ({consecutive_failures}). Stopping execution.")
                        execution_state["error_occurred"] = True  # Mark that an error occurred
                        task_complete = True
                        break
                else:
                    consecutive_failures = 0  # Reset on any success
                
                tool_calls_executed += len(tool_calls)
                executed_tools.update([tc.get("function", {}).get("name") for tc in tool_calls])
                
                # Continue the loop to get LLM response to tool results
                continue
            
            # No tool calls - check if task is complete
            if llm_content and any(phrase in llm_content.lower() for phrase in [
                "task completed", "task complete", "completed successfully", 
                "finished", "done", "no further", "objective achieved"
            ]) or (llm_content and "TASK COMPLETE" in llm_content):
                task_complete = True
                self.bound_logger.info("LLM indicated task completion")
            else:
                # If no tool calls and no completion indication, assume complete
                task_complete = True
                self.bound_logger.info("No tool calls requested, assuming task complete")
        
        # Check if status report is needed and generate it as the final step
        final_content = llm_content or ""
        final_tokens_in = 0
        final_tokens_out = 0
        
        if not execution_state.get("error_occurred", False):
            needs_status_report = await self._check_status_report_needed(
                execution_state, task_complete, max_iterations_reached, llm_content or ""
            )
            
            if needs_status_report:
                # Generate status report as the final step of the workflow
                status_content, status_tokens_in, status_tokens_out = await self._generate_status_report_content(execution_state)
                if status_content:
                    final_content = status_content
                    final_tokens_in = status_tokens_in
                    final_tokens_out = status_tokens_out
                    
                    # Update cumulative token counts
                    execution_state["request_tokens_in"] += final_tokens_in
                    execution_state["request_tokens_out"] += final_tokens_out
        
        # Emit workflow completion event with final content
        await self.event_emitter.emit(EventTypes.WORKFLOW_COMPLETE, {
            "loop_count": execution_state["loop_count"],
            "total_tokens_in": execution_state["request_tokens_in"],
            "total_tokens_out": execution_state["request_tokens_out"],
            "tool_calls_executed": tool_calls_executed,
            "executed_tools": list(executed_tools),
            "task_complete": task_complete,
            "max_iterations_reached": max_iterations_reached,
            "consecutive_failures": consecutive_failures,
            "stopped_due_to_failures": consecutive_failures >= max_consecutive_failures,
            "error_occurred": execution_state.get("error_occurred", False),
            "model_name": execution_state.get("model_name", "unknown"),
            "provider": execution_state.get("provider", "unknown"),
            "messages_in_request": execution_state.get("messages_in_request", 0),
            "elapsed_seconds": int(time.time() - execution_state.get("start_time", time.time())),
            "final_content": final_content,
            "final_tokens_in": final_tokens_in,
            "final_tokens_out": final_tokens_out
        }, execution_state["workflow_id"], execution_state["step_id"], execution_state["user_request_id"])
        
        return {
            "execution_state": execution_state,
            "final_messages": execution_state["messages"],  # Return as final_messages for UI compatibility
            "total_tokens_in": execution_state["request_tokens_in"],
            "total_tokens_out": execution_state["request_tokens_out"],
            "loop_count": execution_state["loop_count"],
            "task_complete": task_complete,
            "max_iterations_reached": max_iterations_reached,
            "tool_calls_executed": tool_calls_executed,
            "executed_tools": list(executed_tools),
            "consecutive_failures": consecutive_failures,
            "stopped_due_to_failures": consecutive_failures >= max_consecutive_failures
        }
    
    async def post_async(self, shared, prep_res, exec_res):
        """Post-process execution results."""
        # Update shared state with final results
        shared["workflow_execution_complete"] = True
        shared["final_execution_state"] = exec_res.get("execution_state", {})
        shared["final_messages"] = exec_res.get("final_messages", [])
        
        self.bound_logger.info("Workflow execution completed successfully")
        
        return "default"  # Workflow complete
    

    
    async def _check_status_report_needed(self, execution_state: Dict[str, Any], task_complete: bool, max_iterations_reached: bool, last_llm_content: str) -> bool:
        """Check if a status report is needed based on completion conditions."""
        # Status report is needed if:
        # 1. Max iterations reached AND no comprehensive summary provided, OR
        # 2. Task completed but no substantial content with TASK COMPLETE
        
        if max_iterations_reached:
            # Check if LLM provided comprehensive summary
            if last_llm_content and "TASK COMPLETE" in last_llm_content:
                parts = last_llm_content.split("TASK COMPLETE")
                if len(parts) > 1 and len(parts[0].strip()) > 200:
                    self.bound_logger.info("LLM already provided comprehensive summary - skipping status report")
                    return False
                else:
                    self.bound_logger.info("Max iterations reached but LLM summary insufficient - requesting status report")
                    return True
            else:
                self.bound_logger.info("Max iterations reached without TASK COMPLETE - requesting status report")
                return True
        
        # If task completed normally, check if we need a status report
        if task_complete and last_llm_content:
            if "TASK COMPLETE" in last_llm_content:
                parts = last_llm_content.split("TASK COMPLETE")
                if len(parts) > 1 and len(parts[0].strip()) > 200:
                    self.bound_logger.info("Task completed with comprehensive summary - skipping status report")
                    return False
        
        return False
    
    async def _generate_status_report_content(self, execution_state: Dict[str, Any]) -> tuple[str, int, int]:
        """Generate status report content as the final step of the workflow."""
        try:
            self.bound_logger.info("Generating status report content as final workflow step")
            
            # Get conversation history from database for status report
            try:
                from api.storage import storage
                user_request_id = execution_state.get("user_request_id")
                if user_request_id:
                    conversation_history = storage.get_llm_conversation_history(request_id=user_request_id)
                    self.bound_logger.info(f"Status report: Retrieved {len(conversation_history)} messages for request {user_request_id}")
                else:
                    self.bound_logger.warning("No user_request_id found in execution state")
                    conversation_history = []
            except Exception as e:
                self.bound_logger.error(f"Failed to get conversation history from database: {e}")
                conversation_history = execution_state.get("messages", [])
                self.bound_logger.warning(f"Using execution state messages as fallback: {len(conversation_history)} messages")
            
            if not conversation_history:
                self.bound_logger.warning("No execution state messages found - this should not happen")
                conversation_history = []
            
            # Filter messages and limit to recent context
            self.bound_logger.info(f"Status report: Processing {len(conversation_history)} messages from conversation history")
            
            # Use all messages from the specific conversation
            valid_messages = conversation_history.copy()
            self.bound_logger.info(f"Status report: Using all {len(valid_messages)} messages from conversation, smart pruning will handle token limits")

            # Convert database format to LLM format before smart pruning
            try:
                from nifi_chat_ui.utils.history_utils import convert_db_messages_to_llm, summarize_and_validate
            except Exception:
                # Relative import fallback
                from ...nifi_chat_ui.utils.history_utils import convert_db_messages_to_llm, summarize_and_validate  # type: ignore

            valid_messages = convert_db_messages_to_llm(valid_messages, logger=self.bound_logger)
            summarize_and_validate(valid_messages, logger=self.bound_logger, tag="status_preprune")
            
            # Apply smart pruning to the context messages using centralized function
            valid_messages = self.apply_smart_pruning_to_messages(valid_messages, execution_state)
            
            # Create status report prompt
            status_prompt = f"""
I've reached the maximum action limit ({execution_state.get('loop_count', 0)} actions) for this request. 
Please provide a brief status report:

1. What have I accomplished so far?
2. What challenges or difficulties did I encounter?
3. What would be my next planned actions if I could continue?
4. Any important notes or recommendations for the user?

Keep this concise but informative.
"""
            
            # Prepare messages for status report
            status_messages = valid_messages + [
                {"role": "user", "content": status_prompt}
            ]
            
            # Call LLM for status report (no tools needed)
            chat_manager = self.get_chat_manager()
            
            # Use the original system prompt for context
            original_system_prompt = execution_state.get("system_prompt", "")
            
            status_response = chat_manager.get_llm_response(
                messages=status_messages,
                system_prompt=original_system_prompt,
                provider=execution_state.get("provider"),
                model_name=execution_state.get("model_name"),
                user_request_id=execution_state.get("user_request_id"),
                tools=None  # No tools for status report
            )
            
            if status_response and status_response.get("content"):
                # Format the status report content
                formatted_content = f"### 📋 Status Report\n\n{status_response['content']}"
                
                # Add status report message to workflow
                status_message = {
                    "role": "assistant",
                    "content": formatted_content,
                    "is_status_report": True,
                    "token_count_in": status_response.get("token_count_in", 0),
                    "token_count_out": status_response.get("token_count_out", 0),
                    "action_id": str(uuid.uuid4()),
                    "workflow_id": execution_state.get("workflow_id", "unguided"),
                    "step_id": execution_state.get("step_id", "async_initialize_execution")
                }
                
                # Add status report to workflow with automatic golden context management
                await self.add_workflow_message(status_message, execution_state)
                
                self.bound_logger.info("Status report content generated successfully")
                return (
                    formatted_content,
                    status_response.get("token_count_in", 0),
                    status_response.get("token_count_out", 0)
                )
            else:
                self.bound_logger.warning("Status report generation failed - no content received")
                return ("", 0, 0)
                
        except Exception as e:
            self.bound_logger.error(f"Error generating status report content: {e}")
            return ("", 0, 0)


    def _convert_database_to_llm_format(self, database_messages: List[Dict]) -> List[Dict]:
        """
        Convert database format messages to LLM format for smart pruning.
        
        Database messages have this structure:
        {
          "id": 765,
          "request_id": "...",
          "role": "assistant",
          "content": "",
          "metadata": {
            "tool_calls": [...],
            "format": "llm_conversation"
          }
        }
        
        LLM format expects:
        {
          "role": "assistant",
          "content": "",
          "tool_calls": [...]
        }
        
        Args:
            database_messages: Messages in database format
            
        Returns:
            Messages converted to LLM format
        """
        if not database_messages:
            return []
        
        try:
            llm_format_messages = []
            
            for i, message in enumerate(database_messages):
                role = message.get("role")
                
                if role == "system":
                    # System messages are already in correct format
                    llm_format_messages.append({
                        "role": "system",
                        "content": message.get("content", "")
                    })
                    
                elif role == "user":
                    # User messages are already in correct format
                    llm_format_messages.append({
                        "role": "user",
                        "content": message.get("content", "")
                    })
                    
                elif role == "assistant":
                    # Convert assistant messages
                    llm_message = {
                        "role": "assistant",
                        "content": message.get("content", "")
                    }
                    
                    # Check if tool_calls are in metadata
                    metadata = message.get("metadata", {})
                    if isinstance(metadata, dict) and "tool_calls" in metadata:
                        tool_calls = metadata["tool_calls"]
                        if tool_calls:
                            llm_message["tool_calls"] = tool_calls
                            self.bound_logger.debug(f"Converted assistant message {i}: {len(tool_calls)} tool calls")
                    
                    llm_format_messages.append(llm_message)
                    
                elif role == "tool":
                    # Convert tool messages
                    llm_message = {
                        "role": "tool",
                        "content": message.get("content", "")
                    }
                    
                    # Check if tool_call_id is in metadata
                    metadata = message.get("metadata", {})
                    if isinstance(metadata, dict) and "tool_call_id" in metadata:
                        llm_message["tool_call_id"] = metadata["tool_call_id"]
                        self.bound_logger.debug(f"Converted tool message {i}: tool_call_id={metadata['tool_call_id']}")
                    else:
                        # Try to find tool_call_id in the message itself
                        tool_call_id = message.get("tool_call_id")
                        if tool_call_id:
                            llm_message["tool_call_id"] = tool_call_id
                            self.bound_logger.debug(f"Converted tool message {i}: tool_call_id={tool_call_id}")
                        else:
                            self.bound_logger.warning(f"Tool message {i} missing tool_call_id, skipping")
                            continue
                    
                    llm_format_messages.append(llm_message)
                    
                else:
                    # Unknown role - skip it
                    self.bound_logger.warning(f"Skipping message {i} with unknown role: {role}")
                    continue
            
            self.bound_logger.info(f"Database to LLM format conversion: {len(database_messages)} -> {len(llm_format_messages)} messages")
            return llm_format_messages
            
        except Exception as e:
            self.bound_logger.error(f"Error during database to LLM format conversion: {e}")
            # Return original messages on error to avoid breaking the workflow
            return database_messages


def create_unguided_workflow() -> Any:
    """Create the async unguided mimic workflow."""
    # Single node workflow
    initialize_node = AsyncInitializeExecutionNode()
    
    # Create async flow
    return AsyncFlow(start=initialize_node)


# Register the async workflow
unguided_definition = WorkflowDefinition(
    name="unguided",
    description="Unguided workflow with minimal logic for unfiltered communication with LLM, Tools, and UI",
    create_workflow_func=create_unguided_workflow,
    display_name="Unguided",
    category="Unguided",
    enabled=True
)

register_workflow(unguided_definition) 