"""Unit tests for component reference formatting."""

import pytest
from unittest.mock import patch, MagicMock

# Mock FastMCP before any imports that might trigger core.py
with patch('mcp.server.FastMCP') as mock_fastmcp:
    mock_fastmcp.return_value = MagicMock()
    from nifi_mcp_server.workflows.nodes.component_formatter import (
        format_component_reference,
        format_processor_reference,
        format_component_for_table,
        format_destination_reference
    )


class TestComponentFormatting:
    """Test unambiguous component reference formatting."""
    
    def test_format_processor_reference(self):
        """Test processor reference formatting."""
        processor = {
            "id": "12345678-1234-1234-1234-123456789012",
            "component": {
                "name": "GetFile",
                "type": "org.apache.nifi.processors.standard.GetFile"
            }
        }
        result = format_processor_reference(processor)
        assert "GetFile" in result
        assert "GetFile)" in result  # Type
        assert "[id:12345678]" in result  # Short ID
    
    def test_format_component_reference_with_id(self):
        """Test component reference with explicit ID."""
        component = {
            "component": {
                "name": "MyProcessor",
                "type": "org.apache.nifi.processors.standard.UpdateAttribute"
            }
        }
        result = format_component_reference(component, component_id="abcd1234-5678-9012-3456-789012345678")
        assert "MyProcessor" in result
        assert "UpdateAttribute" in result
        assert "[id:abcd1234]" in result
    
    def test_format_component_reference_no_id(self):
        """Test component reference without ID."""
        component = {
            "component": {
                "name": "TestProcessor",
                "type": "org.apache.nifi.processors.standard.LogAttribute"
            }
        }
        result = format_component_reference(component)
        assert "TestProcessor" in result
        assert "LogAttribute" in result
        assert "[id:" not in result  # No ID when not provided
    
    def test_format_destination_reference(self):
        """Test destination reference formatting."""
        result = format_destination_reference(
            "abcd1234-5678-9012-3456-789012345678",
            "LogErrors",
            "org.apache.nifi.processors.standard.LogAttribute"
        )
        assert "LogErrors" in result
        assert "LogAttribute" in result
        assert "[id:abcd1234]" in result
    
    def test_format_destination_reference_minimal(self):
        """Test destination reference with minimal info."""
        result = format_destination_reference(
            "12345678-1234-1234-1234-123456789012",
            None,  # No name
            None   # No type
        )
        assert "Unknown" in result
        assert "[id:12345678]" in result
    
    def test_format_component_for_table(self):
        """Test component formatting for table display."""
        component = {
            "id": "abcdef12-3456-7890-abcd-ef1234567890",
            "component": {
                "name": "RouteData",
                "type": "org.apache.nifi.processors.standard.RouteOnAttribute"
            }
        }
        result = format_component_for_table(component)
        
        assert result["name"] == "RouteData"
        assert result["type"] == "RouteOnAttribute"
        assert result["id"] == "abcdef12"
        assert "RouteData (RouteOnAttribute) [id:abcdef12]" in result["full_reference"]
    
    def test_format_component_for_table_no_id(self):
        """Test component formatting when ID is missing."""
        component = {
            "component": {
                "name": "TestProc",
                "type": "org.apache.nifi.processors.standard.LogAttribute"
            }
        }
        result = format_component_for_table(component)
        
        assert result["name"] == "TestProc"
        assert result["type"] == "LogAttribute"
        assert result["id"] == "N/A"
        assert "TestProc (LogAttribute) [id:N/A]" in result["full_reference"]
    
    def test_format_custom_id_length(self):
        """Test component reference with custom ID length."""
        processor = {
            "id": "12345678-1234-1234-1234-123456789012",
            "component": {
                "name": "GetFile",
                "type": "org.apache.nifi.processors.standard.GetFile"
            }
        }
        result = format_processor_reference(processor, short_id_length=4)
        assert "[id:1234]" in result
    
    def test_format_direct_component_data(self):
        """Test formatting when component data is direct (not nested)."""
        component = {
            "name": "DirectProcessor",
            "type": "org.apache.nifi.processors.standard.UpdateAttribute",
            "id": "direct1234-5678-9012-3456-789012345678"
        }
        result = format_component_reference(component)
        assert "DirectProcessor" in result
        assert "UpdateAttribute" in result
        assert "[id:direct12]" in result

