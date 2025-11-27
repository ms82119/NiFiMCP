"""
Flow Documentation Workflow Definition

This file defines the multi-node documentation workflow and registers it
with the workflow registry.

Copy this file to: nifi_mcp_server/workflows/definitions/flow_documentation.py
"""

from pocketflow import AsyncFlow

from ..nodes.doc_nodes import (
    InitializeDocNode,
    DiscoveryNode,
    HierarchicalAnalysisNode,
    DocumentationNode
)
from ..registry import WorkflowDefinition, register_workflow


def create_flow_documentation_workflow() -> AsyncFlow:
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
    init_node >> discovery_node
    
    # DiscoveryNode self-loop for pagination
    # When more data to fetch, returns "continue" to loop back
    # When complete, returns "complete" to move to analysis
    discovery_node - "continue" >> discovery_node  # Self-loop for pagination
    discovery_node - "complete" >> analysis_node
    
    # HierarchicalAnalysisNode self-loop for depth processing
    # Processes each depth level from leaves to root
    # Returns "next_level" to continue processing
    # Returns "complete" when root is processed
    analysis_node - "next_level" >> analysis_node  # Self-loop for hierarchical processing
    analysis_node - "complete" >> doc_node
    
    # DocumentationNode is terminal (no successors)
    # Workflow ends when doc_node completes
    
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

