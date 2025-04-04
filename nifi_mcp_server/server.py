import asyncio
import logging
import signal # Add signal import for cleanup
from typing import List, Dict, Optional, Any
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body
from fastapi.responses import JSONResponse # Import JSONResponse
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import os
import sys

# Import our NiFi API client and exception (Absolute Import)
from nifi_mcp_server.nifi_client import NiFiClient, NiFiAuthenticationError
# Import flow documentation tools
from nifi_mcp_server.flow_documenter import (
    extract_important_properties,
    analyze_expressions,
    build_graph_structure,
    format_connection,
    find_source_to_sink_paths,
    find_decision_branches
)

# Import MCP server components (Corrected for v1.6.0)
from mcp.server import FastMCP
# Remove non-existent imports
# from mcp.context import ToolContext 
# from mcp.shared.types import ToolExecutionResult
# Corrected error import path based on file inspection for v1.6.0
from mcp.shared.exceptions import McpError # Base error
from mcp.server.fastmcp.exceptions import ToolError # Tool-specific errors

# Configure logging for the server - Set level to DEBUG
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("nifi_mcp_server")
logger.debug("nifi_mcp_server logger initialized with DEBUG level.")

# Load .env file at module level for potential Uvicorn execution
load_dotenv()

# --- Server Setup ---

# Initialize FastMCP server - name should be descriptive
# Apply version-specific workarounds for MCP 1.6.0 based on Perplexity analysis
mcp = FastMCP(
    "nifi_controller",
    description="An MCP server to interact with Apache NiFi.",
    protocol_version="2024-09-01",  # Explicitly set protocol version
    type_validation_mode="compat",  # Use compatibility mode for type validation
    # json_serializer=lambda x: x     # REMOVED: Let MCP handle default serialization
)

# Instantiate our NiFi API client (uses environment variables for config)
# Consider a more robust way to handle client lifecycle if needed
try:
    nifi_api_client = NiFiClient()
    logger.info("NiFi API Client instantiated.")
except ValueError as e:
    logger.error(f"Failed to instantiate NiFiClient: {e}. Ensure NIFI_API_URL is set.")
    # Decide how to handle this - maybe exit or have tools return errors
    nifi_api_client = None # Mark as unavailable

# --- Helper Function for Authentication (Keep for potential future use, but commented tools won't call it) ---

async def ensure_authenticated():
    """Helper to ensure the NiFi client is authenticated before tool use."""
    if nifi_api_client is None:
        raise ToolError("NiFi Client is not configured properly (check NIFI_API_URL).")
    if not nifi_api_client.is_authenticated:
        logger.info("NiFi client not authenticated. Attempting authentication...")
        try:
            await nifi_api_client.authenticate()
            logger.info("Authentication successful via MCP tool request.")
        except NiFiAuthenticationError as e:
            logger.error(f"Authentication failed during tool execution: {e}")
            # Raise ToolError, but indicate user action needed in the message
            raise ToolError(
                f"NiFi authentication failed ({e}). Please ensure NIFI_USERNAME and NIFI_PASSWORD "
                "are correctly set in the server's environment/.env file."
            ) from e
        except Exception as e:
            logger.error(f"Unexpected error during authentication: {e}", exc_info=True)
            raise ToolError(f"An unexpected error occurred during NiFi authentication: {e}")
    pass # Add pass to avoid syntax error if body is empty


# --- NiFi Tools ---
@mcp.tool()
async def list_nifi_processors(
    process_group_id: str | None = None # Use pipe syntax
) -> list: # Changed return type hint from str to list
    """
    Lists processors within a specified NiFi process group.

    If process_group_id is not provided, it will attempt to list processors
    in the root process group.

    Args:
        process_group_id: The UUID of the process group to inspect. Defaults to the root group if None.

    Returns:
        A list of dictionaries, where each dictionary
        represents a processor found in the specified process group.
    """
    await ensure_authenticated() # Ensure we are logged in

    target_pg_id = process_group_id
    if target_pg_id is None:
        logger.info("No process_group_id provided, fetching root process group ID.")
        try:
            target_pg_id = await nifi_api_client.get_root_process_group_id()
        except Exception as e:
            logger.error(f"Failed to get root process group ID: {e}", exc_info=True)
            raise ToolError(f"Failed to determine root process group ID: {e}")

    logger.info(f"Executing list_nifi_processors for group: {target_pg_id}")
    try:
        # The nifi_api_client.list_processors is expected to return the list directly
        processors_list = await nifi_api_client.list_processors(target_pg_id)
        # Ensure it's actually a list
        if not isinstance(processors_list, list):
            logger.error(f"API client list_processors did not return a list. Got: {type(processors_list)}")
            raise ToolError("Unexpected data format received from NiFi API client for list_processors.")
        
        # Filter the processor data to include only essential fields
        filtered_processors = [filter_processor_data(processor) for processor in processors_list]
        
        # Return the filtered list directly, let MCP handle serialization
        return filtered_processors # Return the filtered list
    except (NiFiAuthenticationError, ConnectionError) as e:
        logger.error(f"API error listing processors: {e}", exc_info=True)
        # Convert NiFi client errors to MCP errors
        raise ToolError(f"Failed to list NiFi processors: {e}")
    except Exception as e:
        logger.error(f"Unexpected error listing processors: {e}", exc_info=True)
        raise ToolError(f"An unexpected error occurred: {e}")

