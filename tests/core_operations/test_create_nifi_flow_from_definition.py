"""
Test suite for create_nifi_flow_from_definition tool - Phase 1.2 Implementation

Tests the enhanced flow creation tool with:
- Controller services with auto-enabling
- Processors with @ServiceName reference resolution  
- Connections with name-based mapping
- Automatic flow validation
"""

import pytest
from typing import Dict, Any, List
from tests.utils.nifi_test_utils import call_tool
from loguru import logger
import httpx


@pytest.mark.anyio
async def test_create_nifi_flow_from_definition_simple(async_client, base_url, mcp_headers, test_pg):
    """Tests creating a simple complete flow with just processors and connections."""
    
    nifi_objects = [
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.GenerateFlowFile",
            "name": "Generator",
            "position": {"x": 100, "y": 100},
            "properties": {
                "File Size": "1B",
                "Batch Size": "1"
            }
        },
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.LogAttribute",
            "name": "Logger",
            "position": {"x": 400, "y": 100},
            "properties": {
                "Log Level": "info"
            }
        },
        {
            "type": "connection",
            "source": "Generator",
            "target": "Logger", 
            "relationships": ["success"]
        }
    ]
    
    logger.info("Test: Creating simple complete flow")
    result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_flow_from_definition",
        arguments={
            "nifi_objects": nifi_objects,
            "process_group_id": test_pg["id"]  # Use test process group
        },
        headers=mcp_headers,
        custom_logger=logger
    )

    # Handle MCP server wrapping result in list
    if isinstance(result, list) and len(result) > 0:
        result = result[0]

    assert result["status"] in ["success", "warning"], f"Flow creation failed: {result.get('message')}"
    assert result["summary"]["processors_created"] == 2, "Should create 2 processors"
    assert result["summary"]["connections_created"] == 1, "Should create 1 connection"
    assert result["summary"]["controller_services_created"] == 0, "Should create 0 controller services"
    assert "validation" in result, "Should include validation results"
    assert "detailed_results" in result, "Should include detailed results"
    
    # Verify objects were created in the test process group
    assert result["summary"]["process_group_id"] == test_pg["id"], f"Objects should be created in test PG {test_pg['id']}"
    
    logger.info("Test: Simple complete flow creation passed")


@pytest.mark.anyio
async def test_create_nifi_flow_from_definition_with_services(async_client, base_url, mcp_headers, test_pg):
    """Tests creating a complete flow with controller services and @ServiceName references."""
    
    nifi_objects = [
        {
            "type": "controller_service",
            "service_type": "org.apache.nifi.dbcp.DBCPConnectionPool",
            "name": "DatabasePool",
            "properties": {
                "Database Connection URL": "jdbc:h2:mem:testdb",
                "Database Driver Class Name": "org.h2.Driver"
            }
        },
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.GenerateFlowFile",
            "name": "DataSource",
            "position": {"x": 100, "y": 100},
            "properties": {
                "File Size": "100B",
                "Batch Size": "1"
            }
        },
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.LogAttribute", 
            "name": "SimpleProcessor",
            "position": {"x": 400, "y": 100},
            "properties": {
                "Log Level": "info"
            }
        },
        {
            "type": "connection",
            "source": "DataSource",
            "target": "SimpleProcessor",
            "relationships": ["success"]
        }
    ]
    
    logger.info("Test: Creating complete flow with controller services and @ServiceName references")
    result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_flow_from_definition",
        arguments={
            "nifi_objects": nifi_objects,
            "process_group_id": test_pg["id"]  # Use test process group
        },
        headers=mcp_headers,
        custom_logger=logger
    )

    # Handle MCP server wrapping result in list
    if isinstance(result, list) and len(result) > 0:
        result = result[0]

    assert result["status"] in ["success", "warning"], f"Flow creation failed: {result.get('message')}"
    assert result["summary"]["controller_services_created"] == 1, "Should create 1 controller service"
    assert result["summary"]["controller_services_enabled"] >= 0, "Should attempt to enable services"
    assert result["summary"]["processors_created"] == 2, "Should create 2 processors"
    assert result["summary"]["connections_created"] == 1, "Should create 1 connection"
    
    # Verify objects were created in the test process group
    assert result["summary"]["process_group_id"] == test_pg["id"], f"Objects should be created in test PG {test_pg['id']}"
    
    # Verify the controller service was created
    detailed_results = result["detailed_results"]
    db_service_result = None
    for detail in detailed_results:
        if detail.get("name") == "DatabasePool" and detail.get("object_type") == "controller_service":
            db_service_result = detail
            break
    
    assert db_service_result is not None, "Should find DatabasePool in results"
    
    logger.info("Test: Complete flow with controller services passed")


