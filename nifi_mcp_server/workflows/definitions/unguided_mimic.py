"""
Unguided Mimic Workflow for NiFi MCP.

This workflow replicates the existing unguided LLM execution loop behavior
using multiple nodes to better demonstrate PocketFlow's capabilities.
"""

import json
import uuid
from typing import Dict, Any, List, Optional
from loguru import logger

from ..nodes.nifi_node import NiFiWorkflowNode
from ..registry import WorkflowDefinition, register_workflow
from nifi_chat_ui.mcp_handler import get_available_tools, execute_mcp_tool
from config.logging_setup import request_context

# Function to get chat manager instance
def get_chat_manager():
    """Get the global chat manager instance from the server."""
    try:
        from nifi_mcp_server.server import _chat_manager
        return _chat_manager
    except ImportError:
        logger.error("Chat manager not available")
        return None


class InitializeExecutionNode(NiFiWorkflowNode):
    """
    Initialize execution context and prepare for LLM iterations.
    """
    
    def __init__(self):
        super().__init__(
            name="initialize_execution",
            description="Set up execution context and initialize iteration state",
            allowed_phases=["Review", "Creation", "Modification", "Operation"]
        )
        # Simplified successors for basic flow - single node workflow
        self.successors = {
            "error": "initialize_execution"  # Only handle errors by retrying the same node
        }
        
    def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        """Initialize execution context and run full LLM execution loop."""
        try:
            # Extract required context from UI
            if not all(key in prep_res for key in ["provider", "model_name", "system_prompt", "user_request_id", "messages", "selected_nifi_server_id"]):
                missing = [key for key in ["provider", "model_name", "system_prompt", "user_request_id", "messages", "selected_nifi_server_id"] if key not in prep_res]
                raise ValueError(f"Missing required context fields: {missing}")
                
            provider = prep_res["provider"]
            model_name = prep_res["model_name"]
            system_prompt = prep_res["system_prompt"]
            user_req_id = prep_res["user_request_id"]
            initial_messages = prep_res["messages"]
            nifi_server_id = prep_res["selected_nifi_server_id"]
            
            # DEBUG: Check the type of initial_messages
            self.bound_logger.info(f"Messages type: {type(initial_messages)}, value: {initial_messages[:200] if isinstance(initial_messages, str) else f'List with {len(initial_messages)} items' if isinstance(initial_messages, list) else str(initial_messages)}")
            
            # FIX: If messages is a string, try to parse it as JSON
            if isinstance(initial_messages, str):
                import json
                try:
                    initial_messages = json.loads(initial_messages)
                    self.bound_logger.info(f"Successfully parsed messages string to list with {len(initial_messages)} items")
                except json.JSONDecodeError as e:
                    self.bound_logger.error(f"Failed to parse messages string as JSON: {e}")
                    raise ValueError(f"Messages field is a string but not valid JSON: {e}")
            
            # Log warnings for missing fields instead of failing
            missing_fields = []
            if not provider: missing_fields.append("provider")
            if not model_name: missing_fields.append("model_name") 
            if not system_prompt: missing_fields.append("system_prompt")
            if not user_req_id: missing_fields.append("user_request_id")
            if not initial_messages: missing_fields.append("messages")
            if not nifi_server_id: missing_fields.append("selected_nifi_server_id")
            
            if missing_fields:
                raise ValueError(f"Missing required context fields: {missing_fields}")
            
            self.bound_logger.info(f"Starting full LLM execution loop with provider: {provider}, model: {model_name}")
            
            # Initialize execution state
            execution_state = {
                "provider": provider,
                "model_name": model_name,
                "system_prompt": system_prompt,
                "user_request_id": user_req_id,
                "nifi_server_id": nifi_server_id,
                "messages": initial_messages.copy() if initial_messages else [],
                "loop_count": 0,
                "max_iterations": prep_res.get("max_loop_iterations", 10),
                "tool_results": [],
                "request_tokens_in": 0,
                "request_tokens_out": 0,
                "execution_complete": False,
                "workflow_id": "unguided_mimic",
                "step_id": "initialize_execution"
            }
            
            # Run the full execution loop (like current unguided mode)
            final_results = self._run_full_execution_loop(execution_state)
            
            return {
                "status": "success",
                "execution_state": final_results["execution_state"],
                "final_messages": final_results["messages"],
                "total_tokens_in": final_results["total_tokens_in"],
                "total_tokens_out": final_results["total_tokens_out"],
                "loop_count": final_results["loop_count"],
                "task_complete": final_results["task_complete"],
                "max_iterations_reached": final_results["max_iterations_reached"],
                "tool_calls_executed": final_results["tool_calls_executed"],
                "executed_tools": final_results["executed_tools"],
                "message": f"Execution completed with {final_results['loop_count']} iterations"
            }
            
        except Exception as e:
            self.bound_logger.error(f"Error in full execution: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": "Failed to execute LLM workflow"
            }
    
    def _prepare_tools(self, execution_state: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """Prepare tools for LLM execution."""
        try:
            nifi_server_id = execution_state.get("nifi_server_id")
            if not nifi_server_id:
                self.bound_logger.warning("No NiFi server ID provided, skipping tools")
                return []
            
            # Get available tools from the handler
            tools = get_available_tools(
                phase="All",  # Use "All" for unguided mode
                selected_nifi_server_id=nifi_server_id
            )
            
            # Get formatted tools for LLM using the actual provider from execution state
            provider = execution_state.get("provider", "openai")  # Use actual provider
            chat_manager = get_chat_manager()
            if chat_manager:
                formatted_tools = chat_manager.get_tools(
                    provider=provider,
                    user_request_id=execution_state.get("user_request_id")
                )
            else:
                formatted_tools = []
            
            # Handle case where formatting returns None (e.g., Anthropic not available)
            if formatted_tools is None:
                self.bound_logger.warning(f"Tool formatting returned None for provider {provider}, using empty list")
                formatted_tools = []
            
            self.bound_logger.info(f"Prepared {len(formatted_tools)} tools")
            return formatted_tools
            
        except Exception as e:
            self.bound_logger.error(f"Error preparing tools: {e}")
            return []
    
    def _call_llm(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]], 
                  execution_state: Dict[str, Any], action_id: str) -> Dict[str, Any]:
        """Call the LLM with messages and tools."""
        try:
            provider = execution_state.get("provider", "openai")
            model_name = execution_state.get("model_name", "gpt-4o-mini")
            system_prompt = execution_state.get("system_prompt", "You are a helpful assistant.")
            user_request_id = execution_state.get("user_request_id", "unknown")
            
            # Extract non-system messages for the LLM call
            non_system_messages = [msg for msg in messages if msg.get("role") != "system"]
            
            chat_manager = get_chat_manager()
            if chat_manager:
                response_data = chat_manager.get_llm_response(
                    messages=non_system_messages,
                    system_prompt=system_prompt,
                    provider=provider,
                    model_name=model_name,
                    user_request_id=user_request_id,
                    action_id=action_id,  # Pass workflow context action ID
                    selected_nifi_server_id=execution_state.get("nifi_server_id")
                )
            else:
                response_data = {"error": "Chat manager not available"}
            
            return response_data
            
        except Exception as e:
            self.bound_logger.error(f"LLM call failed: {e}")
            return {"error": str(e)}
    
    def _process_tool_calls(self, tool_calls: List[Dict[str, Any]], 
                           execution_state: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Process tool calls and return results."""
        tool_results = []
        
        for tool_call in tool_calls:
            try:
                tool_call_id = tool_call.get("id", str(uuid.uuid4()))
                function_name = tool_call.get("function", {}).get("name")
                function_args = tool_call.get("function", {}).get("arguments", "{}")
                
                if not function_name:
                    continue
                
                # Parse arguments
                try:
                    args_dict = json.loads(function_args) if function_args != "{}" else {}
                except json.JSONDecodeError:
                    args_dict = {}
                
                # Execute tool with workflow context
                workflow_id = execution_state.get("workflow_id", "unguided_mimic")
                step_id = execution_state.get("step_id", f"initialize_execution_iter_{execution_state.get('loop_count', 1)}")
                action_id = f"wf-{workflow_id}-{step_id}-tool-{uuid.uuid4()}"
                
                self.bound_logger.info(f"Executing tool '{function_name}' in workflow context: {workflow_id}/{step_id}")
                
                result = execute_mcp_tool(
                    tool_name=function_name,
                    params=args_dict,
                    selected_nifi_server_id=execution_state.get("nifi_server_id"),
                    user_request_id=execution_state.get("user_request_id", "unknown"),
                    action_id=action_id
                )
                
                # Create tool result message
                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(result, default=str)
                }
                
                tool_results.append({
                    "tool_call_id": tool_call_id,
                    "function_name": function_name,
                    "result": result,
                    "message": tool_message
                })
                
            except Exception as e:
                self.bound_logger.error(f"Tool call failed for {function_name}: {e}")
                error_message = {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", str(uuid.uuid4())),
                    "content": json.dumps({"error": str(e)})
                }
                tool_results.append({
                    "tool_call_id": tool_call.get("id"),
                    "function_name": function_name,
                    "result": {"error": str(e)},
                    "message": error_message
                })
        
        return tool_results

    def _run_full_execution_loop(self, execution_state: Dict[str, Any]) -> Dict[str, Any]:
        """Run the complete LLM execution loop (replicating unguided behavior)."""
        task_complete = False
        max_iterations_reached = False
        tool_calls_executed = 0
        executed_tools = set()
        
        self.bound_logger.info("Starting LLM execution loop")
        self.bound_logger.info(f"Initial execution state: task_complete={task_complete}, max_iterations_reached={max_iterations_reached}")
        self.bound_logger.info(f"Messages count: {len(execution_state.get('messages', []))}")
        self.bound_logger.info(f"Max iterations: {execution_state.get('max_iterations', 10)}")
        
        while not task_complete and not max_iterations_reached:
            execution_state["loop_count"] += 1
            loop_count = execution_state["loop_count"]
            max_iterations = execution_state.get("max_iterations", 10)
            
            self.bound_logger.info(f"LLM iteration {loop_count}/{max_iterations}")
            self.bound_logger.info(f"Loop condition check: task_complete={task_complete}, max_iterations_reached={max_iterations_reached}")
            
            # Check max iterations
            if loop_count > max_iterations:
                max_iterations_reached = True
                self.bound_logger.warning(f"Reached maximum iterations ({max_iterations})")
                break
            
            # Prepare tools
            formatted_tools = self._prepare_tools(execution_state)
            
            # Prepare LLM context
            llm_context_messages = [{"role": "system", "content": execution_state["system_prompt"]}]
            llm_context_messages.extend(execution_state["messages"])
            
            # Generate workflow context action ID for this LLM call
            workflow_id = execution_state.get("workflow_id", "unguided_mimic")
            step_id = f"initialize_execution_iter_{execution_state['loop_count']}"
            llm_action_id = f"wf-{workflow_id}-{step_id}-llm-{uuid.uuid4()}"
            
            # Call LLM
            self.bound_logger.info(f"About to call LLM with {len(llm_context_messages)} messages and {len(formatted_tools) if formatted_tools else 0} tools")
            response_data = self._call_llm(llm_context_messages, formatted_tools, execution_state, llm_action_id)
            self.bound_logger.info(f"LLM response received: {list(response_data.keys()) if response_data else 'None'}")
            
            if "error" in response_data:
                self.bound_logger.error(f"LLM call failed: {response_data['error']}")
                break
            
            # Update token counts
            execution_state["request_tokens_in"] += response_data.get("token_count_in", 0)
            execution_state["request_tokens_out"] += response_data.get("token_count_out", 0)
            
            # Add assistant message to history
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
                "workflow_id": execution_state.get("workflow_id", "unguided_mimic"),
                "step_id": step_id,
                "action_id": llm_action_id  # Use the workflow context action ID
            })
            
            execution_state["messages"].append(assistant_message)
            
            # Process tool calls if any
            if tool_calls:
                tool_results = self._process_tool_calls(tool_calls, execution_state)
                execution_state["tool_results"].extend(tool_results)
                tool_calls_executed += len(tool_calls)
                
                # Track executed tools
                for tool_call in tool_calls:
                    if "function" in tool_call and "name" in tool_call["function"]:
                        executed_tools.add(tool_call["function"]["name"])
                
                # Add tool results to messages
                for tool_result in tool_results:
                    execution_state["messages"].append(tool_result["message"])
            
            # Check for completion
            task_complete = llm_content and "TASK COMPLETE" in llm_content
            if task_complete:
                self.bound_logger.info("LLM indicated task completion")
        
        # DEBUG: Log message count before returning
        self.workflow_logger.bind(
            direction="execution",
            data={
                "message_count": len(execution_state["messages"]),
                "messages": [
                    {
                        "index": i,
                        "role": msg.get("role", "unknown"),
                        "has_content": bool(msg.get("content")),
                        "has_tool_calls": bool(msg.get("tool_calls"))
                    }
                    for i, msg in enumerate(execution_state["messages"])
                ]
            }
        ).info("Returning messages from _run_full_execution_loop")
        
        return {
            "execution_state": execution_state,
            "messages": execution_state["messages"],
            "total_tokens_in": execution_state["request_tokens_in"],
            "total_tokens_out": execution_state["request_tokens_out"],
            "loop_count": execution_state["loop_count"],
            "task_complete": task_complete,
            "max_iterations_reached": max_iterations_reached,
            "tool_calls_executed": tool_calls_executed,
            "executed_tools": list(executed_tools)
        }
    
    def post(self, shared, prep_res, exec_res):
        """Store execution results."""
        # DEBUG: Log what we received from exec
        self.workflow_logger.bind(
            direction="post",
            data={
                "exec_res_keys": list(exec_res.keys()),
                "final_messages_count": len(exec_res.get("final_messages", [])),
                "status": exec_res.get("status", "unknown"),
                "loop_count": exec_res.get("loop_count", 0),
                "total_tokens_in": exec_res.get("total_tokens_in", 0),
                "total_tokens_out": exec_res.get("total_tokens_out", 0),
                "tool_calls_executed": exec_res.get("tool_calls_executed", 0),
                "max_iterations_reached": exec_res.get("max_iterations_reached", False),
                "messages": [
                    {
                        "index": i,
                        "role": msg.get("role", "unknown"),
                        "has_content": bool(msg.get("content")),
                        "has_tool_calls": bool(msg.get("tool_calls"))
                    }
                    for i, msg in enumerate(exec_res.get("final_messages", []))
                ]
            }
        ).info("InitializeExecutionNode post processing")
        
        shared["execution_state"] = exec_res.get("execution_state", {})
        shared["final_messages"] = exec_res.get("final_messages", [])  # Fixed: use "final_messages" instead of "messages"
        shared["total_tokens_in"] = exec_res.get("total_tokens_in", 0)
        shared["total_tokens_out"] = exec_res.get("total_tokens_out", 0)
        shared["loop_count"] = exec_res.get("loop_count", 0)
        shared["task_complete"] = exec_res.get("task_complete", False)
        shared["max_iterations_reached"] = exec_res.get("max_iterations_reached", False)
        shared["tool_calls_executed"] = exec_res.get("tool_calls_executed", 0)
        shared["executed_tools"] = exec_res.get("executed_tools", [])
        
        # Store the result in the format expected by the UI
        shared["unguided_mimic_result"] = {
            "status": exec_res.get("status", "success"),
            "message": exec_res.get("message", "Unguided mimic execution completed"),
            "loop_count": exec_res.get("loop_count", 0),
            "max_iterations_reached": exec_res.get("max_iterations_reached", False),
            "messages": exec_res.get("final_messages", []),  # Fixed: use "final_messages" instead of "messages"
            "total_tokens_in": exec_res.get("total_tokens_in", 0),
            "total_tokens_out": exec_res.get("total_tokens_out", 0),
            "tool_calls_executed": exec_res.get("tool_calls_executed", 0),
            "tool_results": exec_res.get("tool_results", [])
        }
        
        if exec_res.get("status") == "error":
            return "error"
        # Return a string not in successors to terminate the flow gracefully
        return "completed"


class LLMIterationNode(NiFiWorkflowNode):
    """
    Execute a single LLM iteration with tool processing.
    """
    
    def __init__(self):
        super().__init__(
            name="llm_iteration", 
            description="Execute LLM call and process any tool calls",
            allowed_phases=["Review", "Creation", "Modification", "Operation"]
        )
        # Simplified successors - just continue to next node
        self.successors = {
            "continue": "evaluate_completion",
            "error": "finalize_execution"
        }
        
    def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        """Execute single LLM iteration."""
        try:
            execution_state = prep_res.get("execution_state", {})
            execution_state["loop_count"] += 1
            loop_count = execution_state["loop_count"]
            max_iterations = execution_state.get("max_iterations", 10)
            
            # Update step_id for this iteration
            execution_state["step_id"] = f"llm_iteration_{loop_count}"
            
            self.bound_logger.info(f"Starting LLM iteration {loop_count}/{max_iterations}")
            
            # Check max iterations
            if loop_count > max_iterations:
                return {
                    "status": "max_iterations_reached",
                    "execution_state": execution_state,
                    "message": f"Reached maximum iterations ({max_iterations})"
                }
            
            # Prepare tools
            formatted_tools = self._prepare_tools(execution_state)
            
            # Prepare LLM context
            llm_context_messages = [{"role": "system", "content": execution_state["system_prompt"]}]
            llm_context_messages.extend(execution_state["messages"])
            
            # Generate workflow context action ID for this LLM call
            workflow_id = execution_state.get("workflow_id", "unguided_mimic")
            step_id = f"llm_iteration_iter_{loop_count}"
            llm_action_id = f"wf-{workflow_id}-{step_id}-llm-{uuid.uuid4()}"
            
            # Call LLM
            response_data = self._call_llm(llm_context_messages, formatted_tools, execution_state, llm_action_id)
            
            if "error" in response_data:
                return {
                    "status": "error",
                    "execution_state": execution_state,
                    "error": response_data["error"]
                }
            
            # Update token counts
            execution_state["request_tokens_in"] += response_data.get("token_count_in", 0)
            execution_state["request_tokens_out"] += response_data.get("token_count_out", 0)
            
            # Add assistant message to history
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
                "workflow_id": execution_state.get("workflow_id", "unguided_mimic"),
                "step_id": step_id,
                "action_id": llm_action_id  # Use the workflow context action ID
            })
            
            execution_state["messages"].append(assistant_message)
            
            # Process tool calls if any
            if tool_calls:
                tool_results = self._process_tool_calls(tool_calls, execution_state)
                execution_state["tool_results"].extend(tool_results)
                
                # Add tool results to messages
                for tool_result in tool_results:
                    execution_state["messages"].append(tool_result["message"])
            
            # Check for completion
            task_complete = llm_content and "TASK COMPLETE" in llm_content
            
            return {
                "status": "success", 
                "execution_state": execution_state,
                "llm_content": llm_content,
                "task_complete": task_complete,
                "tool_calls_processed": len(tool_calls) if tool_calls else 0,
                "max_iterations_reached": loop_count >= max_iterations
            }
                
        except Exception as e:
            self.bound_logger.error(f"Error in LLM iteration: {e}", exc_info=True)
            return {
                "status": "error",
                "execution_state": execution_state,
                "error": str(e)
            }
    
    def post(self, shared, prep_res, exec_res):
        """Update shared state and determine next step."""
        shared["execution_state"] = exec_res.get("execution_state", {})
        shared["last_iteration_result"] = exec_res
        
        status = exec_res.get("status")
        if status == "error":
            return "error"
        elif exec_res.get("max_iterations_reached") or exec_res.get("task_complete"):
            # Skip evaluation, go directly to finalization
            return "complete"
        else:
            return "continue"
    
    def _prepare_tools(self, execution_state: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """Prepare tools for LLM call."""
        try:
            user_request_id = execution_state.get("user_request_id")
            provider = execution_state.get("provider", "openai")
            nifi_server_id = execution_state.get("nifi_server_id")
            
            # Use correct parameter name for get_available_tools
            raw_tools_list = get_available_tools(
                phase="All",
                selected_nifi_server_id=nifi_server_id
            )
            
            if not raw_tools_list:
                self.bound_logger.warning("No raw tools available")
                return []
                
            chat_manager = get_chat_manager()
            if chat_manager:
                formatted_tools = chat_manager.get_tools(
                    provider=provider,
                    user_request_id=user_request_id
                )
            else:
                formatted_tools = []
            
            # Handle case where formatting returns None
            if formatted_tools is None:
                self.bound_logger.warning(f"Tool formatting returned None for provider {provider}, using empty list")
                formatted_tools = []
            
            if formatted_tools:
                self.bound_logger.debug(f"Tools prepared for {provider} ({len(formatted_tools)} tools)")
            
            return formatted_tools
            
        except Exception as e:
            self.bound_logger.error(f"Error preparing tools: {e}", exc_info=True)
            return []
    
    def _call_llm(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]], 
                  execution_state: Dict[str, Any], action_id: str) -> Dict[str, Any]:
        """Call LLM with the prepared context."""
        try:
            provider = execution_state.get("provider")
            model_name = execution_state.get("model_name") 
            system_prompt = execution_state.get("system_prompt")
            user_req_id = execution_state.get("user_request_id")
            
            self.bound_logger.info(f"Calling LLM ({provider} - {model_name}) with action ID: {action_id}")
            
            chat_manager = get_chat_manager()
            if chat_manager:
                response_data = chat_manager.get_llm_response(
                    messages=messages,
                    system_prompt=system_prompt,
                    provider=provider,
                    model_name=model_name,
                    user_request_id=user_req_id,
                    action_id=action_id,  # Pass workflow context action ID
                    selected_nifi_server_id=execution_state.get("nifi_server_id")
                )
            else:
                response_data = {"error": "Chat manager not available"}
            
            if response_data is None:
                return {"error": "LLM response was empty"}
            
            return response_data
            
        except Exception as e:
            self.bound_logger.error(f"Error calling LLM: {e}", exc_info=True)
            return {"error": f"Error calling LLM: {e}"}
    
    def _process_tool_calls(self, tool_calls: List[Dict[str, Any]], 
                           execution_state: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Process tool calls and return results."""
        tool_results = []
        user_req_id = execution_state.get("user_request_id")
        nifi_server_id = execution_state.get("nifi_server_id")
        
        for tool_call in tool_calls:
            try:
                tool_id = tool_call.get("id")
                function_call = tool_call.get("function", {})
                function_name = function_call.get("name")
                function_args_str = function_call.get("arguments", "{}")
                
                self.bound_logger.info(f"Executing tool: {function_name} (ID: {tool_id})")
                
                arguments = json.loads(function_args_str)
                
                # Execute tool with workflow context
                workflow_id = execution_state.get("workflow_id", "unguided_mimic")
                step_id = execution_state.get("step_id", f"initialize_execution_iter_{execution_state.get('loop_count', 1)}")
                action_id = f"wf-{workflow_id}-{step_id}-tool-{uuid.uuid4()}"
                
                self.bound_logger.info(f"Executing tool '{function_name}' in workflow context: {workflow_id}/{step_id}")
                
                result = execute_mcp_tool(
                    tool_name=function_name,
                    params=arguments,
                    selected_nifi_server_id=nifi_server_id,
                    user_request_id=user_req_id,
                    action_id=action_id
                )
                
                tool_result_content = json.dumps(result) if result is not None else "null"
                
                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": tool_result_content
                }
                
                tool_results.append({
                    "tool_name": function_name,
                    "tool_id": tool_id,
                    "arguments": arguments,
                    "result": result,
                    "message": tool_message
                })
                
            except Exception as e:
                self.bound_logger.error(f"Error executing tool {function_name}: {e}", exc_info=True)
                error_message = {
                    "role": "tool", 
                    "tool_call_id": tool_id,
                    "content": f"Error executing tool: {e}"
                }
                tool_results.append({
                    "tool_name": function_name,
                    "tool_id": tool_id,
                    "error": str(e),
                    "message": error_message
                })
        
        return tool_results


