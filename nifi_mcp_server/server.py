import asyncio
import signal
from typing import List, Dict, Optional, Any, Union, Literal
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body, Request, Query, Header
from fastapi.responses import JSONResponse 
from fastapi.middleware.cors import CORSMiddleware
import os
import sys
from loguru import logger 
from docstring_parser import parse 
from contextlib import asynccontextmanager # Added import
from textwrap import dedent # <-- IMPORT ADDED

# --- Setup Logging --- 
try:
    # Adjust import path based on project structure if necessary
    # If server.py is run directly from project root, this might need adjustment
    # Assuming server is run from project root or config is in PYTHONPATH
    from config.logging_setup import setup_logging, request_context
    setup_logging(context='server')
except ImportError as e:
    logger.warning(f"Logging setup failed: {e}. Check config/logging_setup.py and Python path. Using basic stderr logger.")
    # Minimal fallback if setup fails
    logger.add(sys.stderr, level="INFO")
# ---------------------

# Import our NiFi API client and exception (Absolute Import)
from nifi_mcp_server.nifi_client import NiFiAuthenticationError # Keep Error import
# REMOVED from nifi_mcp_server.nifi_client import NiFiClient

# Import MCP server components (Corrected for v1.6.0)
# REMOVED from mcp.server import FastMCP

# Import core components AFTER logging is setup, but BEFORE tools
from .core import mcp, get_nifi_client

# Import the context var from logging_setup
from config.logging_setup import request_context # Adjust import path if needed

# --- Import ContextVars --- #
from .request_context import current_nifi_client, current_request_logger, current_user_request_id, current_action_id # Added

from mcp.shared.exceptions import McpError # Base error
from mcp.server.fastmcp.exceptions import ToolError # Tool-specific errors

# REMOVED MCP Instantiation Block

# REMOVED NiFiClient Instantiation Block

# --- Import Utilities AFTER core components are imported ---
from .api_tools.utils import (
    tool_phases,
    _format_processor_summary,
    _format_connection_summary,
    _format_port_summary,
    filter_processor_data,
    filter_created_processor_data,
    filter_connection_data,
    filter_port_data,
    filter_process_group_data,
    _tool_phase_registry # Import the registry itself
)
# ---------------------------------------------------------------------

# --- Import Tool Modules AFTER mcp is defined to allow registration ---
from .api_tools import review # Imports list_*, get_*, document_*
from .api_tools import creation
from .api_tools import modification
from .api_tools import operation
from .api_tools import helpers
# Add other tool module imports here as they are created
# from .api_tools import helpers
# ---------------------------------------------------------------------

# --- Import Config Settings --- #
from config.settings import get_nifi_servers # Added


# === FastAPI Application Setup === #

# --- Lifespan Context Manager --- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    logger.info("FastAPI server starting up...")
    
    # Configure logging for server context
    try:
        from config.logging_setup import setup_logging
        setup_logging('server')
        logger.info("Server logging configured")
    except Exception as e:
        logger.error(f"Failed to configure server logging: {e}", exc_info=True)
    
    # Configure LLM clients for workflow execution
    try:
        from nifi_chat_ui.llm.chat_manager import ChatManager
        # Initialize ChatManager with config (import config as needed)
        from config import settings as config
        config_dict = {
            'openai': {
                'api_key': config.OPENAI_API_KEY,
                'models': config.OPENAI_MODELS
            },
            'gemini': {
                'api_key': config.GOOGLE_API_KEY,
                'models': config.GEMINI_MODELS
            },
            'anthropic': {
                'api_key': config.ANTHROPIC_API_KEY,
                'models': config.ANTHROPIC_MODELS
            },
            'perplexity': {
                'api_key': config.PERPLEXITY_API_KEY,
                'models': config.PERPLEXITY_MODELS
            },
            # Add flat keys for Gemini for compatibility
            'GOOGLE_API_KEY': config.GOOGLE_API_KEY,
            'GEMINI_MODELS': config.GEMINI_MODELS,
            'GEMINI_DEFAULT_MODEL': getattr(config, 'GEMINI_DEFAULT_MODEL', 'gemini-1.5-pro'),
        }
        _chat_manager = ChatManager(config_dict)
        logger.info("LLM clients configured for workflow execution")
    except Exception as e:
        logger.error(f"Failed to configure LLM clients: {e}", exc_info=True)
        logger.warning("Workflows may not be able to call LLMs")
    
    if not get_nifi_servers():
        logger.warning("*******************************************************")
        logger.warning("*** No NiFi servers configured in config.yaml!      ***")
        logger.warning("*** The /tools/{tool_name} endpoint will not work! ***")
        logger.warning("*******************************************************")
    else:
        logger.info(f"Found {len(get_nifi_servers())} NiFi server configurations.")
    
    yield # Application runs here
    
    # Shutdown logic (moved from shutdown_event and cleanup)
    logger.info("FastAPI server shutting down...")
    # Call cleanup logic directly here if needed in the future
    # await cleanup() 
    logger.info("Cleanup finished.")

