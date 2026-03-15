# Placeholder for MCP server interaction logic
# This will handle starting the server subprocess and communicating with it. 

import streamlit as st
import requests # Use requests for HTTP calls
import json
from typing import List, Dict, Any, Optional
import os
# Remove standard logging import
# import logging 
from loguru import logger # Import Loguru logger
from google.protobuf.internal.containers import MessageMap # Import the type if possible

def _convert_mapcomposite_to_dict(value):
    """
    Recursively convert MapComposite objects and other Google Proto objects to serializable dictionaries.
    
    Args:
        value: The value to convert, which may be a MapComposite, list, dict, or primitive type
        
    Returns:
        A JSON-serializable representation of the value
    """
    # Handle MapComposite objects
    if hasattr(value, 'items') and callable(value.items):
        # This is a MapComposite or similar dict-like object
        result = {}
        for key, val in value.items():
            result[key] = _convert_mapcomposite_to_dict(val)  # Recursive conversion
        return result
    
    # Handle lists (including ListComposite)
    elif isinstance(value, list) or (hasattr(value, '__iter__') and not isinstance(value, (str, bytes))):
        try:
            return [_convert_mapcomposite_to_dict(item) for item in value]
        except TypeError:
            # If iteration fails, convert to string
            return str(value)
    
    # Handle dictionaries
    elif isinstance(value, dict):
        result = {}
        for key, val in value.items():
            result[key] = _convert_mapcomposite_to_dict(val)
        return result
    
    # Handle Google Proto objects and other complex objects
    elif hasattr(value, '__dict__') or hasattr(value, '_pb') or str(type(value)).startswith('<google'):
        # Try to convert to dict if it has dict-like behavior
        if hasattr(value, 'items') and callable(value.items):
            result = {}
            for key, val in value.items():
                result[key] = _convert_mapcomposite_to_dict(val)
            return result
        else:
            # Fall back to string representation
            return str(value)
    
    # Handle primitive types - test if JSON serializable
    else:
        try:
            json.dumps(value)
            return value
        except (TypeError, OverflowError):
            return str(value)

# --- Configuration --- #
# URL for the MCP REST API (same server as nifi_mcp_server.server).
# Set MCP_SERVER_URL to override, or MCP_SERVER_PORT to change port only (e.g. when 8000 is in use).
_default_port = os.environ.get("MCP_SERVER_PORT", "8000")
API_BASE_URL = os.environ.get("MCP_SERVER_URL", f"http://localhost:{_default_port}")

# --- Remove All MCP Client, Threading, Asyncio imports and helpers --- #
# (Imports like ClientSession, stdio_client, websocket_client, McpError, ToolError removed)
# (Helpers like get_server_params, run_async_in_thread removed)

# --- New Function: Get NiFi Server List --- #
def get_nifi_servers() -> List[Dict[str, str]]:
    """Fetches the list of configured NiFi servers (ID and Name) from the API."""
    url = f"{API_BASE_URL}/config/nifi-servers"
    bound_logger = logger # Use base logger for this config call for now
    bound_logger.info(f"Fetching configured NiFi servers from: {url}")
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        servers = response.json()
        if isinstance(servers, list):
            # Validate basic structure
            valid_servers = [
                s for s in servers 
                if isinstance(s, dict) and "id" in s and "name" in s
            ]
            bound_logger.info(f"Successfully retrieved {len(valid_servers)} NiFi server configurations.")
            return valid_servers
        else:
            bound_logger.error(f"API Error: Unexpected format received for server list (expected list, got {type(servers)}). URL: {url}")
            return []
    except requests.exceptions.HTTPError as e:
        error_detail = "Unknown API error"
        try:
            error_detail = e.response.json().get("detail", e.response.text)
        except json.JSONDecodeError:
            error_detail = e.response.text
        bound_logger.error(f"API Error fetching NiFi servers: {e.response.status_code} - {error_detail}. URL: {url}")
        return []
    except requests.exceptions.ConnectionError as e:
        bound_logger.error(f"Connection Error fetching NiFi servers: Could not connect to {API_BASE_URL}. ({e})")
        return []
    except requests.exceptions.Timeout:
        bound_logger.error(f"Timeout connecting to MCP API server to fetch NiFi servers. URL: {url}")
        return []
    except Exception as e:
        bound_logger.exception(f"Unexpected error fetching NiFi servers. URL: {url}") # Includes traceback
        return []