def filter_processor_data(processor):
    """Extract only the essential fields from a processor object"""
    return {
        "id": processor.get("id"),
        "name": processor.get("component", {}).get("name"),
        "type": processor.get("component", {}).get("type"),
        "state": processor.get("component", {}).get("state"),
        "position": processor.get("position"),
        "runStatus": processor.get("status", {}).get("runStatus"),
        "validationStatus": processor.get("component", {}).get("validationStatus"),
        "relationships": [rel.get("name") for rel in processor.get("component", {}).get("relationships", [])],
        "inputRequirement": processor.get("component", {}).get("inputRequirement"),
        "bundle": processor.get("component", {}).get("bundle"),
    }

@mcp.tool()
async def create_nifi_processor(
    processor_type: str,
    name: str,
    position_x: int,
    position_y: int,
    process_group_id: str | None = None, # Use pipe syntax
    # Add config later if needed
    # config: Optional[Dict[str, Any]] = None
) -> Dict:
    """
    Creates a new processor within a specified NiFi process group.

    If process_group_id is not provided, it will attempt to create the processor
    in the root process group.

    Args:
        processor_type: The fully qualified Java class name of the processor type (e.g., "org.apache.nifi.processors.standard.GenerateFlowFile").
        name: The desired name for the new processor instance.
        position_x: The desired X coordinate for the processor on the canvas.
        position_y: The desired Y coordinate for the processor on the canvas.
        process_group_id: The UUID of the process group where the processor should be created. Defaults to the root group if None.
        # config: An optional dictionary representing the processor's configuration properties.

    Returns:
        A dictionary representing the result, including status and the created entity.
    """
    await ensure_authenticated() # Ensure we are logged in

    target_pg_id = process_group_id
    if target_pg_id is None:
        logger.info("No process_group_id provided for creation, fetching root process group ID.")
        try:
            target_pg_id = await nifi_api_client.get_root_process_group_id()
        except Exception as e:
            logger.error(f"Failed to get root process group ID: {e}", exc_info=True)
            raise ToolError(f"Failed to determine root process group ID for creation: {e}")

    position = {"x": position_x, "y": position_y}
    logger.info(f"Executing create_nifi_processor: Type='{processor_type}', Name='{name}', Position={position} in group: {target_pg_id}")

    try:
        processor_entity = await nifi_api_client.create_processor(
            process_group_id=target_pg_id,
            processor_type=processor_type,
            name=name,
            position=position,
            config=None # Add config dict here when implemented
        )
        logger.info(f"Successfully created processor '{name}' with ID: {processor_entity.get('id', 'N/A')}")
        
        # Check validation status from the response
        component = processor_entity.get("component", {})
        validation_status = component.get("validationStatus", "UNKNOWN")
        validation_errors = component.get("validationErrors", [])
        
        if validation_status == "VALID":
            return {
                "status": "success",
                "message": f"Processor '{name}' created successfully.",
                "entity": processor_entity
            }
        else:
            error_msg_snippet = f" ({validation_errors[0]})" if validation_errors else ""
            logger.warning(f"Processor '{name}' created but is {validation_status}{error_msg_snippet}. Requires configuration or connections.")
            return {
                "status": "warning",
                "message": f"Processor '{name}' created but is currently {validation_status}{error_msg_snippet}. Further configuration or connections likely required.",
                "entity": processor_entity
            }
            
    except (NiFiAuthenticationError, ConnectionError, ValueError) as e: # Include ValueError for potential client-side validation issues
        logger.error(f"API error creating processor: {e}", exc_info=True)
        # raise ToolError(f"Failed to create NiFi processor: {e}")
        # Return error status
        return {"status": "error", "message": f"Failed to create NiFi processor: {e}", "entity": None}
    except Exception as e:
        logger.error(f"Unexpected error creating processor: {e}", exc_info=True)
        # raise ToolError(f"An unexpected error occurred during processor creation: {e}")
        return {"status": "error", "message": f"An unexpected error occurred during processor creation: {e}", "entity": None}


