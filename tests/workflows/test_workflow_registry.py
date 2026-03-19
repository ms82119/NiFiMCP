"""
Unit tests for workflow registry functionality.

Tests workflow registration, discovery, and executor creation.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import List

from nifi_mcp_server.workflows.registry import (
    WorkflowDefinition, 
    WorkflowRegistry, 
    register_workflow,
    get_workflow_registry
)
from nifi_mcp_server.workflows.core.executor import GuidedWorkflowExecutor
from nifi_mcp_server.workflows.nodes.base_node import WorkflowNode


class MockWorkflowNode(WorkflowNode):
    """Mock workflow node for testing."""
    
    def __init__(self, name="mock_node"):
        super().__init__(name=name)
        self.exec_called = False
        
    def exec(self, prep_res):
        self.exec_called = True
        return {"status": "success", "message": "Mock execution"}


def mock_workflow_factory() -> List[WorkflowNode]:
    """Mock factory function for creating workflow nodes."""
    return [MockWorkflowNode("mock_node_1"), MockWorkflowNode("mock_node_2")]


class TestWorkflowDefinition:
    """Test cases for WorkflowDefinition class."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.workflow_def = WorkflowDefinition(
            name="test_workflow",
            display_name="Test Workflow",
            description="A test workflow for testing",
            category="Testing",
            phases=["Review", "Creation"],
            create_workflow_func=mock_workflow_factory,
            enabled=True
        )
        
    def test_initialization(self):
        """Test workflow definition initializes correctly."""
        assert self.workflow_def.name == "test_workflow"
        assert self.workflow_def.display_name == "Test Workflow"
        assert self.workflow_def.description == "A test workflow for testing"
        assert self.workflow_def.category == "Testing"
        assert self.workflow_def.phases == ["Review", "Creation"]
        assert self.workflow_def.factory == mock_workflow_factory
        assert self.workflow_def.enabled is True
        
    def test_create_nodes(self):
        """Test create_nodes method calls factory correctly."""
        nodes = self.workflow_def.create_nodes()
        
        assert len(nodes) == 2
        assert all(isinstance(node, MockWorkflowNode) for node in nodes)
        assert nodes[0].name == "mock_node_1"
        assert nodes[1].name == "mock_node_2"
        
    def test_create_nodes_error_handling(self):
        """Test create_nodes handles factory errors."""
        def failing_factory():
            raise ValueError("Factory failed")
            
        failing_def = WorkflowDefinition(
            name="failing_workflow",
            display_name="Failing Workflow",
            description="A workflow that fails",
            category="Testing",
            phases=["Review"],
            create_workflow_func=failing_factory
        )
        
        with pytest.raises(ValueError) as exc_info:
            failing_def.create_nodes()
        assert "Factory failed" in str(exc_info.value)
        
    def test_to_dict(self):
        """Test to_dict method returns correct dictionary."""
        result = self.workflow_def.to_dict()
        
        expected = {
            "name": "test_workflow",
            "display_name": "Test Workflow",
            "description": "A test workflow for testing",
            "category": "Testing",
            "phases": ["Review", "Creation"],
            "enabled": True,
            "is_async": False
        }
        
        assert result == expected