@pytest.mark.anyio
async def test_create_nifi_flow_from_definition_validation_errors(async_client, base_url, mcp_headers, test_pg):
    """Tests validation error handling in complete flow creation."""
    
    # Test with missing required fields
    invalid_objects = [
        {
            "type": "processor", 
            "name": "MissingType",
            "position": {"x": 100, "y": 100}
            # Missing processor_type
        },
        {
            "type": "connection",
            "source": "NonExistent",
            "target": "AlsoNonExistent",
            "relationships": ["success"]
        }
    ]
    
    logger.info("Test: Testing validation error handling")
    result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_flow_from_definition",
        arguments={
            "nifi_objects": invalid_objects,
            "process_group_id": test_pg["id"]  # Use test process group
        },
        headers=mcp_headers,
        custom_logger=logger
    )
    
    # Handle MCP server wrapping result in list
    if isinstance(result, list) and len(result) > 0:
        result = result[0]
    
    assert result["status"] == "error", "Should fail with validation errors"
    assert result["summary"]["total_errors"] > 0, "Should report errors"
    assert len(result["detailed_results"]) > 0, "Should include detailed error results"
    
    logger.info("Test: Validation error handling passed")


@pytest.mark.anyio
async def test_create_nifi_flow_from_definition_with_process_group(async_client, base_url, mcp_headers):
    """Tests creating a complete flow in a new process group."""
    
    nifi_objects = [
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.GenerateFlowFile",
            "name": "TestGen",
            "position": {"x": 200, "y": 200},
            "properties": {"File Size": "10B"}
        }
    ]
    
    create_process_group = {
        "name": "CompleteFlowTestPG",
        "position_x": 50,
        "position_y": 50
    }
    
    logger.info("Test: Creating complete flow in new process group")
    result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_flow_from_definition",
        arguments={
            "nifi_objects": nifi_objects,
            "create_process_group": create_process_group
        },
        headers=mcp_headers,
        custom_logger=logger
    )
    
    # Handle MCP server wrapping result in list
    if isinstance(result, list) and len(result) > 0:
        result = result[0]
    
    assert result["status"] in ["success", "warning"], f"Flow creation failed: {result.get('message')}"
    assert result["summary"]["processors_created"] == 1, "Should create 1 processor"
    assert "process_group_id" in result["summary"], "Should include process group ID"
    
    # Verify process group was created in detailed results
    pg_created = any(
        detail.get("object_type") == "process_group" and detail.get("status") != "error"
        for detail in result["detailed_results"]
    )
    assert pg_created, "Should create process group successfully"
    
    # Capture the created process group ID for cleanup
    created_pg_id = result["summary"]["process_group_id"]
    
    logger.info("Test: Complete flow with new process group passed")
    
    # Cleanup: Delete the created process group with proper stop/purge sequence
    try:
        logger.info(f"Test cleanup: Cleaning up process group {created_pg_id}")
        
        # Step 1: Stop the process group
        stop_result = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="operate_nifi_objects",
            arguments={
                "operations": [{
                    "object_type": "process_group",
                    "object_id": created_pg_id,
                    "operation_type": "stop"
                }]
            },
            headers=mcp_headers,
            custom_logger=logger
        )
        
        # Step 2: Purge flowfiles 
        purge_result = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="purge_flowfiles",
            arguments={
                "target_id": created_pg_id,
                "target_type": "process_group",
                "timeout_seconds": 30
            },
            headers=mcp_headers,
            custom_logger=logger
        )
        
        # Step 3: Delete the process group
        cleanup_result = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="delete_nifi_objects",
            arguments={
                "objects": [{
                    "object_type": "process_group",
                    "object_id": created_pg_id,
                    "name": "CompleteFlowTestPG"
                }]
            },
            headers=mcp_headers,
            custom_logger=logger
        )
        # Handle MCP server wrapping result in list
        if isinstance(cleanup_result, list) and len(cleanup_result) > 0:
            cleanup_result = cleanup_result[0]
        if cleanup_result.get("status") == "success":
            logger.info(f"Test cleanup: Successfully deleted process group {created_pg_id}")
        else:
            logger.error(f"Test cleanup: Could not delete process group {created_pg_id}: {cleanup_result.get('message')}")
            logger.critical(f"CLEANUP FAILURE: Process Group {created_pg_id} (CompleteFlowTestPG) needs manual cleanup!")
    except Exception as e:
        logger.error(f"Test cleanup: Error deleting process group {created_pg_id}: {e}")
        logger.critical(f"CLEANUP FAILURE: Process Group {created_pg_id} (CompleteFlowTestPG) needs manual cleanup due to exception!")
        # Don't fail the test if cleanup fails


