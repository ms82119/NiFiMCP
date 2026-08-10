"""MCP tools for NiFi canvas labels (documentation annotations on the flow canvas)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger
from mcp.server.fastmcp.exceptions import ToolError

from ..core import mcp
from ..label_utils import extract_label_key, plain_label_text, strip_label_markers
from ..nifi_client import NiFiClient
from ..request_context import current_nifi_client, current_request_logger
from .utils import tool_phases

DEFAULT_LABEL_WIDTH = 420.0
DEFAULT_LABEL_HEIGHT = 90.0


def _resolve_position(
    label: Dict[str, Any], *, slot: int = 0, origin_x: float = 0, origin_y: float = 0, spacing_y: float = 120
) -> Dict[str, float]:
    position_x = label.get("position_x")
    position_y = label.get("position_y")
    if position_x is None or position_y is None:
        position = label.get("position", {})
        if isinstance(position, dict):
            position_x = position_x if position_x is not None else position.get("x")
            position_y = position_y if position_y is not None else position.get("y")
    if position_x is None:
        position_x = origin_x
    if position_y is None:
        position_y = origin_y + slot * spacing_y
    return {"x": float(position_x), "y": float(position_y)}


def _index_labels_by_key(entities: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}
    for ent in entities:
        component = ent.get("component", {}) or {}
        key = extract_label_key(component.get("label", ""))
        if key:
            indexed[key] = ent
    return indexed


async def _create_nifi_label_single(
    *,
    process_group_id: str,
    text: str,
    position_x: float,
    position_y: float,
    width: float,
    height: float,
    style: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set.")

    body_text = plain_label_text(text)
    try:
        created = await nifi_client.create_label(
            process_group_id,
            body_text,
            {"x": position_x, "y": position_y},
            width=width,
            height=height,
            style=style,
        )
        local_logger.info(f"Created label {created.get('id')} in PG {process_group_id}")
        return {
            "status": "success",
            "action": "created",
            "label_id": created.get("id"),
            "process_group_id": process_group_id,
            "component": created.get("component", {}),
        }
    except Exception as e:
        local_logger.error(f"Failed to create label in PG {process_group_id}: {e}")
        return {"status": "error", "message": str(e), "process_group_id": process_group_id}


@mcp.tool()
@tool_phases(["Review"])
async def list_nifi_labels(process_group_id: str) -> List[Dict[str, Any]]:
    """
    Lists canvas labels in a NiFi process group.

    Labels are non-executing plain-text annotations on the flow canvas.
    NiFi does not render HTML in labels.

    Args:
        process_group_id: UUID of the process group.

    Returns:
        List of label summaries: id, label (display text), label_key (if legacy marker present), position, size.
    """
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    if not nifi_client:
        raise ToolError("NiFi client context is not set.")

    labels = await nifi_client.list_labels(process_group_id)
    results: List[Dict[str, Any]] = []
    for ent in labels:
        component = ent.get("component", {}) or {}
        raw = component.get("label", "")
        pos = component.get("position") or {}
        results.append(
            {
                "id": ent.get("id"),
                "process_group_id": process_group_id,
                "label": strip_label_markers(raw),
                "label_key": extract_label_key(raw),
                "position": pos,
                "width": component.get("width"),
                "height": component.get("height"),
                "style": component.get("style"),
            }
        )
    return results


@mcp.tool()
@tool_phases(["Build", "Modify"])
async def create_nifi_labels(
    labels: List[Dict[str, Any]],
    process_group_id: str,
    layout_origin_x: float = 0,
    layout_origin_y: float = 0,
    layout_spacing_y: float = 120,
) -> List[Dict[str, Any]]:
    """
    Creates one or more canvas labels in a process group.

    Labels do not affect flow execution. Use plain text only — NiFi does not render HTML.

    Args:
        labels: Each dict may contain:
            - text (str, required): Plain-text label body (newlines allowed).
            - label_key (str, optional): Metadata for callers; not embedded in NiFi text.
            - position_x / position_y OR position: {x, y}
            - width (float, default 420), height (float, default 90)
            - style (dict, optional): e.g. {"font-size": "12px", "background-color": "#E8F4FC"}
            - process_group_id (str, optional): Override parent PG for this label
        process_group_id: Default PG for all labels.
        layout_origin_x, layout_origin_y, layout_spacing_y: Auto-position when x/y omitted.

    Returns:
        Per-label result with status and label_id on success.
    """
    if not labels:
        raise ToolError("The 'labels' list cannot be empty.")

    results: List[Dict[str, Any]] = []
    auto_slot = 0
    for i, label in enumerate(labels):
        text = label.get("text")
        if not text:
            results.append({"status": "error", "message": f"Label at index {i} missing 'text'.", "definition": label})
            continue
        pos = _resolve_position(label, slot=auto_slot, origin_x=layout_origin_x, origin_y=layout_origin_y, spacing_y=layout_spacing_y)
        if label.get("position_x") is None and label.get("position") is None:
            auto_slot += 1
        pg_id = label.get("process_group_id", process_group_id)
        result = await _create_nifi_label_single(
            process_group_id=pg_id,
            text=text,
            position_x=pos["x"],
            position_y=pos["y"],
            width=float(label.get("width", DEFAULT_LABEL_WIDTH)),
            height=float(label.get("height", DEFAULT_LABEL_HEIGHT)),
            style=label.get("style"),
        )
        if label.get("label_key"):
            result["label_key"] = label["label_key"]
        results.append(result)
    return results


@mcp.tool()
@tool_phases(["Build", "Modify"])
async def upsert_nifi_labels(
    labels: List[Dict[str, Any]],
    process_group_id: str,
    layout_origin_x: float = 0,
    layout_origin_y: float = 0,
    layout_spacing_y: float = 120,
) -> List[Dict[str, Any]]:
    """
    Creates or updates canvas labels by stable `label_key` (idempotent deploy).

    Match order: explicit `label_id` on the spec, then legacy marker in existing label text,
    otherwise create. Label text stored in NiFi is plain text only (no HTML, no embedded keys).

  For repeatable project deploys, keep a sidecar registry mapping label_key -> label_id
  (see TSWhitelisting/nifi/labels.registry.json).

    Args:
        labels: Same shape as create_nifi_labels; `label_key` is required on each entry.
            Optional `label_id` to update a known label directly.
        process_group_id: Default PG when not overridden per label.
        layout_origin_x, layout_origin_y, layout_spacing_y: Auto-position when x/y omitted.

    Returns:
        Per-label result with action 'created' or 'updated'.
    """
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set.")
    if not labels:
        raise ToolError("The 'labels' list cannot be empty.")

    by_pg: Dict[str, Dict[str, Dict[str, Any]]] = {}
    by_pg_id: Dict[str, Dict[str, Dict[str, Any]]] = {}
    results: List[Dict[str, Any]] = []
    auto_slot = 0

    for i, label in enumerate(labels):
        label_key = label.get("label_key")
        text = label.get("text")
        if not label_key:
            results.append({"status": "error", "message": f"Label at index {i} missing required 'label_key'.", "definition": label})
            continue
        if not text:
            results.append({"status": "error", "message": f"Label at index {i} missing required 'text'.", "definition": label})
            continue

        pg_id = label.get("process_group_id", process_group_id)
        if pg_id not in by_pg:
            existing = await nifi_client.list_labels(pg_id)
            by_pg[pg_id] = _index_labels_by_key(existing)
            by_pg_id[pg_id] = {ent.get("id"): ent for ent in existing if ent.get("id")}

        pos = _resolve_position(label, slot=auto_slot, origin_x=layout_origin_x, origin_y=layout_origin_y, spacing_y=layout_spacing_y)
        if label.get("position_x") is None and label.get("position") is None:
            auto_slot += 1

        body_text = plain_label_text(text)
        width = float(label.get("width", DEFAULT_LABEL_WIDTH))
        height = float(label.get("height", DEFAULT_LABEL_HEIGHT))
        style = label.get("style")

        existing_ent = None
        explicit_id = label.get("label_id")
        if explicit_id and explicit_id in by_pg_id.get(pg_id, {}):
            existing_ent = by_pg_id[pg_id][explicit_id]
        elif label_key in by_pg.get(pg_id, {}):
            existing_ent = by_pg[pg_id][label_key]

        try:
            if existing_ent:
                label_id = existing_ent.get("id")
                updated = await nifi_client.update_label(
                    label_id,
                    text=body_text,
                    position=pos,
                    width=width,
                    height=height,
                    style=style,
                )
                by_pg[pg_id][label_key] = updated
                if label_id:
                    by_pg_id[pg_id][label_id] = updated
                results.append(
                    {
                        "status": "success",
                        "action": "updated",
                        "label_id": label_id,
                        "process_group_id": pg_id,
                        "label_key": label_key,
                    }
                )
            else:
                created = await nifi_client.create_label(pg_id, body_text, pos, width=width, height=height, style=style)
                by_pg[pg_id][label_key] = created
                created_id = created.get("id")
                if created_id:
                    by_pg_id[pg_id][created_id] = created
                results.append(
                    {
                        "status": "success",
                        "action": "created",
                        "label_id": created_id,
                        "process_group_id": pg_id,
                        "label_key": label_key,
                    }
                )
        except Exception as e:
            local_logger.error(f"Upsert failed for label_key={label_key} in PG {pg_id}: {e}")
            results.append({"status": "error", "message": str(e), "label_key": label_key, "process_group_id": pg_id})

    return results


@mcp.tool()
@tool_phases(["Modify"])
async def update_nifi_labels(labels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Updates existing canvas labels by label_id.

    Args:
        labels: Each dict must include label_id. Optional: text (plain text), position, width, height, style.

    Returns:
        Per-label update result.
    """
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set.")
    if not labels:
        raise ToolError("The 'labels' list cannot be empty.")

    results: List[Dict[str, Any]] = []
    for i, label in enumerate(labels):
        label_id = label.get("label_id")
        if not label_id:
            results.append({"status": "error", "message": f"Label at index {i} missing 'label_id'."})
            continue
        text = label.get("text")
        if text is not None:
            text = plain_label_text(text)
        position = None
        if label.get("position_x") is not None or label.get("position_y") is not None or label.get("position"):
            position = _resolve_position(label)
        try:
            updated = await nifi_client.update_label(
                label_id,
                text=text,
                position=position,
                width=float(label["width"]) if label.get("width") is not None else None,
                height=float(label["height"]) if label.get("height") is not None else None,
                style=label.get("style"),
            )
            results.append({"status": "success", "label_id": label_id, "component": updated.get("component", {})})
        except Exception as e:
            local_logger.error(f"Failed to update label {label_id}: {e}")
            results.append({"status": "error", "label_id": label_id, "message": str(e)})
    return results