app = FastAPI(
    title="NiFi MCP REST Bridge", 
    description="Exposes NiFi MCP tools via a REST API.",
    lifespan=lifespan # Use the lifespan manager
)

# --- CORS Middleware --- #
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permissive for now
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- FastAPI Event Handlers (REMOVED) --- #
# @app.on_event("startup")
# async def startup_event():
#     logger.info("FastAPI server starting up...")
#     # Check if any NiFi servers are configured
#     if not get_nifi_servers():
#         logger.warning("*******************************************************")
#         logger.warning("*** No NiFi servers configured in config.yaml!      ***")
#         logger.warning("*** The /tools/{tool_name} endpoint will not work! ***")
#         logger.warning("*******************************************************")
#     else:
#         logger.info(f"Found {len(get_nifi_servers())} NiFi server configurations.")

# @app.on_event("shutdown")
# async def shutdown_event():
#     logger.info("FastAPI server shutting down...")
#     await cleanup()

# --- REST API Endpoints --- #

@app.get("/config/nifi-servers", response_model=List[Dict[str, str]], tags=["Configuration"])
async def list_nifi_servers(request: Request):
    """Returns a list of configured NiFi servers (only ID and Name)."""
    user_request_id = request.state.user_request_id if hasattr(request.state, 'user_request_id') else "-"
    action_id = request.state.action_id if hasattr(request.state, 'action_id') else "-"
    bound_logger = logger.bind(user_request_id=user_request_id, action_id=action_id)

    bound_logger.info("Request received for /config/nifi-servers")
    try:
        all_servers = get_nifi_servers() # Fetch from settings.py
        # Filter to return only 'id' and 'name' for the client
        client_safe_servers = [
            {"id": server.get("id", ""), "name": server.get("name", "Unnamed Server")}
            for server in all_servers
            if server.get("id") and server.get("name") # Ensure basic validity
        ]
        bound_logger.info(f"Returning {len(client_safe_servers)} NiFi server configurations to the client.")
        return client_safe_servers
    except Exception as e:
        bound_logger.error(f"Error retrieving NiFi server list: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error retrieving NiFi server list.")

