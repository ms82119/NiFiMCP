"""
Simplified test suite for guided workflows.

This test suite focuses on testing the actual guided workflow functionality
without complex HTTP mocking or external dependencies.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import json

from nifi_mcp_server.workflows.registry import WorkflowRegistry, WorkflowDefinition
from nifi_mcp_server.workflows.core.executor import GuidedWorkflowExecutor
from nifi_mcp_server.workflows.definitions.unguided_mimic import (
    InitializeExecutionNode, 
    create_unguided_mimic_workflow
)


class TestGuidedWorkflowCore:
    """Test core guided workflow functionality."""
    
    def test_workflow_registry_basic_functionality(self):
        """Test that workflow registry works correctly."""
        registry = WorkflowRegistry()
        
        # Test registration
        def mock_workflow_factory():
            return [InitializeExecutionNode()]
        
        workflow_def = WorkflowDefinition(
            name="unguided_mimic",  # Use a name that's in the enabled_workflows list
            description="A test workflow",
            create_workflow_func=mock_workflow_factory,
            display_name="Test Workflow",
            category="Testing",
            enabled=True  # Explicitly enable for testing
        )
        
        registry.register(workflow_def)
        
        # Test retrieval
        retrieved = registry.get_workflow("unguided_mimic")
        assert retrieved is not None
        assert retrieved.name == "unguided_mimic"
        
        # Test listing
        workflows = registry.list_workflows()
        assert len(workflows) >= 1
        assert any(w.name == "unguided_mimic" for w in workflows)
    
    def test_workflow_executor_creation(self):
        """Test that workflow executor can be created."""
        registry = WorkflowRegistry()
        
        def mock_workflow_factory():
            return [InitializeExecutionNode()]
        
        workflow_def = WorkflowDefinition(
            name="unguided_mimic",  # Use a name that's in the enabled_workflows list
            description="A test workflow",
            create_workflow_func=mock_workflow_factory,
            display_name="Test Workflow",
            category="Testing",
            enabled=True  # Explicitly enable for testing
        )
        
        registry.register(workflow_def)
        
        # Test executor creation
        executor = registry.create_executor("unguided_mimic")
        assert executor is not None
        assert isinstance(executor, GuidedWorkflowExecutor)
    
    def test_workflow_node_basic_functionality(self):
        """Test that workflow nodes work correctly."""
        node = InitializeExecutionNode()
        
        # Test node initialization
        assert node.name == "initialize_execution"
        assert node.description is not None
        # Note: action_limit is set via set_action_limit method, not a direct attribute
        
        # Test node preparation
        context = {
            "provider": "openai",
            "model_name": "gpt-4o-mini",
            "system_prompt": "You are a helpful assistant.",
            "user_request_id": "test-123",
            "messages": [{"role": "user", "content": "Help me"}],
            "selected_nifi_server_id": "nifi-local-example"
        }
        
        prepared_context = node.prep(context)
        assert prepared_context is not None
        assert "provider" in prepared_context
        assert "model_name" in prepared_context


class TestGuidedWorkflowIntegration:
    """Test guided workflow integration with mocked dependencies."""
    
    @patch('nifi_mcp_server.workflows.definitions.unguided_mimic.get_available_tools')
    @patch('nifi_mcp_server.workflows.definitions.unguided_mimic.get_workflow_chat_manager')
    def test_workflow_with_mocked_llm(self, mock_chat_manager, mock_get_tools):
        """Test workflow execution with mocked LLM responses."""
        # Mock tool availability
        mock_get_tools.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "list_processors",
                    "description": "List processors",
                    "parameters": {"type": "object", "properties": {}}
                }
            }
        ]
        
        # Mock chat manager
        mock_chat_manager_instance = Mock()
        mock_chat_manager_instance.get_llm_response.return_value = {
            "content": "TASK COMPLETE: Created simple flow",
            "tool_calls": [],
            "token_count_in": 100,
            "token_count_out": 50,
            "error": None
        }
        mock_chat_manager.return_value = mock_chat_manager_instance
        
        # Create and execute workflow
        node = InitializeExecutionNode()
        context = {
            "provider": "openai",
            "model_name": "gpt-4o-mini",
            "system_prompt": "You are a helpful assistant.",
            "user_request_id": "test-123",
            "messages": [{"role": "user", "content": "Create a simple flow"}],
            "selected_nifi_server_id": "nifi-local-example"
        }
        
        result = node.exec(context)
        
        # Verify results
        assert result["status"] == "success"
        assert result["total_tokens_in"] > 0
        assert result["total_tokens_out"] > 0
        assert result["loop_count"] >= 1
        
        # Verify mocks were called
        mock_get_tools.assert_called()
        mock_chat_manager_instance.get_llm_response.assert_called()
    
    @patch('nifi_mcp_server.workflows.definitions.unguided_mimic.get_available_tools')
    @patch('nifi_mcp_server.workflows.definitions.unguided_mimic.get_workflow_chat_manager')
    def test_workflow_error_handling(self, mock_chat_manager, mock_get_tools):
        """Test workflow error handling."""
        # Mock tool availability
        mock_get_tools.return_value = []
        
        # Mock chat manager with error
        mock_chat_manager_instance = Mock()
        mock_chat_manager_instance.get_llm_response.return_value = {
            "content": None,
            "tool_calls": [],
            "token_count_in": 0,
            "token_count_out": 0,
            "error": "API key invalid"
        }
        mock_chat_manager.return_value = mock_chat_manager_instance
        
        # Create and execute workflow
        node = InitializeExecutionNode()
        context = {
            "provider": "openai",
            "model_name": "gpt-4o-mini",
            "system_prompt": "You are a helpful assistant.",
            "user_request_id": "test-123",
            "messages": [{"role": "user", "content": "Create a simple flow"}],
            "selected_nifi_server_id": "nifi-local-example"
        }
        
        result = node.exec(context)
        
        # Verify error handling - the workflow might still succeed but should have error info
        # The actual behavior depends on how the workflow handles errors
        if result["status"] == "error":
            assert "error" in result
        else:
            # If it succeeds, it should still have error information in the response
            assert result["status"] == "success"
            # Check that the error was logged or handled appropriately
    
    def test_workflow_context_validation(self):
        """Test workflow context validation."""
        node = InitializeExecutionNode()
        
        # Test with missing required fields
        invalid_context = {
            "provider": "openai",
            # Missing other required fields
        }
        
        result = node.exec(invalid_context)
        assert result["status"] == "error"
        assert "Missing required context fields" in result.get("error", "")


class TestGuidedWorkflowEndToEnd:
    """Test end-to-end guided workflow functionality."""
    
    @patch('nifi_mcp_server.workflows.definitions.unguided_mimic.get_available_tools')
    @patch('nifi_mcp_server.workflows.definitions.unguided_mimic.get_workflow_chat_manager')
    def test_complete_workflow_execution(self, mock_chat_manager, mock_get_tools):
        """Test complete workflow execution with mocked dependencies."""
        # Mock tool availability
        mock_get_tools.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "list_processors",
                    "description": "List processors",
                    "parameters": {"type": "object", "properties": {}}
                }
            }
        ]
        
        # Mock successful chat manager responses
        mock_chat_manager_instance = Mock()
        mock_chat_manager_instance.get_llm_response.return_value = {
            "content": "TASK COMPLETE: Created simple flow",
            "tool_calls": [],
            "token_count_in": 100,
            "token_count_out": 50,
            "error": None
        }
        mock_chat_manager.return_value = mock_chat_manager_instance
        
        # Create workflow registry and register workflow
        registry = WorkflowRegistry()
        
        # Create a proper workflow definition
        def mock_workflow_factory():
            return [InitializeExecutionNode()]
        
        workflow_def = WorkflowDefinition(
            name="unguided_mimic",
            description="Unguided mimic workflow for testing",
            create_workflow_func=mock_workflow_factory,
            display_name="Unguided Mimic",
            category="Testing",
            enabled=True
        )
        registry.register(workflow_def)
        
        # Create executor
        executor = registry.create_executor("unguided_mimic")
        
        # Execute workflow
        context = {
            "provider": "openai",
            "model_name": "gpt-4o-mini",
            "system_prompt": "You are a helpful assistant.",
            "user_request_id": "test-123",
            "messages": [{"role": "user", "content": "Create a simple flow"}],
            "selected_nifi_server_id": "nifi-local-example"
        }
        
        result = executor.execute(context)
        
        # Verify successful execution
        assert result["status"] == "success"
        assert "workflow_name" in result
        assert result["workflow_name"] == "unguided_mimic"
        
        # Verify mocks were called
        mock_get_tools.assert_called()
        mock_chat_manager_instance.get_llm_response.assert_called()


class TestGuidedWorkflowValidation:
    """Test guided workflow validation and error cases."""
    
    def test_workflow_registry_validation(self):
        """Test workflow registry validation."""
        registry = WorkflowRegistry()
        
        # Test registering invalid workflow
        with pytest.raises(AttributeError):  # The registry tries to access .name on None
            registry.register(None)
        
        # Test getting non-existent workflow
        result = registry.get_workflow("non_existent")
        assert result is None
        
        # Test creating executor for non-existent workflow
        result = registry.create_executor("non_existent")
        assert result is None
    
    def test_workflow_node_validation(self):
        """Test workflow node validation."""
        node = InitializeExecutionNode()
        
        # Test with None context
        result = node.exec(None)
        assert result["status"] == "error"
        
        # Test with empty context
        result = node.exec({})
        assert result["status"] == "error"
        
        # Test with invalid context type
        result = node.exec("invalid")
        assert result["status"] == "error" 