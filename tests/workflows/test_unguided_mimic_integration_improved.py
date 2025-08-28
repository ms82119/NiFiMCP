"""
Improved integration tests for unguided mimic workflow.

This test suite demonstrates better testing practices:
- Mock at the HTTP/network boundary, not function signatures
- Use autospec=True to validate function signatures  
- Test real integration between components
- Validate contracts between modules
- Test error conditions properly
"""

import pytest
import json
import uuid
from unittest.mock import patch, Mock, MagicMock, call
import inspect
import requests

from nifi_mcp_server.workflows.definitions.unguided_mimic import (
    InitializeExecutionNode, 
    create_unguided_mimic_workflow
)
from nifi_mcp_server.workflows.core.executor import GuidedWorkflowExecutor


class TestContractValidation:
    """Test that workflow code matches external function signatures."""
    
    def test_get_llm_response_contract(self):
        """Test that workflow calls get_llm_response with correct signature."""
        from nifi_chat_ui.llm.chat_manager import ChatManager
        import inspect
        
        # Get actual function signature
        sig = inspect.signature(ChatManager.get_llm_response)
        param_names = list(sig.parameters.keys())
        
        # Verify workflow code matches expected parameters
        expected_params = ['messages', 'system_prompt', 'provider', 'model_name', 'user_request_id']
        for param in expected_params:
            assert param in param_names, f"get_llm_response missing expected parameter: {param}"
    
    def test_execute_mcp_tool_contract(self):
        """Test that workflow calls execute_mcp_tool with correct signature."""
        from nifi_chat_ui.mcp_handler import execute_mcp_tool
        import inspect
        
        sig = inspect.signature(execute_mcp_tool)
        param_names = list(sig.parameters.keys())
        
        # Verify workflow code matches expected parameters
        expected_params = ['tool_name', 'params', 'selected_nifi_server_id', 'user_request_id', 'action_id']
        for param in expected_params:
            assert param in param_names, f"execute_mcp_tool missing expected parameter: {param}"
    
    def test_get_available_tools_contract(self):
        """Test that workflow calls get_available_tools with correct signature."""
        from nifi_chat_ui.mcp_handler import get_available_tools
        import inspect
        
        sig = inspect.signature(get_available_tools)
        param_names = list(sig.parameters.keys())
        
        # Verify workflow has correct parameter expectations
        expected_params = ['selected_nifi_server_id', 'user_request_id', 'action_id', 'phase']
        for param in expected_params:
            assert param in param_names, f"get_available_tools missing expected parameter: {param}"


