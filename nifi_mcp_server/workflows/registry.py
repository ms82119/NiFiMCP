"""
Workflow registry for NiFi MCP.

This module provides registration and discovery of available workflows,
following existing NiFi MCP patterns for tool registration.
"""

from typing import Dict, List, Optional, Callable, Any
from loguru import logger
from config.logging_setup import request_context
from config.settings import get_enabled_workflows, is_workflow_enabled

from .core.executor import GuidedWorkflowExecutor
from .nodes.base_node import WorkflowNode


class WorkflowDefinition:
    """
    Definition of a workflow including metadata and factory function.
    """
    
    def __init__(self, 
                 name: str,
                 description: str,
                 create_workflow_func: Callable,
                 display_name: Optional[str] = None,
                 category: str = "Basic",
                 phases: List[str] = None,
                 enabled: bool = True,
                 is_async: bool = False,
                 factory: Optional[Callable] = None):
        """
        Initialize workflow definition.
        
        Args:
            name: Internal name/identifier for the workflow
            description: Description of what the workflow does
            create_workflow_func: Function that creates the workflow (nodes or flow)
            display_name: Human-readable name for the UI (defaults to name)
            category: Category for grouping workflows (e.g., "Basic", "Analysis", "Creation")
            phases: List of NiFi phases this workflow covers
            enabled: Whether the workflow is enabled
            is_async: Whether this is an async workflow
            factory: Legacy parameter for backward compatibility
        """
        self.name = name
        self.display_name = display_name or name
        self.description = description
        self.category = category
        self.phases = phases or ["All"]
        self.create_workflow_func = create_workflow_func
        self.enabled = enabled
        self.is_async = is_async
        
        # Legacy support - use factory if provided, otherwise use create_workflow_func
        self.factory = factory or create_workflow_func
        
    def create_nodes(self) -> List[WorkflowNode]:
        """Create workflow nodes using the factory function."""
        try:
            return self.create_workflow_func()
        except Exception as e:
            logger.error(f"Failed to create nodes for workflow {self.name}: {e}", exc_info=True)
            raise
    
    def create_workflow(self):
        """Create workflow (nodes for sync, flow for async)."""
        try:
            return self.create_workflow_func()
        except Exception as e:
            logger.error(f"Failed to create workflow {self.name}: {e}", exc_info=True)
            raise
            
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "phases": self.phases,
            "enabled": self.enabled,
            "is_async": self.is_async
        }