@mcp.tool()
async def create_nifi_connection(
    source_id: str,
    source_relationship: str,
    target_id: str,
    process_group_id: str | None = None, # Use pipe syntax
    # Add more options like selected_relationships if needed
) -> Dict:
    """
    Creates a connection between two components within a specified NiFi process group.

    Args:
        source_id: The UUID of the source component (processor, port, etc.).
        source_relationship: The name of the relationship originating from the source.
        target_id: The UUID of the target component.
        process_group_id: The UUID of the process group containing the components. Defaults to the root group if None.

    Returns:
        A dictionary representing the created connection entity.
    """
    await ensure_authenticated()

    target_pg_id = process_group_id
    if target_pg_id is None:
        logger.info("No process_group_id provided for connection, fetching root process group ID.")
        try:
            target_pg_id = await nifi_api_client.get_root_process_group_id()
        except Exception as e:
            logger.error(f"Failed to get root process group ID for connection: {e}", exc_info=True)
            raise ToolError(f"Failed to determine root process group ID for connection: {e}")

    relationships = [source_relationship] # API expects a list
    logger.info(f"Executing create_nifi_connection: From {source_id} ({source_relationship}) To {target_id} in group {target_pg_id}")

    try:
        connection_entity = await nifi_api_client.create_connection(
            process_group_id=target_pg_id,
            source_id=source_id,
            target_id=target_id,
            relationships=relationships
        )
        logger.info(f"Successfully created connection with ID: {connection_entity.get('id', 'N/A')}")
        return connection_entity # Return the connection details

    except (NiFiAuthenticationError, ConnectionError, ValueError) as e:
        logger.error(f"API error creating connection: {e}", exc_info=True)
        raise ToolError(f"Failed to create NiFi connection: {e}")
    except Exception as e:
        logger.error(f"Unexpected error creating connection: {e}", exc_info=True)
        raise ToolError(f"An unexpected error occurred during connection creation: {e}")


@mcp.tool()
async def get_nifi_processor_details(processor_id: str) -> dict:
    """
    Retrieves the details and configuration of a specific processor.

    Args:
        processor_id: The UUID of the processor to retrieve.

    Returns:
        A dictionary containing the processor's entity (details, config, revision, etc.).
        Raises ToolError if the processor is not found or an API error occurs.
    """
    await ensure_authenticated()

    logger.info(f"Executing get_nifi_processor_details for processor ID: {processor_id}")
    try:
        processor_entity = await nifi_api_client.get_processor_details(processor_id)
        # The client method should raise ValueError if not found, which we catch below
        logger.info(f"Successfully retrieved details for processor {processor_id}")
        return processor_entity # Return the full entity

    except ValueError as e: # Specific catch for 'processor not found'
        logger.warning(f"Processor with ID {processor_id} not found: {e}")
        raise ToolError(f"Processor not found: {e}") from e
    except (NiFiAuthenticationError, ConnectionError) as e:
        logger.error(f"API error getting processor details: {e}", exc_info=True)
        raise ToolError(f"Failed to get NiFi processor details: {e}")
    except Exception as e:
        logger.error(f"Unexpected error getting processor details: {e}", exc_info=True)
        raise ToolError(f"An unexpected error occurred getting processor details: {e}")


@mcp.tool()
async def delete_nifi_processor(processor_id: str, version: int) -> dict:
    """
    Deletes a specific processor.

    Requires the processor ID and the current revision version to prevent conflicts.

    Args:
        processor_id: The UUID of the processor to delete.
        version: The current revision version of the processor.

    Returns:
        A dictionary indicating success or failure.
    """
    await ensure_authenticated()

    logger.info(f"Executing delete_nifi_processor for ID: {processor_id} at version {version}")
    try:
        success = await nifi_api_client.delete_processor(processor_id, version)
        if success:
            logger.info(f"Successfully deleted processor {processor_id}")
            return {"status": "success", "message": f"Processor {processor_id} deleted."}
        else:
            # This case might not be reachable if the client raises exceptions on failure
            logger.warning(f"NiFi API client reported failure deleting processor {processor_id}, but did not raise exception.")
            return {"status": "error", "message": f"Deletion failed for processor {processor_id} according to API client."}

    except ValueError as e: # Catch 'not found' or 'conflict'
        logger.warning(f"Error deleting processor {processor_id}: {e}")
        # Distinguish between not found and conflict if possible from the error message
        if "conflict" in str(e).lower():
            raise ToolError(f"Conflict deleting processor {processor_id}: Check revision version ({version}). {e}") from e
        else:
            raise ToolError(f"Processor not found or other error: {e}") from e
    except (NiFiAuthenticationError, ConnectionError) as e:
        logger.error(f"API error deleting processor: {e}", exc_info=True)
        raise ToolError(f"Failed to delete NiFi processor: {e}")
    except Exception as e:
        logger.error(f"Unexpected error deleting processor: {e}", exc_info=True)
        raise ToolError(f"An unexpected error occurred during processor deletion: {e}")