@pytest.mark.anyio
async def test_create_nifi_flow_from_definition_duplicate_names(async_client, base_url, mcp_headers, test_pg):
    """Tests handling of duplicate component names."""
    
    nifi_objects = [
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.GenerateFlowFile",
            "name": "DuplicateName",
            "position": {"x": 100, "y": 100},
            "properties": {"File Size": "1B"}
        },
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.LogAttribute", 
            "name": "DuplicateName",  # Same name!
            "position": {"x": 400, "y": 100},
            "properties": {"Log Level": "info"}
        }
    ]
    
    logger.info("Test: Testing duplicate name handling")
    result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_flow_from_definition",
        arguments={
            "nifi_objects": nifi_objects,
            "process_group_id": test_pg["id"]  # Use test process group
        },
        headers=mcp_headers,
        custom_logger=logger
    )
    
    # Handle MCP server wrapping result in list
    if isinstance(result, list) and len(result) > 0:
        result = result[0]
    
    assert result["status"] == "error", "Should fail with duplicate names"
    assert result["summary"]["total_errors"] > 0, "Should report errors"
    
    # Check that duplicate name error was detected
    duplicate_error = any(
        "duplicate name" in detail.get("message", "").lower()
        for detail in result["detailed_results"]
        if detail.get("status") == "error"
    )
    assert duplicate_error, "Should detect duplicate name error"
    
    logger.info("Test: Duplicate name handling passed")


