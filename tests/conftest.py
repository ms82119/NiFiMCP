import pytest
import httpx
import os
import sys
import uuid
import asyncio
from loguru import logger
from typing import AsyncGenerator, Any, Dict, List
from config.settings import get_nifi_servers
from tests.utils.nifi_test_utils import call_tool

# Enable anyio for async test support
pytest_plugins = ("anyio",)

# --- Base Configuration Fixtures ---

@pytest.fixture(scope="session")
def base_url() -> str:
    url = os.environ.get("MCP_SERVER_URL")
    if url:
        return url
    port = os.environ.get("MCP_SERVER_PORT", "8001")
    return f"http://localhost:{port}"

@pytest.fixture(scope="session")
def target_nifi_server_id(global_logger: Any) -> str:
    server_id = os.environ.get("NIFI_TEST_SERVER_ID")
    if not server_id:
        global_logger.error("FATAL: NIFI_TEST_SERVER_ID environment variable is not set.")
        global_logger.error("Please set this variable to the ID of the target NiFi server from the MCP server's config.yaml.")
        pytest.exit("NIFI_TEST_SERVER_ID not set", returncode=1)
    return server_id

@pytest.fixture(scope="session")
def test_run_id() -> str:
    """Provides a unique ID for the current test session."""
    return str(uuid.uuid4())[:8]

# --- Logging Fixture ---

@pytest.fixture(scope="session")
def global_logger() -> Any:
    """Session-scoped logger for tests."""
    logger.remove()
    logger.add(sys.stderr, level=os.environ.get("MCP_TEST_LOG_LEVEL", "INFO").upper())
    return logger

# --- HTTP Client Fixture ---

@pytest.fixture(scope="module")
async def async_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    async with httpx.AsyncClient() as client:
        yield client

# --- Header Fixtures ---

@pytest.fixture(scope="module")
def base_headers() -> dict:
    """Basic headers for most API calls."""
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

@pytest.fixture(scope="module")
def mcp_headers(base_headers: dict, nifi_test_server_id: str) -> dict:
    """Headers required for MCP NiFi operations, including the target server ID."""
    return {
        **base_headers,
        "X-Nifi-Server-Id": nifi_test_server_id,
    }

@pytest.fixture(scope="module")
def nifi_test_server_id():
    """Provides the NiFi server ID for testing from config."""
    servers = get_nifi_servers()
    if not servers:
        pytest.fail("No NiFi servers configured in config.yaml")
    # Use the first configured server for testing
    server_id = servers[0].get('id')
    if not server_id:
        pytest.fail("First NiFi server in config.yaml has no ID")
    return server_id

@pytest.fixture(scope="module", autouse=True)
async def check_server_connectivity(base_url: str, nifi_test_server_id: str, async_client: httpx.AsyncClient, global_logger: Any):
    """Fixture to check MCP server connectivity and NiFi configuration at the start of the session."""
    global_logger.info(f"Checking server connectivity to {base_url} and NiFi config for {nifi_test_server_id}...")
    try:
        config_url = f"{base_url}/config/nifi-servers"
        config_resp = await async_client.get(config_url, timeout=10.0)
        config_resp.raise_for_status()
        servers = config_resp.json()
        server_ids = [s.get('id') for s in servers]
        if nifi_test_server_id not in server_ids:
            global_logger.error(f"FATAL: Target NiFi Server ID '{nifi_test_server_id}' not found in server config: {server_ids}")
            pytest.exit(f"Target NiFi Server ID '{nifi_test_server_id}' not found in server config.", returncode=1)
        global_logger.info(f"Server contacted successfully. Target NiFi ID '{nifi_test_server_id}' found in config.")
    except httpx.RequestError as e:
        global_logger.error(f"FATAL: Could not connect to MCP server at {base_url}: {e}")
        pytest.exit(f"Could not connect to MCP server at {base_url}", returncode=1)
    except httpx.HTTPStatusError as e:
        global_logger.error(f"FATAL: Error fetching config from {base_url}: {e.response.status_code} - {e.response.text}")
        pytest.exit(f"Error fetching config from {base_url}", returncode=1)
    except Exception as e:
        global_logger.error(f"FATAL: Unexpected error during server connectivity check: {e}")
        pytest.exit(f"Unexpected error during server connectivity check: {e}", returncode=1)

# Add more fixtures here as we refactor (e.g., for creating/cleaning up test PGs)

# --- Root Process Group ID Fixture ---

@pytest.fixture(scope="module")
async def root_process_group_id(async_client, base_url, mcp_headers, global_logger):
    """Fetch the root process group ID for the configured NiFi server."""
    url = f"{base_url}/tools/get_process_group_status"
    resp = await async_client.post(url, json={"arguments": {"process_group_id": "root"}}, headers=mcp_headers)
    resp.raise_for_status()
    data = resp.json()
    root_pg_id = data.get("process_group_id")
    assert root_pg_id, "Could not fetch root process group ID"
    global_logger.info(f"Fetched root process group ID: {root_pg_id}")
    return root_pg_id

