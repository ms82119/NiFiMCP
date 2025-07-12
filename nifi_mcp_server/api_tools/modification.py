import asyncio
from typing import List, Dict, Optional, Any, Union, Literal, Tuple
from typing_extensions import TypedDict

# Import necessary components from parent/utils
from loguru import logger
# Import mcp ONLY
from ..core import mcp, handle_nifi_errors, _get_component_details_direct
# Removed nifi_api_client import
# Import context variables
from ..request_context import current_nifi_client, current_request_logger # Added
from config import settings as mcp_settings # Corrected import

from .utils import (
    tool_phases,
    # ensure_authenticated, # Removed
    filter_created_processor_data,
    filter_connection_data,
    filter_controller_service_data,  # Add controller service filter
    smart_parameter_validation
)
from .review import get_nifi_object_details, list_nifi_objects  # Import both functions at the top
from nifi_mcp_server.nifi_client import NiFiClient, NiFiAuthenticationError
from mcp.server.fastmcp.exceptions import ToolError



# --- Tool Definitions --- 

def _requires_restart_for_property_change(processor_type: str, property_name: str, old_value: Any, new_value: Any) -> bool:
    """
    Determine if a processor restart is required for a specific property change.
    
    EFFICIENCY IMPROVEMENT: Avoid unnecessary restarts for properties that can be updated without restart.
    """
    # Properties that don't require restart for most processors
    non_restart_properties = {
        "Log Level",
        "Bulletin Level", 
        "Yield Duration",
        "Penalty Duration",
        "Concurrent Tasks",
        "Scheduling Strategy",
        "Scheduling Period"
    }
    
    if property_name in non_restart_properties:
        return False
    
    # Script processors need restart for script changes
    if processor_type == "org.apache.nifi.processors.script.ExecuteScript":
        script_properties = {"Script Body", "Script File", "Module Directory"}
        if property_name in script_properties:
            return True
    
    # HTTP processors don't need restart for most property changes
    if "HandleHttp" in processor_type:
        http_non_restart = {"HTTP Status Code", "Attributes to add to the HTTP Response (Regex)"}
        if property_name in http_non_restart:
            return False
    
    # Default: assume restart needed for safety
    return True

async def _validate_and_resolve_update_properties(
    processor_type: str,
    properties: Dict[str, Any],
    process_group_id: str,
    nifi_client: NiFiClient,
    logger,
    user_request_id: str,
    action_id: str
) -> tuple[Dict[str, Any], list[str], list[str]]:
    """
    Validates and resolves service references in processor property updates.
    
    This function mirrors the validation and resolution logic from create_complete_nifi_flow
    but is specifically optimized for property updates.
    
    Args:
        processor_type: The type of processor being updated
        properties: The properties to validate and resolve
        process_group_id: The process group ID for service lookup
        nifi_client: NiFi client instance
        logger: Logger instance
        user_request_id: Request ID for logging
        action_id: Action ID for logging
        
    Returns:
        tuple[resolved_properties, warnings, errors]
    """
    resolved_properties = properties.copy()
    warnings = []
    errors = []
    
    try:
        # Build service map from process group
        service_map = {}
        service_type_map = {}  # Separate map for type checking
        services = await nifi_client.list_controller_services(process_group_id, user_request_id=user_request_id, action_id=action_id)
        for service in services:
            service_name = service.get("component", {}).get("name", "")
            service_id = service.get("id", "")
            service_type = service.get("component", {}).get("type", "")
            if service_name and service_id:
                service_map[service_name] = service_id
                service_type_map[service_name] = service_type
        logger.info(f"Built service map with {len(service_map)} services: {list(service_map.keys())}")
        
        # Get processor property descriptors (using hardcoded approach like creation tools)
        # For now, we'll use a simplified approach that doesn't require dynamic property descriptor lookup
        # This matches the approach used in creation.py
        property_descriptors = {}
        
        # Validate and resolve each property
        for prop_name, prop_value in list(resolved_properties.items()):
            if not isinstance(prop_value, str):
                continue
                
            # Handle service references (simplified approach)
            if _looks_like_service_reference(prop_value):
                resolved_value, is_resolved = await _resolve_service_reference(
                    prop_value, service_map, process_group_id, nifi_client,
                    logger, user_request_id, action_id
                )
                
                if is_resolved:
                    if resolved_value != prop_value:
                        resolved_properties[prop_name] = resolved_value
                        warnings.append(f"Resolved service reference '{prop_name}': '{prop_value}' → '{resolved_value}'")
                else:
                    errors.append(f"Could not resolve service reference '{prop_name}': '{prop_value}'")
                
    except Exception as e:
        logger.error(f"Error validating properties: {e}", exc_info=True)
        errors.append(f"Property validation failed: {e}")
        
    return resolved_properties, warnings, errors

