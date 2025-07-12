import asyncio
from typing import List, Dict, Optional, Any, Union, Literal, Tuple
from typing_extensions import TypedDict
import httpx
import json

# Import necessary components from parent/utils
from loguru import logger
# Import mcp ONLY
from ..core import mcp, _handle_drop_request
# Removed nifi_api_client import
# Import context variables
from ..request_context import current_nifi_client, current_request_logger # Added
# Import utils helper for filtering PG data
from .utils import (
    tool_phases,
    # ensure_authenticated, # Removed
    filter_created_processor_data, # Keep for processor/port paths
    filter_process_group_data, # Add for process group path
    filter_connection_data,
    filter_drop_request_data,
    smart_parameter_validation
)
from nifi_mcp_server.nifi_client import NiFiClient, NiFiAuthenticationError
from mcp.server.fastmcp.exceptions import ToolError

# Import status checking function from review module
from .review import get_process_group_status


# REMOVED: Single operate_nifi_object tool - replaced by operate_nifi_objects batch tool
# This function is now internal-only, used by the batch operation helper
async def operate_nifi_object(
    object_type: Literal["processor", "port", "process_group", "controller_service"],
    object_id: str,
    operation_type: Literal["start", "stop", "enable", "disable"]
) -> Dict:
    """
    Internal function to operate on a single NiFi object.
    
    This function is no longer exposed as an MCP tool - it's used internally by the 
    operate_nifi_objects batch tool for individual operations.

    Args:
        object_type: The type of object to operate on ('processor', 'port', 'process_group', or 'controller_service').
        object_id: The UUID of the object.
        operation_type: The operation to perform:
            - 'start'/'stop': For processors, ports, and process groups
            - 'enable'/'disable': For controller services
            - For 'process_group', start/stop applies to all eligible components within

    Returns:
        A dictionary indicating the status (success, warning, error) and potentially the updated entity.
        The structure may vary slightly based on object_type.
    """
    # Get client and logger from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")

    # Authentication handled by factory

    # Bind args
    local_logger = local_logger.bind(object_type=object_type, object_id=object_id, operation_type=operation_type)

    # Validate operation types for object types
    if object_type == "controller_service" and operation_type not in ["enable", "disable"]:
        raise ToolError(f"Invalid operation '{operation_type}' for controller service. Use 'enable' or 'disable'.")
    elif object_type != "controller_service" and operation_type not in ["start", "stop"]:
        raise ToolError(f"Invalid operation '{operation_type}' for {object_type}. Use 'start' or 'stop'.")

    # Map operation type to NiFi state
    if object_type == "controller_service":
        target_state = "ENABLED" if operation_type == "enable" else "DISABLED"
    else:
        target_state = "RUNNING" if operation_type == "start" else "STOPPED"

    local_logger.info(f"Executing {operation_type} operation on {object_type} {object_id} (Target State: {target_state})")

    try:
        updated_entity = None
        port_type_found = None # To track if input/output port for logging/errors
        operation_name_for_log = f"update_{object_type}_state" # Default log name

        # --- Processor Logic ---
        if object_type == "processor":
            # --- Pre-check for starting a processor ---
            if operation_type == "start":
                local_logger.info(f"Performing pre-checks...")
                try:
                    nifi_get_req = {"operation": "get_processor_details", "processor_id": object_id}
                    local_logger.bind(interface="nifi", direction="request", data=nifi_get_req).debug("Calling NiFi API (pre-check)")
                    proc_details = await nifi_client.get_processor_details(object_id)
                    component_precheck = proc_details.get("component", {})
                    precheck_resp = {
                        "id": object_id,
                        "validationStatus": component_precheck.get("validationStatus"),
                        "state": component_precheck.get("state")
                    }
                    local_logger.bind(interface="nifi", direction="response", data=precheck_resp).debug("Received from NiFi API (pre-check)")

                    component = proc_details.get("component", {})
                    validation_status = component.get("validationStatus")
                    current_state = component.get("state")
                    validation_errors = component.get("validationErrors", [])
                    name = component.get("name", object_id)

                    if validation_status != "VALID":
                        error_list_str = ", ".join(validation_errors) if validation_errors else "No specific errors listed."
                        error_msg = f"Processor '{name}' cannot be started. Validation status: {validation_status}. Errors: [{error_list_str}]"
                        local_logger.warning(error_msg)
                        return {"status": "error", "message": error_msg, "entity": None}

                    if current_state == "DISABLED":
                        error_msg = f"Processor '{name}' cannot be started because it is DISABLED. Enable it first."
                        local_logger.warning(error_msg)
                        return {"status": "error", "message": error_msg, "entity": None}

                    local_logger.info(f"Processor pre-checks passed (Validation: {validation_status}, State: {current_state}). Proceeding with start.")

                except ValueError as e:
                    local_logger.warning(f"Processor not found during start pre-check: {e}")
                    local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API (pre-check)")
                    return {"status": "error", "message": f"Processor {object_id} not found.", "entity": None}
                except Exception as e:
                    local_logger.error(f"Error during start pre-check: {e}", exc_info=True)
                    local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API (pre-check)")
                    return {"status": "error", "message": f"Failed pre-start check: {e}", "entity": None}
            # --- End of Pre-check ---

            nifi_update_req = {"operation": operation_name_for_log, "processor_id": object_id, "state": target_state}
            local_logger.bind(interface="nifi", direction="request", data=nifi_update_req).debug("Calling NiFi API")
            updated_entity = await nifi_client.update_processor_state(object_id, target_state)

            # --- Format and return processor result ---
            filtered_entity = filter_created_processor_data(updated_entity) # Re-use processor filter
            local_logger.bind(interface="nifi", direction="response", data=filtered_entity).debug(f"Received from NiFi API ({operation_name_for_log})")

            component = updated_entity.get("component", {})
            current_state = component.get("state")
            name = component.get("name", object_id)
            validation_status = component.get("validationStatus", "UNKNOWN")

            if current_state == target_state:
                 action = "started" if operation_type == "start" else "stopped"
                 local_logger.info(f"Successfully {action} {object_type} '{name}'.")
                 return {"status": "success", "message": f"{object_type.capitalize()} '{name}' {action} successfully.", "entity": filtered_entity}
            else:
                # Check for specific error cases if start failed despite pre-check
                if operation_type == "start" and (current_state == "DISABLED" or validation_status != "VALID"):
                    local_logger.warning(f"{object_type.capitalize()} '{name}' could not be started. State: {current_state}, Validation: {validation_status}.")
                    return {"status": "warning", "message": f"{object_type.capitalize()} '{name}' could not be started (State: {current_state}, Validation: {validation_status}). Check config.", "entity": filtered_entity}
                else:
                     action = "start" if operation_type == "start" else "stop"
                     local_logger.warning(f"{object_type.capitalize()} '{name}' state is {current_state} after {action} request. Expected {target_state}.")
                     return {"status": "warning", "message": f"{object_type.capitalize()} '{name}' is {current_state} after {action} request. Check NiFi UI.", "entity": filtered_entity}

        # --- Port Logic ---
        elif object_type == "port":
            # --- Pre-check for starting a port ---
            port_details_for_check = None
            if operation_type == "start":
                local_logger.info(f"Performing pre-checks...")
                try:
                    try:
                        nifi_get_req = {"operation": "get_input_port_details", "port_id": object_id}
                        local_logger.bind(interface="nifi", direction="request", data=nifi_get_req).debug("Calling NiFi API (trying input for pre-check)")
                        port_details_for_check = await nifi_client.get_input_port_details(object_id)
                        port_type_found = "input"
                    except ValueError: # Not input, try output
                        local_logger.bind(interface="nifi", direction="response", data={"error": "Input port not found"}).debug("Received error from NiFi API (pre-check)")
                        nifi_get_req = {"operation": "get_output_port_details", "port_id": object_id}
                        local_logger.bind(interface="nifi", direction="request", data=nifi_get_req).debug("Calling NiFi API (trying output for pre-check)")
                        port_details_for_check = await nifi_client.get_output_port_details(object_id)
                        port_type_found = "output"

                    component_precheck = port_details_for_check.get("component", {})
                    precheck_resp = {
                        "id": object_id,
                        "type": port_type_found,
                        "validationStatus": component_precheck.get("validationStatus"),
                        "state": component_precheck.get("state")
                    }
                    local_logger.bind(interface="nifi", direction="response", data=precheck_resp).debug("Received from NiFi API (pre-check)")

                    component = port_details_for_check.get("component", {})
                    validation_status = component.get("validationStatus")
                    current_state = component.get("state")
                    validation_errors = component.get("validationErrors", [])
                    name = component.get("name", object_id)

                    if validation_status != "VALID":
                        error_list_str = ", ".join(validation_errors) if validation_errors else "No specific errors listed."
                        error_msg = f"Port '{name}' cannot be started. Validation status: {validation_status}. Errors: [{error_list_str}]"
                        local_logger.warning(error_msg)
                        return {"status": "error", "message": error_msg, "entity": None}

                    if current_state == "DISABLED":
                        error_msg = f"Port '{name}' cannot be started because it is DISABLED. Enable it first."
                        local_logger.warning(error_msg)
                        return {"status": "error", "message": error_msg, "entity": None}

                    local_logger.info(f"Port pre-checks passed (Validation: {validation_status}, State: {current_state}). Proceeding with start.")

                except ValueError as e:
                    local_logger.warning(f"Port not found during start pre-check: {e}")
                    local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API (pre-check)")
                    return {"status": "error", "message": f"Port {object_id} not found.", "entity": None}
                except Exception as e:
                    local_logger.error(f"Error during start pre-check: {e}", exc_info=True)
                    local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API (pre-check)")
                    return {"status": "error", "message": f"Failed pre-start check for port {object_id}: {e}", "entity": None}
            # --- End of Port Pre-check ---

            if port_type_found is None:
                local_logger.info("Determining port type (input/output)...")
                try:
                    _ = await nifi_client.get_input_port_details(object_id)
                    port_type_found = "input"
                    local_logger.info(f"Port {object_id} identified as INPUT.")
                except ValueError:
                    local_logger.warning(f"Port not found as input, trying output.")
                    try:
                        _ = await nifi_client.get_output_port_details(object_id)
                        port_type_found = "output"
                        local_logger.info(f"Port {object_id} identified as OUTPUT.")
                    except ValueError:
                        raise ToolError(f"Port with ID {object_id} not found. Cannot change state.")

            if port_type_found == "input":
                operation_name_for_log = "update_input_port_state"
                nifi_update_req = {"operation": operation_name_for_log, "port_id": object_id, "state": target_state}
                local_logger.bind(interface="nifi", direction="request", data=nifi_update_req).debug("Calling NiFi API")
                updated_entity = await nifi_client.update_input_port_state(object_id, target_state)
            elif port_type_found == "output":
                operation_name_for_log = "update_output_port_state"
                nifi_update_req = {"operation": operation_name_for_log, "port_id": object_id, "state": target_state}
                local_logger.bind(interface="nifi", direction="request", data=nifi_update_req).debug("Calling NiFi API")
                updated_entity = await nifi_client.update_output_port_state(object_id, target_state)

            # --- Format and return port result ---
            filtered_entity = filter_created_processor_data(updated_entity) # Re-use processor filter logic
            local_logger.bind(interface="nifi", direction="response", data=filtered_entity).debug(f"Received from NiFi API ({operation_name_for_log})")

            component = updated_entity.get("component", {})
            current_state = component.get("state")
            name = component.get("name", object_id)
            validation_status = component.get("validationStatus", "UNKNOWN")

            if current_state == target_state:
                 action = "started" if operation_type == "start" else "stopped"
                 local_logger.info(f"Successfully {action} {object_type} '{name}'.")
                 return {"status": "success", "message": f"{object_type.capitalize()} '{name}' {action} successfully.", "entity": filtered_entity}
            else:
                # Check for specific error cases if start failed despite pre-check
                if operation_type == "start" and (current_state == "DISABLED" or validation_status != "VALID"):
                    local_logger.warning(f"{object_type.capitalize()} '{name}' could not be started. State: {current_state}, Validation: {validation_status}.")
                    return {"status": "warning", "message": f"{object_type.capitalize()} '{name}' could not be started (State: {current_state}, Validation: {validation_status}). Check config.", "entity": filtered_entity}
                else:
                     action = "start" if operation_type == "start" else "stop"
                     local_logger.warning(f"{object_type.capitalize()} '{name}' state is {current_state} after {action} request. Expected {target_state}.")
                     return {"status": "warning", "message": f"{object_type.capitalize()} '{name}' is {current_state} after {action} request. Check NiFi UI.", "entity": filtered_entity}

        # --- Process Group Logic ---
        elif object_type == "process_group":
            operation_name_for_log = "update_process_group_state"
            nifi_update_req = {"operation": operation_name_for_log, "process_group_id": object_id, "state": target_state}
            local_logger.bind(interface="nifi", direction="request", data=nifi_update_req).debug("Calling NiFi API (Bulk Update)")
            # No pre-checks for group operation, API handles it
            updated_entity = await nifi_client.update_process_group_state(object_id, target_state)

            # Format and return process group result
            # Use filter_process_group_data helper
            filtered_entity = filter_process_group_data(updated_entity)
            local_logger.bind(interface="nifi", direction="response", data=filtered_entity).debug(f"Received from NiFi API ({operation_name_for_log})")

            action_verb = "started" if operation_type == "start" else "stopped"
            message = f"Successfully initiated {action_verb} operation for all components in process group {object_id}."
            # Note: NiFi API for group state change is async. We report success of the request submission.
            # We could potentially query status afterwards, but keeping it simple for now.
            local_logger.info(message)
            return {
                "status": "success",
                "message": message,
                "process_group_id": object_id,
                "operation_type": operation_type,
                "entity_summary": filtered_entity # Include summary status counts from response
            }

        # --- Controller Service Logic ---
        elif object_type == "controller_service":
            # --- Pre-check for enabling a controller service ---
            if operation_type == "enable":
                local_logger.info(f"Performing pre-checks...")
                try:
                    nifi_get_req = {"operation": "get_controller_service_details", "controller_service_id": object_id}
                    local_logger.bind(interface="nifi", direction="request", data=nifi_get_req).debug("Calling NiFi API (pre-check)")
                    cs_details = await nifi_client.get_controller_service_details(object_id)
                    component_precheck = cs_details.get("component", {})
                    precheck_resp = {
                        "id": object_id,
                        "validationStatus": component_precheck.get("validationStatus"),
                        "state": component_precheck.get("state")
                    }
                    local_logger.bind(interface="nifi", direction="response", data=precheck_resp).debug("Received from NiFi API (pre-check)")

                    component = cs_details.get("component", {})
                    validation_status = component.get("validationStatus")
                    current_state = component.get("state")
                    validation_errors = component.get("validationErrors", [])
                    name = component.get("name", object_id)

                    if validation_status != "VALID":
                        error_list_str = ", ".join(validation_errors) if validation_errors else "No specific errors listed."
                        error_msg = f"Controller service '{name}' cannot be enabled. Validation status: {validation_status}. Errors: [{error_list_str}]"
                        local_logger.warning(error_msg)
                        return {"status": "warning", "message": error_msg, "entity": None}

                    local_logger.info(f"Controller service pre-checks passed (Validation: {validation_status}, State: {current_state}). Proceeding with enable.")

                except ValueError as e:
                    local_logger.warning(f"Controller service not found during enable pre-check: {e}")
                    local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API (pre-check)")
                    return {"status": "error", "message": f"Controller service {object_id} not found.", "entity": None}
                except Exception as e:
                    local_logger.error(f"Error during enable pre-check: {e}", exc_info=True)
                    local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API (pre-check)")
                    return {"status": "error", "message": f"Failed pre-enable check: {e}", "entity": None}
            # --- End of Pre-check ---

            operation_name_for_log = f"{operation_type}_controller_service"
            nifi_update_req = {"operation": operation_name_for_log, "controller_service_id": object_id, "state": target_state}
            local_logger.bind(interface="nifi", direction="request", data=nifi_update_req).debug("Calling NiFi API")
            
            if operation_type == "enable":
                updated_entity = await nifi_client.enable_controller_service(object_id)
            else:  # disable
                updated_entity = await nifi_client.disable_controller_service(object_id)

            # --- Format and return controller service result ---
            from .utils import filter_controller_service_data
            filtered_entity = filter_controller_service_data(updated_entity)
            local_logger.bind(interface="nifi", direction="response", data=filtered_entity).debug(f"Received from NiFi API ({operation_name_for_log})")

            component = updated_entity.get("component", {})
            current_state = component.get("state")
            name = component.get("name", object_id)
            validation_status = component.get("validationStatus", "UNKNOWN")

            if current_state == target_state:
                 action = "enabled" if operation_type == "enable" else "disabled"
                 local_logger.info(f"Successfully {action} controller service '{name}'.")
                 return {"status": "success", "message": f"Controller service '{name}' {action} successfully.", "entity": filtered_entity}
            else:
                # Check for specific error cases if enable failed despite pre-check
                if operation_type == "enable" and validation_status != "VALID":
                    local_logger.warning(f"Controller service '{name}' could not be enabled. State: {current_state}, Validation: {validation_status}.")
                    return {"status": "warning", "message": f"Controller service '{name}' could not be enabled (State: {current_state}, Validation: {validation_status}). Check config.", "entity": filtered_entity}
                else:
                     action = "enable" if operation_type == "enable" else "disable"
                     local_logger.warning(f"Controller service '{name}' state is {current_state} after {action} request. Expected {target_state}.")
                     return {"status": "warning", "message": f"Controller service '{name}' is {current_state} after {action} request. Check NiFi UI.", "entity": filtered_entity}

        # --- Invalid Object Type ---
        else:
            # This case should ideally not be reachable due to Literal typing, but defensive coding is good.
            raise ToolError(f"Invalid object_type: {object_type}. Must be 'processor', 'port', 'process_group', or 'controller_service'.")

    # --- General Exception Handling ---
    except ValueError as e: # Catches specific errors like 404s or 409 conflicts raised by client methods
        local_logger.warning(f"Error operating on {object_type} {object_id}: {e}")
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug(f"Received error from NiFi API ({operation_name_for_log})")
        error_context = f"{object_type.capitalize()} {object_id}" if object_type != 'process_group' else f"Process Group {object_id}"
        if "not found" in str(e).lower():
             return {"status": "error", "message": f"{error_context} not found.", "entity": None}
        elif "conflict" in str(e).lower():
             # More specific conflict messages are raised by client methods
             return {"status": "error", "message": f"Could not {operation_type} {error_context} due to conflict: {e}. Check state/revision.", "entity": None}
        else: # Other ValueErrors from client (e.g., invalid state)
            return {"status": "error", "message": f"Could not {operation_type} {error_context}: {e}", "entity": None}

    except (NiFiAuthenticationError, ConnectionError, ToolError) as e:
        # Handle specific known exceptions
        local_logger.error(f"API/Tool error operating on {object_type} {object_id}: {e}", exc_info=False) # No traceback for these expected errors
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug(f"Received error from NiFi API ({operation_name_for_log})")
        return {"status": "error", "message": f"Failed to {operation_type} NiFi {object_type} {object_id}: {e}", "entity": None}
    except Exception as e:
        # Catch any other unexpected errors
        local_logger.error(f"Unexpected error operating on {object_type} {object_id}: {e}", exc_info=True) # Include traceback
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug(f"Received error from NiFi API ({operation_name_for_log})")
        return {"status": "error", "message": f"An unexpected error occurred: {e}", "entity": None}


