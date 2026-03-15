"""
MCP-only control tools: session info (server, phase, tokens), server switch, and phase restriction.

These tools are hidden from the web UI (REST GET /tools filters them out).
They are intended for pure MCP clients (e.g. Cursor).
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Dict, Any

from loguru import logger

from ..core import mcp
from ..request_context import (
    current_nifi_client,
    current_nifi_server_id,
    current_nifi_phase,
    current_request_logger,
    mcp_session_started_at,
    session_total_tokens_in,
    session_total_tokens_out,
)
from .utils import tool_phases, VALID_PHASES
from mcp.server.fastmcp.exceptions import ToolError

from config.settings import get_nifi_servers, get_nifi_server_config


def _default_server_id() -> str | None:
    """Resolve default NiFi server id from env or config (same logic as stdio_server)."""
    env_id = os.environ.get("NIFI_SERVER_ID", "").strip()
    if env_id:
        return env_id
    servers = get_nifi_servers()
    if not servers:
        return None
    return servers[0].get("id")


def _format_connection_started_ago(ts: float) -> str:
    """Human-readable 'Xm ago' / 'Xs ago' from time.time() - ts."""
    diff = time.time() - ts
    if diff < 60:
        return f"{int(diff)}s ago"
    if diff < 3600:
        return f"{int(diff // 60)}m ago"
    if diff < 86400:
        return f"{int(diff // 3600)}h ago"
    return f"{int(diff // 86400)}d ago"


@mcp.tool()
@tool_phases(["Control"], mcp_only=True)
async def get_nifi_session_info() -> Dict[str, Any]:
    """
    Return session info: current NiFi server (id/name), phase, connection start time, and rough token estimates.

    In stdio (e.g. Cursor), server is the one selected at startup or after set_nifi_server.
    Token counts are cumulative rough estimates (len(json.dumps(...))/4) for tool input/output in this session.
    """
    local_logger = current_request_logger.get() or logger
    server_id = current_nifi_server_id.get()
    if not server_id:
        server_id = _default_server_id()
    if not server_id:
        raise ToolError("No NiFi server is configured. Add servers to config.yaml (nifi.servers).")
    conf = get_nifi_server_config(server_id)
    server_name = (conf or {}).get("name", server_id)

    phase_val = current_nifi_phase.get()
    phase = phase_val if phase_val else "All"

    started_ts = mcp_session_started_at.get()
    connection_started_at = None
    connection_started_ago = None
    if started_ts is not None:
        connection_started_at = datetime.utcfromtimestamp(started_ts).isoformat() + "Z"
        connection_started_ago = _format_connection_started_ago(started_ts)

    total_tokens_in = session_total_tokens_in.get()
    total_tokens_out = session_total_tokens_out.get()

    local_logger.debug(
        f"get_nifi_session_info: {server_id} / {server_name} phase={phase} "
        f"tokens_in={total_tokens_in} tokens_out={total_tokens_out}"
    )
    return {
        "server_id": server_id,
        "server_name": server_name,
        "phase": phase,
        "connection_started_at": connection_started_at,
        "connection_started_ago": connection_started_ago,
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
    }


@mcp.tool()
@tool_phases(["Control"], mcp_only=True)
async def set_nifi_server(server_id: str) -> Dict[str, Any]:
    """
    Switch the NiFi server used for subsequent tool calls in this MCP session.

    server_id must be one of the configured server IDs from config.yaml (nifi.servers).
    Only valid in stdio/MCP; the web UI uses a per-request header instead.
    """
    local_logger = current_request_logger.get() or logger
    servers = get_nifi_servers()
    if not servers:
        raise ToolError("No NiFi servers configured in config.yaml (nifi.servers).")
    valid_ids = [s.get("id") for s in servers if s.get("id")]
    if server_id not in valid_ids:
        raise ToolError(
            f"Invalid server_id '{server_id}'. Valid server IDs from config: {', '.join(valid_ids)}."
        )
    from ..core import get_nifi_client

    old_client = current_nifi_client.get()
    if old_client:
        try:
            await old_client.close()
        except Exception as e:
            local_logger.warning(f"Error closing previous NiFi client: {e}")

    nifi_client = await get_nifi_client(server_id, bound_logger=local_logger)
    current_nifi_client.set(nifi_client)
    current_nifi_server_id.set(server_id)
    conf = get_nifi_server_config(server_id)
    server_name = (conf or {}).get("name", server_id)
    local_logger.info(f"Switched NiFi server to {server_id} ({server_name})")
    return {"status": "success", "server_id": server_id, "server_name": server_name}


@mcp.tool()
@tool_phases(["Control"], mcp_only=True)
async def set_nifi_phase(phase: str) -> Dict[str, Any]:
    """
    Set the current phase restriction for tool execution.

    Only tools whose phases include the current phase can run; others are blocked with a clear error.
    Use phase='All' or empty to clear the restriction.

    Allowed values: Review, Build, Modify, Operate, All.
    """
    local_logger = current_request_logger.get() or logger
    if phase is not None:
        phase = str(phase).strip()
    if not phase or phase.lower() == "all":
        current_nifi_phase.set(None)
        local_logger.info("Phase restriction cleared (All).")
        return {"status": "success", "phase": None, "message": "Phase restriction cleared."}
    allowed_lower = [p.lower() for p in VALID_PHASES if p != "All"]
    if phase.lower() not in allowed_lower:
        raise ToolError(
            f"Invalid phase '{phase}'. Allowed values: {', '.join(VALID_PHASES)}."
        )
    current_nifi_phase.set(phase)
    local_logger.info(f"Phase set to '{phase}'.")
    return {"status": "success", "phase": phase}
