"""
Workflow execution engine for NiFi MCP.

This module provides the main workflow execution engine that integrates
with PocketFlow and manages the execution of guided workflows.
"""

from typing import Dict, Any, Optional, List, Callable
import asyncio
from loguru import logger
from pocketflow import Flow
from config.logging_setup import request_context
from config.settings import get_workflow_action_limit, get_workflow_retry_attempts
from nifi_mcp_server.request_context import current_workflow_id, current_step_id

from .context_manager import ContextManager
from .progress_tracker import ProgressTracker
from ..nodes.base_node import WorkflowNode, WorkflowActionLimitError, WorkflowNodeError


class GuidedWorkflowExecutor:
    """
    Main executor for guided workflows.
    
    Provides workflow execution with context management, progress tracking,
    and integration with the existing NiFi MCP infrastructure.
    """
    
    def __init__(self, workflow_name: str, nodes: List[WorkflowNode]):
        """
        Initialize the workflow executor.
        
        Args:
            workflow_name: Name of the workflow to execute
            nodes: List of workflow nodes to execute
        """
        self.workflow_name = workflow_name
        self.nodes = nodes
        self.context_manager = ContextManager()
        self.progress_tracker = ProgressTracker(workflow_name)
        self.flow: Optional[Flow] = None
        
        # Configure nodes with context manager and progress tracker
        for node in self.nodes:
            node.set_context_manager(self.context_manager)
            node.set_progress_tracker(self.progress_tracker)
            node.set_action_limit(get_workflow_action_limit())
        
    @property
    def bound_logger(self):
        """Get a logger bound with current context."""
        ctx = request_context.get()
        return logger.bind(
            user_request_id=ctx.get("user_request_id", "-"),
            action_id=ctx.get("action_id", "-"),
            workflow_name=self.workflow_name,
            component="workflow_executor"
        )
        
    @property
    def workflow_logger(self):
        """Get a logger specifically for workflow interface logging."""
        ctx = request_context.get()
        return logger.bind(
            user_request_id=ctx.get("user_request_id", "-"),
            action_id=ctx.get("action_id", "-"),
            workflow_name=self.workflow_name,
            interface="workflow"
        )
        
    def _create_flow(self) -> Flow:
        """Create a PocketFlow Flow from the workflow nodes with proper chaining."""
        if not self.nodes:
            raise WorkflowNodeError("Cannot create flow: no nodes provided")
            
        # Create the flow starting with the first node
        flow = Flow(self.nodes[0])
        
        # Chain the nodes together if there are multiple
        # PocketFlow will use the post() method return values for navigation
        for i in range(1, len(self.nodes)):
            flow = flow >> self.nodes[i]
            
        self.bound_logger.debug(f"Created flow with {len(self.nodes)} nodes in sequence")
        return flow
        
    def execute(self, initial_context: Optional[Dict[str, Any]] = None, 
                progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
        """
        Execute the workflow.
        
        Args:
            initial_context: Initial context for the workflow
            progress_callback: Optional callback function for progress updates
            
        Returns:
            Final workflow results
            
        Raises:
            WorkflowNodeError: If workflow execution fails
        """
        self.bound_logger.info(f"Starting workflow execution: {self.workflow_name}")
        
        # Log workflow start with interface logger middleware structure
        self.workflow_logger.bind(
            direction="start",
            data={
                "workflow_name": self.workflow_name,
                "nodes_count": len(self.nodes),
                "node_names": [node.name for node in self.nodes],
                "initial_context_keys": list(initial_context.keys()) if initial_context else [],
                "initial_context_values": {k: str(v)[:200] for k, v in (initial_context or {}).items()}  # Truncate for logging
            }
        ).info("workflow-start")
        
        try:
            # Set workflow context for logging
            workflow_id_token = current_workflow_id.set(self.workflow_name)
            current_context = request_context.get()
            current_context["workflow_id"] = self.workflow_name
            request_context.set(current_context)
            
            # Initialize progress tracking
            self.progress_tracker.start_workflow()
            
            # Set up initial shared state
            shared_state = initial_context.copy() if initial_context else {}
            shared_state["workflow_name"] = self.workflow_name
            shared_state["execution_start_time"] = self.progress_tracker.start_time
            
            # Update the context manager's shared state with the initial context
            if initial_context:
                for key, value in initial_context.items():
                    self.context_manager.update_shared_state(key, value)
            self.context_manager.update_shared_state("workflow_name", self.workflow_name)
            self.context_manager.update_shared_state("execution_start_time", self.progress_tracker.start_time)
            
            # Create and configure the flow
            self.flow = self._create_flow()
            
            # Execute the flow
            self.bound_logger.debug(f"Executing flow with {len(self.nodes)} nodes")
            
            # Call progress callback if provided
            if progress_callback:
                try:
                    progress_callback(self.progress_tracker.get_progress_summary())
                except Exception as e:
                    self.bound_logger.warning(f"Progress callback failed: {e}")
            
            # Run the flow - this is synchronous in PocketFlow 0.0.2
            # PocketFlow modifies shared_state in-place. The return value is the last action.
            self.flow.run(shared_state)
            final_shared_state = shared_state
            
            # Log final shared state after flow execution with interface logger middleware structure
            self.workflow_logger.bind(
                direction="execution",
                data={
                    "shared_state_keys": list(final_shared_state.keys()),
                    "final_messages_count": len(final_shared_state.get("final_messages", [])),
                    "unguided_mimic_result": {
                        "status": final_shared_state.get("unguided_mimic_result", {}).get("status", "unknown"),
                        "loop_count": final_shared_state.get("unguided_mimic_result", {}).get("loop_count", 0),
                        "total_tokens_in": final_shared_state.get("unguided_mimic_result", {}).get("total_tokens_in", 0),
                        "total_tokens_out": final_shared_state.get("unguided_mimic_result", {}).get("total_tokens_out", 0),
                        "tool_calls_executed": final_shared_state.get("unguided_mimic_result", {}).get("tool_calls_executed", 0),
                        "max_iterations_reached": final_shared_state.get("unguided_mimic_result", {}).get("max_iterations_reached", False)
                    }
                }
            ).info("workflow-execution")
            
            # Mark workflow as completed
            self.progress_tracker.complete_workflow(success=True)
            
            # Final progress callback
            if progress_callback:
                try:
                    progress_callback(self.progress_tracker.get_progress_summary())
                except Exception as e:
                    self.bound_logger.warning(f"Final progress callback failed: {e}")
            
            # Prepare final results
            results = {
                "workflow_name": self.workflow_name,
                "status": "success",
                "message": f"Workflow {self.workflow_name} completed successfully",
                "shared_state": final_shared_state,
                "progress_summary": self.progress_tracker.get_progress_summary(),
                "step_details": self.progress_tracker.get_step_details(),
                "context_milestones": self.context_manager.key_milestones
            }
            
            # Log workflow completion with interface logger middleware structure
            self.workflow_logger.bind(
                direction="completion",
                data={
                    "status": "success",
                    "workflow_name": self.workflow_name,
                    "shared_state_keys": list(final_shared_state.keys()),
                    "step_count": len(self.progress_tracker.get_step_details()),
                    "milestones_count": len(self.context_manager.key_milestones),
                    "progress_summary": self.progress_tracker.get_progress_summary(),
                    "step_details": self.progress_tracker.get_step_details()
                }
            ).info("workflow-completion")
            
            self.bound_logger.info(f"Workflow execution completed successfully: {self.workflow_name}")
            return results
            
        except WorkflowActionLimitError as e:
            error_msg = f"Workflow action limit exceeded: {e}"
            self.bound_logger.error(error_msg)
            self.progress_tracker.complete_workflow(success=False, error_message=error_msg)
            
            return {
                "workflow_name": self.workflow_name,
                "status": "error",
                "message": error_msg,
                "error_type": "action_limit_exceeded",
                "progress_summary": self.progress_tracker.get_progress_summary(),
                "step_details": self.progress_tracker.get_step_details()
            }
            
        except Exception as e:
            error_msg = f"Workflow execution failed: {e}"
            self.bound_logger.error(error_msg, exc_info=True)
            self.progress_tracker.complete_workflow(success=False, error_message=error_msg)
            
            return {
                "workflow_name": self.workflow_name,
                "status": "error", 
                "message": error_msg,
                "error_type": "execution_error",
                "progress_summary": self.progress_tracker.get_progress_summary(),
                "step_details": self.progress_tracker.get_step_details()
            }
            
    def get_progress(self) -> Dict[str, Any]:
        """Get current workflow progress information."""
        return {
            "workflow_name": self.workflow_name,
            "progress_summary": self.progress_tracker.get_progress_summary(),
            "current_step": self.progress_tracker.get_current_step_info(),
            "step_details": self.progress_tracker.get_step_details(),
            "context_state": {
                "shared_state_keys": list(self.context_manager.shared_state.keys()),
                "step_results_count": len(self.context_manager.step_results),
                "milestones_count": len(self.context_manager.key_milestones)
            }
        }
        
    def get_detailed_state(self) -> Dict[str, Any]:
        """Get detailed workflow state for debugging."""
        return {
            "workflow_name": self.workflow_name,
            "nodes_count": len(self.nodes),
            "node_names": [node.name for node in self.nodes],
            "progress_log": self.progress_tracker.export_progress_log(),
            "context_state": {
                "shared_state": self.context_manager.shared_state,
                "step_results": self.context_manager.step_results,
                "key_milestones": self.context_manager.key_milestones
            },
            "flow_configured": self.flow is not None
        }
        
    def reset(self):
        """Reset the executor for a new execution."""
        self.bound_logger.debug(f"Resetting workflow executor: {self.workflow_name}")
        
        # Reset context manager and progress tracker
        self.context_manager = ContextManager()
        self.progress_tracker = ProgressTracker(self.workflow_name)
        self.flow = None
        
        # Re-configure nodes
        for node in self.nodes:
            node.set_context_manager(self.context_manager)
            node.set_progress_tracker(self.progress_tracker)
            node._action_count = 0  # Reset action count
            
        self.bound_logger.debug("Workflow executor reset completed") 