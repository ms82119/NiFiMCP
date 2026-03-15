# Tests for NiFi Connection operations 

import pytest
import httpx
from loguru import logger
from typing import Dict, Any
import anyio
import asyncio

from tests.utils.nifi_test_utils import call_tool

@pytest.mark.anyio
async def test_create_and_verify_connection(
    test_pg_with_processors: Dict[str, Any],  # Fixture provides PG with Generate and Log processors
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any
):
    """Test creating a connection between processors and verify its properties."""
    # Get processor IDs from the fixture
    generate_proc_id = test_pg_with_processors.get("generate_proc_id")
    log_proc_id = test_pg_with_processors.get("log_proc_id")
    pg_id = test_pg_with_processors.get("pg_id")
    assert generate_proc_id, "Generate Processor ID not found from fixture."
    assert log_proc_id, "Log Processor ID not found from fixture."
    assert pg_id, "Process Group ID not found from fixture."

    global_logger.info(f"Test: Creating connection from GenerateFlowFile ({generate_proc_id}) to LogAttribute ({log_proc_id})")
    
    # Create connection
    connect_args = {
        "source_name": generate_proc_id,
        "relationships": ["success"],
        "target_name": log_proc_id
    }
    conn_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_connections",
        arguments={"connections": [connect_args], "process_group_id": pg_id},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert isinstance(conn_result_list, list) and len(conn_result_list) > 0, "Connection creation should return a result list"
    conn_result = conn_result_list[0]
    assert conn_result.get("status") == "success", f"Failed to create connection: {conn_result.get('message')}"
    connection_id = conn_result.get("entity", {}).get("id")
    assert connection_id, "Connection ID not found in response."
    global_logger.info(f"Test: Successfully created Connection ID: {connection_id}")

    # Verify connection details
    verify_args = {
        "object_type": "connection",
        "object_id": connection_id
    }
    verify_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="get_nifi_object_details",
        arguments=verify_args,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert isinstance(verify_result_list, list) and len(verify_result_list) > 0, "Connection details should be returned"
    verify_result = verify_result_list[0]
    
    # Verify connection properties
    component = verify_result.get("component", {})
    assert component.get("source", {}).get("id") == generate_proc_id, "Source processor ID mismatch"
    assert component.get("destination", {}).get("id") == log_proc_id, "Destination processor ID mismatch"
    assert "success" in component.get("selectedRelationships", []), "Success relationship not found"
    
    global_logger.info(f"Test: Successfully verified connection {connection_id} properties.")

@pytest.mark.anyio
async def test_delete_connection(
    test_pg_with_processors: Dict[str, Any],  # Fixture provides PG with Generate and Log processors
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any
):
    """Test deleting a connection between processors."""
    # First create a connection
    generate_proc_id = test_pg_with_processors.get("generate_proc_id")
    log_proc_id = test_pg_with_processors.get("log_proc_id")
    pg_id = test_pg_with_processors.get("pg_id")
    assert generate_proc_id and log_proc_id and pg_id, "Processor IDs and PG ID not found from fixture."

    # Create connection
    connect_args = {
        "source_name": generate_proc_id,
        "relationships": ["success"],
        "target_name": log_proc_id
    }
    conn_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_connections",
        arguments={"connections": [connect_args], "process_group_id": pg_id},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    connection_id = conn_result_list[0].get("entity", {}).get("id")
    assert connection_id, "Failed to create test connection."
    global_logger.info(f"Test: Created test connection {connection_id} for deletion test.")

    # Now delete the connection
    delete_args = {
        "objects": [{
            "object_type": "connection",
            "object_id": connection_id,
            "name": f"test-connection-{connection_id}"
        }]
    }
    delete_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="delete_nifi_objects",
        arguments=delete_args,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert isinstance(delete_result_list, list) and len(delete_result_list) > 0, "Delete operation should return a result"
    delete_result = delete_result_list[0]
    assert delete_result.get("status") == "success", f"Failed to delete connection: {delete_result.get('message')}"
    global_logger.info(f"Test: Successfully deleted connection {connection_id}")

    # Verify connection is gone - should get a 400 error
    verify_args = {
        "object_type": "connection",
        "object_id": connection_id
    }
    try:
        verify_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="get_nifi_object_details",
            arguments=verify_args,
            headers=mcp_headers,
            custom_logger=global_logger
        )
        assert False, f"Connection {connection_id} still exists after deletion"
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            # This is expected - the connection should not exist
            global_logger.info(f"Test: Confirmed connection {connection_id} no longer exists (got expected 400 error).")
        else:
            raise  # Re-raise if it's not a 400 error

