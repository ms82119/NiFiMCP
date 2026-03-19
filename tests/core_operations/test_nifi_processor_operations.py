# Tests for NiFi Processor operations 

import pytest
import httpx
from loguru import logger
from typing import Dict, Any, List
import anyio
import asyncio

from nifi_mcp_server.nifi_client import NiFiClient # Corrected import
from tests.utils.nifi_test_utils import call_tool # Import call_tool

@pytest.mark.anyio
async def test_create_processors_in_pg(
    test_pg_with_processors: Dict[str, Any], # Fixture provides PG and processor IDs
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    global_logger: Any,
    nifi_test_server_id: str # Changed from target_nifi_server_id
):
    """Tests the creation of processors within a PG using the test_pg_with_processors fixture."""
    pg_id = test_pg_with_processors.get("pg_id")
    generate_proc_id = test_pg_with_processors.get("generate_proc_id")
    log_proc_id = test_pg_with_processors.get("log_proc_id")

    global_logger.info(f"Test: test_create_processors_in_pg received PG_ID: {pg_id}, Gen_Proc_ID: {generate_proc_id}, Log_Proc_ID: {log_proc_id}")

    assert pg_id and isinstance(pg_id, str) and len(pg_id) > 0, "Invalid PG ID from fixture."
    assert generate_proc_id and isinstance(generate_proc_id, str) and len(generate_proc_id) > 0, "Invalid Generate Processor ID from fixture."
    assert log_proc_id and isinstance(log_proc_id, str) and len(log_proc_id) > 0, "Invalid Log Processor ID from fixture."

    # Verify Generate Processor details
    global_logger.info(f"Test: Verifying details for Generate Processor ID: {generate_proc_id}")
    gen_details_args = {"object_type": "processor", "object_id": generate_proc_id}
    gen_details_list = await call_tool(
        client=async_client, base_url=base_url, tool_name="get_nifi_object_details",
        arguments=gen_details_args, headers=mcp_headers, custom_logger=global_logger
    )
    assert isinstance(gen_details_list, list) and gen_details_list and isinstance(gen_details_list[0], dict)
    gen_details = gen_details_list[0].get("component", gen_details_list[0])
    assert gen_details and gen_details.get("id") == generate_proc_id
    expected_gen_name = f"mcp-test-generate-{nifi_test_server_id}"
    actual_gen_name = gen_details.get("name")
    assert actual_gen_name == expected_gen_name, \
        f"Generate processor name mismatch: expected '{expected_gen_name}', got '{actual_gen_name}'"
    global_logger.info(f"Test: Successfully verified Generate Processor {generate_proc_id} with name '{actual_gen_name}'.")

    # Verify LogAttribute Processor details
    global_logger.info(f"Test: Verifying details for LogAttribute Processor ID: {log_proc_id}")
    log_details_args = {"object_type": "processor", "object_id": log_proc_id}
    log_details_list = await call_tool(
        client=async_client, base_url=base_url, tool_name="get_nifi_object_details",
        arguments=log_details_args, headers=mcp_headers, custom_logger=global_logger
    )
    assert isinstance(log_details_list, list) and log_details_list and isinstance(log_details_list[0], dict)
    log_details = log_details_list[0].get("component", log_details_list[0])
    assert log_details and log_details.get("id") == log_proc_id
    expected_log_name = f"mcp-test-log-{nifi_test_server_id}"
    actual_log_name = log_details.get("name")
    assert actual_log_name == expected_log_name, \
        f"LogAttribute processor name mismatch: expected '{expected_log_name}', got '{actual_log_name}'"
    global_logger.info(f"Test: Successfully verified LogAttribute Processor {log_proc_id} with name '{actual_log_name}'.")

    assert log_proc_id in [proc.get("id") for proc in test_pg_with_processors["processors"]], \
        f"Processor '{log_proc_id}' not found in created processors."

# TODO: Add tests for updating processor properties, relationships, etc.

