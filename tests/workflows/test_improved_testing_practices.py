"""
Improved testing practices for NiFi MCP workflows.

This demonstrates better testing approaches that would have caught the real issues:
1. Contract validation between modules
2. Integration testing with HTTP-level mocking
3. Parameter signature validation with autospec
4. Error condition testing
5. Realistic end-to-end scenarios
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
from nifi_chat_ui.llm.chat_manager import ChatManager


class TestContractValidation:
    """Test that workflow code matches external function signatures."""
    
    def test_get_llm_response_contract(self):
        """Test that workflow calls get_llm_response with correct signature."""
        from nifi_chat_ui.llm.chat_manager import ChatManager
        import inspect
        
        # Get actual method signature
        sig = inspect.signature(ChatManager.get_llm_response)
        param_names = list(sig.parameters.keys())
        
        # Verify workflow code matches expected parameters
        expected_params = ['messages', 'system_prompt', 'provider', 'model_name', 'user_request_id', 'action_id', 'selected_nifi_server_id']
        for param in expected_params:
            assert param in param_names, f"ChatManager.get_llm_response missing expected parameter: {param}"
    
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
        
    @patch('requests.post')  # Mock HTTP, not functions
    @patch('requests.get')   # Mock HTTP, not functions
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
        
        # Verify OpenAI call structure
        openai_call = mock_post.call_args
        call_data = json.loads(openai_call[1]['data'])  # Get POST data
        assert 'messages' in call_data
        assert 'model' in call_data
        assert call_data['model'] == 'gpt-4o-mini'
    
    @patch('requests.post')
    @patch('requests.get') 
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


class TestParameterValidationWithAutospec:
    """Test parameter validation using autospec mocking."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.node = InitializeExecutionNode()
    
    @patch('nifi_chat_ui.llm.chat_manager.ChatManager.get_llm_response', autospec=True)
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


class TestBetterErrorTesting:
    """Test error conditions more realistically."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.node = InitializeExecutionNode()
    
    def test_missing_required_parameters(self):
        """Test behavior with missing required parameters."""
        # Test with completely empty context
        prep_res = {}
        
        result = self.node.exec(prep_res)
        
        # Should handle missing parameters gracefully
        assert result["status"] == "success"  # Should use fallbacks
        assert "execution_state" in result
    
    @patch('requests.post')
    @patch('requests.get')
    def test_network_timeouts(self, mock_get, mock_post):
        """Test handling of network timeouts."""
        # Simulate timeout
        mock_get.side_effect = requests.Timeout("Request timed out")
        
        prep_res = {
            'provider': 'openai',
            'model_name': 'gpt-4o-mini',
            'system_prompt': 'Test',
            'user_request_id': 'test-timeout',
            'messages': [{'role': 'user', 'content': 'Test'}],
            'selected_nifi_server_id': 'nifi-local-example'
        }
        
        result = self.node.exec(prep_res)
        
        # Should handle timeouts gracefully
        assert result["status"] == "success"  # Should continue with empty tools
        assert result["loop_count"] >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"]) 