class WorkflowRegistry:
    """
    Registry for managing available workflows.
    
    Provides registration, discovery, and instantiation of workflows
    similar to the existing tool phase registry pattern.
    """
    
    def __init__(self):
        """Initialize the workflow registry."""
        self._workflows: Dict[str, WorkflowDefinition] = {}
        self._categories: Dict[str, List[str]] = {}
        
    @property
    def bound_logger(self):
        """Get a logger bound with current context."""
        ctx = request_context.get()
        return logger.bind(
            user_request_id=ctx.get("user_request_id", "-"),
            action_id=ctx.get("action_id", "-"),
            component="workflow_registry"
        )
        
    def register(self, workflow_def: WorkflowDefinition):
        """
        Register a workflow definition.
        
        Args:
            workflow_def: Workflow definition to register
        """
        self.bound_logger.debug(f"Registering workflow: {workflow_def.name}")
        
        # Check if workflow is enabled in configuration
        if not is_workflow_enabled(workflow_def.name):
            self.bound_logger.debug(f"Workflow {workflow_def.name} is disabled in configuration")
            workflow_def.enabled = False
            
        self._workflows[workflow_def.name] = workflow_def
        
        # Update category index
        if workflow_def.category not in self._categories:
            self._categories[workflow_def.category] = []
        self._categories[workflow_def.category].append(workflow_def.name)
        
        self.bound_logger.info(f"Registered workflow: {workflow_def.name} in category {workflow_def.category}")
        
    def get_workflow(self, name: str) -> Optional[WorkflowDefinition]:
        """
        Get a workflow definition by name.
        
        Args:
            name: Workflow name
            
        Returns:
            Workflow definition or None if not found
        """
        return self._workflows.get(name)
        
    def list_workflows(self, category: Optional[str] = None, 
                      phase: Optional[str] = None,
                      enabled_only: bool = True) -> List[WorkflowDefinition]:
        """
        List available workflows with optional filtering.
        
        Args:
            category: Optional category filter
            phase: Optional phase filter
            enabled_only: Whether to include only enabled workflows
            
        Returns:
            List of workflow definitions
        """
        workflows = list(self._workflows.values())
        
        # Filter by enabled status
        if enabled_only:
            workflows = [w for w in workflows if w.enabled]
            
        # Filter by category
        if category:
            workflows = [w for w in workflows if w.category.lower() == category.lower()]
            
        # Filter by phase
        if phase:
            workflows = [w for w in workflows if phase.lower() in [p.lower() for p in w.phases]]
            
        return workflows
        
    def get_categories(self) -> List[str]:
        """Get list of available workflow categories."""
        return list(self._categories.keys())
        
    def get_workflows_by_category(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get workflows grouped by category."""
        result = {}
        for category in self._categories:
            workflows = self.list_workflows(category=category, enabled_only=True)
            result[category] = [w.to_dict() for w in workflows]
        return result
        
    def create_executor(self, workflow_name: str) -> Optional[GuidedWorkflowExecutor]:
        """
        Create a workflow executor for the specified workflow.
        
        Args:
            workflow_name: Name of the workflow to create
            
        Returns:
            Workflow executor or None if workflow not found
        """
        workflow_def = self.get_workflow(workflow_name)
        if not workflow_def:
            self.bound_logger.warning(f"Workflow not found: {workflow_name}")
            return None
            
        if not workflow_def.enabled:
            self.bound_logger.warning(f"Workflow is disabled: {workflow_name}")
            return None
            
        try:
            if workflow_def.is_async:
                # For async workflows, we'll need to handle them differently
                # For now, return None and use create_async_executor instead
                self.bound_logger.warning(f"Workflow {workflow_name} is async, use create_async_executor instead")
                return None
            
            nodes = workflow_def.create_nodes()
            executor = GuidedWorkflowExecutor(workflow_name, nodes)
            self.bound_logger.debug(f"Created executor for workflow: {workflow_name} with {len(nodes)} nodes")
            return executor
        except Exception as e:
            self.bound_logger.error(f"Failed to create executor for workflow {workflow_name}: {e}", exc_info=True)
            return None
    
    def create_async_executor(self, workflow_name: str):
        """
        Create an async workflow executor for the specified workflow.
        
        Args:
            workflow_name: Name of the workflow to create
            
        Returns:
            Async workflow executor or None if workflow not found
        """
        workflow_def = self.get_workflow(workflow_name)
        if not workflow_def:
            self.bound_logger.warning(f"Workflow not found: {workflow_name}")
            return None
            
        if not workflow_def.enabled:
            self.bound_logger.warning(f"Workflow is disabled: {workflow_name}")
            return None
            
        try:
            from .core.async_executor import AsyncWorkflowExecutor
            
            # Create the workflow (either nodes or flow)
            workflow = workflow_def.create_workflow()
            
            # Create async executor
            executor = AsyncWorkflowExecutor(workflow_name, workflow)
            self.bound_logger.debug(f"Created async executor for workflow: {workflow_name}")
            return executor
        except Exception as e:
            self.bound_logger.error(f"Failed to create async executor for workflow {workflow_name}: {e}", exc_info=True)
            return None
            
    def get_workflow_info(self, workflow_name: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a workflow.
        
        Args:
            workflow_name: Name of the workflow
            
        Returns:
            Workflow information dictionary or None if not found
        """
        workflow_def = self.get_workflow(workflow_name)
        if not workflow_def:
            return None
            
        try:
            # Get node information
            nodes = workflow_def.create_nodes()
            node_info = [
                {
                    "name": node.name,
                    "description": node.description,
                    "type": node.__class__.__name__
                }
                for node in nodes
            ]
            
            info = workflow_def.to_dict()
            info.update({
                "nodes_count": len(nodes),
                "nodes": node_info
            })
            
            return info
        except Exception as e:
            self.bound_logger.error(f"Failed to get info for workflow {workflow_name}: {e}", exc_info=True)
            # Return basic info even if node creation fails
            return workflow_def.to_dict()
            
    def validate_workflow(self, workflow_name: str) -> Dict[str, Any]:
        """
        Validate a workflow definition.
        
        Args:
            workflow_name: Name of the workflow to validate
            
        Returns:
            Validation results
        """
        workflow_def = self.get_workflow(workflow_name)
        if not workflow_def:
            return {
                "valid": False,
                "errors": [f"Workflow '{workflow_name}' not found"]
            }
            
        errors = []
        warnings = []
        
        try:
            # Test node creation
            nodes = workflow_def.create_nodes()
            
            if not nodes:
                errors.append("Workflow has no nodes")
            else:
                # Validate nodes
                for i, node in enumerate(nodes):
                    if not node.name:
                        errors.append(f"Node {i} has no name")
                    if not hasattr(node, 'exec'):
                        errors.append(f"Node {i} ({node.name}) has no exec method")
                        
                # Check for duplicate node names
                node_names = [node.name for node in nodes if node.name]
                if len(node_names) != len(set(node_names)):
                    errors.append("Duplicate node names found")
                    
        except Exception as e:
            errors.append(f"Node creation failed: {e}")
            
        # Check configuration
        if not is_workflow_enabled(workflow_name):
            warnings.append("Workflow is disabled in configuration")
            
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "workflow_info": workflow_def.to_dict()
        }


# Global registry instance
_workflow_registry = WorkflowRegistry()


def register_workflow(workflow_def: WorkflowDefinition):
    """Register a workflow with the global registry."""
    _workflow_registry.register(workflow_def)
    

def get_workflow_registry() -> WorkflowRegistry:
    """Get the global workflow registry instance."""
    return _workflow_registry 