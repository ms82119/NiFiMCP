# Integration tests for analyze_nifi_processor_errors with process_group_id (PG-level mode)

import pytest
from typing import Any, Dict

from tests.utils.nifi_test_utils import call_tool


@pytest.mark.anyio
async def test_analyze_nifi_processor_errors_process_group_mode(
    async_client,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any,
    test_pg: Dict[str, Any],
):
    """Test analyze_nifi_processor_errors with process_group_id returns processor_errors list and total_processors_with_errors."""
    pg_id = test_pg["id"]
    result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="analyze_nifi_processor_errors",
        arguments={"process_group_id": pg_id, "include_suggestions": False},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert isinstance(result_list, list) and len(result_list) > 0
    result = result_list[0]
    assert result.get("status") == "success"
    assert result.get("process_group_id") == pg_id
    assert "processor_errors" in result
    assert isinstance(result["processor_errors"], list)
    assert "total_processors_with_errors" in result
    assert result["total_processors_with_errors"] == len(result["processor_errors"])
    for pe in result["processor_errors"]:
        assert "processor_id" in pe
        assert "processor_name" in pe
        assert "error_count" in pe
        assert "errors" in pe
    global_logger.info(f"PG-level error analysis for {pg_id}: {result['total_processors_with_errors']} processor(s) with errors")