# --- Internal helper function for single object operation ---
async def _operate_single_nifi_object(
    object_type: str,
    object_id: str,
    operation_type: str,
    object_name: str,
    nifi_client: NiFiClient,
    logger
) -> Dict:
    """
    Internal function to operate on a single NiFi object.
    Contains the core operation logic extracted from the original operate_nifi_object function.
    """
    logger.info(f"Executing {operation_type} operation on {object_type} '{object_name}' ({object_id})")

    # Call the original operate_nifi_object logic with context setup
    try:
        # Set the context for the single operation
        from ..request_context import current_nifi_client, current_request_logger
        token_client = current_nifi_client.set(nifi_client)
        token_logger = current_request_logger.set(logger)
        
        try:
            result = await operate_nifi_object(
                object_type=object_type,
                object_id=object_id,
                operation_type=operation_type
            )
            return result
        finally:
            # Reset context
            current_nifi_client.reset(token_client)
            current_request_logger.reset(token_logger)
    except Exception as e:
        logger.error(f"Unexpected error operating on {object_type} '{object_name}' ({object_id}): {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"An unexpected error occurred during {operation_type} operation: {e}",
            "entity": None
        }


class OperationRequest(TypedDict):
    object_type: Literal["processor", "port", "process_group", "controller_service"]
    object_id: str
    operation_type: Literal["start", "stop", "enable", "disable"]
    name: Optional[str]  # Optional descriptive name