@mcp.tool()
async def delete_nifi_connection(connection_id: str, version: int) -> dict:
    """
    Deletes a specific connection.

    Requires the connection ID and the current revision version.

    Args:
        connection_id: The UUID of the connection to delete.
        version: The current revision version of the connection.

    Returns:
        A dictionary indicating success or failure.
    """
    await ensure_authenticated()

    logger.info(f"Executing delete_nifi_connection for ID: {connection_id} at version {version}")
    try:
        success = await nifi_api_client.delete_connection(connection_id, version)
        if success:
            logger.info(f"Successfully deleted connection {connection_id}")
            return {"status": "success", "message": f"Connection {connection_id} deleted."}
        else:
            logger.warning(f"NiFi API client reported failure deleting connection {connection_id}, but did not raise exception.")
            return {"status": "error", "message": f"Deletion failed for connection {connection_id} according to API client."}

    except ValueError as e: # Catch 'not found' or 'conflict'
        logger.warning(f"Error deleting connection {connection_id}: {e}")
        if "conflict" in str(e).lower():
            raise ToolError(f"Conflict deleting connection {connection_id}: Check revision version ({version}). {e}") from e
        else:
            raise ToolError(f"Connection not found or other error: {e}") from e
    except (NiFiAuthenticationError, ConnectionError) as e:
        logger.error(f"API error deleting connection: {e}", exc_info=True)
        raise ToolError(f"Failed to delete NiFi connection: {e}")
    except Exception as e:
        logger.error(f"Unexpected error deleting connection: {e}", exc_info=True)
        raise ToolError(f"An unexpected error occurred during connection deletion: {e}")


@mcp.tool()
async def update_nifi_processor_config(
    processor_id: str,
    config_properties: Dict[str, Any]
    # Add state? scheduled: Optional[bool] = None
) -> Dict:
    """
    Updates the configuration properties of a specific processor.

    This requires fetching the current processor revision first.

    Args:
        processor_id: The UUID of the processor to update.
        config_properties: A dictionary where keys are property names and values are the desired settings.

    Returns:
        A dictionary representing the updated processor entity or an error status.
    """
    await ensure_authenticated()

    logger.info(f"Executing update_nifi_processor_config for ID: {processor_id} with properties: {config_properties}")
    try:
        # The client method handles getting revision and making the update
        updated_entity = await nifi_api_client.update_processor_config(
            processor_id=processor_id,
            config_updates=config_properties
        )
        logger.info(f"Successfully updated configuration for processor {processor_id}")
        
        # Check validation status
        component = updated_entity.get("component", {})
        validation_status = component.get("validationStatus", "UNKNOWN")
        validation_errors = component.get("validationErrors", [])
        name = component.get("name", processor_id)
        
        if validation_status == "VALID":
            return {
                "status": "success",
                "message": f"Processor '{name}' configuration updated successfully.",
                "entity": updated_entity
            }
        else:
            error_msg_snippet = f" ({validation_errors[0]})" if validation_errors else ""
            logger.warning(f"Processor '{name}' configuration updated, but validation status is {validation_status}{error_msg_snippet}.")
            return {
                "status": "warning",
                "message": f"Processor '{name}' configuration updated, but validation status is {validation_status}{error_msg_snippet}. Check configuration.",
                "entity": updated_entity
            }

    except ValueError as e: # Catch 'not found' or 'conflict'
        logger.warning(f"Error updating processor config {processor_id}: {e}")
        if "conflict" in str(e).lower():
            # raise ToolError(f"Conflict updating processor {processor_id}: {e}") from e
            return {"status": "error", "message": f"Conflict updating processor {processor_id}: {e}", "entity": None}
        else:
            # raise ToolError(f"Processor not found or other value error updating config: {e}") from e
            return {"status": "error", "message": f"Processor not found or other error updating config: {e}", "entity": None}
    except (NiFiAuthenticationError, ConnectionError) as e:
        logger.error(f"API error updating processor config: {e}", exc_info=True)
        # raise ToolError(f"Failed to update NiFi processor config: {e}")
        return {"status": "error", "message": f"Failed to update NiFi processor config: {e}", "entity": None}
    except Exception as e:
        logger.error(f"Unexpected error updating processor config: {e}", exc_info=True)
        # raise ToolError(f"An unexpected error occurred during processor config update: {e}")
        return {"status": "error", "message": f"An unexpected error occurred during processor config update: {e}", "entity": None}


