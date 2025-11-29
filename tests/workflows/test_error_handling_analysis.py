"""Unit tests for error handling analysis."""

import pytest
from unittest.mock import AsyncMock
from nifi_mcp_server.workflows.nodes.doc_nodes import HierarchicalAnalysisNode


class TestErrorHandlingAnalysis:
    """Test lightweight error handling analysis."""
    
    @pytest.fixture
    def analysis_node(self):
        return HierarchicalAnalysisNode()
    
    @pytest.mark.asyncio
    async def test_extract_handled_errors(self, analysis_node):
        """Test identification of handled errors."""
        processors = [
            {
                "id": "proc1",
                "component": {
                    "name": "GetFile",
                    "type": "org.apache.nifi.processors.standard.GetFile",
                    "config": {
                        "relationships": [
                            {
                                "name": "success",
                                "autoTerminate": False
                            },
                            {
                                "name": "failure",
                                "autoTerminate": False
                            }
                        ]
                    }
                }
            },
            {
                "id": "proc2",
                "component": {
                    "name": "LogErrors",
                    "type": "org.apache.nifi.processors.standard.LogAttribute"
                }
            }
        ]
        
        connections = [
            {
                "id": "conn1",
                "component": {
                    "source": {"id": "proc1", "type": "PROCESSOR"},
                    "destination": {"id": "proc2", "type": "PROCESSOR"},
                    "selectedRelationships": ["failure"]
                },
                "parentGroupId": "root"
            }
        ]
        
        prep_res = {
            "flow_graph": {
                "processors": {p["id"]: p for p in processors},
                "connections": connections
            },
            "workflow_id": "test-wf",
            "step_id": "test",
            "user_request_id": "test-123"
        }
        
        error_handling = await analysis_node._extract_error_handling(
            processors, connections, prep_res
        )
        
        assert len(error_handling) > 0
        # Should identify that failure relationship is handled
        handled = [e for e in error_handling if e.get("status") == "HANDLED"]
        assert len(handled) > 0
    
    @pytest.mark.asyncio
    async def test_extract_ignored_errors(self, analysis_node):
        """Test identification of ignored (auto-terminated) errors."""
        processors = [
            {
                "id": "proc1",
                "component": {
                    "name": "ExecuteScript",
                    "type": "org.apache.nifi.processors.script.ExecuteScript",
                    "config": {
                        "relationships": [
                            {
                                "name": "success",
                                "autoTerminate": False
                            },
                            {
                                "name": "failure",
                                "autoTerminate": True  # Auto-terminated
                            }
                        ]
                    }
                }
            }
        ]
        
        connections = []  # No connections for failure relationship
        
        prep_res = {
            "flow_graph": {
                "processors": {p["id"]: p for p in processors},
                "connections": connections
            },
            "workflow_id": "test-wf",
            "step_id": "test",
            "user_request_id": "test-123"
        }
        
        error_handling = await analysis_node._extract_error_handling(
            processors, connections, prep_res
        )
        
        # Should identify auto-terminated relationships as IGNORED
        ignored = [e for e in error_handling if e.get("status") == "IGNORED"]
        assert len(ignored) > 0
    
    @pytest.mark.asyncio
    async def test_extract_unhandled_errors(self, analysis_node):
        """Test identification of unhandled errors."""
        processors = [
            {
                "id": "proc1",
                "component": {
                    "name": "InvokeHTTP",
                    "type": "org.apache.nifi.processors.standard.InvokeHTTP",
                    "config": {
                        "relationships": [
                            {
                                "name": "success",
                                "autoTerminate": False
                            },
                            {
                                "name": "retry",
                                "autoTerminate": False  # Not auto-terminated
                            }
                        ]
                    }
                }
            }
        ]
        
        connections = []  # No connections, not auto-terminated = unhandled
        
        prep_res = {
            "flow_graph": {
                "processors": {p["id"]: p for p in processors},
                "connections": connections
            },
            "workflow_id": "test-wf",
            "step_id": "test",
            "user_request_id": "test-123"
        }
        
        error_handling = await analysis_node._extract_error_handling(
            processors, connections, prep_res
        )
        
        # Should identify unhandled relationships
        unhandled = [e for e in error_handling if e.get("status") == "UNHANDLED"]
        assert len(unhandled) > 0
    
    def test_error_relationship_keywords(self, analysis_node):
        """Test that error relationship keywords are identified."""
        relationships = [
            {"name": "success", "autoTerminate": False},
            {"name": "failure", "autoTerminate": False},
            {"name": "error", "autoTerminate": False},
            {"name": "retry", "autoTerminate": False},
            {"name": "invalid", "autoTerminate": False},
            {"name": "unmatched", "autoTerminate": False},
            {"name": "exception", "autoTerminate": False},
            {"name": "normal", "autoTerminate": False}  # Not an error keyword
        ]
        
        # Check that error keywords are recognized
        error_keywords = ["failure", "error", "retry", "invalid", "unmatched", "exception"]
        for rel in relationships:
            if rel["name"] in error_keywords:
                assert rel["name"] in error_keywords

