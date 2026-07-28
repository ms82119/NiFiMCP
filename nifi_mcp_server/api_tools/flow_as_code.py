"""
Flow-as-code MCP tools: export to a named path, import/replace from JSON, structural diff.

These complement save_nifi_flow_export (timestamped snapshots) with git-friendly named paths
and NiFi upload / replace-request APIs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from mcp.server.fastmcp.exceptions import ToolError

from ..core import mcp
from ..flow_diff import DEFAULT_IGNORE_KEYS, diff_flow_documents
from ..request_context import (
    current_nifi_client,
    current_request_logger,
)
from .utils import tool_phases


def _require_client():
    nifi_client = current_nifi_client.get()
    if not nifi_client:
        raise ToolError(
            "No NiFi client available for this session. Ensure you are connected to a NiFi server "
            "(e.g. start MCP with a server or call set_nifi_server)."
        )
    return nifi_client


def _resolve_path(path: str) -> Path:
    if not path or not str(path).strip():
        raise ToolError("path is required.")
    return Path(path).expanduser().resolve()


def _load_flow_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise ToolError(f"Flow definition file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ToolError(f"Invalid JSON in {path}: {e}") from e
    except OSError as e:
        raise ToolError(f"Could not read {path}: {e}") from e
    if not isinstance(data, dict):
        raise ToolError(f"Flow definition must be a JSON object, got {type(data).__name__}")
    return data


def _suggest_group_name(flow_doc: Dict[str, Any], fallback: str = "Imported Flow") -> str:
    fc = flow_doc.get("flowContents")
    if isinstance(fc, dict) and fc.get("name"):
        return str(fc["name"])
    if flow_doc.get("name"):
        return str(flow_doc["name"])
    return fallback


@mcp.tool()
@tool_phases(["Review", "Build", "Modify"])
async def export_flow_to_path(
    path: str,
    process_group_id: Optional[str] = None,
    include_referenced_services: bool = False,
) -> Dict[str, Any]:
    """
    Download a process group's flow definition and write it to a named path (git-friendly).

    Unlike save_nifi_flow_export (timestamped under flow_exports/), this writes exactly to `path`
    so you can keep flows/<env>/<name>/flow.json under version control. Creates parent directories.
    If process_group_id is omitted, exports the NiFi root process group.
    """
    local_logger = current_request_logger.get() or logger
    nifi_client = _require_client()
    out = _resolve_path(path)

    try:
        pg_id = process_group_id or await nifi_client.get_root_process_group_id()
    except Exception as e:
        raise ToolError(f"Could not resolve process group: {e}") from e

    try:
        flow_json = await nifi_client.download_flow_definition(
            pg_id, include_referenced_services=include_referenced_services
        )
    except Exception as e:
        local_logger.error(f"Failed to download flow definition: {e}")
        raise ToolError(f"Download flow definition failed: {e}") from e

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(flow_json, f, indent=2)
            f.write("\n")
    except OSError as e:
        raise ToolError(f"Could not write flow file {out}: {e}") from e

    fc = flow_json.get("flowContents") if isinstance(flow_json, dict) else None
    pg_name = fc.get("name") if isinstance(fc, dict) else None
    return {
        "path": str(out),
        "process_group_id": pg_id,
        "process_group_name": pg_name,
        "bytes": out.stat().st_size,
        "include_referenced_services": include_referenced_services,
    }


@mcp.tool()
@tool_phases(["Build", "Modify"])
async def import_flow_definition(
    parent_process_group_id: str,
    path: str,
    group_name: Optional[str] = None,
    position_x: float = 0.0,
    position_y: float = 0.0,
) -> Dict[str, Any]:
    """
    Upload a flow definition JSON file as a new child process group under a parent.

    Uses NiFi POST .../process-groups/upload (same as UI 'Upload flow definition').
    Prefer a sandbox parent PG — do not upload onto production root without intent.
    group_name defaults to the flowContents.name from the file when omitted.
    """
    local_logger = current_request_logger.get() or logger
    nifi_client = _require_client()
    if not parent_process_group_id or not str(parent_process_group_id).strip():
        raise ToolError("parent_process_group_id is required.")

    file_path = _resolve_path(path)
    flow_doc = _load_flow_json(file_path)
    name = group_name or _suggest_group_name(flow_doc)

    try:
        created = await nifi_client.upload_process_group_from_flow_definition(
            parent_process_group_id,
            flow_doc,
            group_name=name,
            position_x=position_x,
            position_y=position_y,
        )
    except Exception as e:
        local_logger.error(f"Upload flow definition failed: {e}")
        raise ToolError(f"Import (upload) failed: {e}") from e

    component = (created or {}).get("component") or {}
    return {
        "action": "upload",
        "path": str(file_path),
        "parent_process_group_id": parent_process_group_id,
        "process_group_id": created.get("id") or component.get("id"),
        "process_group_name": component.get("name") or name,
        "revision": (created or {}).get("revision"),
    }


@mcp.tool()
@tool_phases(["Build", "Modify"])
async def replace_process_group_from_file(
    process_group_id: str,
    path: str,
    timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 1.0,
) -> Dict[str, Any]:
    """
    Replace an existing process group's contents with a flow definition JSON file.

    Uses NiFi async replace-requests (stops processors / disables controller services as needed,
    then applies the snapshot). Prefer sandbox PGs. Waits until complete (default 300s) and
    cleans up the replace request. Destructive for the target PG's current contents.
    """
    local_logger = current_request_logger.get() or logger
    nifi_client = _require_client()
    if not process_group_id or not str(process_group_id).strip():
        raise ToolError("process_group_id is required.")

    file_path = _resolve_path(path)
    flow_doc = _load_flow_json(file_path)

    try:
        result = await nifi_client.replace_process_group_from_flow_definition(
            process_group_id,
            flow_doc,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=timeout_seconds,
            cleanup_request=True,
        )
    except TimeoutError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        local_logger.error(f"Replace process group failed: {e}")
        raise ToolError(f"Replace from file failed: {e}") from e

    result = dict(result)
    result["action"] = "replace"
    result["path"] = str(file_path)
    return result


@mcp.tool()
@tool_phases(["Review", "Build", "Modify"])
async def diff_flow_json(
    path_a: str,
    path_b: str,
    ignore_positions: bool = True,
    max_changes: int = 200,
) -> Dict[str, Any]:
    """
    Structurally compare two NiFi flow definition JSON files (no NiFi connection required).

    Ignores layout noise by default (positions, style, propertyDescriptors, instanceIdentifier).
    Matches components by identifier when shared, else by type+name (connections by endpoints).
    path_a is the baseline (expected/older); path_b is the comparison (newer/live).
    Returns summary counts plus a capped list of added/removed/changed entries.
    """
    left_path = _resolve_path(path_a)
    right_path = _resolve_path(path_b)
    left = _load_flow_json(left_path)
    right = _load_flow_json(right_path)

    ignore_keys = set(DEFAULT_IGNORE_KEYS) if ignore_positions else set()
    if not ignore_positions:
        # Still drop huge descriptor blobs unless caller wants a full raw compare
        ignore_keys.add("propertyDescriptors")

    try:
        result = diff_flow_documents(
            left,
            right,
            ignore_keys=ignore_keys,
            max_changes=max(1, int(max_changes)),
        )
    except (TypeError, ValueError) as e:
        raise ToolError(f"Could not diff flow JSON: {e}") from e

    result["path_a"] = str(left_path)
    result["path_b"] = str(right_path)
    return result
