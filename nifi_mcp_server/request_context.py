from contextvars import ContextVar
from typing import Optional

# Import types carefully to avoid circular dependencies if types are complex
# For now, assume basic types or forward references if needed
from nifi_mcp_server.nifi_client import NiFiClient
from loguru._logger import Logger # Correct import for type hint

# Context variables to hold request-specific instances
current_nifi_client: ContextVar[Optional[NiFiClient]] = ContextVar("current_nifi_client", default=None)
current_request_logger: ContextVar[Optional[Logger]] = ContextVar("current_request_logger", default=None)
current_user_request_id: ContextVar[Optional[str]] = ContextVar("current_user_request_id", default=None)
current_action_id: ContextVar[Optional[str]] = ContextVar("current_action_id", default=None)
current_workflow_id: ContextVar[Optional[str]] = ContextVar("current_workflow_id", default=None)
current_step_id: ContextVar[Optional[str]] = ContextVar("current_step_id", default=None)
# MCP-only control: current NiFi server (stdio) and phase restriction
current_nifi_server_id: ContextVar[Optional[str]] = ContextVar("current_nifi_server_id", default=None)
current_nifi_phase: ContextVar[Optional[str]] = ContextVar("current_nifi_phase", default=None)
# Session info: start time and rough token estimates (len(json.dumps(...))/4 per tool call)
mcp_session_started_at: ContextVar[Optional[float]] = ContextVar("mcp_session_started_at", default=None)
session_total_tokens_in: ContextVar[int] = ContextVar("session_total_tokens_in", default=0)
session_total_tokens_out: ContextVar[int] = ContextVar("session_total_tokens_out", default=0)

# Selected NiFi server id for MCP sessions.
#
# Why this exists:
# - ContextVars are scoped to the current async task/context and may not persist across
#   separate tool invocations in some MCP transports.
# - We still want `set_nifi_server()` to affect subsequent tool calls.
_selected_nifi_server_id: Optional[str] = None


def set_selected_nifi_server_id(server_id: Optional[str]) -> None:
    global _selected_nifi_server_id
    _selected_nifi_server_id = server_id


def get_selected_nifi_server_id() -> Optional[str]:
    return _selected_nifi_server_id

# Usage example (in tool functions):
# from .request_context import current_nifi_client, current_request_logger, current_user_request_id, current_action_id
#
# nifi_client = current_nifi_client.get()
# logger = current_request_logger.get()
# user_request_id = current_user_request_id.get() or "-"
# action_id = current_action_id.get() or "-"
# if not nifi_client or not logger:
#     raise ToolError("Required context (client or logger) is not set.")
# # ... use nifi_client and logger ...
# # ... pass user_request_id, action_id to specific client methods if needed ...

# Usage example (in server.py):
# from .request_context import current_nifi_client, current_request_logger, current_user_request_id, current_action_id
#
# client_token = current_nifi_client.set(nifi_client_instance)
# logger_token = current_request_logger.set(bound_logger_instance)
# user_id_token = current_user_request_id.set(user_request_id_from_header)
# action_id_token = current_action_id.set(action_id_from_header)
# try:
#     # ... call mcp.execute_tool ...
# finally:
#     current_nifi_client.reset(client_token)
#     current_request_logger.reset(logger_token)
#     current_user_request_id.reset(user_id_token)
#     current_action_id.reset(action_id_token) 