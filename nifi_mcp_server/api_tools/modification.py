import asyncio
from typing import List, Dict, Optional, Any, Union, Literal

# Import necessary components from parent/utils
from loguru import logger
# Import mcp ONLY
from ..core import mcp, handle_nifi_errors, _get_component_details_direct, _stop_pg_direct
# Removed nifi_api_client import
# Import context variables
from ..request_context import current_nifi_client, current_request_logger # Added
from config import settings as mcp_settings # Corrected import

from .utils import (
    tool_phases,
    # ensure_authenticated, # Removed
    filter_created_processor_data,
    filter_connection_data
)
from nifi_mcp_server.nifi_client import NiFiClient, NiFiAuthenticationError
from mcp.server.fastmcp.exceptions import ToolError

# --- Tool Definitions --- 

@mcp.tool()
@tool_phases(["Modify"])
async def update_nifi_processor_properties(
    processor_id: str,
    processor_config_properties: Dict[str, Any]
) -> Dict:
    """
    Updates a processor's configuration properties by *replacing* the existing property dictionary.

    Example:
    ```python
    {
        "processor_id": "123e4567-e89b-12d3-a456-426614174000",
        "processor_config_properties": {
            "Property1": "Value1",
            "Property2": "Value2",
            ...
        }
    }
    ```

    Args:
        processor_id: The UUID of the processor to update.
        processor_config_properties: A complete dictionary representing the desired final state of all properties. Cannot be empty.

    Returns:
        A dictionary representing the updated processor entity or an error status.
    """
    # Get client and logger from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
         
    # Authentication handled by factory

    if not processor_config_properties:
        error_msg = "The 'processor_config_properties' argument cannot be empty. Fetch current config first."
        local_logger.warning(f"Validation failed for update_nifi_processor_properties (ID={processor_id}): {error_msg}")
        raise ToolError(error_msg)

    if not isinstance(processor_config_properties, dict):
         raise ToolError(f"Invalid 'processor_config_properties' type. Expected dict, got {type(processor_config_properties)}.")

    # Handle potential accidental nesting (e.g., passing {"properties": {...}})
    if isinstance(processor_config_properties, dict) and \
       list(processor_config_properties.keys()) == ["properties"] and \
       isinstance(processor_config_properties["properties"], dict):
        original_input = processor_config_properties
        processor_config_properties = processor_config_properties["properties"]
        local_logger.warning(f"Detected nested 'properties' key in input for processor {processor_id}. Correcting structure.")

    local_logger = local_logger.bind(processor_id=processor_id)
    local_logger.info(f"Executing update_nifi_processor_properties with properties: {processor_config_properties}")
    try:
        local_logger.info(f"Fetching current details for processor {processor_id} before update.")
        nifi_get_req = {"operation": "get_processor_details", "processor_id": processor_id}
        local_logger.bind(interface="nifi", direction="request", data=nifi_get_req).debug("Calling NiFi API")
        current_entity = await nifi_client.get_processor_details(processor_id)
        component_precheck = current_entity.get("component", {})
        current_state = component_precheck.get("state")
        current_revision = current_entity.get("revision")
        precheck_resp = {"id": processor_id, "state": current_state, "version": current_revision.get('version') if current_revision else None}
        local_logger.bind(interface="nifi", direction="response", data=precheck_resp).debug("Received from NiFi API (pre-check)")
        
        if current_state == "RUNNING":
            error_msg = f"Processor '{component_precheck.get('name', processor_id)}' is RUNNING. Stop it before updating properties."
            local_logger.warning(error_msg)
            return {"status": "error", "message": error_msg, "entity": None}
        
        if not current_revision:
             raise ToolError(f"Could not retrieve revision for processor {processor_id}.")
             
        nifi_update_req = {
            "operation": "update_processor_config",
            "processor_id": processor_id,
            "update_type": "properties",
            "update_data": processor_config_properties
        }
        local_logger.bind(interface="nifi", direction="request", data=nifi_update_req).debug("Calling NiFi API")
        updated_entity = await nifi_client.update_processor_config(
            processor_id=processor_id,
            update_type="properties",
            update_data=processor_config_properties
        )
        filtered_updated_entity = filter_created_processor_data(updated_entity)
        local_logger.bind(interface="nifi", direction="response", data=filtered_updated_entity).debug("Received from NiFi API")

        local_logger.info(f"Successfully updated properties for processor {processor_id}")

        component = updated_entity.get("component", {})
        validation_status = component.get("validationStatus", "UNKNOWN")
        validation_errors = component.get("validationErrors", [])
        name = component.get("name", processor_id)

        if validation_status == "VALID":
            return {
                "status": "success",
                "message": f"Processor '{name}' properties updated successfully.",
                "entity": filtered_updated_entity
            }
        else:
            error_msg_snippet = f" ({validation_errors[0]})" if validation_errors else ""
            local_logger.warning(f"Processor '{name}' properties updated, but validation status is {validation_status}{error_msg_snippet}.")
            return {
                "status": "warning",
                "message": f"Processor '{name}' properties updated, but validation status is {validation_status}{error_msg_snippet}. Check configuration.",
                "entity": filtered_updated_entity
            }

    except ValueError as e:
        local_logger.warning(f"Error updating processor properties: {e}")
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"Error updating properties: {e}", "entity": None}
    except (NiFiAuthenticationError, ConnectionError, ToolError) as e: # Include ToolError
        local_logger.error(f"API/Tool error updating processor properties: {e}", exc_info=False)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"Failed to update properties: {e}", "entity": None}
    except Exception as e:
        local_logger.error(f"Unexpected error updating processor properties: {e}", exc_info=True)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"An unexpected error occurred during update: {e}", "entity": None}


