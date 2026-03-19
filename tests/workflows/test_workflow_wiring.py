"""Test workflow definition and node wiring."""

import pytest
from nifi_mcp_server.workflows.definitions.flow_documentation import (
    create_flow_documentation_workflow
)
from nifi_mcp_server.workflows.registry import get_workflow_registry


class TestFlowDocumentationWorkflowWiring:
    """Test multi-node workflow wiring."""
    
    def test_workflow_registration(self):
        """Test workflow is registered correctly."""
        registry = get_workflow_registry()
        workflow = registry.get_workflow("flow_documentation")
        
        assert workflow is not None
        assert workflow.name == "flow_documentation"
        assert workflow.is_async is True
        assert "Review" in workflow.phases
    
    def test_workflow_creation(self):
        """Test workflow creates without error."""
        flow = create_flow_documentation_workflow()
        
        assert flow is not None
        assert flow.start_node is not None
        assert flow.start_node.name == "initialize_doc"
    
    def test_node_transitions(self):
        """Test all node transitions are wired correctly."""
        flow = create_flow_documentation_workflow()
        
        # Get all nodes by traversing
        visited = set()
        nodes = []
        
        def traverse(node):
            if node is None or id(node) in visited:
                return
            visited.add(id(node))
            nodes.append(node)
            if hasattr(node, 'successors'):
                for successor in node.successors.values():
                    traverse(successor)
        
        traverse(flow.start_node)
        
        # Should have 4 nodes
        assert len(nodes) == 4
        
        # Verify node names
        node_names = {n.name for n in nodes}
        assert "initialize_doc" in node_names
        assert "discovery_node" in node_names
        assert "hierarchical_analysis_node" in node_names
        assert "documentation_node" in node_names
    
    def test_discovery_self_loop(self):
        """Test discovery node has self-loop for pagination."""
        flow = create_flow_documentation_workflow()
        discovery = flow.start_node.successors.get("default")
        
        assert discovery is not None
        assert "continue" in discovery.successors
        # Self-loop: discovery -> discovery
        assert discovery.successors["continue"] is discovery
    
    def test_analysis_self_loop(self):
        """Test analysis node has self-loop for depth iteration."""
        flow = create_flow_documentation_workflow()
        discovery = flow.start_node.successors.get("default")
        analysis = discovery.successors.get("complete")
        
        assert analysis is not None
        assert "next_level" in analysis.successors
        # Self-loop: analysis -> analysis
        assert analysis.successors["next_level"] is analysis

