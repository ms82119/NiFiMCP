"""
Pytest configuration and fixtures for workflow tests.

Provides common fixtures and test setup for workflow-related tests.
"""

import pytest
from unittest.mock import Mock, patch
import tempfile
import os
from pathlib import Path

# Workflow-specific imports
from nifi_mcp_server.workflows.core.context_manager import ContextManager
from nifi_mcp_server.workflows.core.progress_tracker import ProgressTracker
from nifi_mcp_server.workflows.registry import WorkflowRegistry


@pytest.fixture
def mock_context_manager():
    """Mock context manager for testing."""
    context_manager = Mock(spec=ContextManager)
    context_manager.shared_state = {}
    context_manager.step_results = {}
    context_manager.key_milestones = []
    
    # Mock methods
    context_manager.curate_context_for_step.return_value = {
        "test_context": "value",
        "messages": [{"role": "user", "content": "Test"}]
    }
    context_manager.update_shared_state.return_value = None
    context_manager.store_step_result.return_value = None
    
    return context_manager


@pytest.fixture
def mock_progress_tracker():
    """Mock progress tracker for testing."""
    progress_tracker = Mock(spec=ProgressTracker)
    progress_tracker.workflow_name = "test_workflow"
    progress_tracker.start_time = "2024-01-01T00:00:00Z"
    
    # Mock methods
    progress_tracker.start_workflow.return_value = None
    progress_tracker.update_step_status.return_value = None
    progress_tracker.complete_workflow.return_value = None
    progress_tracker.get_progress_summary.return_value = {
        "workflow_name": "test_workflow",
        "status": "running",
        "current_step": "test_step",
        "steps_completed": 0,
        "total_steps": 1
    }
    progress_tracker.get_step_details.return_value = []
    progress_tracker.get_current_step_info.return_value = {
        "step_name": "test_step",
        "status": "running"
    }
    
    return progress_tracker


@pytest.fixture
def clean_workflow_registry():
    """Clean workflow registry for testing."""
    registry = WorkflowRegistry()
    return registry


@pytest.fixture
def mock_request_context():
    """Mock request context for testing."""
    with patch('config.logging_setup.request_context') as mock_context:
        mock_context.get.return_value = {
            "user_request_id": "test-req-123",
            "action_id": "test-act-456",
            "workflow_id": "test-wf-789",
            "step_id": "test-step-abc"
        }
        yield mock_context


@pytest.fixture
def mock_llm_response():
    """Mock LLM response for testing."""
    return {
        "content": "Test response from LLM",
        "tool_calls": [],
        "token_count_in": 100,
        "token_count_out": 50
    }


@pytest.fixture
def mock_tool_list():
    """Mock list of available tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": "list_processors",
                "description": "List all NiFi processors",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "create_processor",
                "description": "Create a new NiFi processor",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "processor_type": {
                            "type": "string",
                            "description": "Type of processor to create"
                        }
                    },
                    "required": ["processor_type"]
                }
            }
        }
    ]


@pytest.fixture
def sample_workflow_context():
    """Sample workflow execution context."""
    return {
        "provider": "anthropic",
        "model_name": "claude-sonnet-4-20250109",
        "system_prompt": "You are a helpful NiFi assistant.",
        "user_request_id": "test-req-123",
        "messages": [
            {"role": "user", "content": "Help me create a NiFi flow"}
        ],
        "selected_nifi_server_id": "test-server",
        "selected_phase": "All",
        "current_objective": "Create a simple data processing flow"
    }


@pytest.fixture
def temp_log_directory():
    """Temporary directory for log files during testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture(autouse=True)
def setup_test_environment():
    """Set up test environment for all workflow tests."""
    # Set environment variables for testing
    os.environ["TESTING"] = "true"
    
    # Mock critical imports that might not be available in test environment
    with patch('nifi_chat_ui.llm.chat_manager.ChatManager.get_llm_response'), \
         patch('nifi_chat_ui.mcp_handler.get_available_tools'), \
         patch('nifi_chat_ui.mcp_handler.execute_mcp_tool'):
        yield
    
    # Clean up
    if "TESTING" in os.environ:
        del os.environ["TESTING"]


# Override async fixtures for workflow tests - they don't need them
@pytest.fixture
def async_client():
    """Mock async client for workflow tests (not needed)."""
    return Mock()


@pytest.fixture
def check_server_connectivity():
    """Mock check_server_connectivity for workflow tests (not needed)."""
    return None


@pytest.fixture
def mock_workflow_execution_result():
    """Mock result from workflow execution."""
    return {
        "status": "success",
        "workflow_name": "test_workflow",
        "message": "Workflow completed successfully",
        "shared_state": {
            "execution_state": {
                "provider": "anthropic",
                "model_name": "claude-sonnet-4-20250109",
                "loop_count": 2,
                "total_tokens_in": 200,
                "total_tokens_out": 100
            },
            "final_messages": [
                {"role": "assistant", "content": "Task completed successfully"}
            ],
            "total_tokens_in": 200,
            "total_tokens_out": 100,
            "loop_count": 2,
            "task_complete": True,
            "executed_tools": ["list_processors"]
        },
        "progress_summary": {
            "workflow_name": "test_workflow",
            "status": "completed",
            "steps_completed": 1,
            "total_steps": 1
        },
        "step_details": [
            {
                "step_name": "initialize_execution",
                "status": "completed",
                "duration": 1.5
            }
        ]
    }


# Test configuration
def pytest_configure(config):
    """Configure pytest for workflow tests."""
    # Add custom markers
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )
    config.addinivalue_line(
        "markers", "unit: marks tests as unit tests"
    )
    config.addinivalue_line(
        "markers", "workflow: marks tests as workflow-related"
    )


# Test collection configuration
def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers automatically."""
    for item in items:
        # Mark all tests in this directory as workflow tests
        if "workflows" in str(item.fspath):
            item.add_marker(pytest.mark.workflow)
            
        # Mark integration tests
        if "integration" in item.name or "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
            
        # Mark unit tests
        if "test_" in item.name and "integration" not in item.name:
            item.add_marker(pytest.mark.unit) 