@pytest.mark.anyio
async def test_auto_purge_connection_with_queued_data(
    test_pg_with_processors: Dict[str, Any],  # Fixture provides PG with Generate and Log processors
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any
):
    """Test that deleting a connection with queued data and auto-purge enabled works correctly."""
    # Get processor IDs and process group ID from the fixture
    generate_proc_id = test_pg_with_processors.get("generate_proc_id")
    log_proc_id = test_pg_with_processors.get("log_proc_id")
    pg_id = test_pg_with_processors.get("pg_id")
    assert generate_proc_id and log_proc_id and pg_id, "Processor IDs and PG ID not found from fixture."

    # First auto-terminate relationships for the generator
    update_rels_args = {
        "processor_id": generate_proc_id,
        "auto_terminated_relationships": []  # Clear any auto-termination
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
        "Failed to update relationships"
    global_logger.info(f"Test: Updated relationships for processor {generate_proc_id}")

    # Create connection between processors
    connect_args = {
        "source_name": generate_proc_id,
        "relationships": ["success"],
        "target_name": log_proc_id
    }
    conn_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_connections",
        arguments={"connections": [connect_args], "process_group_id": pg_id},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert conn_result_list[0].get("status") == "success", "Failed to create connection"
    connection_id = conn_result_list[0].get("entity", {}).get("id")
    assert connection_id, "Connection ID not found in response"
    global_logger.info(f"Test: Created connection {connection_id} between processors")

    # Start the generator processor to create some queued data
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
    assert start_result_list[0].get("status") == "success", "Failed to start generator processor"
    global_logger.info(f"Test: Started generator processor {generate_proc_id}")
    await asyncio.sleep(2)  # Allow time for data to queue up

    # Stop the generator processor
    stop_args = {
        "object_type": "processor",
        "object_id": generate_proc_id,
        "operation_type": "stop"
    }
    stop_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="operate_nifi_objects",
        arguments={"operations": [stop_args]},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert stop_result_list[0].get("status") == "success", "Failed to stop generator processor"
    global_logger.info(f"Test: Stopped generator processor {generate_proc_id}")
    await asyncio.sleep(1)

    # Verify data is queued
    verify_conn_args = {
        "object_type": "connection",
        "object_id": connection_id
    }
    verify_conn_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="get_nifi_object_details",
        arguments=verify_conn_args,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    queued_count = int(verify_conn_list[0].get("status", {}).get("aggregateSnapshot", {}).get("queuedCount", "0"))
    assert queued_count > 0, "No data queued in connection for test"
    global_logger.info(f"Test: Verified {queued_count} items queued in connection {connection_id}")

    # Attempt delete with Auto-Purge enabled
    headers_auto_purge_true = {**mcp_headers, "X-Mcp-Auto-Purge-Enabled": "true"}
    delete_args = {
        "objects": [{
            "object_type": "connection",
            "object_id": connection_id,
            "name": f"test-connection-auto-purge-{connection_id}"
        }]
    }
    delete_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="delete_nifi_objects",
        arguments=delete_args,
        headers=headers_auto_purge_true,
        custom_logger=global_logger
    )
    # The Auto-Purge feature should be implemented now, expect success
    assert delete_result_list[0].get("status") == "success", \
        "Failed to delete connection with Auto-Purge enabled"
    global_logger.info("Test: Successfully deleted connection with Auto-Purge enabled")

    # Verify connection is gone
    try:
        verify_conn_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="get_nifi_object_details",
            arguments=verify_conn_args,
            headers=mcp_headers,
            custom_logger=global_logger
        )
        assert False, f"Connection {connection_id} still exists after deletion with Auto-Purge"
    except httpx.HTTPStatusError as e:
        if e.response.status_code in [400, 404]:
            # This is expected - the connection should not exist
            global_logger.info(f"Test: Confirmed connection {connection_id} no longer exists (got expected {e.response.status_code} error).")
        else:
            raise  # Re-raise if it's not a 400/404 error

    # Create a new connection for the Auto-Purge disabled test
    new_conn_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_connections",
        arguments={"connections": [connect_args], "process_group_id": pg_id},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert new_conn_result_list[0].get("status") == "success", "Failed to create new connection for disabled test"
    new_connection_id = new_conn_result_list[0].get("entity", {}).get("id")
    assert new_connection_id, "New connection ID not found in response"
    global_logger.info(f"Test: Created new connection {new_connection_id} for disabled test")

    # Generate flow files again
    await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="operate_nifi_objects",
        arguments={"operations": [start_args]},  # Start generator
        headers=mcp_headers,
        custom_logger=global_logger
    )
    await asyncio.sleep(2)  # Allow time for data to queue up
    await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="operate_nifi_objects",
        arguments={"operations": [stop_args]},  # Stop generator
        headers=mcp_headers,
        custom_logger=global_logger
    )
    await asyncio.sleep(1)

    # Verify data is queued in the new connection
    new_verify_conn_args = {
        "object_type": "connection",
        "object_id": new_connection_id
    }
    new_verify_conn_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="get_nifi_object_details",
        arguments=new_verify_conn_args,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    new_queued_count = int(new_verify_conn_list[0].get("status", {}).get("aggregateSnapshot", {}).get("queuedCount", "0"))
    assert new_queued_count > 0, "No data queued in new connection for disabled test"
    global_logger.info(f"Test: Verified {new_queued_count} items queued in new connection {new_connection_id}")

    # Attempt delete with header that would have disabled Auto-Purge (Auto-Purge is now always on)
    headers_auto_purge_false = {**mcp_headers, "X-Mcp-Auto-Purge-Enabled": "false"}
    delete_args = {
        "objects": [{
            "object_type": "connection",
            "object_id": new_connection_id,
            "name": f"test-connection-auto-purge-disabled-{new_connection_id}"
        }]
    }
    delete_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="delete_nifi_objects",
        arguments=delete_args,
        headers=headers_auto_purge_false,
        custom_logger=global_logger
    )
    assert delete_result_list[0].get("status") == "success", \
        "Auto-Purge is always on; delete with queued data should purge and succeed"
    global_logger.info("Test: Delete with queued data succeeded (Auto-Purge always on)")

