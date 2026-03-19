"""
Tests for the invoke_http_endpoint tool with enhanced flow status integration.
These tests validate that the new mandatory process_group_id parameter works correctly
and that flow status is always included in the response.
"""

import pytest
import httpx
from typing import Dict, Any
import json

from tests.utils.nifi_test_utils import call_tool


@pytest.mark.anyio
async def test_invoke_http_endpoint_missing_process_group_id(
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    global_logger: Any
):
    """Test that missing process_group_id parameter causes validation error."""
    global_logger.info("Test: Testing invoke_http_endpoint with missing process_group_id")
    
    # Try to call without process_group_id - this should fail validation
    with pytest.raises(Exception) as exc_info:
        await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="invoke_http_endpoint",
            arguments={
                "url": "http://httpbin.org/get",
                "method": "GET"
                # Missing process_group_id
            },
            headers=mcp_headers,
            custom_logger=global_logger
        )
    
    # Should get a validation error
    assert "400" in str(exc_info.value)
    global_logger.info("Test: Successfully validated that process_group_id is required")


@pytest.mark.anyio
async def test_invoke_http_endpoint_success_with_flow_status(
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    global_logger: Any,
    nifi_test_server_id: str
):
    """Test successful HTTP endpoint invocation includes flow status."""
    global_logger.info("Test: Testing invoke_http_endpoint with successful response and flow status")
    
    test_url = "http://httpbin.org/post"
    test_process_group_id = "root"
    test_payload = {"test_key": "test_value"}
    
    # Call the tool
    result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="invoke_http_endpoint",
        arguments={
            "url": test_url,
            "process_group_id": test_process_group_id,
            "method": "POST",
            "payload": test_payload,
            "headers": {"Content-Type": "application/json"},
            "timeout_seconds": 10
        },
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    # Handle MCP server wrapping result in list
    if isinstance(result, list) and len(result) > 0:
        result = result[0]
    
    global_logger.info(f"invoke_http_endpoint result keys: {list(result.keys())}")
    
    # Verify basic HTTP response structure
    assert "status" in result
    assert "message" in result
    assert "request_details" in result
    assert "flow_status" in result, "flow_status should always be included"
    
    # Verify HTTP response details
    request_details = result["request_details"]
    assert request_details["url"] == test_url
    assert request_details["method"] == "POST"
    assert request_details["payload_type"] == "json"
    
    # Verify flow status structure
    flow_status = result["flow_status"]
    assert "success" in flow_status
    assert "process_group_id" in flow_status
    assert flow_status["process_group_id"] == test_process_group_id
    assert "data" in flow_status or "error" in flow_status
    
    if flow_status["success"]:
        # If flow status was successful, verify structure
        flow_data = flow_status["data"]
        assert "process_group_id" in flow_data
        assert "process_group_name" in flow_data
        assert "component_summary" in flow_data
        assert "queue_summary" in flow_data
        global_logger.info("Test: Flow status successfully retrieved and properly structured")
    else:
        # If flow status failed, should have error message
        assert "error" in flow_status
        global_logger.info(f"Test: Flow status check failed (expected in test environment): {flow_status['error']}")


@pytest.mark.anyio
async def test_invoke_http_endpoint_timeout_with_flow_status(
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    global_logger: Any
):
    """Test HTTP endpoint timeout still includes flow status."""
    global_logger.info("Test: Testing invoke_http_endpoint with timeout and flow status")
    
    # Use httpbin delay endpoint to trigger timeout
    test_url = "http://httpbin.org/delay/15"  # 15 second delay
    test_process_group_id = "root"
    
    # Call the tool with short timeout
    result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="invoke_http_endpoint",
        arguments={
            "url": test_url,
            "process_group_id": test_process_group_id,
            "method": "GET",
            "timeout_seconds": 2  # Short timeout to trigger timeout
        },
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    # Handle MCP server wrapping result in list
    if isinstance(result, list) and len(result) > 0:
        result = result[0]
    
    global_logger.info(f"Timeout test result status: {result.get('status')}")
    
    # Verify timeout status
    assert result["status"] == "timeout"
    assert "timed out" in result["message"].lower()
    assert result["response_status_code"] is None
    
    # Verify flow status is still included even on timeout
    assert "flow_status" in result, "flow_status should be included even on timeout"
    flow_status = result["flow_status"]
    assert flow_status["process_group_id"] == test_process_group_id
    assert "success" in flow_status
    assert "data" in flow_status or "error" in flow_status
    
    global_logger.info("Test: Flow status correctly included after timeout")