@pytest.mark.anyio
async def test_update_processor_relationships(
    test_pg_with_processors: Dict[str, Any],
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    global_logger: Any,
    nifi_test_server_id: str
):
    """Tests updating a processor's auto-terminated relationships.
    Note: LogAttribute processor will show warnings without input connections,
    but the relationship update itself should still succeed.
    The LogAttribute processor only has a 'success' relationship."""
    log_proc_id = test_pg_with_processors.get("log_proc_id")
    assert log_proc_id, "Log Processor ID not found from fixture."

    # Update relationships
    global_logger.info(f"Test: Auto-terminating relationships for LogAttribute Processor ID: {log_proc_id}")
    update_rels_args = {
        "processor_id": log_proc_id,
        "auto_terminated_relationships": ["success"]  # LogAttribute only has 'success' relationship
    }
    rels_result_list = await call_tool(
        client=async_client, base_url=base_url, tool_name="update_nifi_processor_relationships",
        arguments=update_rels_args, headers=mcp_headers, custom_logger=global_logger
    )
    assert isinstance(rels_result_list, list) and rels_result_list and isinstance(rels_result_list[0], dict), \
        "Unexpected response format for update_nifi_processor_relationships"
    rels_result = rels_result_list[0]

    # Accept both success and warning as valid states
    assert rels_result.get("status") in ["success", "warning"], \
        f"Failed to update relationships: {rels_result.get('message')}"
    global_logger.info(f"Test: Successfully submitted update for LogAttribute relationships. Response status: {rels_result.get('status')}")

    # Verify the update by re-fetching processor details
    global_logger.info(f"Test: Verifying relationship update for LogAttribute Processor ID: {log_proc_id}")
    details_args = {"object_type": "processor", "object_id": log_proc_id}
    details_list = await call_tool(
        client=async_client, base_url=base_url, tool_name="get_nifi_object_details",
        arguments=details_args, headers=mcp_headers, custom_logger=global_logger
    )
    assert isinstance(details_list, list) and details_list and isinstance(details_list[0], dict)
    details = details_list[0].get("component", details_list[0])
    assert details and details.get("id") == log_proc_id

    # Check relationships array for auto-terminated settings
    relationships = details.get("relationships", [])
    global_logger.info(f"Checking relationships: {relationships}")
    
    # Find and verify the success relationship is auto-terminated
    success_rel = next((rel for rel in relationships if rel.get("name") == "success"), None)
    assert success_rel is not None, "Success relationship not found in processor relationships"
    assert success_rel.get("autoTerminate", False), "Success relationship is not auto-terminated"
    
    global_logger.info("Test: Successfully verified auto-terminated relationships in processor configuration")

