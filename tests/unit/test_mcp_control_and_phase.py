"""Unit tests for MCP-only tool registry and phase enforcement."""

import pytest

# Ensure control tools are registered so _mcp_only_tool_names is populated
import nifi_mcp_server.api_tools.control  # noqa: F401

from nifi_mcp_server.api_tools.control import get_nifi_session_info
from nifi_mcp_server.api_tools.utils import (
    is_mcp_only_tool,
    CONTROL_TOOL_NAMES,
    VALID_PHASES,
    tool_phases,
    _estimate_tokens,
    _accumulate_session_tokens,
)
from nifi_mcp_server.request_context import (
    current_nifi_phase,
    current_nifi_server_id,
    mcp_session_started_at,
    session_total_tokens_in,
    session_total_tokens_out,
)
from mcp.server.fastmcp.exceptions import ToolError


class TestMcpOnlyAndPhaseRegistry:
    """Test is_mcp_only_tool and control tool names."""

    def test_control_tool_names_defined(self):
        assert CONTROL_TOOL_NAMES == {
            "get_nifi_session_info",
            "set_nifi_server",
            "set_nifi_phase",
        }

    def test_valid_phases_contains_expected(self):
        assert "Review" in VALID_PHASES
        assert "Build" in VALID_PHASES
        assert "Modify" in VALID_PHASES
        assert "Operate" in VALID_PHASES
        assert "All" in VALID_PHASES

    def test_is_mcp_only_true_for_control_tools(self):
        assert is_mcp_only_tool("get_nifi_session_info") is True
        assert is_mcp_only_tool("set_nifi_server") is True
        assert is_mcp_only_tool("set_nifi_phase") is True

    def test_is_mcp_only_false_for_normal_tools(self):
        assert is_mcp_only_tool("list_nifi_objects") is False
        assert is_mcp_only_tool("get_process_group_status") is False
        assert is_mcp_only_tool("nonexistent_tool") is False


class TestPhaseEnforcementWrapper:
    """Test that tool_phases wrapper blocks when phase does not match."""

    @pytest.mark.anyio
    async def test_phase_blocks_when_not_allowed(self):
        @tool_phases(["Operate"])
        async def only_operate_tool() -> str:
            return "ok"

        token = current_nifi_phase.set("Review")
        try:
            with pytest.raises(ToolError) as exc_info:
                await only_operate_tool()
            assert "Phase is set to 'Review'" in str(exc_info.value)
            assert "not allowed in this phase" in str(exc_info.value)
        finally:
            current_nifi_phase.reset(token)

    @pytest.mark.anyio
    async def test_phase_allows_when_matching(self):
        @tool_phases(["Review", "Build"])
        async def review_build_tool() -> str:
            return "ok"

        token = current_nifi_phase.set("Review")
        try:
            result = await review_build_tool()
            assert result == "ok"
        finally:
            current_nifi_phase.reset(token)

    @pytest.mark.anyio
    async def test_phase_cleared_allows_any(self):
        @tool_phases(["Operate"])
        async def only_operate_tool() -> str:
            return "ok"

        assert current_nifi_phase.get() is None or str(current_nifi_phase.get()).lower() == "all"
        result = await only_operate_tool()
        assert result == "ok"

    @pytest.mark.anyio
    async def test_phase_all_allows_any(self):
        @tool_phases(["Operate"])
        async def only_operate_tool() -> str:
            return "ok"

        token = current_nifi_phase.set("All")
        try:
            result = await only_operate_tool()
            assert result == "ok"
        finally:
            current_nifi_phase.reset(token)


class TestSessionInfoAndTokens:
    """Test get_nifi_session_info return shape and token estimation."""

    def test_estimate_tokens_returns_positive_int(self):
        assert _estimate_tokens({"a": 1}) >= 0
        assert _estimate_tokens("hello") >= 0
        assert _estimate_tokens([]) == 0

    def test_accumulate_session_tokens_updates_context(self):
        session_total_tokens_in.set(10)
        session_total_tokens_out.set(20)
        _accumulate_session_tokens((1,), {"x": 2}, {"result": "ok"})
        assert session_total_tokens_in.get() >= 10
        assert session_total_tokens_out.get() >= 20
        # Reset for other tests
        session_total_tokens_in.set(0)
        session_total_tokens_out.set(0)

    @pytest.mark.anyio
    async def test_get_nifi_session_info_returns_expected_keys(self):
        """get_nifi_session_info returns server_id, server_name, phase, connection_started_at, total_tokens_in, total_tokens_out."""
        from unittest.mock import patch

        server_token = current_nifi_server_id.set("test-server")
        started_token = mcp_session_started_at.set(1000000.0)
        try:
            with patch("nifi_mcp_server.api_tools.control.get_nifi_server_config") as m:
                m.return_value = {"name": "Test NiFi"}
                out = await get_nifi_session_info()
        finally:
            current_nifi_server_id.reset(server_token)
            mcp_session_started_at.reset(started_token)
        expected_keys = {
            "server_id", "server_name", "phase", "connection_started_at",
            "connection_started_ago", "total_tokens_in", "total_tokens_out",
        }
        for k in expected_keys:
            assert k in out, f"Missing key: {k}"
        assert out["server_id"] == "test-server"
        assert out["server_name"] == "Test NiFi"
        assert out["total_tokens_in"] >= 0
        assert out["total_tokens_out"] >= 0