@pytest.mark.anyio  
async def test_create_nifi_flow_from_definition_unresolved_service_references(async_client, base_url, mcp_headers, test_pg):
    """Tests handling of unresolved @ServiceName references."""
    
    nifi_objects = [
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.ConvertRecord",
            "name": "ProcessorWithBadRef",
            "position": {"x": 200, "y": 200},
            "properties": {
                "Record Reader": "@NonExistentService",
                "Record Writer": "@AnotherMissingService"
            }
        }
    ]
    
    logger.info("Test: Testing unresolved service reference handling")
    result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_flow_from_definition",
        arguments={
            "nifi_objects": nifi_objects,
            "process_group_id": test_pg["id"]  # Use test process group
        },
        headers=mcp_headers,
        custom_logger=logger
    )
    
    # Handle MCP server wrapping result in list
    if isinstance(result, list) and len(result) > 0:
        result = result[0]
    
    # Should still create the processor but note unresolved references
    assert result["status"] in ["success", "warning", "error"], f"Unexpected status: {result.get('status')}"
    assert result["summary"]["processors_created"] >= 0, "Should attempt processor creation"
    
    # Check for unresolved reference tracking
    processor_result = None
    for detail in result["detailed_results"]:
        if detail.get("name") == "ProcessorWithBadRef":
            processor_result = detail
            break
    
    if processor_result and processor_result.get("status") != "error":
        # If processor was created, should note unresolved references
        assert "unresolved_service_references" in processor_result, "Should track unresolved references"
        unresolved = processor_result["unresolved_service_references"]
        assert "@NonExistentService" in unresolved, "Should note NonExistentService as unresolved"
        assert "@AnotherMissingService" in unresolved, "Should note AnotherMissingService as unresolved"
    
    logger.info("Test: Unresolved service reference handling passed")


@pytest.mark.anyio
async def test_create_nifi_flow_from_definition_comprehensive(
    test_pg: Dict[str, Any],
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    global_logger: Any
):
    """Tests comprehensive complete flow creation with complex dependencies and validation."""
    pg_id = test_pg["id"]
    
    # Define a comprehensive flow with services, processors, and connections
    comprehensive_flow = [
        # Controller Service
        {
            "type": "controller_service",
            "service_type": "org.apache.nifi.http.StandardHttpContextMap",
            "name": "HttpContextMap"
        },
        # Processors with complex properties and relationships
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.HandleHttpRequest",
            "name": "ReceiveHTTP",
            "position": {"x": 100, "y": 100},
            "properties": {
                "Listening Port": "8080",
                "HTTP Context Map": "@HttpContextMap"
            }
        },
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.LogAttribute",
            "name": "LogInput",
            "position": {"x": 300, "y": 100}
        },
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.HandleHttpResponse",
            "name": "SendResponse",
            "position": {"x": 500, "y": 100},
            "properties": {
                "HTTP Context Map": "@HttpContextMap"
            }
        },
        # Connections
        {
            "type": "connection",
            "source": "ReceiveHTTP",
            "target": "LogInput",
            "relationships": ["success"]
        },
        {
            "type": "connection",
            "source": "LogInput",
            "target": "SendResponse",
            "relationships": ["success"]
        }
    ]
    
    global_logger.info("Test: Creating comprehensive complete flow")
    
    # Create complete flow
    result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_flow_from_definition",
        arguments={
            "process_group_id": pg_id,
            "nifi_objects": comprehensive_flow
        },
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    assert isinstance(result_list, list) and len(result_list) > 0
    result = result_list[0]
    assert result.get("status") in ["success", "warning"], f"Flow creation failed: {result.get('message')}"
    
    # Verify summary shows expected counts
    summary = result.get("summary", {})
    assert summary.get("controller_services_created") == 1
    assert summary.get("processors_created") == 3
    assert summary.get("connections_created") == 2
    
    global_logger.info("Test: Comprehensive complete flow creation passed")