@pytest.mark.anyio
async def test_update_processor_properties_and_verify(
    test_pg_with_processors: Dict[str, Any],
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    global_logger: Any,
    nifi_test_server_id: str
):
    """Tests updating a processor's properties and verifies the update."""
    log_proc_id = test_pg_with_processors.get("log_proc_id")
    generate_proc_id = test_pg_with_processors.get("generate_proc_id")
    pg_id = test_pg_with_processors.get("pg_id")
    assert log_proc_id, "Log Processor ID not found from fixture."
    assert generate_proc_id, "Generate Processor ID not found from fixture."
    assert pg_id, "Process Group ID not found from fixture."

    # First auto-terminate relationships to make the processor valid
    global_logger.info(f"Test: Auto-terminating relationships for LogAttribute Processor ID: {log_proc_id}")
    update_rels_args = {
        "processor_id": log_proc_id,
        "auto_terminated_relationships": ["success"]  # LogAttribute only has 'success' relationship
    }
    rels_result_list = await call_tool(
        client=async_client, base_url=base_url, tool_name="update_nifi_processor_relationships",
        arguments=update_rels_args, headers=mcp_headers, custom_logger=global_logger
    )
    assert isinstance(rels_result_list, list) and rels_result_list and isinstance(rels_result_list[0], dict), \
        "Unexpected response format for update_nifi_processor_relationships"
    rels_result = rels_result_list[0]
    assert rels_result.get("status") in ["success", "warning"], \
        f"Failed to update relationships: {rels_result.get('message')}"
    global_logger.info(f"Test: Successfully auto-terminated relationships. Status: {rels_result.get('status')}")

    # Create connection between GenerateFlowFile and LogAttribute
    global_logger.info(f"Test: Creating connection from GenerateFlowFile to LogAttribute")
    create_connection_args = {
        "source_id": generate_proc_id,
        "target_id": log_proc_id,  # Changed from destination_id
        "relationships": ["success"]  # Changed from relationships_to_connect
    }
    connection_result_list = await call_tool(
        client=async_client, base_url=base_url, tool_name="create_nifi_connections",
        arguments={"connections": [create_connection_args], "process_group_id": pg_id}, headers=mcp_headers, custom_logger=global_logger
    )
    assert isinstance(connection_result_list, list) and connection_result_list and isinstance(connection_result_list[0], dict), \
        "Unexpected response format for create_nifi_connection"
    connection_result = connection_result_list[0]
    assert connection_result.get("status") in ["success", "warning"], \
        f"Failed to create connection: {connection_result.get('message')}"
    global_logger.info(f"Test: Successfully created connection. Status: {connection_result.get('status')}")

    # Now update the properties
    global_logger.info(f"Test: Updating LogAttribute ({log_proc_id}) properties.")
    target_log_level = "warn"
    update_props_args = {
        "updates": [{
            "processor_id": log_proc_id,
            "properties": {
                "Log Level": target_log_level,
                "Attributes to Log": ".*",      # As per original test
                "Log prefix": "mcp-test"       # As per original test
            }
        }]
    }
    update_result_list = await call_tool(
        client=async_client, base_url=base_url, tool_name="update_nifi_processors_properties",
        arguments=update_props_args, headers=mcp_headers, custom_logger=global_logger
    )
    assert isinstance(update_result_list, list) and update_result_list and isinstance(update_result_list[0], dict), \
        "Unexpected response format for update_nifi_processors_properties"
    update_result = update_result_list[0]
    assert update_result.get("status") in ["success", "warning"], \
        f"Failed to update properties: {update_result.get('message')}"
    global_logger.info(f"Test: Successfully submitted update for LogAttribute properties. Status: {update_result.get('status')}")

    # Re-fetch details to verify the update
    global_logger.info(f"Test: Re-fetching LogAttribute ({log_proc_id}) details to verify property update.")
    verify_update_args = {"object_type": "processor", "object_id": log_proc_id}
    verify_result_list = await call_tool(
        client=async_client, base_url=base_url, tool_name="get_nifi_object_details",
        arguments=verify_update_args, headers=mcp_headers, custom_logger=global_logger
    )
    assert isinstance(verify_result_list, list) and verify_result_list and isinstance(verify_result_list[0], dict), \
        "Unexpected response format for get_nifi_object_details (verify update)"

    verify_details_response = verify_result_list[0]
    if verify_details_response.get("status") == "error":
        assert False, f"Failed to get details for processor {log_proc_id} after update: {verify_details_response.get('message')}"

    verified_component = verify_details_response.get("component", verify_details_response)
    assert verified_component is not None, "Component details not found in verification step."

    verified_config = verified_component.get("config", {})
    verified_props = verified_config.get("properties", {})
    validation_status = verified_component.get("validationStatus")

    # Verify properties were updated correctly
    actual_log_level = verified_props.get("Log Level")
    assert actual_log_level == target_log_level, \
        f"Log Level property was not updated correctly. Expected '{target_log_level}', found '{actual_log_level}'."
    global_logger.info(f"Test: Successfully verified LogAttribute properties update (Log Level: {actual_log_level}).")

    # Verify relationships are still auto-terminated
    relationships = verified_component.get("relationships", [])
    success_rel = next((rel for rel in relationships if rel.get("name") == "success"), None)
    assert success_rel is not None, "Success relationship not found in processor relationships"
    assert success_rel.get("autoTerminate", False), "Success relationship is not auto-terminated"
    global_logger.info("Test: Verified success relationship is still auto-terminated")

    # Check validation status - should now be VALID since we have auto-terminated relationships and upstream connection
    assert validation_status == "VALID", \
        f"Processor {log_proc_id} is not VALID after property update. Status: {validation_status}."
    global_logger.info(f"Test: Processor {log_proc_id} validation status confirmed: {validation_status}")