# DEPRECATED: Use update_nifi_processors_properties instead
# This function is kept for backward compatibility but will be removed
async def _update_nifi_processor_properties_legacy(
    processor_id: str,
    processor_config_properties: Dict[str, Any]
) -> Dict:
    """
    Updates a processor's configuration properties by *replacing* the existing property dictionary.
    
    Automatically stops running processors if Auto-Stop feature is enabled, then performs the update.
    If the processor was originally running and the update is valid, automatically restarts it.
    If Auto-Stop is disabled, running processors will cause an error.

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
        A dictionary with enhanced status information including property update and restart status.
        Possible status values: "success", "partial_success", "warning", "error"
    """
    # Get client and logger from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
         
    # Authentication handled by factory
    from ..request_context import current_user_request_id, current_action_id
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"

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
        original_state = component_precheck.get("state")  # Capture original state
        current_revision = current_entity.get("revision")
        precheck_resp = {"id": processor_id, "state": original_state, "version": current_revision.get('version') if current_revision else None}
        local_logger.bind(interface="nifi", direction="response", data=precheck_resp).debug("Received from NiFi API (pre-check)")
        
        local_logger.info(f"Processor original state: {original_state}")
        
        # --- AUTO-STOP LOGIC ---
        if original_state == "RUNNING":
            local_logger.info(f"Processor '{component_precheck.get('name', processor_id)}' is RUNNING. Checking Auto-Stop feature.")
            
            # Get headers from request context
            from config.logging_setup import request_context
            context_data = request_context.get()
            request_headers = context_data.get('headers', {}) if context_data else {}
            
            # Convert header keys to lowercase for case-insensitive comparison
            if request_headers:
                request_headers = {k.lower(): v for k, v in request_headers.items()}
            
            is_auto_stop_feature_enabled = mcp_settings.get_feature_auto_stop_enabled(headers=request_headers)
            local_logger.info(f"[Auto-Stop] Feature flag check - headers: {request_headers}")
            local_logger.info(f"[Auto-Stop] Feature enabled: {is_auto_stop_feature_enabled}")

            if is_auto_stop_feature_enabled:
                # Stop the processor first
                local_logger.info(f"[Auto-Stop] Stopping processor {processor_id}")
                try:
                    nifi_request_data = {"operation": "stop_processor", "processor_id": processor_id}
                    local_logger.bind(interface="nifi", direction="request", data=nifi_request_data).debug("Calling NiFi API")
                    await nifi_client.stop_processor(processor_id)
                    local_logger.bind(interface="nifi", direction="response", data={"status": "success"}).debug("Received from NiFi API")
                    
                    # Wait for processor to fully stop
                    max_wait_seconds = 15
                    for attempt in range(max_wait_seconds):
                        updated_details = await nifi_client.get_processor_details(processor_id)
                        current_state = updated_details.get("component", {}).get("state")
                        if current_state == "STOPPED":
                            local_logger.info(f"[Auto-Stop] Confirmed processor {processor_id} is stopped")
                            # Update our references with the latest details
                            current_entity = updated_details
                            component_precheck = current_entity.get("component", {})
                            current_revision = current_entity.get("revision")
                            break
                        
                        if attempt == max_wait_seconds - 1:
                            raise ToolError(f"Processor {processor_id} did not stop after {max_wait_seconds} seconds")
                        
                        local_logger.info(f"[Auto-Stop] Waiting for processor to stop (attempt {attempt + 1}/{max_wait_seconds})")
                        await asyncio.sleep(1)

                except Exception as e:
                    local_logger.error(f"[Auto-Stop] Failed to stop processor: {e}", exc_info=True)
                    local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
                    return {"status": "error", "message": f"Failed to auto-stop processor for update: {e}", "entity": None}
            else:
                error_msg = f"Processor '{component_precheck.get('name', processor_id)}' is RUNNING. Stop it before updating properties or enable Auto-Stop."
                local_logger.warning(error_msg)
                return {"status": "error", "message": error_msg, "entity": None}
        
        if not current_revision:
             raise ToolError(f"Could not retrieve revision for processor {processor_id}.")
             
        # Get processor type for validation
        processor_type = component_precheck.get("type")
        if not processor_type:
            raise ToolError(f"Could not determine processor type for {processor_id}")
            
        # Get process group ID for service resolution
        process_group_id = component_precheck.get("parentGroupId")
        if not process_group_id:
            raise ToolError(f"Could not determine process group for processor {processor_id}")
            
        # Validate and resolve properties
        resolved_properties, warnings, errors = await _validate_and_resolve_update_properties(
            processor_type=processor_type,
            properties=processor_config_properties,
            process_group_id=process_group_id,
            nifi_client=nifi_client,
            logger=local_logger,
            user_request_id=user_request_id,
            action_id=action_id
        )
        
        # Handle validation errors
        if errors:
            error_msg = f"Property validation failed: {'; '.join(errors)}"
            local_logger.error(error_msg)
            return {
                "status": "error",
                "message": error_msg,
                "validation_errors": errors,
                "entity": None
            }
            
        # Proceed with update using resolved properties
        nifi_update_req = {
            "operation": "update_processor_config",
            "processor_id": processor_id,
            "update_type": "properties",
            "update_data": resolved_properties
        }
        local_logger.bind(interface="nifi", direction="request", data=nifi_update_req).debug("Calling NiFi API")
        updated_entity = await nifi_client.update_processor_config(
            processor_id=processor_id,
            update_type="properties",
            update_data=resolved_properties
        )
        filtered_updated_entity = filter_created_processor_data(updated_entity)
        local_logger.bind(interface="nifi", direction="response", data=filtered_updated_entity).debug("Received from NiFi API")

        local_logger.info(f"Successfully updated properties for processor {processor_id}")

        component = updated_entity.get("component", {})
        validation_status = component.get("validationStatus", "UNKNOWN")
        validation_errors = component.get("validationErrors", [])
        name = component.get("name", processor_id)

        # Initialize status tracking
        property_update_status = {"status": "success"}
        restart_status = {"status": "not_attempted", "reason": "processor was not originally running"}
        
        # --- AUTO-RESTART LOGIC ---
        if original_state == "RUNNING" and validation_status == "VALID":
            local_logger.info(f"[Auto-Restart] Processor was originally running and validation is VALID. Attempting restart.")
            restart_status = {"status": "attempting"}
            
            try:
                # Start the processor
                nifi_start_req = {"operation": "start_processor", "processor_id": processor_id}
                local_logger.bind(interface="nifi", direction="request", data=nifi_start_req).debug("Calling NiFi API")
                await nifi_client.start_processor(processor_id)
                local_logger.bind(interface="nifi", direction="response", data={"status": "success"}).debug("Received from NiFi API")
                
                # Wait for processor to fully start
                max_wait_seconds = 15
                for attempt in range(max_wait_seconds):
                    restarted_details = await nifi_client.get_processor_details(processor_id)
                    current_state = restarted_details.get("component", {}).get("state")
                    if current_state == "RUNNING":
                        local_logger.info(f"[Auto-Restart] Confirmed processor {processor_id} is running")
                        # Update entity with latest details
                        updated_entity = restarted_details
                        filtered_updated_entity = filter_created_processor_data(updated_entity)
                        restart_status = {"status": "success", "final_state": "RUNNING"}
                        break
                    
                    if attempt == max_wait_seconds - 1:
                        # Timeout - processor started but didn't reach RUNNING state
                        restart_status = {
                            "status": "timeout", 
                            "reason": f"Processor didn't reach RUNNING state within {max_wait_seconds} seconds",
                            "current_state": current_state
                        }
                        local_logger.warning(f"[Auto-Restart] Processor {processor_id} start timeout. Current state: {current_state}")
                        break
                    
                    local_logger.info(f"[Auto-Restart] Waiting for processor to start (attempt {attempt + 1}/{max_wait_seconds})")
                    await asyncio.sleep(1)
                    
            except ValueError as e:
                error_str = str(e).lower()
                if "validation" in error_str:
                    restart_status = {
                        "status": "failed", 
                        "reason": "Runtime validation failed during start",
                        "details": str(e)
                    }
                elif "connection" in error_str or "relationship" in error_str:
                    restart_status = {
                        "status": "dependency_error", 
                        "reason": "Missing connections or relationship configuration",
                        "details": str(e)
                    }
                else:
                    restart_status = {
                        "status": "failed", 
                        "reason": "Start operation failed",
                        "details": str(e)
                    }
                local_logger.warning(f"[Auto-Restart] Failed to restart processor: {e}")
                
            except (NiFiAuthenticationError, ConnectionError) as e:
                restart_status = {
                    "status": "api_error", 
                    "reason": "API error during restart",
                    "details": str(e)
                }
                local_logger.error(f"[Auto-Restart] API error during restart: {e}")
                
            except Exception as e:
                restart_status = {
                    "status": "unexpected_error", 
                    "reason": "Unexpected error during restart",
                    "details": str(e)
                }
                local_logger.error(f"[Auto-Restart] Unexpected error during restart: {e}", exc_info=True)
                
        elif original_state == "RUNNING" and validation_status != "VALID":
            restart_status = {
                "status": "skipped", 
                "reason": f"Validation status is {validation_status}, not attempting restart",
                "validation_errors": validation_errors
            }
            local_logger.info(f"[Auto-Restart] Skipping restart due to validation status: {validation_status}")

        # --- DETERMINE OVERALL STATUS AND MESSAGE ---
        if validation_status != "VALID":
            # Property update succeeded but validation issues
            property_update_status["status"] = "warning"
            error_msg_snippet = f" ({validation_errors[0]})" if validation_errors else ""
            
            if restart_status["status"] == "skipped":
                return {
                    "status": "warning",
                    "message": f"Processor '{name}' properties updated, but validation status is {validation_status}{error_msg_snippet}. Restart skipped due to validation issues.",
                    "property_update": property_update_status,
                    "restart_status": restart_status,
                    "entity": filtered_updated_entity
                }
            else:
                return {
                    "status": "warning",
                    "message": f"Processor '{name}' properties updated, but validation status is {validation_status}{error_msg_snippet}. Check configuration.",
                    "property_update": property_update_status,
                    "restart_status": restart_status,
                    "entity": filtered_updated_entity
                }
                
        elif restart_status["status"] == "success":
            # Both property update and restart succeeded
            return {
                "status": "success",
                "message": f"Processor '{name}' properties updated successfully. Processor restarted and is now running.",
                "property_update": property_update_status,
                "restart_status": restart_status,
                "entity": filtered_updated_entity
            }
            
        elif restart_status["status"] in ["failed", "timeout", "api_error", "dependency_error", "unexpected_error"]:
            # Property update succeeded but restart failed
            user_action = "Please manually start the processor if desired."
            if restart_status["status"] == "dependency_error":
                user_action = "Check processor connections and relationships, then manually start if desired."
            elif restart_status["status"] == "timeout":
                user_action = f"Processor may still be starting (current state: {restart_status.get('current_state', 'unknown')}). Check processor status."
                
            return {
                "status": "partial_success",
                "message": f"Processor '{name}' properties updated successfully, but restart failed: {restart_status['reason']}.",
                "property_update": property_update_status,
                "restart_status": restart_status,
                "entity": filtered_updated_entity,
                "user_action_required": user_action
            }
            
        else:
            # Property update succeeded, no restart needed or attempted
            return {
                "status": "success",
                "message": f"Processor '{name}' properties updated successfully.",
                "property_update": property_update_status,
                "restart_status": restart_status,
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


async def _validate_and_resolve_controller_service_properties(
    service_type: str,
    properties: Dict[str, Any],
    process_group_id: str,
    nifi_client: NiFiClient,
    logger,
    user_request_id: str,
    action_id: str
) -> tuple[Dict[str, Any], list[str], list[str]]:
    """
    Validates and resolves service references in controller service property updates.
    
    Similar to _validate_and_resolve_update_properties but specialized for controller services.
    
    Args:
        service_type: The type of controller service being updated
        properties: The properties to validate and resolve
        process_group_id: The process group ID for service lookup
        nifi_client: NiFi client instance
        logger: Logger instance
        user_request_id: Request ID for logging
        action_id: Action ID for logging
        
    Returns:
        tuple[resolved_properties, warnings, errors]
    """
    resolved_properties = properties.copy()
    warnings = []
    errors = []
    
    try:
        # Build service map from process group
        service_map = {}
        service_type_map = {}  # Add this line
        services = await nifi_client.list_controller_services(process_group_id, user_request_id=user_request_id, action_id=action_id)
        for service in services:
            service_name = service.get("component", {}).get("name", "")
            service_id = service.get("id", "")
            service_type = service.get("component", {}).get("type", "")
            if service_name and service_id:
                service_map[service_name] = service_id 
                service_type_map[service_name] = service_type  
        logger.info(f"Built service map with {len(service_map)} services: {list(service_map.keys())}")
        
        # Get service property descriptors (using simplified approach like processors)
        # For now, we'll use a simplified approach that doesn't require dynamic property descriptor lookup
        property_descriptors = {}
        
        # Validate and resolve each property
        for prop_name, prop_value in list(resolved_properties.items()):
            if not isinstance(prop_value, str):
                continue
                
            # Handle service references (simplified approach)
            if _looks_like_service_reference(prop_value):
                resolved_value, is_resolved = await _resolve_service_reference(
                    prop_value, service_map, process_group_id, nifi_client,
                    logger, user_request_id, action_id
                )
                
                if is_resolved:
                    if resolved_value != prop_value:
                        resolved_properties[prop_name] = resolved_value
                        warnings.append(f"Resolved service reference '{prop_name}': '{prop_value}' → '{resolved_value}'")
                else:
                    errors.append(f"Could not resolve service reference '{prop_name}': '{prop_value}'")
                
    except Exception as e:
        logger.error(f"Error validating properties: {e}", exc_info=True)
        errors.append(f"Property validation failed: {e}")
        
    return resolved_properties, warnings, errors

@mcp.tool()
@tool_phases(["Modify"])
async def update_controller_service_properties(
    controller_service_id: str,
    controller_service_properties: Dict[str, Any]
) -> Dict:
    """
    Updates a controller service's configuration properties by replacing the existing property dictionary.
    
    Automatically disables enabled controller services, then performs the update.
    If the controller service was originally enabled and the update is valid, automatically re-enables it.

    Args:
        controller_service_id: The UUID of the controller service to update.
        controller_service_properties: A complete dictionary representing the desired final state of all properties. Cannot be empty.

    Returns:
        A dictionary with enhanced status information including property update and restart status.
        Possible status values: "success", "partial_success", "warning", "error"
    """
    # Get client and logger from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
         
    # Authentication handled by factory
    from ..request_context import current_user_request_id, current_action_id
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"

    if not controller_service_properties:
        error_msg = "The 'controller_service_properties' argument cannot be empty. Fetch current config first."
        local_logger.warning(f"Validation failed for update_controller_service_properties (ID={controller_service_id}): {error_msg}")
        raise ToolError(error_msg)

    if not isinstance(controller_service_properties, dict):
         raise ToolError(f"Invalid 'controller_service_properties' type. Expected dict, got {type(controller_service_properties)}.")

    local_logger = local_logger.bind(controller_service_id=controller_service_id)
    local_logger.info(f"Executing update_controller_service_properties with properties: {controller_service_properties}")
    
    try:
        local_logger.info(f"Fetching current details for controller service {controller_service_id} before update.")
        nifi_get_req = {"operation": "get_controller_service_details", "controller_service_id": controller_service_id}
        local_logger.bind(interface="nifi", direction="request", data=nifi_get_req).debug("Calling NiFi API")
        current_entity = await nifi_client.get_controller_service_details(controller_service_id, user_request_id=user_request_id, action_id=action_id)
        component_precheck = current_entity.get("component", {})
        original_state = component_precheck.get("state")  # Capture original state (ENABLED/DISABLED)
        current_revision = current_entity.get("revision")
        precheck_resp = {"id": controller_service_id, "state": original_state, "version": current_revision.get('version') if current_revision else None}
        local_logger.bind(interface="nifi", direction="response", data=precheck_resp).debug("Received from NiFi API (pre-check)")
        
        local_logger.info(f"Controller service original state: {original_state}")
        
        # --- AUTO-DISABLE LOGIC ---
        if original_state == "ENABLED":
            local_logger.info(f"Controller service '{component_precheck.get('name', controller_service_id)}' is ENABLED. Auto-disabling for property update.")
            
            try:
                nifi_request_data = {"operation": "disable_controller_service", "controller_service_id": controller_service_id}
                local_logger.bind(interface="nifi", direction="request", data=nifi_request_data).debug("Calling NiFi API")
                await nifi_client.disable_controller_service(controller_service_id, user_request_id=user_request_id, action_id=action_id)
                local_logger.bind(interface="nifi", direction="response", data={"status": "success"}).debug("Received from NiFi API")
                
                # Wait for controller service to fully disable
                max_wait_seconds = 15
                for attempt in range(max_wait_seconds):
                    updated_details = await nifi_client.get_controller_service_details(controller_service_id, user_request_id=user_request_id, action_id=action_id)
                    current_state = updated_details.get("component", {}).get("state")
                    if current_state == "DISABLED":
                        local_logger.info(f"[Auto-Disable] Confirmed controller service {controller_service_id} is disabled")
                        # Update our references with the latest details
                        current_entity = updated_details
                        component_precheck = current_entity.get("component", {})
                        current_revision = current_entity.get("revision")
                        break
                    
                    if attempt == max_wait_seconds - 1:
                        raise ToolError(f"Controller service {controller_service_id} did not disable after {max_wait_seconds} seconds")
                    
                    local_logger.info(f"[Auto-Disable] Waiting for controller service to disable (attempt {attempt + 1}/{max_wait_seconds})")
                    await asyncio.sleep(1)

            except Exception as e:
                local_logger.error(f"[Auto-Disable] Failed to disable controller service: {e}", exc_info=True)
                local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
                return {"status": "error", "message": f"Failed to auto-disable controller service for update: {e}", "entity": None}
        
        if not current_revision:
             raise ToolError(f"Could not retrieve revision for controller service {controller_service_id}.")
             
        # Get service type for validation
        service_type = component_precheck.get("type")
        if not service_type:
            raise ToolError(f"Could not determine service type for {controller_service_id}")
            
        # Get process group ID for service resolution
        process_group_id = component_precheck.get("parentGroupId")
        if not process_group_id:
            raise ToolError(f"Could not determine process group for service {controller_service_id}")
            
        # Validate and resolve properties
        resolved_properties, warnings, errors = await _validate_and_resolve_controller_service_properties(
            service_type=service_type,
            properties=controller_service_properties,
            process_group_id=process_group_id,
            nifi_client=nifi_client,
            logger=local_logger,
            user_request_id=user_request_id,
            action_id=action_id
        )
        
        # Handle validation errors
        if errors:
            error_msg = f"Property validation failed: {'; '.join(errors)}"
            local_logger.error(error_msg)
            return {
                "status": "error",
                "message": error_msg,
                "validation_errors": errors,
                "entity": None
            }
            
        # Proceed with update using resolved properties
        nifi_update_req = {
            "operation": "update_controller_service_properties",
            "controller_service_id": controller_service_id,
            "properties": resolved_properties
        }
        local_logger.bind(interface="nifi", direction="request", data=nifi_update_req).debug("Calling NiFi API")
        updated_entity = await nifi_client.update_controller_service_properties(
            controller_service_id=controller_service_id,
            properties=resolved_properties,
            user_request_id=user_request_id,
            action_id=action_id
        )
        filtered_updated_entity = filter_controller_service_data(updated_entity)
        local_logger.bind(interface="nifi", direction="response", data=filtered_updated_entity).debug("Received from NiFi API")

        local_logger.info(f"Successfully updated properties for controller service {controller_service_id}")

        component = updated_entity.get("component", {})
        validation_status = component.get("validationStatus", "UNKNOWN")
        validation_errors = component.get("validationErrors", [])
        name = component.get("name", controller_service_id)

        # Initialize status tracking
        property_update_status = {"status": "success"}
        restart_status = {"status": "not_attempted", "reason": "controller service was not originally enabled"}
        
        # --- AUTO-ENABLE LOGIC ---
        if original_state == "ENABLED" and validation_status == "VALID":
            local_logger.info(f"[Auto-Enable] Controller service was originally enabled and validation is VALID. Attempting re-enable.")
            restart_status = {"status": "attempting"}
            
            try:
                # Enable the controller service
                nifi_enable_req = {"operation": "enable_controller_service", "controller_service_id": controller_service_id}
                local_logger.bind(interface="nifi", direction="request", data=nifi_enable_req).debug("Calling NiFi API")
                await nifi_client.enable_controller_service(controller_service_id, user_request_id=user_request_id, action_id=action_id)
                local_logger.bind(interface="nifi", direction="response", data={"status": "success"}).debug("Received from NiFi API")
                
                # Wait for controller service to fully enable
                max_wait_seconds = 15
                for attempt in range(max_wait_seconds):
                    reenabled_details = await nifi_client.get_controller_service_details(controller_service_id, user_request_id=user_request_id, action_id=action_id)
                    current_state = reenabled_details.get("component", {}).get("state")
                    if current_state == "ENABLED":
                        local_logger.info(f"[Auto-Enable] Confirmed controller service {controller_service_id} is enabled")
                        # Update the entity with the latest details
                        updated_entity = reenabled_details
                        filtered_updated_entity = filter_controller_service_data(updated_entity)
                        restart_status = {"status": "success", "final_state": "ENABLED"}
                        break
                    
                    if attempt == max_wait_seconds - 1:
                        restart_status = {
                            "status": "timeout",
                            "reason": f"Controller service did not enable after {max_wait_seconds} seconds",
                            "current_state": current_state
                        }
                        break
                    
                    local_logger.info(f"[Auto-Enable] Waiting for controller service to enable (attempt {attempt + 1}/{max_wait_seconds})")
                    await asyncio.sleep(1)

            except Exception as e:
                local_logger.error(f"[Auto-Enable] Failed to re-enable controller service: {e}", exc_info=True)
                restart_status = {
                    "status": "failed",
                    "reason": f"Enable operation failed: {e}",
                    "error_type": type(e).__name__
                }
        elif original_state == "ENABLED" and validation_status != "VALID":
            restart_status = {
                "status": "skipped",
                "reason": f"Validation status is {validation_status}, cannot auto-enable",
                "validation_errors": validation_errors
            }
            local_logger.warning(f"[Auto-Enable] Skipping auto-enable due to validation status: {validation_status}")

        # Return appropriate response based on status
        if restart_status["status"] == "success":
            return {
                "status": "success",
                "message": f"Controller service '{name}' properties updated and re-enabled successfully.",
                "property_update": property_update_status,
                "restart_status": restart_status,
                "entity": filtered_updated_entity
            }
        elif restart_status["status"] == "skipped":
            return {
                "status": "warning",
                "message": f"Controller service '{name}' properties updated but left disabled due to validation errors.",
                "property_update": property_update_status,
                "restart_status": restart_status,
                "entity": filtered_updated_entity,
                "user_action_required": "Review validation errors and manually enable when resolved."
            }
        elif restart_status["status"] in ["failed", "timeout"]:
            # Property update succeeded but enable failed
            user_action = "Please manually enable the controller service if desired."
            if restart_status["status"] == "timeout":
                user_action = f"Controller service may still be enabling (current state: {restart_status.get('current_state', 'unknown')}). Check service status."
                
            return {
                "status": "partial_success",
                "message": f"Controller service '{name}' properties updated successfully, but re-enable failed: {restart_status['reason']}.",
                "property_update": property_update_status,
                "restart_status": restart_status,
                "entity": filtered_updated_entity,
                "user_action_required": user_action
            }
        else:
            # Property update succeeded, no re-enable needed or attempted
            return {
                "status": "success",
                "message": f"Controller service '{name}' properties updated successfully.",
                "property_update": property_update_status,
                "restart_status": restart_status,
                "entity": filtered_updated_entity
            }

        # Add warnings to response if any
        if warnings:
            property_update_status["warnings"] = warnings

    except ValueError as e:
        local_logger.warning(f"Error updating controller service properties: {e}")
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"Error updating properties: {e}", "entity": None}
    except (NiFiAuthenticationError, ConnectionError, ToolError) as e:
        local_logger.error(f"API/Tool error updating controller service properties: {e}", exc_info=False)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"Failed to update properties: {e}", "entity": None}
    except Exception as e:
        local_logger.error(f"Unexpected error updating controller service properties: {e}", exc_info=True)
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
    
    Automatically stops running processors if Auto-Stop feature is enabled, then performs the update.
    If Auto-Stop is disabled, running processors will cause an error.

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

        # --- AUTO-STOP LOGIC ---
        if current_state == "RUNNING":
            local_logger.info(f"Processor '{current_component.get('name', processor_id)}' is RUNNING. Checking Auto-Stop feature.")
            
            # Get headers from request context
            from config.logging_setup import request_context
            context_data = request_context.get()
            request_headers = context_data.get('headers', {}) if context_data else {}
            
            # Convert header keys to lowercase for case-insensitive comparison
            if request_headers:
                request_headers = {k.lower(): v for k, v in request_headers.items()}
            
            is_auto_stop_feature_enabled = mcp_settings.get_feature_auto_stop_enabled(headers=request_headers)
            local_logger.info(f"[Auto-Stop] Feature flag check - headers: {request_headers}")
            local_logger.info(f"[Auto-Stop] Feature enabled: {is_auto_stop_feature_enabled}")

            if is_auto_stop_feature_enabled:
                # Stop the processor first
                local_logger.info(f"[Auto-Stop] Stopping processor {processor_id}")
                try:
                    nifi_request_data = {"operation": "stop_processor", "processor_id": processor_id}
                    local_logger.bind(interface="nifi", direction="request", data=nifi_request_data).debug("Calling NiFi API")
                    await nifi_client.stop_processor(processor_id)
                    local_logger.bind(interface="nifi", direction="response", data={"status": "success"}).debug("Received from NiFi API")
                    
                    # Wait for processor to fully stop
                    max_wait_seconds = 15
                    for attempt in range(max_wait_seconds):
                        updated_details = await nifi_client.get_processor_details(processor_id)
                        current_state = updated_details.get("component", {}).get("state")
                        if current_state == "STOPPED":
                            local_logger.info(f"[Auto-Stop] Confirmed processor {processor_id} is stopped")
                            # Update our references with the latest details
                            current_entity = updated_details
                            current_component = current_entity.get("component", {})
                            current_revision = current_entity.get("revision")
                            break
                        
                        if attempt == max_wait_seconds - 1:
                            raise ToolError(f"Processor {processor_id} did not stop after {max_wait_seconds} seconds")
                        
                        local_logger.info(f"[Auto-Stop] Waiting for processor to stop (attempt {attempt + 1}/{max_wait_seconds})")
                        await asyncio.sleep(1)

                except Exception as e:
                    local_logger.error(f"[Auto-Stop] Failed to stop processor: {e}", exc_info=True)
                    local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
                    return {"status": "error", "message": f"Failed to auto-stop processor for update: {e}", "entity": None}
            else:
                error_msg = f"Processor '{current_component.get('name', processor_id)}' is RUNNING. Stop it before deleting properties or enable Auto-Stop."
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
    
    Automatically stops running processors if Auto-Stop feature is enabled, then performs the update.
    If Auto-Stop is disabled, running processors will cause an error.
    
    Intelligently handles connections that would become invalid:
    - If Auto-Delete feature is enabled, automatically deletes connections using relationships being auto-terminated
    - If Auto-Delete is disabled, warns about connections that will become invalid
    - Validates post-update to ensure no invalid connections remain

    Args:
        processor_id: The UUID of the processor to update.
        auto_terminated_relationships: A list of relationship names (strings) to be auto-terminated.
                                        Use an empty list `[]` to clear all auto-terminations.

    Returns:
        A dictionary representing the updated processor entity or an error status.
        Status may be "warning" if connections remain invalid after the update.
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

        # Get headers from request context for feature flags
        from config.logging_setup import request_context
        context_data = request_context.get()
        request_headers = context_data.get('headers', {}) if context_data else {}
        
        # Convert header keys to lowercase for case-insensitive comparison
        if request_headers:
            request_headers = {k.lower(): v for k, v in request_headers.items()}

        # --- AUTO-STOP LOGIC ---
        if current_state == "RUNNING":
            local_logger.info(f"Processor '{component_precheck.get('name', processor_id)}' is RUNNING. Checking Auto-Stop feature.")
            
            is_auto_stop_feature_enabled = mcp_settings.get_feature_auto_stop_enabled(headers=request_headers)
            local_logger.info(f"[Auto-Stop] Feature flag check - headers: {request_headers}")
            local_logger.info(f"[Auto-Stop] Feature enabled: {is_auto_stop_feature_enabled}")

            if is_auto_stop_feature_enabled:
                # Stop the processor first
                local_logger.info(f"[Auto-Stop] Stopping processor {processor_id}")
                try:
                    nifi_request_data = {"operation": "stop_processor", "processor_id": processor_id}
                    local_logger.bind(interface="nifi", direction="request", data=nifi_request_data).debug("Calling NiFi API")
                    await nifi_client.stop_processor(processor_id)
                    local_logger.bind(interface="nifi", direction="response", data={"status": "success"}).debug("Received from NiFi API")
                    
                    # Wait for processor to fully stop
                    max_wait_seconds = 15
                    for attempt in range(max_wait_seconds):
                        updated_details = await nifi_client.get_processor_details(processor_id)
                        current_state = updated_details.get("component", {}).get("state")
                        if current_state == "STOPPED":
                            local_logger.info(f"[Auto-Stop] Confirmed processor {processor_id} is stopped")
                            # Update our references with the latest details
                            current_entity = updated_details
                            component_precheck = current_entity.get("component", {})
                            current_revision = current_entity.get("revision")
                            break
                        
                        if attempt == max_wait_seconds - 1:
                            raise ToolError(f"Processor {processor_id} did not stop after {max_wait_seconds} seconds")
                        
                        local_logger.info(f"[Auto-Stop] Waiting for processor to stop (attempt {attempt + 1}/{max_wait_seconds})")
                        await asyncio.sleep(1)

                except Exception as e:
                    local_logger.error(f"[Auto-Stop] Failed to stop processor: {e}", exc_info=True)
                    local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
                    return {"status": "error", "message": f"Failed to auto-stop processor for update: {e}", "entity": None}
            else:
                error_msg = f"Processor '{component_precheck.get('name', processor_id)}' is RUNNING. Stop it before updating relationships or enable Auto-Stop."
                local_logger.warning(error_msg)
                return {"status": "error", "message": error_msg, "entity": None}

        # --- AUTO-DELETE CONNECTIONS LOGIC ---
        # Check if any relationships being auto-terminated have existing connections that would become invalid
        if auto_terminated_relationships:
            local_logger.info(f"Checking for connections using relationships that will be auto-terminated: {auto_terminated_relationships}")
            
            # Get the processor's parent process group to list connections
            parent_pg_id = component_precheck.get("parentGroupId")
            if not parent_pg_id:
                local_logger.error(f"Could not determine parent process group for processor {processor_id}")
                return {"status": "error", "message": f"Failed to determine parent process group for processor {processor_id}", "entity": None}
            
            try:
                # List all connections in the parent process group
                connections_list = await nifi_client.list_connections(parent_pg_id)
                
                # Find connections that originate from this processor and use relationships being auto-terminated
                connections_to_delete = []
                for connection_data in connections_list:
                    if not isinstance(connection_data, dict):
                        continue
                    
                    component = connection_data.get('component', {})
                    source = component.get('source', {})
                    selected_relationships = component.get('selectedRelationships', [])
                    
                    # Check if this connection originates from our processor
                    if source.get('id') == processor_id:
                        # Check if any of the selected relationships will be auto-terminated
                        conflicting_relationships = [rel for rel in selected_relationships if rel in auto_terminated_relationships]
                        if conflicting_relationships:
                            connection_id = connection_data.get('id')
                            if not connection_id:
                                connection_id = component.get('id')
                            
                            if connection_id:
                                connection_name = component.get('name', connection_id)
                                destination = component.get('destination', {})
                                dest_name = destination.get('name', destination.get('id', 'unknown'))
                                
                                connections_to_delete.append({
                                    'id': connection_id,
                                    'name': connection_name,
                                    'conflicting_relationships': conflicting_relationships,
                                    'destination_name': dest_name
                                })
                                
                                local_logger.info(f"Found connection '{connection_name}' ({connection_id}) to '{dest_name}' using relationships {conflicting_relationships} that will be auto-terminated")
                
                # Check if Auto-Delete feature is enabled for handling these connections
                if connections_to_delete:
                    is_auto_delete_feature_enabled = mcp_settings.get_feature_auto_delete_enabled(headers=request_headers)
                    local_logger.info(f"[Auto-Delete] Found {len(connections_to_delete)} connections that would become invalid")
                    local_logger.info(f"[Auto-Delete] Feature enabled: {is_auto_delete_feature_enabled}")
                    
                    if is_auto_delete_feature_enabled:
                        # Delete the conflicting connections
                        local_logger.info(f"[Auto-Delete] Automatically deleting {len(connections_to_delete)} connections that would become invalid")
                        
                        connection_ids = [conn['id'] for conn in connections_to_delete]
                        try:
                            batch_results = await nifi_client.delete_connections_batch(connection_ids)
                            
                            # Check for any failures
                            failures = []
                            successes = []
                            for conn_id, result in batch_results.items():
                                conn_info = next((c for c in connections_to_delete if c['id'] == conn_id), {'name': conn_id})
                                if result.get("success"):
                                    successes.append(conn_info['name'])
                                    local_logger.info(f"[Auto-Delete] Successfully deleted connection '{conn_info['name']}' ({conn_id})")
                                else:
                                    error_msg = result.get('message', 'Unknown error')
                                    failures.append(f"{conn_info['name']}: {error_msg}")
                                    local_logger.error(f"[Auto-Delete] Failed to delete connection '{conn_info['name']}' ({conn_id}): {error_msg}")
                            
                            if failures:
                                error_message = f"Auto-Delete encountered {len(failures)} errors: {', '.join(failures)}"
                                local_logger.warning(f"[Auto-Delete] {error_message}")
                                return {"status": "error", "message": error_message, "entity": None}
                            
                            local_logger.info(f"[Auto-Delete] Successfully deleted {len(successes)} connections: {', '.join(successes)}")
                            
                        except Exception as conn_delete_error:
                            error_msg = f"Error in batch connection deletion: {conn_delete_error}"
                            local_logger.error(f"[Auto-Delete] {error_msg}")
                            return {"status": "error", "message": error_msg, "entity": None}
                    else:
                        # Auto-Delete is disabled, warn about the potential validation issues
                        connection_names = [f"'{conn['name']}' (using {conn['conflicting_relationships']})" for conn in connections_to_delete]
                        warning_msg = f"Warning: {len(connections_to_delete)} connections will become invalid after auto-terminating relationships: {', '.join(connection_names)}. Enable Auto-Delete to automatically remove them."
                        local_logger.warning(f"[Auto-Delete] Feature disabled. {warning_msg}")
                        # Continue with the relationship update but include warning in response
                else:
                    local_logger.info("No existing connections found that would conflict with the auto-terminated relationships")
                    
            except Exception as e:
                local_logger.error(f"Error checking for conflicting connections: {str(e)}")
                return {"status": "error", "message": f"Error checking for conflicting connections: {str(e)}", "entity": None}
            
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
        
        # Check if we have any warnings about connections that would become invalid
        success_message = f"Processor '{name}' auto-terminated relationships updated successfully."
        
        # Add information about deleted connections if any
        if auto_terminated_relationships:
            try:
                # Re-check for any remaining invalid connections to warn about
                parent_pg_id = component_precheck.get("parentGroupId")
                if parent_pg_id:
                    connections_list = await nifi_client.list_connections(parent_pg_id)
                    remaining_invalid_connections = []
                    
                    for connection_data in connections_list:
                        if not isinstance(connection_data, dict):
                            continue
                        
                        component = connection_data.get('component', {})
                        source = component.get('source', {})
                        selected_relationships = component.get('selectedRelationships', [])
                        
                        if source.get('id') == processor_id:
                            conflicting_relationships = [rel for rel in selected_relationships if rel in auto_terminated_relationships]
                            if conflicting_relationships:
                                connection_name = component.get('name', component.get('id', 'unknown'))
                                remaining_invalid_connections.append(f"'{connection_name}' (using {conflicting_relationships})")
                    
                    if remaining_invalid_connections:
                        warning_msg = f" Warning: {len(remaining_invalid_connections)} connections may now be invalid: {', '.join(remaining_invalid_connections)}."
                        success_message += warning_msg
                        local_logger.warning(f"Post-update validation warning: {warning_msg}")
                        return {
                            "status": "warning",
                            "message": success_message,
                            "entity": filtered_updated_entity
                        }
            except Exception as e:
                local_logger.warning(f"Could not verify connection status after relationship update: {e}")
        
        return {
            "status": "success",
            "message": success_message,
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


class DeleteRequest(TypedDict):
    object_type: Literal["processor", "connection", "port", "process_group", "controller_service"]
    object_id: str
    name: Optional[str]  # Optional descriptive name

@smart_parameter_validation
@mcp.tool()
@tool_phases(["Modify"])
@handle_nifi_errors
async def delete_nifi_objects(
    objects: List[DeleteRequest]
) -> List[Dict]:
    """
    Deletes multiple NiFi objects (processors, connections, ports, process groups, or controller services) in batch.
    Attempts Auto-Stop for running processors if enabled.
    Attempts Auto-Delete for processors with connections if enabled.
    Attempts Auto-Purge for connections with queued data if enabled.
    Attempts Auto-Disable for enabled controller services.

    Args:
        objects: A list of deletion request dictionaries, each containing:
            - object_type: The type of the object to delete ('processor', 'connection', 'port', 'process_group', 'controller_service')
            - object_id: The UUID of the object to delete
            - name (optional): A descriptive name for the object (used in logging/results)

    Example:
    ```python
    [
        {
            "object_type": "processor",
            "object_id": "123e4567-e89b-12d3-a456-426614174000",
            "name": "MyProcessor"
        },
        {
            "object_type": "connection", 
            "object_id": "456e7890-e89b-12d3-a456-426614174001"
        },
        {
            "object_type": "controller_service",
            "object_id": "789e1234-e89b-12d3-a456-426614174002",
            "name": "MyControllerService"
        }
    ]
    ```

    Returns:
        A list of dictionaries, each indicating success or failure for the corresponding deletion request.
    """
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
    
    if not objects:
        raise ToolError("The 'objects' list cannot be empty.")
    if not isinstance(objects, list):
        raise ToolError("Invalid 'objects' type. Expected a list of dictionaries.")
    
    # Validate each deletion request
    for i, req in enumerate(objects):
        if not isinstance(req, dict):
            raise ToolError(f"Deletion request {i} is not a dictionary.")
        if "object_type" not in req or "object_id" not in req:
            raise ToolError(f"Deletion request {i} missing required fields 'object_type' and/or 'object_id'.")
        if req["object_type"] not in ["processor", "connection", "port", "process_group", "controller_service"]:
            raise ToolError(f"Deletion request {i} has invalid object_type '{req['object_type']}'. Must be one of: processor, connection, port, process_group, controller_service.")

    local_logger.info(f"Executing delete_nifi_objects for {len(objects)} objects")
    
    results = []
    
    for i, deletion_request in enumerate(objects):
        object_type = deletion_request["object_type"]
        object_id = deletion_request["object_id"]
        object_name = deletion_request.get("name", object_id)
        
        request_logger = local_logger.bind(object_id=object_id, object_type=object_type, request_index=i)
        request_logger.info(f"Processing deletion request {i+1}/{len(objects)} for {object_type} '{object_name}' ({object_id})")
        
        try:
            # Call the original deletion logic for a single object
            result = await _delete_single_nifi_object(
                object_type=object_type,
                object_id=object_id,
                object_name=object_name,
                nifi_client=nifi_client,
                logger=request_logger
            )
            
            # Add metadata to the result
            result["object_type"] = object_type
            result["object_id"] = object_id
            result["object_name"] = object_name
            result["request_index"] = i
            
            results.append(result)
            
        except Exception as e:
            error_result = {
                "status": "error",
                "message": f"Unexpected error deleting {object_type} '{object_name}' ({object_id}): {e}",
                "object_type": object_type,
                "object_id": object_id,
                "object_name": object_name,
                "request_index": i
            }
            results.append(error_result)
            request_logger.error(f"Unexpected error in deletion request {i}: {e}", exc_info=True)
    
    # Summary logging
    successful_deletions = [r for r in results if r.get("status") == "success"]
    failed_deletions = [r for r in results if r.get("status") == "error"]
    warning_deletions = [r for r in results if r.get("status") == "warning"]
    
    local_logger.info(f"Batch deletion completed: {len(successful_deletions)} successful, {len(failed_deletions)} failed, {len(warning_deletions)} warnings")
    
    return results


async def _delete_single_nifi_object(
    object_type: str,
    object_id: str,
    object_name: str,
    nifi_client: NiFiClient,
    logger
) -> Dict:
    """
    Internal function to delete a single NiFi object.
    Contains the core deletion logic extracted from the original delete_nifi_object function.
    """
    logger.info(f"Executing deletion for {object_type} '{object_name}' ({object_id})")

    try:
        # 1. Get current details using get_nifi_object_details
        logger.info(f"Fetching details for {object_type} {object_id}")
        current_entity = await get_nifi_object_details(object_type=object_type, object_id=object_id)
        if not current_entity:
            raise ToolError(f"Could not retrieve details for {object_type} {object_id}")

        # Refine port type if needed
        original_object_type = object_type
        if object_type == "port":
            port_type = current_entity.get("component", {}).get("type", "").lower()
            if "input" in port_type:
                object_type = "input_port"
            elif "output" in port_type:
                object_type = "output_port"
            else:
                raise ToolError(f"Unknown port type for port {object_id}: {port_type}")

        current_revision_dict = current_entity.get("revision")
        if not current_revision_dict or "version" not in current_revision_dict:
            raise ToolError(f"Could not retrieve current revision version for {object_type} {object_id}. Cannot delete.")
        current_version = current_revision_dict["version"]

        component = current_entity.get("component", {})
        state = component.get("state")
        name = component.get("name", object_name)

        # --- AUTO-DELETE PRE-EMPTIVE LOGIC ---
        if original_object_type == "processor":
            # Get headers from request context
            from config.logging_setup import request_context
            context_data = request_context.get()
            request_headers = context_data.get('headers', {}) if context_data else {}
            
            # Convert header keys to lowercase for case-insensitive comparison
            if request_headers:
                request_headers = {k.lower(): v for k, v in request_headers.items()}
            
            is_auto_delete_feature_enabled = mcp_settings.get_feature_auto_delete_enabled(headers=request_headers)
            logger.info(f"[Auto-Delete] Feature flag check - headers: {request_headers}")
            logger.info(f"[Auto-Delete] Feature enabled: {is_auto_delete_feature_enabled}")
            
            # Auto-Stop should have already run by this point and verified processor stopped
            # Now check for connections
            processor_details = await nifi_client.get_processor_details(object_id)
            processor_component = processor_details.get("component", {})
            processor_name = processor_component.get("name", "unknown")
            parent_pg_id = processor_component.get("parentGroupId")
            
            if not parent_pg_id:
                logger.error(f"[Auto-Delete] Could not determine parent process group for processor {processor_name} ({object_id})")
                return {"status": "error", "message": f"Failed to determine parent process group for processor {processor_name}"}
            
            # Check if the processor is part of any connections
            logger.info(f"[Auto-Delete] Checking for connections to processor {processor_name} ({object_id}) in process group {parent_pg_id}")
            try:
                connections_list = await nifi_client.list_connections(parent_pg_id)
                
                connections = []
                connection_ids = []
                for connection_data in connections_list:
                    # The connection details are in the first item of the list
                    if not isinstance(connection_data, dict):
                        continue
                    component = connection_data.get('component', {})
                    source = component.get('source', {})
                    destination = component.get('destination', {})
                    if (source.get('id') == object_id or destination.get('id') == object_id):
                        connections.append(connection_data)
                        connection_id = connection_data.get('id')
                        if not connection_id:
                            # Try to get from component if not at root level
                            connection_id = component.get('id')
                        if connection_id:
                            connection_ids.append(connection_id)
                            logger.info(f"[Auto-Delete] Found connection: {connection_id} with source={source.get('id')} and destination={destination.get('id')}")
                
                if connections:
                    logger.info(f"[Auto-Delete] Found {len(connections)} connections for processor {processor_name}")
                    
                    if not is_auto_delete_feature_enabled:
                        # Auto-Delete is disabled, we should fail the deletion
                        logger.warning(f"[Auto-Delete] Feature disabled. Cannot delete processor with connections.")
                        return {"status": "error", "message": f"Processor {processor_name} has {len(connections)} connections. Auto-Delete is disabled. Please delete connections first or enable Auto-Delete."}
                    
                    # Auto-Delete is enabled, delete the connections first
                    logger.info(f"[Auto-Delete] Feature enabled. Automatically deleting {len(connections)} connections.")
                    
                    # Use the batch delete method to delete all connections at once
                    if connection_ids:
                        logger.info(f"[Auto-Delete] Attempting batch deletion of {len(connection_ids)} connections: {connection_ids}")
                        try:
                            # Make sure we have a valid client
                            if not nifi_client._client:
                                await nifi_client._get_client()
                            
                            # First, check if any source processors are running and stop them
                            processors_to_stop = set()
                            for connection_data in connections:
                                component = connection_data.get('component', {})
                                source = component.get('source', {})
                                source_id = source.get('id')
                                source_type = source.get('type', '').upper()
                                
                                # Only stop processors, not other component types
                                if source_id and source_type == 'PROCESSOR' and source_id != object_id:
                                    # Check if the processor is running
                                    try:
                                        proc_details = await nifi_client.get_processor_details(source_id)
                                        proc_state = proc_details.get('component', {}).get('state', '')
                                        if proc_state == 'RUNNING':
                                            processors_to_stop.add(source_id)
                                            logger.info(f"[Auto-Delete] Found running source processor {source_id} that needs to be stopped")
                                    except Exception as e:
                                        logger.warning(f"[Auto-Delete] Could not check processor {source_id} state: {e}")
                            
                            # Stop any running processors
                            for proc_id in processors_to_stop:
                                try:
                                    logger.info(f"[Auto-Delete] Stopping processor {proc_id} to allow connection deletion")
                                    await nifi_client.stop_processor(proc_id)
                                    # Wait a moment for the processor to fully stop
                                    await asyncio.sleep(1)
                                except Exception as e:
                                    logger.warning(f"[Auto-Delete] Failed to stop processor {proc_id}: {e}")

                            # Verify processors are fully stopped before proceeding with connection deletion
                            if processors_to_stop:
                                logger.info(f"[Auto-Delete] Verifying {len(processors_to_stop)} processors are fully stopped")
                                max_wait_seconds = 5
                                for attempt in range(max_wait_seconds):
                                    all_stopped = True
                                    for proc_id in processors_to_stop:
                                        try:
                                            proc_details = await nifi_client.get_processor_details(proc_id)
                                            proc_state = proc_details.get('component', {}).get('state')
                                            if proc_state != 'STOPPED':
                                                logger.info(f"[Auto-Delete] Processor {proc_id} still in state {proc_state} on attempt {attempt+1}")
                                                all_stopped = False
                                                break
                                        except Exception as e:
                                            logger.warning(f"[Auto-Delete] Error checking processor state: {e}")
                                            all_stopped = False
                                            break
                                    
                                    if all_stopped:
                                        logger.info(f"[Auto-Delete] All source processors verified as stopped")
                                        break
                                    
                                    if attempt < max_wait_seconds - 1:  # Don't sleep on last iteration
                                        logger.info(f"[Auto-Delete] Waiting for processors to stop (attempt {attempt+1}/{max_wait_seconds})")
                                        await asyncio.sleep(1)
                                
                                if not all_stopped:
                                    logger.warning(f"[Auto-Delete] Some processors may not be fully stopped yet. Proceeding with caution.")
                            
                            # Now try to delete the connections
                            auto_delete_errors = []  # Initialize the errors collection
                            batch_results = await nifi_client.delete_connections_batch(connection_ids)
                            
                            # Debug output
                            for conn_id, result in batch_results.items():
                                if result.get("success"):
                                    logger.info(f"[Auto-Delete] Successfully deleted connection {conn_id}")
                                else:
                                    error_msg = result.get('message', 'Unknown error')
                                    # Check if this is an expected error (processor still running)
                                    if "running" in error_msg.lower() or "active" in error_msg.lower():
                                        logger.warning(f"[Auto-Delete] Could not delete connection {conn_id}: {error_msg}")
                                    else:
                                        logger.error(f"[Auto-Delete] Failed to delete connection {conn_id}: {error_msg}")
                                    auto_delete_errors.append(f"{conn_id}: {error_msg}")
                                    if result.get("error"):
                                        # Log the actual error at debug level to reduce console noise
                                        logger.debug(f"[Auto-Delete] Error details: {str(result.get('error'))}")
                        except Exception as conn_delete_error:
                            error_msg = f"Error in batch deletion: {conn_delete_error}"
                            logger.error(f"[Auto-Delete] {error_msg}")
                            auto_delete_errors.append(error_msg)
                        
                        # Check for any failures
                        failures = []
                        for conn_id, result in batch_results.items():
                            if not result.get("success"):
                                failures.append(f"{conn_id}: {result.get('message')}")
                                
                        if failures:
                            error_message = f"Auto-Delete encountered {len(failures)} errors: {', '.join(failures)}"
                            # Log as warning if we're proceeding with processor deletion anyway
                            logger.warning(f"[Auto-Delete] {error_message}")
                            return {"status": "error", "message": error_message}
                        
                        logger.info(f"[Auto-Delete] Successfully deleted {len(connection_ids)} connections in batch.")
                else:
                    logger.info(f"[Auto-Delete] No connections found for processor {processor_name}")
            except Exception as e:
                logger.error(f"[Auto-Delete] Error checking for connections: {str(e)}")
                return {"status": "error", "message": f"Error checking for connections: {str(e)}"}

        # --- AUTO-PURGE PRE-EMPTIVE LOGIC ---
        if object_type == "connection":
            # Get headers from request context
            from config.logging_setup import request_context
            context_data = request_context.get()
            request_headers = context_data.get('headers', {}) if context_data else {}
            
            # Convert header keys to lowercase for case-insensitive comparison
            if request_headers:
                request_headers = {k.lower(): v for k, v in request_headers.items()}
            
            is_auto_purge_feature_enabled = mcp_settings.get_feature_auto_purge_enabled(headers=request_headers)
            logger.info(f"[Auto-Purge] Feature flag check - headers: {request_headers}")
            logger.info(f"[Auto-Purge] Feature enabled: {is_auto_purge_feature_enabled}")

            # Check if connection has queued data
            connection_status = current_entity.get("status", {}).get("aggregateSnapshot", {})
            queued_count = int(connection_status.get("queuedCount", "0"))
            
            if queued_count > 0:
                if is_auto_purge_feature_enabled:
                    # Attempt to purge the queue
                    logger.info(f"[Auto-Purge] Connection has {queued_count} queued items. Attempting to purge.")
                    try:
                        # Create a drop request
                        drop_request = await nifi_client.create_drop_request(object_id)
                        drop_request_id = drop_request.get("id") # Get ID directly from the drop request object
                        if not drop_request_id:
                            raise ToolError("Failed to create drop request - no ID returned")
                        
                        # Wait for the drop request to complete
                        await nifi_client.handle_drop_request(object_id, timeout_seconds=30)
                        logger.info(f"[Auto-Purge] Successfully purged queue for connection {object_id}")
                    except Exception as e:
                        logger.error(f"[Auto-Purge] Failed to purge connection queue: {e}", exc_info=True)
                        raise ToolError(f"Failed to auto-purge connection queue: {e}")
                else:
                    error_msg = f"Cannot delete connection {object_id} with {queued_count} queued items when Auto-Purge is disabled"
                    logger.warning(error_msg)
                    return {"status": "error", "message": error_msg}

        # --- AUTO-DISABLE PRE-EMPTIVE LOGIC ---
        if original_object_type == "controller_service" and state == "ENABLED":
            logger.info(f"Controller service '{name}' is ENABLED. Auto-disabling for deletion.")
            try:
                nifi_request_data = {"operation": "disable_controller_service", "controller_service_id": object_id}
                logger.bind(interface="nifi", direction="request", data=nifi_request_data).debug("Calling NiFi API")
                await nifi_client.disable_controller_service(object_id)
                logger.bind(interface="nifi", direction="response", data={"status": "success"}).debug("Received from NiFi API")
                
                # Wait for controller service to fully disable
                max_wait_seconds = 15
                for attempt in range(max_wait_seconds):
                    updated_details = await nifi_client.get_controller_service_details(object_id)
                    current_state = updated_details.get("component", {}).get("state")
                    if current_state == "DISABLED":
                        logger.info(f"[Auto-Disable] Confirmed controller service {object_id} is disabled")
                        # Update our references with the latest details
                        current_entity = updated_details
                        component = current_entity.get("component", {})
                        current_revision_dict = current_entity.get("revision")
                        current_version = current_revision_dict.get("version") if current_revision_dict else None
                        state = current_state
                        name = component.get("name", object_name)
                        break
                    
                    if attempt == max_wait_seconds - 1:
                        raise ToolError(f"Controller service {object_id} did not disable after {max_wait_seconds} seconds")
                    
                    logger.info(f"[Auto-Disable] Waiting for controller service to disable (attempt {attempt + 1}/{max_wait_seconds})")
                    await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"[Auto-Disable] Failed to disable controller service: {e}", exc_info=True)
                logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
                raise ToolError(f"Failed to auto-disable controller service for deletion: {e}")

        # --- AUTO-STOP PRE-EMPTIVE LOGIC --- 
        if original_object_type == "processor" and state == "RUNNING":
            logger.info(f"Processor '{name}' is RUNNING. Checking Auto-Stop feature.")
            
            # Get headers from request context
            from config.logging_setup import request_context
            context_data = request_context.get()
            request_headers = context_data.get('headers', {}) if context_data else {}
            
            # Convert header keys to lowercase for case-insensitive comparison
            if request_headers:
                request_headers = {k.lower(): v for k, v in request_headers.items()}
            
            is_auto_stop_feature_enabled = mcp_settings.get_feature_auto_stop_enabled(headers=request_headers)
            logger.info(f"[Auto-Stop] Feature flag check - headers: {request_headers}")
            logger.info(f"[Auto-Stop] Feature enabled: {is_auto_stop_feature_enabled}")

            if is_auto_stop_feature_enabled:
                # Stop the component first
                logger.info(f"[Auto-Stop] Stopping {original_object_type} {object_id}")
                try:
                    async def verify_stopped(obj_type: str, obj_id: str, max_wait_seconds: int = 15) -> bool:
                        """Verify that a component (processor or process group) has stopped."""
                        for attempt in range(max_wait_seconds):
                            if obj_type == "processor":
                                details = await nifi_client.get_processor_details(obj_id)
                                current_state = details.get("component", {}).get("state")
                                if current_state == "STOPPED":
                                    logger.info(f"[Auto-Stop] Confirmed processor {obj_id} is stopped")
                                    return True, details  # Return both status and details
                            elif obj_type == "process_group":
                                # For PGs, we need to check all processors within
                                processors = await list_nifi_objects(
                                    object_type="processors",
                                    process_group_id=obj_id,
                                    search_scope="all"
                                )
                                all_stopped = True
                                for proc in processors:
                                    if isinstance(proc, dict):
                                        state = proc.get("component", {}).get("state")
                                        if state == "RUNNING":
                                            all_stopped = False
                                            break
                                if all_stopped:
                                    logger.info(f"[Auto-Stop] Confirmed all processors in PG {obj_id} are stopped")
                                    return True, None  # No specific details for PG
                            
                            if attempt == max_wait_seconds - 1:
                                logger.warning(f"[Auto-Stop] Maximum wait time reached without confirming stopped state for {obj_type} {obj_id}")
                                return False, None
                            
                            logger.info(f"[Auto-Stop] Waiting for {obj_type} to stop (attempt {attempt + 1}/{max_wait_seconds})")
                            await asyncio.sleep(1)
                        return False, None

                    if original_object_type == "processor":
                        nifi_request_data = {"operation": "stop_processor", "processor_id": object_id}
                        logger.bind(interface="nifi", direction="request", data=nifi_request_data).debug("Calling NiFi API")
                        await nifi_client.stop_processor(object_id)
                        logger.bind(interface="nifi", direction="response", data={"status": "success"}).debug("Received from NiFi API")
                        
                        is_stopped, updated_details = await verify_stopped("processor", object_id)
                        if not is_stopped:
                            raise ToolError(f"Processor {object_id} did not stop after 15 seconds")
                        # Update our state and component info with the latest details
                        if updated_details:
                            state = updated_details.get("component", {}).get("state")
                            component = updated_details.get("component", {})
                            name = component.get("name", object_name)
                            current_revision_dict = updated_details.get("revision")
                            current_version = current_revision_dict.get("version") if current_revision_dict else None
                            
                    elif original_object_type == "process_group":
                        nifi_request_data = {"operation": "stop_process_group", "process_group_id": object_id}
                        logger.bind(interface="nifi", direction="request", data=nifi_request_data).debug("Calling NiFi API")
                        await nifi_client.stop_process_group(object_id)
                        logger.bind(interface="nifi", direction="response", data={"status": "success"}).debug("Received from NiFi API")
                        
                        is_stopped, _ = await verify_stopped("process_group", object_id)
                        if not is_stopped:
                            raise ToolError(f"Process Group {object_id} did not fully stop after 15 seconds")

                except Exception as e:
                    logger.error(f"[Auto-Stop] Failed to stop {original_object_type}: {e}", exc_info=True)
                    logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
                    raise ToolError(f"Failed to auto-stop {original_object_type}: {e}")
            else:
                raise ToolError(f"Cannot delete running {original_object_type} {object_id} when Auto-Stop is disabled")

        # Final check on state before attempting deletion
        if object_type != "connection" and state == "RUNNING":
             error_msg = f"{object_type.capitalize()} '{name}' ({object_id}) is still RUNNING. It must be stopped before deletion. Auto-Stop may have failed or not fully stopped the component."
             logger.warning(error_msg)
             return {"status": "error", "message": error_msg}
        
        # Check if controller service is still enabled
        if object_type == "controller_service" and state == "ENABLED":
             error_msg = f"Controller service '{name}' ({object_id}) is still ENABLED. It must be disabled before deletion. Auto-Disable may have failed."
             logger.warning(error_msg)
             return {"status": "error", "message": error_msg} 

        # 2. Attempt deletion using the obtained version
        delete_op = f"delete_{object_type}" # Use potentially refined object_type for ports
        nifi_delete_req = {"operation": delete_op, "id": object_id, "version": current_version}
        logger.bind(interface="nifi", direction="request", data=nifi_delete_req).debug("Calling NiFi API (delete)")
        
        deleted = False
        try:
            if object_type == "processor":
                deleted = await nifi_client.delete_processor(object_id, current_version)
            elif object_type == "connection":
                deleted = await nifi_client.delete_connection(object_id, current_version)
            elif object_type in ["input_port", "output_port"]:
                deleted = await nifi_client.delete_port(object_id, current_version)
            elif object_type == "process_group":
                deleted = await nifi_client.delete_process_group(object_id, current_version)
            elif object_type == "controller_service":
                deleted = await nifi_client.delete_controller_service(object_id, current_version)
            else:
                raise ToolError(f"Unsupported object type for deletion: {object_type}")
        except ValueError as e:
            if "not empty" in str(e).lower():
                error_msg = f"Cannot delete {object_type} '{name}' ({object_id}) because it is not empty."
                logger.warning(error_msg)
                return {"status": "error", "message": error_msg}
            elif "has data" in str(e).lower() or "active queue" in str(e).lower():
                error_msg = f"Cannot delete {object_type} '{name}' ({object_id}) because it has queued data."
                logger.warning(error_msg)
                return {"status": "error", "message": error_msg}
            else:
                raise  # Re-raise other ValueError types

        if deleted:
            success_msg = f"Successfully deleted {object_type} '{name}' ({object_id})"
            logger.info(success_msg)
            return {"status": "success", "message": success_msg}
        else:
            error_msg = f"Failed to delete {object_type} '{name}' ({object_id}). NiFi API returned false."
            logger.warning(error_msg)
            return {"status": "error", "message": error_msg}

    except ValueError as e:
        logger.warning(f"Error deleting {object_type} {object_id} (ValueError caught in main try-except): {e}")
        logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API (delete)")
        
        error_message = str(e)
        error_message_lower = error_message.lower()

        if "not found" in error_message_lower:
             return {"status": "error", "message": f"{object_type.capitalize()} {object_id} not found."}
        elif "conflict" in error_message_lower or "revision mismatch" in error_message_lower or "is currently RUNNING" in error_message_lower:
             return {"status": "error", "message": f"Conflict or state issue deleting {object_type} '{name}' ({object_id}): {e}"}
        else:
            return {"status": "error", "message": f"Error deleting {object_type} '{name}' ({object_id}): {e}"}
            
    except (NiFiAuthenticationError, ConnectionError, ToolError) as e:
        logger.error(f"API/Tool error deleting {object_type} {object_id}: {e}", exc_info=False)
        logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API (delete)")
        return {"status": "error", "message": f"Failed to delete {object_type} {object_id}: {e}"}
    except Exception as e:
        logger.error(f"Unexpected error deleting {object_type} {object_id}: {e}", exc_info=True)
        logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API (delete)")
        return {"status": "error", "message": f"An unexpected error occurred during deletion: {e}"}


