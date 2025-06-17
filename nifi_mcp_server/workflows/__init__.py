"""
NiFi MCP Workflow System

This module provides structured, guided workflows for NiFi operations,
built on top of the PocketFlow orchestration framework.
"""

from .registry import WorkflowRegistry, get_workflow_registry
from .core.executor import GuidedWorkflowExecutor

# Import workflow definitions to ensure they are registered
from .definitions import unguided_mimic

__all__ = ['WorkflowRegistry', 'GuidedWorkflowExecutor', 'get_workflow_registry'] 