@mcp.tool()
@tool_phases(["Modify"])
async def delete_nifi_processor_properties(
    processor_id: str,
    property_names_to_delete: List[str]
) -> Dict:
    """
    Deletes specific properties from a processor's configuration by setting their values to null.

    Args:
        processor_id: The UUID of the processor to modify.
        property_names_to_delete: A non-empty list of property names (strings) to delete.

    Returns:
        A dictionary representing the updated processor entity or an error status.
    """
    # Get client and logger from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
         
    # Authentication handled by factory

    if not property_names_to_delete:
        raise ToolError("The 'property_names_to_delete' list cannot be empty.")
    if not isinstance(property_names_to_delete, list) or not all(isinstance(item, str) for item in property_names_to_delete):
        raise ToolError("Invalid 'property_names_to_delete' type. Expected a non-empty list of strings.")

    local_logger = local_logger.bind(processor_id=processor_id)
    local_logger.info(f"Preparing to delete properties {property_names_to_delete}")

    try:
        local_logger.info(f"Fetching current details for processor {processor_id}...")
        nifi_get_req = {"operation": "get_processor_details", "processor_id": processor_id}
        local_logger.bind(interface="nifi", direction="request", data=nifi_get_req).debug("Calling NiFi API")
        current_entity = await nifi_client.get_processor_details(processor_id)
        local_logger.bind(interface="nifi", direction="response", data=current_entity).debug("Received from NiFi API (full details)")

        current_revision = current_entity.get("revision")
        current_component = current_entity.get("component", {})
        current_config = current_component.get("config", {})
        current_properties = current_config.get("properties", {})
        current_state = current_component.get("state")

        if current_state == "RUNNING":
            error_msg = f"Processor '{current_component.get('name', processor_id)}' is RUNNING. Stop it before deleting properties."
            local_logger.warning(error_msg)
            return {"status": "error", "message": error_msg, "entity": None}
        
        if not current_revision:
             raise ToolError(f"Could not retrieve revision for processor {processor_id}.")

        modified_properties = current_properties.copy()
        properties_actually_deleted = []
        for prop_name in property_names_to_delete:
            if prop_name in modified_properties:
                modified_properties[prop_name] = None
                properties_actually_deleted.append(prop_name)
            else:
                local_logger.warning(f"Property '{prop_name}' not found for deletion. Skipping.")

        if not properties_actually_deleted:
            local_logger.warning(f"None of the requested properties {property_names_to_delete} were found. No update sent.")
            filtered_current_entity = filter_created_processor_data(current_entity)
            return {
                "status": "success",
                "message": f"No properties needed deletion for processor '{current_component.get('name', processor_id)}'. Requested properties not found.",
                "entity": filtered_current_entity
            }
            
        local_logger.info(f"Attempting to delete properties: {properties_actually_deleted}")
        nifi_update_req = {
            "operation": "update_processor_config",
            "processor_id": processor_id,
            "update_type": "properties",
            "update_data": modified_properties
        }
        local_logger.bind(interface="nifi", direction="request", data=nifi_update_req).debug("Calling NiFi API")
        updated_entity = await nifi_client.update_processor_config(
            processor_id=processor_id,
            update_type="properties",
            update_data=modified_properties
        )
        filtered_updated_entity = filter_created_processor_data(updated_entity)
        local_logger.bind(interface="nifi", direction="response", data=filtered_updated_entity).debug("Received from NiFi API")

        local_logger.info(f"Successfully submitted update to delete properties for processor {processor_id}")

        component = updated_entity.get("component", {})
        validation_status = component.get("validationStatus", "UNKNOWN")
        validation_errors = component.get("validationErrors", [])
        name = component.get("name", processor_id)

        if validation_status == "VALID":
            return {
                "status": "success",
                "message": f"Processor '{name}' properties ({properties_actually_deleted}) deleted successfully.",
                "entity": filtered_updated_entity
            }
        else:
            error_msg_snippet = f" ({validation_errors[0]})" if validation_errors else ""
            local_logger.warning(f"Processor '{name}' properties deleted, but validation status is {validation_status}{error_msg_snippet}.")
            return {
                "status": "warning",
                "message": f"Processor '{name}' properties ({properties_actually_deleted}) deleted, but validation status is {validation_status}{error_msg_snippet}. Check configuration.",
                "entity": filtered_updated_entity
            }

    except ValueError as e:
        local_logger.warning(f"Error deleting processor properties: {e}")
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        if "not found" in str(e).lower():
             return {"status": "error", "message": f"Processor {processor_id} not found.", "entity": None}
        elif "conflict" in str(e).lower() or "revision mismatch" in str(e).lower():
             return {"status": "error", "message": f"Conflict deleting properties for processor {processor_id}. Revision mismatch: {e}", "entity": None}
        else:
            return {"status": "error", "message": f"Error deleting properties for processor {processor_id}: {e}", "entity": None}
    except (NiFiAuthenticationError, ConnectionError, ToolError) as e:
        local_logger.error(f"API/Tool error deleting processor properties: {e}", exc_info=False)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"Failed to delete properties: {e}", "entity": None}
    except Exception as e:
        local_logger.error(f"Unexpected error deleting processor properties: {e}", exc_info=True)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"An unexpected error occurred during deletion: {e}", "entity": None}