class ProcessorPropertyUpdate(TypedDict):
    processor_id: str
    properties: Dict[str, Any]  # Dictionary of properties to update
    name: Optional[str]  # Optional descriptive name for logging

@smart_parameter_validation
@mcp.tool()
@tool_phases(["Modify"])
async def update_nifi_processors_properties(
    updates: List[ProcessorPropertyUpdate]
) -> List[Dict]:
    """
    Updates one or more processors' properties efficiently.
    
    This tool handles both single processor updates and batch updates, automatically
    optimizing the operation for the number of processors provided.
    
    Args:
        updates: List of property update dictionaries, each containing:
            - processor_id: UUID of the processor to update
            - properties: Dictionary of properties to update
            - name (optional): Descriptive name for logging
            
    Example:
    ```python
    updates = [
        {
            "processor_id": "abc-123",
            "properties": {"Log Level": "info", "Yield Duration": "1 sec"},
            "name": "LogRawInput"
        },
        {
            "processor_id": "def-456", 
            "properties": {"Log Level": "info"},
            "name": "LogResponse"
        }
    ]
    ```
    
    Returns:
        List of update results, one per processor.
    """
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
    
    if not updates:
        raise ToolError("The 'updates' list cannot be empty.")
    if not isinstance(updates, list):
        raise ToolError("Invalid 'updates' type. Expected a list of dictionaries.")
    
    # Validate each update request
    for i, update in enumerate(updates):
        if not isinstance(update, dict):
            raise ToolError(f"Property update {i} is not a dictionary.")
        if "processor_id" not in update or "properties" not in update:
            raise ToolError(f"Property update {i} missing required fields 'processor_id' and/or 'properties'.")
        if not isinstance(update["properties"], dict) or not update["properties"]:
            raise ToolError(f"Property update {i} 'properties' must be a non-empty dictionary.")

    local_logger.info(f"Executing batch property updates for {len(updates)} processors")
    
    results = []
    
    for i, update in enumerate(updates):
        processor_id = update["processor_id"]
        properties = update["properties"]
        processor_name = update.get("name", processor_id)
        
        request_logger = local_logger.bind(processor_id=processor_id, request_index=i)
        request_logger.info(f"Processing property update {i+1}/{len(updates)} for processor '{processor_name}' ({processor_id})")
        
        try:
            # Call the legacy single processor update function
            result = await _update_nifi_processor_properties_legacy(
                processor_id=processor_id,
                processor_config_properties=properties
            )
            
            # Add metadata to the result
            result["processor_id"] = processor_id
            result["processor_name"] = processor_name
            result["request_index"] = i
            
            results.append(result)
            
        except Exception as e:
            error_result = {
                "status": "error",
                "message": f"Unexpected error updating processor '{processor_name}' ({processor_id}): {e}",
                "processor_id": processor_id,
                "processor_name": processor_name,
                "request_index": i
            }
            results.append(error_result)
            request_logger.error(f"Unexpected error in property update {i}: {e}", exc_info=True)
    
    # Summary logging
    successful_updates = [r for r in results if r.get("status") == "success"]
    failed_updates = [r for r in results if r.get("status") == "error"]
    warning_updates = [r for r in results if r.get("status") in ["warning", "partial_success"]]
    
    local_logger.info(f"Batch property updates completed: {len(successful_updates)} successful, {len(failed_updates)} failed, {len(warning_updates)} warnings")
    
    return results