@smart_parameter_validation
@mcp.tool()
@tool_phases(["Operate"])
async def operate_nifi_objects(
    operations: List[OperationRequest]
) -> List[Dict]:
    """
    Performs start, stop, enable, or disable operations on multiple NiFi objects in batch.
    
    This tool provides efficient batch processing for object state operations, reducing the number
    of individual tool calls required. Each operation is processed independently, so failure of 
    one operation does not prevent others from completing.
    
    Args:
        operations: A list of operation request dictionaries, each containing:
            - object_type: The type of object ('processor', 'port', 'process_group', or 'controller_service')
            - object_id: The UUID of the object to operate on  
            - operation_type: The operation to perform ('start', 'stop', 'enable', or 'disable')
            - name (optional): A descriptive name for the object (used in logging/results)
    
    Example:
    ```python
    operations = [
        {
            "object_type": "processor",
            "object_id": "123e4567-e89b-12d3-a456-426614174000", 
            "operation_type": "start",
            "name": "MyProcessor"
        },
        {
            "object_type": "controller_service",
            "object_id": "456e7890-e89b-12d3-a456-426614174001",
            "operation_type": "enable",
            "name": "MyControllerService"
        },
        {
            "object_type": "process_group",
            "object_id": "789e1234-e89b-12d3-a456-426614174002", 
            "operation_type": "stop"
        }
    ]
    result = operate_nifi_objects(operations)
    ```
    
    Returns:
        A list of dictionaries, each indicating success or failure for the corresponding operation request.
        Each result includes:
            - status: 'success', 'warning', or 'error'
            - message: Descriptive message about the operation result
            - entity: The updated NiFi entity (if successful)
            - object_type: Type of object operated on
            - object_id: ID of object operated on  
            - operation_type: Operation that was performed
            - object_name: Name of object (if provided)
            - request_index: Index of request in the input list
    """
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
    
    if not operations:
        raise ToolError("The 'operations' list cannot be empty.")
    if not isinstance(operations, list):
        raise ToolError("Invalid 'operations' type. Expected a list of dictionaries.")
    
    # Validate each operation request
    for i, req in enumerate(operations):
        if not isinstance(req, dict):
            raise ToolError(f"Operation request {i} is not a dictionary.")
        if "object_type" not in req or "object_id" not in req or "operation_type" not in req:
            raise ToolError(f"Operation request {i} missing required fields 'object_type', 'object_id', and/or 'operation_type'.")
        if req["object_type"] not in ["processor", "port", "process_group", "controller_service"]:
            raise ToolError(f"Operation request {i} has invalid object_type '{req['object_type']}'. Must be one of: processor, port, process_group, controller_service.")
        if req["operation_type"] not in ["start", "stop", "enable", "disable"]:
            raise ToolError(f"Operation request {i} has invalid operation_type '{req['operation_type']}'. Must be one of: start, stop, enable, disable.")
        
        # Validate operation type compatibility with object type
        object_type = req["object_type"]
        operation_type = req["operation_type"]
        if object_type == "controller_service" and operation_type not in ["enable", "disable"]:
            raise ToolError(f"Operation request {i}: Invalid operation '{operation_type}' for controller service. Use 'enable' or 'disable'.")
        elif object_type != "controller_service" and operation_type not in ["start", "stop"]:
            raise ToolError(f"Operation request {i}: Invalid operation '{operation_type}' for {object_type}. Use 'start' or 'stop'.")

    local_logger.info(f"Executing operate_nifi_objects for {len(operations)} objects")
    
    results = []
    
    for i, operation_request in enumerate(operations):
        object_type = operation_request["object_type"]
        object_id = operation_request["object_id"]
        operation_type = operation_request["operation_type"]
        object_name = operation_request.get("name", object_id)
        
        request_logger = local_logger.bind(object_id=object_id, object_type=object_type, operation_type=operation_type, request_index=i)
        request_logger.info(f"Processing operation request {i+1}/{len(operations)} for {object_type} '{object_name}' ({object_id}): {operation_type}")
        
        try:
            # Call the helper function that wraps the original operate_nifi_object logic
            result = await _operate_single_nifi_object(
                object_type=object_type,
                object_id=object_id,
                operation_type=operation_type,
                object_name=object_name,
                nifi_client=nifi_client,
                logger=request_logger
            )
            
            # Add metadata to the result
            result["object_type"] = object_type
            result["object_id"] = object_id
            result["operation_type"] = operation_type
            result["object_name"] = object_name
            result["request_index"] = i
            
            results.append(result)
            
        except Exception as e:
            error_result = {
                "status": "error",
                "message": f"Unexpected error during {operation_type} operation on {object_type} '{object_name}' ({object_id}): {e}",
                "entity": None,
                "object_type": object_type,
                "object_id": object_id,
                "operation_type": operation_type,
                "object_name": object_name,
                "request_index": i
            }
            results.append(error_result)
            request_logger.error(f"Unexpected error in operation request {i}: {e}", exc_info=True)
    
    # Summary logging
    successful_operations = [r for r in results if r.get("status") == "success"]
    failed_operations = [r for r in results if r.get("status") == "error"]
    warning_operations = [r for r in results if r.get("status") == "warning"]
    
    local_logger.info(f"Batch operation completed: {len(successful_operations)} successful, {len(failed_operations)} failed, {len(warning_operations)} warnings")
    
    return results


