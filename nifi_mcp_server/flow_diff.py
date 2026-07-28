"""Structural diff for NiFi versioned flow definition JSON (git-friendly comparisons).

Compares flowContents trees while ignoring layout noise (positions, style, etc.).
Components are matched by identifier when present on both sides, else by a stable
structural key (type + name, or connection endpoints + relationships).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

DEFAULT_IGNORE_KEYS: frozenset[str] = frozenset(
    {
        "position",
        "instanceIdentifier",
        "propertyDescriptors",
        "style",
        "bends",
        "labelIndex",
        "zIndex",
    }
)

# Collections nested under a VersionedProcessGroup
_PG_CHILD_COLLECTIONS: Tuple[str, ...] = (
    "processors",
    "controllerServices",
    "inputPorts",
    "outputPorts",
    "funnels",
    "labels",
    "connections",
    "remoteProcessGroups",
    "processGroups",
)


def _as_flow_root(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Return the VersionedProcessGroup root (flowContents) from a download/export doc."""
    if not isinstance(doc, dict):
        raise TypeError("Flow document must be a JSON object")
    if "flowContents" in doc and isinstance(doc["flowContents"], dict):
        return doc["flowContents"]
    # Already a VersionedProcessGroup (has processors/processGroups keys)
    if any(k in doc for k in _PG_CHILD_COLLECTIONS) or "name" in doc:
        return doc
    raise ValueError("Flow document has no flowContents and does not look like a process group")


def _component_type_label(comp: Dict[str, Any]) -> str:
    return (
        comp.get("componentType")
        or comp.get("type")
        or "UNKNOWN"
    )


def _stable_key(comp: Dict[str, Any], collection: str) -> str:
    """Structural match key when identifiers diverge (e.g. after re-import)."""
    if collection == "connections":
        src = (comp.get("source") or {}) if isinstance(comp.get("source"), dict) else {}
        dst = (comp.get("destination") or {}) if isinstance(comp.get("destination"), dict) else {}
        rels = comp.get("selectedRelationships") or []
        if isinstance(rels, list):
            rel_part = ",".join(sorted(str(r) for r in rels))
        else:
            rel_part = str(rels)
        return (
            f"CONN:{src.get('name', '')}->{dst.get('name', '')}"
            f"[{rel_part}]"
        )
    name = comp.get("name") or ""
    ctype = _component_type_label(comp)
    return f"{ctype}:{name}"


def _normalize(value: Any, ignore_keys: Set[str]) -> Any:
    if isinstance(value, dict):
        return {
            k: _normalize(v, ignore_keys)
            for k, v in sorted(value.items())
            if k not in ignore_keys
        }
    if isinstance(value, list):
        return [_normalize(v, ignore_keys) for v in value]
    return value


def _field_diffs(before: Any, after: Any, prefix: str = "") -> List[Dict[str, Any]]:
    """Return leaf-level field changes between two normalized structures."""
    changes: List[Dict[str, Any]] = []
    if type(before) is not type(after) and not (
        isinstance(before, (int, float)) and isinstance(after, (int, float))
    ):
        changes.append({"path": prefix or ".", "before": before, "after": after})
        return changes
    if isinstance(before, dict) and isinstance(after, dict):
        keys = set(before) | set(after)
        for key in sorted(keys):
            path = f"{prefix}.{key}" if prefix else key
            if key not in before:
                changes.append({"path": path, "before": None, "after": after[key]})
            elif key not in after:
                changes.append({"path": path, "before": before[key], "after": None})
            else:
                changes.extend(_field_diffs(before[key], after[key], path))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        if before != after:
            changes.append({"path": prefix or ".", "before": before, "after": after})
        return changes
    if before != after:
        changes.append({"path": prefix or ".", "before": before, "after": after})
    return changes


def _index_collection(
    items: Sequence[Dict[str, Any]], collection: str
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]], List[str]]:
    """Return (by_id, by_stable, duplicate_stable_keys)."""
    by_id: Dict[str, Dict[str, Any]] = {}
    by_stable: Dict[str, Dict[str, Any]] = {}
    dupes: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ident = item.get("identifier")
        if isinstance(ident, str) and ident:
            by_id[ident] = item
        sk = _stable_key(item, collection)
        if sk in by_stable:
            dupes.append(sk)
        else:
            by_stable[sk] = item
    return by_id, by_stable, dupes