@pytest.mark.anyio
async def test_auto_stop_update_running_processor(
    test_pg_with_processors: Dict[str, Any],  # Fixture provides PG with Generate and Log processors
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any
):
    """Test that updating a running processor's properties with auto-stop enabled works correctly."""
    # Get processor IDs from the fixture
    generate_proc_id = test_pg_with_processors.get("generate_proc_id")
    assert generate_proc_id, "Generate Processor ID not found from fixture."

    # First auto-terminate relationships
    update_rels_args = {
        "processor_id": generate_proc_id,
        "auto_terminated_relationships": ["success"]
    }
    rels_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="update_nifi_processor_relationships",
        arguments=update_rels_args,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert rels_result_list[0].get("status") in ["success", "warning"], \
        "Failed to auto-terminate relationships"
    global_logger.info(f"Test: Auto-terminated relationships for processor {generate_proc_id}")

    # Then start the processor
    start_args = {
        "object_type": "processor",
        "object_id": generate_proc_id,
        "operation_type": "start"
    }
    start_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="operate_nifi_objects",
        arguments={"operations": [start_args]},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert start_result_list[0].get("status") == "success", "Failed to start processor"
    global_logger.info(f"Test: Started processor {generate_proc_id}")
    await asyncio.sleep(1)  # Brief pause after starting

    # Verify processor is running and get current properties
    verify_args = {
        "object_type": "processor",
        "object_id": generate_proc_id
    }
    verify_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="get_nifi_object_details",
        arguments=verify_args,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert verify_result_list[0].get("component", {}).get("state") == "RUNNING", \
        "Processor not running before update test"
    
    # Get current properties - use a simple property that we know exists
    current_config = verify_result_list[0].get("component", {}).get("config", {})
    current_properties = current_config.get("properties", {})
    
    # Use "File Size" property which should exist on GenerateFlowFile processor
    current_file_size = current_properties.get("File Size", "1 kB")
    target_file_size = "2 kB" if current_file_size == "1 kB" else "1 kB"
    global_logger.info(f"Test: Current File Size: {current_file_size}, target: {target_file_size}")

    # Attempt update with Auto-Stop enabled
    headers_auto_stop_true = {**mcp_headers, "X-Mcp-Auto-Stop-Enabled": "true"}
    update_args = {
        "updates": [{
            "processor_id": generate_proc_id,
            "properties": {
                "File Size": target_file_size
            }
        }]
    }
    update_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="update_nifi_processors_properties",
        arguments=update_args,
        headers=headers_auto_stop_true,
        custom_logger=global_logger
    )
    # Now that Auto-Stop is implemented, expect success
    assert isinstance(update_result_list, list) and len(update_result_list) > 0, "Expected list response from batch tool"
    update_result = update_result_list[0]
    assert update_result.get("status") == "success", \
        "Expected success with Auto-Stop enabled (feature is now implemented)"
    global_logger.info("Test: Got expected success with Auto-Stop enabled")
    
    # Verify the enhanced return structure includes restart information
    assert "property_update" in update_result, "Expected property_update in response"
    assert "restart_status" in update_result, "Expected restart_status in response"
    assert update_result["property_update"]["status"] == "success", "Property update should be successful"
    assert update_result["restart_status"]["status"] == "success", "Restart should be successful"
    assert update_result["restart_status"]["final_state"] == "RUNNING", "Final state should be RUNNING"
    global_logger.info("Test: Verified enhanced return structure with restart information")

    # Verify processor was stopped and restarted, and properties were updated
    verify_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="get_nifi_object_details",
        arguments=verify_args,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    # With the new auto-restart feature, processor should be running again since it was originally running and config is valid
    assert verify_result_list[0].get("component", {}).get("state") == "RUNNING", \
        "Processor should be auto-restarted after Auto-Stop update (new auto-restart feature)"
    
    # Verify the property was updated
    updated_properties = verify_result_list[0].get("component", {}).get("config", {}).get("properties", {})
    updated_file_size = updated_properties.get("File Size")
    assert updated_file_size == target_file_size, \
        f"File Size property should have been updated. Expected '{target_file_size}', got '{updated_file_size}'"
    global_logger.info(f"Test: Confirmed processor {generate_proc_id} was auto-restarted and File Size updated to {target_file_size}")

    # Processor is already running due to auto-restart, so we can proceed directly to the Auto-Stop disabled test
    # No need to manually restart since auto-restart feature already did it
    global_logger.info(f"Test: Processor {generate_proc_id} is already running due to auto-restart feature")

    # Attempt update with header that would have disabled Auto-Stop (Auto-Stop is now always on, so update succeeds)
    headers_auto_stop_false = {**mcp_headers, "X-Mcp-Auto-Stop-Enabled": "false"}
    disabled_test_file_size = "3 kB" if target_file_size != "3 kB" else "4 kB"
    disabled_update_args = {
        "updates": [{
            "processor_id": generate_proc_id,
            "properties": {
                "File Size": disabled_test_file_size
            }
        }]
    }
    update_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="update_nifi_processors_properties",
        arguments=disabled_update_args,
        headers=headers_auto_stop_false,
        custom_logger=global_logger
    )
    assert isinstance(update_result_list, list) and len(update_result_list) > 0, "Expected list response from batch tool"
    assert update_result_list[0].get("status") == "success", \
        "Auto-Stop is always on; update of running processor should succeed"
    global_logger.info("Test: Update succeeded with Auto-Stop always on")

    # Verify processor state and that the property was updated
    verify_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="get_nifi_object_details",
        arguments=verify_args,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert verify_result_list[0].get("component", {}).get("state") == "RUNNING", \
        "Processor should be running after update (may have been stopped and restarted)"
    
    # Verify the property was updated to the disabled-test value
    updated_properties = verify_result_list[0].get("component", {}).get("config", {}).get("properties", {})
    updated_file_size = updated_properties.get("File Size")
    assert updated_file_size == disabled_test_file_size, \
        f"File Size property should have been updated. Expected '{disabled_test_file_size}', got '{updated_file_size}'"
    global_logger.info(f"Test: Confirmed processor {generate_proc_id} is running with File Size: {disabled_test_file_size}")

