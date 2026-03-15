"""Unit tests for save_nifi_flow_export and flow export directory/snapshot listing."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure control module is loaded so tools are registered
import nifi_mcp_server.api_tools.control  # noqa: F401

from nifi_mcp_server.api_tools.control import save_nifi_flow_export
from nifi_mcp_server.request_context import current_nifi_client, current_nifi_server_id
from mcp.server.fastmcp.exceptions import ToolError


@pytest.mark.anyio
async def test_save_nifi_flow_export_writes_file_and_returns_summary(tmp_path):
    """save_nifi_flow_export builds correct filename, writes JSON, returns path/filename/server/exported_at."""
    mock_client = MagicMock()
    mock_client.get_root_process_group_id = AsyncMock(return_value="root-id-123")
    mock_client.download_flow_definition = AsyncMock(return_value={"flow": "data"})

    server_token = current_nifi_server_id.set("my-server")
    client_token = current_nifi_client.set(mock_client)
    try:
        with patch("nifi_mcp_server.api_tools.control.get_flow_export_directory", return_value=str(tmp_path)):
            with patch("nifi_mcp_server.api_tools.control.get_nifi_server_config") as m_conf:
                m_conf.return_value = {"name": "My NiFi"}
                result = await save_nifi_flow_export(include_referenced_services=False)
    finally:
        current_nifi_server_id.reset(server_token)
        current_nifi_client.reset(client_token)

    assert "path" in result
    assert "filename" in result
    assert result["filename"].startswith("my-server_")
    assert result["filename"].endswith(".json")
    assert "server_id" in result and result["server_id"] == "my-server"
    assert "server_name" in result and result["server_name"] == "My NiFi"
    assert "exported_at" in result
    mock_client.download_flow_definition.assert_called_once_with("root-id-123", include_referenced_services=False)
    filepath = tmp_path / result["filename"]
    assert filepath.exists()
    content = filepath.read_text()
    assert '"flow": "data"' in content


@pytest.mark.anyio
async def test_save_nifi_flow_export_raises_when_no_client():
    """save_nifi_flow_export raises ToolError when current_nifi_client is None."""
    server_token = current_nifi_server_id.set("some-server")
    client_token = current_nifi_client.set(None)
    try:
        with pytest.raises(ToolError) as exc_info:
            await save_nifi_flow_export()
        assert "No NiFi client" in str(exc_info.value)
    finally:
        current_nifi_server_id.reset(server_token)
        current_nifi_client.reset(client_token)


@pytest.mark.anyio
async def test_save_nifi_flow_export_raises_when_no_server():
    """save_nifi_flow_export raises ToolError when no server is configured."""
    server_token = current_nifi_server_id.set(None)
    try:
        with patch("nifi_mcp_server.api_tools.control._default_server_id", return_value=None):
            with pytest.raises(ToolError) as exc_info:
                await save_nifi_flow_export()
            assert "No NiFi server" in str(exc_info.value)
    finally:
        current_nifi_server_id.reset(server_token)