# --- Tool Execution (Synchronous HTTP) --- #
def execute_mcp_tool(
    tool_name: str, 
    params: dict,
    selected_nifi_server_id: str | None, # Added parameter
    user_request_id: str | None = None, # Added context ID
    action_id: str | None = None # Added context ID
) -> dict | str:
    """Executes a tool call via the REST API."""
    # Bind context IDs for logging within this function call
    bound_logger = logger.bind(user_request_id=user_request_id, action_id=action_id, nifi_server_id=selected_nifi_server_id)
    
    url = f"{API_BASE_URL}/tools/{tool_name}"

    # Log context IDs explicitly for debugging
    bound_logger.debug(f"Tool execution context: user_request_id={user_request_id}, action_id={action_id}, nifi_server_id={selected_nifi_server_id}")

    # Convert MapComposite to dict before creating payload
    processed_params = {}
    for key, value in params.items():
        # Use the robust recursive conversion function
        processed_params[key] = _convert_mapcomposite_to_dict(value)

    # Add context IDs to the payload
    payload = {
        "arguments": processed_params,
        "context": {
            "user_request_id": user_request_id or "-",
            "action_id": action_id or "-",
            "nifi_server_id": selected_nifi_server_id or "-"
        }
    }

    # Create headers with context IDs
    headers = {
        "X-Request-ID": user_request_id or "-",
        "X-Action-ID": action_id or "-",
        "Content-Type": "application/json"
    }
    # Add the NiFi Server ID header if provided
    if selected_nifi_server_id:
        headers["X-Nifi-Server-Id"] = selected_nifi_server_id
    else:
        # Log a warning or error if the ID is missing, as it's now required by the backend
        bound_logger.error("Missing NiFi Server ID for tool execution request. This is required.")
        # Optionally raise an error or return an error message immediately
        # For now, let the API call proceed and likely fail on the backend
        # return {"status": "error", "message": "Missing required NiFi Server ID selection in the UI."} 
    
    bound_logger.info(f"Executing tool '{tool_name}' via API: {url}")
    bound_logger.debug(f"Payload for tool '{tool_name}': {payload}")
    bound_logger.debug(f"Headers for tool '{tool_name}': {headers}")
    
    # --- Log MCP Request ---
    bound_logger.bind(
        interface="mcp", 
        direction="request", 
        data={"url": url, "payload": payload, "headers": headers}
    ).debug("Sending request to MCP API")
    # -----------------------
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=60) # Add timeout
        response.raise_for_status() # Raise exception for bad status codes (4xx or 5xx)
        
        result_data = response.json()
        bound_logger.info(f"Received successful response from API for tool '{tool_name}'.")
        bound_logger.debug(f"API Response data: {result_data}")
        
        # --- Log MCP Response (Success) ---
        bound_logger.bind(
            interface="mcp", 
            direction="response", 
            data={"status_code": response.status_code, "body": result_data}
        ).debug("Received successful response from MCP API")
        # --------------------------------
        
        return result_data

    except requests.exceptions.HTTPError as e:
        error_detail = "Unknown API error"
        error_body = None
        try:
            error_body = e.response.json()
            error_detail = error_body.get("detail", e.response.text)
        except json.JSONDecodeError:
            error_detail = e.response.text 
            error_body = {"raw_text": error_detail} # Store raw text if not JSON
            
        # Check if this is a validation error (LLM input mistake) vs system error
        is_validation_error = (
            e.response.status_code == 400 and 
            ("validation error" in error_detail.lower() or 
             "field required" in error_detail.lower() or
             "invalid" in error_detail.lower() or
             "expected" in error_detail.lower() or
             "parameter" in error_detail.lower())
        )
        
        if is_validation_error:
            # Log the validation error for debugging but don't show in UI
            bound_logger.info(f"LLM parameter validation error (not shown in UI): {error_detail}")
            # Return a helpful message to the LLM without scary error details
            return {
                "error": True,
                "validation_error": True,
                "message": f"Parameter validation failed for '{tool_name}'. Please check the tool documentation and parameter format.",
                "details": error_detail,  # LLM can still see the details
                "ui_message": "Adjusting parameters..."  # Friendly message for UI
            }
        else:
            # This is a real system error that should be shown
            bound_logger.error(f"API Error executing tool '{tool_name}': {e.response.status_code} - {error_detail}")
            raise Exception(f"API Error executing tool '{tool_name}': {e.response.status_code} - {error_detail}")
        
    except requests.exceptions.ConnectionError as e:
        error_message = f"Connection Error: Could not connect to the MCP API server at {API_BASE_URL}. Is it running?"
        # Replace logging with logger
        bound_logger.error(f"{error_message} ({e})")
        # logging.error(f"{error_message} ({e})") # Use logging
        st.error(error_message) # Keep UI error
        return error_message
        
    except requests.exceptions.Timeout:
        error_message = f"Timeout connecting to MCP API server for tool '{tool_name}'."
        # Replace logging with logger
        bound_logger.error(error_message)
        # logging.error(error_message) # Use logging
        st.error(error_message) # Keep UI error
        return error_message
        
    except Exception as e:
        # Catch other unexpected errors (e.g., JSON decoding of success response)
        error_message = f"Unexpected error during tool execution API call for '{tool_name}'"
        # Replace logging.exception with logger.exception
        bound_logger.exception(error_message) # Includes traceback
        # logging.exception(error_message) # Use logging.exception to include traceback
        st.error(f"{error_message}: {e}") # Also show brief error in UI
        return f"{error_message}: {e}" # Return error string

