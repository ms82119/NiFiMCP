"""
Stdio entrypoint for the NiFi MCP server.

Runs the same FastMCP tools over stdio so that Cursor IDE (and other MCP clients)
can use the NiFi tools directly without the REST API or chat UI.

Server selection:
- Set NIFI_SERVER_ID to a configured server id from config.yaml, or
- If unset, the first NiFi server in config.yaml is used.

Usage (for Cursor MCP config):
  command: uv (or python)
  args: ["run", "nifi_mcp_server.stdio_server"]
  env: { "NIFI_SERVER_ID": "your-server-id" }  # optional
"""

from __future__ import annotations

import asyncio
import os
import sys

# Ensure project root is on path when run as __main__ or via -m
if __name__ == "__main__" or (__package__ or "").startswith("nifi_mcp_server"):
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _root not in sys.path:
        sys.path.insert(0, _root)

import time
import anyio
from loguru import logger

# Use same logging config as REST server (DEBUG, server.log, nifi_debug.log when enabled)
try:
    from config.logging_setup import setup_logging
    setup_logging(context="server")
except Exception as e:
    logger.warning("Logging setup failed (using defaults): {0}", e)

# Import core MCP and context so tools are registered and we can set context
from nifi_mcp_server.core import get_nifi_client, mcp

# Import tool modules so they register their tools with the shared mcp instance
# (same as server.py does for the REST API)
from nifi_mcp_server.api_tools import review  # noqa: F401
from nifi_mcp_server.api_tools import creation  # noqa: F401
from nifi_mcp_server.api_tools import modification  # noqa: F401
from nifi_mcp_server.api_tools import operation  # noqa: F401
from nifi_mcp_server.api_tools import helpers  # noqa: F401
from nifi_mcp_server.api_tools import control  # noqa: F401 - MCP-only server/phase tools

from nifi_mcp_server.request_context import (
    current_action_id,
    current_nifi_client,
    current_nifi_server_id,
    current_request_logger,
    current_user_request_id,
    mcp_session_started_at,
    session_total_tokens_in,
    session_total_tokens_out,
)
from config.settings import get_nifi_servers


def _default_server_id() -> str | None:
    """Resolve default NiFi server id from env or config."""
    env_id = os.environ.get("NIFI_SERVER_ID", "").strip()
    if env_id:
        return env_id
    servers = get_nifi_servers()
    if not servers:
        return None
    return servers[0].get("id")


async def run_stdio_with_context() -> None:
    """Set NiFi client (and logger) in context, then run MCP over stdio."""
    server_id = _default_server_id()
    if not server_id:
        logger.error(
            "No NiFi server selected. Set NIFI_SERVER_ID or configure at least one server in config.yaml."
        )
        sys.exit(1)

    logger.info(f"Using NiFi server id: {server_id}")

    nifi_client = None
    try:
        nifi_client = await get_nifi_client(server_id, bound_logger=logger)
        current_nifi_client.set(nifi_client)
        current_nifi_server_id.set(server_id)
        mcp_session_started_at.set(time.time())
        session_total_tokens_in.set(0)
        session_total_tokens_out.set(0)
        current_request_logger.set(logger)
        current_user_request_id.set("-")
        current_action_id.set("-")

        logger.info("NiFi MCP server starting (stdio). Tools will use the selected NiFi instance.")
        await mcp.run_stdio_async()
    except Exception as e:
        logger.exception(f"NiFi MCP stdio server failed: {e}")
        raise
    finally:
        if nifi_client:
            await nifi_client.close()
            current_nifi_client.set(None)


def main() -> None:
    """Entry point for stdio transport (used by Cursor and other MCP clients)."""
    anyio.run(run_stdio_with_context)


if __name__ == "__main__":
    main()