@mcp.tool()
@tool_phases(["Operate", "Verify"])
async def invoke_nifi_http_endpoint(
    url: str,
    process_group_id: str,
    method: Literal["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"] = "POST",
    payload: Optional[Union[str, Dict[str, Any]]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout_seconds: int = 10
) -> Dict:
    """
    Sends an HTTP request to a specified URL, typically to test a NiFi flow endpoint (e.g., ListenHTTP).
    Waits for a response or times out, then automatically checks the flow status for context.

    WARNING: Ensure the URL points to a trusted endpoint, ideally one hosted by your NiFi instance or related infrastructure.

    Examples:
    Call (POST JSON):
    ```tool_code
    print(default_api.invoke_nifi_http_endpoint(url='http://localhost:9999/myflow', process_group_id='root', method='POST', payload={'key': 'value'}, headers={'Content-Type': 'application/json'}))
    ```

    Call (GET):
    ```tool_code
    print(default_api.invoke_nifi_http_endpoint(url='http://localhost:9998/status', process_group_id='abc123', method='GET'))
    ```

    Args:
        url: The full URL of the target endpoint.
        process_group_id: The ID of the process group to check for flow status after the HTTP request.
        method: The HTTP method to use (e.g., GET, POST). Defaults to POST.
        payload: The request body. If a dictionary, it will be sent as JSON with 'Content-Type: application/json' unless overridden in headers. If a string, it's sent as is.
        headers: A dictionary of custom request headers.
        timeout_seconds: Maximum time in seconds to wait for a response. Defaults to 10.

    Returns:
        A dictionary describing the outcome:
        {
          "status": "success" | "timeout" | "connection_error" | "http_error" | "internal_error",
          "message": "Descriptive message of the outcome.",
          "response_status_code": Optional[int],  # e.g., 200, 404, 500
          "response_headers": Optional[Dict[str, str]],
          "response_body": Optional[str], # Response body as text (potentially truncated)
          "request_details": { # Details of the request sent
            "url": str,
            "method": str,
            "headers": Dict[str, str],
            "payload_type": str # e.g., 'json', 'string', 'none'
          },
          "flow_status": { # Flow status information checked after HTTP request
            "success": bool,  # Whether status check succeeded
            "data": Optional[Dict],  # The actual status data from get_process_group_status
            "error": Optional[str],  # Error message if status check failed
            "process_group_id": str  # Which PG was checked
          }
        }
    """
    local_logger = current_request_logger.get() or logger
    local_logger = local_logger.bind(target_url=url, http_method=method, process_group_id=process_group_id)
    local_logger.info(f"Attempting to invoke HTTP endpoint.")

    request_headers = headers or {}
    content_to_send = None
    payload_type = "none"
    
    # Initialize flow status structure
    flow_status = {
        "success": False,
        "data": None,
        "error": None,
        "process_group_id": process_group_id
    }

    try:
        if isinstance(payload, dict):
            if 'content-type' not in (h.lower() for h in request_headers):
                request_headers['Content-Type'] = 'application/json'
            try:
                content_to_send = json.dumps(payload).encode('utf-8')
                payload_type = "json"
            except (TypeError, OverflowError) as json_err:
                 local_logger.error(f"Failed to serialize JSON payload: {json_err}", exc_info=True)
                 http_result = {
                     "status": "internal_error",
                     "message": f"Failed to serialize dictionary payload to JSON: {json_err}",
                     "response_status_code": None,
                     "response_headers": None,
                     "response_body": None,
                     "request_details": {"url": url, "method": method, "headers": request_headers, "payload_type": "dict (serialization failed)"}
                 }
                 # Still try to get flow status even after JSON serialization error
                 local_logger.info(f"Checking flow status for process group {process_group_id} after JSON error")
                 try:
                     flow_status_data = await get_process_group_status(
                         process_group_id=process_group_id,
                         include_bulletins=True,
                         bulletin_limit=15
                     )
                     flow_status["success"] = True
                     flow_status["data"] = flow_status_data
                 except Exception as status_err:
                     flow_status["error"] = str(status_err)
                 
                 http_result["flow_status"] = flow_status
                 return http_result
        elif isinstance(payload, str):
            content_to_send = payload.encode('utf-8') # Assume UTF-8 for strings
            payload_type = "string"

        # Log the headers being sent *before* the request
        final_request_headers = request_headers
        local_logger.debug(f"Sending request: Method={method}, URL={url}, Headers={final_request_headers}, Payload Type={payload_type}")

        request_details_for_response = {
            "url": url,
            "method": method,
            "headers": final_request_headers,
            "payload_type": payload_type
        }

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=final_request_headers,
                content=content_to_send
            )

            # Attempt to decode body, handle potential errors
            response_body_text = None
            try:
                response_body_text = response.text
                # Optional: Truncate large responses
                # max_len = 1024
                # if len(response_body_text) > max_len:
                #     response_body_text = response_body_text[:max_len] + "... (truncated)"
            except Exception as decode_err:
                local_logger.warning(f"Could not decode response body: {decode_err}")
                response_body_text = "<Could not decode response body>"

            response_headers_dict = dict(response.headers)
            local_logger.info(f"Received response: Status={response.status_code}")
            local_logger.debug(f"Response Headers: {response_headers_dict}")
            local_logger.debug(f"Response Body: {response_body_text}") # Be mindful of logging sensitive data

            # Check if status code indicates an HTTP error (4xx or 5xx)
            if 400 <= response.status_code < 600:
                http_result = {
                    "status": "http_error",
                    "message": f"Endpoint returned HTTP error status code: {response.status_code}",
                    "response_status_code": response.status_code,
                    "response_headers": response_headers_dict,
                    "response_body": response_body_text,
                    "request_details": request_details_for_response
                }
            else:
                http_result = {
                    "status": "success",
                    "message": f"Successfully received response from endpoint.",
                    "response_status_code": response.status_code,
                    "response_headers": response_headers_dict,
                    "response_body": response_body_text,
                    "request_details": request_details_for_response
                }



    except httpx.TimeoutException:
        local_logger.warning(f"Request timed out after {timeout_seconds} seconds.")
        http_result = {
            "status": "timeout",
            "message": f"Request timed out after {timeout_seconds} seconds.",
            "response_status_code": None,
            "response_headers": None,
            "response_body": None,
            "request_details": request_details_for_response # Use headers prepared before request
        }
    except httpx.RequestError as e:
        # Covers connection errors, DNS errors, invalid URL errors (caught by httpx), etc.
        local_logger.error(f"HTTP request error: {e}", exc_info=False)
        http_result = {
            "status": "connection_error",
            "message": f"Failed to connect or send request: {e}",
            "response_status_code": None,
            "response_headers": None,
            "response_body": None,
            "request_details": request_details_for_response
        }
    except Exception as e:
        # Catch-all for unexpected errors during preparation or execution
        local_logger.error(f"Unexpected internal error during HTTP invocation: {e}", exc_info=True)
        http_result = {
            "status": "internal_error",
            "message": f"An unexpected internal error occurred: {e}",
            "response_status_code": None,
            "response_headers": None,
            "response_body": None,
            "request_details": {"url": url, "method": method, "headers": request_headers, "payload_type": payload_type}
        }

    # Always attempt to get flow status, even after errors/timeouts
    local_logger.info(f"Checking flow status for process group {process_group_id}")
    try:
        flow_status_data = await get_process_group_status(
            process_group_id=process_group_id,
            include_bulletins=True,
            bulletin_limit=15  # Reasonable limit for context
        )
        flow_status["success"] = True
        flow_status["data"] = flow_status_data
        local_logger.debug(f"Successfully retrieved flow status for PG {process_group_id}")
    except Exception as status_err:
        local_logger.warning(f"Failed to get flow status for PG {process_group_id}: {status_err}")
        flow_status["error"] = str(status_err)

    # Combine HTTP result with flow status
    http_result["flow_status"] = flow_status
    return http_result