@pytest.mark.anyio
async def test_purge_single_connection(
    test_pg_with_processors: Dict[str, Any],  # Fixture provides PG with Generate and Log processors
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any
):
    """Test purging flowfiles from a single connection."""
    # Get processor IDs and process group ID from the fixture
    generate_proc_id = test_pg_with_processors.get("generate_proc_id")
    log_proc_id = test_pg_with_processors.get("log_proc_id")
    pg_id = test_pg_with_processors.get("pg_id")
    assert generate_proc_id and log_proc_id and pg_id, "Processor IDs and PG ID not found from fixture."

    # First auto-terminate relationships for the generator
    update_rels_args = {
        "processor_id": generate_proc_id,
        "auto_terminated_relationships": []  # Clear any auto-termination
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
        "Failed to update relationships"
    global_logger.info(f"Test: Updated relationships for processor {generate_proc_id}")

    # Create connection between processors
    connect_args = {
        "source_name": generate_proc_id,
        "relationships": ["success"],
        "target_name": log_proc_id
    }
    conn_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_connections",
        arguments={"connections": [connect_args], "process_group_id": pg_id},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert conn_result_list[0].get("status") == "success", "Failed to create connection"
    connection_id = conn_result_list[0].get("entity", {}).get("id")
    assert connection_id, "Connection ID not found in response"
    global_logger.info(f"Test: Created connection {connection_id} between processors")

    # Start the generator processor to create some queued data
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
    assert start_result_list[0].get("status") == "success", "Failed to start generator processor"
    global_logger.info(f"Test: Started generator processor {generate_proc_id}")
    await asyncio.sleep(2)  # Allow time for data to queue up

    # Stop the generator processor
    stop_args = {
        "object_type": "processor",
        "object_id": generate_proc_id,
        "operation_type": "stop"
    }
    stop_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="operate_nifi_objects",
        arguments={"operations": [stop_args]},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert stop_result_list[0].get("status") == "success", "Failed to stop generator processor"
    global_logger.info(f"Test: Stopped generator processor {generate_proc_id}")
    await asyncio.sleep(1)

    # Verify data is queued
    verify_conn_args = {
        "object_type": "connection",
        "object_id": connection_id
    }
    verify_conn_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="get_nifi_object_details",
        arguments=verify_conn_args,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    queued_count = int(verify_conn_list[0].get("status", {}).get("aggregateSnapshot", {}).get("queuedCount", "0"))
    assert queued_count > 0, "No data queued in connection for test"
    global_logger.info(f"Test: Verified {queued_count} items queued in connection {connection_id}")

    # Purge the connection
    purge_args = {
        "target_id": connection_id,
        "target_type": "connection",
        "timeout_seconds": 30
    }
    purge_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="purge_flowfiles",
        arguments=purge_args,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert purge_result_list[0].get("success") is True, "Failed to purge connection"
    global_logger.info(f"Test: Successfully purged connection {connection_id}")

    # Verify queue is empty
    verify_empty_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="get_nifi_object_details",
        arguments=verify_conn_args,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    empty_count = int(verify_empty_list[0].get("status", {}).get("aggregateSnapshot", {}).get("queuedCount", "0"))
    assert empty_count == 0, f"Connection queue not empty after purge, contains {empty_count} items"
    global_logger.info(f"Test: Verified connection {connection_id} is empty after purge")

@pytest.mark.anyio
async def test_purge_process_group(
    test_pg_with_processors: Dict[str, Any],  # Fixture provides PG with Generate and Log processors
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any
):
    """Test purging all flowfiles from a process group."""
    # Get processor IDs and PG ID from the fixture
    generate_proc_id = test_pg_with_processors.get("generate_proc_id")
    log_proc_id = test_pg_with_processors.get("log_proc_id")
    pg_id = test_pg_with_processors.get("pg_id")
    assert generate_proc_id and log_proc_id and pg_id, "Required IDs not found from fixture."

    # Create additional processors for multiple connections
    processors = []
    for i in range(2):  # Create 2 additional GenerateFlowFile processors
        create_proc_args = {
            "processor_type": "org.apache.nifi.processors.standard.GenerateFlowFile",
            "name": f"Generate_{i+2}",  # Generate_2, Generate_3
            "position_x": (i+2) * 200,  # Space them out
            "position_y": 0
        }
        proc_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_processors",
            arguments={
                "processors": [create_proc_args],
                "process_group_id": pg_id
            },
            headers=mcp_headers,
            custom_logger=global_logger
        )
        assert proc_result_list[0].get("status") in ["success", "warning"], f"Failed to create additional generator {i+2}"
        proc_id = proc_result_list[0].get("entity", {}).get("id")
        assert proc_id, f"Processor {i+2} ID not found in response"
        global_logger.info(f"Test: Created additional generator processor {proc_id}")

        # Configure processor properties
        config_args = {
            "updates": [{
                "processor_id": proc_id,
                "properties": {
                    "File Size": "0B",
                    "Batch Size": "1",
                    "Data Format": "Text",
                    "Unique FlowFiles": "false"
                }
            }]
        }
        config_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="update_nifi_processors_properties",
            arguments=config_args,
            headers=mcp_headers,
            custom_logger=global_logger
        )
        assert config_result_list[0].get("status") in ["success", "warning"], f"Failed to configure generator {i+2}"
        global_logger.info(f"Test: Configured generator processor {proc_id}")

        # Configure processor relationships
        rels_args = {
            "processor_id": proc_id,
            "auto_terminated_relationships": []  # Don't auto-terminate any relationships
        }
        rels_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="update_nifi_processor_relationships",
            arguments=rels_args,
            headers=mcp_headers,
            custom_logger=global_logger
        )
        assert rels_result_list[0].get("status") in ["success", "warning"], f"Failed to configure relationships for generator {i+2}"
        global_logger.info(f"Test: Configured relationships for generator processor {proc_id}")

        processors.append(proc_id)

    # Create connections from each generator to the log processor
    connections = []
    source_processors = [generate_proc_id] + processors  # Original + new generators
    
    for i, source_id in enumerate(source_processors):
        # Create connection
        connect_args = {
            "source_name": source_id,
            "relationships": ["success"],
            "target_name": log_proc_id
        }
        conn_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_connections",
            arguments={"connections": [connect_args], "process_group_id": pg_id},
            headers=mcp_headers,
            custom_logger=global_logger
        )
        assert conn_result_list[0].get("status") == "success", f"Failed to create connection {i+1}"
        connection_id = conn_result_list[0].get("entity", {}).get("id")
        assert connection_id, f"Connection {i+1} ID not found in response"
        connections.append(connection_id)
        global_logger.info(f"Test: Created connection {connection_id}")

    try:
        # Start all generators to fill queues
        for proc_id in source_processors:
            start_args = {
                "object_type": "processor",
                "object_id": proc_id,
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
            assert start_result_list[0].get("status") == "success", f"Failed to start generator processor {proc_id}"
        await asyncio.sleep(2)  # Allow time for data to queue up

        # Stop all generators
        for proc_id in source_processors:
            stop_args = {
                "object_type": "processor",
                "object_id": proc_id,
                "operation_type": "stop"
            }
            stop_result_list = await call_tool(
                client=async_client,
                base_url=base_url,
                tool_name="operate_nifi_objects",
                arguments={"operations": [stop_args]},
                headers=mcp_headers,
                custom_logger=global_logger
            )
            assert stop_result_list[0].get("status") == "success", f"Failed to stop generator processor {proc_id}"
        await asyncio.sleep(1)

        # Verify data is queued in all connections
        total_queued = 0
        for conn_id in connections:
            verify_args = {
                "object_type": "connection",
                "object_id": conn_id
            }
            verify_list = await call_tool(
                client=async_client,
                base_url=base_url,
                tool_name="get_nifi_object_details",
                arguments=verify_args,
                headers=mcp_headers,
                custom_logger=global_logger
            )
            queued = int(verify_list[0].get("status", {}).get("aggregateSnapshot", {}).get("queuedCount", "0"))
            assert queued > 0, f"No data queued in connection {conn_id}"
            total_queued += queued
        global_logger.info(f"Test: Verified total of {total_queued} items queued across {len(connections)} connections")

        # Purge the entire process group
        purge_args = {
            "target_id": pg_id,
            "target_type": "process_group",
            "timeout_seconds": 30
        }
        purge_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="purge_flowfiles",
            arguments=purge_args,
            headers=mcp_headers,
            custom_logger=global_logger
        )
        assert purge_result_list[0].get("success") is True, "Failed to purge process group"
        global_logger.info(f"Test: Successfully purged process group {pg_id}")

        # Verify all queues are empty
        for conn_id in connections:
            verify_args = {
                "object_type": "connection",
                "object_id": conn_id
            }
            verify_list = await call_tool(
                client=async_client,
                base_url=base_url,
                tool_name="get_nifi_object_details",
                arguments=verify_args,
                headers=mcp_headers,
                custom_logger=global_logger
            )
            empty_count = int(verify_list[0].get("status", {}).get("aggregateSnapshot", {}).get("queuedCount", "0"))
            assert empty_count == 0, f"Connection {conn_id} queue not empty after purge, contains {empty_count} items"
        global_logger.info(f"Test: Verified all connections in process group {pg_id} are empty after purge")

    finally:
        # Cleanup additional processors and connections
        global_logger.info("Test: Starting cleanup of test components")
        
        # Stop any running processors first
        for proc_id in source_processors:
            try:
                stop_args = {
                    "object_type": "processor",
                    "object_id": proc_id,
                    "operation_type": "stop"
                }
                await call_tool(
                    client=async_client,
                    base_url=base_url,
                    tool_name="operate_nifi_objects",
                    arguments={"operations": [stop_args]},
                    headers=mcp_headers,
                    custom_logger=global_logger
                )
            except Exception as e:
                global_logger.warning(f"Error stopping processor {proc_id} during cleanup: {e}")

        # Delete connections
        for conn_id in connections:
            try:
                delete_args = {
                    "objects": [{
                        "object_type": "connection",
                        "object_id": conn_id,
                        "name": f"cleanup-connection-{conn_id}"
                    }]
                }
                # Use auto-purge enabled headers for cleanup
                cleanup_headers = {**mcp_headers, "X-Mcp-Auto-Purge-Enabled": "true"}
                await call_tool(
                    client=async_client,
                    base_url=base_url,
                    tool_name="delete_nifi_objects",
                    arguments=delete_args,
                    headers=cleanup_headers,
                    custom_logger=global_logger
                )
            except Exception as e:
                global_logger.warning(f"Error deleting connection {conn_id} during cleanup: {e}")

        # Delete additional processors
        for proc_id in processors:  # Only delete the ones we created
            try:
                delete_args = {
                    "objects": [{
                        "object_type": "processor",
                        "object_id": proc_id,
                        "name": f"cleanup-processor-{proc_id}"
                    }]
                }
                # Use auto-stop enabled headers for cleanup
                cleanup_headers = {**mcp_headers, "X-Mcp-Auto-Stop-Enabled": "true"}
                await call_tool(
                    client=async_client,
                    base_url=base_url,
                    tool_name="delete_nifi_objects",
                    arguments=delete_args,
                    headers=cleanup_headers,
                    custom_logger=global_logger
                )
            except Exception as e:
                global_logger.warning(f"Error deleting processor {proc_id} during cleanup: {e}")

# Add other connection related tests here 