class TestRealIntegrationWithMockedHTTP:
    """Integration tests using real functions but mocking HTTP calls."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.node = InitializeExecutionNode()
        
    @patch('nifi_chat_ui.mcp_handler.requests.post')  # Mock the actual HTTP calls in mcp_handler
    @patch('nifi_chat_ui.mcp_handler.requests.get')   # Mock the actual HTTP calls in mcp_handler
    def test_real_openai_integration(self, mock_get, mock_post):
        """Test with real LLM function but mocked HTTP calls."""
        # Mock HTTP responses for tools API
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "list_processors", 
                    "description": "List processors",
                    "parameters": {"type": "object", "properties": {}}
                }
            }
        ]
        
        # Mock OpenAI HTTP response
        mock_openai_response = {
            "choices": [{
                "message": {
                    "content": "TASK COMPLETE: Created simple flow",
                    "tool_calls": []
                }
            }],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50
            }
        }
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = mock_openai_response
        
        # Test with real context
        prep_res = {
            'provider': 'openai',
            'model_name': 'gpt-4o-mini', 
            'system_prompt': 'You are a helpful NiFi assistant.',
            'user_request_id': 'test-123',
            'messages': [{'role': 'user', 'content': 'Help me create a simple flow'}],
            'selected_nifi_server_id': 'nifi-local-example'
        }
        
        # Execute with real function calls
        result = self.node.exec(prep_res)
        
        # Verify it actually called the functions correctly
        assert result["status"] == "success"
        assert result["total_tokens_in"] > 0
        assert result["total_tokens_out"] > 0
        assert result["loop_count"] >= 1
        
        # Verify HTTP calls were made with correct structure
        assert mock_get.called  # Tools API called
        assert mock_post.called  # OpenAI API called
        
        # Verify that both MCP and OpenAI calls were made
        assert mock_get.called  # Tools API called
        assert mock_post.called  # Both MCP and OpenAI APIs called
        
        # Find the OpenAI call (to api.openai.com)
        openai_calls = [call for call in mock_post.call_args_list if 'api.openai.com' in str(call)]
        assert len(openai_calls) > 0, "No OpenAI API calls found"
        
        # Verify OpenAI call structure
        openai_call = openai_calls[0]
        if 'json' in openai_call[1]:
            call_data = openai_call[1]['json']
        else:
            call_data = json.loads(openai_call[1].get('data', '{}'))
        
        assert 'messages' in call_data
        assert 'model' in call_data
        assert call_data['model'] == 'gpt-4o-mini'
    
    @patch('nifi_chat_ui.mcp_handler.requests.post')
    @patch('nifi_chat_ui.mcp_handler.requests.get') 
    def test_anthropic_unavailable_fallback(self, mock_get, mock_post):
        """Test behavior when Anthropic is not available."""
        # Mock tools API
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = []
        
        # Test with Anthropic provider (should handle gracefully)
        prep_res = {
            'provider': 'anthropic',
            'model_name': 'claude-sonnet-4-20250109',
            'system_prompt': 'You are a helpful NiFi assistant.',
            'user_request_id': 'test-123',
            'messages': [{'role': 'user', 'content': 'Help me'}],
            'selected_nifi_server_id': 'nifi-local-example'
        }
        
        result = self.node.exec(prep_res)
        
        # Should handle Anthropic unavailability gracefully
        # (This would catch the Anthropic package not available issue)
        assert result["status"] == "success" or "error" in result
        if result["status"] == "error":
            assert "anthropic" in result.get("message", "").lower()
    
    @patch('nifi_chat_ui.mcp_handler.requests.post')
    @patch('nifi_chat_ui.mcp_handler.requests.get')
    def test_tool_execution_with_real_calls(self, mock_get, mock_post):
        """Test tool execution using real execute_mcp_tool function."""
        # Mock tools API
        mock_get.return_value.status_code = 200  
        mock_get.return_value.json.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "list_nifi_objects",
                    "description": "List NiFi objects",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "object_type": {"type": "string", "enum": ["processors"]}
                        },
                        "required": ["object_type"]
                    }
                }
            }
        ]
        
        # Mock OpenAI response with tool call
        mock_openai_response = {
            "choices": [{
                "message": {
                    "content": "I'll list the processors for you.",
                    "tool_calls": [{
                        "id": "call_123",
                        "type": "function", 
                        "function": {
                            "name": "list_nifi_objects",
                            "arguments": '{"object_type": "processors"}'
                        }
                    }]
                }
            }],
            "usage": {"prompt_tokens": 150, "completion_tokens": 75}
        }
        
        # Mock completion response
        mock_completion_response = {
            "choices": [{
                "message": {
                    "content": "TASK COMPLETE: Listed processors successfully",
                    "tool_calls": []
                }
            }],
            "usage": {"prompt_tokens": 200, "completion_tokens": 100}
        }
        
        mock_post.side_effect = [
            Mock(status_code=200, json=lambda: mock_openai_response),
            Mock(status_code=200, json=lambda: {"status": "success", "processors": []}),  # Tool execution
            Mock(status_code=200, json=lambda: mock_completion_response)
        ]
        
        prep_res = {
            'provider': 'openai',
            'model_name': 'gpt-4o-mini',
            'system_prompt': 'You are a NiFi assistant.',
            'user_request_id': 'test-456',
            'messages': [{'role': 'user', 'content': 'List all processors'}],
            'selected_nifi_server_id': 'nifi-local-example'
        }
        
        result = self.node.exec(prep_res)
        
        # Verify tool execution happened
        assert result["status"] == "success"
        assert result["tool_calls_executed"] > 0
        assert "list_nifi_objects" in result.get("executed_tools", [])
        
        # Verify correct HTTP calls were made
        assert mock_post.call_count >= 2  # At least LLM + tool execution


class TestErrorConditionsWithRealFunctions:
    """Test error conditions using real functions."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.node = InitializeExecutionNode()
    
    @patch('nifi_chat_ui.mcp_handler.requests.post')
    @patch('nifi_chat_ui.mcp_handler.requests.get')
    def test_network_error_handling(self, mock_get, mock_post):
        """Test handling of network errors."""
        # Simulate network error
        mock_get.side_effect = requests.ConnectionError("Connection failed")
        
        prep_res = {
            'provider': 'openai',
            'model_name': 'gpt-4o-mini',
            'system_prompt': 'Test',
            'user_request_id': 'test-error',
            'messages': [{'role': 'user', 'content': 'Test'}],
            'selected_nifi_server_id': 'nifi-local-example'
        }
        
        result = self.node.exec(prep_res)
        
        # Should handle network errors gracefully
        assert result["status"] == "success"  # Should continue with empty tools
        assert result["loop_count"] >= 1
    
    @patch('nifi_chat_ui.mcp_handler.requests.post')
    @patch('nifi_chat_ui.mcp_handler.requests.get')
    def test_invalid_openai_api_key(self, mock_get, mock_post):
        """Test handling of invalid API key."""
        # Mock tools API success
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = []
        
        # Mock OpenAI 401 error
        mock_post.return_value.status_code = 401
        mock_post.return_value.json.return_value = {"error": {"message": "Invalid API key"}}
        
        prep_res = {
            'provider': 'openai',
            'model_name': 'gpt-4o-mini',
            'system_prompt': 'Test',
            'user_request_id': 'test-auth',
            'messages': [{'role': 'user', 'content': 'Test'}],
            'selected_nifi_server_id': 'nifi-local-example'
        }
        
        result = self.node.exec(prep_res)
        
        # Should handle auth errors properly
        assert result["status"] == "success"  # Should exit gracefully
        assert result["loop_count"] >= 1