@pytest.mark.anyio
async def test_create_processor_with_service_reference_resolution(
    test_pg: dict,
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    global_logger: any
):
    """Tests that processors can be created with service references that get resolved automatically."""
    pg_id = test_pg["id"]
    
    global_logger.info(f"Test: Creating HTTP Context Map service in PG {pg_id}")
    
    # Step 1: Create a controller service
    create_service_result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_controller_services",
        arguments={
            "controller_services": [
                {
                    "service_type": "org.apache.nifi.http.StandardHttpContextMap",
                    "name": "TestHttpContextMap"
                }
            ],
            "process_group_id": pg_id
        },
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    assert create_service_result[0]["status"] in ["success", "warning"], f"Failed to create controller service: {create_service_result[0].get('message')}"
    service_id = create_service_result[0]["entity"]["id"]
    global_logger.info(f"Test: Successfully created service with ID: {service_id}")
    
    # Step 2: Create processors that reference the service by name (should be auto-resolved to UUID)
    processors_data = [{
        "processor_type": "org.apache.nifi.processors.standard.HandleHttpRequest",
        "name": "TestHttpRequest",
        "position_x": 100,
        "position_y": 100,
        "process_group_id": pg_id,
        "properties": {
            "HTTP Context Map": "TestHttpContextMap",  # Reference by name, should be resolved to UUID
            "Listening Port": "8080",
            "Base Path": "/test"
        }
    }, {
        "processor_type": "org.apache.nifi.processors.standard.HandleHttpResponse",
        "name": "TestHttpResponse",
        "position_x": 300,
        "position_y": 100,
        "process_group_id": pg_id,
        "properties": {
            "HTTP Context Map": "TestHttpContextMap"  # Reference by name, should be resolved to UUID
        }
    }]
    
    global_logger.info(f"Test: Creating processors with service references in PG {pg_id}")
    create_processors_result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_processors",
        arguments={"processors": processors_data, "process_group_id": pg_id},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    # Verify both processors were created successfully
    assert len(create_processors_result) == 2, f"Expected 2 processor results, got {len(create_processors_result)}"
    
    for i, result in enumerate(create_processors_result):
        processor_name = processors_data[i]["name"]
        assert result["status"] in ["success", "warning"], f"Failed to create processor {processor_name}: {result.get('message')}"
        
        # Verify the service reference was resolved correctly
        processor_entity = result["entity"]
        properties = processor_entity.get("properties", {})
        http_context_map_value = properties.get("HTTP Context Map")
        
        # The service reference should now be the UUID, not the name
        assert http_context_map_value == service_id, f"Service reference not resolved correctly for {processor_name}. Expected {service_id}, got {http_context_map_value}"
        
        global_logger.info(f"Test: Successfully created processor {processor_name} with resolved service reference {service_id}") 