def format_drop_request_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    """Formats a drop request result into a standardized summary format.
    
    Args:
        result: The raw drop request result from NiFi
        
    Returns:
        Dict containing formatted summary with consistent fields
    """
    # Extract basic fields
    success = result.get("success", False)
    message = result.get("message", "")
    results = result.get("results", [])
    
    # Calculate summary metrics
    total_connections = len(results)
    successful_drops = len([r for r in results if r.get("success", False)])
    failed_drops = total_connections - successful_drops
    
    # Parse dropped count from format like "1 / 0 bytes"
    def parse_dropped_count(count_str: str) -> int:
        try:
            # Split on '/' and take first number
            return int(count_str.split('/')[0].strip())
        except (ValueError, AttributeError, IndexError):
            return 0
            
    total_dropped = sum(parse_dropped_count(str(r.get("dropped_count", "0"))) 
                       for r in results if r.get("success", False))
    
    return {
        "success": success,
        "message": message,
        "total_connections": total_connections,
        "successful_drops": successful_drops,
        "failed_drops": failed_drops,
        "total_dropped": total_dropped,
        "results": results
    }

@mcp.tool()
@tool_phases(["Operate"])
async def purge_flowfiles(
    target_id: str,
    target_type: Literal["connection", "process_group"],
    timeout_seconds: Optional[int] = 30
) -> Dict[str, Any]:
    """Purges all FlowFiles from a connection or all connections in a process group.
    
    This tool will wait for the purge operation to complete or until the timeout is reached.
    For process groups, it will attempt to purge all connections and report any failures.
    
    Args:
        target_id: The ID of the connection or process group to purge
        target_type: Either "connection" for a single connection or "process_group" for all connections in a PG
        timeout_seconds: Maximum time in seconds to wait for each purge operation (default: 30)
        
    Returns:
        Dict containing:
            - success: Whether all purge operations succeeded
            - message: Summary message
            - summary: Detailed summary of operations
            - results: List of individual purge results
    """
    nifi_client = current_nifi_client.get()
    local_logger = current_request_logger.get()
    
    if target_type == "connection":
        # Single connection purge
        result = await _handle_drop_request(nifi_client, target_id, timeout_seconds, local_logger)
        formatted_result = format_drop_request_summary(result)
        return formatted_result
    
    else:  # process_group
        # Use the client's process group purge method
        results = await nifi_client.purge_process_group_flowfiles(target_id, timeout_seconds)
        return format_drop_request_summary(results)