@mcp.tool()
@tool_phases(["Modify"])
async def update_nifi_processor_relationships(
    processor_id: str,
    auto_terminated_relationships: List[str]
) -> Dict:
    """
    Updates the list of auto-terminated relationships for a processor.
    Replaces the entire existing list with the provided list.

    Args:
        processor_id: The UUID of the processor to update.
        auto_terminated_relationships: A list of relationship names (strings) to be auto-terminated.
                                        Use an empty list `[]` to clear all auto-terminations.

    Returns:
        A dictionary representing the updated processor entity or an error status.
    """
    # Get client and logger from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
         
    # Authentication handled by factory

    if not isinstance(auto_terminated_relationships, list) or not all(isinstance(item, str) for item in auto_terminated_relationships):
         raise ToolError("Invalid 'auto_terminated_relationships' type. Expected a list of strings (can be empty).")

    local_logger = local_logger.bind(processor_id=processor_id)
    local_logger.info(f"Executing update_nifi_processor_relationships for ID: {processor_id}. Setting auto-terminate to: {auto_terminated_relationships}")

    try:
        # Fetch current entity first to check state (might not be strictly needed for relationships, but good practice)
        local_logger.info(f"Fetching current details for processor {processor_id} before update.")
        nifi_get_req = {"operation": "get_processor_details", "processor_id": processor_id}
        local_logger.bind(interface="nifi", direction="request", data=nifi_get_req).debug("Calling NiFi API")
        current_entity = await nifi_client.get_processor_details(processor_id)
        component_precheck = current_entity.get("component", {})
        current_state = component_precheck.get("state")
        local_logger.bind(interface="nifi", direction="response", data=current_entity).debug("Received from NiFi API (pre-check)")

        if current_state == "RUNNING":
            error_msg = f"Processor '{component_precheck.get('name', processor_id)}' is RUNNING. Stop it before updating relationships."
            local_logger.warning(error_msg)
            return {"status": "error", "message": error_msg, "entity": None}
            
        nifi_update_req = {
            "operation": "update_processor_config",
            "processor_id": processor_id,
            "update_type": "auto-terminatedrelationships",
            "update_data": auto_terminated_relationships
        }
        local_logger.bind(interface="nifi", direction="request", data=nifi_update_req).debug("Calling NiFi API")
        updated_entity = await nifi_client.update_processor_config(
            processor_id=processor_id,
            update_type="auto-terminatedrelationships",
            update_data=auto_terminated_relationships
        )
        filtered_updated_entity = filter_created_processor_data(updated_entity)
        local_logger.bind(interface="nifi", direction="response", data=filtered_updated_entity).debug("Received from NiFi API")

        local_logger.info(f"Successfully updated auto-terminated relationships for processor {processor_id}")
        name = updated_entity.get("component", {}).get("name", processor_id)
        return {
            "status": "success",
            "message": f"Processor '{name}' auto-terminated relationships updated successfully.",
            "entity": filtered_updated_entity
        }

    except ValueError as e:
        local_logger.warning(f"Error updating processor relationships {processor_id}: {e}")
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"Error updating relationships for processor {processor_id}: {e}", "entity": None}
    except (NiFiAuthenticationError, ConnectionError, ToolError) as e:
        local_logger.error(f"API/Tool error updating processor relationships: {e}", exc_info=False)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"Failed to update relationships: {e}", "entity": None}
    except Exception as e:
        local_logger.error(f"Unexpected error updating processor relationships: {e}", exc_info=True)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"An unexpected error occurred during relationship update: {e}", "entity": None}


