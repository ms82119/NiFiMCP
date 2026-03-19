# Tests for documentation/health out-of-scope items: boundary ports in list_nifi_objects,
# cross-PG port resolution in document_nifi_flow, recursive get_process_group_status,
# recursive document_nifi_flow.

import pytest
from typing import Any, Dict

from tests.utils.nifi_test_utils import call_tool


@pytest.mark.anyio
async def test_list_nifi_objects_process_groups_recursive_with_boundary_ports(
    async_client,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any,
):
    """list_nifi_objects with object_type=process_groups, search_scope=recursive, include_boundary_ports=True returns nodes with input_ports/output_ports."""
    result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="list_nifi_objects",
        arguments={
            "object_type": "process_groups",
            "process_group_id": None,
            "search_scope": "recursive",
            "include_boundary_ports": True,
        },
        headers=mcp_headers,
        custom_logger=global_logger,
    )
    assert isinstance(result_list, list) and len(result_list) > 0
    hierarchy = result_list[0]
    assert isinstance(hierarchy, dict)
    assert "id" in hierarchy or "child_process_groups" in hierarchy
    assert "input_ports" in hierarchy, "Root node should have input_ports when include_boundary_ports=True"
    assert "output_ports" in hierarchy, "Root node should have output_ports when include_boundary_ports=True"
    assert isinstance(hierarchy.get("input_ports"), list)
    assert isinstance(hierarchy.get("output_ports"), list)
    global_logger.info("list_nifi_objects(process_groups, recursive, include_boundary_ports=True) returned hierarchy with boundary ports")


@pytest.mark.anyio
async def test_get_process_group_status_include_child_groups(
    async_client,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any,
    test_pg: Dict[str, Any],
):
    """get_process_group_status with include_child_groups=True returns child_groups and child_health_summary."""
    pg_id = test_pg["id"]
    result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="get_process_group_status",
        arguments={
            "process_group_id": pg_id,
            "include_bulletins": False,
            "include_child_groups": True,
            "max_depth": 3,
        },
        headers=mcp_headers,
        custom_logger=global_logger,
    )
    assert isinstance(result_list, list) and len(result_list) > 0
    status = result_list[0]
    assert status.get("process_group_id") == pg_id
    assert "child_groups" in status
    assert isinstance(status["child_groups"], list)
    assert "child_health_summary" in status
    summary = status["child_health_summary"]
    assert "healthy" in summary and "errors" in summary and "degraded" in summary
    global_logger.info(f"get_process_group_status(include_child_groups=True) returned {len(status['child_groups'])} child groups")


@pytest.mark.anyio
async def test_document_nifi_flow_include_child_groups(
    async_client,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any,
    test_pg: Dict[str, Any],
):
    """document_nifi_flow with include_child_groups=True returns process_group_id, process_group_name, documentation, child_groups."""
    pg_id = test_pg["id"]
    result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="document_nifi_flow",
        arguments={
            "process_group_id": pg_id,
            "include_flow_summary": False,
            "include_child_groups": True,
            "max_depth": 2,
        },
        headers=mcp_headers,
        custom_logger=global_logger,
    )
    assert isinstance(result_list, list) and len(result_list) > 0
    doc_result = result_list[0]
    assert doc_result.get("status") == "success"
    assert "process_group_id" in doc_result
    assert "process_group_name" in doc_result
    assert "documentation" in doc_result
    assert "child_groups" in doc_result
    assert doc_result["process_group_id"] == pg_id
    assert isinstance(doc_result["child_groups"], list)
    for child in doc_result["child_groups"]:
        assert "process_group_id" in child
        assert "process_group_name" in child
        assert "documentation" in child
        assert "child_groups" in child
    global_logger.info(f"document_nifi_flow(include_child_groups=True) returned {len(doc_result['child_groups'])} child groups")