@app.get("/tools", response_model=List[Dict[str, Any]], tags=["Tools"])
async def get_tools(
    request: Request, 
    phase: str | None = Query(None) # Add phase query parameter
):
    """Retrieve the list of available MCP tools, optionally filtered by phase."""
    user_request_id = request.state.user_request_id
    action_id = request.state.action_id
    bound_logger = logger.bind(user_request_id=user_request_id, action_id=action_id, requested_phase=phase)
    
    bound_logger.debug(f"/tools endpoint received phase parameter: {phase!r}") # Log the raw value
    
    try:
        bound_logger.debug(f"Fetching tools, requested phase: '{phase}'")
        formatted_tools = []
        tool_manager = getattr(mcp, '_tool_manager', None)
        if tool_manager:
            tools_info = tool_manager.list_tools()
            
            for tool_info in tools_info: 
                tool_name = getattr(tool_info, 'name', 'unknown')
                tool_phases_list = _tool_phase_registry.get(tool_name, [])
                if not tool_phases_list:
                     bound_logger.warning(f"Could not find phase tags in registry for tool '{tool_name}'. Assuming it belongs to all phases for safety.")

                requested_phase_lower = phase.lower() if phase else None
                tool_phases_lower = [p.lower() for p in tool_phases_list]
                
                if requested_phase_lower and requested_phase_lower != "all" and requested_phase_lower not in tool_phases_lower:
                    bound_logger.trace(f"Skipping tool '{tool_name}' due to phase mismatch (requested: {phase}, tool phases: {tool_phases_list})")
                    continue # Skip this tool if phase doesn't match

                raw_docstring = getattr(tool_info, 'description', '')
                parsed_docstring = parse(raw_docstring)
                returns_description = ""
                if parsed_docstring.returns and parsed_docstring.returns.description:
                    returns_description = f"\n\n**Returns:**\n{parsed_docstring.returns.description}"

                base_description_parts = []
                if parsed_docstring.short_description:
                    base_description_parts.append(parsed_docstring.short_description)
                
                if parsed_docstring.long_description:
                    base_description_parts.append("\n\n" + parsed_docstring.long_description) # Add separation

                if base_description_parts:
                    base_description = "".join(base_description_parts)
                    bound_logger.trace(f"Using parsed short/long description for tool '{tool_name}'")
                else:
                    base_description = raw_docstring.split('\n\n')[0] # Original fallback
                    bound_logger.trace(f"Falling back to basic description for tool '{tool_name}' (parsing empty?)")

                # --- BEGIN ADDITION: Extract Example section ---
                example_section = ""
                example_markers = ["Example:\n", "Examples:\n"] # Check for both singular and plural
                normalized_docstring = "\n" + raw_docstring # Ensure leading newline for marker check at start

                marker_found = None
                marker_start_index = -1
                for marker in example_markers:
                    found_index = normalized_docstring.find(marker)
                    if found_index != -1:
                        marker_found = marker
                        marker_start_index = found_index
                        break
                
                if marker_found:
                    # Start content search *after* the marker
                    content_start_index = marker_start_index + len(marker_found)
                    potential_example_content = normalized_docstring[content_start_index:]

                    # Find the end of the example block by looking for the next common section marker
                    next_section_markers = ["Args:\n", "Returns:\n", "Raises:\n", "Attributes:\n", "Yields:\n"] # Add others if needed
                    end_index = len(potential_example_content) # Default to end of string

                    for next_marker in next_section_markers:
                         found_index = potential_example_content.find(next_marker)
                         if found_index != -1:
                            end_index = min(end_index, found_index)

                    # Dedent the extracted block before stripping
                    raw_example_content = potential_example_content[:end_index]
                    dedented_content = dedent(raw_example_content)
                    example_content = dedented_content.strip() # Strip after dedenting
                    
                    if example_content:
                         # Re-add the marker itself (stripped) and format with bold markdown around it
                         example_section = f"\n\n**{marker_found.strip()}**\n{example_content}\n\n"
                         bound_logger.trace(f"Extracted Example section for tool '{tool_name}'")
                # --- END ADDITION ---

                tool_description = f"{base_description}{example_section}{returns_description}"

                param_descriptions = {p.arg_name: p.description for p in parsed_docstring.params}
                raw_params_schema = getattr(tool_info, 'parameters', {})
                parameters_schema = {"type": "object", "properties": {}}
                raw_properties = raw_params_schema.get('properties', {})
                cleaned_properties = {}
                if isinstance(raw_properties, dict):
                    for prop_name, prop_schema in raw_properties.items():
                        if isinstance(prop_schema, dict):
                            cleaned_schema = prop_schema.copy()
                            cleaned_schema.pop('anyOf', None)
                            cleaned_schema.pop('title', None)
                            cleaned_schema.pop('default', None)
                            cleaned_schema['description'] = param_descriptions.get(prop_name, '')
                            cleaned_properties[prop_name] = cleaned_schema
                        else:
                            logger.warning(f"Property '{prop_name}' in tool '{tool_name}' has non-dict schema: {prop_schema}. Skipping property.")
                parameters_schema["properties"] = cleaned_properties
                required_list = raw_params_schema.get('required', [])
                if required_list and cleaned_properties:
                     parameters_schema["required"] = required_list
                elif "required" in parameters_schema:
                     del parameters_schema["required"]
                if not parameters_schema["properties"]:
                     del parameters_schema["properties"]
                     if "required" in parameters_schema: del parameters_schema["required"]
                if 'required' in raw_params_schema:
                    parameters_schema["required"] = list(raw_params_schema['required'])
                if 'properties' in parameters_schema:
                    for prop_name, prop_data in parameters_schema['properties'].items():
                        if isinstance(prop_data, dict) and 'enum' in prop_data:
                            prop_data['enum'] = [str(val) for val in prop_data['enum']]
                
                formatted_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": tool_description,
                        "parameters": parameters_schema
                    },
                    "phases": tool_phases_list # Include phases in the response
                })
            bound_logger.info(f"Returning {len(formatted_tools)} tool definitions (Phase: {phase or 'All'}).")
            return formatted_tools
        else:
            bound_logger.warning("Could not find ToolManager (_tool_manager) on MCP instance.")
            return []
    except Exception as e:
        bound_logger.error(f"Error retrieving tool definitions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error retrieving tools.")