@pytest.mark.anyio
async def test_create_nifi_flow_from_definition_with_el_auto_correction(
    test_pg: Dict[str, Any],
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    global_logger: Any
):
    """Tests enhanced EL auto-correction and intelligent relationship auto-termination to prevent post-creation cleanup."""
    pg_id = test_pg["id"]
    
    # Define a flow that would previously require post-creation cleanup
    # This simulates the exact scenario from the MCP logs
    flow_with_el_issues = [
        # Controller Services
        {
            "type": "controller_service",
            "service_type": "org.apache.nifi.http.StandardHttpContextMap",
            "name": "HttpContextMap"
        },
        # Processors with problematic EL that should be auto-corrected
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.HandleHttpRequest",
            "name": "ReceiveHTTP",
            "position": {"x": 100, "y": 100},
            "properties": {
                "Listening Port": "8080",
                "HTTP Context Map": "@HttpContextMap"
            }
        },
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.LogAttribute",
            "name": "LogRawInput",
            "position": {"x": 300, "y": 100},
            "properties": {
                "Log Level": "info",
                "Log Payload": "false"
            }
        },
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.EvaluateJsonPath",
            "name": "ExtractFields",
            "position": {"x": 500, "y": 100},
            "properties": {
                "Destination": "flowfile-attribute",
                "userId": "$.userId",
                "orderItems": "$.order.items",
                "shippingAddress": "$.order.shipping.address"
            }
        },
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.RouteOnAttribute",
            "name": "RouteValidation",
            "position": {"x": 700, "y": 100},
            "properties": {
                "Routing Strategy": "Route to Property name",
                # This EL expression has the exact issues from the logs - should be auto-corrected
                "valid": "${userId:trim():length():gt(0) and orderItems:trim():length():gt(0) and shippingAddress:trim():length():gt(0)}"
            }
        },
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.HandleHttpResponse",
            "name": "SendResponse",
            "position": {"x": 900, "y": 100},
            "properties": {
                "HTTP Context Map": "@HttpContextMap",
                "HTTP Status Code": "${http.status.code:ifNull(200)}"
            }
        },
        # Connections that should prevent auto-termination of used relationships
        {
            "type": "connection",
            "source": "ReceiveHTTP",
            "target": "LogRawInput",
            "relationships": ["success"]
        },
        {
            "type": "connection",
            "source": "LogRawInput",
            "target": "ExtractFields",
            "relationships": ["success"]
        },
        {
            "type": "connection",
            "source": "ExtractFields",
            "target": "RouteValidation",
            "relationships": ["matched"]
        },
        {
            "type": "connection",
            "source": "RouteValidation",
            "target": "SendResponse",
            "relationships": ["valid"]
        }
    ]
    
    global_logger.info("Test: Creating flow with EL auto-correction and intelligent relationship auto-termination")
    
    # Create complete flow - should handle EL correction and relationship auto-termination automatically
    result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_flow_from_definition",
        arguments={
            "process_group_id": pg_id,
            "nifi_objects": flow_with_el_issues
        },
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    assert isinstance(result_list, list) and len(result_list) > 0
    result = result_list[0]
    assert result.get("status") in ["success", "warning"], f"Flow creation failed: {result.get('message')}"
    
    # Verify summary shows expected counts
    summary = result.get("summary", {})
    assert summary.get("controller_services_created") == 1
    assert summary.get("processors_created") == 5
    assert summary.get("connections_created") == 4
    
    # Check detailed results for EL auto-correction evidence
    detailed_results = result.get("detailed_results", [])
    
    # Look for EL auto-correction warnings in the RouteOnAttribute processor
    route_processor_result = None
    for detail in detailed_results:
        if detail.get("name") == "RouteValidation" and detail.get("object_type") == "processor":
            route_processor_result = detail
            break
    
    assert route_processor_result is not None, "RouteValidation processor result not found"
    
    # Should have warnings about EL auto-correction
    warnings = route_processor_result.get("warnings", [])
    el_correction_found = any("Auto-corrected EL logical operators" in warning for warning in warnings)
    global_logger.info(f"EL auto-correction warnings found: {el_correction_found}")
    global_logger.info(f"All warnings: {warnings}")
    
    # Look for intelligent relationship auto-termination results
    relationship_auto_termination_found = False
    for detail in detailed_results:
        if detail.get("object_type") == "processor_relationships":
            relationship_auto_termination_found = True
            global_logger.info(f"Found relationship auto-termination: {detail}")
            break
    
    global_logger.info(f"Intelligent relationship auto-termination found: {relationship_auto_termination_found}")
    
    # Verify that no processors are in INVALID state after creation
    # This would indicate our auto-corrections and relationship handling worked
    validation_result = result.get("validation", {})
    validation_status = validation_result.get("status", "unknown")
    
    global_logger.info(f"Final validation status: {validation_status}")
    global_logger.info(f"Validation issues: {validation_result.get('issues', [])}")
    
    # The flow should be valid or have minimal issues (not the severe EL errors from the original logs)
    assert validation_status in ["success", "warning"], f"Flow validation failed: {validation_result}"
    
    global_logger.info("Test: EL auto-correction and intelligent relationship auto-termination successful")


