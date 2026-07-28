"""Unit tests for WP5-driven NiFiMCP improvements."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nifi_mcp_server.api_tools.creation import _create_nifi_connection_single
from nifi_mcp_server.api_tools.modification import set_nifi_process_group_parameter_context
from nifi_mcp_server.api_tools.review import (
    get_nifi_process_group_parameter_context,
    list_nifi_parameter_contexts,
)
from nifi_mcp_server.flow_documenter_improved import (
    apply_output_profile,
    apply_profile_to_documentation,
)
from nifi_mcp_server.request_context import current_nifi_client


def test_stdio_imports_flow_as_code():
    """Cursor stdio entrypoint must register flow-as-code tools."""
    import nifi_mcp_server.stdio_server as stdio_mod

    assert hasattr(stdio_mod, "flow_as_code")
    from nifi_mcp_server.api_tools import flow_as_code as fac

    for name in (
        "export_flow_to_path",
        "import_flow_definition",
        "replace_process_group_from_file",
        "diff_flow_json",
    ):
        assert hasattr(fac, name)


def test_apply_output_profile_truncates_nested_properties():
    long_script = "x" * 800
    proc = {
        "id": "p1",
        "name": "Build",
        "state": "RUNNING",
        "properties": {"Script Body": long_script, "Script Engine": "Groovy"},
        "expressions": {},
    }
    profiled = apply_output_profile(proc, "doc_optimized")
    assert "state" not in profiled  # include_status False
    assert profiled["properties"]["Script Engine"] == "Groovy"
    assert profiled["properties"]["Script Body"].startswith("x" * 500)
    assert "truncated" in profiled["properties"]["Script Body"]
    assert len(profiled["properties"]["Script Body"]) < len(long_script)


def test_apply_profile_to_documentation_summary_drops_properties():
    doc = {
        "components": {
            "processors": {
                "p1": {
                    "id": "p1",
                    "name": "A",
                    "state": "RUNNING",
                    "properties": {"a": "1"},
                    "expressions": {},
                }
            },
            "ports": {},
        }
    }
    out = apply_profile_to_documentation(doc, "summary")
    proc = out["components"]["processors"]["p1"]
    assert "properties" not in proc
    assert "expressions" not in proc


@pytest.mark.anyio
async def test_connection_rejects_unknown_relationship():
    mock_client = MagicMock()
    mock_client.get_processor_details = AsyncMock(
        side_effect=[
            {
                "id": "src",
                "component": {
                    "id": "src",
                    "name": "Fetch TS Whitelist",
                    "parentGroupId": "pg1",
                    "relationships": [
                        {"name": "hits", "autoTerminate": False},
                        {"name": "failure", "autoTerminate": False},
                        {"name": "original", "autoTerminate": True},
                    ],
                },
            },
            {
                "id": "dst",
                "component": {
                    "id": "dst",
                    "name": "Build",
                    "parentGroupId": "pg1",
                },
            },
        ]
    )
    token = current_nifi_client.set(mock_client)
    try:
        with pytest.raises(ToolError) as exc:
            await _create_nifi_connection_single(
                source_id="src",
                relationships=["no hits"],
                target_id="dst",
            )
    finally:
        current_nifi_client.reset(token)

    msg = str(exc.value)
    assert "no hits" in msg
    assert "hits" in msg
    assert "Auto-terminated" in msg


@pytest.mark.anyio
async def test_list_and_get_parameter_contexts_redact_sensitive():
    mock_client = MagicMock()
    mock_client.list_parameter_contexts = AsyncMock(
        return_value=[
            {
                "id": "pc1",
                "name": "AML_ALL_VARIABLES",
                "inherited_parameter_contexts": [],
                "parameter_count": 2,
            }
        ]
    )
    mock_client.get_process_group_parameter_context_summary = AsyncMock(
        return_value={
            "process_group_id": "pg1",
            "process_group_name": "Handler",
            "parameter_context": {"id": "pc1", "name": "AML_ALL_VARIABLES"},
            "parameters": [
                {"name": "ES_PASSWORD", "sensitive": True, "value": None},
                {"name": "hostName", "sensitive": False, "value": "example.com"},
            ],
        }
    )
    token = current_nifi_client.set(mock_client)
    try:
        listed = await list_nifi_parameter_contexts()
        got = await get_nifi_process_group_parameter_context("pg1")
    finally:
        current_nifi_client.reset(token)

    assert listed["status"] == "success"
    assert listed["parameter_contexts"][0]["name"] == "AML_ALL_VARIABLES"
    sens = [p for p in got["parameters"] if p["name"] == "ES_PASSWORD"][0]
    assert sens["sensitive"] is True
    assert sens["value"] is None


@pytest.mark.anyio
async def test_set_parameter_context_by_name():
    mock_client = MagicMock()
    mock_client.list_parameter_contexts = AsyncMock(
        return_value=[{"id": "pc-uuid", "name": "AML_ALL_VARIABLES"}]
    )
    mock_client.set_process_group_parameter_context = AsyncMock(
        return_value={
            "process_group_id": "pg1",
            "process_group_name": "Handler",
            "parameter_context": {"id": "pc-uuid", "name": "AML_ALL_VARIABLES"},
        }
    )
    token = current_nifi_client.set(mock_client)
    try:
        result = await set_nifi_process_group_parameter_context(
            process_group_id="pg1",
            parameter_context_name="AML_ALL_VARIABLES",
        )
    finally:
        current_nifi_client.reset(token)

    assert result["status"] == "success"
    mock_client.set_process_group_parameter_context.assert_called_once()
    kwargs = mock_client.set_process_group_parameter_context.call_args.kwargs
    assert kwargs["parameter_context_id"] == "pc-uuid"
