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
        """Set config dictionary from main thread to avoid background thread imports."""
        self._config_dict = config_dict
        self.bound_logger.info("Config set in async executor")
        
        # Debug: Log workflow structure
        self.bound_logger.info(f"Workflow type: {type(self.workflow)}, is_async: {self.is_async_workflow}")
        if hasattr(self.workflow, 'start_node'):
            self.bound_logger.info(f"Workflow has start_node: {type(self.workflow.start_node)}")
        if hasattr(self.workflow, 'nodes'):
            self.bound_logger.info(f"Workflow has nodes: {len(self.workflow.nodes)}")
        if hasattr(self.workflow, '_nodes'):
            self.bound_logger.info(f"Workflow has _nodes: {len(self.workflow._nodes)}")
        
        # Pass config to workflow nodes if they support it
        if self.is_async_workflow:
            # For async workflows, try different ways to access nodes
            if hasattr(self.workflow, 'nodes'):
                # Direct nodes access
                for node in self.workflow.nodes:
                    if hasattr(node, 'set_config'):
                        node.set_config(config_dict)
                        self.bound_logger.info(f"Passed config to workflow node: {node.name}")
            elif hasattr(self.workflow, 'start_node') and hasattr(self.workflow.start_node, 'set_config'):
                # Single node workflow (like unguided)
                self.workflow.start_node.set_config(config_dict)
                self.bound_logger.info(f"Passed config to workflow start node: {self.workflow.start_node.name}")
            elif hasattr(self.workflow, '_nodes'):
                # Alternative nodes access
                for node in self.workflow._nodes:
                    if hasattr(node, 'set_config'):
                        node.set_config(config_dict)
                        self.bound_logger.info(f"Passed config to workflow node: {node.name}")
            else:
                self.bound_logger.warning(f"Could not find nodes in async workflow {self.workflow_name} to pass config to")
        elif not self.is_async_workflow and isinstance(self.workflow, list):
            for node in self.workflow:
                if hasattr(node, 'set_config'):
                    node.set_config(config_dict)
                    self.bound_logger.info(f"Passed config to workflow node: {node.name}")
    
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
        
        try:
            # Emit workflow start event
            await emit_workflow_start(
                self.workflow_name, 
                "workflow_start", 
                {
                    "workflow_type": "async" if self.is_async_workflow else "sync",
                    "initial_context_keys": list(initial_context.keys()) if initial_context else []
                },
                initial_context.get("user_request_id") if initial_context else None
            )
            
            # Set up shared state
            shared_state = initial_context.copy() if initial_context else {}
            shared_state["workflow_name"] = self.workflow_name
            
            # Pass config through shared state as backup
            if self._config_dict:
                shared_state["_config_dict"] = self._config_dict
                self.bound_logger.info("Added config to shared state as backup")
            
            # Set execution state in workflow nodes for config access
            if self.is_async_workflow:
                if hasattr(self.workflow, 'start_node') and hasattr(self.workflow.start_node, 'set_execution_state'):
                    self.workflow.start_node.set_execution_state(shared_state)
                    self.bound_logger.info("Set execution state in workflow start node")
            
            if self.is_async_workflow:
                # Execute async workflow
                self.bound_logger.info(f"Executing async workflow: {self.workflow_name}")
                result = await self._execute_async_workflow(shared_state)
            else:
                # Execute sync workflow in thread pool
                self.bound_logger.info(f"Executing sync workflow in thread pool: {self.workflow_name}")
                result = await self._execute_sync_workflow_async(shared_state)
            
            # Workflow completion event is now handled by the workflow itself
            # No need to emit a separate completion event from the executor
            
            return result
            
        except Exception as e:
            # Emit workflow error event
            await self.event_emitter.emit(EventTypes.WORKFLOW_ERROR, {
                "error": str(e),
                "workflow_name": self.workflow_name
            }, self.workflow_name, "workflow_error", 
            initial_context.get("user_request_id") if initial_context else None)
            
            self.bound_logger.error(f"Async workflow execution failed: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": f"Async workflow {self.workflow_name} execution failed"
            }
    
    async def _execute_async_workflow(self, shared_state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute an async workflow."""
        self.bound_logger.info(f"Executing async workflow: {self.workflow_name}")
        
        # Run the async flow
        result = await self.workflow.run_async(shared_state)
        
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