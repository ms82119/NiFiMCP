"""Unit tests for flow-as-code MCP tools (export / import / replace / diff)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

import nifi_mcp_server.api_tools.flow_as_code  # noqa: F401

from nifi_mcp_server.api_tools.flow_as_code import (
    diff_flow_json,
    export_flow_to_path,
    import_flow_definition,
    replace_process_group_from_file,
)
from nifi_mcp_server.request_context import current_nifi_client


@pytest.mark.anyio
async def test_export_flow_to_path_writes_named_file(tmp_path):
    mock_client = MagicMock()
    mock_client.get_root_process_group_id = AsyncMock(return_value="root-1")
    mock_client.download_flow_definition = AsyncMock(
        return_value={"flowContents": {"name": "Root", "processors": []}}
    )
    out = tmp_path / "flows" / "uat" / "flow.json"
    token = current_nifi_client.set(mock_client)
    try:
        result = await export_flow_to_path(path=str(out))
    finally:
        current_nifi_client.reset(token)

    assert result["process_group_id"] == "root-1"
    assert result["process_group_name"] == "Root"
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["flowContents"]["name"] == "Root"
    mock_client.download_flow_definition.assert_called_once_with(
        "root-1", include_referenced_services=False
    )


@pytest.mark.anyio
async def test_export_flow_to_path_uses_explicit_pg(tmp_path):
    mock_client = MagicMock()
    mock_client.download_flow_definition = AsyncMock(
        return_value={"flowContents": {"name": "Child"}}
    )
    out = tmp_path / "child.json"
    token = current_nifi_client.set(mock_client)
    try:
        result = await export_flow_to_path(path=str(out), process_group_id="pg-99")
    finally:
        current_nifi_client.reset(token)

    assert result["process_group_id"] == "pg-99"
    mock_client.get_root_process_group_id.assert_not_called()
    mock_client.download_flow_definition.assert_called_once_with(
        "pg-99", include_referenced_services=False
    )


@pytest.mark.anyio
async def test_import_flow_definition_uploads(tmp_path):
    flow_file = tmp_path / "flow.json"
    flow_file.write_text(
        json.dumps({"flowContents": {"name": "FromFile", "processors": []}}),
        encoding="utf-8",
    )
    mock_client = MagicMock()
    mock_client.upload_process_group_from_flow_definition = AsyncMock(
        return_value={
            "id": "new-pg",
            "component": {"id": "new-pg", "name": "FromFile"},
            "revision": {"version": 0},
        }
    )
    token = current_nifi_client.set(mock_client)
    try:
        result = await import_flow_definition(
            parent_process_group_id="parent-1",
            path=str(flow_file),
        )
    finally:
        current_nifi_client.reset(token)

    assert result["action"] == "upload"
    assert result["process_group_id"] == "new-pg"
    assert result["process_group_name"] == "FromFile"
    mock_client.upload_process_group_from_flow_definition.assert_called_once()
    args, kwargs = mock_client.upload_process_group_from_flow_definition.call_args
    assert args[0] == "parent-1"
    assert kwargs.get("group_name") == "FromFile" or args[2] == "FromFile"


@pytest.mark.anyio
async def test_replace_process_group_from_file(tmp_path):
    flow_file = tmp_path / "flow.json"
    flow_file.write_text(json.dumps({"flowContents": {"name": "X"}}), encoding="utf-8")
    mock_client = MagicMock()
    mock_client.replace_process_group_from_flow_definition = AsyncMock(
        return_value={
            "process_group_id": "pg-1",
            "request_id": "req-1",
            "complete": True,
            "state": "Complete",
        }
    )
    token = current_nifi_client.set(mock_client)
    try:
        result = await replace_process_group_from_file(
            process_group_id="pg-1", path=str(flow_file)
        )
    finally:
        current_nifi_client.reset(token)

    assert result["action"] == "replace"
    assert result["request_id"] == "req-1"
    assert result["path"] == str(flow_file.resolve())


@pytest.mark.anyio
async def test_diff_flow_json_tool(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    base = {
        "flowContents": {
            "name": "Root",
            "processors": [
                {
                    "identifier": "p1",
                    "name": "Log",
                    "type": "org.apache.nifi.processors.standard.LogMessage",
                    "componentType": "PROCESSOR",
                    "position": {"x": 0, "y": 0},
                    "properties": {"logLevel": "INFO"},
                }
            ],
            "connections": [],
            "controllerServices": [],
            "inputPorts": [],
            "outputPorts": [],
            "funnels": [],
            "labels": [],
            "processGroups": [],
            "remoteProcessGroups": [],
        }
    }
    other = json.loads(json.dumps(base))
    other["flowContents"]["processors"][0]["properties"]["logLevel"] = "ERROR"
    other["flowContents"]["processors"][0]["position"] = {"x": 100, "y": 100}
    a.write_text(json.dumps(base), encoding="utf-8")
    b.write_text(json.dumps(other), encoding="utf-8")

    result = await diff_flow_json(path_a=str(a), path_b=str(b))
    assert result["identical"] is False
    assert result["summary"]["changed"] == 1


@pytest.mark.anyio
async def test_tools_require_client():
    token = current_nifi_client.set(None)
    try:
        with pytest.raises(ToolError, match="No NiFi client"):
            await export_flow_to_path(path="/tmp/x.json")
    finally:
        current_nifi_client.reset(token)
