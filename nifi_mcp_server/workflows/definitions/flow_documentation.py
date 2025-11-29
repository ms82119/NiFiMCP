"""
Flow Documentation Workflow Definition

This file defines the multi-node documentation workflow and registers it
with the workflow registry.
"""

import sys
import os
from typing import TYPE_CHECKING, Any

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

from ..nodes.doc_nodes import (
    InitializeDocNode,
    DiscoveryNode,
    HierarchicalAnalysisNode,
    DocumentationNode
)
from ..registry import WorkflowDefinition, register_workflow

# Type checking imports
if TYPE_CHECKING:
    from pocketflow import AsyncFlow as AsyncFlowType
else:
    AsyncFlowType = Any


def create_flow_documentation_workflow() -> AsyncFlowType:
    """
    Create the multi-node flow documentation workflow.
    
    Workflow structure:
    
        InitializeDocNode
            │
            ▼ "default"
        DiscoveryNode ◄──┐
            │            │ "continue" (pagination)
            ├────────────┘
            │
            ▼ "complete"
        HierarchicalAnalysisNode ◄──┐
            │                       │ "next_level" (depth iteration)
            ├───────────────────────┘
            │
            ▼ "complete"
        DocumentationNode
            │
            ▼ (end - empty successors)
    
    Returns:
        AsyncFlow: The wired-up documentation workflow
    """
    # Create node instances
    init_node = InitializeDocNode()
    discovery_node = DiscoveryNode()
    analysis_node = HierarchicalAnalysisNode()
    doc_node = DocumentationNode()
    
    # Wire up transitions using PocketFlow syntax
    # Note: PocketFlow uses >> for default transitions and - "action" >> for named transitions
    
    # InitializeDocNode -> DiscoveryNode (default transition)
    # Error transitions: all nodes can return "error" to stop workflow
    init_node >> discovery_node
    init_node - "error" >> doc_node  # Terminal error handler (doc_node will handle error state)
    
    # DiscoveryNode self-loop for pagination
    # When more data to fetch, returns "continue" to loop back
    # When complete, returns "complete" to move to analysis
    # When error, go to doc_node to report error
    discovery_node - "continue" >> discovery_node  # Self-loop for pagination
    discovery_node - "complete" >> analysis_node
    discovery_node - "error" >> doc_node  # Error handler
    
    # HierarchicalAnalysisNode self-loop for depth processing
    # Processes each depth level from leaves to root
    # Returns "next_level" to continue processing
    # Returns "complete" when root is processed
    # When error, go to doc_node to report error
    analysis_node - "next_level" >> analysis_node  # Self-loop for hierarchical processing
    analysis_node - "complete" >> doc_node
    analysis_node - "error" >> doc_node  # Error handler
    
    # DocumentationNode is terminal (no successors)
    # Workflow ends when doc_node completes (success or error)
    
    # Create and return the flow
    return AsyncFlow(start=init_node)


# Register the workflow with the global registry
flow_documentation_definition = WorkflowDefinition(
    name="flow_documentation",
    description="Generate comprehensive documentation for NiFi flows using hierarchical analysis",
    create_workflow_func=create_flow_documentation_workflow,
    display_name="Flow Documentation",
    category="Analysis",
    phases=["Review"],
    enabled=True,
    is_async=True
)

register_workflow(flow_documentation_definition)

