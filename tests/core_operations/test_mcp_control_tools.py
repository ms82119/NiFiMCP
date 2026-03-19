"""Integration tests for MCP-only control tools and phase enforcement.

- GET /tools must not include MCP-only tools (get_nifi_session_info, set_nifi_server, set_nifi_phase).
- POST /tools/{name} with X-Nifi-Phase header must return 403 when the tool's phases do not include that phase.
"""

import pytest
import httpx


@pytest.mark.anyio
async def test_get_tools_excludes_mcp_only_tools(
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
):
    """GET /tools must not list MCP-only control tools (web UI must not see them)."""
    resp = await async_client.get(f"{base_url}/tools", headers=mcp_headers, timeout=10.0)
    resp.raise_for_status()
    tools = resp.json()
    assert isinstance(tools, list)
    tool_names = []
    for t in tools:
        if isinstance(t, dict) and t.get("type") == "function":
            name = t.get("function", {}).get("name")
            if name:
                tool_names.append(name)
    mcp_only = {"get_nifi_session_info", "set_nifi_server", "set_nifi_phase"}
    for name in mcp_only:
        assert name not in tool_names, f"MCP-only tool '{name}' must not appear in GET /tools"


@pytest.mark.anyio
async def test_execute_tool_phase_restriction_returns_403(
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: dict,
):
    """POST with X-Nifi-Phase: Review to an Operate-only tool must return 403."""
    headers = {**mcp_headers, "X-Nifi-Phase": "Review"}
    # operate_nifi_objects is Operate-only; phase Review should block it
    resp = await async_client.post(
        f"{base_url}/tools/operate_nifi_objects",
        json={"arguments": {"operations": []}},
        headers=headers,
        timeout=10.0,
    )
    assert resp.status_code == 403
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    detail = data.get("detail", resp.text)
    assert "Phase" in detail or "phase" in detail
