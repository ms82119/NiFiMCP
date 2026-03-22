"""MCP server instructions file and loading (no FastMCP mock in subprocess check)."""

from pathlib import Path
import subprocess
import sys


def test_mcp_instructions_file_exists_and_has_playbook():
    root = Path(__file__).resolve().parents[2]
    path = root / "nifi_mcp_server" / "mcp_instructions.txt"
    assert path.is_file(), f"Expected {path}"
    text = path.read_text(encoding="utf-8")
    assert len(text.strip()) > 50
    assert "document_nifi_flow" in text or "parallel" in text.lower()


def test_core_fastmcp_receives_non_empty_instructions():
    """Import core in a subprocess so unit conftest's FastMCP mock does not apply."""
    root = Path(__file__).resolve().parents[2]
    code = (
        "from nifi_mcp_server.core import mcp; "
        "assert mcp.instructions, 'mcp.instructions should be set'; "
        "assert len(mcp.instructions) > 50"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