@pytest.fixture(scope="function")
async def test_pg(
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    nifi_test_server_id: str,  # Changed from test_run_id
    global_logger: Any,
    root_process_group_id: str,  # <-- Add this
):
    """Creates a test process group for a test function and ensures its deletion afterwards."""
    pg_name = f"mcp-test-pg-{nifi_test_server_id}"  # Use server ID instead of run ID
    global_logger.info(f"Fixture: Creating test process group: {pg_name}")
    create_pg_args = {"name": pg_name, "position_x": 0, "position_y": 0, "parent_process_group_id": root_process_group_id}
    
    pg_result_list = await call_tool(
        client=async_client, 
        base_url=base_url, 
        tool_name="create_nifi_process_group", 
        arguments=create_pg_args, 
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    assert isinstance(pg_result_list, list) and len(pg_result_list) > 0 and isinstance(pg_result_list[0], dict), \
        "Unexpected response format for create_nifi_process_group in fixture"
    pg_result = pg_result_list[0]
    assert pg_result.get("status") == "success", \
        f"Fixture failed to create process group: {pg_result.get('message')}"
    pg_id = pg_result.get("entity", {}).get("id")
    assert pg_id, "Process group ID not found in fixture response."
    global_logger.info(f"Fixture: Successfully created Process Group ID: {pg_id}")

    # Get the process group details
    details_args = {"object_type": "process_group", "object_id": pg_id}
    details_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="get_nifi_object_details",
        arguments=details_args,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert isinstance(details_result_list, list) and details_result_list and isinstance(details_result_list[0], dict), \
        "Unexpected response format for get_nifi_object_details in fixture"
    details_result = details_result_list[0]
    assert details_result.get("status") != "error", \
        f"Failed to get details for process group {pg_id}: {details_result.get('message')}"
    
    pg_details = {
        "id": pg_id,
        "name": pg_name,
        "details": details_result.get("component", details_result)
    }

    yield pg_details # Provide the PG details to the test

    # Teardown: First stop, then purge flowfiles, then delete the process group
    global_logger.info(f"Fixture: Cleaning up test process group: {pg_id}")
    try:
        # Step 1: Stop the process group first
        stop_args = {
            "object_type": "process_group",
            "object_id": pg_id,
            "operation_type": "stop"
        }
        try:
            stop_result_list = await call_tool(
                client=async_client,
                base_url=base_url,
                tool_name="operate_nifi_objects",
                arguments={"operations": [stop_args]},
                headers=mcp_headers,
                custom_logger=global_logger
            )
            if stop_result_list and stop_result_list[0].get("status") == "success":
                global_logger.info(f"Fixture: Successfully stopped Process Group {pg_id}")
            else:
                global_logger.warning(f"Fixture: Could not confirm Process Group {pg_id} stopped")
        except Exception as e_stop:
            global_logger.warning(f"Fixture: Error stopping Process Group {pg_id} (continuing with cleanup): {e_stop}")

        # Step 2: Disable any controller services in the process group
        try:
            # List controller services in the process group
            list_cs_args = {
                "object_type": "controller_services",
                "process_group_id": pg_id
            }
            cs_list_result = await call_tool(
                client=async_client,
                base_url=base_url,
                tool_name="list_nifi_objects",
                arguments=list_cs_args,
                headers=mcp_headers,
                custom_logger=global_logger
            )
            
            if cs_list_result and isinstance(cs_list_result, list) and len(cs_list_result) > 0:
                global_logger.info(f"Fixture: Found {len(cs_list_result)} controller services to disable in Process Group {pg_id}")
                
                # Disable each controller service
                for cs in cs_list_result:
                    cs_id = cs.get("id")
                    if cs_id:
                        try:
                            disable_cs_args = {
                                "object_type": "controller_service",
                                "object_id": cs_id,
                                "operation_type": "disable"
                            }
                            disable_cs_result = await call_tool(
                                client=async_client,
                                base_url=base_url,
                                tool_name="operate_nifi_objects",
                                arguments={"operations": [disable_cs_args]},
                                headers=mcp_headers,
                                custom_logger=global_logger
                            )
                            if disable_cs_result and disable_cs_result[0].get("status") == "success":
                                global_logger.info(f"Fixture: Successfully disabled controller service {cs_id}")
                            else:
                                global_logger.warning(f"Fixture: Could not confirm controller service {cs_id} disabled")
                        except Exception as e_cs_disable:
                            global_logger.warning(f"Fixture: Error disabling controller service {cs_id} (continuing): {e_cs_disable}")
            else:
                global_logger.info(f"Fixture: No controller services found in Process Group {pg_id}")
        except Exception as e_cs_list:
            global_logger.warning(f"Fixture: Error listing controller services (continuing with cleanup): {e_cs_list}")

        # Step 3: Purge any queued flowfiles
        purge_args = {
            "target_id": pg_id,
            "target_type": "process_group",
            "timeout_seconds": 30
        }
        try:
            purge_result_list = await call_tool(
                client=async_client,
                base_url=base_url,
                tool_name="purge_flowfiles",
                arguments=purge_args,
                headers=mcp_headers,
                custom_logger=global_logger
            )
            if purge_result_list and purge_result_list[0].get("status") == "success":
                global_logger.info(f"Fixture: Successfully purged flowfiles from Process Group {pg_id}")
            else:
                global_logger.warning(f"Fixture: Some flowfiles may remain in Process Group {pg_id}")
        except Exception as e_purge:
            # Continue even if purge fails - we'll still try to delete the Process Group
            global_logger.warning(f"Fixture: Error purging flowfiles (continuing with cleanup): {e_purge}")

        # Step 4: Delete the process group
        delete_pg_args = {
            "objects": [{
                "object_type": "process_group", 
                "object_id": pg_id,
                "name": f"Test Process Group {pg_id}"
            }]
        }
        delete_pg_result_list = await call_tool(
            client=async_client, 
            base_url=base_url,
            tool_name="delete_nifi_objects", 
            arguments=delete_pg_args, 
            headers=mcp_headers,
            custom_logger=global_logger
        )
        assert isinstance(delete_pg_result_list, list) and len(delete_pg_result_list) > 0 and isinstance(delete_pg_result_list[0], dict), \
            "Unexpected response format for delete_nifi_objects (process group) in fixture teardown"
        delete_pg_result = delete_pg_result_list[0]
        if delete_pg_result.get("status") == "success":
            global_logger.info(f"Fixture: Successfully deleted Test Process Group {pg_id}")
        else:
            global_logger.error(f"Fixture: Failed to delete Test Process Group {pg_id}: {delete_pg_result.get('message')}")
            # Log this as a critical cleanup failure
            global_logger.critical(f"CLEANUP FAILURE: Process Group {pg_id} ({pg_name}) was not deleted and may need manual cleanup!")
    except Exception as e_del_pg:
        global_logger.error(f"Fixture: Error during Test Process Group {pg_id} cleanup: {e_del_pg}", exc_info=False)
        # Log this as a critical cleanup failure
        global_logger.critical(f"CLEANUP FAILURE: Process Group {pg_id} ({pg_name}) may need manual cleanup due to exception!")

@pytest.fixture(scope="function")
async def test_pg_with_processors(
    test_pg: dict, # Get the details from the test_pg fixture
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    nifi_test_server_id: str, # Changed from test_run_id
    global_logger: Any,
    root_process_group_id: str,  # <-- Add this for consistency (even if not used directly)
) -> AsyncGenerator[Dict[str, Any], None]:
    """Creates a test PG and two standard processors (Generate & Log) within it."""
    pg_id = test_pg["id"]
    pg_name = test_pg["name"]
    global_logger.info(f"Fixture: test_pg_with_processors using PG ID: {pg_id}")

    # Initialize components dictionary
    components: Dict[str, Any] = {}

    # 1. Create GenerateFlowFile Processor
    gen_proc_name = f"mcp-test-generate-{nifi_test_server_id}"  # Use server ID instead of run ID
    create_gen_args = {
        "processor_type": "org.apache.nifi.processors.standard.GenerateFlowFile",
        "name": gen_proc_name,
        "position_x": 0,
        "position_y": 0
    }
    global_logger.info(f"Fixture: Creating GenerateFlowFile processor: {gen_proc_name} in PG {pg_id}")
    gen_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_processors",
        arguments={"processors": [create_gen_args], "process_group_id": pg_id},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert isinstance(gen_result_list, list) and len(gen_result_list) > 0 and isinstance(gen_result_list[0], dict), \
        "Unexpected response format for create_nifi_processor (Generate) in fixture"
    gen_result = gen_result_list[0]
    assert gen_result.get("status") in ["success", "warning"], \
        f"Fixture failed to create Generate processor: {gen_result.get('message')}"
    generate_proc_id = gen_result.get("entity", {}).get("id")
    assert generate_proc_id, "Generate processor ID not found in fixture response."
    global_logger.info(f"Fixture: Successfully created GenerateFlowFile Processor ID: {generate_proc_id}")

    # Get Generate processor details
    gen_details_args = {"object_type": "processor", "object_id": generate_proc_id}
    gen_details_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="get_nifi_object_details",
        arguments=gen_details_args,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert isinstance(gen_details_list, list) and gen_details_list and isinstance(gen_details_list[0], dict)
    gen_details = gen_details_list[0].get("component", gen_details_list[0])

    # 2. Create LogAttribute Processor
    log_proc_name = f"mcp-test-log-{nifi_test_server_id}"  # Use server ID instead of run ID
    create_log_args = {
        "processor_type": "org.apache.nifi.processors.standard.LogAttribute",
        "name": log_proc_name,
        "position_x": 0,
        "position_y": 200
    }
    global_logger.info(f"Fixture: Creating LogAttribute processor: {log_proc_name} in PG {pg_id}")
    log_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_processors",
        arguments={"processors": [create_log_args], "process_group_id": pg_id},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert isinstance(log_result_list, list) and len(log_result_list) > 0 and isinstance(log_result_list[0], dict), \
        "Unexpected response format for create_nifi_processor (Log) in fixture"
    log_result = log_result_list[0]
    assert log_result.get("status") in ["success", "warning"], \
        f"Fixture failed to create Log processor: {log_result.get('message')}"
    log_proc_id = log_result.get("entity", {}).get("id")
    assert log_proc_id, "Log processor ID not found in fixture response."
    global_logger.info(f"Fixture: Successfully created LogAttribute Processor ID: {log_proc_id}")

    # Get Log processor details
    log_details_args = {"object_type": "processor", "object_id": log_proc_id}
    log_details_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="get_nifi_object_details",
        arguments=log_details_args,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert isinstance(log_details_list, list) and log_details_list and isinstance(log_details_list[0], dict)
    log_details = log_details_list[0].get("component", log_details_list[0])

    # 3. Create Split Processor (RouteOnAttribute)
    split_proc_args = {
        "processor_type": "org.apache.nifi.processors.standard.RouteOnAttribute",
        "name": "Split Flow",
        "position_x": 300,
        "position_y": 100,
        "properties": {
            "Route Strategy": "Route to Property name",
            "Routing Strategy": "Route to Property name",
            "success": "${status:equals('success')}",
            "failure": "${status:equals('failure')}",
            "original": "${status:equals('original')}"
        }
    }
    split_proc_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_processors",
        arguments={"processors": [split_proc_args], "process_group_id": pg_id},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    assert isinstance(split_proc_result_list, list) and len(split_proc_result_list) > 0
    split_proc_result = split_proc_result_list[0]
    assert split_proc_result.get("status") in ["success", "warning"]
    split_proc_id = split_proc_result.get("entity", {}).get("id")
    assert split_proc_id
    components["split_processor"] = split_proc_id

    # Return the process group details and processors
    result = {
        "pg_details": test_pg["details"],
        "processors": [gen_details, log_details],
        "pg_id": pg_id,
        "generate_proc_id": generate_proc_id,
        "log_proc_id": log_proc_id
    }

    yield result

    # Teardown for processors is implicitly handled by the test_pg fixture deleting the parent PG.
    # If explicit processor deletion testing is needed, separate fixtures/tests would be better.
    global_logger.info(f"Fixture: test_pg_with_processors cleanup (delegated to test_pg for PG {pg_id})")

@pytest.fixture(scope="function")
async def test_connection(
    test_pg_with_processors: dict, # Get processor IDs from this fixture
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
    global_logger: Any
):
    """Creates a connection between the Generate and Log processors from test_pg_with_processors."""
    generate_proc_id = test_pg_with_processors["generate_proc_id"]
    log_proc_id = test_pg_with_processors["log_proc_id"]
    pg_id = test_pg_with_processors["pg_id"]

    global_logger.info(f"Fixture: Connecting {generate_proc_id} -> {log_proc_id}")

    connect_args = {
        "source_id": generate_proc_id,
        "relationships": ["success"], # Default relationship for GenerateFlowFile
        "target_id": log_proc_id
    }
    conn_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_connections",
        arguments={"connections": [connect_args], "process_group_id": pg_id},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert isinstance(conn_result_list, list) and conn_result_list and isinstance(conn_result_list[0], dict), \
        "Unexpected response format for create_nifi_connection in fixture"
    conn_result = conn_result_list[0]
    assert conn_result.get("status") == "success", \
        f"Fixture failed to create connection: {conn_result.get('message')}"
    connection_id = conn_result.get("entity", {}).get("id")
    assert connection_id, "Connection ID not found in fixture response."
    global_logger.info(f"Fixture: Successfully created Connection ID: {connection_id}")

    yield connection_id

    # Teardown: Delete the connection
    global_logger.info(f"Fixture: Cleaning up test connection: {connection_id}")
    try:
        delete_conn_args = {
            "objects": [{
                "object_type": "connection",
                "object_id": connection_id,
                "name": f"test-connection-{connection_id}"
            }]
        }
        delete_conn_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="delete_nifi_objects",
            arguments=delete_conn_args,
            headers=mcp_headers,
            custom_logger=global_logger
        )
        delete_conn_result = delete_conn_result_list[0]
        if delete_conn_result.get("status") == "success":
            global_logger.info(f"Fixture: Successfully deleted Connection {connection_id}")
        else:
            global_logger.warning(f"Fixture: Could not delete Connection {connection_id}: {delete_conn_result.get('message')}")
    except Exception as e_del_conn:
        global_logger.error(f"Fixture: Error during Connection {connection_id} deletion: {e_del_conn}", exc_info=False)

