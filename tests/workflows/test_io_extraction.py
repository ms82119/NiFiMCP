"""Unit tests for IO endpoint extraction."""

import pytest
from unittest.mock import AsyncMock
from nifi_mcp_server.workflows.nodes.doc_nodes import HierarchicalAnalysisNode


class TestIOExtraction:
    """Test IO endpoint extraction logic."""
    
    @pytest.fixture
    def analysis_node(self, mock_call_nifi_tool):
        node = HierarchicalAnalysisNode()
        node.call_nifi_tool = mock_call_nifi_tool
        return node
    
    @pytest.mark.asyncio
    async def test_extract_io_endpoints_file_based(self, analysis_node):
        """Test extraction of file-based IO endpoints."""
        processors = [
            {
                "id": "proc1",
                "component": {
                    "name": "GetFile",
                    "type": "org.apache.nifi.processors.standard.GetFile"
                }
            }
        ]
        
        # Mock get_nifi_object_details to return file path
        async def mock_tool(tool_name, arguments, prep_res):
            if tool_name == "get_nifi_object_details":
                return {
                    "component": {
                        "config": {
                            "properties": {
                                "Input Directory": "/data/incoming"
                            }
                        }
                    }
                }
            return {}
        
        analysis_node.call_nifi_tool = mock_tool
        
        prep_res = {
            "flow_graph": {"processors": {}},
            "nifi_server_id": "test",
            "workflow_id": "test-wf",
            "step_id": "test",
            "user_request_id": "test-123"
        }
        
        endpoints = await analysis_node._extract_io_endpoints_detailed(processors, prep_res)
        
        assert len(endpoints) > 0
        assert any("GetFile" in str(e) for e in endpoints)
    
    @pytest.mark.asyncio
    async def test_extract_io_endpoints_kafka(self, analysis_node):
        """Test extraction of Kafka IO endpoints."""
        processors = [
            {
                "id": "proc1",
                "component": {
                    "name": "ConsumeKafka",
                    "type": "org.apache.nifi.processors.kafka.pubsub.ConsumeKafka_2_0"
                }
            }
        ]
        
        async def mock_tool(tool_name, arguments, prep_res):
            if tool_name == "get_nifi_object_details":
                return {
                    "component": {
                        "config": {
                            "properties": {
                                "Topic Name": "input-topic",
                                "Kafka Brokers": "localhost:9092"
                            }
                        }
                    }
                }
            return {}
        
        analysis_node.call_nifi_tool = mock_tool
        
        prep_res = {
            "flow_graph": {"processors": {}},
            "nifi_server_id": "test",
            "workflow_id": "test-wf",
            "step_id": "test",
            "user_request_id": "test-123"
        }
        
        endpoints = await analysis_node._extract_io_endpoints_detailed(processors, prep_res)
        
        # Should identify Kafka processors
        assert len(endpoints) > 0
    
    def test_categorize_io_processors(self, analysis_node):
        """Test that IO processors are correctly categorized."""
        # Initialize categorizer if needed
        from nifi_mcp_server.processor_categories import get_categorizer
        if analysis_node.categorizer is None:
            analysis_node.categorizer = get_categorizer()
        
        processors = [
            {
                "id": "1",
                "component": {
                    "name": "GetFile",
                    "type": "org.apache.nifi.processors.standard.GetFile"
                }
            },
            {
                "id": "2",
                "component": {
                    "name": "PutFile",
                    "type": "org.apache.nifi.processors.standard.PutFile"
                }
            },
            {
                "id": "3",
                "component": {
                    "name": "RouteOnAttribute",
                    "type": "org.apache.nifi.processors.standard.RouteOnAttribute"
                }
            }
        ]
        
        categorized = analysis_node._categorize_processors(processors)
        
        # Should have IO_READ and IO_WRITE categories
        assert "IO_READ" in categorized
        assert "IO_WRITE" in categorized
        assert "LOGIC" in categorized
        
        # _categorize_processors returns category -> list of processor names (strings)
        # GetFile should be in IO_READ
        assert "GetFile" in categorized["IO_READ"]
        
        # PutFile should be in IO_WRITE
        assert "PutFile" in categorized["IO_WRITE"]
        
        # RouteOnAttribute should be in LOGIC
        assert "RouteOnAttribute" in categorized["LOGIC"]

