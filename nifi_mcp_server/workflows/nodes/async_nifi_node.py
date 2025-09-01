"""
Async NiFi Workflow Node Base Classes

These classes extend the PocketFlow AsyncNode to provide NiFi-specific
functionality with real-time event emission.
"""

import sys
import os
import uuid
import asyncio
from typing import Dict, Any, List, Optional

# Import PocketFlow async classes from the installed package
try:
    from pocketflow import AsyncNode
except ImportError:
    # Fallback for development - try to import from local examples
    pocketflow_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'docs', 'libdocs', 'pocketflow examples')
    if os.path.exists(pocketflow_path):
        sys.path.append(pocketflow_path)
        import importlib.util
        spec = importlib.util.spec_from_file_location("pocketflow_init", os.path.join(pocketflow_path, "__init__.py"))
        pocketflow_init = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pocketflow_init)
        AsyncNode = pocketflow_init.AsyncNode
    else:
        raise ImportError("PocketFlow package not found and local examples not available")

from loguru import logger
from ..core.event_system import (
    get_event_emitter, 
    emit_llm_start, emit_llm_complete, 
    emit_tool_start, emit_tool_complete,
    emit_message_added,
    EventTypes
)
from nifi_chat_ui.llm.chat_manager import ChatManager
from nifi_chat_ui.llm.mcp.client import MCPClient
from nifi_chat_ui.mcp_handler import get_available_tools, execute_mcp_tool