# --- Tool Definitions (Synchronous HTTP) --- #
# @st.cache_data # Consider caching this
def get_available_tools(
    selected_nifi_server_id: str | None, # Added parameter
    user_request_id: str | None = None,
    action_id: str | None = None,
    phase: str | None = None # Add phase parameter
) -> list[dict]:
    """Fetches tool definitions from the REST API, optionally filtered by phase."""
    # Construct URL with optional phase parameter
    if phase and phase.lower() != "all":
        url = f"{API_BASE_URL}/tools?phase={phase}" # Pass phase if specified
    else:
        url = f"{API_BASE_URL}/tools"
    
    # Bind context IDs for logging within this function call
    bound_logger = logger.bind(user_request_id=user_request_id, action_id=action_id, nifi_server_id=selected_nifi_server_id)
    
    # Use logger instead of print
    bound_logger.info(f"Fetching available tools from API: {url}")
    
    # --- ADD LOGGING HERE ---
    bound_logger.debug(f"Constructed request URL for tools: {url}")
    # ------------------------
    
    try:
        # Create headers with context IDs
        headers = {
            "X-Request-ID": user_request_id or "-",
            "X-Action-ID": action_id or "-",
            "Content-Type": "application/json"
        }
        # Add the NiFi Server ID header if provided
        if selected_nifi_server_id:
            headers["X-Nifi-Server-Id"] = selected_nifi_server_id
        else:
            # Log a warning if the ID is missing, as /tools endpoint might work without it but /tools/{tool_name} won't
            bound_logger.warning("NiFi Server ID not provided for get_available_tools request. Backend might default or error.")
        
        # Log headers for debugging
        bound_logger.debug(f"Headers for tools request: {headers}")
        
        response = requests.get(url, headers=headers, timeout=30) # Add timeout and headers
        response.raise_for_status() # Raise exception for bad status codes
        
        tools = response.json() # Expecting a list of tool dicts
        if isinstance(tools, list):
            # Use logger instead of print
            bound_logger.info(f"Successfully retrieved {len(tools)} tool definitions from API.")
            return tools
        else:
            error_message = f"API Error: Unexpected format received for tools list (expected list, got {type(tools)})."
            # Use logger instead of print
            bound_logger.error(error_message)
            st.error(error_message) # Keep UI error
            return []
            
    except requests.exceptions.HTTPError as e:
        error_detail = "Unknown API error"
        try:
            error_detail = e.response.json().get("detail", e.response.text)
        except json.JSONDecodeError:
            error_detail = e.response.text
            
        error_message = f"API Error fetching tools: {e.response.status_code} - {error_detail}"
        # Replace logging with logger
        bound_logger.error(error_message)
        # logging.error(error_message) # Use logging
        st.error(error_message) # Keep UI error
        return []
        
    except requests.exceptions.ConnectionError as e:
        error_message = f"Connection Error: Could not connect to the MCP API server at {API_BASE_URL} to get tools. Is it running?"
        # Replace logging with logger
        bound_logger.error(f"{error_message} ({e})")
        # logging.error(f"{error_message} ({e})") # Use logging
        st.error(error_message) # Keep UI error
        return []
        
    except requests.exceptions.Timeout:
        error_message = f"Timeout connecting to MCP API server to get tools."
        # Replace logging with logger
        bound_logger.error(error_message)
        # logging.error(error_message) # Use logging
        st.error(error_message) # Keep UI error
        return []
        
    except Exception as e:
        error_message = f"Unexpected error during get_tools API call: {e}"
        # Replace logging.exception with logger.exception
        bound_logger.exception(error_message)
        # logging.exception(error_message) # Use logging.exception
        st.error(error_message) # Keep UI error
        return []