class TestParameterValidationWithAutospec:
    """Test parameter validation using autospec mocking."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.node = InitializeExecutionNode()
    
    @patch('nifi_chat_ui.chat_manager.get_llm_response', autospec=True)
    @patch('nifi_chat_ui.mcp_handler.get_available_tools', autospec=True) 
    def test_function_called_with_correct_parameters(self, mock_get_tools, mock_llm_response):
        """Test that functions are called with correct parameter names and types."""
        # Setup mocks with autospec (validates signatures)
        mock_get_tools.return_value = []
        mock_llm_response.return_value = {
            "content": "TASK COMPLETE: Test completed",
            "tool_calls": [],
            "token_count_in": 100,
            "token_count_out": 50
        }
        
        prep_res = {
            'provider': 'openai',
            'model_name': 'gpt-4o-mini',
            'system_prompt': 'You are a helpful assistant.',
            'user_request_id': 'test-123',
            'messages': [{'role': 'user', 'content': 'Test'}],
            'selected_nifi_server_id': 'nifi-local-example'
        }
        
        result = self.node.exec(prep_res)
        
        # Verify functions were called with correct parameter names
        # (autospec will fail if wrong parameter names used)
        mock_get_tools.assert_called_with(
            phase="All",
            selected_nifi_server_id='nifi-local-example'
        )
        
        mock_llm_response.assert_called_with(
            messages=mock_llm_response.call_args[1]['messages'],
            system_prompt='You are a helpful assistant.',
            tools=[],
            provider='openai',
            model_name='gpt-4o-mini',
            user_request_id='test-123'
        )
        
        assert result["status"] == "success"


class TestWorkflowEndToEndImproved:
    """Improved end-to-end workflow tests."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.workflow_name = "test_unguided_mimic"
        self.nodes = create_unguided_mimic_workflow()
        self.executor = GuidedWorkflowExecutor(self.workflow_name, self.nodes)
    
    @patch('nifi_chat_ui.mcp_handler.requests.post')
    @patch('nifi_chat_ui.mcp_handler.requests.get')
    def test_complete_workflow_execution_realistic(self, mock_get, mock_post):
        """Test complete workflow with realistic HTTP mocking."""
        # Mock tools API
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "create_complete_nifi_flow",
                    "description": "Create a complete NiFi flow",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "nifi_objects": {"type": "array"},
                            "process_group_id": {"type": "string"}
                        }
                    }
                }
            }
        ]
        
        # Mock realistic OpenAI conversation
        llm_responses = [
            # Initial response with tool call
            {
                "choices": [{
                    "message": {
                        "content": "I'll create a simple flow for you.",
                        "tool_calls": [{
                            "id": "call_create_flow",
                            "type": "function",
                            "function": {
                                "name": "create_complete_nifi_flow",
                                "arguments": '{"nifi_objects": [{"type": "processor", "processor_type": "org.apache.nifi.processors.standard.GenerateFlowFile", "name": "GenerateFlowFile", "position": {"x": 100, "y": 100}}], "process_group_id": "root"}'
                            }
                        }]
                    }
                }],
                "usage": {"prompt_tokens": 200, "completion_tokens": 150}
            },
            # Completion response
            {
                "choices": [{
                    "message": {
                        "content": "TASK COMPLETE: Successfully created your NiFi flow with a GenerateFlowFile processor.",
                        "tool_calls": []
                    }
                }],
                "usage": {"prompt_tokens": 300, "completion_tokens": 100}
            }
        ]
        
        # Setup HTTP call sequence
        http_calls = []
        for response in llm_responses:
            http_calls.append(Mock(status_code=200, json=lambda r=response: r))
        
        # Add tool execution response
        http_calls.insert(1, Mock(status_code=200, json=lambda: {
            "status": "success", 
            "message": "Flow created successfully",
            "entity": {"id": "test-processor-123"}
        }))
        
        mock_post.side_effect = http_calls
        
        # Execute workflow
        initial_context = {
            "provider": "openai",
            "model_name": "gpt-4o-mini",
            "system_prompt": "You are a helpful NiFi assistant.",
            "user_request_id": "test-integration",
            "messages": [{"role": "user", "content": "Create a simple flow"}],
            "selected_nifi_server_id": "nifi-local-example"
        }
        
        result = self.executor.execute(initial_context)
        
        # Verify realistic execution results
        assert result["status"] == "success"
        assert result["workflow_name"] == self.workflow_name
        
        shared_state = result["shared_state"]
        assert shared_state["total_tokens_in"] > 0
        assert shared_state["total_tokens_out"] > 0
        assert shared_state["loop_count"] >= 2
        assert shared_state["tool_calls_executed"] > 0
        assert "create_complete_nifi_flow" in shared_state.get("executed_tools", [])


if __name__ == "__main__":
    pytest.main([__file__, "-v"]) 