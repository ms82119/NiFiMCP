"""Unit tests for documentation workflow nodes."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from nifi_mcp_server.workflows.nodes.doc_nodes import (
    InitializeDocNode,
    DiscoveryNode,
    HierarchicalAnalysisNode,
    DocumentationNode
)


class TestInitializeDocNode:
    """Test initialization node."""
    
    @pytest.fixture
    def init_node(self):
        return InitializeDocNode()
    
    @pytest.mark.asyncio
    async def test_initialization_success(self, init_node):
        """Test successful initialization."""
        shared = {
            "process_group_id": "root",
            "user_request_id": "test-123",
            "provider": "openai",
            "model_name": "gpt-4",
            "selected_nifi_server_id": "nifi-local",
            "workflow_id": "test-wf",
            "step_id": "init"
        }
        
        prep_res = await init_node.prep_async(shared)
        exec_res = await init_node.exec_async(prep_res)
        
        assert exec_res["status"] == "success"
        assert "initialized_state" in exec_res
        assert exec_res["initialized_state"]["process_group_id"] == "root"
        assert "pg_tree" in exec_res["initialized_state"]
        assert "pg_summaries" in exec_res["initialized_state"]
        assert "virtual_groups" in exec_res["initialized_state"]
    
    @pytest.mark.asyncio
    async def test_initialization_missing_server(self, init_node):
        """Test initialization fails without NiFi server."""
        shared = {
            "process_group_id": "root",
            "user_request_id": "test-123",
            "selected_nifi_server_id": None,  # Missing
            "workflow_id": "test-wf",
            "step_id": "init"
        }
        
        prep_res = await init_node.prep_async(shared)
        exec_res = await init_node.exec_async(prep_res)
        
        assert exec_res["status"] == "error"
        assert "No NiFi server" in exec_res["error"]


class TestDiscoveryNode:
    """Test discovery node."""
    
    @pytest.fixture
    def discovery_node(self, mock_call_nifi_tool):
        node = DiscoveryNode()
        node.call_nifi_tool = mock_call_nifi_tool
        return node
    
    @pytest.mark.asyncio
    async def test_discovery_builds_pg_tree(self, discovery_node, mock_call_nifi_tool):
        """Test that discovery builds PG tree structure."""
        # Mock tool responses
        async def mock_tool(tool_name, arguments, prep_res):
            if tool_name == "list_nifi_objects_recursive":
                return {
                    "results": [{"objects": [], "process_group_id": "root"}],
                    "completed": True
                }
            elif tool_name == "list_nifi_objects" and arguments["object_type"] == "process_groups":
                return {
                    "results": [{
                        "objects": [
                            {
                                "id": "pg1",
                                "component": {
                                    "name": "SubGroup1",
                                    "parentGroupId": "root"
                                }
                            }
                        ]
                    }]
                }
            elif tool_name == "list_nifi_objects" and arguments["object_type"] == "connections":
                return {"results": [{"objects": []}]}
            return {}
        
        discovery_node.call_nifi_tool = mock_tool
        
        shared = {
            "process_group_id": "root",
            "user_request_id": "test-123",
            "nifi_server_id": "test-server",
            "workflow_id": "test-wf",
            "step_id": "discovery",
            "flow_graph": {"processors": {}, "connections": [], "process_groups": {}},
            "pg_tree": {},
            "continuation_token": None
        }
        
        prep_res = await discovery_node.prep_async(shared)
        exec_res = await discovery_node.exec_async(prep_res)
        
        assert exec_res["status"] == "success"
        assert "pg_tree" in exec_res
        assert "root" in exec_res["pg_tree"]


class TestHierarchicalAnalysisNode:
    """Test hierarchical analysis node with mocked LLM."""
    
    @pytest.fixture
    def analysis_node(self, mock_call_llm_async, mock_call_nifi_tool):
        """Analysis node with mocked LLM and tools."""
        node = HierarchicalAnalysisNode()
        node.call_llm_async = mock_call_llm_async
        node.call_nifi_tool = mock_call_nifi_tool
        return node
    
    @pytest.mark.asyncio
    async def test_categorize_processors(self, analysis_node):
        """Test processor categorization."""
        processors = [
            {"id": "1", "component": {"name": "GetFile", "type": "org.apache.nifi.processors.standard.GetFile"}},
            {"id": "2", "component": {"name": "RouteData", "type": "org.apache.nifi.processors.standard.RouteOnAttribute"}},
        ]
        
        # Call the categorization method directly
        categorized = analysis_node._categorize_processors(processors)
        
        assert "IO_READ" in categorized
        assert "LOGIC" in categorized
        assert len(categorized["IO_READ"]) == 1
        assert len(categorized["LOGIC"]) == 1
    
    @pytest.mark.asyncio
    async def test_build_pg_tree_structure(self, analysis_node):
        """Test PG tree building structure."""
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
            }
        ]
        
        processor_counts = {"root": 5, "pg1": 2, "pg2": 3}
        tree = analysis_node._build_pg_tree("root", process_groups, processor_counts)
        
        assert "root" in tree
        assert "pg1" in tree
        assert "pg2" in tree
        assert tree["root"]["children"] == ["pg1", "pg2"]
        assert tree["pg1"]["parent"] == "root"
        assert tree["pg2"]["parent"] == "root"
    
    @pytest.mark.asyncio
    async def test_calculate_depths(self, analysis_node):
        """Test depth calculation."""
        tree = {
            "root": {"id": "root", "name": "Root", "parent": None, "children": ["pg1"], "depth": 0},
            "pg1": {"id": "pg1", "name": "PG1", "parent": "root", "children": ["pg2"], "depth": 0},
            "pg2": {"id": "pg2", "name": "PG2", "parent": "pg1", "children": [], "depth": 0}
        }
        
        analysis_node._calculate_depths(tree, "root")
        
        assert tree["root"]["depth"] == 0
        assert tree["pg1"]["depth"] == 1
        assert tree["pg2"]["depth"] == 2


class TestDocumentationNode:
    """Test documentation generation node."""
    
    @pytest.fixture
    def doc_node(self, mock_call_llm_async):
        """Documentation node with mocked LLM."""
        node = DocumentationNode()
        node.call_llm_async = mock_call_llm_async
        return node
    
    @pytest.mark.asyncio
    async def test_documentation_structure(self, doc_node):
        """Test that documentation has required sections."""
        shared = {
            "pg_tree": {
                "root": {"id": "root", "name": "Root", "depth": 0, "children": []}
            },
            "pg_summaries": {
                "root": {
                    "name": "Root",
                    "summary": "Test summary",
                    "io_endpoints": [],
                    "error_handling": []
                }
            },
            "virtual_groups": {},
            "workflow_id": "test-wf",
            "step_id": "doc",
            "user_request_id": "test-123"
        }
        
        prep_res = await doc_node.prep_async(shared)
        exec_res = await doc_node.exec_async(prep_res)
        
        assert exec_res["status"] == "success"
        assert "final_document" in exec_res
        assert "doc_sections" in exec_res
        
        doc = exec_res["final_document"]
        assert "#" in doc  # Should have markdown headers
        assert "```mermaid" in doc or "flowchart" in doc.lower()  # Should have diagram