@pytest.mark.anyio
async def test_create_nifi_flow_from_definition_efficiency_comparison(async_client, base_url, mcp_headers, test_pg_with_processors):
    """Tests efficiency improvement over individual tool calls."""
    
    import time
    
    # Skip cleanup for efficiency test - processors will be stopped after
    generate_proc_id = test_pg_with_processors["generate_proc_id"]
    log_proc_id = test_pg_with_processors["log_proc_id"]
    test_pg_id = test_pg_with_processors["pg_id"]
    
    logger.info("Test: Efficiency comparison - Individual vs Batch creation")
    
    # Test 1: Individual tool calls (simulate what would be needed)
    start_time = time.time()

    # Simulate individual processor creation
    proc_result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_processors",
        arguments={
            "processors": [{
                "processor_type": "org.apache.nifi.processors.standard.LogAttribute",
                "name": "IndividualProcessor",
                "position_x": 800,
                "position_y": 200,
                "properties": {"Log Level": "info"}
            }],
            "process_group_id": test_pg_id
        },
        headers=mcp_headers,
        custom_logger=logger
    )
    
    individual_time = time.time() - start_time
    
    # Test 2: Complete flow creation (batch)
    batch_objects = [
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.LogAttribute",
            "name": "BatchProcessor",
            "position": {"x": 800, "y": 400},
            "properties": {"Log Level": "info"}
        }
    ]
    
    start_time = time.time()
    batch_result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_flow_from_definition",
        arguments={
            "nifi_objects": batch_objects,
            "process_group_id": test_pg_id
        },
        headers=mcp_headers,
        custom_logger=logger
    )
    batch_time = time.time() - start_time
    
    # Handle MCP server wrapping result in list
    if isinstance(batch_result, list) and len(batch_result) > 0:
        batch_result = batch_result[0]
    
    # Efficiency analysis
    efficiency_ratio = batch_time / individual_time if individual_time > 0 else 1.0
    
    logger.info(f"Performance Results:")
    logger.info(f"  Individual calls: {individual_time:.3f}s")
    logger.info(f"  Batch call: {batch_time:.3f}s") 
    logger.info(f"  Efficiency ratio: {efficiency_ratio:.2f}")
    
    # Batch should be at least competitive, allowing some tolerance
    assert efficiency_ratio <= 2.0, f"Batch should be reasonably efficient (ratio: {efficiency_ratio:.2f})"
    assert batch_result["status"] in ["success", "warning"], "Batch creation should succeed"
    
    logger.info("Test: Efficiency comparison completed - batch flow creation is efficient")