@mcp.tool()
async def start_nifi_processor(processor_id: str) -> Dict:
    """
    Starts a specific processor.

    Args:
        processor_id: The UUID of the processor to start.

    Returns:
        A dictionary indicating the status (success, warning, error) and the updated entity.
    """
    await ensure_authenticated()
    logger.info(f"Executing start_nifi_processor for ID: {processor_id}")
    try:
        updated_entity = await nifi_api_client.update_processor_state(processor_id, "RUNNING")
        # Check the actual state returned
        component = updated_entity.get("component", {})
        current_state = component.get("state")
        name = component.get("name", processor_id)
        validation_status = component.get("validationStatus", "UNKNOWN")
        
        if current_state == "RUNNING":
             logger.info(f"Successfully started processor '{name}'.")
             return {"status": "success", "message": f"Processor '{name}' started successfully.", "entity": updated_entity}
        elif current_state == "DISABLED" or validation_status != "VALID":
             logger.warning(f"Processor '{name}' could not be started. Current state: {current_state}, Validation: {validation_status}.")
             return {"status": "warning", "message": f"Processor '{name}' could not be started (State: {current_state}, Validation: {validation_status}). Check configuration and dependencies.", "entity": updated_entity}
        else:
             logger.warning(f"Processor '{name}' state is {current_state} after start request. Expected RUNNING.")
             return {"status": "warning", "message": f"Processor '{name}' is {current_state} after start request. Check NiFi UI for details.", "entity": updated_entity}

    except ValueError as e: # Not found / Invalid state
        logger.warning(f"Error starting processor {processor_id}: {e}")
        # raise ToolError(f"Could not start processor {processor_id}: {e}") from e
        return {"status": "error", "message": f"Could not start processor {processor_id}: {e}", "entity": None}
    except (NiFiAuthenticationError, ConnectionError) as e:
        logger.error(f"API error starting processor: {e}", exc_info=True)
        # raise ToolError(f"Failed to start NiFi processor: {e}")
        return {"status": "error", "message": f"Failed to start NiFi processor: {e}", "entity": None}
    except Exception as e:
        logger.error(f"Unexpected error starting processor: {e}", exc_info=True)
        # raise ToolError(f"An unexpected error occurred starting processor: {e}")
        return {"status": "error", "message": f"An unexpected error occurred starting processor: {e}", "entity": None}

@mcp.tool()
async def stop_nifi_processor(processor_id: str) -> Dict:
    """
    Stops a specific processor.

    Args:
        processor_id: The UUID of the processor to stop.

    Returns:
        A dictionary indicating the status (success, warning, error) and the updated entity.
    """
    await ensure_authenticated()
    logger.info(f"Executing stop_nifi_processor for ID: {processor_id}")
    try:
        updated_entity = await nifi_api_client.update_processor_state(processor_id, "STOPPED")
        # Check the actual state returned
        component = updated_entity.get("component", {})
        current_state = component.get("state")
        name = component.get("name", processor_id)

        if current_state == "STOPPED":
             logger.info(f"Successfully stopped processor '{name}'.")
             return {"status": "success", "message": f"Processor '{name}' stopped successfully.", "entity": updated_entity}
        else:
             # This might happen if it was already stopped or disabled
             logger.warning(f"Processor '{name}' state is {current_state} after stop request. Expected STOPPED.")
             # Consider returning success if already stopped, but warning is safer
             return {"status": "warning", "message": f"Processor '{name}' is {current_state} after stop request. Check NiFi UI for details.", "entity": updated_entity}

    except ValueError as e: # Not found / Invalid state
        logger.warning(f"Error stopping processor {processor_id}: {e}")
        # raise ToolError(f"Could not stop processor {processor_id}: {e}") from e
        return {"status": "error", "message": f"Could not stop processor {processor_id}: {e}", "entity": None}
    except (NiFiAuthenticationError, ConnectionError) as e:
        logger.error(f"API error stopping processor: {e}", exc_info=True)
        # raise ToolError(f"Failed to stop NiFi processor: {e}")
        return {"status": "error", "message": f"Failed to stop NiFi processor: {e}", "entity": None}
    except Exception as e:
        logger.error(f"Unexpected error stopping processor: {e}", exc_info=True)
        # raise ToolError(f"An unexpected error occurred stopping processor: {e}")
        return {"status": "error", "message": f"An unexpected error occurred stopping processor: {e}", "entity": None}


# === Add a Simple Dummy Tool ===
@mcp.tool()
async def ping_test(message: str) -> str:
    """A simple async test tool that echoes a message."""
    logger.info(f"Executing async ping_test with message: {message}")
    # Simulate async work if needed, but not necessary for this test
    # await asyncio.sleep(0.01)
    return f"Pong: {message}"