@mcp.tool()
@tool_phases(["Modify"])
async def update_nifi_connection(
    connection_id: str,
    relationships: List[str]
) -> Dict:
    """
    Updates the selected relationships for an existing connection.
    Replaces the entire list of selected relationships with the provided list.

    Args:
        connection_id: The UUID of the connection to update.
        relationships: The complete list of relationship names (strings) that should be active for this connection.
                       Cannot be empty.

    Returns:
        A dictionary representing the updated connection entity or an error status.
    """
    # Get client and logger from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
         
    # Authentication handled by factory

    if not relationships:
         raise ToolError("The 'relationships' list cannot be empty. Provide at least one relationship name.")
    if not isinstance(relationships, list) or not all(isinstance(item, str) for item in relationships):
         raise ToolError("Invalid 'relationships' type. Expected a non-empty list of strings.")
         
    local_logger = local_logger.bind(connection_id=connection_id)
    local_logger.info(f"Executing update_nifi_connection for ID: {connection_id}. Setting relationships to: {relationships}")

    try:
        local_logger.info(f"Fetching current details for connection {connection_id} before update.")
        nifi_get_req = {"operation": "get_connection", "connection_id": connection_id}
        local_logger.bind(interface="nifi", direction="request", data=nifi_get_req).debug("Calling NiFi API")
        current_entity = await nifi_client.get_connection(connection_id)
        local_logger.bind(interface="nifi", direction="response", data=current_entity).debug("Received from NiFi API (full details)")

        current_revision = current_entity.get("revision")
        current_component = current_entity.get("component", {})
        
        if not current_revision:
            raise ToolError(f"Could not retrieve revision for connection {connection_id}.")

        # Prepare the update payload - only include fields that need updating + identity fields
        update_component = {
            "id": current_component.get("id"),
            "parentGroupId": current_component.get("parentGroupId"),
            # Include source and destination for potential validation/context by NiFi
            "source": current_component.get("source"),
            "destination": current_component.get("destination"),
            # The actual update
            "selectedRelationships": relationships
        }

        update_payload = {
            "revision": current_revision,
            "component": update_component
        }

        local_logger.info(f"Attempting update for connection {connection_id}")
        nifi_update_req = {"operation": "update_connection", "connection_id": connection_id, "payload": update_payload}
        local_logger.bind(interface="nifi", direction="request", data=nifi_update_req).debug("Calling NiFi API")
        updated_entity = await nifi_client.update_connection(connection_id, update_payload)
        filtered_updated_entity = filter_connection_data(updated_entity)
        local_logger.bind(interface="nifi", direction="response", data=filtered_updated_entity).debug("Received from NiFi API")

        local_logger.info(f"Successfully updated relationships for connection {connection_id}")
        return {
            "status": "success",
            "message": f"Connection {connection_id} relationships updated successfully.",
            "entity": filtered_updated_entity
        }

    except ValueError as e:
        local_logger.warning(f"Error updating connection {connection_id}: {e}")
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        if "not found" in str(e).lower():
             return {"status": "error", "message": f"Connection {connection_id} not found.", "entity": None}
        elif "conflict" in str(e).lower() or "revision mismatch" in str(e).lower():
             return {"status": "error", "message": f"Conflict updating connection {connection_id}. Revision mismatch: {e}", "entity": None}
        else:
            return {"status": "error", "message": f"Error updating connection {connection_id}: {e}", "entity": None}
    except (NiFiAuthenticationError, ConnectionError, ToolError) as e:
        local_logger.error(f"API/Tool error updating connection: {e}", exc_info=False)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"Failed to update connection: {e}", "entity": None}
    except Exception as e:
        local_logger.error(f"Unexpected error updating connection: {e}", exc_info=True)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"An unexpected error occurred during connection update: {e}", "entity": None}