@mcp.tool()
@tool_phases(["Debug"])
async def analyze_nifi_processor_errors(
    processor_id: str,
    include_suggestions: bool = True
) -> Dict[str, Any]:
    """
    Analyze processor errors and provide debugging suggestions for faster resolution.
    
    EFFICIENCY IMPROVEMENT: Proactive error analysis to reduce debugging iterations.
    
    Args:
        processor_id: The ID of the processor to analyze
        include_suggestions: Whether to include automated debugging suggestions
        
    Returns:
        Analysis results including error patterns, suggestions, and potential fixes
    """
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")

    # Context IDs for logging (not currently used in this tool)
    # user_request_id = current_user_request_id.get() or "-"
    # action_id = current_action_id.get() or "-"

    try:
        # Get processor details
        processor_details = await nifi_client.get_processor_details(processor_id)
        processor_name = processor_details.get("component", {}).get("name", "Unknown")
        processor_type = processor_details.get("component", {}).get("type", "Unknown")
        validation_status = processor_details.get("component", {}).get("validationStatus", "UNKNOWN")
        
        # Get bulletins (error messages)
        bulletins = processor_details.get("bulletins", [])
        
        # Analyze errors
        analysis = {
            "processor_id": processor_id,
            "processor_name": processor_name,
            "processor_type": processor_type,
            "validation_status": validation_status,
            "error_count": len(bulletins),
            "errors": [],
            "patterns": [],
            "suggestions": [] if include_suggestions else None
        }
        
        # Process each bulletin
        for bulletin in bulletins:
            bulletin_data = bulletin.get("bulletin", {})
            if bulletin_data.get("level") == "ERROR":
                error_info = {
                    "timestamp": bulletin_data.get("timestamp"),
                    "message": bulletin_data.get("message"),
                    "category": bulletin_data.get("category")
                }
                analysis["errors"].append(error_info)
                
                # Analyze error patterns
                if include_suggestions:
                    patterns = _analyze_error_patterns(bulletin_data.get("message", ""), processor_type)
                    analysis["patterns"].extend(patterns)
        
        # Generate suggestions based on patterns
        if include_suggestions and analysis["patterns"]:
            analysis["suggestions"] = _generate_debugging_suggestions(analysis["patterns"], processor_type)
        
        local_logger.info(f"Analyzed {len(bulletins)} bulletins for processor {processor_name}")
        
        return {
            "status": "success",
            "message": f"Analyzed processor '{processor_name}' errors",
            "analysis": analysis
        }
        
    except Exception as e:
        local_logger.error(f"Error analyzing processor {processor_id}: {e}", exc_info=True)
        raise ToolError(f"Failed to analyze processor errors: {e}")