# Define a Pydantic model for the request body with context support
from pydantic import BaseModel

# Define a model for context data
class ContextModel(BaseModel):
    user_request_id: Optional[str] = "-"
    action_id: Optional[str] = "-"
    # nifi_server_id: Optional[str] = "-" # Removed - handled by header

class ToolExecutionPayload(BaseModel):
    arguments: Dict[str, Any]
    context: Optional[ContextModel] = None

# Middleware for binding context IDs to logger
@app.middleware("http")
async def add_context_to_logger(request: Request, call_next):
    user_request_id = request.headers.get("X-Request-ID", "-")
    action_id = request.headers.get("X-Action-ID", "-")
    
    # Store IDs in request.state (as before, might be useful elsewhere)
    request.state.user_request_id = user_request_id
    request.state.action_id = action_id

    # --- Set ContextVar for Loguru Patcher --- 
    context_data = {
        "user_request_id": user_request_id,
        "action_id": action_id,
        "headers": dict(request.headers)  # Add all headers to context
    }
    loguru_context_token = request_context.set(context_data) # Set context for Loguru patcher
    # -----------------------------------------
    
    # --- Set ContextVars for Request IDs --- #
    user_id_token = current_user_request_id.set(user_request_id)
    action_id_token = current_action_id.set(action_id)
    # --------------------------------------- #

    if user_request_id != "-" or action_id != "-":
        # Use logger directly here, it will be patched
        logger.debug(f"Received request with context IDs: user_request_id={user_request_id}, action_id={action_id}")
    
    try:
        response = await call_next(request)
    finally:
        # --- Reset ContextVar --- 
        request_context.reset(loguru_context_token) # Reset Loguru context
        # ------------------------
        # --- Reset Request ID ContextVars --- #
        current_user_request_id.reset(user_id_token)
        current_action_id.reset(action_id_token)
        # ---------------------------------- #
    return response