@pytest.mark.anyio
async def test_invoke_http_endpoint_different_methods(
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    global_logger: Any
):
    """Test different HTTP methods all include flow status."""
    global_logger.info("Test: Testing invoke_http_endpoint with different HTTP methods")
    
    methods_to_test = ["GET", "POST", "PUT"]
    
    for method in methods_to_test:
        global_logger.info(f"Test: Testing {method} method")
        
        # Adjust URL and payload based on method
        if method == "GET":
            test_url = "http://httpbin.org/get"
            payload = None
        else:
            test_url = f"http://httpbin.org/{method.lower()}"
            payload = {"method": method}
        
        result = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="invoke_http_endpoint",
            arguments={
                "url": test_url,
                "process_group_id": "root",
                "method": method,
                "payload": payload,
                "timeout_seconds": 10
            },
            headers=mcp_headers,
            custom_logger=global_logger
        )
        
        # Handle MCP server wrapping result in list
        if isinstance(result, list) and len(result) > 0:
            result = result[0]
        
        # Each method should include flow status
        assert "flow_status" in result, f"{method} method should include flow_status"
        assert result["request_details"]["method"] == method
        
        global_logger.info(f"Test: {method} method test completed successfully")


@pytest.mark.anyio
async def test_invoke_http_endpoint_http_error_with_flow_status(
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    global_logger: Any
):
    """Test HTTP endpoint returning error status still includes flow status."""
    global_logger.info("Test: Testing invoke_http_endpoint with HTTP error response")
    
    # Use httpbin 404 endpoint to get HTTP error
    test_url = "http://httpbin.org/status/404"
    
    result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="invoke_http_endpoint",
        arguments={
            "url": test_url,
            "process_group_id": "root",
            "method": "GET",
            "timeout_seconds": 10
        },
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    # Handle MCP server wrapping result in list
    if isinstance(result, list) and len(result) > 0:
        result = result[0]
    
    global_logger.info(f"HTTP error test result status: {result.get('status')}")
    
    # Should get http_error status
    assert result["status"] == "http_error"
    assert result["response_status_code"] == 404
    assert "404" in result["message"]
    
    # But should still include flow status
    assert "flow_status" in result
    flow_status = result["flow_status"]
    assert flow_status["process_group_id"] == "root"
    
    global_logger.info("Test: HTTP error with flow status test completed successfully")


@pytest.mark.anyio
async def test_invoke_http_endpoint_string_payload(
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    global_logger: Any
):
    """Test HTTP endpoint with string payload and custom headers."""
    global_logger.info("Test: Testing invoke_http_endpoint with string payload")
    
    test_url = "http://httpbin.org/post"
    test_payload = "This is a string payload"
    custom_headers = {
        "Content-Type": "text/plain",
        "X-Custom-Header": "test-value"
    }
    
    result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="invoke_http_endpoint",
        arguments={
            "url": test_url,
            "process_group_id": "root",
            "method": "POST",
            "payload": test_payload,
            "headers": custom_headers,
            "timeout_seconds": 10
        },
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    # Handle MCP server wrapping result in list
    if isinstance(result, list) and len(result) > 0:
        result = result[0]
    
    # Verify string payload handling
    assert result["request_details"]["payload_type"] == "string"
    # Check that custom headers are included
    sent_headers = result["request_details"]["headers"]
    assert sent_headers["Content-Type"] == "text/plain"
    assert sent_headers["X-Custom-Header"] == "test-value"
    
    # Still should include flow status
    assert "flow_status" in result
    
    global_logger.info("Test: String payload and custom headers test completed successfully")


@pytest.mark.anyio 
async def test_flow_status_structure_validation(
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    global_logger: Any
):
    """Test that flow status has the expected structure when present."""
    global_logger.info("Test: Testing flow status structure validation")
    
    result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="invoke_http_endpoint",
        arguments={
            "url": "http://httpbin.org/get",
            "process_group_id": "root",
            "method": "GET",
            "timeout_seconds": 10
        },
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    # Handle MCP server wrapping result in list
    if isinstance(result, list) and len(result) > 0:
        result = result[0]
    
    flow_status = result["flow_status"]
    
    # Basic structure validation
    assert isinstance(flow_status, dict)
    assert "success" in flow_status
    assert "process_group_id" in flow_status
    assert flow_status["process_group_id"] == "root"
    
    if flow_status["success"]:
        # If successful, validate the flow data structure
        flow_data = flow_status["data"]
        assert isinstance(flow_data, dict)
        
        # Check for expected top-level keys from get_process_group_status
        expected_keys = [
            "process_group_id",
            "process_group_name", 
            "component_summary",
            "queue_summary"
        ]
        
        for key in expected_keys:
            assert key in flow_data, f"Expected key '{key}' missing from flow_status.data"
        
        # Validate component_summary structure
        component_summary = flow_data["component_summary"]
        assert "processors" in component_summary
        assert "input_ports" in component_summary
        assert "output_ports" in component_summary
        
        # Validate queue_summary structure
        queue_summary = flow_data["queue_summary"]
        assert "total_queued_count" in queue_summary
        assert "total_queued_size_bytes" in queue_summary
        assert "connections_with_data" in queue_summary
        
        global_logger.info("Test: Flow status structure validation passed")
    else:
        # If failed, should have error
        assert "error" in flow_status
        global_logger.info(f"Test: Flow status failed as expected in test environment: {flow_status.get('error')}") 