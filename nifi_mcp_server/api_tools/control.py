"""
MCP-only control tools: session info (server, phase, tokens), server switch, and phase restriction.

These tools are hidden from the web UI (REST GET /tools filters them out).
They are intended for pure MCP clients (e.g. Cursor).
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

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
    get_selected_nifi_server_id,
    set_selected_nifi_server_id,
)
from .utils import tool_phases, VALID_PHASES
from mcp.server.fastmcp.exceptions import ToolError

from config.settings import get_nifi_servers, get_nifi_server_config, get_flow_export_directory


def _default_server_id() -> str | None:
    """Resolve default NiFi server id from env or config (same logic as stdio_server)."""
    env_id = os.environ.get("NIFI_SERVER_ID", "").strip()
    if env_id:
        return env_id
    servers = get_nifi_servers()
    if not servers:
        return None
    return servers[0].get("id")


def _sanitize_server_id_for_filename(server_id: str) -> str:
    """Sanitize server ID for use in filenames (spaces/slashes -> underscore)."""
    if not server_id:
        return "nifi"
    return re.sub(r"[\s/\\]+", "_", server_id).strip("_") or "nifi"


@mcp.tool()
@tool_phases(["Review", "Build", "Modify"])
async def save_nifi_flow_export(include_referenced_services: bool = False) -> Dict[str, Any]:
    """
    Save the root NiFi flow as a JSON file (same as UI 'Download flow definition' without external services).

    Writes to the directory configured in config (nifi.flow_export_directory). Filename format:
    {server_id}_{YYYYMMDD-HHMMSS}.json (server_id sanitized for filesystem). Use get_nifi_session_info
    to see the export directory and list of saved snapshots for the current server.
    """
    local_logger = current_request_logger.get() or logger
    server_id = current_nifi_server_id.get()
    if not server_id:
        server_id = _default_server_id()
    if not server_id:
        raise ToolError("No NiFi server is configured. Add servers to config.yaml (nifi.servers).")
    nifi_client = current_nifi_client.get()
    if not nifi_client:
        raise ToolError(
            "No NiFi client available for this session. Ensure you are connected to a NiFi server "
            "(e.g. start MCP with a server or call set_nifi_server)."
        )
    try:
        root_id = await nifi_client.get_root_process_group_id()
    except Exception as e:
        local_logger.error(f"Failed to get root process group: {e}")
        raise ToolError(f"Could not get root process group: {e}. Check NiFi connectivity.") from e
    try:
        flow_json = await nifi_client.download_flow_definition(root_id, include_referenced_services=include_referenced_services)
    except Exception as e:
        local_logger.error(f"Failed to download flow definition: {e}")
        raise ToolError(f"Download flow definition failed: {e}. Check NiFi version supports /process-groups/{{id}}/download.") from e
    export_dir = get_flow_export_directory()
    Path(export_dir).mkdir(parents=True, exist_ok=True)
    prefix = _sanitize_server_id_for_filename(server_id)
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    filename = f"{prefix}_{timestamp}.json"
    filepath = Path(export_dir) / filename
    try:
        with open(filepath, "w") as f:
            json.dump(flow_json, f, indent=2)
    except OSError as e:
        local_logger.error(f"Failed to write flow export: {e}")
        raise ToolError(f"Could not write export file: {e}. Check flow_export_directory is writable.") from e
    exported_at = now.isoformat()
    conf = get_nifi_server_config(server_id)
    server_name = (conf or {}).get("name", server_id)
    return {
        "path": str(filepath.resolve()),
        "filename": filename,
        "server_id": server_id,
        "server_name": server_name,
        "exported_at": exported_at,
    }


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
    server_id = get_selected_nifi_server_id()
    if not server_id:
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

    flow_export_directory = get_flow_export_directory()
    saved_flow_snapshots: List[Dict[str, Any]] = []
    export_path = Path(flow_export_directory)
    if export_path.exists() and export_path.is_dir():
        prefix = _sanitize_server_id_for_filename(server_id)
        pattern = f"{prefix}_*.json"
        try:
            matches = sorted(export_path.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            for p in matches:
                try:
                    mtime = p.stat().st_mtime
                    modified = datetime.utcfromtimestamp(mtime).isoformat() + "Z"
                except OSError:
                    modified = None
                saved_flow_snapshots.append({
                    "filename": p.name,
                    "path": str(p.resolve()),
                    "modified": modified,
                })
        except OSError:
            pass

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
        "flow_export_directory": flow_export_directory,
        "saved_flow_snapshots": saved_flow_snapshots,
    }


@mcp.tool()
@tool_phases(["Control"], mcp_only=True)
async def list_nifi_servers() -> Dict[str, Any]:
    """
    List configured NiFi servers from config.yaml without connecting to any server.

    Useful when you want to know which server IDs are available before calling set_nifi_server().
    """
    servers = get_nifi_servers()
    # Intentionally exclude username/password.
    result = []
    for s in servers:
        if not s:
            continue
        result.append(
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "url": s.get("url"),
                "tls_verify": s.get("tls_verify", True),
            }
        )
    return {"servers": result}


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

    from ..core import create_nifi_client

    # Persist server selection across tool calls by updating shared state.
    set_selected_nifi_server_id(server_id)
    current_nifi_server_id.set(server_id)

    # Reconfigure the existing NiFi client instance in-place so future tool calls
    # (which may run in different async contexts) still use the updated server.
    old_client = current_nifi_client.get()
    new_client = await create_nifi_client(server_id, bound_logger=local_logger)

    if old_client:
        try:
            await old_client.close()
        except Exception as e:
            local_logger.warning(f"Error closing previous NiFi client: {e}")

        old_client.base_url = new_client.base_url
        old_client.username = new_client.username
        old_client.password = new_client.password
        old_client.tls_verify = new_client.tls_verify
        old_client.credential_callback = new_client.credential_callback
        old_client._server_id = server_id
        old_client._token = None
        old_client._token_from_oidc = False
        old_client._auth_config = None
        old_client._client = None
    else:
        # Fallback: if there's no client in the current context yet,
        # set the new client here. Subsequent calls should still consult
        # the same object reference most transports capture at startup.
        current_nifi_client.set(new_client)

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