class EvaluateCompletionNode(NiFiWorkflowNode):
    """
    Evaluate whether the workflow should continue or complete.
    """
    
    def __init__(self):
        super().__init__(
            name="evaluate_completion",
            description="Determine if execution should continue or complete",
            allowed_phases=["Review"]
        )
        self.successors = {
            "continue": "llm_iteration",
            "complete": "finalize_execution"
        }
    
    def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate completion status."""
        try:
            execution_state = prep_res.get("execution_state", {})
            last_result = prep_res.get("last_iteration_result", {})
            
            loop_count = execution_state.get("loop_count", 0)
            max_iterations = execution_state.get("max_iterations", 10)
            task_complete = last_result.get("task_complete", False)
            
            # Simple completion logic
            if loop_count >= max_iterations or task_complete:
                return {
                    "status": "complete",
                    "decision": "complete",
                    "reason": "Task complete or max iterations reached"
                }
            else:
                return {
                    "status": "continue",
                    "decision": "continue",
                    "reason": "More iterations needed"
                }
                
        except Exception as e:
            self.bound_logger.error(f"Error evaluating completion: {e}", exc_info=True)
            return {
                "status": "error",
                "decision": "complete",
                "error": str(e)
            }
    
    def post(self, shared, prep_res, exec_res):
        """Determine next step based on evaluation."""
        shared["completion_evaluation"] = exec_res
        decision = exec_res.get("decision", "complete")
        return decision


class FinalizeExecutionNode(NiFiWorkflowNode):
    """
    Finalize execution and prepare final results.
    """
    
    def __init__(self):
        super().__init__(
            name="finalize_execution",
            description="Generate final results and completion summary",
            allowed_phases=["Review"]
        )
        # No successors - this is the final node
        self.successors = {}
    
    def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        """Finalize execution and prepare results."""
        try:
            execution_state = prep_res.get("execution_state", {})
            
            loop_count = execution_state.get("loop_count", 0)
            max_iterations = execution_state.get("max_iterations", 10)
            max_iterations_reached = loop_count >= max_iterations
            
            # Prepare final results
            final_result = {
                "status": "success",
                "message": f"Unguided mimic execution completed after {loop_count} iterations",
                "loop_count": loop_count,
                "max_iterations_reached": max_iterations_reached,
                "messages": execution_state.get("messages", []),
                "total_tokens_in": execution_state.get("request_tokens_in", 0),
                "total_tokens_out": execution_state.get("request_tokens_out", 0),
                "tool_calls_executed": len(execution_state.get("tool_results", [])),
                "tool_results": execution_state.get("tool_results", [])
            }
            
            if max_iterations_reached:
                final_result["status"] = "warning"
                final_result["message"] = f"Reached maximum iterations ({max_iterations})"
            
            self.bound_logger.info(f"Execution finalized: {final_result['status']}")
            return final_result
            
        except Exception as e:
            self.bound_logger.error(f"Error finalizing execution: {e}", exc_info=True)
            return {
                "status": "error",
                "message": f"Execution failed: {e}",
                "loop_count": 0,
                "messages": []
            }
    
    def post(self, shared, prep_res, exec_res):
        """Store final results."""
        shared["unguided_mimic_result"] = exec_res
        shared["execution_completed"] = True
        return "completed"


def create_unguided_mimic_workflow() -> List[NiFiWorkflowNode]:
    """Factory function to create the unguided mimic workflow - temporarily simplified to debug."""
    # Temporarily use only the initialization node to test basic execution
    return [
        InitializeExecutionNode()
    ]


# Register the unguided mimic workflow
unguided_mimic_workflow = WorkflowDefinition(
    name="unguided_mimic",
    description="Replicates the existing unguided LLM execution behavior using multi-node workflow",
    create_workflow_func=create_unguided_mimic_workflow,
    display_name="Unguided Mimic",
    category="Basic",
    phases=["Review", "Creation", "Modification", "Operation"],
    enabled=True
)

# Auto-register the workflow when this module is imported
register_workflow(unguided_mimic_workflow) 