def _looks_like_service_reference(value: str) -> bool:
    """Check if a property value looks like a service reference."""
    if not isinstance(value, str):
        return False
    # @ServiceName pattern or UUID pattern
    return value.startswith("@") or _is_valid_uuid(value)

def _is_valid_uuid(uuid_string: str) -> bool:
    """Check if a string is a valid UUID."""
    try:
        import uuid
        uuid.UUID(uuid_string)
        return True
    except ValueError:
        return False

async def _resolve_service_reference(
    reference: str, 
    service_map: Dict[str, str], 
    process_group_id: str, 
    nifi_client: NiFiClient,
    logger,
    user_request_id: str,
    action_id: str
) -> tuple[str, bool]:
    """
    Enhanced service reference resolution that handles multiple patterns.
    
    Supports:
    - Direct UUID: "855ec3ad-0197-1000-0937-3067b9c6f54c" → returns as-is
    - @ServiceName: "@HttpContextMap" → resolves to service ID  
    - ServiceName: "HttpContextMap" → resolves to service ID (fallback)
    - Fuzzy matching: "HttpContext" → finds best match
    """
    if not isinstance(reference, str) or not reference.strip():
        return reference, False
    
    reference = reference.strip()
    
    # Pattern 1: Direct UUID (already resolved)
    if _is_valid_uuid(reference):
        logger.debug(f"Direct UUID reference: {reference}")
        return reference, True
    
    # Pattern 2: @ServiceName syntax
    if reference.startswith("@"):
        service_name = reference[1:]
        if service_name in service_map:
            resolved_id = service_map[service_name]
            logger.debug(f"Resolved @{service_name} → {resolved_id}")
            return resolved_id, True
        else:
            logger.warning(f"Service '@{service_name}' not found in current flow services")
            reference = service_name
    
    # Pattern 3: Direct service name (fallback for backward compatibility)
    if reference in service_map:
        resolved_id = service_map[reference]
        logger.debug(f"Resolved service name '{reference}' → {resolved_id}")
        return resolved_id, True
    
    # Pattern 4: Process group service lookup (for existing services)
    try:
        logger.debug(f"Looking up service '{reference}' in process group {process_group_id}")
        services = await nifi_client.list_controller_services(
            process_group_id=process_group_id,
            user_request_id=user_request_id,
            action_id=action_id
        )
        
        # Exact name match
        exact_matches = [s for s in services if s.get("component", {}).get("name") == reference]
        if len(exact_matches) == 1:
            resolved_id = exact_matches[0]["id"]
            logger.info(f"Found exact match for service '{reference}' → {resolved_id}")
            return resolved_id, True
        elif len(exact_matches) > 1:
            logger.warning(f"Multiple services found with name '{reference}' in process group")
            return reference, False
        
        # Fuzzy matching (case-insensitive partial match)
        fuzzy_matches = [
            s for s in services 
            if reference.lower() in s.get("component", {}).get("name", "").lower()
        ]
        if len(fuzzy_matches) == 1:
            resolved_id = fuzzy_matches[0]["id"]
            service_name = fuzzy_matches[0].get("component", {}).get("name", "")
            logger.info(f"Fuzzy match: '{reference}' → '{service_name}' ({resolved_id})")
            return resolved_id, True
        elif len(fuzzy_matches) > 1:
            match_names = [s.get("component", {}).get("name", "") for s in fuzzy_matches]
            logger.warning(f"Multiple fuzzy matches for '{reference}': {match_names}")
            return reference, False

    except Exception as e:
        logger.warning(f"Error looking up service '{reference}' in process group: {e}")
    
    # Pattern 5: No resolution possible
    logger.warning(f"Could not resolve service reference '{reference}'")
    return reference, False


