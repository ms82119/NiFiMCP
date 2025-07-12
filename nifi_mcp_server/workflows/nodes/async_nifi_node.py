"""
Async NiFi Workflow Node Base Classes

These classes extend the PocketFlow AsyncNode to provide NiFi-specific
functionality with real-time event emission.
"""

import sys
import os
import uuid
import asyncio
import importlib.util
from typing import Dict, Any, List, Optional

# Import PocketFlow async classes
pocketflow_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'docs', 'pocketflow examples')
sys.path.append(pocketflow_path)

# Import AsyncNode from the pocketflow examples __init__.py
spec = importlib.util.spec_from_file_location("pocketflow_init", os.path.join(pocketflow_path, "__init__.py"))
pocketflow_init = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pocketflow_init)
AsyncNode = pocketflow_init.AsyncNode

from loguru import logger
from ..core.event_system import (
    get_event_emitter, 
    emit_llm_start, emit_llm_complete, 
    emit_tool_start, emit_tool_complete,
    emit_message_added,
    EventTypes
)
from nifi_chat_ui.llm.chat_manager import ChatManager
from nifi_chat_ui.mcp_handler import get_available_tools, execute_mcp_tool


class AsyncNiFiWorkflowNode(AsyncNode):
    """
    Base class for async NiFi workflow nodes with event emission.
    """
    
    def __init__(self, name: str, description: str, allowed_phases: List[str] = None):
        super().__init__()
        self.name = name
        self.description = description
        self.allowed_phases = allowed_phases or ["All"]
        self.successors = {}  # Initialize successors
        self.bound_logger = logger
        self.workflow_logger = logger  # For consistency with sync nodes
        self.event_emitter = get_event_emitter()
    
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare the node execution context."""
        # Default prep - subclasses should override
        return shared
    
    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the node logic."""
        # Default exec - subclasses must override
        raise NotImplementedError("Subclasses must implement exec_async")
    
    async def post_async(self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]) -> str:
        """Post-process the execution results."""
        # Default post - return default action
        return "default"
    
    async def call_llm_async(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]], 
                           execution_state: Dict[str, Any], action_id: str) -> Dict[str, Any]:
        """Call LLM asynchronously with event emission."""
        workflow_id = execution_state.get("workflow_id", "unknown")
        step_id = execution_state.get("step_id", self.name)
        user_request_id = execution_state.get("user_request_id")
        
        # Emit LLM start event
        await emit_llm_start(workflow_id, step_id, {
            "action_id": action_id,
            "message_count": len(messages),
            "tool_count": len(tools) if tools else 0,
            "provider": execution_state.get("provider", "unknown"),
            "model": execution_state.get("model_name", "unknown")
        }, user_request_id)
        
        try:
            # Call LLM (this is still synchronous, but we're in an async context)
            provider = execution_state.get("provider", "openai")
            model_name = execution_state.get("model_name", "gpt-4o-mini")
            system_prompt = execution_state.get("system_prompt", "You are a helpful assistant.")
            
            # Extract non-system messages
            non_system_messages = [msg for msg in messages if msg.get("role") != "system"]
            
            # Define the sync function to call in executor
            def call_sync_llm():
                return ChatManager.get_llm_response(
                    messages=non_system_messages,
                    system_prompt=system_prompt,
                    tools=tools,
                    provider=provider,
                    model_name=model_name,
                    user_request_id=user_request_id,
                    action_id=action_id
                )
            
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            response_data = await loop.run_in_executor(None, call_sync_llm)
            
            # Validate response data
            if response_data is None:
                self.bound_logger.error("LLM response is None")
                response_data = {"error": "LLM response is None"}
            elif not isinstance(response_data, dict):
                self.bound_logger.error(f"LLM response is not a dict: {type(response_data)}")
                response_data = {"error": f"Invalid response type: {type(response_data)}"}
            
            # Check for error response
            if "error" in response_data:
                # Emit LLM error event
                await self.event_emitter.emit(EventTypes.LLM_ERROR, {
                    "action_id": action_id,
                    "error": response_data["error"],
                    "status": "error"
                }, workflow_id, step_id, user_request_id)
                
                self.bound_logger.error(f"LLM returned error: {response_data['error']}")
                return response_data
            
            # Emit LLM complete event
            tool_calls_data = response_data.get("tool_calls") or []
            await emit_llm_complete(workflow_id, step_id, {
                "action_id": action_id,
                "response_content": response_data.get("content", "")[:200] if response_data.get("content") else "",
                "tool_calls": len(tool_calls_data),
                "tokens_in": response_data.get("token_count_in", 0),
                "tokens_out": response_data.get("token_count_out", 0),
                "status": "success"
            }, user_request_id)
            
            return response_data
            
        except Exception as e:
            # Emit LLM error event
            await self.event_emitter.emit(EventTypes.LLM_ERROR, {
                "action_id": action_id,
                "error": str(e),
                "status": "error"
            }, workflow_id, step_id, user_request_id)
            
            self.bound_logger.error(f"Async LLM call failed: {e}", exc_info=True)
            return {"error": str(e)}
    
    async def execute_tool_calls_async(self, tool_calls: List[Dict[str, Any]], 
                                     execution_state: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Execute tool calls asynchronously with event emission."""
        workflow_id = execution_state.get("workflow_id", "unknown")
        step_id = execution_state.get("step_id", self.name)
        user_request_id = execution_state.get("user_request_id")
        nifi_server_id = execution_state.get("nifi_server_id")
        
        tool_results = []
        
        for i, tool_call in enumerate(tool_calls):
            try:
                tool_call_id = tool_call.get("id", str(uuid.uuid4()))
                function_name = tool_call.get("function", {}).get("name")
                function_args = tool_call.get("function", {}).get("arguments", "{}")
                
                if not function_name:
                    continue
                
                # Parse arguments
                try:
                    import json
                    args_dict = json.loads(function_args) if function_args != "{}" else {}
                except json.JSONDecodeError:
                    args_dict = {}
                
                # Emit tool start event
                await emit_tool_start(workflow_id, step_id, {
                    "tool_name": function_name,
                    "tool_call_id": tool_call_id,
                    "tool_index": i + 1,
                    "total_tools": len(tool_calls),
                    "arguments": args_dict
                }, user_request_id)
                
                # Define the sync function to call in executor
                def call_sync_tool():
                    return execute_mcp_tool(
                        tool_name=function_name,
                        params=args_dict,
                        selected_nifi_server_id=nifi_server_id,
                        user_request_id=user_request_id
                    )
                
                # Execute tool in thread pool to avoid blocking
                loop = asyncio.get_event_loop()
                tool_result = await loop.run_in_executor(None, call_sync_tool)
                
                # Create tool result message
                result_message = {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": function_name,
                    "content": str(tool_result)
                }
                tool_results.append(result_message)
                
                # Emit tool complete event
                await emit_tool_complete(workflow_id, step_id, {
                    "tool_name": function_name,
                    "tool_call_id": tool_call_id,
                    "tool_index": i + 1,
                    "total_tools": len(tool_calls),
                    "result_length": len(str(tool_result)),
                    "status": "success"
                }, user_request_id)
                
            except Exception as e:
                # Emit tool error event
                await self.event_emitter.emit(EventTypes.TOOL_ERROR, {
                    "tool_name": function_name,
                    "tool_call_id": tool_call_id,
                    "error": str(e),
                    "status": "error"
                }, workflow_id, step_id, user_request_id)
                
                self.bound_logger.error(f"Tool execution failed: {function_name} - {e}")
                
                # Add error result
                error_result = {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": function_name,
                    "content": f"Error: {str(e)}"
                }
                tool_results.append(error_result)
        
        return tool_results
    
    async def add_message_to_context_async(self, message: Dict[str, Any], 
                                         execution_state: Dict[str, Any]):
        """Add a message to the execution context with event emission."""
        workflow_id = execution_state.get("workflow_id", "unknown")
        step_id = execution_state.get("step_id", self.name)
        user_request_id = execution_state.get("user_request_id")
        
        # Add to messages
        if "messages" not in execution_state:
            execution_state["messages"] = []
        execution_state["messages"].append(message)
        
        # Emit message added event
        await emit_message_added(workflow_id, step_id, {
            "message_role": message.get("role"),
            "message_type": "tool_calls" if "tool_calls" in message else "content",
            "content_length": len(message.get("content", "")),
            "tool_calls": len(message.get("tool_calls", [])),
            "action_id": message.get("action_id")
        }, user_request_id)
    
    def prepare_tools(self, execution_state: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """Prepare tools for LLM execution (sync method for compatibility)."""
        try:
            nifi_server_id = execution_state.get("nifi_server_id")
            if not nifi_server_id:
                self.bound_logger.warning("No NiFi server ID provided, skipping tools")
                return []
            
            # Get available tools
            tools = get_available_tools(
                phase="All",  # Use "All" for unguided mode
                selected_nifi_server_id=nifi_server_id
            )
            
            # Get formatted tools for LLM
            provider = execution_state.get("provider", "openai")
            formatted_tools = ChatManager.get_formatted_tool_definitions(
                provider=provider,
                raw_tools=tools,
                user_request_id=execution_state.get("user_request_id")
            )
            
            if formatted_tools is None:
                self.bound_logger.warning(f"Tool formatting returned None for provider {provider}")
                formatted_tools = []
            
            self.bound_logger.info(f"Prepared {len(formatted_tools)} tools")
            return formatted_tools
            
        except Exception as e:
            self.bound_logger.error(f"Error preparing tools: {e}")
            return [] 