@mcp.tool()
@tool_phases(["Modify"])
@handle_nifi_errors
async def delete_nifi_object(
    object_type: Literal["processor", "connection", "port", "process_group"],
    object_id: str,
    **kwargs # To potentially catch headers if passed by MCP
) -> Dict:
    """
    Deletes a specific NiFi object (processor, connection, port, or process group).
    Attempts Auto-Stop for running processors if enabled.

    Args:
        object_type: The type of the object to delete ('processor', 'connection', 'port', 'process_group').
        object_id: The UUID of the object to delete.
        **kwargs: May contain 'request_headers' for feature flag overrides.

    Returns:
        A dictionary indicating success or failure.
    """
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
    
    local_logger = local_logger.bind(object_id=object_id, object_type=object_type)
    local_logger.info(f"Executing delete_nifi_object for {object_type} ID: {object_id}")

    try:
        # 1. Get current details to find revision and potentially state/type for ports
        get_details_op = f"get_{object_type}_details"
        if object_type == "port": get_details_op = "get_port_details" # Generic message
            
        nifi_get_req = {"operation": get_details_op, "id": object_id}
        local_logger.bind(interface="nifi", direction="request", data=nifi_get_req).debug("Calling NiFi API (for details)")
        
        current_entity = None
        original_object_type = object_type # Store original type for port type refinement

        if object_type == "processor":
            current_entity = await nifi_client.get_processor_details(object_id)
        elif object_type == "connection":
            current_entity = await nifi_client.get_connection(object_id)
        elif object_type == "process_group":
            current_entity = await nifi_client.get_process_group_details(object_id)
        elif object_type == "port":
            try:
                current_entity = await nifi_client.get_input_port_details(object_id)
                object_type = "input_port" # Refine type for delete call
                local_logger.debug(f"Refined object type to {object_type}")
            except ValueError:
                try:
                    current_entity = await nifi_client.get_output_port_details(object_id)
                    object_type = "output_port" # Refine type for delete call
                    local_logger.debug(f"Refined object type to {object_type}")
                except ValueError as e_out:
                    raise ToolError(f"Port with ID '{object_id}' not found (checked input/output). Cannot delete.") from e_out
        else:
            raise ToolError(f"Invalid object_type '{original_object_type}' for deletion.")
            
        local_logger.bind(interface="nifi", direction="response", data=current_entity).debug("Received from NiFi API (full details)")

        current_revision_dict = current_entity.get("revision")
        if not current_revision_dict or "version" not in current_revision_dict:
            raise ToolError(f"Could not retrieve current revision version for {object_type} {object_id}. Cannot delete.")
        current_version = current_revision_dict["version"]

        component = current_entity.get("component", {})
        state = component.get("state")
        name = component.get("name", object_id)

        # --- AUTO-STOP PRE-EMPTIVE LOGIC --- 
        if original_object_type == "processor" and state == "RUNNING": # Use original_object_type here
            local_logger.info(f"Processor '{name}' is RUNNING. Checking Auto-Stop feature.")
            
            request_headers = kwargs.get("request_headers") 
            if not request_headers and hasattr(local_logger, '_context') and hasattr(local_logger._context, 'get'):
                 request_headers = local_logger._context.get('headers')
            
            is_auto_stop_feature_enabled = mcp_settings.get_feature_auto_stop_enabled(headers=request_headers)
            local_logger.info(f"Auto-Stop enabled via config/header: {is_auto_stop_feature_enabled}")

            if is_auto_stop_feature_enabled:
                local_logger.info(f"[Auto-Stop] Attempting for processor: {object_id}")
                parent_pg_id = component.get("parentGroupId")

                if parent_pg_id:
                    local_logger.info(f"[Auto-Stop] Identified parent PG ID: {parent_pg_id} for processor {object_id}")
                    try:
                        local_logger.info(f"[Auto-Stop] Attempting to stop parent PG: {parent_pg_id}")
                        stop_success = await _stop_pg_direct(nifi_client, parent_pg_id, local_logger)
                        if stop_success:
                            local_logger.info(f"[Auto-Stop] Parent PG {parent_pg_id} stopped. Re-evaluating processor {object_id} state.")
                            await asyncio.sleep(mcp_settings.get_auto_feature_retry_delay_seconds()) 
                            local_logger.info(f"[Auto-Stop] Re-fetching processor details for {object_id} after PG stop.")
                            current_entity = await nifi_client.get_processor_details(object_id) 
                            component = current_entity.get("component", {})
                            state = component.get("state") 
                            current_revision_dict = current_entity.get("revision")
                            if not current_revision_dict or "version" not in current_revision_dict:
                                local_logger.error(f"[Auto-Stop] Could not retrieve post-stop revision for {object_id}. Deletion may fail due to version mismatch.")
                                # Let it proceed, deletion attempt will likely fail with version conflict, or state is still bad
                            else:
                                current_version = current_revision_dict["version"]
                            local_logger.info(f"[Auto-Stop] Processor {object_id} state after PG stop: {state}, new version: {current_version}")
                        else:
                            local_logger.warning(f"[Auto-Stop] Failed to stop parent PG {parent_pg_id}. Processor deletion will likely fail if still running.")
                    except Exception as e_stop:
                        local_logger.error(f"[Auto-Stop] Exception during stop process for PG {parent_pg_id}: {e_stop}", exc_info=True)
                else:
                    local_logger.warning(f"[Auto-Stop] Could not determine parent PG for processor {object_id}. Cannot Auto-Stop.")
        # --- END AUTO-STOP LOGIC ---

        # Final check on state before attempting deletion (state might have been updated by Auto-Stop logic)
        if object_type != "connection" and state == "RUNNING": # object_type here should be the potentially refined one (input_port/output_port)
             error_msg = f"{object_type.capitalize()} '{name}' ({object_id}) is still RUNNING. It must be stopped before deletion. Auto-Stop may have failed or not fully stopped the component."
             local_logger.warning(error_msg)
             return {"status": "error", "message": error_msg} 

        # 2. Attempt deletion using the obtained version
        delete_op = f"delete_{object_type}" # Use potentially refined object_type for ports
        nifi_delete_req = {"operation": delete_op, "id": object_id, "version": current_version}
        local_logger.bind(interface="nifi", direction="request", data=nifi_delete_req).debug("Calling NiFi API (delete)")
        
        deleted = False
        # Use refined object_type for client calls
        if object_type == "processor":
            deleted = await nifi_client.delete_processor(object_id, current_version)
        elif object_type == "connection":
            deleted = await nifi_client.delete_connection(object_id, current_version)
        elif object_type == "input_port":
            deleted = await nifi_client.delete_input_port(object_id, current_version)
        elif object_type == "output_port":
            deleted = await nifi_client.delete_output_port(object_id, current_version)
        elif object_type == "process_group":
            deleted = await nifi_client.delete_process_group(object_id, current_version)

        nifi_delete_resp = {"deleted": deleted}
        local_logger.bind(interface="nifi", direction="response", data=nifi_delete_resp).debug("Received from NiFi API (delete)")

        if deleted:
            local_logger.info(f"Successfully deleted {object_type} '{name}' ({object_id}).")
            return {"status": "success", "message": f"{object_type.capitalize()} '{name}' deleted successfully."}
        else:
            local_logger.warning(f"Deletion call for {object_type} '{name}' ({object_id}) returned False. This might be due to a 404 (already deleted) or other client-side handled issue.")
            # The NiFiClient delete methods should raise ValueError for actual API errors like 409 Conflict.
            # If we reach here with `deleted = False`, it implies the client method itself returned False (e.g. 404 not found)
            return {"status": "error", "message": f"Deletion failed for {object_type} '{name}'. It might have already been deleted or the client handled an issue returning False instead of an exception."}

    except ValueError as e:
        local_logger.warning(f"Error deleting {object_type} {object_id} (ValueError caught in main try-except): {e}")
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API (delete)")
        
        error_message = str(e)
        error_message_lower = error_message.lower()

        # No re-raise here for the decorator for now, as Auto-Stop is preemptive.
        # The decorator can be re-enabled later if needed for other types of errors.

        if "not found" in error_message_lower:
             return {"status": "error", "message": f"{object_type.capitalize()} {object_id} not found."}
        elif "conflict" in error_message_lower or "revision mismatch" in error_message_lower or "is currently RUNNING" in error_message_lower:
             return {"status": "error", "message": f"Conflict or state issue deleting {object_type} '{name}' ({object_id}): {e}"}
        else:
            return {"status": "error", "message": f"Error deleting {object_type} '{name}' ({object_id}): {e}"}
            
    except (NiFiAuthenticationError, ConnectionError, ToolError) as e:
        local_logger.error(f"API/Tool error deleting {object_type} {object_id}: {e}", exc_info=False)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API (delete)")
        return {"status": "error", "message": f"Failed to delete {object_type} {object_id}: {e}"}
    except Exception as e:
        local_logger.error(f"Unexpected error deleting {object_type} {object_id}: {e}", exc_info=True)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API (delete)")
        return {"status": "error", "message": f"An unexpected error occurred during deletion: {e}"}