@pytest.mark.anyio
async def test_create_processor_with_invalid_base_path_property(
    test_pg: dict,
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    global_logger: any
):
    """Tests that Phase 2B property validation removes invalid 'Base Path' property from HandleHttpRequest."""
    pg_id = test_pg["id"]
    
    global_logger.info(f"Test: Creating HandleHttpRequest processor with invalid 'Base Path' property in PG {pg_id}")
    
    # Create processor with invalid "Base Path" property (should be "Allowed Paths")
    processors_data = [{
        "processor_type": "org.apache.nifi.processors.standard.HandleHttpRequest",
        "name": "TestInvalidBasePathProcessor",
        "position_x": 100,
        "position_y": 100,
        "process_group_id": pg_id,
        "properties": {
            "Listening Port": "8080",
            "Base Path": "/test/invalid",  # This is INVALID for HandleHttpRequest
            "Allow GET": "true"
        }
    }]
    
    create_result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_processors",
        arguments={"processors": processors_data, "process_group_id": pg_id},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    # Should be a list with one result
    assert isinstance(create_result, list) and len(create_result) == 1
    result = create_result[0]
    
    # Should succeed with warnings (invalid property removed)
    assert result["status"] in ["success", "warning"], f"Expected success/warning, got: {result.get('message')}"
    
    # Verify warnings about invalid property
    warnings = result.get("warnings", [])
    invalid_base_path_warning = any(
        "invalid property" in warning.lower() and "base path" in warning.lower() 
        for warning in warnings
    )
    assert invalid_base_path_warning, f"Expected warning about invalid 'Base Path' property. Got warnings: {warnings}"
    
    # Verify processor was created successfully
    entity = result.get("entity", {})
    assert entity.get("id"), "Processor should have been created"
    processor_id = entity["id"]
    
    # Verify the invalid property was removed
    properties = entity.get("properties", {})
    assert "Base Path" not in properties, f"Invalid 'Base Path' property should have been removed. Found properties: {list(properties.keys())}"
    
    # Verify valid properties are still there
    assert properties.get("Listening Port") == "8080", "Valid properties should be preserved"
    assert properties.get("Allow GET") == "true", "Valid properties should be preserved"
    
    global_logger.info(f"Test: Successfully validated Phase 2B property removal for processor {processor_id}")
    global_logger.info(f"Test: Warnings generated: {warnings}") 

@pytest.mark.anyio
async def test_create_processor_with_nested_position_format(
    test_pg: dict,
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    global_logger: any
):
    """Tests that processors can be created with nested position format {"position": {"x": 100, "y": 200}}."""
    pg_id = test_pg["id"]
    
    global_logger.info(f"Test: Creating processor with nested position format in PG {pg_id}")
    
    # Test both flat and nested position formats
    processors_data = [
        {
            "processor_type": "org.apache.nifi.processors.standard.LogAttribute",
            "name": "TestNestedPosition",
            "position": {"x": 200, "y": 150},  # Nested format
            "process_group_id": pg_id,
            "properties": {
                "Log Level": "info"
            }
        },
        {
            "processor_type": "org.apache.nifi.processors.standard.LogAttribute", 
            "name": "TestFlatPosition",
            "position_x": 400,  # Flat format
            "position_y": 150,
            "process_group_id": pg_id,
            "properties": {
                "Log Level": "debug"
            }
        }
    ]
    
    create_result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_processors",
        arguments={"processors": processors_data, "process_group_id": pg_id},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    # Should be a list with two results
    assert isinstance(create_result, list) and len(create_result) == 2
    
    # Both should succeed
    for i, result in enumerate(create_result):
        assert result["status"] in ["success", "warning"], f"Processor {i} creation failed: {result.get('message')}"
        
        # Verify processor was created with correct position
        entity = result.get("entity", {})
        assert entity.get("id"), f"Processor {i} should have been created"
        
        position = entity.get("position", {})
        expected_positions = [{"x": 200.0, "y": 150.0}, {"x": 400.0, "y": 150.0}]
        assert position == expected_positions[i], f"Processor {i} should have correct position {expected_positions[i]}, got {position}"
        
        global_logger.info(f"Test: Successfully created processor {entity.get('name')} at position {position}") 

