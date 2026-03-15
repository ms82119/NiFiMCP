# Tests for NiFi Process Group operations 

import pytest
import httpx
from typing import Any, Dict
import anyio

from tests.utils.nifi_test_utils import call_tool, get_test_run_id

@pytest.mark.anyio
async def test_create_process_group(
    test_pg: Dict[str, Any], # Fixture provides the created PG details
    async_client: httpx.AsyncClient, # To make calls if needed
    base_url: str, # For call_tool
    mcp_headers: Dict[str, str], # For call_tool
    global_logger: Any # For logging
):
    """Test that the test_pg fixture creates a process group successfully."""
    global_logger.info(f"Test: test_create_process_group received PG details: {test_pg}")
    assert test_pg is not None, "test_pg fixture did not return Process Group details."
    assert isinstance(test_pg, dict) and "id" in test_pg and "name" in test_pg, \
        "Process Group details from fixture are invalid (missing id or name)."

    # The fixture already creates and verifies. This test mostly ensures the fixture works as expected.
    # Optionally, re-verify details here if an extra layer of checking is desired for this specific test.
    pg_id = test_pg["id"]
    pg_name = test_pg["name"]
    global_logger.info(f"Test: Verifying details for PG ID: {pg_id} with name '{pg_name}' from fixture.")

    # Use get_nifi_object_details tool to verify the PG details
    get_pg_args = {
        "object_type": "process_group",
        "object_id": pg_id
    }
    pg_results_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="get_nifi_object_details",
        arguments=get_pg_args,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert isinstance(pg_results_list, list) and len(pg_results_list) > 0, "PG details should be returned"
    component_details = pg_results_list[0]

    assert component_details is not None, f"Component details not found for PG {pg_id}."
    assert component_details.get("id") == pg_id, "Retrieved PG ID does not match the fixture-created PG ID."
    assert component_details.get("component", {}).get("name") == pg_name, "Retrieved PG name does not match fixture."
    global_logger.info(f"Test: Successfully verified PG {pg_id} with name '{component_details.get('component', {}).get('name')}'.")

@pytest.mark.anyio
async def test_get_process_group_status(
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any,
    test_pg: Dict[str, Any], # Fixture provides the created PG details
):
    """Tests getting the status of a Process Group."""
    pg_id = test_pg["id"]
    pg_name = test_pg["name"]
    global_logger.info(f"Test: Getting status for Process Group {pg_id} ('{pg_name}')")

    # Use get_process_group_status tool
    status_args = {
        "process_group_id": pg_id
    }
    status_results_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="get_process_group_status",
        arguments=status_args,
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert isinstance(status_results_list, list) and len(status_results_list) > 0, "Status info should be returned"
    status_info = status_results_list[0]

    assert status_info is not None, "Status info should not be None"
    assert status_info.get("process_group_id") == pg_id, "Status info ID should match requested PG ID"
    assert status_info.get("process_group_name") == pg_name, f"PG status name should be {pg_name}"
    # Check for some basic status fields
    assert "component_summary" in status_info, "PG status should have component_summary"
    # Health verdict and bulletin summary (from documentation/health tools plan)
    assert "health" in status_info, "PG status should have health verdict"
    assert status_info["health"] in ("healthy", "errors", "degraded"), "health should be one of healthy, errors, degraded"
    global_logger.info(f"Test: Successfully retrieved status for PG {pg_id}. Name: {status_info.get('process_group_name')}, Component Summary: {status_info.get('component_summary')}")


@pytest.mark.anyio
async def test_get_process_group_status_includes_bulletin_summary(
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any,
    test_pg: Dict[str, Any],
):
    """Test that get_process_group_status with include_bulletins=True returns bulletin_summary."""
    pg_id = test_pg["id"]
    status_results_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="get_process_group_status",
        arguments={"process_group_id": pg_id, "include_bulletins": True},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert isinstance(status_results_list, list) and len(status_results_list) > 0
    status_info = status_results_list[0]
    assert status_info.get("process_group_id") == pg_id
    assert "bulletin_summary" in status_info, "Status should include bulletin_summary when include_bulletins=True"
    summary = status_info.get("bulletin_summary")
    if summary is not None:
        assert "count_by_level" in summary, "bulletin_summary should have count_by_level"
        assert "last_errors" in summary, "bulletin_summary should have last_errors"

# Add other PG related tests here, e.g., test_get_process_group_status, test_operate_process_group 