"""Helpers for NiFi canvas labels.

NiFi renders label text literally (no HTML). Upsert identity uses an optional
project registry file and/or legacy embedded markers during migration.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

# Legacy: HTML comment appended by early versions (visible in NiFi — do not use).
LABEL_KEY_MARKER_RE = re.compile(r"<!--\s*nifi-mcp-label-key:([^>]+?)\s*-->\s*")
# Legacy: first-line bracket prefix [key]
LABEL_KEY_PREFIX_RE = re.compile(r"^\[([^\]]+)\]\s*\n?", re.MULTILINE)


def extract_label_key(label_text: Optional[str]) -> Optional[str]:
    if not label_text:
        return None
    match = LABEL_KEY_MARKER_RE.search(label_text)
    if match:
        return match.group(1).strip()
    prefix = LABEL_KEY_PREFIX_RE.match(label_text)
    if prefix:
        return prefix.group(1).strip()
    return None


def strip_label_markers(label_text: Optional[str]) -> str:
    """Return display-safe label text with machine markers removed."""
    if not label_text:
        return ""
    text = LABEL_KEY_MARKER_RE.sub("", label_text)
    text = LABEL_KEY_PREFIX_RE.sub("", text, count=1)
    return text.strip()


def plain_label_text(text: str) -> str:
    """Normalize author text for NiFi display (plain text only)."""
    return strip_label_markers(text.replace("<br/>", "\n").replace("<br>", "\n"))


def default_label_style() -> Dict[str, str]:
    return {
        "font-size": "12px",
        "background-color": "#E8F4FC",
    }


def normalize_label_style(style: Optional[Dict[str, Any]]) -> Dict[str, str]:
    merged = default_label_style()
    if isinstance(style, dict):
        merged.update({str(k): str(v) for k, v in style.items()})
    return merged