class TestWorkflowRegistry:
    """Test cases for WorkflowRegistry class."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.registry = WorkflowRegistry()
        
        # Create test workflow definitions
        self.workflow_def_1 = WorkflowDefinition(
            name="workflow_1",
            display_name="Workflow One",
            description="First test workflow",
            category="Basic",
            phases=["Review"],
            create_workflow_func=mock_workflow_factory,
            enabled=True
        )
        
        self.workflow_def_2 = WorkflowDefinition(
            name="workflow_2",
            display_name="Workflow Two",
            description="Second test workflow",
            category="Advanced",
            phases=["Creation", "Modification"],
            create_workflow_func=mock_workflow_factory,
            enabled=True
        )
        
        self.disabled_workflow = WorkflowDefinition(
            name="disabled_workflow",
            display_name="Disabled Workflow",
            description="A disabled workflow",
            category="Basic",
            phases=["Review"],
            create_workflow_func=mock_workflow_factory,
            enabled=False
        )
        
    def test_registry_initialization(self):
        """Test registry initializes correctly."""
        assert len(self.registry._workflows) == 0
        assert len(self.registry._categories) == 0
        
    @patch('nifi_mcp_server.workflows.registry.is_workflow_enabled')
    def test_register_workflow(self, mock_is_enabled):
        """Test workflow registration."""
        mock_is_enabled.return_value = True
        
        self.registry.register(self.workflow_def_1)
        
        # Check workflow was registered
        assert "workflow_1" in self.registry._workflows
        assert self.registry._workflows["workflow_1"] == self.workflow_def_1
        
        # Check category was updated
        assert "Basic" in self.registry._categories
        assert "workflow_1" in self.registry._categories["Basic"]
        
    @patch('nifi_mcp_server.workflows.registry.is_workflow_enabled')
    def test_register_disabled_workflow(self, mock_is_enabled):
        """Test registering disabled workflow."""
        mock_is_enabled.return_value = False
        
        self.registry.register(self.workflow_def_1)
        
        # Workflow should be registered but marked as disabled
        assert "workflow_1" in self.registry._workflows
        assert self.registry._workflows["workflow_1"].enabled is False
        
    def test_get_workflow(self):
        """Test retrieving workflow by name."""
        self.registry.register(self.workflow_def_1)
        
        # Existing workflow
        result = self.registry.get_workflow("workflow_1")
        assert result == self.workflow_def_1
        
        # Non-existing workflow
        result = self.registry.get_workflow("nonexistent")
        assert result is None
        
    @patch('nifi_mcp_server.workflows.registry.is_workflow_enabled')
    def test_list_workflows_no_filters(self, mock_is_enabled):
        """Test listing all workflows."""
        # Mock enabled status for test workflows
        def enabled_side_effect(workflow_name):
            return workflow_name != "disabled_workflow"
        mock_is_enabled.side_effect = enabled_side_effect
        
        self.registry.register(self.workflow_def_1)
        self.registry.register(self.workflow_def_2)
        self.registry.register(self.disabled_workflow)
        
        # All enabled workflows
        workflows = self.registry.list_workflows(enabled_only=True)
        assert len(workflows) == 2
        workflow_names = [w.name for w in workflows]
        assert "workflow_1" in workflow_names
        assert "workflow_2" in workflow_names
        assert "disabled_workflow" not in workflow_names
        
        # All workflows including disabled
        workflows = self.registry.list_workflows(enabled_only=False)
        assert len(workflows) == 3
        
    @patch('nifi_mcp_server.workflows.registry.is_workflow_enabled')
    def test_list_workflows_category_filter(self, mock_is_enabled):
        """Test listing workflows filtered by category."""
        mock_is_enabled.return_value = True  # Enable all test workflows
        
        self.registry.register(self.workflow_def_1)
        self.registry.register(self.workflow_def_2)
        
        # Filter by Basic category
        workflows = self.registry.list_workflows(category="Basic")
        assert len(workflows) == 1
        assert workflows[0].name == "workflow_1"
        
        # Filter by Advanced category
        workflows = self.registry.list_workflows(category="Advanced")
        assert len(workflows) == 1
        assert workflows[0].name == "workflow_2"
        
        # Filter by non-existing category
        workflows = self.registry.list_workflows(category="NonExistent")
        assert len(workflows) == 0
        
    @patch('nifi_mcp_server.workflows.registry.is_workflow_enabled')
    def test_list_workflows_phase_filter(self, mock_is_enabled):
        """Test listing workflows filtered by phase."""
        mock_is_enabled.return_value = True  # Enable all test workflows
        
        self.registry.register(self.workflow_def_1)
        self.registry.register(self.workflow_def_2)
        
        # Filter by Review phase
        workflows = self.registry.list_workflows(phase="Review")
        assert len(workflows) == 1
        assert workflows[0].name == "workflow_1"
        
        # Filter by Creation phase
        workflows = self.registry.list_workflows(phase="Creation")
        assert len(workflows) == 1
        assert workflows[0].name == "workflow_2"
        
        # Filter by non-existing phase
        workflows = self.registry.list_workflows(phase="NonExistent")
        assert len(workflows) == 0
        
    def test_get_categories(self):
        """Test getting list of categories."""
        self.registry.register(self.workflow_def_1)
        self.registry.register(self.workflow_def_2)
        
        categories = self.registry.get_categories()
        assert "Basic" in categories
        assert "Advanced" in categories
        assert len(categories) == 2
        
    @patch('nifi_mcp_server.workflows.registry.is_workflow_enabled')
    def test_get_workflows_by_category(self, mock_is_enabled):
        """Test getting workflows grouped by category."""
        mock_is_enabled.return_value = True  # Enable all test workflows
        
        self.registry.register(self.workflow_def_1)
        self.registry.register(self.workflow_def_2)
        
        result = self.registry.get_workflows_by_category()
        
        assert "Basic" in result
        assert "Advanced" in result
        assert len(result["Basic"]) == 1
        assert len(result["Advanced"]) == 1
        assert result["Basic"][0]["name"] == "workflow_1"
        assert result["Advanced"][0]["name"] == "workflow_2"
        
    @patch('nifi_mcp_server.workflows.registry.is_workflow_enabled')
    @patch('nifi_mcp_server.workflows.registry.GuidedWorkflowExecutor')
    def test_create_executor_success(self, mock_executor_class, mock_is_enabled):
        """Test successful executor creation."""
        mock_is_enabled.return_value = True  # Enable workflow
        mock_executor = Mock()
        mock_executor_class.return_value = mock_executor
        
        self.registry.register(self.workflow_def_1)
        
        executor = self.registry.create_executor("workflow_1")
        
        # Verify executor was created
        assert executor == mock_executor
        mock_executor_class.assert_called_once()
        
    def test_create_executor_workflow_not_found(self):
        """Test executor creation for non-existent workflow."""
        executor = self.registry.create_executor("nonexistent")
        assert executor is None
        
    def test_create_executor_disabled_workflow(self):
        """Test executor creation for disabled workflow."""
        self.registry.register(self.disabled_workflow)
        
        executor = self.registry.create_executor("disabled_workflow")
        assert executor is None
        
    @patch('nifi_mcp_server.workflows.registry.GuidedWorkflowExecutor')
    def test_create_executor_factory_error(self, mock_executor_class):
        """Test executor creation when factory fails."""
        def failing_factory():
            raise ValueError("Factory failed")
            
        failing_def = WorkflowDefinition(
            name="failing_workflow",
            display_name="Failing Workflow",
            description="A workflow that fails",
            category="Testing",
            phases=["Review"],
            create_workflow_func=failing_factory,
            enabled=True
        )
        
        self.registry.register(failing_def)
        
        executor = self.registry.create_executor("failing_workflow")
        assert executor is None
        
    def test_get_workflow_info(self):
        """Test getting workflow information."""
        self.registry.register(self.workflow_def_1)
        
        # Existing workflow
        info = self.registry.get_workflow_info("workflow_1")
        assert info is not None
        assert info["name"] == "workflow_1"
        assert info["display_name"] == "Workflow One"
        
        # Non-existing workflow
        info = self.registry.get_workflow_info("nonexistent")
        assert info is None


class TestWorkflowRegistryGlobalFunctions:
    """Test cases for global registry functions."""
    
    @patch('nifi_mcp_server.workflows.registry._workflow_registry')
    def test_register_workflow_function(self, mock_global_registry):
        """Test global register_workflow function."""
        workflow_def = WorkflowDefinition(
            name="test",
            display_name="Test",
            description="Test",
            category="Test",
            phases=["Review"],
            create_workflow_func=mock_workflow_factory
        )
        
        register_workflow(workflow_def)
        
        mock_global_registry.register.assert_called_once_with(workflow_def)
        
    def test_get_workflow_registry_function(self):
        """Test global get_workflow_registry function."""
        result = get_workflow_registry()
        
        # Should return the global registry instance
        assert result is not None
        assert isinstance(result, WorkflowRegistry)


class TestWorkflowRegistryContextIntegration:
    """Test cases for workflow registry integration with logging context."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.registry = WorkflowRegistry()
        
    @patch('nifi_mcp_server.workflows.registry.request_context')
    def test_bound_logger_context(self, mock_request_context):
        """Test bound logger uses correct context."""
        mock_request_context.get.return_value = {
            "user_request_id": "req-123",
            "action_id": "act-456"
        }
        
        # Access bound logger
        logger = self.registry.bound_logger
        
        # Verify context was accessed
        mock_request_context.get.assert_called()


if __name__ == "__main__":
    pytest.main([__file__]) 