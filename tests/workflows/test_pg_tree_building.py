"""Unit tests for PG tree building logic."""

import pytest
from nifi_mcp_server.workflows.nodes.doc_nodes import DiscoveryNode, HierarchicalAnalysisNode


class TestPGTreeBuilding:
    """Test process group tree structure building."""
    
    @pytest.fixture
    def discovery_node(self):
        return DiscoveryNode()
    
    @pytest.fixture
    def analysis_node(self):
        return HierarchicalAnalysisNode()
    
    def test_build_simple_tree(self, discovery_node):
        """Test building a simple 2-level tree."""
        process_groups = [
            {
                "id": "pg1",
                "component": {
                    "name": "Child1",
                    "parentGroupId": "root"
                }
            }
        ]
        processor_counts = {"root": 3, "pg1": 2}
        
        tree = discovery_node._build_pg_tree("root", process_groups, processor_counts)
        
        assert "root" in tree
        assert "pg1" in tree
        assert tree["root"]["children"] == ["pg1"]
        assert tree["pg1"]["parent"] == "root"
        assert tree["root"]["processor_count"] == 3
        assert tree["pg1"]["processor_count"] == 2
    
    def test_build_nested_tree(self, discovery_node):
        """Test building a 3-level nested tree."""
        process_groups = [
            {
                "id": "pg1",
                "component": {
                    "name": "Level1",
                    "parentGroupId": "root"
                }
            },
            {
                "id": "pg2",
                "component": {
                    "name": "Level2",
                    "parentGroupId": "pg1"
                }
            }
        ]
        processor_counts = {"root": 5, "pg1": 3, "pg2": 2}
        
        tree = discovery_node._build_pg_tree("root", process_groups, processor_counts)
        
        assert tree["root"]["children"] == ["pg1"]
        assert tree["pg1"]["children"] == ["pg2"]
        assert tree["pg2"]["parent"] == "pg1"
        assert tree["pg1"]["parent"] == "root"
    
    def test_calculate_depths_bfs(self, discovery_node):
        """Test depth calculation uses BFS correctly."""
        tree = {
            "root": {"id": "root", "name": "Root", "parent": None, "children": ["pg1", "pg2"], "depth": 0},
            "pg1": {"id": "pg1", "name": "PG1", "parent": "root", "children": ["pg3"], "depth": 0},
            "pg2": {"id": "pg2", "name": "PG2", "parent": "root", "children": [], "depth": 0},
            "pg3": {"id": "pg3", "name": "PG3", "parent": "pg1", "children": [], "depth": 0}
        }
        
        discovery_node._calculate_depths(tree, "root")
        
        assert tree["root"]["depth"] == 0
        assert tree["pg1"]["depth"] == 1
        assert tree["pg2"]["depth"] == 1
        assert tree["pg3"]["depth"] == 2
    
    def test_multiple_children_same_parent(self, discovery_node):
        """Test tree with multiple children at same level."""
        process_groups = [
            {
                "id": "pg1",
                "component": {
                    "name": "Child1",
                    "parentGroupId": "root"
                }
            },
            {
                "id": "pg2",
                "component": {
                    "name": "Child2",
                    "parentGroupId": "root"
                }
            },
            {
                "id": "pg3",
                "component": {
                    "name": "Child3",
                    "parentGroupId": "root"
                }
            }
        ]
        processor_counts = {"root": 10, "pg1": 3, "pg2": 4, "pg3": 3}
        
        tree = discovery_node._build_pg_tree("root", process_groups, processor_counts)
        
        assert len(tree["root"]["children"]) == 3
        assert set(tree["root"]["children"]) == {"pg1", "pg2", "pg3"}
        assert all(tree[pg]["parent"] == "root" for pg in ["pg1", "pg2", "pg3"])
    
    def test_empty_tree(self, discovery_node):
        """Test building tree with no child PGs."""
        process_groups = []
        processor_counts = {"root": 5}
        
        tree = discovery_node._build_pg_tree("root", process_groups, processor_counts)
        
        assert "root" in tree
        assert tree["root"]["children"] == []
        assert tree["root"]["depth"] == 0