def _analyze_error_patterns(error_message: str, processor_type: str) -> List[str]:
    """Analyze error message for common patterns."""
    patterns = []
    error_lower = error_message.lower()
    
    # Script processor patterns
    if "org.apache.nifi.processors.script.ExecuteScript" in processor_type:
        if "missingpropertyexception" in error_lower:
            if "flowfile" in error_lower:
                patterns.append("groovy_flowfile_scope_issue")
        if "scriptexception" in error_lower:
            patterns.append("groovy_script_syntax_error")
        if "compilationexception" in error_lower:
            patterns.append("groovy_compilation_error")
    
    # HTTP processor patterns  
    if "handlehttprequest" in processor_type.lower() or "handlehttpresponse" in processor_type.lower():
        if "context map" in error_lower:
            patterns.append("http_context_map_missing")
        if "connection refused" in error_lower:
            patterns.append("http_connection_issue")
    
    # General patterns
    if "validation" in error_lower and "invalid" in error_lower:
        patterns.append("property_validation_error")
    if "no such property" in error_lower:
        patterns.append("missing_property_reference")
    
    return patterns


def _generate_debugging_suggestions(patterns: List[str], processor_type: str) -> List[Dict[str, str]]:
    """Generate debugging suggestions based on error patterns."""
    suggestions = []
    
    for pattern in patterns:
        if pattern == "groovy_flowfile_scope_issue":
            suggestions.append({
                "issue": "Groovy FlowFile Scope Issue",
                "description": "Script references 'flowFile' variable that's not in scope",
                "solution": "Use 'final FlowFile ff = session.get()' and import org.apache.nifi.flowfile.FlowFile",
                "example": "import org.apache.nifi.flowfile.FlowFile\nfinal FlowFile ff = session.get()\nif (ff == null) return"
            })
        
        elif pattern == "groovy_script_syntax_error":
            suggestions.append({
                "issue": "Groovy Script Syntax Error", 
                "description": "Script has syntax errors preventing execution",
                "solution": "Check script syntax, imports, and variable declarations",
                "example": "Ensure all imports are at the top and variables are properly declared"
            })
        
        elif pattern == "http_context_map_missing":
            suggestions.append({
                "issue": "HTTP Context Map Service Missing",
                "description": "HTTP processor requires a context map service to be configured",
                "solution": "Create and configure an HTTP Context Map service, then reference it in the processor",
                "example": "Create StandardHttpContextMap service and reference it in 'HTTP Context Map' property"
            })
        
        elif pattern == "property_validation_error":
            suggestions.append({
                "issue": "Property Validation Error",
                "description": "One or more processor properties have invalid values",
                "solution": "Review processor configuration and correct invalid property values",
                "example": "Check for empty strings in optional properties - use null instead"
            })
    
    return suggestions