# Remove FastAPI integration
# app = FastAPI(...) and websocket_endpoint and middleware

# Keep cleanup function
async def cleanup():
    """Perform cleanup tasks on server shutdown."""
    if nifi_api_client:
        logger.info("Closing NiFi API client connection.")
        await nifi_api_client.close()

# === FastAPI Application Setup === #
app = FastAPI(
    title="NiFi MCP REST Bridge", 
    description="Exposes NiFi MCP tools via a REST API."
)

# --- CORS Middleware --- #
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permissive for now
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- FastAPI Event Handlers --- #
@app.on_event("startup")
async def startup_event():
    logger.info("FastAPI server starting up...")
    # Perform initial authentication check
    try:
        await ensure_authenticated()
        logger.info("Initial NiFi authentication successful.")
    except Exception as e:
        logger.error(f"Initial NiFi authentication failed on startup: {e}", exc_info=True)
        # Depending on requirements, you might want to prevent startup
        # raise RuntimeError("NiFi authentication failed, cannot start server.") from e

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("FastAPI server shutting down...")
    await cleanup()

# --- REST API Endpoints --- #

@app.get("/tools", response_model=List[Dict[str, Any]])
async def get_tools():
    """Retrieve the list of available MCP tools in OpenAI function format."""
    try:
        logger.debug(f"Inspecting mcp object attributes: {dir(mcp)}") # Keep debug for now
        formatted_tools = []
        # Access the ToolManager instance
        tool_manager = getattr(mcp, '_tool_manager', None)
        if tool_manager:
            # Call the ToolManager's list_tools method
            tools_info = tool_manager.list_tools() # Assuming this returns ToolInfo objects or similar
            
            for tool_info in tools_info: 
                tool_name = getattr(tool_info, 'name', 'unknown')
                tool_description = getattr(tool_info, 'description', '')
                # Extract only the necessary parts for the schema
                raw_params_schema = getattr(tool_info, 'parameters', {})
                
                # Build the schema explicitly for OpenAI/Gemini compatibility
                parameters_schema = {
                    "type": "object",
                    "properties": {}, # Initialize empty properties
                }
                raw_properties = raw_params_schema.get('properties', {})
                
                # Iterate through properties and clean them
                cleaned_properties = {}
                if isinstance(raw_properties, dict):
                    for prop_name, prop_schema in raw_properties.items():
                        if isinstance(prop_schema, dict):
                            # Create a copy to avoid modifying the original
                            cleaned_schema = prop_schema.copy()
                            # Remove problematic fields: anyOf, title, default, etc.
                            cleaned_schema.pop('anyOf', None) 
                            cleaned_schema.pop('title', None)
                            cleaned_schema.pop('default', None)  # Also remove default values
                            cleaned_properties[prop_name] = cleaned_schema
                        else:
                            # Handle cases where a property schema isn't a dict
                            logger.warning(f"Property '{prop_name}' in tool '{tool_name}' has non-dict schema: {prop_schema}. Skipping property.")
                
                parameters_schema["properties"] = cleaned_properties
                
                # Only include required if it's non-empty and properties exist
                required_list = raw_params_schema.get('required', [])
                if required_list and cleaned_properties: # Only add required if there are properties
                     parameters_schema["required"] = required_list
                elif "required" in parameters_schema: # Clean up just in case
                     del parameters_schema["required"]

                # Remove properties/required fields entirely if properties dict is empty
                if not parameters_schema["properties"]:
                     del parameters_schema["properties"]
                     if "required" in parameters_schema: del parameters_schema["required"]

                formatted_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": tool_description,
                        "parameters": parameters_schema # Use the cleaned schema
                    }
                })
            logger.info(f"Returning {len(formatted_tools)} tool definitions via ToolManager.")
            return formatted_tools
        else:
            logger.warning("Could not find ToolManager (_tool_manager) on MCP instance.")
            return []
    except Exception as e:
        logger.error(f"Error retrieving tool definitions via ToolManager: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error retrieving tools.")

# Define a Pydantic model for the request body, expecting arbitrary key-value pairs
from pydantic import BaseModel
class ToolExecutionPayload(BaseModel):
    arguments: Dict[str, Any]