@pytest.fixture
async def test_complex_flow(
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any,
    root_process_group_id: str,  # <-- Add this
) -> AsyncGenerator[Dict[str, Any], None]:
    """Creates a more complex flow for testing documentation features.
    
    Flow structure:
    Input Port -> Split -> [Success] -> Transform -> [Success] -> Merge
                       -> [Failure] -> LogError
                       -> [Original] -> LogOriginal
    """
    components: Dict[str, Any] = {}
    
    # Create a process group to contain our test flow
    pg_creation = {
        "name": f"mcp-test-doc-flow-{mcp_headers['X-Nifi-Server-Id']}",
        "position_x": 0,
        "position_y": 0,
        "parent_process_group_id": root_process_group_id,
    }
    
    # Create the process group
    pg_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_process_group",
        arguments=pg_creation,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    assert isinstance(pg_result_list, list) and len(pg_result_list) > 0, "Expected non-empty list response"
    pg_result = pg_result_list[0]
    assert pg_result.get("status") in ["success", "warning"], f"Failed to create process group: {pg_result.get('message')}"
    
    pg_id = pg_result.get("entity", {}).get("id")
    assert pg_id, "Process group ID not found in response"
    components["process_group_id"] = pg_id

    try:
        # Input Port
        input_port_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_ports",
            arguments={
                "ports": [{
                    "name": "Test Input",
                    "position_x": 100,
                    "position_y": 100,
                    "port_type": "input"
                }],
                "process_group_id": pg_id
            },
            headers=mcp_headers,
            custom_logger=global_logger
        )
        
        assert isinstance(input_port_result_list, list) and len(input_port_result_list) > 0
        input_port_result = input_port_result_list[0]
        assert input_port_result.get("status") in ["success", "warning"]
        input_port_id = input_port_result.get("entity", {}).get("id")
        assert input_port_id
        components["input_port"] = input_port_id
        
        # Split Processor (RouteOnAttribute)
        split_proc_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_processors",
            arguments={
                "processors": [{
                    "processor_type": "org.apache.nifi.processors.standard.RouteOnAttribute",
                    "name": "Split Flow",
                    "position_x": 300,
                    "position_y": 100,
                    "properties": {
                        "Route Strategy": "Route to Property name",
                        "Routing Strategy": "Route to Property name",
                        "success": "${status:equals('success')}",
                        "failure": "${status:equals('failure')}",
                        "original": "${status:equals('original')}"
                    }
                }],
                "process_group_id": pg_id
            },
            headers=mcp_headers,
            custom_logger=global_logger
        )
        
        assert isinstance(split_proc_result_list, list) and len(split_proc_result_list) > 0
        split_proc_result = split_proc_result_list[0]
        assert split_proc_result.get("status") in ["success", "warning"]
        split_proc_id = split_proc_result.get("entity", {}).get("id")
        assert split_proc_id
        components["split_processor"] = split_proc_id
        
        # Transform Processor (Success Path)
        transform_proc_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_processors",
            arguments={
                "processors": [{
                    "processor_type": "org.apache.nifi.processors.attributes.UpdateAttribute",
                    "name": "Transform Data",
                    "position_x": 500,
                    "position_y": 0,
                    "properties": {
                        "transformed": "true"
                    }
                }],
                "process_group_id": pg_id
            },
            headers=mcp_headers,
            custom_logger=global_logger
        )
        
        assert isinstance(transform_proc_result_list, list) and len(transform_proc_result_list) > 0
        transform_proc_result = transform_proc_result_list[0]
        assert transform_proc_result.get("status") in ["success", "warning"]
        transform_proc_id = transform_proc_result.get("entity", {}).get("id")
        assert transform_proc_id
        components["transform_processor"] = transform_proc_id
        
        # Merge Processor
        merge_proc_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_processors",
            arguments={
                "processors": [{
                    "processor_type": "org.apache.nifi.processors.standard.MergeContent",
                    "name": "Merge Results",
                    "position_x": 700,
                    "position_y": 0,
                    "properties": {
                        "Merge Strategy": "Defragment",
                        "Correlation Attribute Name": "fragment.identifier"
                    }
                }],
                "process_group_id": pg_id
            },
            headers=mcp_headers,
            custom_logger=global_logger
        )
        
        assert isinstance(merge_proc_result_list, list) and len(merge_proc_result_list) > 0
        merge_proc_result = merge_proc_result_list[0]
        assert merge_proc_result.get("status") in ["success", "warning"]
        merge_proc_id = merge_proc_result.get("entity", {}).get("id")
        assert merge_proc_id
        components["merge_processor"] = merge_proc_id
        
        # Error Logger (Failure Path)
        error_log_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_processors",
            arguments={
                "processors": [{
                    "processor_type": "org.apache.nifi.processors.standard.LogAttribute",
                    "name": "Log Error",
                    "position_x": 500,
                    "position_y": 200,
                    "properties": {
                        "Log Level": "WARN",
                        "Log Prefix": "Flow Error"
                    }
                }],
                "process_group_id": pg_id
            },
            headers=mcp_headers,
            custom_logger=global_logger
        )
        
        assert isinstance(error_log_result_list, list) and len(error_log_result_list) > 0
        error_log_result = error_log_result_list[0]
        assert error_log_result.get("status") in ["success", "warning"]
        error_log_id = error_log_result.get("entity", {}).get("id")
        assert error_log_id
        components["error_logger"] = error_log_id
        
        # Original Path Logger
        orig_log_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_processors",
            arguments={
                "processors": [{
                    "processor_type": "org.apache.nifi.processors.standard.LogAttribute",
                    "name": "Log Original",
                    "position_x": 500,
                    "position_y": 400,
                    "properties": {
                        "Log Level": "INFO",
                        "Log Prefix": "Original Flow"
                    }
                }],
                "process_group_id": pg_id
            },
            headers=mcp_headers,
            custom_logger=global_logger
        )
        
        assert isinstance(orig_log_result_list, list) and len(orig_log_result_list) > 0
        orig_log_result = orig_log_result_list[0]
        assert orig_log_result.get("status") in ["success", "warning"]
        orig_log_id = orig_log_result.get("entity", {}).get("id")
        assert orig_log_id
        components["original_logger"] = orig_log_id
        
        # Create connections
        # Input Port -> Split
        input_port_conn_result = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_connections",
            arguments={"connections": [{
                "source_name": input_port_id,
                "target_name": split_proc_id,
                "relationships": []  # Empty list for port connections
            }], "process_group_id": pg_id},
            headers=mcp_headers,
            custom_logger=global_logger
        )
        
        # Verify the connection was created
        assert isinstance(input_port_conn_result, list) and len(input_port_conn_result) > 0
        input_port_conn = input_port_conn_result[0]
        assert input_port_conn.get("status") in ["success", "warning"], \
            f"Failed to create input port connection: {input_port_conn.get('message')}"
        
        # Split -> Transform (Success)
        await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_connections",
            arguments={"connections": [{
                "source_name": split_proc_id,
                "target_name": transform_proc_id,
                "relationships": ["success"]
            }], "process_group_id": pg_id},
            headers=mcp_headers,
            custom_logger=global_logger
        )
        
        # Split -> Error Log (Failure)
        await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_connections",
            arguments={"connections": [{
                "source_name": split_proc_id,
                "target_name": error_log_id,
                "relationships": ["failure"]
            }], "process_group_id": pg_id},
            headers=mcp_headers,
            custom_logger=global_logger
        )

        # Split -> Original Log (Original)
        await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_connections",
            arguments={"connections": [{
                "source_name": split_proc_id,
                "target_name": orig_log_id,
                "relationships": ["original"]
            }], "process_group_id": pg_id},
            headers=mcp_headers,
            custom_logger=global_logger
        )
        
        # Transform -> Merge (Success)
        await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_connections",
            arguments={"connections": [{
                "source_name": transform_proc_id,
                "target_name": merge_proc_id,
                "relationships": ["success"]
            }], "process_group_id": pg_id},
            headers=mcp_headers,
            custom_logger=global_logger
        )
        
        yield components

    finally:
        # Teardown: First stop the process group, then purge flowfiles, then delete
        global_logger.info(f"Fixture: Cleaning up test complex flow in process group: {pg_id}")
        try:
            # 1. Stop the process group
            stop_args = {
                "object_type": "process_group",
                "object_id": pg_id,
                "operation_type": "stop"
            }
            try:
                stop_result_list = await call_tool(
                    client=async_client,
                    base_url=base_url,
                    tool_name="operate_nifi_objects",
                    arguments={"operations": [stop_args]},
                    headers=mcp_headers,
                    custom_logger=global_logger
                )
                if stop_result_list and stop_result_list[0].get("status") == "success":
                    global_logger.info(f"Fixture: Successfully stopped Process Group {pg_id}")
                else:
                    global_logger.warning(f"Fixture: Could not confirm Process Group {pg_id} stopped")
            except Exception as e_stop:
                global_logger.warning(f"Fixture: Error stopping Process Group {pg_id} (continuing with cleanup): {e_stop}")

            # 2. Disable any controller services in the process group
            try:
                # List controller services in the process group
                list_cs_args = {
                    "object_type": "controller_services",
                    "process_group_id": pg_id
                }
                cs_list_result = await call_tool(
                    client=async_client,
                    base_url=base_url,
                    tool_name="list_nifi_objects",
                    arguments=list_cs_args,
                    headers=mcp_headers,
                    custom_logger=global_logger
                )
                
                if cs_list_result and isinstance(cs_list_result, list) and len(cs_list_result) > 0:
                    global_logger.info(f"Fixture: Found {len(cs_list_result)} controller services to disable in Process Group {pg_id}")
                    
                    # Disable each controller service
                    for cs in cs_list_result:
                        cs_id = cs.get("id")
                        if cs_id:
                            try:
                                disable_cs_args = {
                                    "object_type": "controller_service",
                                    "object_id": cs_id,
                                    "operation_type": "disable"
                                }
                                disable_cs_result = await call_tool(
                                    client=async_client,
                                    base_url=base_url,
                                    tool_name="operate_nifi_objects",
                                    arguments={"operations": [disable_cs_args]},
                                    headers=mcp_headers,
                                    custom_logger=global_logger
                                )
                                if disable_cs_result and disable_cs_result[0].get("status") == "success":
                                    global_logger.info(f"Fixture: Successfully disabled controller service {cs_id}")
                                else:
                                    global_logger.warning(f"Fixture: Could not confirm controller service {cs_id} disabled")
                            except Exception as e_cs_disable:
                                global_logger.warning(f"Fixture: Error disabling controller service {cs_id} (continuing): {e_cs_disable}")
                else:
                    global_logger.info(f"Fixture: No controller services found in Process Group {pg_id}")
            except Exception as e_cs_list:
                global_logger.warning(f"Fixture: Error listing controller services (continuing with cleanup): {e_cs_list}")

            # 3. Purge any queued flowfiles
            purge_args = {
                "target_id": pg_id,
                "target_type": "process_group",
                "timeout_seconds": 30
            }
            try:
                purge_result_list = await call_tool(
                    client=async_client,
                    base_url=base_url,
                    tool_name="purge_flowfiles",
                    arguments=purge_args,
                    headers=mcp_headers,
                    custom_logger=global_logger
                )
                if purge_result_list and purge_result_list[0].get("status") == "success":
                    global_logger.info(f"Fixture: Successfully purged flowfiles from Process Group {pg_id}")
                else:
                    global_logger.warning(f"Fixture: Some flowfiles may remain in Process Group {pg_id}")
            except Exception as e_purge:
                global_logger.warning(f"Fixture: Error purging flowfiles (continuing with cleanup): {e_purge}")

            # 4. Delete the process group
            delete_pg_args = {
                "objects": [{
                    "object_type": "process_group", 
                    "object_id": pg_id,
                    "name": f"Test Process Group {pg_id}"
                }]
            }
            delete_pg_result_list = await call_tool(
                client=async_client,
                base_url=base_url,
                tool_name="delete_nifi_objects",
                arguments=delete_pg_args,
                headers=mcp_headers,
                custom_logger=global_logger
            )
            if delete_pg_result_list[0].get("status") == "success":
                global_logger.info(f"Fixture: Successfully deleted Test Process Group {pg_id}")
            else:
                global_logger.error(f"Fixture: Failed to delete Test Process Group {pg_id}")
        except Exception as e_del_pg:
            global_logger.error(f"Fixture: Error during Test Process Group {pg_id} cleanup: {e_del_pg}", exc_info=False) 

