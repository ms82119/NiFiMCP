"""Integration tests for documentation workflow with real NiFi."""

import pytest
import uuid
from unittest.mock import AsyncMock, patch
from nifi_mcp_server.workflows.registry import get_workflow_registry


@pytest.mark.integration
@pytest.mark.slow
class TestDocWorkflowIntegration:
    """
    Integration tests that run against real NiFi.
    
    These tests require:
    - A running NiFi instance
    - MCP server running on localhost:8000
    - LLM mocking to avoid API costs
    
    Run manually with: pytest -m integration tests/workflows/test_doc_workflow_integration.py -v
    """
    
    @pytest.fixture
    def mock_llm_responses(self):
        """Load cached LLM responses for testing."""
        import json
        from pathlib import Path
        
        fixtures_dir = Path(__file__).parent.parent / "fixtures" / "llm_responses"
        responses = {}
        
        # Load cached responses
        for file in fixtures_dir.glob("*.json"):
            with open(file) as f:
                data = json.load(f)
                responses.update(data)
        
        # Default responses if files don't exist
        defaults = {
            "pg_summary_default": "This process group handles data ingestion and basic processing.",
            "virtual_groups_default": "[]",
            "executive_summary_default": "This flow processes data through multiple stages."
        }
        
        for key, value in defaults.items():
            if key not in responses:
                responses[key] = value
        
        return responses
    
    @pytest.fixture
    def mock_call_llm_async(self, mock_llm_responses):
        """
        Mock LLM calls to return cached responses.
        
        This prevents LLM API costs during testing.
        """
        async def _mock_call_llm(messages, tools, execution_state, action_id):
            # Match action_id pattern to cached response
            if "pg-summary" in action_id or "hierarchical-summary" in action_id:
                return {
                    "content": mock_llm_responses.get("pg_summary_default", ""),
                    "token_count_in": 1000,
                    "token_count_out": 100
                }
            elif "virtual-groups" in action_id:
                return {
                    "content": mock_llm_responses.get("virtual_groups_default", "[]"),
                    "token_count_in": 800,
                    "token_count_out": 50
                }
            elif "generation-exec-summary" in action_id or "executive-summary" in action_id:
                return {
                    "content": mock_llm_responses.get("executive_summary_default", ""),
                    "token_count_in": 2000,
                    "token_count_out": 200
                }
            else:
                return {
                    "content": "Mock LLM response for testing",
                    "token_count_in": 500,
                    "token_count_out": 50
                }
        
        return _mock_call_llm
    
    async def test_simple_flow_documentation(
        self,
        test_pg_with_processors,
        async_client,
        base_url,
        mcp_headers,
        mock_call_llm_async
    ):
        """Test documentation of simple flow with mocked LLM."""
        pg_id = test_pg_with_processors.get("pg_id")
        
        if not pg_id:
            pytest.skip("test_pg_with_processors fixture did not provide pg_id")
        
        # Create workflow executor
        registry = get_workflow_registry()
        executor = registry.create_async_executor("flow_documentation")
        
        if not executor:
            pytest.fail("Failed to create async executor for flow_documentation workflow")
        
        # Mock LLM calls in all nodes
        from nifi_mcp_server.workflows.nodes.doc_nodes import (
            HierarchicalAnalysisNode,
            DocumentationNode
        )
        
        # Get all nodes and mock their LLM calls
        all_nodes = executor._collect_all_nodes()
        for node in all_nodes:
            if hasattr(node, 'call_llm_async'):
                node.call_llm_async = mock_call_llm_async
        
        # Execute workflow with mocked LLM
        request_id = f"test-integration-{uuid.uuid4().hex[:8]}"
        result = await executor.execute_async({
            "user_request_id": request_id,
            "process_group_id": pg_id,
            "provider": "openai",
            "model_name": "gpt-4o-mini",
            "selected_nifi_server_id": mcp_headers["X-Nifi-Server-Id"],
            "nifi_server_id": mcp_headers["X-Nifi-Server-Id"],
            "workflow_id": request_id
        })
        
        # Verify result structure
        assert result is not None, "Workflow execution returned None"
        assert "shared_state" in result, "Result missing shared_state"
        
        shared = result.get("shared_state", {})
        
        # Check that workflow completed
        assert "final_document" in shared, "Workflow did not produce final_document"
        
        # Verify document structure
        final_doc = shared.get("final_document", "")
        assert len(final_doc) > 0, "Final document is empty"
        
        # Check for required sections (basic structure validation)
        assert "#" in final_doc, "Document should have markdown headers"
        
        # Verify workflow collected data
        assert "pg_summaries" in shared or "pg_tree" in shared, "Workflow should collect PG data"
    
    async def test_workflow_initialization(
        self,
        test_pg_with_processors,
        mcp_headers,
        mock_call_llm_async
    ):
        """Test that workflow initializes correctly with valid input."""
        pg_id = test_pg_with_processors.get("pg_id")
        
        if not pg_id:
            pytest.skip("test_pg_with_processors fixture did not provide pg_id")
        
        registry = get_workflow_registry()
        executor = registry.create_async_executor("flow_documentation")
        
        # Mock LLM calls
        all_nodes = executor._collect_all_nodes()
        for node in all_nodes:
            if hasattr(node, 'call_llm_async'):
                node.call_llm_async = mock_call_llm_async
        
        # Test initialization node
        from nifi_mcp_server.workflows.nodes.doc_nodes import InitializeDocNode
        
        init_node = None
        for node in all_nodes:
            if isinstance(node, InitializeDocNode):
                init_node = node
                break
        
        assert init_node is not None, "InitializeDocNode not found in workflow"
        
        # Test prep_async
        shared = {
            "user_request_id": "test-init",
            "process_group_id": pg_id,
            "provider": "openai",
            "model_name": "gpt-4o-mini",
            "selected_nifi_server_id": mcp_headers["X-Nifi-Server-Id"],
            "nifi_server_id": mcp_headers["X-Nifi-Server-Id"]
        }
        
        prep_res = await init_node.prep_async(shared)
        assert prep_res is not None, "prep_async returned None"
        assert "process_group_id" in prep_res, "prep_async missing process_group_id"
        
        # Test exec_async
        exec_res = await init_node.exec_async(prep_res)
        assert exec_res is not None, "exec_async returned None"
        assert exec_res.get("status") in ["success", "error"], "exec_async should return status"
    
    async def test_workflow_with_missing_pg_id(
        self,
        mcp_headers
    ):
        """Test that workflow handles missing process_group_id gracefully."""
        registry = get_workflow_registry()
        executor = registry.create_async_executor("flow_documentation")
        
        # Execute with missing process_group_id
        request_id = f"test-missing-pg-{uuid.uuid4().hex[:8]}"
        
        with pytest.raises((ValueError, KeyError, Exception)) as exc_info:
            result = await executor.execute_async({
                "user_request_id": request_id,
                # Missing process_group_id
                "provider": "openai",
                "model_name": "gpt-4o-mini",
                "selected_nifi_server_id": mcp_headers["X-Nifi-Server-Id"]
            })
        
        # Should raise an error about missing process_group_id
        error_msg = str(exc_info.value).lower()
        assert "process_group" in error_msg or "pg_id" in error_msg or "required" in error_msg, \
            f"Expected error about missing process_group_id, got: {error_msg}"