@app.post("/tools/{tool_name}")
async def execute_tool(tool_name: str, payload: ToolExecutionPayload, context: Any | None = None) -> Dict[str, Any]:
    """Execute a specified MCP tool with the given arguments via ToolManager."""
    logger.info(f"Received request to execute tool '{tool_name}' via ToolManager with arguments: {payload.arguments}")
    
    tool_manager = getattr(mcp, '_tool_manager', None)
    if not tool_manager:
        logger.error("Could not find ToolManager on MCP instance during execution.")
        raise HTTPException(status_code=500, detail="Internal server configuration error: ToolManager not found.")

    # Check if tool exists using ToolManager (assuming it has a way to check/get)
    # Option A: Try/Except around call_tool
    # Option B: Check via list_tools result (less efficient)
    # Let's go with Option A for now.

    try:
        # Ensure NiFi client is authenticated before execution
        await ensure_authenticated() 
        
        # Call the ToolManager's call_tool method
        result = await tool_manager.call_tool(tool_name, payload.arguments, context=context)
            
        logger.info(f"Execution of tool '{tool_name}' via ToolManager successful.")
        
        # The result from call_tool might need conversion (similar to FastMCP.call_tool)
        # For now, assume it's the direct result we want.
        # TODO: Verify the return type of tool_manager.call_tool and convert if necessary.
        return {"result": result}
        
    except ToolError as e:
        # Log the specific ToolError details
        # Use str(e) to get the message and remove e.code as it might not exist
        logger.error(f"ToolError executing tool '{tool_name}' via ToolManager: {str(e)}", exc_info=True) 
        # Return 422 Unprocessable Entity for tool execution errors caused by bad input/state
        return JSONResponse(
            status_code=422, # Use 422 for semantic errors in the request
            content={"detail": f"Tool execution failed: {str(e)}"} # Use 'detail' key
        )
    except NiFiAuthenticationError as e:
         logger.error(f"NiFi Authentication Error during tool '{tool_name}' execution: {e}", exc_info=True)
         # For auth errors, 503 Service Unavailable might be suitable, or 401/403 if it's client-fixable
         # Let's stick with a client-side error code if the user needs to fix env vars.
         # Using 401 Unauthorized might imply the client needs to send credentials, which isn't the case here.
         # Using 403 Forbidden might be better if the server credentials are just wrong.
         # Let's use 403 for now.
         # raise HTTPException(status_code=503, detail=f"NiFi authentication failed: {e}") # 503 Service Unavailable
         return JSONResponse(
             status_code=403, 
             content={"detail": f"NiFi authentication failed: {str(e)}. Check server credentials."}
        ) 
    except Exception as e:
        logger.error(f"Unexpected error executing tool '{tool_name}' via ToolManager: {e}", exc_info=True)
        # Check if it's a context-related error
        if "Context is not available outside of a request" in str(e):
             logger.error(f"Tool '{tool_name}' likely requires context, which is unavailable in this REST setup.")
             # 501 Not Implemented is appropriate here
             return JSONResponse(status_code=501, detail=f"Tool '{tool_name}' cannot be executed via REST API as it requires MCP context.")
        # Catch potential argument mismatches or other runtime errors
        if isinstance(e, TypeError) and ("required positional argument" in str(e) or "unexpected keyword argument" in str(e)):
             # 422 is also appropriate for invalid arguments
             return JSONResponse(status_code=422, detail=f"Invalid arguments for tool '{tool_name}': {e}")
        # For truly unexpected errors, use 500
        # raise HTTPException(status_code=500, detail=f"Internal server error executing tool '{tool_name}'.")
        return JSONResponse(status_code=500, detail=f"Internal server error executing tool '{tool_name}'.")

