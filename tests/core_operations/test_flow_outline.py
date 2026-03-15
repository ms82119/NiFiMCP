# Tests for get_flow_outline tool (NiFi documentation and health tools plan)

import pytest
from typing import Any, Dict

from tests.utils.nifi_test_utils import call_tool


@pytest.mark.anyio
async def test_get_flow_outline_returns_outline(
    async_client,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any,
):
    """Test that get_flow_outline returns outline with id, name, depth, counts, child_process_groups."""
    result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="get_flow_outline",
        arguments={"process_group_id": None, "include_boundary_ports": True},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert isinstance(result_list, list) and len(result_list) > 0
    result = result_list[0]
    assert "process_group_id" in result
    assert "process_group_name" in result
    assert "outline" in result
    assert "completed" in result
    assert "timeout_occurred" in result
    outline = result["outline"]
    assert "id" in outline
    assert "name" in outline
    assert "depth" in outline
    assert outline["depth"] == 0, "Root outline node should have depth 0"
    assert "counts" in outline
    assert "child_process_groups" in outline
    assert isinstance(outline["child_process_groups"], list)
    assert "input_ports" in outline
    assert "output_ports" in outline
    global_logger.info(f"get_flow_outline returned outline for PG {result.get('process_group_id')} with {len(outline.get('child_process_groups', []))} child groups")


@pytest.mark.anyio
async def test_get_flow_outline_with_pg_id(
    async_client,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any,
    test_pg: Dict[str, Any],
):
    """Test get_flow_outline with explicit process_group_id."""
    pg_id = test_pg["id"]
    result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="get_flow_outline",
        arguments={"process_group_id": pg_id, "include_boundary_ports": False},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert isinstance(result_list, list) and len(result_list) > 0
    result = result_list[0]
    assert result.get("process_group_id") == pg_id
    assert result["outline"]["id"] == pg_id
    assert result["outline"]["depth"] == 0
    assert "counts" in result["outline"]
