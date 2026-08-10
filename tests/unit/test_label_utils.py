"""Unit tests for NiFi label helpers."""

from nifi_mcp_server.label_utils import extract_label_key, plain_label_text, strip_label_markers


def test_plain_label_strips_html_and_legacy_comment():
    raw = (
        "<b>Title</b><br/>\n"
        "Line two\n"
        "<!-- nifi-mcp-label-key:ts-wl-overview -->"
    )
    assert plain_label_text(raw) == "<b>Title</b>\n\nLine two"
    assert extract_label_key(raw) == "ts-wl-overview"
    assert strip_label_markers(raw) == "<b>Title</b><br/>\nLine two"


def test_plain_label_converts_br_to_newline():
    assert plain_label_text("A<br/>B") == "A\nB"


def test_extract_legacy_prefix_key():
    text = "[ts-wl-case]\nCase path notes"
    assert extract_label_key(text) == "ts-wl-case"
    assert strip_label_markers(text) == "Case path notes"
