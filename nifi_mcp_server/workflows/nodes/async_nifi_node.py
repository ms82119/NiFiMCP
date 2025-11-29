"""
Async NiFi Workflow Node Base Classes

These classes extend the PocketFlow AsyncNode to provide NiFi-specific
functionality with real-time event emission.
"""

import sys
import os
import uuid
import asyncio
import time
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
            config_dict = None  # Initialize to None
            
            # Try to get config from execution state first (passed from main thread)
            if hasattr(self, '_config_dict') and self._config_dict:
                config_dict = self._config_dict
                self.bound_logger.info("Using config passed from main thread")
            elif hasattr(self, 'execution_state') and self.execution_state:
                # Defensive check: ensure execution_state is a dict before calling .get()
                if isinstance(self.execution_state, dict) and self.execution_state.get('_config_dict'):
                    config_dict = self.execution_state['_config_dict']
                    self.bound_logger.info("Using config from shared state")
                else:
                    # execution_state exists but is not a dict or doesn't have _config_dict
                    self.bound_logger.debug(f"execution_state is not a dict or missing _config_dict: {type(self.execution_state)}")
                    config_dict = None
            
            if not config_dict:
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
        # Defensive check: ensure execution_state is a dict
        if not isinstance(execution_state, dict):
            self.bound_logger.error(f"set_execution_state called with non-dict! Type: {type(execution_state)}, Value: {str(execution_state)[:200]}")
            raise ValueError(f"execution_state must be a dict, got {type(execution_state).__name__}")
        self.execution_state = execution_state
        self.bound_logger.info("Execution state set in workflow node")
    
    def get_mcp_client(self) -> MCPClient:
        """Get or create the MCPClient instance."""
        if self._mcp_client is None:
            self._mcp_client = MCPClient()
        return self._mcp_client
    
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare the node execution context."""
        # Log node prep with shared state snapshot
        if isinstance(shared, dict):
            snapshot = self._create_shared_state_snapshot(shared)
            self.workflow_logger.bind(
                interface="workflow",
                data={
                    "node_name": self.name,
                    "action": "prep",
                    "shared_state_snapshot": snapshot
                }
            ).info(f"Node prep: {self.name}")
        
        # Default prep - subclasses should override
        return shared
    
    def _create_shared_state_snapshot(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        """Create a snapshot of shared state for logging."""
        if not isinstance(shared, dict):
            return {"error": "shared is not a dict"}
        
        snapshot = {
            "keys": list(shared.keys()),
            "sizes": {}
        }
        
        # Track sizes of important keys
        important_keys = ["pg_tree", "pg_summaries", "doc_sections", "final_document", 
                         "processors", "connections", "process_groups"]
        for key in important_keys:
            value = shared.get(key)
            if isinstance(value, (list, dict, str)):
                snapshot["sizes"][key] = len(value)
            elif value is not None:
                snapshot["sizes"][key] = 1  # Non-empty but not sizeable
        
        # Include a few key values (truncated)
        snapshot["sample_values"] = {}
        for key in ["current_phase", "workflow_name", "process_group_id", "user_request_id"]:
            if key in shared:
                val = shared[key]
                snapshot["sample_values"][key] = str(val)[:100] if val else None
        
        return snapshot
    
    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the node logic."""
        # Default exec - subclasses must override
        raise NotImplementedError("Subclasses must implement exec_async")
    
    async def post_async(self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]) -> str:
        """Post-process the execution results."""
        # Log node post with shared state snapshot showing changes
        if isinstance(shared, dict):
            snapshot = self._create_shared_state_snapshot(shared)
            exec_summary = {}
            if isinstance(exec_res, dict):
                exec_summary = {
                    "status": exec_res.get("status", "unknown"),
                    "keys": list(exec_res.keys())[:10]  # Limit to first 10 keys
                }
            
            self.workflow_logger.bind(
                interface="workflow",
                data={
                    "node_name": self.name,
                    "action": "post",
                    "shared_state_snapshot": snapshot,
                    "exec_result_summary": exec_summary
                }
            ).info(f"Node post: {self.name}")
        
        # Default post - return default action
        return "default"
    
    async def call_llm_async(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]], 
                           execution_state: Dict[str, Any], action_id: str) -> Dict[str, Any]:
        """Call LLM asynchronously with event emission."""
        # Defensive check: ensure execution_state is a dict
        if not isinstance(execution_state, dict):
            self.bound_logger.error(f"execution_state is not a dict in call_llm_async! Type: {type(execution_state)}")
            raise ValueError(f"execution_state must be a dict, got {type(execution_state).__name__}")
        
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
            "messages_in_request": len([msg for msg in messages if msg.get("role") in ["user", "assistant", "tool"]]) + 1,  # Count non-system messages + 1 for system message
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
                "loop_count": execution_state.get("loop_count", 0),  # Include current iteration
                "provider": execution_state.get("provider", "unknown"),
                "model": execution_state.get("model_name", "unknown"),
                "model_name": execution_state.get("model_name", "unknown")  # Add explicit model_name field
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
        # self.golden_context_manager.add_message_to_golden_context(message) # This line was removed
        
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
            max_tokens = execution_state.get("max_tokens_limit", 32000)
            
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
            # Don't fail the entire workflow due to pruning errors
            # Return original messages and continue execution
            # This ensures the LLM can still process the conversation even if pruning fails
            self.bound_logger.warning("Smart pruning failed, continuing with original messages to ensure workflow execution")
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
    
    async def call_nifi_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        prep_res: Dict[str, Any]
    ) -> Any:
        """
        Helper method to call NiFi tools via MCP.
        
        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments
            prep_res: Prep result containing execution context
        
        Returns:
            Tool execution result
        """
        workflow_id = prep_res.get("workflow_id", "unknown")
        step_id = prep_res.get("step_id", self.name)
        user_request_id = prep_res.get("user_request_id")
        nifi_server_id = prep_res.get("nifi_server_id")
        
        # Generate action ID for this tool call
        import uuid
        action_id = f"wf-{workflow_id}-{step_id}-tool-{uuid.uuid4()}"
        
        # Emit tool start event
        await emit_tool_start(workflow_id, step_id, {
            "tool_name": tool_name,
            "arguments": arguments,
            "action_id": action_id
        }, user_request_id)
        
        try:
            # Execute tool via MCP
            loop = asyncio.get_event_loop()
            
            def call_sync_tool():
                return execute_mcp_tool(
                    tool_name=tool_name,
                    params=arguments,
                    selected_nifi_server_id=nifi_server_id,
                    user_request_id=user_request_id,
                    action_id=action_id
                )
            
            result = await loop.run_in_executor(None, call_sync_tool)
            
            # Calculate result length for UI display
            result_str = str(result) if result is not None else ""
            result_length = len(result_str)
            
            # Emit tool complete event with result length for UI
            await emit_tool_complete(workflow_id, step_id, {
                "tool_name": tool_name,
                "action_id": action_id,
                "result_length": result_length,
                "tool_result": result_str[:200] if result_length > 0 else "",  # Preview for UI
                "status": "success"
            }, user_request_id)
            
            return result
            
        except Exception as e:
            # Emit tool error event
            await self.event_emitter.emit(EventTypes.TOOL_ERROR, {
                "tool_name": tool_name,
                "action_id": action_id,
                "error": str(e),
                "status": "error"
            }, workflow_id, step_id, user_request_id)
            
            self.bound_logger.error(f"Tool call failed: {e}", exc_info=True)
            raise
    
    async def emit_doc_phase_event(
        self,
        event_type: str,
        phase: str,
        shared: Dict[str, Any],
        metrics: Optional[Dict[str, Any]] = None,
        progress_message: Optional[str] = None
    ):
        """
        Helper method to emit documentation phase events.
        
        Args:
            event_type: Event type (from EventTypes)
            phase: Phase name (INIT, DISCOVERY, ANALYSIS, GENERATION)
            shared: Shared state dict
            metrics: Optional metrics dict
            progress_message: Optional progress message
        """
        from ..core.event_system import (
            emit_doc_phase_start,
            emit_doc_phase_complete,
            emit_doc_progress,
        )
        
        workflow_id = shared.get("workflow_id", "unknown") if isinstance(shared, dict) else "unknown"
        step_id = shared.get("step_id", self.name) if isinstance(shared, dict) else self.name
        user_request_id = shared.get("user_request_id") if isinstance(shared, dict) else None

        # Optional: small shared_state snapshot for observability
        details = None
        if isinstance(shared, dict):
            snapshot_keys = ["pg_tree", "pg_summaries", "doc_sections", "final_document"]
            sizes: Dict[str, int] = {}
            for key in snapshot_keys:
                value = shared.get(key)
                if isinstance(value, (list, dict, str)):
                    sizes[key] = len(value)
            if sizes:
                details = {"snapshot_sizes": sizes}
        
        if event_type == EventTypes.DOC_PHASE_START:
            # Use provided progress_message or a sensible default
            msg = progress_message or f"{phase} phase starting..."
            await emit_doc_phase_start(
                workflow_id,
                step_id,
                phase,
                progress_message=msg,
                details=details,
                user_request_id=user_request_id,
            )
        elif event_type == EventTypes.DOC_PHASE_COMPLETE:
            started_at = shared.get(f"{phase}_start_time", time.time())
            await emit_doc_phase_complete(
                workflow_id,
                step_id,
                phase,
                started_at,
                metrics or {},
                progress_message=progress_message,
                details=details,
                user_request_id=user_request_id,
            )
        elif event_type == EventTypes.DOC_PROGRESS_UPDATE:
            await emit_doc_progress(
                workflow_id,
                step_id,
                phase,
                progress_message or "",
                None,
                metrics,
                user_request_id,
            )
        else:
            # Generic event emission
            await self.event_emitter.emit(event_type, {
                "phase": phase,
                "metrics": metrics or {},
                "message": progress_message
            }, workflow_id, step_id, user_request_id) 