@pytest.fixture
async def test_merge_flow(
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any,
    root_process_group_id: str,  # <-- Add this
) -> AsyncGenerator[Dict[str, Any], None]:
    """Creates a flow with multiple inputs converging to a single processor.
    
    Flow structure:
    GenerateFlowFile1 -> UpdateAttribute1 -----> Merge
    GenerateFlowFile2 -> UpdateAttribute2 -----> Merge
    """
    components: Dict[str, Any] = {}
    
    # Create a process group
    pg_creation = {
        "name": f"mcp-test-merge-flow-{mcp_headers['X-Nifi-Server-Id']}",
        "position_x": 0,
        "position_y": 0,
        "parent_process_group_id": root_process_group_id,
    }
    
    pg_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="create_nifi_process_group",
        arguments=pg_creation,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    assert isinstance(pg_result_list, list) and len(pg_result_list) > 0
    pg_result = pg_result_list[0]
    assert pg_result.get("status") in ["success", "warning"]
    pg_id = pg_result.get("entity", {}).get("id")
    assert pg_id
    components["process_group_id"] = pg_id

    try:
        # Create first Generate processor
        gen1_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_processors",
            arguments={
                "processors": [{
                    "processor_type": "org.apache.nifi.processors.standard.GenerateFlowFile",
                    "name": "Generate Flow 1",
                    "position_x": 100,
                    "position_y": 100,
                    "properties": {
                        "Custom Text": "Data from source 1"
                    }
                }],
                "process_group_id": pg_id
            },
            headers=mcp_headers,
            custom_logger=global_logger
        )
        
        assert isinstance(gen1_result_list, list) and len(gen1_result_list) > 0
        gen1_result = gen1_result_list[0]
        assert gen1_result.get("status") in ["success", "warning"]
        gen1_id = gen1_result.get("entity", {}).get("id")
        assert gen1_id
        components["generate1"] = gen1_id

        # Create second Generate processor
        gen2_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_processors",
            arguments={
                "processors": [{
                    "processor_type": "org.apache.nifi.processors.standard.GenerateFlowFile",
                    "name": "Generate Flow 2",
                    "position_x": 100,
                    "position_y": 300,
                    "properties": {
                        "Custom Text": "Data from source 2"
                    }
                }],
                "process_group_id": pg_id
            },
            headers=mcp_headers,
            custom_logger=global_logger
        )
        
        assert isinstance(gen2_result_list, list) and len(gen2_result_list) > 0
        gen2_result = gen2_result_list[0]
        assert gen2_result.get("status") in ["success", "warning"]
        gen2_id = gen2_result.get("entity", {}).get("id")
        assert gen2_id
        components["generate2"] = gen2_id

        # Create first UpdateAttribute processor
        update1_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_processors",
            arguments={
                "processors": [{
                    "processor_type": "org.apache.nifi.processors.attributes.UpdateAttribute",
                    "name": "Update 1",
                    "position_x": 400,
                    "position_y": 100,
                    "properties": {
                        "source": "flow1"
                    }
                }],
                "process_group_id": pg_id
            },
            headers=mcp_headers,
            custom_logger=global_logger
        )
        
        assert isinstance(update1_result_list, list) and len(update1_result_list) > 0
        update1_result = update1_result_list[0]
        assert update1_result.get("status") in ["success", "warning"]
        update1_id = update1_result.get("entity", {}).get("id")
        assert update1_id
        components["update1"] = update1_id

        # Create second UpdateAttribute processor
        update2_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_processors",
            arguments={
                "processors": [{
                    "processor_type": "org.apache.nifi.processors.attributes.UpdateAttribute",
                    "name": "Update 2",
                    "position_x": 400,
                    "position_y": 300,
                    "properties": {
                        "source": "flow2"
                    }
                }],
                "process_group_id": pg_id
            },
            headers=mcp_headers,
            custom_logger=global_logger
        )
        
        assert isinstance(update2_result_list, list) and len(update2_result_list) > 0
        update2_result = update2_result_list[0]
        assert update2_result.get("status") in ["success", "warning"]
        update2_id = update2_result.get("entity", {}).get("id")
        assert update2_id
        components["update2"] = update2_id

        # Create Merge processor
        merge_result_list = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_processors",
            arguments={
                "processors": [{
                    "processor_type": "org.apache.nifi.processors.standard.MergeContent",
                    "name": "Merge Flows",
                    "position_x": 700,
                    "position_y": 200,
                    "properties": {
                        "Merge Strategy": "Defragment",
                        "Correlation Attribute Name": "source"
                    }
                }],
                "process_group_id": pg_id
            },
            headers=mcp_headers,
            custom_logger=global_logger
        )
        
        assert isinstance(merge_result_list, list) and len(merge_result_list) > 0
        merge_result = merge_result_list[0]
        assert merge_result.get("status") in ["success", "warning"]
        merge_id = merge_result.get("entity", {}).get("id")
        assert merge_id
        components["merge"] = merge_id

        # Create connections
        # Generate1 -> Update1
        await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_connections",
            arguments={"connections": [{
                "source_name": gen1_id,
                "target_name": update1_id,
                "relationships": ["success"]
            }], "process_group_id": pg_id},
            headers=mcp_headers,
            custom_logger=global_logger
        )

        # Generate2 -> Update2
        await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_connections",
            arguments={"connections": [{
                "source_name": gen2_id,
                "target_name": update2_id,
                "relationships": ["success"]
            }], "process_group_id": pg_id},
            headers=mcp_headers,
            custom_logger=global_logger
        )

        # Update1 -> Merge
        await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_connections",
            arguments={"connections": [{
                "source_name": update1_id,
                "target_name": merge_id,
                "relationships": ["success"]
            }], "process_group_id": pg_id},
            headers=mcp_headers,
            custom_logger=global_logger
        )

        # Update2 -> Merge
        await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="create_nifi_connections",
            arguments={"connections": [{
                "source_name": update2_id,
                "target_name": merge_id,
                "relationships": ["success"]
            }], "process_group_id": pg_id},
            headers=mcp_headers,
            custom_logger=global_logger
        )

        yield components

    finally:
        # Teardown: Stop, purge, and delete the process group
        global_logger.info(f"Fixture: Cleaning up test merge flow in process group: {pg_id}")
        try:
            # Stop the process group
            stop_args = {
                "object_type": "process_group",
                "object_id": pg_id,
                "operation_type": "stop"
            }
            try:
                stop_result_list = await call_tool(
                    client=async_client,
                    base_url=base_url,
                    tool_name="operate_nifi_objects",
                    arguments={"operations": [stop_args]},
                    headers=mcp_headers,
                    custom_logger=global_logger
                )
                if stop_result_list and stop_result_list[0].get("status") == "success":
                    global_logger.info(f"Fixture: Successfully stopped Process Group {pg_id}")
                else:
                    global_logger.warning(f"Fixture: Could not confirm Process Group {pg_id} stopped")
            except Exception as e_stop:
                global_logger.warning(f"Fixture: Error stopping Process Group {pg_id} (continuing with cleanup): {e_stop}")

            # Purge flowfiles
            purge_args = {
                "target_id": pg_id,
                "target_type": "process_group",
                "timeout_seconds": 30
            }
            try:
                purge_result_list = await call_tool(
                    client=async_client,
                    base_url=base_url,
                    tool_name="purge_flowfiles",
                    arguments=purge_args,
                    headers=mcp_headers,
                    custom_logger=global_logger
                )
                if purge_result_list and purge_result_list[0].get("status") == "success":
                    global_logger.info(f"Fixture: Successfully purged flowfiles from Process Group {pg_id}")
                else:
                    global_logger.warning(f"Fixture: Some flowfiles may remain in Process Group {pg_id}")
            except Exception as e_purge:
                global_logger.warning(f"Fixture: Error purging flowfiles (continuing with cleanup): {e_purge}")

            # Delete the process group
            delete_pg_args = {
                "objects": [{
                    "object_type": "process_group", 
                    "object_id": pg_id,
                    "name": f"Test Process Group {pg_id}"
                }]
            }
            delete_pg_result_list = await call_tool(
                client=async_client,
                base_url=base_url,
                tool_name="delete_nifi_objects",
                arguments=delete_pg_args,
                headers=mcp_headers,
                custom_logger=global_logger
            )
            if delete_pg_result_list[0].get("status") == "success":
                global_logger.info(f"Fixture: Successfully deleted Test Process Group {pg_id}")
            else:
                global_logger.error(f"Fixture: Failed to delete Test Process Group {pg_id}")
        except Exception as e:
            global_logger.error(f"Fixture: Error during Test Process Group {pg_id} cleanup: {e}", exc_info=False) 

 