def _match_pairs(
    left_items: Sequence[Dict[str, Any]],
    right_items: Sequence[Dict[str, Any]],
    collection: str,
) -> Tuple[
    List[Tuple[Dict[str, Any], Dict[str, Any]]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[str],
]:
    left_by_id, left_by_sk, left_dupes = _index_collection(left_items, collection)
    right_by_id, right_by_sk, right_dupes = _index_collection(right_items, collection)
    warnings = [f"duplicate stable key on left ({collection}): {d}" for d in left_dupes]
    warnings.extend(f"duplicate stable key on right ({collection}): {d}" for d in right_dupes)

    matched: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    used_left: Set[int] = set()
    used_right: Set[int] = set()

    # Prefer identifier matches
    for ident, left in left_by_id.items():
        right = right_by_id.get(ident)
        if right is None:
            continue
        matched.append((left, right))
        used_left.add(id(left))
        used_right.add(id(right))

    # Fall back to stable keys for remaining
    for sk, left in left_by_sk.items():
        if id(left) in used_left:
            continue
        right = right_by_sk.get(sk)
        if right is None or id(right) in used_right:
            continue
        matched.append((left, right))
        used_left.add(id(left))
        used_right.add(id(right))

    removed = [item for item in left_items if isinstance(item, dict) and id(item) not in used_left]
    added = [item for item in right_items if isinstance(item, dict) and id(item) not in used_right]
    return matched, removed, added, warnings


def _pg_path(parent_path: str, pg: Dict[str, Any]) -> str:
    name = pg.get("name") or pg.get("identifier") or "?"
    return f"{parent_path}/{name}" if parent_path else str(name)


def _scalar_pg_fields(pg: Dict[str, Any], ignore_keys: Set[str]) -> Dict[str, Any]:
    """PG-level fields excluding nested collections."""
    skip = set(_PG_CHILD_COLLECTIONS) | ignore_keys | {"variables"}
    return {
        k: _normalize(v, ignore_keys)
        for k, v in pg.items()
        if k not in skip and not isinstance(v, (list, dict))
    }


def _diff_process_group(
    left: Dict[str, Any],
    right: Dict[str, Any],
    path: str,
    ignore_keys: Set[str],
    changes: List[Dict[str, Any]],
    warnings: List[str],
    max_changes: int,
) -> None:
    if len(changes) >= max_changes:
        return

    # Scalar / simple PG attributes
    left_scalar = _scalar_pg_fields(left, ignore_keys)
    right_scalar = _scalar_pg_fields(right, ignore_keys)
    for fd in _field_diffs(left_scalar, right_scalar):
        if len(changes) >= max_changes:
            return
        changes.append(
            {
                "kind": "changed",
                "component_type": "processGroup",
                "path": path,
                "name": left.get("name") or right.get("name"),
                "field": fd["path"],
                "before": fd["before"],
                "after": fd["after"],
            }
        )

    # Variables map (string->string) if present
    if "variables" not in ignore_keys:
        lv = left.get("variables") if isinstance(left.get("variables"), dict) else {}
        rv = right.get("variables") if isinstance(right.get("variables"), dict) else {}
        for fd in _field_diffs(_normalize(lv, ignore_keys), _normalize(rv, ignore_keys), "variables"):
            if len(changes) >= max_changes:
                return
            changes.append(
                {
                    "kind": "changed",
                    "component_type": "processGroup",
                    "path": path,
                    "name": left.get("name") or right.get("name"),
                    "field": fd["path"],
                    "before": fd["before"],
                    "after": fd["after"],
                }
            )

    for collection in _PG_CHILD_COLLECTIONS:
        if collection == "processGroups":
            continue
        left_items = left.get(collection) or []
        right_items = right.get(collection) or []
        if not isinstance(left_items, list):
            left_items = []
        if not isinstance(right_items, list):
            right_items = []

        pairs, removed, added, w = _match_pairs(left_items, right_items, collection)
        warnings.extend(w)

        for item in removed:
            if len(changes) >= max_changes:
                return
            changes.append(
                {
                    "kind": "removed",
                    "component_type": collection.rstrip("s") if collection.endswith("s") else collection,
                    "collection": collection,
                    "path": f"{path}/{item.get('name') or _stable_key(item, collection)}",
                    "name": item.get("name"),
                    "identifier": item.get("identifier"),
                    "type": item.get("type") or item.get("componentType"),
                }
            )
        for item in added:
            if len(changes) >= max_changes:
                return
            changes.append(
                {
                    "kind": "added",
                    "component_type": collection.rstrip("s") if collection.endswith("s") else collection,
                    "collection": collection,
                    "path": f"{path}/{item.get('name') or _stable_key(item, collection)}",
                    "name": item.get("name"),
                    "identifier": item.get("identifier"),
                    "type": item.get("type") or item.get("componentType"),
                }
            )

        # Compare matched component payloads (excluding nested process groups handled below)
        compare_skip = ignore_keys | {"processGroups"}
        for left_item, right_item in pairs:
            if len(changes) >= max_changes:
                return
            ln = _normalize(
                {k: v for k, v in left_item.items() if k != "processGroups"},
                compare_skip,
            )
            rn = _normalize(
                {k: v for k, v in right_item.items() if k != "processGroups"},
                compare_skip,
            )
            field_changes = _field_diffs(ln, rn)
            if not field_changes:
                continue
            # Cap per-component field detail
            shown = field_changes[:25]
            changes.append(
                {
                    "kind": "changed",
                    "component_type": collection.rstrip("s") if collection.endswith("s") else collection,
                    "collection": collection,
                    "path": f"{path}/{left_item.get('name') or _stable_key(left_item, collection)}",
                    "name": left_item.get("name") or right_item.get("name"),
                    "identifier": left_item.get("identifier") or right_item.get("identifier"),
                    "type": left_item.get("type") or left_item.get("componentType"),
                    "fields": [fc["path"] for fc in shown],
                    "field_changes": shown,
                    "field_changes_truncated": len(field_changes) > len(shown),
                    "field_change_count": len(field_changes),
                }
            )

    # Recurse into child process groups
    left_pgs = left.get("processGroups") or []
    right_pgs = right.get("processGroups") or []
    if not isinstance(left_pgs, list):
        left_pgs = []
    if not isinstance(right_pgs, list):
        right_pgs = []
    pairs, removed, added, w = _match_pairs(left_pgs, right_pgs, "processGroups")
    warnings.extend(w)

    for item in removed:
        if len(changes) >= max_changes:
            return
        changes.append(
            {
                "kind": "removed",
                "component_type": "processGroup",
                "collection": "processGroups",
                "path": _pg_path(path, item),
                "name": item.get("name"),
                "identifier": item.get("identifier"),
            }
        )
    for item in added:
        if len(changes) >= max_changes:
            return
        changes.append(
            {
                "kind": "added",
                "component_type": "processGroup",
                "collection": "processGroups",
                "path": _pg_path(path, item),
                "name": item.get("name"),
                "identifier": item.get("identifier"),
            }
        )
    for left_pg, right_pg in pairs:
        if len(changes) >= max_changes:
            return
        child_path = _pg_path(path, left_pg)
        _diff_process_group(
            left_pg, right_pg, child_path, ignore_keys, changes, warnings, max_changes
        )


def diff_flow_documents(
    left_doc: Dict[str, Any],
    right_doc: Dict[str, Any],
    *,
    ignore_keys: Optional[Iterable[str]] = None,
    max_changes: int = 200,
) -> Dict[str, Any]:
    """Structurally diff two NiFi flow definition documents.

    Args:
        left_doc: Baseline flow JSON (typically older / expected).
        right_doc: Comparison flow JSON (typically newer / live).
        ignore_keys: Keys stripped before comparison (default: positions, style, descriptors…).
        max_changes: Cap on change entries returned (summary still counts all up to this list).

    Returns:
        Dict with identical, summary counts, changes list, warnings, truncated flag.
    """
    ignore = set(DEFAULT_IGNORE_KEYS)
    if ignore_keys is not None:
        ignore = set(ignore_keys)

    left_root = _as_flow_root(left_doc)
    right_root = _as_flow_root(right_doc)
    root_name = left_root.get("name") or right_root.get("name") or "flowContents"
    path = str(root_name)

    changes: List[Dict[str, Any]] = []
    warnings: List[str] = []
    _diff_process_group(left_root, right_root, path, ignore, changes, warnings, max_changes)

    # Top-level snapshot metadata (external services / parameter contexts) — high-level only
    for top_key in ("externalControllerServices", "parameterContexts", "parameterProviders"):
        lv = left_doc.get(top_key) if isinstance(left_doc, dict) else None
        rv = right_doc.get(top_key) if isinstance(right_doc, dict) else None
        if lv is None and rv is None:
            continue
        ln = _normalize(lv or {}, ignore)
        rn = _normalize(rv or {}, ignore)
        if ln != rn:
            if len(changes) < max_changes:
                changes.append(
                    {
                        "kind": "changed",
                        "component_type": "snapshot",
                        "path": top_key,
                        "name": top_key,
                        "fields": [top_key],
                        "before_keys": sorted(ln.keys()) if isinstance(ln, dict) else None,
                        "after_keys": sorted(rn.keys()) if isinstance(rn, dict) else None,
                    }
                )

    added = sum(1 for c in changes if c.get("kind") == "added")
    removed = sum(1 for c in changes if c.get("kind") == "removed")
    changed = sum(1 for c in changes if c.get("kind") == "changed")
    truncated = len(changes) >= max_changes

    return {
        "identical": added == 0 and removed == 0 and changed == 0,
        "summary": {
            "added": added,
            "removed": removed,
            "changed": changed,
            "total": added + removed + changed,
        },
        "changes": changes,
        "warnings": warnings,
        "truncated": truncated,
        "max_changes": max_changes,
        "ignore_keys": sorted(ignore),
        "left_root_name": left_root.get("name"),
        "right_root_name": right_root.get("name"),
    }