@pytest.mark.anyio
async def test_create_processor_with_invalid_property_name_service_reference(
    test_pg: dict,
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    global_logger: any
):
    """Tests that Phase 2B property validation maps invalid property names containing service references to correct names."""
    pg_id = test_pg["id"]
    
    global_logger.info(f"Test: Creating HTTP Context Map service first in PG {pg_id}")
    
    # Step 1: Create a controller service
    create_service_result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_controller_services",
        arguments={
            "controller_services": [
                {
                    "service_type": "org.apache.nifi.http.StandardHttpContextMap",
                    "name": "TestHttpContextMap"
                }
            ],
            "process_group_id": pg_id
        },
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    assert create_service_result[0]["status"] == "success", f"Failed to create controller service: {create_service_result[0].get('message')}"
    service_id = create_service_result[0]["entity"]["id"]
    global_logger.info(f"Test: Successfully created service with ID: {service_id}")
    
    # Step 2: Create processor with wrong property name but correct service reference
    processors_data = [{
        "processor_type": "org.apache.nifi.processors.standard.HandleHttpRequest",
        "name": "TestInvalidPropertyNameServiceRef",
        "position_x": 100,
        "position_y": 100,
        "process_group_id": pg_id,
        "properties": {
            "Listening Port": "8080",
            "Context Map": "@TestHttpContextMap",  # WRONG property name but CORRECT service reference
            "Allow GET": "true"
        }
    }]
    
    create_result = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_processors",
        arguments={"processors": processors_data, "process_group_id": pg_id},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    # Should be a list with one result
    assert isinstance(create_result, list) and len(create_result) == 1
    result = create_result[0]
    
    # Should succeed with warnings (property name mapped)
    assert result["status"] in ["success", "warning"], f"Expected success/warning, got: {result.get('message')}"
    
    # Verify warnings about property name mapping
    warnings = result.get("warnings", [])
    property_mapping_warning = any(
        "mapped invalid property name" in warning.lower() and "context map" in warning.lower() and "http context map" in warning.lower()
        for warning in warnings
    )
    assert property_mapping_warning, f"Expected warning about property name mapping. Got warnings: {warnings}"
    
    # Verify processor was created successfully
    entity = result.get("entity", {})
    assert entity.get("id"), "Processor should have been created"
    processor_id = entity["id"]
    
    # Verify the property name was corrected AND service reference was resolved
    properties = entity.get("properties", {})
    assert "Context Map" not in properties, "Invalid property name should have been removed"
    assert "HTTP Context Map" in properties, "Correct property name should be present"
    
    # CRITICAL: Verify the service reference was resolved to the UUID
    http_context_map_value = properties.get("HTTP Context Map")
    assert http_context_map_value == service_id, f"Service reference should be resolved to UUID {service_id}, got {http_context_map_value}"
    
    # Verify other valid properties are still there
    assert properties.get("Listening Port") == "8080", "Valid properties should be preserved"
    assert properties.get("Allow GET") == "true", "Valid properties should be preserved"
    
    global_logger.info(f"Test: Successfully validated property name mapping and service resolution for processor {processor_id}")
    global_logger.info(f"Test: Service reference '@TestHttpContextMap' was correctly mapped and resolved to UUID {service_id}")
    global_logger.info(f"Test: Warnings generated: {warnings}") 