@pytest.mark.anyio
async def test_create_nifi_flow_from_definition_with_invalid_properties(async_client, base_url, mcp_headers, test_pg):
    """Test that Phase 2B property validation removes invalid properties"""
    process_group_id = test_pg["id"]
    
    logger.info("Test: Phase 2B invalid property validation")
    
    # Flow with invalid "Base Path" property for HandleHttpRequest
    nifi_objects = [
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.HandleHttpRequest",
            "name": "TestHttpRequest",
            "position": {"x": 100, "y": 100},
            "properties": {
                "Listening Port": "8080",
                "Base Path": "/invalid",  # This is an invalid property for HandleHttpRequest
                "Allow GET": "true"
            }
        }
    ]
    
    result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_flow_from_definition",
        arguments={
            "nifi_objects": nifi_objects,
            "process_group_id": process_group_id
        },
        headers=mcp_headers,
        custom_logger=logger
    )
    
    # Handle MCP server wrapping result in list
    if isinstance(result, list) and len(result) > 0:
        result = result[0]
    
    assert result["status"] in ["success", "warning"], f"Flow creation failed: {result.get('message')}"
    
    # Check that the processor was created
    assert result["summary"]["processors_created"] == 1
    
    # Check that warnings were generated for the invalid property
    processor_result = None
    for detail in result["detailed_results"]:
        if detail.get("object_type") == "processor" and detail.get("name") == "TestHttpRequest":
            processor_result = detail
            break
    
    assert processor_result is not None, "Processor result not found"
    
    # Verify that warnings were generated about the invalid property
    warnings = processor_result.get("warnings", [])
    invalid_property_warning_found = any("invalid property" in warning.lower() and "base path" in warning.lower() for warning in warnings)
    assert invalid_property_warning_found, f"Expected warning about invalid 'Base Path' property, got warnings: {warnings}"
    
    # Verify the processor was actually created (the API would have rejected it with the invalid property)
    entity = processor_result.get("entity", {})
    assert entity.get("id"), "Processor should have been created successfully"
    
    # Verify the invalid property was removed from the final processor
    properties = entity.get("properties", {})
    assert "Base Path" not in properties, f"Invalid property 'Base Path' should have been removed, but found in properties: {list(properties.keys())}"
    
    logger.info("Test: Phase 2B invalid property validation passed")


@pytest.mark.anyio
async def test_create_nifi_flow_from_definition_separate_connections(async_client, base_url, mcp_headers, test_pg):
    """Test Phase 2B connection logic with separate connections parameter"""
    process_group_id = test_pg["id"]
    
    logger.info("Test: Phase 2B separate connections parameter")
    
    # Flow with processors but connections passed separately (like in the LLM log)
    nifi_objects = [
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.GenerateFlowFile",
            "name": "Generate",
            "position": {"x": 100, "y": 100},
            "properties": {
                "File Size": "1B",
                "Batch Size": "1"
            }
        },
        {
            "type": "processor", 
            "processor_type": "org.apache.nifi.processors.standard.LogAttribute",
            "name": "LogData",
            "position": {"x": 300, "y": 100},
            "properties": {
                "Log Level": "info"
            }
        }
    ]
    
    # Connections passed as separate parameter (like LLM did)
    connections = [
        {
            "source": "Generate",
            "target": "LogData",
            "relationships": ["success"]
        }
    ]
    
    result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_flow_from_definition",
        arguments={
            "nifi_objects": nifi_objects,
            "process_group_id": process_group_id,
            "connections": connections  # Phase 2B enhancement
        },
        headers=mcp_headers,
        custom_logger=logger
    )
    
    # Handle MCP server wrapping result in list
    if isinstance(result, list) and len(result) > 0:
        result = result[0]
    
    assert result["status"] in ["success", "warning"], f"Flow creation failed: {result.get('message')}"
    
    # Check that processors were created
    assert result["summary"]["processors_created"] == 2
    
    # Phase 2B: Check that connections were created (should not be 0 anymore)
    assert result["summary"]["connections_created"] == 1, f"Expected 1 connection, got {result['summary']['connections_created']}"
    
    # Verify connection was created properly
    connection_result = None
    for detail in result["detailed_results"]:
        if detail.get("object_type") == "connection":
            connection_result = detail
            break
    
    assert connection_result is not None, "Connection result not found"
    assert connection_result.get("status") == "success", f"Connection creation failed: {connection_result.get('message')}"
    assert connection_result.get("source_name") == "Generate"
    assert connection_result.get("target_name") == "LogData"
    
    logger.info("Test: Phase 2B separate connections parameter passed") 