@app.post("/tools/{tool_name}", tags=["Tools"])
async def execute_tool(
    tool_name: str,
    payload: ToolExecutionPayload,
    request: Request,
    nifi_server_id: Optional[str] = Header(None, alias="X-Nifi-Server-Id")
) -> Any:
    """Execute a specific MCP tool by name.

    Requires the `X-Nifi-Server-Id` header to specify which configured NiFi server to target.
    """
    user_request_id = request.state.user_request_id
    action_id = request.state.action_id
    bound_logger = logger.bind(user_request_id=user_request_id, action_id=action_id, tool_name=tool_name, nifi_server_id=nifi_server_id)

    bound_logger.info(f"Received request to execute tool: {tool_name}")

    # --- NiFi Server ID Check --- #
    if not get_nifi_servers():
        bound_logger.error("Cannot execute tool: No NiFi servers are configured in config.yaml.")
        raise HTTPException(status_code=503, detail="No NiFi servers configured on the server.")

    if not nifi_server_id:
        bound_logger.warning("Missing X-Nifi-Server-Id header.")
        raise HTTPException(status_code=400, detail="Missing required header: X-Nifi-Server-Id")
    # -------------------------- #

    tool_input = payload.arguments
    bound_logger.debug(f"Tool arguments received: {tool_input}")

    nifi_client = None # Define outside try block for cleanup
    client_token = None # Token for contextvar reset
    logger_token = None # Token for contextvar reset
    try:
        # --- Get NiFi Client for this request --- #
        bound_logger.debug(f"Attempting to get NiFi client for server ID: {nifi_server_id}")
        nifi_client = await get_nifi_client(nifi_server_id, bound_logger=bound_logger)
        bound_logger.debug(f"Successfully obtained authenticated NiFi client for {nifi_server_id}")
        # -------------------------------------- #
        
        # --- Set ContextVars --- #
        client_token = current_nifi_client.set(nifi_client)
        logger_token = current_request_logger.set(bound_logger)
        bound_logger.trace("Set NiFi client and logger in context variables.")
        # ----------------------- #

        # --- Execute the tool via MCP --- #
        bound_logger.info(f"Executing tool '{tool_name}'...")
        
        # Call the tool using the correct method on the FastMCP instance
        # ContextVars provide client/logger implicitly via the context mechanism within call_tool
        tool_result_mcp_format = await mcp.call_tool(tool_name, tool_input)
                
        bound_logger.info(f"Tool '{tool_name}' execution successful.")
        bound_logger.debug(f"Raw MCP Tool result: {tool_result_mcp_format}") 
        
        # --- Extract serializable result from MCP format --- 
        final_result_to_serialize = None
        
        # Handle case where result is a tuple (TextContent + dict)
        if isinstance(tool_result_mcp_format, tuple) and len(tool_result_mcp_format) == 2:
            text_content_list, result_dict = tool_result_mcp_format
            
            # Extract the actual result from the dictionary
            if isinstance(result_dict, dict) and 'result' in result_dict:
                final_result_to_serialize = result_dict['result']
                bound_logger.debug(f"Extracted result from tuple format: {final_result_to_serialize}")
            else:
                final_result_to_serialize = result_dict
                bound_logger.debug(f"Using dictionary part of tuple: {final_result_to_serialize}")
        
        # Check if the result is a list (potentially multiple TextContent objects)
        elif isinstance(tool_result_mcp_format, list):
            parsed_list = []
            for item in tool_result_mcp_format:
                if hasattr(item, 'type') and item.type == 'text' and hasattr(item, 'text'):
                    try:
                        parsed_item = json.loads(item.text)
                        parsed_list.append(parsed_item)
                    except json.JSONDecodeError:
                        # If text isn't JSON, append the raw text
                        parsed_list.append(item.text)
                        bound_logger.warning(f"List item TextContent for tool '{tool_name}' was not valid JSON. Appending as plain text.")
                # Handle other potential list item types if necessary (e.g., ImageContent raw data?)
                else:
                    parsed_list.append(item) # Append raw item if not TextContent
                    bound_logger.warning(f"Unexpected item type {type(item)} in MCP result list for tool '{tool_name}'. Appending raw item.")
            final_result_to_serialize = parsed_list
            bound_logger.debug(f"Parsed list from MCP TextContent objects: {final_result_to_serialize}")
        
        # Handle case where result is a single object (e.g., single TextContent)
        elif hasattr(tool_result_mcp_format, 'type') and tool_result_mcp_format.type == 'text' and hasattr(tool_result_mcp_format, 'text'):
             try:
                 # Assume the text content is the JSON representation of the actual result
                 final_result_to_serialize = json.loads(tool_result_mcp_format.text)
                 bound_logger.debug(f"Extracted and parsed JSON from single TextContent: {final_result_to_serialize}")
             except json.JSONDecodeError as json_err:
                 # If it's not JSON, maybe it's just plain text?
                 final_result_to_serialize = tool_result_mcp_format.text
                 bound_logger.warning(f"Single TextContent for tool '{tool_name}' was not valid JSON ({json_err}). Returning as plain text.")
        
        # Handle other potential single result types (ImageContent, etc.) if needed
        else:
            # If the result wasn't a list or known single content type, assign it directly
            final_result_to_serialize = tool_result_mcp_format
            bound_logger.debug(f"Tool '{tool_name}' did not return standard MCP list/TextContent format. Using raw result: {final_result_to_serialize}")
        # -----------------------------------------------------
        
        # Ensure the extracted result is JSON serializable
        try:
            # Attempt to serialize to catch issues early
            json.dumps(final_result_to_serialize)
            # Return the extracted and potentially parsed result
            return final_result_to_serialize 
        except TypeError as json_err:
            bound_logger.error(f"Final tool '{tool_name}' result is not JSON serializable: {json_err}", exc_info=True)
            bound_logger.error(f"Problematic final result data structure: {final_result_to_serialize}")
            raise HTTPException(status_code=500, detail=f"Tool execution succeeded but result is not serializable.")

    except ValueError as e:
        # Catch specific errors like invalid server ID from get_nifi_client
        bound_logger.error(f"Value error during tool execution: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except NiFiAuthenticationError as e:
        bound_logger.error(f"NiFi authentication failed for server {nifi_server_id}: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail=f"Failed to authenticate with NiFi server: {nifi_server_id}")
    except ToolError as e:
        # Catch errors specifically raised by tools
        bound_logger.warning(f"Tool '{tool_name}' raised an error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except McpError as e:
        # Catch other MCP related errors (tool not found, etc.)
        bound_logger.error(f"MCP error during tool execution: {e}", exc_info=True)
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        else:
            raise HTTPException(status_code=500, detail=f"MCP Error: {e}")
    except Exception as e:
        # Catch-all for unexpected errors
        bound_logger.error(f"Unexpected error executing tool '{tool_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error during tool execution.")
    finally:
        # --- Reset ContextVars --- #
        if client_token:
            current_nifi_client.reset(client_token)
            bound_logger.trace("Reset NiFi client context variable.")
        if logger_token:
            current_request_logger.reset(logger_token)
            bound_logger.trace("Reset request logger context variable.")
        # ------------------------ #
        # --- Clean up NiFi Client --- #
        if nifi_client:
            bound_logger.debug(f"Closing NiFi client connection for server ID: {nifi_server_id}")
            await nifi_client.close() # Ensure connection is closed after request
        # -------------------------- #

# --- Workflow Endpoints --- #

from pydantic import BaseModel

class WorkflowExecutionPayload(BaseModel):
    workflow_name: str
    initial_context: Optional[Dict[str, Any]] = None
    context: Optional[ContextModel] = None

@app.get("/workflows", response_model=List[Dict[str, Any]], tags=["Workflows"])
async def list_workflows(
    request: Request,
    category: Optional[str] = Query(None),
    phase: Optional[str] = Query(None),
    enabled_only: bool = Query(True)
):
    """List available workflows with optional filtering."""
    user_request_id = request.state.user_request_id
    action_id = request.state.action_id
    bound_logger = logger.bind(user_request_id=user_request_id, action_id=action_id)
    
    bound_logger.info(f"Request received for /workflows (category={category}, phase={phase}, enabled_only={enabled_only})")
    
    try:
        # Import workflows to ensure registration
        from nifi_mcp_server.workflows import get_workflow_registry
        
        registry = get_workflow_registry()
        workflows = registry.list_workflows(category=category, phase=phase, enabled_only=enabled_only)
        
        result = [workflow.to_dict() for workflow in workflows]
        bound_logger.info(f"Returning {len(result)} workflows")
        return result
        
    except Exception as e:
        bound_logger.error(f"Error retrieving workflows: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error retrieving workflows")

@app.get("/workflows/{workflow_name}", response_model=Dict[str, Any], tags=["Workflows"])
async def get_workflow_info(
    workflow_name: str,
    request: Request
):
    """Get detailed information about a specific workflow."""
    user_request_id = request.state.user_request_id
    action_id = request.state.action_id
    bound_logger = logger.bind(user_request_id=user_request_id, action_id=action_id, workflow_name=workflow_name)
    
    bound_logger.info(f"Request received for workflow info: {workflow_name}")
    
    try:
        from nifi_mcp_server.workflows import get_workflow_registry
        
        registry = get_workflow_registry()
        workflow_info = registry.get_workflow_info(workflow_name)
        
        if not workflow_info:
            bound_logger.warning(f"Workflow not found: {workflow_name}")
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_name}' not found")
            
        bound_logger.info(f"Returning workflow info for: {workflow_name}")
        return workflow_info
        
    except HTTPException:
        raise
    except Exception as e:
        bound_logger.error(f"Error retrieving workflow info for {workflow_name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error retrieving workflow info")

@app.post("/workflows/execute", tags=["Workflows"])
async def execute_workflow(
    payload: WorkflowExecutionPayload,
    request: Request,
    nifi_server_id: Optional[str] = Header(None, alias="X-Nifi-Server-Id")
):
    """Execute a workflow with the specified context."""
    user_request_id = request.state.user_request_id
    action_id = request.state.action_id
    workflow_name = payload.workflow_name
    bound_logger = logger.bind(
        user_request_id=user_request_id, 
        action_id=action_id,
        workflow_name=workflow_name,
        nifi_server_id=nifi_server_id
    )
    
    bound_logger.info(f"Request received to execute workflow: {workflow_name}")
    
    # --- NiFi Server ID Check --- #
    if not get_nifi_servers():
        bound_logger.error("Cannot execute workflow: No NiFi servers are configured in config.yaml.")
        raise HTTPException(status_code=503, detail="No NiFi servers configured on the server.")

    if not nifi_server_id:
        bound_logger.warning("Missing X-Nifi-Server-Id header.")
        raise HTTPException(status_code=400, detail="Missing required header: X-Nifi-Server-Id")
    # -------------------------- #
    
    nifi_client = None
    client_token = None
    logger_token = None
    
    try:
        # --- Get NiFi Client for this request --- #
        bound_logger.debug(f"Attempting to get NiFi client for server ID: {nifi_server_id}")
        nifi_client = await get_nifi_client(nifi_server_id, bound_logger=bound_logger)
        bound_logger.debug(f"Successfully obtained authenticated NiFi client for {nifi_server_id}")
        # -------------------------------------- #
        
        # --- Set ContextVars --- #
        client_token = current_nifi_client.set(nifi_client)
        logger_token = current_request_logger.set(bound_logger)
        bound_logger.trace("Set NiFi client and logger in context variables.")
        # ----------------------- #
        
        # --- Execute the workflow --- #
        from nifi_mcp_server.workflows import get_workflow_registry
        
        registry = get_workflow_registry()
        executor = registry.create_executor(workflow_name)
        
        if not executor:
            bound_logger.warning(f"Could not create executor for workflow: {workflow_name}")
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_name}' not found or disabled")
            
        # Prepare initial context
        initial_context = payload.initial_context or {}
        initial_context.update({
            "user_request_id": user_request_id,
            "action_id": action_id,
            "nifi_server_id": nifi_server_id,
            "workflow_name": workflow_name
        })
        
        bound_logger.info(f"Executing workflow: {workflow_name}")
        result = executor.execute(initial_context=initial_context)
        
        bound_logger.info(f"Workflow execution completed: {workflow_name} - {result.get('status', 'unknown')}")
        return result
        
    except HTTPException:
        raise
    except ValueError as e:
        bound_logger.error(f"Value error during workflow execution: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except NiFiAuthenticationError as e:
        bound_logger.error(f"NiFi authentication failed for server {nifi_server_id}: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail=f"Failed to authenticate with NiFi server: {nifi_server_id}")
    except Exception as e:
        bound_logger.error(f"Unexpected error executing workflow '{workflow_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error during workflow execution")
    finally:
        # --- Reset ContextVars --- #
        if client_token:
            current_nifi_client.reset(client_token)
            bound_logger.trace("Reset NiFi client context variable.")
        if logger_token:
            current_request_logger.reset(logger_token)
            bound_logger.trace("Reset request logger context variable.")
        # ------------------------ #
        # --- Clean up NiFi Client --- #
        if nifi_client:
            bound_logger.debug(f"Closing NiFi client connection for server ID: {nifi_server_id}")
            await nifi_client.close()
        # -------------------------- #

@app.get("/workflows/validate/{workflow_name}", response_model=Dict[str, Any], tags=["Workflows"])
async def validate_workflow(
    workflow_name: str,
    request: Request
):
    """Validate a workflow definition."""
    user_request_id = request.state.user_request_id
    action_id = request.state.action_id
    bound_logger = logger.bind(user_request_id=user_request_id, action_id=action_id, workflow_name=workflow_name)
    
    bound_logger.info(f"Request received for workflow validation: {workflow_name}")
    
    try:
        from nifi_mcp_server.workflows import get_workflow_registry
        
        registry = get_workflow_registry()
        validation_result = registry.validate_workflow(workflow_name)
        
        bound_logger.info(f"Workflow validation completed: {workflow_name} - {'valid' if validation_result['valid'] else 'invalid'}")
        return validation_result
        
    except Exception as e:
        bound_logger.error(f"Error validating workflow {workflow_name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error during workflow validation")

# --- End Workflow Endpoints --- #

# Run with uvicorn if this module is run directly
if __name__ == "__main__":
    import uvicorn
    # Disable default access logs to potentially reduce noise/interleaving
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000, 
        log_level="info", # Keep uvicorn's own level if desired
        access_log=False # Disable standard access log lines
    )
