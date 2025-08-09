"""
Async Unguided Mimic Workflow for NiFi MCP.

This workflow replicates the existing unguided LLM execution loop behavior
using async nodes with real-time event emission for UI updates.
"""

import json
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
        """Prepare execution context."""
        # Extract required context from shared state
        return {
            "provider": shared.get("provider"),
            "model_name": shared.get("model_name"),
            "system_prompt": shared.get("system_prompt"),
            "user_request_id": shared.get("user_request_id"),
            "messages": shared.get("messages", []),
            "selected_nifi_server_id": shared.get("selected_nifi_server_id"),
            "max_loop_iterations": shared.get("max_loop_iterations", 10),
            "workflow_id": "async_unguided_mimic",
            "step_id": "async_initialize_execution"
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
                "workflow_id": "async_unguided_mimic",
                "step_id": "async_initialize_execution"
            }
            
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
            }, "async_unguided_mimic", "async_initialize_execution", prep_res.get("user_request_id"))
            
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
            "workflow_id": execution_state.get("workflow_id", "async_unguided_mimic"),
            "step_id": "async_initialize_execution",
            "status": "started"
        }, execution_state.get("workflow_id", "async_unguided_mimic"), "async_initialize_execution", execution_state.get("user_request_id"))
        
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
            workflow_id = execution_state.get("workflow_id", "async_unguided_mimic")
            step_id = f"async_initialize_execution_iter_{loop_count}"
            llm_action_id = f"wf-{workflow_id}-{step_id}-llm-{uuid.uuid4()}"
            
            # Update step_id in execution state for events
            execution_state["step_id"] = step_id
            
            # Call LLM asynchronously with events
            response_data = await self.call_llm_async(
                execution_state["messages"], 
                formatted_tools, 
                execution_state, 
                llm_action_id
            )
            
            if response_data.get("error"):  # Changed from "error" in response_data to response_data.get("error")
                self.bound_logger.error(f"Async LLM call failed: {response_data['error']}")
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
                "workflow_id": execution_state.get("workflow_id", "async_unguided_mimic"),
                "step_id": step_id,
                "action_id": llm_action_id  # Use the workflow context action ID
            })
            
            # Add assistant message to context with event
            await self.add_message_to_context_async(assistant_message, execution_state)
            
            # Process tool calls if present
            if tool_calls:
                self.bound_logger.info(f"Processing {len(tool_calls)} tool calls asynchronously")
                
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
                    await self.add_message_to_context_async(tool_result, execution_state)
                
                # Track consecutive failures to prevent infinite loops
                if failed_tools == len(tool_calls):
                    consecutive_failures += 1
                    self.bound_logger.warning(f"All {len(tool_calls)} tool calls failed. Consecutive failures: {consecutive_failures}")
                    
                    if consecutive_failures >= max_consecutive_failures:
                        self.bound_logger.error(f"Too many consecutive tool failures ({consecutive_failures}). Stopping execution.")
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
        
        # Emit workflow completion event
        await self.event_emitter.emit(EventTypes.WORKFLOW_COMPLETE, {
            "loop_count": execution_state["loop_count"],
            "total_tokens_in": execution_state["request_tokens_in"],
            "total_tokens_out": execution_state["request_tokens_out"],
            "tool_calls_executed": tool_calls_executed,
            "executed_tools": list(executed_tools),
            "task_complete": task_complete,
            "max_iterations_reached": max_iterations_reached,
            "consecutive_failures": consecutive_failures,
            "stopped_due_to_failures": consecutive_failures >= max_consecutive_failures
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
        
        return "default"  # Workflow complete


def create_async_unguided_mimic_workflow() -> Any:
    """Create the async unguided mimic workflow."""
    # Single node workflow
    initialize_node = AsyncInitializeExecutionNode()
    
    # Create async flow
    return AsyncFlow(start=initialize_node)


# Register the async workflow
async_unguided_mimic_definition = WorkflowDefinition(
    name="async_unguided_mimic",
    description="Async version of unguided LLM workflow with real-time progress updates",
    create_workflow_func=create_async_unguided_mimic_workflow,
    display_name="Basic",
    category="Basic",
    phases=["Review", "Creation", "Modification", "Operation"],
    is_async=True  # Mark as async workflow
)

register_workflow(async_unguided_mimic_definition) 