# Ensure streamlit UI code calls these synchronous functions directly.

# Remove old/unused functions and state
# def stop_mcp_server(): ... (no longer needed, session handles process)
# mcp_server_process = None
# mcp_lock = threading.Lock()
# stderr_thread = None
# stderr_queue = queue.Queue()
# def read_stderr(): ...
# def check_server_stderr(): ...
# def start_mcp_server(): ...
# def get_mcp_client(): ...

# --- Tool Definitions --- #

# @st.cache_data(ttl=3600)  # Cache for 1 hour, adjust as needed
# def get_tool_definitions() -> list[dict] | None:
#     """Fetches tool definitions from the MCP server.
#     Returns a list of tool definitions in OpenAI function-calling format,
#     or None if the server doesn't support tool definition queries.
#     """
#     # Use our refactored execute function
#     result_or_error = execute_mcp_tool("mcp.get_tool_definitions", {})
#
#     # If execute_mcp_tool returned an error string
#     if isinstance(result_or_error, str):
#         # Check if the error indicates the method wasn't found
#         # This requires inspecting the error string or potentially modifying
#         # execute_mcp_tool to return error codes.
#         # Let's check common substrings for now.
#         if "method not found" in result_or_error.lower() or f"(code: {types.METHOD_NOT_FOUND})" in result_or_error.lower():
#             print("Warning: MCP server likely doesn't support mcp.get_tool_definitions")
#             return None # Indicate not supported
#         else:
#             # Log other errors but still return None as definitions aren't available
#             print(f"Error getting tool definitions: {result_or_error}")
#             return None
#
#     # If we got a dictionary back, it should be the successful result
#     elif isinstance(result_or_error, dict):
#         try:
#             # According to our server implementation, the result should be: {'tools': [...]}
#             if "tools" in result_or_error:
#                 tools = result_or_error["tools"]
#                 if isinstance(tools, list):
#                     print(f"Successfully retrieved {len(tools)} tool definitions from server")
#                     return tools
#                 else:
#                     print(f"Unexpected format for 'tools' key in get_tool_definitions result: {type(tools)}")
#                     return None
#             else:
#                 print(f"Missing 'tools' key in successful get_tool_definitions result: {result_or_error}")
#                 return None
#
#         except Exception as e:
#             # Catch errors during result parsing
#             print(f"Error parsing successful tool definitions result: {e}")
#             return None
#     else:
#         # Should not happen if execute_mcp_tool works correctly
#         print(f"Unexpected return type from execute_mcp_tool: {type(result_or_error)}")
#         return None 