@mcp.tool()
async def document_nifi_flow(
    process_group_id: str | None = None,
    starting_processor_id: str | None = None,
    max_depth: int = 10,
    include_properties: bool = True,
    include_descriptions: bool = True
) -> Dict[str, Any]:
    """
    Documents a NiFi flow by traversing processors and their connections.

    Args:
        process_group_id: The UUID of the process group to document. Defaults to the root group if None.
        starting_processor_id: The UUID of the processor to start the traversal from.
            If None, documents all processors in the process group.
        max_depth: Maximum depth to traverse from the starting processor. Defaults to 10.
        include_properties: Whether to include processor properties in the documentation. Defaults to True.
        include_descriptions: Whether to include processor descriptions in the documentation. Defaults to True.

    Returns:
        A dictionary containing the flow documentation, including:
        - processors: A list of processors and their configurations
        - connections: A list of connections between processors
        - graph_structure: The graph structure for traversal
        - common_paths: Pre-identified paths through the flow
        - decision_points: Branching points in the flow
        - parameters: Parameter context information (if available)
    """
    await ensure_authenticated()

    # Get data from NiFi
    target_pg_id = process_group_id
    if target_pg_id is None:
        logger.info("No process_group_id provided, fetching root process group ID.")
        try:
            target_pg_id = await nifi_api_client.get_root_process_group_id()
        except Exception as e:
            logger.error(f"Failed to get root process group ID: {e}", exc_info=True)
            raise ToolError(f"Failed to determine root process group ID: {e}")
    
    try:
        # Get all processors in the process group
        processors = await nifi_api_client.list_processors(target_pg_id)
        connections = await nifi_api_client.list_connections(target_pg_id)
        
        # Filter processors if starting_processor_id is provided
        filtered_processors = processors
        if starting_processor_id:
            # Find the starting processor
            start_processor = next((p for p in processors if p["id"] == starting_processor_id), None)
            if not start_processor:
                raise ToolError(f"Starting processor with ID {starting_processor_id} not found")
            
            # Build graph and perform traversal to find connected processors
            processor_map = {p["id"]: p for p in processors}
            graph = build_graph_structure(processors, connections)
            
            # Build a set of processor IDs to include (breadth-first search)
            included_processors = set([starting_processor_id])
            to_visit = [starting_processor_id]
            visited = set()
            depth = 0
            
            while to_visit and depth < max_depth:
                current_level = to_visit
                to_visit = []
                depth += 1
                
                for proc_id in current_level:
                    visited.add(proc_id)
                    
                    # Add outgoing connections
                    if proc_id in graph["outgoing"]:
                        for conn in graph["outgoing"][proc_id]:
                            dest_id = conn["destinationId"] if "destinationId" in conn else conn["destination"]["id"]
                            if dest_id not in visited and dest_id not in to_visit:
                                included_processors.add(dest_id)
                                to_visit.append(dest_id)
                    
                    # Add incoming connections
                    if proc_id in graph["incoming"]:
                        for conn in graph["incoming"][proc_id]:
                            src_id = conn["sourceId"] if "sourceId" in conn else conn["source"]["id"]
                            if src_id not in visited and src_id not in to_visit:
                                included_processors.add(src_id)
                                to_visit.append(src_id)
            
            # Filter processors and connections
            filtered_processors = [p for p in processors if p["id"] in included_processors]
            filtered_connections = [
                c for c in connections if 
                (c["sourceId"] if "sourceId" in c else c["source"]["id"]) in included_processors and
                (c["destinationId"] if "destinationId" in c else c["destination"]["id"]) in included_processors
            ]
        else:
            filtered_connections = connections
        
        # Enrich processor data with important properties and expressions
        enriched_processors = []
        for processor in filtered_processors:
            proc_data = {
                "id": processor["id"],
                "name": processor["component"]["name"],
                "type": processor["component"]["type"],
                "state": processor["component"]["state"],
                "position": processor["position"],
                "relationships": [r["name"] for r in processor["component"].get("relationships", [])],
                "validation_status": processor["component"].get("validationStatus", "UNKNOWN")
            }
            
            if include_properties:
                # Extract and analyze properties
                property_info = extract_important_properties(processor)
                proc_data["properties"] = property_info["key_properties"]
                proc_data["dynamic_properties"] = property_info["dynamic_properties"]
                
                # Analyze expressions
                proc_data["expressions"] = analyze_expressions(property_info["all_properties"])
            
            if include_descriptions:
                proc_data["description"] = processor["component"].get("config", {}).get("comments", "")
            
            enriched_processors.append(proc_data)
        
        # Build graph structure for the filtered processors
        processor_map = {p["id"]: p for p in filtered_processors}
        graph = build_graph_structure(filtered_processors, filtered_connections)
        
        # Find common paths and decision points
        paths = find_source_to_sink_paths(processor_map, graph)
        decision_points = find_decision_branches(processor_map, graph)
        
        # Format connections
        formatted_connections = [format_connection(c, processor_map) for c in filtered_connections]
        
        # Assemble result
        result = {
            "processors": enriched_processors,
            "connections": formatted_connections,
            "graph_structure": {
                "outgoing_count": {p_id: len(conns) for p_id, conns in graph["outgoing"].items()},
                "incoming_count": {p_id: len(conns) for p_id, conns in graph["incoming"].items()}
            },
            "common_paths": paths,
            "decision_points": decision_points
        }
        
        # Include parameter context if available
        if include_properties:
            parameters = await nifi_api_client.get_parameter_context(target_pg_id)
            if parameters:
                result["parameters"] = parameters
        
        return result
        
    except (NiFiAuthenticationError, ConnectionError) as e:
        logger.error(f"API error documenting flow: {e}", exc_info=True)
        raise ToolError(f"Failed to document NiFi flow: {e}")
    except Exception as e:
        logger.error(f"Unexpected error documenting flow: {e}", exc_info=True)
        raise ToolError(f"An unexpected error occurred while documenting the flow: {e}")

# Run with uvicorn if this module is run directly
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
