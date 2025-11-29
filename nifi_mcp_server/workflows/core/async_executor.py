"""
Async Workflow Executor for NiFi MCP.

This module provides async workflow execution with real-time event emission
and support for both sync and async workflows.
"""

import sys
import os
import asyncio
from typing import Dict, Any, Optional, List, Callable, Union, TYPE_CHECKING

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
from config.logging_setup import request_context
from ..core.event_system import get_event_emitter, emit_workflow_start, EventTypes
from ..nodes.base_node import WorkflowNode
from ..nodes.async_nifi_node import AsyncNiFiWorkflowNode

# Type checking imports
if TYPE_CHECKING:
    from pocketflow import AsyncFlow as AsyncFlowType
else:
    AsyncFlowType = Any


class AsyncWorkflowExecutor:
    """
    Async executor for both sync and async workflows with real-time events.
    """
    
    def __init__(self, workflow_name: str, workflow: Union[List[WorkflowNode], AsyncFlowType]):
        """
        Initialize the async workflow executor.
        
        Args:
            workflow_name: Name of the workflow
            workflow: Either list of sync nodes or async flow
        """
        self.workflow_name = workflow_name
        self.workflow = workflow
        self.event_emitter = get_event_emitter()
        self.is_async_workflow = isinstance(workflow, AsyncFlow)
        self._config_dict = None
    
    def set_config(self, config_dict: Dict[str, Any]):
        """
        Set config dictionary to ALL nodes in the workflow.
        
        This method traverses the entire workflow graph to ensure all nodes
        receive configuration, not just the start node. This is critical for
        multi-node workflows like flow_documentation.
        """
        self._config_dict = config_dict
        self.bound_logger.info("Config set in async executor")
        
        # Debug: Log workflow structure
        self.bound_logger.info(f"Workflow type: {type(self.workflow)}, is_async: {self.is_async_workflow}")
        
        if self.is_async_workflow:
            # For async workflows, collect all nodes by traversing the graph
            all_nodes = self._collect_all_nodes()
            
            config_count = 0
            for node in all_nodes:
                if hasattr(node, 'set_config'):
                    node.set_config(config_dict)
                    config_count += 1
                    self.bound_logger.debug(f"Config passed to node: {node.name}")
            
            self.bound_logger.info(f"Config distributed to {config_count}/{len(all_nodes)} nodes")
            
        elif isinstance(self.workflow, list):
            # Sync workflow - list of nodes
            for node in self.workflow:
                if hasattr(node, 'set_config'):
                    node.set_config(config_dict)
                    self.bound_logger.debug(f"Config passed to sync node: {node.name}")
    
    def _collect_all_nodes(self) -> List:
        """
        Traverse workflow graph to collect all nodes.
        
        Handles:
        - Single node workflows (unguided pattern)
        - Multi-node workflows with transitions
        - Self-loops (nodes that transition back to themselves)
        
        Returns:
            List of all nodes in the workflow
        """
        visited: set = set()  # Track by id() to handle duplicate references
        nodes: List = []
        
        def traverse(node):
            """Recursive traversal with cycle detection."""
            if node is None:
                return
            
            node_id = id(node)
            if node_id in visited:
                return  # Already visited (handles self-loops)
            
            visited.add(node_id)
            nodes.append(node)
            
            # Traverse successors if the node has them
            if hasattr(node, 'successors') and node.successors:
                for action, successor in node.successors.items():
                    traverse(successor)
        
        # Start traversal from the workflow's start node
        if hasattr(self.workflow, 'start_node'):
            traverse(self.workflow.start_node)
        elif hasattr(self.workflow, 'nodes'):
            # Alternative: Direct access to nodes list
            nodes = list(self.workflow.nodes)
        elif hasattr(self.workflow, '_nodes'):
            # Another alternative
            nodes = list(self.workflow._nodes)
        else:
            self.bound_logger.warning(
                f"Could not find nodes in workflow {self.workflow_name}"
            )
        
        return nodes
    
    @property
    def bound_logger(self):
        """Get a logger bound with current context."""
        ctx = request_context.get()
        return logger.bind(
            user_request_id=ctx.get("user_request_id", "-"),
            action_id=ctx.get("action_id", "-"),
            workflow_name=self.workflow_name,
            component="async_workflow_executor"
        )
    
    async def execute_async(self, initial_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Execute workflow asynchronously with real-time events.
        
        Args:
            initial_context: Initial context for the workflow
            
        Returns:
            Final workflow results
        """
        self.bound_logger.info(f"Starting async workflow execution: {self.workflow_name}")
        
        # Defensive check: ensure initial_context is a dict
        if initial_context is not None and not isinstance(initial_context, dict):
            self.bound_logger.error(f"initial_context must be a dict, got {type(initial_context)}: {initial_context}")
            return {
                "status": "error",
                "error": f"Invalid initial_context type: {type(initial_context).__name__}",
                "message": "Initial context must be a dictionary"
            }
        
        try:
            # Emit workflow start event
            await emit_workflow_start(
                self.workflow_name, 
                "workflow_start", 
                {
                    "workflow_name": self.workflow_name,  # Include workflow_name for event_bridge
                    "workflow_type": "async" if self.is_async_workflow else "sync",
                    "initial_context_keys": list(initial_context.keys()) if initial_context and isinstance(initial_context, dict) else []
                },
                initial_context.get("user_request_id") if initial_context and isinstance(initial_context, dict) else None
            )
            
            # Set up shared state
            shared_state = initial_context.copy() if initial_context and isinstance(initial_context, dict) else {}
            shared_state["workflow_name"] = self.workflow_name
            
            # Pass config through shared state as backup
            if self._config_dict:
                shared_state["_config_dict"] = self._config_dict
                self.bound_logger.info("Added config to shared state as backup")
            
            # Set execution state in workflow nodes for config access
            if self.is_async_workflow:
                if hasattr(self.workflow, 'start_node') and hasattr(self.workflow.start_node, 'set_execution_state'):
                    # Ensure shared_state is a dict before setting
                    if isinstance(shared_state, dict):
                        self.workflow.start_node.set_execution_state(shared_state)
                        self.bound_logger.info("Set execution state in workflow start node")
                    else:
                        self.bound_logger.warning(f"Cannot set execution state: shared_state is not a dict (type: {type(shared_state)})")
            
            if self.is_async_workflow:
                # Execute async workflow
                self.bound_logger.info(f"Executing async workflow: {self.workflow_name}")
                try:
                    # Ensure shared_state is a dict before passing
                    if not isinstance(shared_state, dict):
                        raise ValueError(f"shared_state must be a dict before workflow execution, got {type(shared_state).__name__}")
                    
                    result = await self._execute_async_workflow(shared_state)
                    
                    # Validate result is a dict - but don't call .get() on it if it's not
                    if not isinstance(result, dict):
                        self.bound_logger.error(f"_execute_async_workflow returned non-dict: {type(result)} = {str(result)[:200]}")
                        # Create a proper error dict instead of raising
                        result = {
                            "status": "error",
                            "workflow_name": self.workflow_name,
                            "error": f"Workflow execution returned invalid result type: {type(result).__name__}",
                            "message": "Workflow execution failed"
                        }
                except Exception as workflow_error:
                    # Re-raise with more context, but ensure we return a dict
                    self.bound_logger.error(f"Error in _execute_async_workflow: {workflow_error}", exc_info=True)
                    # Don't re-raise - return error dict instead
                    result = {
                        "status": "error",
                        "workflow_name": self.workflow_name,
                        "error": str(workflow_error),
                        "message": f"Async workflow {self.workflow_name} execution failed"
                    }
            else:
                # Execute sync workflow in thread pool
                self.bound_logger.info(f"Executing sync workflow in thread pool: {self.workflow_name}")
                try:
                    result = await self._execute_sync_workflow_async(shared_state)
                    # Validate result is a dict
                    if not isinstance(result, dict):
                        self.bound_logger.error(f"_execute_sync_workflow_async returned non-dict: {type(result)} = {result}")
                        raise ValueError(f"Workflow execution returned invalid result type: {type(result).__name__}")
                except Exception as workflow_error:
                    # Re-raise with more context
                    self.bound_logger.error(f"Error in _execute_sync_workflow_async: {workflow_error}", exc_info=True)
                    raise
            
            # Workflow completion event is now handled by the workflow itself
            # No need to emit a separate completion event from the executor
            
            return result
            
        except Exception as e:
            # Emit workflow error event
            user_request_id = None
            if initial_context and isinstance(initial_context, dict):
                user_request_id = initial_context.get("user_request_id")
            
            try:
                await self.event_emitter.emit(EventTypes.WORKFLOW_ERROR, {
                    "error": str(e),
                    "workflow_name": self.workflow_name
                }, self.workflow_name, "workflow_error", user_request_id)
            except Exception as emit_error:
                self.bound_logger.warning(f"Failed to emit workflow error event: {emit_error}")
            
            self.bound_logger.error(f"Async workflow execution failed: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Async workflow {self.workflow_name} execution failed"
            }
    
    async def _execute_async_workflow(self, shared_state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute an async workflow."""
        self.bound_logger.info(f"Executing async workflow: {self.workflow_name}")
        
        # Defensive check: ensure shared_state is a dict
        if not isinstance(shared_state, dict):
            self.bound_logger.error(f"shared_state is not a dict! Type: {type(shared_state)}, Value: {shared_state}")
            return {
                "status": "error",
                "workflow_name": self.workflow_name,
                "error": f"Invalid shared_state type: {type(shared_state).__name__}",
                "message": "Workflow execution failed due to invalid shared state"
            }
        
        # Run the async flow
        # Note: PocketFlow modifies shared_state in-place and may return a string (navigation key) or the shared_state
        try:
            result = await self.workflow.run_async(shared_state)
        except Exception as e:
            # Capture full traceback so we can finally see *where* the '.get' is being called on a str
            import traceback
            tb_str = traceback.format_exc()
            
            self.bound_logger.error(
                f"PocketFlow run_async raised exception: {e}",
                exc_info=True
            )
            self.bound_logger.error(f"PocketFlow run_async traceback:\n{tb_str}")
            
            # Also surface this in the workflow result so the API client & test script can see it
            error_message = f"{e.__class__.__name__}: {e}"
            
            # If the shared_state already carries an error, prefer that but attach traceback
            if isinstance(shared_state, dict) and "error" in shared_state:
                return {
                    "status": "error",
                    "workflow_name": self.workflow_name,
                    "shared_state": shared_state,
                    "error": str(shared_state.get("error", error_message)),
                    "traceback": tb_str,
                    "message": f"Async workflow {self.workflow_name} failed inside PocketFlow.run_async"
                }
            
            # Generic error wrapper with traceback
            return {
                "status": "error",
                "workflow_name": self.workflow_name,
                "shared_state": shared_state if isinstance(shared_state, dict) else {"shared_state_type": str(type(shared_state))},
                "error": error_message,
                "traceback": tb_str,
                "message": f"Async workflow {self.workflow_name} failed inside PocketFlow.run_async"
            }
        
        # Handle case where PocketFlow returns a string (navigation key) instead of dict
        if isinstance(result, str):
            self.bound_logger.warning(f"PocketFlow returned string navigation key '{result}' instead of dict. Using shared_state.")
            # Ensure shared_state is still a dict (PocketFlow might have modified it)
            if not isinstance(shared_state, dict):
                self.bound_logger.error(f"shared_state became non-dict after PocketFlow execution! Type: {type(shared_state)}")
                return {
                    "status": "error",
                    "workflow_name": self.workflow_name,
                    "error": f"Shared state corrupted: expected dict, got {type(shared_state).__name__}",
                    "message": f"Async workflow {self.workflow_name} failed"
                }
            # Check if there's an error in shared_state
            if "error" in shared_state:
                return {
                    "status": "error",
                    "workflow_name": self.workflow_name,
                    "shared_state": shared_state,
                    "error": str(shared_state.get("error", "Unknown error")),
                    "message": f"Async workflow {self.workflow_name} failed"
                }
            # Otherwise treat as success
            result = shared_state
        
        # Ensure result is a dict
        if not isinstance(result, dict):
            self.bound_logger.warning(f"PocketFlow returned non-dict result: {type(result)}. Using shared_state.")
            if not isinstance(shared_state, dict):
                self.bound_logger.error(f"shared_state is also not a dict! Type: {type(shared_state)}")
                return {
                    "status": "error",
                    "workflow_name": self.workflow_name,
                    "error": f"Both result and shared_state are invalid types",
                    "message": f"Async workflow {self.workflow_name} failed"
                }
            result = shared_state
        
        # Final validation before returning
        if not isinstance(shared_state, dict):
            self.bound_logger.error(f"shared_state is not a dict at return! Type: {type(shared_state)}")
            return {
                "status": "error",
                "workflow_name": self.workflow_name,
                "error": f"Shared state corrupted: {type(shared_state).__name__}",
                "message": f"Async workflow {self.workflow_name} failed"
            }
        
        if not isinstance(result, dict):
            self.bound_logger.error(f"result is not a dict at return! Type: {type(result)}")
            result = {"result": result}  # Wrap non-dict result
        
        return {
            "status": "success",
            "workflow_name": self.workflow_name,
            "shared_state": shared_state,
            "flow_result": result,
            "message": f"Async workflow {self.workflow_name} completed successfully"
        }
    
    async def _execute_sync_workflow_async(self, shared_state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a sync workflow in a thread pool."""
        self.bound_logger.info(f"Executing sync workflow in thread pool: {self.workflow_name}")
        
        # Import sync executor
        from .executor import GuidedWorkflowExecutor
        
        # Create sync executor - workflow should be a list of nodes for sync workflows
        if not isinstance(self.workflow, list):
            raise ValueError(f"Sync workflow {self.workflow_name} must be a list of nodes, got {type(self.workflow)}")
        
        sync_executor = GuidedWorkflowExecutor(self.workflow_name, self.workflow)
        
        # Run in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: sync_executor.execute(shared_state)
        )
        
        return result


class AsyncWorkflowEventHandler:
    """
    Handler for workflow events that can update UI in real-time.
    """
    
    def __init__(self):
        self.event_emitter = get_event_emitter()
        self.ui_callbacks: List[Callable] = []
        
    def register_ui_callback(self, callback: Callable):
        """Register a callback for UI updates."""
        self.ui_callbacks.append(callback)
        self.event_emitter.on(self._handle_event_for_ui)
    
    async def _handle_event_for_ui(self, event):
        """Handle events and forward to UI callbacks."""
        for callback in self.ui_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                logger.error(f"UI callback error: {e}", exc_info=True) 