# Removed GoldenContextManager class - no longer needed


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
        
        # Initialize ChatManager for new modular architecture
        self._chat_manager = None
        self._mcp_client = None
        
        # No longer need golden context manager - using database messages directly
    
    def get_chat_manager(self) -> ChatManager:
        """Get or create the ChatManager instance."""
        if self._chat_manager is None:
            # Try to get config from execution state first (passed from main thread)
            if hasattr(self, '_config_dict') and self._config_dict:
                config_dict = self._config_dict
                self.bound_logger.info("Using config passed from main thread")
            elif hasattr(self, 'execution_state') and self.execution_state and self.execution_state.get('_config_dict'):
                config_dict = self.execution_state['_config_dict']
                self.bound_logger.info("Using config from shared state")
            else:
                # Fallback: Import config in background thread (may cause warnings)
                try:
                    import sys
                    import os
                    current_dir = os.path.dirname(os.path.abspath(__file__))
                    parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))  # Go up to NiFiMCP root
                    if parent_dir not in sys.path:
                        sys.path.insert(0, parent_dir)
                    
                    from config import settings as config
                    
                    # Use the existing config system
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
                    self.bound_logger.info("Initialized config in background thread")
                except Exception as e:
                    self.bound_logger.error(f"Failed to import config in background thread: {e}")
                    # Use empty config as fallback
                    config_dict = {}
            
            self._chat_manager = ChatManager(config_dict)
            self.bound_logger.info("Initialized new modular ChatManager for workflow")
        
        return self._chat_manager
    
    def set_config(self, config_dict: Dict[str, Any]):
        """Set config dictionary from main thread to avoid background thread imports."""
        self._config_dict = config_dict
        self.bound_logger.info("Config set from main thread")
    
    def set_execution_state(self, execution_state: Dict[str, Any]):
        """Set execution state to access config from shared state."""
        self.execution_state = execution_state
        self.bound_logger.info("Execution state set in workflow node")
    
    def get_mcp_client(self) -> MCPClient:
        """Get or create the MCPClient instance."""
        if self._mcp_client is None:
            self._mcp_client = MCPClient()
        return self._mcp_client
    
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
        
        # Emit LLM start event with enhanced information
        await emit_llm_start(workflow_id, step_id, {
            "action_id": action_id,
            "message_count": len(messages),
            "tool_count": len(tools) if tools else 0,
            "provider": execution_state.get("provider", "unknown"),
            "model": execution_state.get("model_name", "unknown"),
            "model_name": execution_state.get("model_name", "unknown"),  # Explicit model name for UI
            "messages_in_request": len([msg for msg in messages if msg.get("role") in ["user", "assistant", "tool"]]),  # Count non-system messages
            "tools_available": len(tools) if tools else 0,  # Count available tools
            "loop_count": execution_state.get("loop_count", 0)  # Include current iteration
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
                chat_manager = self.get_chat_manager()
                return chat_manager.get_llm_response(
                    messages=non_system_messages,
                    system_prompt=system_prompt,
                    provider=provider,
                    model_name=model_name,
                    user_request_id=user_request_id,
                    action_id=action_id,
                    tools=tools
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
            if response_data.get("error"):  # Changed from "error" in response_data to response_data.get("error")
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
                "response_content": response_data.get("content", ""),
                "tool_calls": len(tool_calls_data),
                "tokens_in": response_data.get("token_count_in", 0),
                "tokens_out": response_data.get("token_count_out", 0),
                "status": "success",
                "loop_count": execution_state.get("loop_count", 0)  # Include current iteration
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
                                     execution_state: Dict[str, Any], 
                                     llm_action_id: str = None) -> List[Dict[str, Any]]:
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
                
                # Generate tool-specific action ID
                tool_action_id = f"wf-{workflow_id}-{step_id}-tool-{uuid.uuid4()}"
                
                # Emit tool start event with LLM action_id for correlation
                await emit_tool_start(workflow_id, step_id, {
                    "tool_name": function_name,
                    "tool_call_id": tool_call_id,
                    "tool_index": i + 1,
                    "total_tools": len(tool_calls),
                    "arguments": args_dict,
                    "llm_action_id": llm_action_id,  # Add LLM action_id for correlation
                    "loop_count": execution_state.get("loop_count", 0)  # Include current iteration
                }, user_request_id)
                
                # Define the sync function to call in executor
                def call_sync_tool():
                    return execute_mcp_tool(
                        tool_name=function_name,
                        params=args_dict,
                        selected_nifi_server_id=nifi_server_id,
                        user_request_id=user_request_id,
                        action_id=tool_call_id  # Use LLM's tool_call_id instead of generated action_id
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
                
                # Emit tool complete event with LLM action_id for correlation
                await emit_tool_complete(workflow_id, step_id, {
                    "tool_name": function_name,
                    "tool_call_id": tool_call_id,
                    "tool_index": i + 1,
                    "total_tools": len(tool_calls),
                    "result_length": len(str(tool_result)),
                    "status": "success",
                    "llm_action_id": llm_action_id,  # Add LLM action_id for correlation
                    "loop_count": execution_state.get("loop_count", 0)  # Include current iteration
                }, user_request_id)
                
            except Exception as e:
                # Emit tool error event with LLM action_id for correlation
                await self.event_emitter.emit(EventTypes.TOOL_ERROR, {
                    "tool_name": function_name,
                    "tool_call_id": tool_call_id,
                    "error": str(e),
                    "status": "error",
                    "llm_action_id": llm_action_id,  # Add LLM action_id for correlation
                    "loop_count": execution_state.get("loop_count", 0)  # Include current iteration
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
        """Add a message to the execution context with event emission and golden context update."""
        workflow_id = execution_state.get("workflow_id", "unknown")
        step_id = execution_state.get("step_id", self.name)
        user_request_id = execution_state.get("user_request_id")
        
        # Add to execution state messages
        if "messages" not in execution_state:
            execution_state["messages"] = []
        execution_state["messages"].append(message)
        
        # Add to golden context (this now persists to session state)
        self.golden_context_manager.add_message_to_golden_context(message)
        
        # Emit message added event
        await emit_message_added(workflow_id, step_id, {
            "message_role": message.get("role"),
            "message_type": "tool_calls" if "tool_calls" in message else "content",
            "content_length": len(message.get("content", "")),
            "tool_calls": len(message.get("tool_calls", [])),
            "action_id": message.get("action_id")
        }, user_request_id)
    
    # Removed get_golden_context_for_llm method - no longer needed
    
    async def add_workflow_message(self, message: Dict[str, Any], 
                                 execution_state: Dict[str, Any],
                                 persist_to_golden: bool = True):
        """Add a message to the workflow with optional golden context persistence."""
        # Always add to execution state
        if "messages" not in execution_state:
            execution_state["messages"] = []
        execution_state["messages"].append(message)
        
        # Messages are already persisted to database - no need for golden context
        if message.get("workflow_event"):
            self.bound_logger.debug(f"Workflow event message: {message.get('content', '')[:50]}...")
        else:
            self.bound_logger.debug(f"Added message to execution state: role={message.get('role')}, has_content={bool(message.get('content'))}")
        
        # Emit message added event
        workflow_id = execution_state.get("workflow_id", "unknown")
        step_id = execution_state.get("step_id", self.name)
        user_request_id = execution_state.get("user_request_id")
        
        await emit_message_added(workflow_id, step_id, {
            "message_role": message.get("role"),
            "message_type": "tool_calls" if "tool_calls" in message else "content",
            "content_length": len(message.get("content", "")),
            "tool_calls": len(message.get("tool_calls", [])),
            "action_id": message.get("action_id"),
            "is_status_report": message.get("is_status_report", False)  # Include status report flag
        }, user_request_id)
    
    def apply_smart_pruning_to_messages(self, messages: List[Dict[str, Any]], execution_state: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Apply smart pruning to messages based on execution state settings."""
        if not messages:
            return []
        
        # Check if auto-prune history is enabled
        auto_prune_history = execution_state.get("auto_prune_history", True)
        if not auto_prune_history:
            self.bound_logger.debug("Auto-prune history disabled, returning all messages without pruning")
            return messages
        
        try:
            # Import smart pruning function
            import sys
            import os
            current_dir = os.path.dirname(os.path.abspath(__file__))
            # Fix the import path - go up to the workspace root, then into nifi_chat_ui
            workspace_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
            if workspace_root not in sys.path:
                sys.path.insert(0, workspace_root)
            
            from nifi_chat_ui.utils.message_utils import smart_prune_messages
            
            # Get tools for token calculation
            tools = self.prepare_tools(execution_state)
            
            # Ensure tools is a list (not None)
            if tools is None:
                tools = []
                self.bound_logger.debug("No tools available for token calculation, using empty list")
            
            # Get max tokens limit
            max_tokens = execution_state.get("max_tokens_limit", 16000)
            
            # Log the pruning attempt details
            self.bound_logger.debug(f"Smart pruning attempt: {len(messages)} messages, max_tokens={max_tokens}, tools_count={len(tools)}")
            
            # Apply smart pruning
            pruned_messages = smart_prune_messages(
                messages,
                max_tokens=max_tokens,
                provider=execution_state.get("provider"),
                model_name=execution_state.get("model_name"),
                tools=tools,
                logger=self.bound_logger
            )
            
            self.bound_logger.info(f"Applied smart pruning: {len(messages)} -> {len(pruned_messages)} messages (max_tokens={max_tokens})")
            return pruned_messages
            
        except ImportError as e:
            self.bound_logger.warning(f"Could not import smart_prune_messages: {e}")
            return messages
        except Exception as e:
            self.bound_logger.error(f"Error applying smart pruning: {e}")
            return messages
    
    def prepare_tools(self, execution_state: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """Prepare tools for LLM execution (sync method for compatibility)."""
        try:
            nifi_server_id = execution_state.get("nifi_server_id")
            if not nifi_server_id:
                self.bound_logger.warning("No NiFi server ID provided, skipping tools")
                return []
            
            # Get available tools (raw tools - ChatManager will handle formatting)
            tools = get_available_tools(
                phase="All",  # Use "All" for unguided mode
                selected_nifi_server_id=nifi_server_id
            )
            
            if tools is None:
                self.bound_logger.warning("Tool retrieval returned None")
                tools = []
            
            self.bound_logger.info(f"Prepared {len(tools)} raw tools (ChatManager will format them)")
            return tools
            
        except Exception as e:
            self.bound_logger.error(f"Error preparing tools: {e}")
            return [] 