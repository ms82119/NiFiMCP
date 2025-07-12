import asyncio
from typing import List, Dict, Optional, Any, Union, Literal, Tuple
from typing_extensions import TypedDict

# Import necessary components from parent/utils
from loguru import logger
# Import mcp ONLY
from ..core import mcp
# Removed nifi_api_client import
# Import context variables
from ..request_context import current_nifi_client, current_request_logger, current_user_request_id, current_action_id # Added

from .utils import (
    tool_phases,
    # ensure_authenticated, # Removed
    filter_created_processor_data,
    filter_connection_data,
    filter_port_data,
    filter_process_group_data,
    filter_processor_data, # Needed for create_nifi_flow
    filter_controller_service_data,  # Add controller service import
    smart_parameter_validation
)
from nifi_mcp_server.nifi_client import NiFiClient, NiFiAuthenticationError
from mcp.server.fastmcp.exceptions import ToolError

# Import modules that were previously imported dynamically
from .review import list_nifi_objects, get_process_group_status
from .operation import operate_nifi_objects
from .modification import update_nifi_processor_relationships

# --- Tool Definitions --- 

# --- Private helpers for single-object creation ---
async def _create_nifi_processor_single(
    processor_type: str,
    name: str,
    position_x: int,
    position_y: int,
    process_group_id: str | None = None,
    properties: Optional[Dict[str, Any]] = None
) -> Dict:
    """
    Creates a new processor within a specified NiFi process group.

    Example Call:
    ```tool_code
    print(default_api.create_nifi_processors(processors=[{
        'processor_type': 'org.apache.nifi.processors.standard.LogAttribute', 
        'name': 'Log Test Attribute', 
        'position_x': 200, 
        'position_y': 300, 
        'properties': {'Log Level': 'info', 'Attributes to Log': 'uuid, filename'}
    }]))
    ```

    Args:
        processor_type: The fully qualified Java class name of the processor type (e.g., 'org.apache.nifi.processors.standard.ReplaceText').
        name: The desired name for the new processor instance.
        position_x: The desired X coordinate for the processor's position on the canvas.
        position_y: The desired Y coordinate for the processor's position on the canvas.
        process_group_id: The UUID of the target process group. If None, the root process group of the NiFi instance will be used.
        properties: A dictionary containing the processor's configuration properties. Provide the properties directly as key-value pairs. Do NOT include the 'properties' key itself within this dictionary, as it will be added automatically by the underlying client. Example: {'Replacement Strategy': 'Always Replace', 'Replacement Value': 'Hello'}.

    Returns:
        A dictionary reporting the success, warning, or error status of the operation, including the created processor entity details.
        The entity includes properties and relationships as configured at creation time (before any connections are added).
    """
    # Get client and logger from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
         
    # Authentication handled by factory
    # context = local_logger._context # REMOVED
    # user_request_id = context.get("user_request_id", "-") # REMOVED
    # action_id = context.get("action_id", "-") # REMOVED
    # --- Get IDs from context ---
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"
    # --------------------------

    target_pg_id = process_group_id
    if target_pg_id is None:
        local_logger.info("No process_group_id provided for creation, fetching root process group ID.")
        try:
            nifi_request_data = {"operation": "get_root_process_group_id"}
            local_logger.bind(interface="nifi", direction="request", data=nifi_request_data).debug("Calling NiFi API")
            target_pg_id = await nifi_client.get_root_process_group_id(user_request_id=user_request_id, action_id=action_id)
            nifi_response_data = {"root_pg_id": target_pg_id}
            local_logger.bind(interface="nifi", direction="response", data=nifi_response_data).debug("Received from NiFi API")
        except Exception as e:
            local_logger.error(f"Failed to get root process group ID: {e}", exc_info=True)
            local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
            raise ToolError(f"Failed to determine root process group ID for creation: {e}")

    position = {"x": position_x, "y": position_y}
    local_logger = local_logger.bind(process_group_id=target_pg_id) # Bind PG ID to logger now
    local_logger.info(f"Executing create_nifi_processor: Type='{processor_type}', Name='{name}', Position={position}")

    # Enhanced Property Validation and Service Reference Resolution
    validated_properties = properties or {}
    warnings = []
    
    if properties:
        local_logger.info("Applying enhanced property validation and service reference resolution...")
        
        # CRITICAL FIX: Add Phase 2B property validation to single processor creation
        # Phase 2B: Enhanced property validation - remove invalid properties first
        phase2b_properties, phase2b_warnings = await _validate_processor_properties_phase2b(
            processor_type=processor_type,
            properties=properties,
            logger=local_logger
        )
        warnings.extend(phase2b_warnings)
        
        # 1. Build service map for reference resolution
        service_map = {}
        try:
            services = await nifi_client.list_controller_services(target_pg_id, user_request_id=user_request_id, action_id=action_id)
            for service in services:
                service_name = service.get("component", {}).get("name", "")
                service_id = service.get("id", "")
                if service_name and service_id:
                    service_map[service_name] = service_id
            local_logger.info(f"Built service map with {len(service_map)} services: {list(service_map.keys())}")
        except Exception as e:
            local_logger.warning(f"Failed to build service map, service references may not resolve: {e}")
        
        # 2. Apply enhanced property validation and service resolution (using phase2b validated properties)
        try:
            validated_properties, prop_warnings, prop_errors = await _validate_and_fix_processor_properties(
                processor_type=processor_type,
                properties=phase2b_properties,  # Use pre-validated properties from Phase 2B
                service_map=service_map,
                process_group_id=target_pg_id,
                nifi_client=nifi_client,
                logger=local_logger,
                user_request_id=user_request_id,
                action_id=action_id
            )
            warnings.extend(prop_warnings)
            
            if prop_errors:
                error_msg = f"Property validation failed for processor '{name}': {'; '.join(prop_errors)}"
                local_logger.error(error_msg)
                return {"status": "error", "message": error_msg, "entity": None}
                
        except Exception as e:
            local_logger.warning(f"Property validation failed, using original properties: {e}")
            validated_properties = phase2b_properties  # Use phase2b properties as fallback

    try:
        nifi_request_data = {
            "operation": "create_processor", 
            "process_group_id": target_pg_id,
            "processor_type": processor_type,
            "name": name,
            "position": position,
            "config": validated_properties
        }
        local_logger.bind(interface="nifi", direction="request", data=nifi_request_data).debug("Calling NiFi API")
        processor_entity = await nifi_client.create_processor(
            process_group_id=target_pg_id,
            processor_type=processor_type,
            name=name,
            position=position,
            config=validated_properties
        )
        nifi_response_data = filter_created_processor_data(processor_entity)
        local_logger.bind(interface="nifi", direction="response", data=nifi_response_data).debug("Received from NiFi API")
        
        local_logger.info(f"Successfully created processor '{name}' with ID: {processor_entity.get('id', 'N/A')}")
        
        component = processor_entity.get("component", {})
        validation_status = component.get("validationStatus", "UNKNOWN")
        validation_errors = component.get("validationErrors", [])
        
        # Include property validation warnings in response
        result_message = f"Processor '{name}' created successfully."
        if warnings:
            result_message += f" Warnings: {'; '.join(warnings)}"
        
        if validation_status == "VALID":
            return {
                "status": "success",
                "message": result_message,
                "entity": nifi_response_data,
                "warnings": warnings
            }
        else:
            error_msg_snippet = f" ({validation_errors[0]})" if validation_errors else ""
            local_logger.warning(f"Processor '{name}' created but is {validation_status}{error_msg_snippet}. Requires configuration or connections.")
            warning_message = f"Processor '{name}' created but is currently {validation_status}{error_msg_snippet}. Further configuration or connections likely required."
            if warnings:
                warning_message += f" Property warnings: {'; '.join(warnings)}"
            return {
                "status": "warning",
                "message": warning_message,
                "entity": nifi_response_data,
                "warnings": warnings
            }
            
    except (NiFiAuthenticationError, ConnectionError, ValueError) as e:
        local_logger.error(f"API error creating processor: {e}", exc_info=False)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        # Return structured error instead of raising ToolError
        return {"status": "error", "message": f"Failed to create NiFi processor: {e}", "entity": None}
    except Exception as e:
        local_logger.error(f"Unexpected error creating processor: {e}", exc_info=True)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"An unexpected error occurred during processor creation: {e}", "entity": None}

async def _create_nifi_connection_single(
    source_id: str,
    relationships: List[str],
    target_id: str,
) -> Dict:
    """
    Creates a connection between two components (processors or ports) within the same NiFi process group.

    Args:
        source_id: The UUID of the source component.
        relationships: A list of relationship names. Must be non-empty for processors, but should be empty for ports.
        target_id: The UUID of the target component.

    Returns:
        A dictionary representing the created connection entity or an error message.
    """
    # Get client and logger from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
         
    # Authentication handled by factory
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"

    local_logger = local_logger.bind(source_id=source_id, target_id=target_id, relationships=relationships)
         
    if not isinstance(relationships, list) or not all(isinstance(item, str) for item in relationships):
        raise ToolError("Invalid 'relationships' elements.")

    source_entity = None
    source_type = None
    target_entity = None
    target_type = None

    local_logger.info(f"Fetching details for source component {source_id}...")
    try:
        try:
            source_entity = await nifi_client.get_processor_details(source_id)
            source_type = "PROCESSOR"
            local_logger.info(f"Source component {source_id} identified as a PROCESSOR.")
            # Validate relationships for processors
            if not relationships:
                raise ToolError("The 'relationships' list cannot be empty for processor connections.")
        except ValueError:
            try:
                source_entity = await nifi_client.get_input_port_details(source_id)
                source_type = "INPUT_PORT"
                local_logger.info(f"Source component {source_id} identified as an INPUT_PORT.")
                # Input ports should have empty relationships
                if relationships:
                    local_logger.warning(f"Relationships specified for input port connection will be ignored: {relationships}")
                relationships = []
            except ValueError:
                try:
                    source_entity = await nifi_client.get_output_port_details(source_id)
                    source_type = "OUTPUT_PORT"
                    local_logger.info(f"Source component {source_id} identified as an OUTPUT_PORT.")
                    # Output ports should have empty relationships
                    if relationships:
                        local_logger.warning(f"Relationships specified for output port connection will be ignored: {relationships}")
                    relationships = []
                except ValueError:
                    raise ToolError(f"Source component with ID {source_id} not found or is not connectable.")

        local_logger.info(f"Fetching details for target component {target_id}...")
        try:
            target_entity = await nifi_client.get_processor_details(target_id)
            target_type = "PROCESSOR"
            local_logger.info(f"Target component {target_id} identified as a PROCESSOR.")
        except ValueError:
            try:
                target_entity = await nifi_client.get_input_port_details(target_id)
                target_type = "INPUT_PORT"
                local_logger.info(f"Target component {target_id} identified as an INPUT_PORT.")
            except ValueError:
                try:
                    target_entity = await nifi_client.get_output_port_details(target_id)
                    target_type = "OUTPUT_PORT"
                    local_logger.info(f"Target component {target_id} identified as an OUTPUT_PORT.")
                except ValueError:
                     raise ToolError(f"Target component with ID {target_id} not found or is not connectable.")

        source_parent_pg_id = source_entity.get("component", {}).get("parentGroupId")
        target_parent_pg_id = target_entity.get("component", {}).get("parentGroupId")

        if not source_parent_pg_id or not target_parent_pg_id:
            missing_component = "source" if not source_parent_pg_id else "target"
            local_logger.error(f"Could not determine parent group ID for {missing_component} component.")
            raise ToolError(f"Could not determine parent group ID for {missing_component}.")

        if source_parent_pg_id != target_parent_pg_id:
            local_logger.error(f"Source ({source_parent_pg_id}) and target ({target_parent_pg_id}) are in different groups.")
            raise ToolError(f"Source and target components must be in the same process group.")

        common_parent_pg_id = source_parent_pg_id
        local_logger = local_logger.bind(process_group_id=common_parent_pg_id)
        local_logger.info(f"Validated components are in the same group: {common_parent_pg_id}")

    except (ValueError, NiFiAuthenticationError, ConnectionError) as e:
         local_logger.error(f"API error fetching component details: {e}", exc_info=False)
         raise ToolError(f"Failed to fetch details for components: {e}")
    except Exception as e:
         local_logger.error(f"Unexpected error fetching component details: {e}", exc_info=True)
         raise ToolError(f"An unexpected error occurred fetching details: {e}")

    local_logger.info(f"Checking for existing connections between {source_id} and {target_id}...")
    try:
        nifi_list_req = {"operation": "list_connections", "process_group_id": common_parent_pg_id}
        local_logger.bind(interface="nifi", direction="request", data=nifi_list_req).debug("Calling NiFi API")
        existing_connections = await nifi_client.list_connections(common_parent_pg_id, user_request_id=user_request_id, action_id=action_id)
        nifi_list_resp = {"connection_count": len(existing_connections)}
        local_logger.bind(interface="nifi", direction="response", data=nifi_list_resp).debug("Received from NiFi API")
        
        for existing_conn_entity in existing_connections:
            existing_comp = existing_conn_entity.get("component", {})
            existing_source = existing_comp.get("source", {})
            existing_dest = existing_comp.get("destination", {})
            
            if existing_source.get("id") == source_id and existing_dest.get("id") == target_id:
                existing_conn_id = existing_conn_entity.get("id")
                local_logger.warning(f"Duplicate connection detected: {existing_conn_id}")
                try:
                    existing_conn_details = await nifi_client.get_connection(existing_conn_id)
                    existing_relationships = existing_conn_details.get("component", {}).get("selectedRelationships", [])
                except Exception as detail_err:
                    local_logger.error(f"Could not fetch details for existing connection {existing_conn_id}: {detail_err}")
                    existing_relationships = ["<error retrieving>"]
                error_msg = (
                    f"A connection already exists between source '{source_id}' and target '{target_id}'. "
                    f"ID: {existing_conn_id}. Relationships: {existing_relationships}. "
                    f"Use 'update_nifi_connection' to modify."
                )
                return {"status": "error", "message": error_msg, "entity": None}
        local_logger.info("No duplicate connection found. Proceeding with creation.")
    except (NiFiAuthenticationError, ConnectionError, ValueError) as e:
        local_logger.error(f"API error checking existing connections: {e}", exc_info=False)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        raise ToolError(f"Failed to check for existing connections: {e}")
    except Exception as e:
        local_logger.error(f"Unexpected error checking existing connections: {e}", exc_info=True)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        raise ToolError(f"An unexpected error occurred checking connections: {e}")

    local_logger.info(f"Attempting to create connection from {source_type} '{source_id}' ({relationships}) to {target_type} '{target_id}' in group {common_parent_pg_id}")
    try:
        nifi_create_req = {
            "operation": "create_connection",
            "process_group_id": common_parent_pg_id,
            "source_id": source_id,
            "target_id": target_id,
            "relationships": relationships,
            "source_type": source_type,
            "target_type": target_type
        }
        local_logger.bind(interface="nifi", direction="request", data=nifi_create_req).debug("Calling NiFi API")
        
        connection_entity = await nifi_client.create_connection(
            process_group_id=common_parent_pg_id,
            source_id=source_id,
            target_id=target_id,
            relationships=relationships,
            source_type=source_type,
            target_type=target_type
        )
        filtered_entity = filter_connection_data(connection_entity)
        local_logger.bind(interface="nifi", direction="response", data=filtered_entity).debug("Received from NiFi API")
        
        local_logger.info(f"Successfully created connection with ID: {connection_entity.get('id', 'N/A')}")
        return {
            "status": "success",
            "message": "Connection created successfully.",
            "entity": filtered_entity
        }

    except (NiFiAuthenticationError, ConnectionError, ValueError) as e:
        local_logger.error(f"API error creating connection: {e}", exc_info=False)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        # Return structured error instead of raising ToolError
        return {"status": "error", "message": f"Failed to create NiFi connection: {e}", "entity": None}
    except Exception as e:
        local_logger.error(f"Unexpected error creating connection: {e}", exc_info=True)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"An unexpected error occurred during connection creation: {e}", "entity": None}

async def _create_nifi_port_single(
    port_type: Literal["input", "output"],
    name: str,
    position_x: int,
    position_y: int,
    process_group_id: str | None = None
) -> Dict:
    """
    Creates a new input or output port within a specified NiFi process group.

    Args:
        port_type: Whether to create an "input" or "output" port.
        name: The desired name for the new port instance.
        position_x: The desired X coordinate for the port on the canvas.
        position_y: The desired Y coordinate for the port on the canvas.
        process_group_id: The UUID of the process group where the port should be created. Defaults to the root group if None.

    Returns:
        A dictionary representing the result, including status and the created port entity.
    """
    # Get client and logger from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
         
    # Normalize port type to lowercase
    port_type = port_type.lower()
    if port_type not in ["input", "output"]:
        raise ToolError(f"Invalid port_type: {port_type}. Must be 'input' or 'output'.")

    # Authentication handled by factory
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"

    target_pg_id = process_group_id
    if target_pg_id is None:
        local_logger.info("No process_group_id provided, fetching root process group ID.")
        try:
            nifi_request_data = {"operation": "get_root_process_group_id"}
            local_logger.bind(interface="nifi", direction="request", data=nifi_request_data).debug("Calling NiFi API")
            target_pg_id = await nifi_client.get_root_process_group_id(user_request_id=user_request_id, action_id=action_id)
            nifi_response_data = {"root_pg_id": target_pg_id}
            local_logger.bind(interface="nifi", direction="response", data=nifi_response_data).debug("Received from NiFi API")
        except Exception as e:
            local_logger.error(f"Failed to get root process group ID: {e}", exc_info=True)
            local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
            raise ToolError(f"Failed to determine root process group ID for port creation: {e}")

    position = {"x": position_x, "y": position_y}
    local_logger = local_logger.bind(process_group_id=target_pg_id, port_type=port_type)
    local_logger.info(f"Executing create_nifi_port: Type='{port_type}', Name='{name}', Position={position}")

    try:
        nifi_request_data = {
            "operation": f"create_{port_type}_port",
            "process_group_id": target_pg_id,
            "name": name,
            "position": position
        }
        local_logger.bind(interface="nifi", direction="request", data=nifi_request_data).debug("Calling NiFi API")

        if port_type == "input":
            port_entity = await nifi_client.create_input_port(target_pg_id, name, position)
        else: # port_type == "output"
            port_entity = await nifi_client.create_output_port(target_pg_id, name, position)
            
        filtered_entity = filter_port_data(port_entity)
        local_logger.bind(interface="nifi", direction="response", data=filtered_entity).debug("Received from NiFi API")
        
        port_id = port_entity.get('id', 'N/A')
        local_logger.info(f"Successfully created {port_type} port '{name}' with ID: {port_id}")
        return {
            "status": "success",
            "message": f"{port_type.capitalize()} port '{name}' created successfully.",
            "entity": filtered_entity
        }

    except (NiFiAuthenticationError, ConnectionError, ValueError) as e:
        local_logger.error(f"API error creating {port_type} port: {e}", exc_info=False)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"Failed to create NiFi {port_type} port: {e}", "entity": None}
    except Exception as e:
        local_logger.error(f"Unexpected error creating {port_type} port: {e}", exc_info=True)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"An unexpected error occurred during {port_type} port creation: {e}", "entity": None}

@mcp.tool()
@tool_phases(["Modify"])
async def create_nifi_controller_service(
    service_type: str,
    name: str,
    process_group_id: str,
    properties: Optional[Dict[str, Any]] = None
) -> Dict:
    """
    Creates a new controller service within a specified NiFi process group.

    Args:
        service_type: The fully qualified Java class name of the controller service type (e.g., 'org.apache.nifi.dbcp.DBCPConnectionPool').
        name: The desired name for the new controller service instance.
        process_group_id: The UUID of the target process group where the controller service should be created.
        properties: A dictionary containing the controller service's configuration properties.
    
    Returns:
        A dictionary reporting the success, warning, or error status of the operation, including the created controller service entity details.
    """
    # Get client and logger from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
         
    # Get IDs from context
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"

    local_logger = local_logger.bind(process_group_id=process_group_id)
    local_logger.info(f"Executing create_controller_service: Type='{service_type}', Name='{name}'")

    try:
        nifi_request_data = {
            "operation": "create_controller_service", 
            "process_group_id": process_group_id,
            "service_type": service_type,
            "name": name,
            "properties": properties or {}
        }
        local_logger.bind(interface="nifi", direction="request", data=nifi_request_data).debug("Calling NiFi API")
        
        controller_service_entity = await nifi_client.create_controller_service(
            process_group_id=process_group_id,
            service_type=service_type,
            name=name,
            properties=properties or {},
            user_request_id=user_request_id,
            action_id=action_id
        )
        
        nifi_response_data = filter_controller_service_data(controller_service_entity)
        local_logger.bind(interface="nifi", direction="response", data=nifi_response_data).debug("Received from NiFi API")
        
        local_logger.info(f"Successfully created controller service '{name}' with ID: {controller_service_entity.get('id', 'N/A')}")
        
        component = controller_service_entity.get("component", {})
        validation_status = component.get("validationStatus", "UNKNOWN")
        validation_errors = component.get("validationErrors", [])
        
        if validation_status == "VALID":
            return {
                "status": "success",
                "message": f"Controller service '{name}' created successfully.",
                "entity": nifi_response_data
            }
        else:
            error_msg_snippet = f" ({validation_errors[0]})" if validation_errors else ""
            local_logger.warning(f"Controller service '{name}' created but is {validation_status}{error_msg_snippet}. Requires configuration.")
            return {
                "status": "warning",
                "message": f"Controller service '{name}' created but is currently {validation_status}{error_msg_snippet}. Further configuration likely required.",
                "entity": nifi_response_data
            }
            
    except (NiFiAuthenticationError, ConnectionError, ValueError) as e:
        local_logger.error(f"API error creating controller service: {e}", exc_info=False)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"Failed to create controller service: {e}", "entity": None}
    except Exception as e:
        local_logger.error(f"Unexpected error creating controller service: {e}", exc_info=True)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"An unexpected error occurred during controller service creation: {e}", "entity": None}


async def _create_controller_service_single(
    service_type: str,
    name: str,
    process_group_id: str,
    properties: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Helper function to create a single controller service.
    
    Returns result with status, message, and entity if successful.
    """
    try:
        result = await create_nifi_controller_service(
            service_type=service_type,
            name=name,
            process_group_id=process_group_id,
            properties=properties
        )
        return result
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to create controller service '{name}': {e}",
            "service_type": service_type,
            "name": name
        }




# --- Plural batch tools ---

class ProcessorDefinition(TypedDict):
    processor_type: str  # Fully qualified Java class name
    name: str  # Desired name for the processor instance
    position_x: Optional[int]  # X coordinate (alternative to position)
    position_y: Optional[int]  # Y coordinate (alternative to position)
    position: Optional[Dict[str, int]]  # Nested position object {"x": 100, "y": 200}
    process_group_id: Optional[str]  # UUID of target process group
    properties: Optional[Dict[str, Any]]  # Configuration properties

class PortDefinition(TypedDict):
    port_type: Literal["input", "output"]
    name: str  # Desired name for the port instance
    position_x: Optional[int]  # X coordinate (alternative to position)
    position_y: Optional[int]  # Y coordinate (alternative to position)
    position: Optional[Dict[str, int]]  # Nested position object {"x": 100, "y": 200}
    process_group_id: Optional[str]  # UUID of target process group

class ControllerServiceDefinition(TypedDict):
    service_type: str  # Fully qualified Java class name
    name: str  # Desired name for the controller service
    properties: Optional[Dict[str, Any]]  # Configuration properties

class ConnectionDefinition(TypedDict):
    source_name: str  # Name OR UUID of source component
    target_name: str  # Name OR UUID of target component
    relationships: List[str]  # List of relationship names
    process_group_id: Optional[str]  # Process group to search in

class NifiObjectDefinition(TypedDict):
    type: Literal["controller_service", "processor", "connection"]
    # For controller_service
    service_type: Optional[str]  # Required only if type="controller_service"
    name: str  # Required for all
    properties: Optional[Dict[str, Any]]  # Optional for all
    # For processor  
    processor_type: Optional[str]  # Required only if type="processor"
    position: Optional[Dict[str, int]]  # Optional for processor
    position_x: Optional[int]  # Alternative to position
    position_y: Optional[int]  # Alternative to position
    process_group_id: Optional[str]  # Optional for all
    # For connection
    source_name: Optional[str]  # Required only if type="connection"
    target_name: Optional[str]  # Required only if type="connection"  
    relationships: Optional[List[str]]  # Required only if type="connection"

# Legacy TypedDict for backward compatibility (commented out)
# class NifiObjectDefinition(TypedDict):
#     type: Literal["controller_service", "processor", "connection"]
#     # For controller_service
#     service_type: Optional[str]  # Fully qualified Java class name
#     name: str  # Desired name
#     properties: Optional[Dict[str, Any]]  # Configuration properties
#     # For processor
#     processor_type: Optional[str]  # Fully qualified Java class name
#     position: Optional[Dict[str, int]]  # Position object {"x": 100, "y": 200}
#     # For connection
#     source: Optional[str]  # Source component name
#     target: Optional[str]  # Target component name
#     relationships: Optional[List[str]]  # List of relationship names

@mcp.tool()
@tool_phases(["Modify"])
async def create_nifi_processors(
    processors: List[ProcessorDefinition]
) -> List[Dict]:
    """
    Creates one or more NiFi processors in batch.

    Args:
        processors: List of processor definitions. Each dictionary must contain:
            - processor_type (str): Fully qualified Java class name of the processor
            - name (str): The desired name for the processor instance
            - position_x (int) OR position (dict): X coordinate OR nested position object {"x": 100, "y": 200}
            - position_y (int): Y coordinate (only if using position_x format)
            - process_group_id (str, optional): UUID of the target process group
            - properties (dict, optional): Configuration properties for the processor

    Returns:
        List of result dictionaries, one per processor creation attempt.
    """
    results = []
    for proc in processors:
        # CRITICAL FIX: Handle both flat and nested position formats
        position_x = proc.get("position_x")
        position_y = proc.get("position_y")
        
        # Support nested position format: {"position": {"x": 100, "y": 200}}
        if position_x is None or position_y is None:
            position = proc.get("position", {})
            if isinstance(position, dict):
                position_x = position.get("x")
                position_y = position.get("y")
        
        result = await _create_nifi_processor_single(
            processor_type=proc.get("processor_type"),
            name=proc.get("name"),
            position_x=position_x,
            position_y=position_y,
            process_group_id=proc.get("process_group_id"),
            properties=proc.get("properties")
        )
        results.append(result)
    return results

# DEPRECATED: Use create_nifi_connections_smart instead
# This function is kept for backward compatibility but will be removed
async def _create_nifi_connections_legacy(
    connections: List[Dict[str, Any]]
) -> List[Dict]:
    """
    Creates one or more NiFi connections in batch.

    IMPORTANT: This tool requires component UUIDs, not names. Use list_nifi_objects 
    to get UUIDs first, or use create_complete_nifi_flow for name-based connections.

    Args:
        connections: List of connection definitions. Each dictionary must contain:
            - source_id (str): UUID of the source component (NOT the name)
            - target_id (str): UUID of the target component (NOT the name) 
            - relationships (list): List of relationship names to connect
           
    Example - CORRECT format:
    ```python
    connections = [\n        {\n            \"source_id\": \"93865bc5-0197-1000-1b2c-1865bbf13903\",  # UUID from list_nifi_objects\n            \"target_id\": \"93865be3-0197-1000-9448-9091b9911330\",  # UUID from list_nifi_objects\n            \"relationships\": [\"success\"]\n        }\n    ]\n    ```
    
    Common Mistakes to Avoid:\n    ❌ Using component names: {\"source\": \"HandleHttpRequest\", \"target\": \"LogAttribute\"}\n    ❌ Wrong parameter names: {\"source_id\": \"...\", \"destination_id\": \"...\"}  \n    ❌ Wrong parameter names: {\"objects\": [...]}  # This is for delete_nifi_objects\n    
    \n    For name-based connections, use create_complete_nifi_flow instead.

    Returns:
        List of result dictionaries, one per connection creation attempt.
    """
    # Convert legacy UUID-based format to new format and delegate to main tool
    converted_connections = []
    for conn in connections:
        converted_connections.append({
            "source_name": conn.get("source_id"),  # UUIDs will be detected automatically
            "target_name": conn.get("target_id"),  # UUIDs will be detected automatically  
            "relationships": conn.get("relationships", [])
        })
    
    # Call the new unified tool
    return await create_nifi_connections(converted_connections)

@mcp.tool()
@tool_phases(["Modify"])
async def create_nifi_ports(
    ports: List[PortDefinition]
) -> List[Dict]:
    """
    Creates one or more NiFi ports in batch.

    Args:
        ports: List of port definitions. Each dictionary must contain:
            - port_type (str): Either "input" or "output"
            - name (str): The desired name for the port instance
            - position_x (int) OR position (dict): X coordinate OR nested position object {"x": 100, "y": 200}
            - position_y (int): Y coordinate (only if using position_x format)
            - process_group_id (str, optional): UUID of the target process group

    Returns:
        List of result dictionaries, one per port creation attempt.
    """
    results = []
    for port in ports:
        # CRITICAL FIX: Handle both flat and nested position formats
        position_x = port.get("position_x")
        position_y = port.get("position_y")
        
        # Support nested position format: {"position": {"x": 100, "y": 200}}
        if position_x is None or position_y is None:
            position = port.get("position", {})
            if isinstance(position, dict):
                position_x = position.get("x")
                position_y = position.get("y")
        
        result = await _create_nifi_port_single(
            port_type=port.get("port_type"),
            name=port.get("name"),
            position_x=position_x,
            position_y=position_y,
            process_group_id=port.get("process_group_id")
        )
        results.append(result)
    return results

@mcp.tool()
@tool_phases(["Modify"])
async def create_controller_services(
    controller_services: List[ControllerServiceDefinition],
    process_group_id: str
) -> List[Dict]:
    """
    Creates one or more controller services in batch within a specified process group.
    
    Args:
        controller_services: List of controller service definitions, each containing:
            - service_type: The fully qualified Java class name (required)
            - name: The desired name for the controller service (required)
            - properties: Dict of configuration properties (optional)
        process_group_id: The UUID of the process group where services should be created (required)
    
    Returns:
        List of results, one per controller service creation attempt.
        Includes enhanced error handling with available types when invalid types are provided.
        
    Example:
        ```python
        controller_services = [{
            "service_type": "org.apache.nifi.dbcp.DBCPConnectionPool",
            "name": "DatabaseConnectionPool",
            "properties": {
                "Database Connection URL": "jdbc:postgresql://localhost:5432/mydb",
                "Database Driver Class Name": "org.postgresql.Driver"
            }
        }]
        process_group_id = "process-group-uuid"
        result = create_controller_services(controller_services, process_group_id)
        ```
    """
    results = []
    for cs in controller_services:
        # Validate required fields
        service_type = cs.get("service_type")
        name = cs.get("name")
        
        if not service_type:
            results.append({
                "status": "error", 
                "message": "Controller service definition missing required field 'service_type'.", 
                "definition": cs
            })
            continue
            
        if not name:
            results.append({
                "status": "error", 
                "message": "Controller service definition missing required field 'name'.", 
                "definition": cs
            })
            continue
        
        result = await _create_controller_service_single(
            service_type=service_type,
            name=name,
            process_group_id=process_group_id,
            properties=cs.get("properties")
        )
        results.append(result)
    
    return results

@mcp.tool()
@tool_phases(["Modify"])
async def create_nifi_process_group(
    name: str,
    position_x: int,
    position_y: int,
    parent_process_group_id: str | None = None
) -> Dict:
    """
    Creates a new process group within a specified parent NiFi process group.

    Args:
        name: The desired name for the new process group.
        position_x: The desired X coordinate for the process group on the canvas.
        position_y: The desired Y coordinate for the process group on the canvas.
        parent_process_group_id: The UUID of the parent process group where the new group should be created. Defaults to the root group if None.

    Returns:
        A dictionary representing the result, including status and the created process group entity.
    """
    # Get client and logger from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
         
    # Authentication handled by factory
    # context = local_logger._context # REMOVED
    # user_request_id = context.get("user_request_id", "-") # REMOVED
    # action_id = context.get("action_id", "-") # REMOVED
    # --- Get IDs from context ---
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"
    # --------------------------

    target_parent_pg_id = parent_process_group_id
    if target_parent_pg_id is None:
        local_logger.info("No parent_process_group_id provided, fetching root process group ID.")
        try:
            nifi_request_data = {"operation": "get_root_process_group_id"}
            local_logger.bind(interface="nifi", direction="request", data=nifi_request_data).debug("Calling NiFi API")
            target_parent_pg_id = await nifi_client.get_root_process_group_id(user_request_id=user_request_id, action_id=action_id)
            nifi_response_data = {"root_pg_id": target_parent_pg_id}
            local_logger.bind(interface="nifi", direction="response", data=nifi_response_data).debug("Received from NiFi API")
        except Exception as e:
            local_logger.error(f"Failed to get root process group ID: {e}", exc_info=True)
            local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
            raise ToolError(f"Failed to determine root process group ID for PG creation: {e}")

    position = {"x": position_x, "y": position_y}
    local_logger = local_logger.bind(parent_process_group_id=target_parent_pg_id)
    local_logger.info(f"Executing create_nifi_process_group: Name='{name}', Position={position}")

    try:
        nifi_request_data = {
            "operation": "create_process_group",
            "parent_process_group_id": target_parent_pg_id,
            "name": name,
            "position": position
        }
        local_logger.bind(interface="nifi", direction="request", data=nifi_request_data).debug("Calling NiFi API")

        pg_entity = await nifi_client.create_process_group(target_parent_pg_id, name, position)
        filtered_entity = filter_process_group_data(pg_entity)
        local_logger.bind(interface="nifi", direction="response", data=filtered_entity).debug("Received from NiFi API")
        
        pg_id = pg_entity.get('id', 'N/A')
        local_logger.info(f"Successfully created process group '{name}' with ID: {pg_id}")
        return {
            "status": "success",
            "message": f"Process group '{name}' created successfully.",
            "entity": filtered_entity
        }

    except (NiFiAuthenticationError, ConnectionError, ValueError) as e:
        local_logger.error(f"API error creating process group: {e}", exc_info=False)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"Failed to create NiFi process group: {e}", "entity": None}
    except Exception as e:
        local_logger.error(f"Unexpected error creating process group: {e}", exc_info=True)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        return {"status": "error", "message": f"An unexpected error occurred during process group creation: {e}", "entity": None}


# REMOVED: create_nifi_flow tool - replaced by create_complete_nifi_flow
# The enhanced create_complete_nifi_flow tool provides all functionality of this tool
# plus controller services, @ServiceName resolution, and automatic flow validation
#
# DEPRECATED: This function has been removed from MCP tool registration.
# Use create_complete_nifi_flow instead, which supports:
# - Controller services with automatic enabling
# - Processors with @ServiceName reference resolution  
# - Connections with name-based mapping
# - Automatic flow validation
async def create_nifi_flow(
    nifi_objects: List[Dict[str, Any]],
    process_group_id: str | None = None,
    create_process_group: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    DEPRECATED: Creates a NiFi flow based on a list of processors and connections.
    
    This function has been replaced by create_complete_nifi_flow which provides
    enhanced functionality including controller services and flow validation.
    
    This function is kept for backward compatibility in tests but is no longer
    exposed as an MCP tool.

    Args:
        nifi_objects: A list where each item describes a processor or connection.
        process_group_id: The ID of the existing process group to create the flow in.
        create_process_group: Optional. If provided, a new process group is created.

    Returns:
        A list of results, one for each object creation attempt (success or error).
    """
    # Get client and logger from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
         
    # Authentication handled by factory
    # context = local_logger._context # REMOVED
    # user_request_id = context.get("user_request_id", "-") # REMOVED
    # action_id = context.get("action_id", "-") # REMOVED
    # --- Get IDs from context ---
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"
    # --------------------------
    
    results = []
    id_map = {}  # Maps local_id to actual NiFi ID
    target_pg_id = process_group_id
    parent_pg_id_for_new_group = None

    try:
        # 1. Determine target process group ID
        if create_process_group:
            local_logger.info(f"Request to create new process group: {create_process_group.get('name')}")
            pg_name = create_process_group.get("name")
            pg_pos_x = create_process_group.get("position_x", 0)
            pg_pos_y = create_process_group.get("position_y", 0)
            parent_pg_id_for_new_group = process_group_id # Parent for the *new* group
            
            if not pg_name:
                raise ToolError("Missing 'name' in create_process_group configuration.")

            # Determine the parent ID for the new PG (defaults to root if not specified)
            if parent_pg_id_for_new_group is None:
                parent_pg_id_for_new_group = await nifi_client.get_root_process_group_id(user_request_id=user_request_id, action_id=action_id)
                local_logger.info(f"No explicit parent ID for new group, using root: {parent_pg_id_for_new_group}")

            # Call create_nifi_process_group tool logic (or direct client call)
            pg_creation_result = await create_nifi_process_group(
                name=pg_name,
                position_x=pg_pos_x,
                position_y=pg_pos_y,
                parent_process_group_id=parent_pg_id_for_new_group
            )
            results.append(pg_creation_result)
            if pg_creation_result.get("status") == "error":
                raise ToolError(f"Failed to create process group: {pg_creation_result.get('message')}")
            
            target_pg_id = pg_creation_result.get("entity", {}).get("id")
            if not target_pg_id:
                 raise ToolError("Could not get ID of newly created process group.")
            local_logger.info(f"Successfully created process group '{pg_name}' with ID {target_pg_id}. Flow will be built inside.")
        
        elif target_pg_id is None:
            target_pg_id = await nifi_client.get_root_process_group_id(user_request_id=user_request_id, action_id=action_id)
            local_logger.info(f"No process group specified, using root group: {target_pg_id}")
        else:
            local_logger.info(f"Using existing process group: {target_pg_id}")
        
        local_logger = local_logger.bind(target_process_group_id=target_pg_id)

        # 2. Create Processors
        local_logger.info(f"Processing {len(nifi_objects)} objects for processor creation...")
        for item in nifi_objects:
            if item.get("type") == "processor": # Check top-level type
                proc_def = item # Use the item directly
                proc_name = proc_def.get("name")
                # Get type from 'class' key, fallback to 'processor_type' or 'type'
                proc_type = proc_def.get("class") or proc_def.get("processor_type") or proc_def.get("type")
                # Get position dictionary
                position_dict = proc_def.get("position", {})
                pos_x = position_dict.get("x")
                pos_y = position_dict.get("y")
                # Get properties (might be nested or top-level depending on LLM mood)
                properties = proc_def.get("properties", {}) 

                if not proc_name:
                    results.append({"status": "error", "message": "Processor definition missing 'name'.", "definition": proc_def})
                    continue
                if proc_name in id_map:
                    results.append({"status": "error", "message": f"Duplicate processor name '{proc_name}' found. Names must be unique for connection mapping.", "definition": proc_def})
                    continue

                if not all([proc_type, pos_x is not None, pos_y is not None]):
                    results.append({"status": "error", "message": f"Processor '{proc_name}' definition missing required fields (class/processor_type/type, position.x, position.y).", "definition": proc_def})
                    continue

                local_logger.info(f"Attempting to create processor: Name='{proc_name}', Type='{proc_type}'")
                # Call create_nifi_processor tool logic
                proc_creation_result = await _create_nifi_processor_single(
                    processor_type=proc_type,
                    name=proc_name,
                    position_x=pos_x,
                    position_y=pos_y,
                    process_group_id=target_pg_id,
                    properties=properties
                )
                # We also need to apply the properties if provided
                created_proc_id = None
                if proc_creation_result.get("status") != "error":
                    created_proc_id = proc_creation_result.get("entity", {}).get("id")
                proc_creation_result["name_used_for_mapping"] = proc_name
                results.append(proc_creation_result)

                if proc_creation_result.get("status") != "error" and created_proc_id:
                    id_map[proc_name] = created_proc_id # Map NAME to NiFi ID
                    local_logger.debug(f"Mapped name '{proc_name}' to ID '{created_proc_id}'")
                elif proc_creation_result.get("status") != "error" and not created_proc_id:
                    local_logger.error(f"Could not get NiFi ID for created processor with name '{proc_name}'")
                    proc_creation_result["status"] = "error"
                    proc_creation_result["message"] += " (Could not retrieve NiFi ID after creation)"
                else:
                    local_logger.error(f"Failed to create processor with name '{proc_name}': {proc_creation_result.get('message')}")

        # 3. Create Connections
        local_logger.info(f"Processing {len(nifi_objects)} objects for connection creation...")
        for item in nifi_objects:
            if item.get("type") == "connection": # Check top-level type
                conn_def = item # Use the item directly
                # Extract details using names
                source_name = conn_def.get("source") # Expecting name
                target_name = conn_def.get("dest") or conn_def.get("destination") # Allow variations
                relationships = conn_def.get("relationships")

                if not all([source_name, target_name, relationships]):
                    results.append({"status": "error", "message": "Connection definition missing required fields (source name, dest/destination name, relationships).", "definition": conn_def})
                    continue
                
                # Use names to lookup NiFi IDs from our map
                source_nifi_id = id_map.get(source_name)
                target_nifi_id = id_map.get(target_name)

                if not source_nifi_id:
                    results.append({"status": "error", "message": f"Source component with name '{source_name}' not found or failed to create.", "definition": conn_def})
                    continue
                if not target_nifi_id:
                    results.append({"status": "error", "message": f"Target component with name '{target_name}' not found or failed to create.", "definition": conn_def})
                    continue

                local_logger.info(f"Attempting to create connection: From='{source_name}' ({source_nifi_id}) To='{target_name}' ({target_nifi_id}) Rel='{relationships}'")
                # Call create_nifi_connection tool logic
                conn_creation_result = await _create_nifi_connection_single(
                    source_id=source_nifi_id,
                    target_id=target_nifi_id,
                    relationships=relationships
                )
                conn_creation_result["definition"] = conn_def
                results.append(conn_creation_result)
                
                if conn_creation_result.get("status") == "error":
                     local_logger.error(f"Failed to create connection from '{source_name}' to '{target_name}': {conn_creation_result.get('message')}")
                 
        # 4. Identify and report any unprocessed items
        processed_indices = set()
        for i, item in enumerate(nifi_objects):
            # Check if item was processed as a processor or connection based on our loops
            if item.get("type") == "processor" or item.get("type") == "connection":
                 processed_indices.add(i)
            # Add checks here if we support other types later

        unprocessed_items = []
        for i, item in enumerate(nifi_objects):
            if i not in processed_indices:
                unprocessed_items.append({
                    "index": i,
                    "item": item, # Include the problematic item for context
                    "message": f"Input object at index {i} was ignored. Expected 'type' to be 'processor' or 'connection'."
                })

        if unprocessed_items:
            local_logger.warning(f"Found {len(unprocessed_items)} unprocessed items in the input list.")
            # Add a summary error or individual errors to the main results
            results.append({
                "status": "warning",
                "message": f"{len(unprocessed_items)} input object(s) were ignored due to unrecognized type.",
                "unprocessed_details": unprocessed_items # Provide details
            })

        local_logger.info("Finished processing all objects for flow creation.")
        return results

    except ToolError as e:
        local_logger.error(f"ToolError during flow creation: {e}", exc_info=False)
        # Append the final error to results if possible
        results.append({"status": "error", "message": f"Flow creation failed: {e}"})
        return results
    except Exception as e:
        local_logger.error(f"Unexpected error during flow creation: {e}", exc_info=True)
        results.append({"status": "error", "message": f"An unexpected error occurred during flow creation: {e}"})
        return results

@mcp.tool()
@tool_phases(["Build"])
async def create_complete_nifi_flow(
    nifi_objects: List[NifiObjectDefinition],
    process_group_id: str | None = None,
    create_process_group: Optional[Dict[str, Any]] = None,
    connections: Optional[List[ConnectionDefinition]] = None
) -> Dict[str, Any]:
    """
    Creates a complete NiFi flow with controller services, processors, and connections.
    
    This enhanced flow creation tool provides comprehensive support for:
    - Controller services with automatic enabling
    - Processors with @ServiceName reference resolution
    - Connections with name-based mapping
    - Automatic flow validation
    - Simplified input schema
    - Atomic operations with rollback on failure (Phase 1B)
    
    Example (Method 1 - All in nifi_objects):
    ```python
    nifi_objects = [
        {
            "type": "controller_service",
            "service_type": "org.apache.nifi.json.JsonTreeReader", 
            "name": "JsonReader",
            "properties": {"Schema Access Strategy": "Use Schema Name Property"}
        },
        {
            "type": "processor",
            "processor_type": "org.apache.nifi.processors.standard.HandleHttpRequest",
            "name": "ReceiveHTTP", 
            "position": {"x": 100, "y": 100},
            "properties": {
                "Listening Port": "8080",
                "HTTP Context Map": "@HttpContextMap"
            }
        },
        {
            "type": "connection",
            "source": "ReceiveHTTP",
            "target": "ProcessData", 
            "relationships": ["success"]
        }
    ]
    ```
    
    Example (Method 2 - Separate connections):
    ```python
    nifi_objects = [
        # controller services and processors only
    ]
    connections = [
        {
            "source": "ReceiveHTTP",
            "target": "ProcessData", 
            "relationships": ["success"]
        }
    ]
    ```
    
    Args:
        nifi_objects: List of objects to create. Supported types:
            - controller_service: {type, service_type, name, properties}
            - processor: {type, processor_type, name, position, properties}  
            - connection: {type, source, target, relationships} (optional here)
        process_group_id: Target process group ID (defaults to root)
        create_process_group: Optional new process group config
        connections: Optional separate list of connections (alternative to including in nifi_objects)
        
    Returns:
        Comprehensive results including created objects, validation status, and summary
    """
    # Get client and logger from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
         
    # Get IDs from context
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"
    
    results = []
    id_map = {}  # Maps names to actual NiFi IDs
    service_map = {}  # Maps service names to IDs for @ServiceName resolution
    target_pg_id = process_group_id
    
    # Phase 1B: Rollback tracking
    created_objects = []  # Track all created objects for potential rollback
    rollback_enabled = True
    
    # Statistics tracking
    stats = {
        "controller_services_created": 0,
        "controller_services_enabled": 0, 
        "processors_created": 0,
        "connections_created": 0,
        "errors": 0,
        "warnings": 0
    }

    try:
        local_logger.info("Starting complete NiFi flow creation with atomic operations...")
        
        # 1. Determine target process group ID
        if create_process_group:
            local_logger.info(f"Creating new process group: {create_process_group.get('name')}")
            pg_name = create_process_group.get("name")
            pg_pos_x = create_process_group.get("position_x", 0)
            pg_pos_y = create_process_group.get("position_y", 0)
            parent_pg_id = process_group_id
            
            if not pg_name:
                raise ToolError("Missing 'name' in create_process_group configuration.")

            if parent_pg_id is None:
                parent_pg_id = await nifi_client.get_root_process_group_id(user_request_id=user_request_id, action_id=action_id)
                local_logger.info(f"Using root group as parent: {parent_pg_id}")

            pg_creation_result = await create_nifi_process_group(
                name=pg_name,
                position_x=pg_pos_x,
                position_y=pg_pos_y,
                parent_process_group_id=parent_pg_id
            )
            pg_creation_result["object_type"] = "process_group"
            results.append(pg_creation_result)
            
            if pg_creation_result.get("status") == "error":
                raise ToolError(f"Failed to create process group: {pg_creation_result.get('message')}")
            
            target_pg_id = pg_creation_result.get("entity", {}).get("id")
            if not target_pg_id:
                 raise ToolError("Could not get ID of newly created process group.")
            
            # Track process group for rollback
            created_objects.append({
                "type": "process_group",
                "id": target_pg_id,
                "name": pg_name
            })
            
            local_logger.info(f"Process group '{pg_name}' created with ID: {target_pg_id}")
        
        elif target_pg_id is None:
            target_pg_id = await nifi_client.get_root_process_group_id(user_request_id=user_request_id, action_id=action_id)
            local_logger.info(f"Using root process group: {target_pg_id}")
            rollback_enabled = False  # Don't rollback root process group
        else:
            local_logger.info(f"Using specified process group: {target_pg_id}")
            rollback_enabled = False  # Don't rollback existing process group
        
        local_logger = local_logger.bind(target_process_group_id=target_pg_id)

        # ✅ FIX 1: Validate types upfront before any creation
        local_logger.info("Validating processor and service types...")
        types_valid, type_error_response = await _validate_and_suggest_types_upfront(
            nifi_objects, nifi_client, local_logger, user_request_id, action_id
        )
        
        if not types_valid:
            local_logger.error("Type validation failed - returning suggestions")
            return type_error_response

        # 2. Create Controller Services
        controller_services = [obj for obj in nifi_objects if obj.get("type") == "controller_service"]
        processors = [obj for obj in nifi_objects if obj.get("type") == "processor"]
        
        # Phase 2B: Enhanced Connection Logic - Support both input methods
        connections_from_objects = [obj for obj in nifi_objects if obj.get("type") == "connection"]
        connections_from_param = connections or []
        all_connections = connections_from_objects + connections_from_param
        
        local_logger.info(f"Creating {len(controller_services)} controller services...")
        
        # ✅ FIX 2: Enhanced Duplicate Prevention with clear guidance
        existing_objects = {}
        if controller_services or processors or all_connections:
            local_logger.info("Checking for existing objects to prevent duplicates...")
            try:
                existing_list = await list_nifi_objects(
                    object_type="processors",
                    process_group_id=target_pg_id
                )
                existing_services = await list_nifi_objects(
                    object_type="controller_services", 
                    process_group_id=target_pg_id
                )
                
                # Build comprehensive map of existing objects by name and type
                for obj in existing_list + existing_services:
                    obj_name = obj.get("name", "")
                    obj_type = obj.get("type", "")
                    if obj_name:
                        existing_objects[obj_name] = {
                            "id": obj.get("id"),
                            "name": obj_name,
                            "type": obj_type,
                            "object_category": "processor" if "processor" in obj_type.lower() else "controller_service"
                        }
                        
                local_logger.info(f"Found {len(existing_objects)} existing objects in process group")
            except Exception as e:
                local_logger.warning(f"Could not check existing objects: {e}")
        
        # FIX 2: Pre-flight duplicate detection with clear error messages
        new_names = {obj.get("name") for obj in nifi_objects if obj.get("name")}
        conflicts = set(existing_objects.keys()) & new_names
        
        if conflicts:
            conflict_details = []
            detailed_results = []
            for name in conflicts:
                existing_obj = existing_objects[name]
                conflict_details.append(f"'{name}' (existing: {existing_obj['type']})")
                # Add detailed result for test compatibility
                detailed_results.append({
                    "status": "error",
                    "message": f"Object named '{name}' already exists in process group",
                    "object_type": "duplicate_name",
                    "name": name,
                    "existing_type": existing_obj['type']
                })
            
            return {
                "status": "error",
                "message": f"Objects already exist in process group: {', '.join(conflicts)}",
                "conflict_details": conflict_details,
                "existing_objects": list(existing_objects.keys()),
                "suggested_action": "Use 'list_nifi_objects' to see existing objects, choose different names, or use 'update_*' tools to modify existing objects",
                "detailed_results": detailed_results,  # Add this for test compatibility
                "summary": {
                    "process_group_id": target_pg_id,
                    "objects_processed": 0,
                    "controller_services_created": 0,
                    "controller_services_enabled": 0,
                    "processors_created": 0,
                    "connections_created": 0,
                    "total_errors": 1,
                    "total_warnings": 0
                }
            }
        
        for cs_def in controller_services:
            name = cs_def.get("name")
            service_type = cs_def.get("service_type") or cs_def.get("class")
            properties = cs_def.get("properties", {})
            
            if not all([name, service_type]):
                error_result = {
                    "status": "error",
                    "message": "Controller service missing required fields (name, service_type)",
                    "definition": cs_def,
                    "object_type": "controller_service"
                }
                results.append(error_result)
                stats["errors"] += 1
                continue
                
            if name in service_map:
                error_result = {
                    "status": "error", 
                    "message": f"Duplicate controller service name '{name}' found. Names must be unique.",
                    "definition": cs_def,
                    "object_type": "controller_service"
                }
                results.append(error_result)
                stats["errors"] += 1
                continue
                
            # Note: Duplicate checking now handled in pre-flight phase above
            
            local_logger.info(f"Creating controller service: {name} ({service_type})")
            cs_result = await create_controller_services([{
                "service_type": service_type,
                "name": name,
                "properties": properties
            }], target_pg_id)
            # Extract the single result from the list
            cs_result = cs_result[0] if cs_result else {"status": "error", "message": "No result returned"}
            
            cs_result["object_type"] = "controller_service"
            cs_result["name"] = name
            results.append(cs_result)
            
            if cs_result.get("status") != "error":
                created_id = cs_result.get("entity", {}).get("id")
                if created_id:
                    service_map[name] = created_id
                    stats["controller_services_created"] += 1
                    
                    # Track for rollback
                    created_objects.append({
                        "type": "controller_service",
                        "id": created_id,
                        "name": name
                    })
                    
                    local_logger.debug(f"Mapped service '{name}' to ID '{created_id}'")
                else:
                    cs_result["status"] = "error"
                    cs_result["message"] += " (Could not retrieve ID after creation)"
                    stats["errors"] += 1
            else:
                stats["errors"] += 1
                
                # CRITICAL FIX: Immediate rollback on critical service failures
                error_msg = cs_result.get("message", "")
                is_critical_error = any(phrase in error_msg.lower() for phrase in [
                    "not known to this nifi",
                    "invalid service type", 
                    "service type not found",
                    "409",  # Conflict status code
                    "400"   # Bad request status code
                ])
                
                if is_critical_error or stats["errors"] >= 2:  # Lower threshold for services
                    local_logger.error(f"Critical controller service failure detected: {error_msg}")
                    raise ToolError(f"Critical controller service creation failure. Error: {cs_result.get('message')}")

        # 3. Enable Controller Services
        if service_map:
            local_logger.info(f"Enabling {len(service_map)} controller services...")
            enable_requests = []
            for service_name, service_id in service_map.items():
                enable_requests.append({
                    "object_type": "controller_service",
                    "object_id": service_id,
                    "operation_type": "enable",
                    "name": service_name
                })
            
            # Use our batch operation tool
            enable_results = await operate_nifi_objects(enable_requests)
            
            for enable_result in enable_results:
                if enable_result.get("status") == "success":
                    stats["controller_services_enabled"] += 1
                elif enable_result.get("status") == "error":
                    stats["errors"] += 1
                    # Service enabling failure is not critical - continue
                    local_logger.warning(f"Service enabling failed: {enable_result.get('message')}")
                else:
                    stats["warnings"] += 1
                    
                # Add to results with context
                enable_result["operation"] = "enable_controller_service"
                results.append(enable_result)

        # 4. Create Processors (with @ServiceName resolution)
        local_logger.info(f"Creating {len(processors)} processors...")
        
        for proc_def in processors:
            name = proc_def.get("name")
            processor_type = proc_def.get("processor_type") or proc_def.get("class")
            position = proc_def.get("position", {})
            properties = proc_def.get("properties", {}).copy()  # Copy to avoid mutating original
            
            if not all([name, processor_type, position.get("x") is not None, position.get("y") is not None]):
                error_result = {
                    "status": "error",
                    "message": f"Processor missing required fields (name, processor_type, position.x, position.y)",
                    "definition": proc_def,
                    "object_type": "processor"
                }
                results.append(error_result)
                stats["errors"] += 1
                continue
                
            if name in id_map:
                error_result = {
                    "status": "error",
                    "message": f"Duplicate name '{name}' found. Names must be unique.",
                    "definition": proc_def,
                    "object_type": "processor"
                }
                results.append(error_result)
                stats["errors"] += 1
                continue
                
            # Note: Duplicate checking now handled in pre-flight phase above

            # ✅ FIX 4: Enhanced Property Schema Validation BEFORE creation
            validated_properties, schema_warnings, schema_errors = await _validate_processor_properties_upfront(
                processor_type=processor_type,
                properties=properties,
                nifi_client=nifi_client,
                user_request_id=user_request_id,
                action_id=action_id
            )
            
            # Fail fast on schema errors
            if schema_errors:
                error_result = {
                    "status": "error",
                    "message": f"Property schema validation failed for processor '{name}': {'; '.join(schema_errors)}",
                    "definition": proc_def,
                    "object_type": "processor",
                    "schema_errors": schema_errors
                }
                results.append(error_result)
                stats["errors"] += 1
                continue

            # Phase 2B: Enhanced property validation - remove invalid properties
            phase2b_properties, phase2b_warnings = await _validate_processor_properties_phase2b(
                processor_type=processor_type,
                properties=validated_properties,  # Use schema-validated properties
                logger=local_logger
            )

            # Phase 1A & 1C: Enhanced service reference resolution and property validation
            resolved_properties, warnings, errors = await _validate_and_fix_processor_properties(
                processor_type=processor_type,
                properties=phase2b_properties,  # Use phase2b-validated properties
                service_map=service_map,
                process_group_id=target_pg_id,
                nifi_client=nifi_client,
                logger=local_logger,
                user_request_id=user_request_id,
                action_id=action_id
            )
            
            # Combine warnings from all phases
            all_warnings = schema_warnings + phase2b_warnings + warnings
            
            # Track unresolved service references for test compatibility
            unresolved_refs = []
            for prop_key, prop_value in properties.items():
                if isinstance(prop_value, str) and prop_value.startswith("@"):
                    service_name = prop_value[1:]  # Remove @ prefix
                    if service_name not in service_map:
                        # Check if it was resolved by process group lookup
                        resolved_value = resolved_properties.get(prop_key)
                        if resolved_value == prop_value:  # Unchanged, so unresolved
                            unresolved_refs.append(prop_value)
            
            # CRITICAL FIX: Check for critical errors that would prevent processor creation
            if errors:
                error_result = {
                    "status": "error",
                    "message": f"Processor '{name}' has validation errors: {'; '.join(errors)}",
                    "definition": proc_def,
                    "object_type": "processor",
                    "validation_errors": errors
                }
                results.append(error_result)
                stats["errors"] += 1
                
                # CRITICAL FIX: Immediate rollback on service reference failures
                has_service_ref_error = any("service reference" in error.lower() for error in errors)
                if has_service_ref_error or stats["errors"] >= 3:  # Lower threshold for processors
                    local_logger.error(f"Critical processor validation failure detected: {errors}")
                    raise ToolError(f"Critical processor validation failure. Error: {'; '.join(errors)}")
                continue
            
            local_logger.info(f"Creating processor: {name} ({processor_type})")
            proc_result = await create_nifi_processors([{
                "processor_type": processor_type,
                "name": name,
                "position_x": position["x"],
                "position_y": position["y"],
                "process_group_id": target_pg_id,
                "properties": resolved_properties
            }])
            # Extract the single result from the list
            proc_result = proc_result[0] if proc_result else {"status": "error", "message": "No result returned"}
            
            proc_result["object_type"] = "processor"
            proc_result["name"] = name
            if all_warnings:
                proc_result["warnings"] = all_warnings
            if errors:
                proc_result["errors"] = errors
            if unresolved_refs:
                proc_result["unresolved_service_references"] = unresolved_refs
                
            results.append(proc_result)
            
            if proc_result.get("status") != "error":
                created_id = proc_result.get("entity", {}).get("id")
                if created_id:
                    id_map[name] = created_id
                    stats["processors_created"] += 1
                    
                    # Track for rollback
                    created_objects.append({
                        "type": "processor",
                        "id": created_id,
                        "name": name
                    })
                    
                    local_logger.debug(f"Mapped processor '{name}' to ID '{created_id}'")
                else:
                    proc_result["status"] = "error"
                    proc_result["message"] += " (Could not retrieve ID after creation)"
                    stats["errors"] += 1
            else:
                stats["errors"] += 1
                # CRITICAL FIX: Lower threshold for processor failures
                if stats["errors"] >= 3:  # Reduced from 5 to 3
                    raise ToolError(f"Too many processor creation failures. Last error: {proc_result.get('message')}")

        # 4.5. PHASE 2B PREVIEW: Intelligent Relationship Auto-Termination
        # Auto-terminate relationships that won't be used by connections to prevent validation errors
        if len(id_map) > 0:  # Only if we have processors
            local_logger.info("Analyzing relationships for intelligent auto-termination...")
            relationship_updates = await _analyze_and_auto_terminate_relationships(
                nifi_objects=nifi_objects,
                id_map=id_map,
                nifi_client=nifi_client,
                logger=local_logger,
                user_request_id=user_request_id,
                action_id=action_id
            )
            
            # Apply relationship updates
            for update in relationship_updates:
                processor_id = update["processor_id"]
                processor_name = update["processor_name"]
                relationships_to_terminate = update["relationships_to_terminate"]
                
                if relationships_to_terminate:
                    local_logger.info(f"Auto-terminating relationships for '{processor_name}': {relationships_to_terminate}")
                    
                    rel_update_result = await update_nifi_processor_relationships(
                        processor_id=processor_id,
                        auto_terminated_relationships=relationships_to_terminate
                    )
                    
                    if rel_update_result.get("status") in ["success", "warning"]:
                        local_logger.info(f"Successfully auto-terminated relationships for '{processor_name}'")
                        stats["warnings"] += 1  # Count as warning since it's automatic
                        
                        # Add to results for transparency
                        results.append({
                            "status": "success",
                            "message": f"Auto-terminated unused relationships for processor '{processor_name}': {relationships_to_terminate}",
                            "object_type": "processor_relationships",
                            "processor_name": processor_name,
                            "processor_id": processor_id,
                            "auto_terminated_relationships": relationships_to_terminate
                        })
                    else:
                        local_logger.warning(f"Failed to auto-terminate relationships for '{processor_name}': {rel_update_result.get('message')}")
                        stats["warnings"] += 1

        # 5. Create Connections
        local_logger.info(f"Creating {len(all_connections)} connections...")
        
        for conn_def in all_connections:
            source_name = conn_def.get("source")
            target_name = conn_def.get("target") or conn_def.get("dest") or conn_def.get("destination")
            relationships = conn_def.get("relationships")
            
            if not all([source_name, target_name, relationships]):
                error_result = {
                    "status": "error",
                    "message": "Connection missing required fields (source, target, relationships)",
                    "definition": conn_def,
                    "object_type": "connection"
                }
                results.append(error_result)
                stats["errors"] += 1
                continue
            
            source_id = id_map.get(source_name)
            target_id = id_map.get(target_name)
            
            if not source_id:
                error_result = {
                    "status": "error",
                    "message": f"Source component '{source_name}' not found or failed to create",
                    "definition": conn_def,
                    "object_type": "connection"
                }
                results.append(error_result)
                stats["errors"] += 1
                continue
                
            if not target_id:
                error_result = {
                    "status": "error",
                    "message": f"Target component '{target_name}' not found or failed to create",
                    "definition": conn_def,
                    "object_type": "connection"
                }
                results.append(error_result)
                stats["errors"] += 1
                continue

            local_logger.info(f"Creating connection: {source_name} → {target_name} ({relationships})")
            conn_result = await _create_nifi_connection_single(
                source_id=source_id,
                target_id=target_id,
                relationships=relationships
            )
            
            conn_result["object_type"] = "connection"
            conn_result["source_name"] = source_name
            conn_result["target_name"] = target_name
            results.append(conn_result)
            
            if conn_result.get("status") == "success":
                stats["connections_created"] += 1
                
                # Track for rollback
                connection_id = conn_result.get("entity", {}).get("id")
                if connection_id:
                    created_objects.append({
                        "type": "connection",
                        "id": connection_id,
                        "name": f"{source_name}→{target_name}"
                    })
                    
            elif conn_result.get("status") == "error":
                stats["errors"] += 1
                # Connection failures are often due to missing components - continue
                local_logger.warning(f"Connection creation failed: {conn_result.get('message')}")
            else:
                stats["warnings"] += 1

        # 6. Flow Validation
        local_logger.info("Performing complete flow validation...")
        validation_result = await _validate_complete_flow(target_pg_id, nifi_client, local_logger, user_request_id, action_id)
        
        # 7. Prepare comprehensive response
        local_logger.info("Flow creation completed. Preparing response...")
        
        # Count success/warning/error statuses
        status_counts = {"success": 0, "warning": 0, "error": 0}
        for result in results:
            status = result.get("status", "unknown")
            if status in status_counts:
                status_counts[status] += 1

        # Determine overall status
        overall_status = "success"
        if stats["errors"] > 0:
            overall_status = "error"
        elif stats["warnings"] > 0 or status_counts["warning"] > 0:
            overall_status = "warning"

        response = {
            "status": overall_status,
            "message": f"Complete flow creation {overall_status}",
            "summary": {
                "process_group_id": target_pg_id,
                "objects_processed": len(nifi_objects),
                "controller_services_created": stats["controller_services_created"],
                "controller_services_enabled": stats["controller_services_enabled"],
                "processors_created": stats["processors_created"], 
                "connections_created": stats["connections_created"],
                "total_errors": stats["errors"],
                "total_warnings": stats["warnings"]
            },
            "validation": validation_result,
            "detailed_results": results
        }
        
        local_logger.info(f"Complete flow creation finished: {overall_status} - {len(results)} operations")
        return response

    except ToolError as e:
        local_logger.error(f"ToolError during complete flow creation: {e}", exc_info=False)
        
        # Phase 1B: Automatic rollback on critical failure
        if rollback_enabled and created_objects:
            local_logger.warning(f"Attempting rollback of {len(created_objects)} created objects...")
            rollback_results = await _rollback_created_objects(created_objects, nifi_client, local_logger, user_request_id, action_id)
            local_logger.info(f"Rollback completed: {rollback_results}")
        
        return {
            "status": "error",
            "message": f"Complete flow creation failed: {e}",
            "summary": stats,
            "detailed_results": results,
            "rollback_performed": rollback_enabled and len(created_objects) > 0
        }
    except Exception as e:
        local_logger.error(f"Unexpected error during complete flow creation: {e}", exc_info=True)
        
        # Phase 1B: Automatic rollback on unexpected failure
        if rollback_enabled and created_objects:
            local_logger.warning(f"Attempting emergency rollback of {len(created_objects)} created objects...")
            rollback_results = await _rollback_created_objects(created_objects, nifi_client, local_logger, user_request_id, action_id)
            local_logger.info(f"Emergency rollback completed: {rollback_results}")
        
        return {
            "status": "error", 
            "message": f"An unexpected error occurred during complete flow creation: {e}",
            "summary": stats,
            "detailed_results": results,
            "rollback_performed": rollback_enabled and len(created_objects) > 0
        }


async def _validate_complete_flow(
    process_group_id: str,
    nifi_client: NiFiClient,
    logger,
    user_request_id: str,
    action_id: str
) -> Dict[str, Any]:
    """
    Validates a complete NiFi flow for readiness and common issues.
    
    Returns validation summary with issues and recommendations.
    """
    try:
        # Get process group status for validation
        status_result = await get_process_group_status(
            process_group_id=process_group_id,
            include_bulletins=True,
            bulletin_limit=10
        )
        
        validation_issues = []
        recommendations = []
        
        if status_result.get("status") == "error":
            validation_issues.append("Could not retrieve process group status for validation")
            return {
                "status": "error",
                "message": "Flow validation failed",
                "issues": validation_issues,
                "recommendations": []
            }
        
        pg_status = status_result.get("process_group_status", {})
        
        # Check for invalid components
        invalid_components = pg_status.get("component_states", {}).get("invalid", 0)
        if invalid_components > 0:
            validation_issues.append(f"{invalid_components} component(s) have validation errors")
            recommendations.append("Review component configurations and resolve validation errors")
        
        # Check for disabled components  
        disabled_components = pg_status.get("component_states", {}).get("disabled", 0)
        if disabled_components > 0:
            validation_issues.append(f"{disabled_components} component(s) are disabled")
            recommendations.append("Enable required controller services and processors")
        
        # Check bulletins for errors
        bulletins = pg_status.get("bulletins", [])
        error_bulletins = [b for b in bulletins if b.get("level") == "ERROR"]
        if error_bulletins:
            validation_issues.append(f"{len(error_bulletins)} error bulletin(s) found")
            recommendations.append("Check component bulletins for specific error details")
        
        # Determine validation status
        if validation_issues:
            validation_status = "warning" if not any("error" in issue.lower() for issue in validation_issues) else "error"
        else:
            validation_status = "success"
        
        return {
            "status": validation_status,
            "message": "Flow validation completed",
            "issues": validation_issues,
            "recommendations": recommendations,
            "component_summary": pg_status.get("component_states", {}),
            "bulletin_count": len(bulletins)
        }
        
    except Exception as e:
        logger.error(f"Error during flow validation: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"Flow validation failed: {e}",
            "issues": ["Validation process encountered an error"],
            "recommendations": ["Manually verify flow configuration"]
        }

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
    
    Args:
        reference: The reference string to resolve
        service_map: Current name-to-ID mapping from flow creation
        process_group_id: Target process group for service lookup
        nifi_client: NiFi client for service queries
        logger: Logger instance
        user_request_id: Request ID for logging
        action_id: Action ID for logging
        
    Returns:
        tuple[resolved_id, is_resolved]: (resolved service ID, whether resolution succeeded)
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
            # Fall through to process group lookup
            reference = service_name
    
    # Pattern 3: Direct service name (fallback for backward compatibility)
    if reference in service_map:
        resolved_id = service_map[reference]
        logger.debug(f"Resolved service name '{reference}' → {resolved_id}")
        return resolved_id, True
    
    # Pattern 4: Process group service lookup (for existing services)
    try:
        logger.debug(f"Looking up service '{reference}' in process group {process_group_id}")
        # Get all controller services in the process group
        services_response = await nifi_client.get_controller_services(
            process_group_id=process_group_id,
            user_request_id=user_request_id,
            action_id=action_id
        )
        
        if services_response and services_response.get("controllerServices"):
            services = services_response["controllerServices"]
            
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


def _is_valid_uuid(uuid_string: str) -> bool:
    """Check if a string is a valid UUID format."""
    import re
    uuid_pattern = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        re.IGNORECASE
    )
    return bool(uuid_pattern.match(uuid_string))


async def _validate_and_fix_processor_properties(
    processor_type: str,
    properties: Dict[str, Any],
    service_map: Dict[str, str],
    process_group_id: str,
    nifi_client: NiFiClient,
    logger,
    user_request_id: str,
    action_id: str
) -> tuple[Dict[str, Any], list[str], list[str]]:
    """
    Phase 1C + 2A: Smart Property Validation & Defaults + Enhanced EL Validation
    
    Validates and auto-corrects processor properties with:
    - Service reference resolution (enhanced)
    - Common property value corrections  
    - Required property defaults
    - NiFi Expression Language validation and auto-correction
    
    Returns:
        tuple[fixed_properties, warnings, errors]: (corrected properties, warning messages, error messages)
    """
    fixed_properties = properties.copy()
    warnings = []
    errors = []
    
    # Property schemas for common processors
    PROPERTY_SCHEMAS = {
        'org.apache.nifi.processors.script.ExecuteScript': {
            'Script Engine': {
                'required': True,
                'valid_values': ['ECMAScript', 'Groovy', 'python', 'ruby', 'lua', 'Clojure'],
                'default': 'ECMAScript',
                'aliases': {'JavaScript': 'ECMAScript'}  # Common mistake
            }
        },
        'org.apache.nifi.processors.standard.HandleHttpResponse': {
            'HTTP Status Code': {
                'required': True,
                'default': '200'
            },
            'HTTP Context Map': {
                'required': True,
                'type': 'controller_service_reference'
            }
        },
        'org.apache.nifi.processors.standard.HandleHttpRequest': {
            'HTTP Context Map': {
                'required': True,
                'type': 'controller_service_reference'
            },
            'Listening Port': {
                'default': '80'
            }
        },
        'org.apache.nifi.processors.standard.RouteOnAttribute': {
            'Routing Strategy': {
                'default': 'Route to Property name'
            }
        }
    }
    
    schema = PROPERTY_SCHEMAS.get(processor_type, {})
    
    # Get EL-enabled properties for this processor type
    el_properties = _get_processor_el_properties(processor_type)
    
    # Process each property in the schema
    for prop_name, prop_schema in schema.items():
        current_value = properties.get(prop_name)
        
        # Handle aliases (common mistakes)
        if current_value and 'aliases' in prop_schema and current_value in prop_schema['aliases']:
            corrected_value = prop_schema['aliases'][current_value]
            fixed_properties[prop_name] = corrected_value
            warnings.append(f"Auto-corrected '{prop_name}': '{current_value}' → '{corrected_value}'")
            logger.info(f"Property correction: {prop_name} {current_value} → {corrected_value}")
            continue
        
        # Add missing required properties with defaults OR handle required service references
        if not current_value and prop_schema.get('required'):
            if 'default' in prop_schema:
                # Standard default value
                default_value = prop_schema['default']
                fixed_properties[prop_name] = default_value
                warnings.append(f"Added missing required property '{prop_name}': '{default_value}'")
                logger.info(f"Added default property: {prop_name} = {default_value}")
                continue
            elif prop_schema.get('type') == 'controller_service_reference':
                # Required service reference - need to auto-create or find service
                service_suggestion = _suggest_missing_service_reference(
                    processor_type, prop_name, process_group_id, service_map
                )
                if service_suggestion:
                    # Only add the reference if it's a real UUID (existing service)
                    # For @ServiceName suggestions, just warn - don't add unresolvable references
                    if not service_suggestion['reference'].startswith('@'):
                        fixed_properties[prop_name] = service_suggestion['reference']
                        warnings.append(
                            f"Added missing required service reference '{prop_name}': "
                            f"'{service_suggestion['reference']}' (found: {service_suggestion['message']})"
                        )
                    else:
                        # This is a @ServiceName suggestion - just warn, don't add unresolvable reference
                        warnings.append(
                            f"Missing required service reference '{prop_name}' - "
                            f"{service_suggestion['message']} to resolve this dependency"
                        )
                else:
                    warnings.append(
                        f"Missing required service reference '{prop_name}' - "
                        f"create appropriate controller service and reference it"
                    )
                continue
        
        # Validate existing service references (if property has a value)
        if current_value and prop_schema.get('type') == 'controller_service_reference':
            resolved_value, is_resolved = await _resolve_service_reference(
                current_value, service_map, process_group_id, nifi_client, 
                logger, user_request_id, action_id
            )
            if is_resolved and resolved_value != current_value:
                fixed_properties[prop_name] = resolved_value
                warnings.append(f"Resolved service reference '{prop_name}': '{current_value}' → '{resolved_value}'")
            elif not is_resolved:
                errors.append(f"Could not resolve service reference '{prop_name}': '{current_value}'")
    
    # Phase 2A: Enhanced Expression Language validation for all properties
    for prop_name, prop_value in properties.items():
        if not isinstance(prop_value, str):
            continue
            
        # Skip if already processed in schema
        if prop_name in schema:
            continue
            
        # Handle service references in dynamic properties
        if _looks_like_service_reference(prop_value):
            resolved_value, is_resolved = await _resolve_service_reference(
                prop_value, service_map, process_group_id, nifi_client,
                logger, user_request_id, action_id
            )
            if is_resolved and resolved_value != prop_value:
                fixed_properties[prop_name] = resolved_value
                warnings.append(f"Resolved dynamic service reference '{prop_name}': '{prop_value}' → '{resolved_value}'")
            continue
        
        # Phase 2A: Enhanced Expression Language validation and auto-correction
        should_validate_el = False
        
        # Check if this property should have EL validation
        if isinstance(el_properties, set):
            should_validate_el = prop_name in el_properties
        elif isinstance(el_properties, dict) and el_properties.get('dynamic_properties_use_el'):
            # For processors like RouteOnAttribute where dynamic properties use EL
            should_validate_el = prop_name not in ['Routing Strategy']  # Skip known non-EL properties
        
        # Also validate if the property contains EL syntax
        if '${' in prop_value:
            should_validate_el = True
        
        if should_validate_el:
            try:
                corrected_value, el_warnings, el_errors = await _validate_and_auto_correct_expression_language(
                    prop_value, processor_type
                )
                
                if corrected_value != prop_value:
                    fixed_properties[prop_name] = corrected_value
                    logger.info(f"EL auto-correction: {prop_name} = {prop_value} → {corrected_value}")
                
                # Add EL-specific warnings and errors
                for warning in el_warnings:
                    warnings.append(f"EL in '{prop_name}': {warning}")
                
                for error in el_errors:
                    errors.append(f"EL error in '{prop_name}': {error}")
                    
            except Exception as e:
                logger.warning(f"EL validation failed for property '{prop_name}': {e}")
                errors.append(f"EL validation failed for '{prop_name}': {e}")
    
    return fixed_properties, warnings, errors


def _looks_like_service_reference(value: str) -> bool:
    """Check if a property value looks like a service reference."""
    if not isinstance(value, str):
        return False
    # @ServiceName pattern or UUID pattern
    return value.startswith("@") or _is_valid_uuid(value)


def _validate_nifi_expression_language(expression: str) -> list[str]:
    """
    Phase 2A: Enhanced NiFi Expression Language validation.
    
    Validates NiFi EL syntax and provides auto-correction suggestions.
    Returns list of error messages if validation fails.
    """
    errors = []
    
    if not isinstance(expression, str):
        return errors
    
    # Skip validation if no EL expressions present
    if '${' not in expression:
        return errors
    
    # 1. Basic bracket matching
    if expression.count('${') != expression.count('}'):
        errors.append("Mismatched ${} brackets in expression")
        return errors  # Can't continue validation with broken brackets
    
    # 2. Extract all EL expressions for individual validation
    import re
    el_expressions = re.findall(r'\$\{([^}]+)\}', expression)
    
    for el_expr in el_expressions:
        el_expr = el_expr.strip()
        
        # 3. Function validation and auto-correction
        function_errors = _validate_el_functions(el_expr)
        errors.extend(function_errors)
        
        # 4. Attribute reference validation
        attr_errors = _validate_el_attributes(el_expr)
        errors.extend(attr_errors)
        
        # 5. Operator validation
        operator_errors = _validate_el_operators(el_expr)
        errors.extend(operator_errors)
        
        # 6. Nested expression validation
        if '${' in el_expr:
            errors.append(f"Nested expressions not supported in NiFi EL: {el_expr}")
    
    return errors


def _validate_el_functions(expression: str) -> list[str]:
    """Validate NiFi EL functions and suggest corrections."""
    errors = []
    
    # Common function mistakes and their corrections
    FUNCTION_CORRECTIONS = {
        'exists': 'notNull',
        'ne': 'notEquals', 
        'eq': 'equals',
        'len': 'length',
        'substr': 'substring',
        'lower': 'toLower',
        'upper': 'toUpper',
        'trim_whitespace': 'trim',
        'replace_all': 'replaceAll',
        'split_on': 'split'
    }
    
    # Check for incorrect functions
    for wrong_func, correct_func in FUNCTION_CORRECTIONS.items():
        if f':{wrong_func}(' in expression:
            errors.append(f"Function '{wrong_func}()' not available in NiFi EL. Use '{correct_func}()' instead.")
    
    # Check for missing colons before functions
    import re
    function_pattern = r'\b(notNull|equals|notEquals|length|substring|toLower|toUpper|trim|replaceAll|split|and|or|not|contains|startsWith|endsWith|matches)\s*\('
    matches = re.findall(function_pattern, expression)
    for func in matches:
        if f':{func}(' not in expression:
            errors.append(f"NiFi EL functions require ':' prefix. Use ':{func}(' instead of '{func}('.")
    
    return errors


def _validate_el_attributes(expression: str) -> list[str]:
    """Validate NiFi EL attribute references."""
    errors = []
    
    # Common attribute mistakes
    ATTRIBUTE_CORRECTIONS = {
        'file_name': 'filename',
        'file_size': 'file.size',
        'file_path': 'absolute.path',
        'mime_type': 'mime.type',
        'timestamp': 'entryDate'
    }
    
    # Check for incorrect attribute names
    for wrong_attr, correct_attr in ATTRIBUTE_CORRECTIONS.items():
        if wrong_attr in expression:
            errors.append(f"Attribute '{wrong_attr}' should likely be '{correct_attr}' in NiFi.")
    
    # Check for common flowfile attribute patterns
    if 'flowfile.' in expression.lower():
        errors.append("Use attribute names directly, not 'flowfile.attribute'. Example: use 'filename' not 'flowfile.filename'.")
    
    return errors


def _validate_el_operators(expression: str) -> list[str]:
    """Validate NiFi EL operators and logical constructs."""
    errors = []
    
    # Check for incorrect logical operators
    if ' and ' in expression and ':and(' not in expression:
        errors.append("Use ':and()' function instead of ' and ' operator in NiFi EL.")
    
    if ' or ' in expression and ':or(' not in expression:
        errors.append("Use ':or()' function instead of ' or ' operator in NiFi EL.")
    
    if ' not ' in expression and ':not(' not in expression:
        errors.append("Use ':not()' function instead of ' not ' operator in NiFi EL.")
    
    # Check for comparison operators (these are valid)
    comparison_ops = ['==', '!=', '<', '>', '<=', '>=']
    # These are fine, no errors needed
    
    return errors


async def _validate_and_auto_correct_expression_language(
    expression: str,
    processor_type: str = None
) -> tuple[str, list[str], list[str]]:
    """
    Phase 2A: Auto-correct common NiFi EL mistakes.
    
    Returns:
        tuple[corrected_expression, warnings, errors]: (fixed expression, warning messages, error messages)
    """
    if not isinstance(expression, str) or '${' not in expression:
        return expression, [], []
    
    corrected_expression = expression
    warnings = []
    errors = []
    
    # Auto-corrections for common mistakes (NON-LOGICAL OPERATORS ONLY)
    SIMPLE_CORRECTIONS = {
        # Function corrections
        ':exists()': ':notNull()',
        ':ne(': ':notEquals(',
        ':eq(': ':equals(',
        ':len()': ':length()',
        ':substr(': ':substring(',
        ':lower()': ':toLower()',
        ':upper()': ':toUpper()',
        
        # Attribute corrections
        'file_name': 'filename',
        'file_size': 'file.size',
        'file_path': 'absolute.path',
        'mime_type': 'mime.type'
    }
    
    # Apply simple corrections first
    for wrong_pattern, correct_pattern in SIMPLE_CORRECTIONS.items():
        if wrong_pattern in corrected_expression:
            corrected_expression = corrected_expression.replace(wrong_pattern, correct_pattern)
            warnings.append(f"Auto-corrected EL: '{wrong_pattern}' → '{correct_pattern}'")
    
    # CRITICAL FIX: Proper logical operator handling
    corrected_expression, logical_warnings = _fix_logical_operators_in_el(corrected_expression)
    warnings.extend(logical_warnings)
    
    # Final validation of corrected expression
    final_errors = _validate_nifi_expression_language(corrected_expression)
    errors.extend(final_errors)
    
    return corrected_expression, warnings, errors


def _fix_logical_operators_in_el(expression: str) -> tuple[str, list[str]]:
    """
    Fix logical operators in NiFi EL expressions with proper parentheses balancing.
    
    Handles patterns like:
    - "A and B and C" → "A:and(B):and(C)" 
    - "A or B" → "A:or(B)"
    - "not A" → "A:not()"
    
    Returns:
        tuple[corrected_expression, warnings]
    """
    warnings = []
    
    # Skip if no logical operators present
    if not any(op in expression for op in [' and ', ' or ', ' not ']):
        return expression, warnings
    
    # Extract the EL content (remove ${ and })
    import re
    el_match = re.search(r'\$\{(.+)\}', expression)
    if not el_match:
        return expression, warnings
    
    el_content = el_match.group(1)
    original_el_content = el_content
    
    # Handle logical operators with proper NiFi EL syntax
    try:
        # Fix 'and' operators: "A and B and C" → "A:and(B):and(C)"
        if ' and ' in el_content:
            # Split by ' and ' and rebuild with proper NiFi syntax
            parts = el_content.split(' and ')
            if len(parts) > 1:
                # Build nested :and() structure: A:and(B):and(C)
                corrected_parts = parts[0].strip()  # Start with first part
                for i in range(1, len(parts)):
                    part = parts[i].strip()
                    corrected_parts = f"{corrected_parts}:and({part})"
                
                el_content = corrected_parts
                warnings.append(f"Auto-corrected EL logical operators: ' and ' → ':and()'")
        
        # Fix 'or' operators: "A or B or C" → "A:or(B):or(C)"
        if ' or ' in el_content:
            parts = el_content.split(' or ')
            if len(parts) > 1:
                corrected_parts = parts[0].strip()
                for i in range(1, len(parts)):
                    part = parts[i].strip()
                    corrected_parts = f"{corrected_parts}:or({part})"
                
                el_content = corrected_parts
                warnings.append(f"Auto-corrected EL logical operators: ' or ' → ':or()'")
        
        # Fix 'not' operators: "not A" → "A:not()"
        # This is more complex as 'not' is a prefix operator in normal logic
        # but a suffix function in NiFi EL
        if ' not ' in el_content:
            # Simple case: "not A" → "A:not()"
            el_content = re.sub(r'\bnot\s+([^)]+)', r'\1:not()', el_content)
            warnings.append(f"Auto-corrected EL logical operators: ' not ' → ':not()'")
        
        # Reconstruct the full expression
        if el_content != original_el_content:
            corrected_expression = f"${{{el_content}}}"
            return corrected_expression, warnings
    
    except Exception as e:
        warnings.append(f"EL logical operator correction failed: {e}")
        return expression, warnings
    
    return expression, warnings


def _get_processor_el_properties(processor_type: str) -> set[str]:
    """
    Get property names that commonly contain Expression Language for specific processor types.
    
    This helps us focus EL validation on properties that are likely to contain expressions.
    """
    EL_PROPERTY_PATTERNS = {
        'org.apache.nifi.processors.standard.RouteOnAttribute': {
            # All dynamic properties (routing rules) use EL
            'dynamic_properties_use_el': True
        },
        'org.apache.nifi.processors.standard.UpdateAttribute': {
            # All dynamic properties (attribute updates) use EL
            'dynamic_properties_use_el': True
        },
        'org.apache.nifi.processors.standard.ReplaceText': {
            'Replacement Value', 'Regular Expression'
        },
        'org.apache.nifi.processors.standard.EvaluateJsonPath': {
            # Dynamic properties (JSON path expressions)
            'dynamic_properties_use_el': True
        },
        'org.apache.nifi.processors.standard.InvokeHTTP': {
            'Remote URL', 'HTTP Headers'
        },
        'org.apache.nifi.processors.standard.PutFile': {
            'Directory', 'Conflict Resolution Strategy'
        },
        'org.apache.nifi.processors.standard.GetFile': {
            'Directory', 'File Filter'
        },
        'org.apache.nifi.processors.script.ExecuteScript': {
            # Script properties may contain EL
            'Script Body'
        }
    }
    
    return EL_PROPERTY_PATTERNS.get(processor_type, set())


def _get_processor_valid_properties(processor_type: str) -> set[str]:
    """
    Get the set of valid properties for a processor type to catch invalid properties.
    
    Phase 2B: Enhanced Property Validation - catches common invalid properties
    """
    valid_properties_by_type = {
        "org.apache.nifi.processors.standard.HandleHttpRequest": {
            "Listening Port", "Hostname", "SSL Context Service", "HTTP Protocols",
            "HTTP Context Map", "Allowed Paths", "Default URL Character Set",
            "Allow GET", "Allow POST", "Allow PUT", "Allow DELETE", "Allow HEAD", "Allow OPTIONS",
            "Maximum Threads", "Additional HTTP Methods", "Client Authentication",
            "container-queue-size", "multipart-request-max-size", "multipart-read-buffer-size",
            "parameters-to-attributes"
            # Note: "Base Path" is NOT a valid property for HandleHttpRequest
        },
        "org.apache.nifi.processors.standard.HandleHttpResponse": {
            "HTTP Status Code", "HTTP Context Map", "Attributes to add to the HTTP Response (Regex)"
        },
        "org.apache.nifi.processors.script.ExecuteScript": {
            "Script Engine", "Script File", "Script Body", "Module Directory"
        }
    }
    
    return valid_properties_by_type.get(processor_type, set())


async def _validate_processor_properties_phase2b(
    processor_type: str,
    properties: Dict[str, Any],
    logger
) -> tuple[Dict[str, Any], list[str]]:
    """
    Phase 2B: Enhanced property validation to catch invalid properties before creation.
    
    CRITICAL FIX: Smart handling of service references in invalid property names.
    Instead of deleting invalid properties with service references, tries to map them to correct names.
    EFFICIENCY FIX: Proactive script validation to catch common Groovy scripting issues.
    
    Returns:
        tuple[validated_properties, warnings]
    """
    warnings = []
    validated_properties = properties.copy()
    
    # EFFICIENCY FIX: Run script-specific validation first
    script_warnings = _validate_script_processor_properties(processor_type, validated_properties)
    warnings.extend(script_warnings)
    
    # Get valid properties for this processor type
    valid_properties = _get_processor_valid_properties(processor_type)
    
    if valid_properties:  # Only validate if we have a known property set
        # CRITICAL FIX: Smart property name mapping for common mistakes
        property_name_mappings = _get_processor_property_name_mappings(processor_type)
        
        for prop_name in list(validated_properties.keys()):
            if prop_name not in valid_properties:
                prop_value = validated_properties[prop_name]
                
                # CRITICAL FIX: Check if this invalid property name has a known mapping
                if prop_name in property_name_mappings:
                    correct_name = property_name_mappings[prop_name]
                    validated_properties[correct_name] = prop_value
                    del validated_properties[prop_name]
                    warnings.append(f"Mapped invalid property name '{prop_name}' → '{correct_name}' (preserving value: {prop_value})")
                    logger.info(f"Property name mapping: {prop_name} → {correct_name}")
                    
                # CRITICAL FIX: If property contains service reference, don't delete blindly
                elif isinstance(prop_value, str) and _looks_like_service_reference(prop_value):
                    # Try to find a similar valid property name for service references
                    similar_prop = _find_similar_service_property(prop_name, valid_properties)
                    if similar_prop:
                        validated_properties[similar_prop] = prop_value
                        del validated_properties[prop_name]
                        warnings.append(f"Mapped invalid service reference property '{prop_name}' → '{similar_prop}' (preserving reference: {prop_value})")
                        logger.info(f"Service reference property mapping: {prop_name} → {similar_prop}")
                    else:
                        # No mapping found, remove but note it contained a service reference
                        logger.warning(f"Invalid property '{prop_name}' with service reference '{prop_value}' detected for {processor_type}")
                        warnings.append(f"Removed invalid property '{prop_name}' (not supported by {processor_type}) - contained service reference: {prop_value}")
                        del validated_properties[prop_name]
                else:
                    # Regular invalid property without service reference - remove as before
                    logger.warning(f"Invalid property '{prop_name}' detected for {processor_type}")
                    warnings.append(f"Removed invalid property '{prop_name}' (not supported by {processor_type})")
                    del validated_properties[prop_name]
    
    return validated_properties, warnings


def _get_processor_property_name_mappings(processor_type: str) -> Dict[str, str]:
    """
    Get mappings from common invalid property names to correct ones for specific processor types.
    
    This handles common LLM mistakes like "Context Map" → "HTTP Context Map".
    """
    mappings_by_type = {
        "org.apache.nifi.processors.standard.HandleHttpRequest": {
            "Context Map": "HTTP Context Map",
            "Base Path": "Allowed Paths",  # Note: Base Path is not actually supported, but this preserves the intent
        },
        "org.apache.nifi.processors.standard.HandleHttpResponse": {
            "Context Map": "HTTP Context Map",
            "Response Code": "HTTP Status Code",
            "Mime Type": "HTTP Status Code",  # Fallback - Mime Type isn't supported
        }
    }
    
    return mappings_by_type.get(processor_type, {})


def _find_similar_service_property(invalid_name: str, valid_properties: set[str]) -> str | None:
    """
    Find a valid property name that might be intended for service references.
    
    Looks for properties containing similar words (case-insensitive).
    """
    invalid_lower = invalid_name.lower()
    
    # Look for properties that contain key words from the invalid name
    key_words = ["context", "map", "service", "reader", "writer"]
    
    for word in key_words:
        if word in invalid_lower:
            for valid_prop in valid_properties:
                if word in valid_prop.lower():
                    return valid_prop
    
    return None


def _validate_script_processor_properties(processor_type: str, properties: Dict[str, Any]) -> List[str]:
    """Enhanced validation for script processors to catch common issues."""
    warnings = []
    
    if processor_type == "org.apache.nifi.processors.script.ExecuteScript":
        # Check for empty string vs None for optional properties
        script_file = properties.get("Script File", None)
        module_dir = properties.get("Module Directory", None)
        script_body = properties.get("Script Body", None)
        
        # Empty strings cause validation issues - should be None
        if script_file == "":
            properties["Script File"] = None
            warnings.append("Converted empty 'Script File' to None to avoid validation errors")
            
        if module_dir == "":
            properties["Module Directory"] = None  
            warnings.append("Converted empty 'Module Directory' to None to avoid validation errors")
        
        # Validate Groovy script common issues
        if script_body and properties.get("Script Engine") == "Groovy":
            script_warnings = _validate_groovy_script(script_body)
            warnings.extend(script_warnings)
    
    return warnings


def _validate_groovy_script(script_body: str) -> List[str]:
    """Validate Groovy script for common NiFi scripting issues."""
    warnings = []
    
    # Check for flowFile variable usage patterns that cause scope issues
    has_flowfile_usage = False
    has_session_get = "session.get()" in script_body
    
    # Look for flowFile usage in various patterns
    import re
    flowfile_patterns = [
        r'\bflowFile\b',  # Direct flowFile usage
        r'session\.read\(\s*flowFile',  # session.read(flowFile, ...)
        r'session\.write\(\s*flowFile',  # session.write(flowFile, ...)
        r'def\s+\w+\s*=\s*flowFile'  # def something = flowFile
    ]
    
    for pattern in flowfile_patterns:
        if re.search(pattern, script_body):
            has_flowfile_usage = True
            break
    
    if has_flowfile_usage and not has_session_get:
        warnings.append("Script uses 'flowFile' variable but doesn't call 'session.get()' - this may cause MissingPropertyException")
    
    # Check for proper FlowFile import
    if "FlowFile" in script_body and "import org.apache.nifi.flowfile.FlowFile" not in script_body:
        warnings.append("Script references FlowFile but missing import: 'import org.apache.nifi.flowfile.FlowFile'")
    
    # Check for variable reassignment patterns
    if "ff = flowFile" in script_body:
        warnings.append("Script uses 'ff = flowFile' pattern - ensure 'flowFile' is properly initialized from session.get()")
    
    # Check for proper session callback patterns
    if "session.write" in script_body and "OutputStreamCallback" not in script_body:
        warnings.append("Script uses session.write but may be missing OutputStreamCallback import")
        
    return warnings


async def _rollback_created_objects(
    created_objects: List[Dict[str, Any]],
    nifi_client: NiFiClient,
    logger,
    user_request_id: str,
    action_id: str
) -> Dict[str, Any]:
    """
    Phase 1B: Atomic Operations Rollback
    
    Rolls back all created objects in reverse order (LIFO) to clean up
    partial flow creation attempts.
    
    Args:
        created_objects: List of objects to delete (in creation order)
        nifi_client: NiFi client for API calls
        logger: Logger instance
        user_request_id: Request ID for logging
        action_id: Action ID for logging
        
    Returns:
        Rollback summary with success/failure counts
    """
    if not created_objects:
        return {"status": "success", "message": "No objects to rollback"}
    
    logger.info(f"Starting rollback of {len(created_objects)} objects...")
    
    rollback_results = []
    success_count = 0
    error_count = 0
    
    # Process in reverse order (LIFO - Last In, First Out)
    for obj in reversed(created_objects):
        obj_type = obj.get("type")
        obj_id = obj.get("id") 
        obj_name = obj.get("name", "unnamed")
        is_existing = obj.get("existing", False)
        
        # CRITICAL FIX: Skip deletion of existing objects
        if is_existing:
            logger.debug(f"Skipping rollback of existing {obj_type} '{obj_name}' ({obj_id})")
            rollback_results.append({
                "type": obj_type,
                "id": obj_id,
                "name": obj_name,
                "status": "skipped",
                "message": f"Skipped deletion of existing {obj_type} {obj_name}"
            })
            continue
        
        try:
            logger.debug(f"Rolling back {obj_type} '{obj_name}' ({obj_id})")
            
            if obj_type == "connection":
                # Connections must be stopped before deletion
                # They are typically auto-stopped when processors are stopped
                await nifi_client.delete_connection(
                    connection_id=obj_id,
                    user_request_id=user_request_id,
                    action_id=action_id
                )
                rollback_results.append({
                    "type": obj_type,
                    "id": obj_id, 
                    "name": obj_name,
                    "status": "success",
                    "message": f"Deleted connection {obj_name}"
                })
                success_count += 1
                
            elif obj_type == "processor":
                # Stop processor first (if running), then delete
                try:
                    await nifi_client.stop_processor(
                        processor_id=obj_id,
                        user_request_id=user_request_id,
                        action_id=action_id
                    )
                except Exception:
                    pass  # Processor might already be stopped
                
                await nifi_client.delete_processor(
                    processor_id=obj_id,
                    user_request_id=user_request_id,
                    action_id=action_id
                )
                rollback_results.append({
                    "type": obj_type,
                    "id": obj_id,
                    "name": obj_name, 
                    "status": "success",
                    "message": f"Deleted processor {obj_name}"
                })
                success_count += 1
                
            elif obj_type == "controller_service":
                # Disable service first (if enabled), then delete
                try:
                    await nifi_client.disable_controller_service(
                        service_id=obj_id,
                        user_request_id=user_request_id,
                        action_id=action_id
                    )
                except Exception:
                    pass  # Service might already be disabled
                
                await nifi_client.delete_controller_service(
                    service_id=obj_id,
                    user_request_id=user_request_id,
                    action_id=action_id
                )
                rollback_results.append({
                    "type": obj_type,
                    "id": obj_id,
                    "name": obj_name,
                    "status": "success", 
                    "message": f"Deleted controller service {obj_name}"
                })
                success_count += 1
                
            elif obj_type == "process_group":
                # Delete process group (will delete all contained objects)
                await nifi_client.delete_process_group(
                    process_group_id=obj_id,
                    user_request_id=user_request_id,
                    action_id=action_id
                )
                rollback_results.append({
                    "type": obj_type,
                    "id": obj_id,
                    "name": obj_name,
                    "status": "success",
                    "message": f"Deleted process group {obj_name}"
                })
                success_count += 1
                
            else:
                logger.warning(f"Unknown object type for rollback: {obj_type}")
                rollback_results.append({
                    "type": obj_type,
                    "id": obj_id,
                    "name": obj_name,
                    "status": "skipped",
                    "message": f"Unknown object type: {obj_type}"
                })
                
        except Exception as e:
            logger.warning(f"Failed to rollback {obj_type} '{obj_name}': {e}")
            rollback_results.append({
                "type": obj_type,
                "id": obj_id,
                "name": obj_name,
                "status": "error",
                "message": f"Rollback failed: {e}"
            })
            error_count += 1
    
    # Summary
    total_objects = len(created_objects)
    overall_status = "success" if error_count == 0 else "partial"
    
    summary = {
        "status": overall_status,
        "message": f"Rollback completed: {success_count}/{total_objects} objects deleted successfully",
        "summary": {
            "total_objects": total_objects,
            "successful_deletions": success_count,
            "failed_deletions": error_count
        },
        "detailed_results": rollback_results
    }
    
    logger.info(f"Rollback summary: {success_count} success, {error_count} errors")
    return summary


async def _analyze_and_auto_terminate_relationships(
    nifi_objects: List[Dict[str, Any]],
    id_map: Dict[str, str],
    nifi_client: NiFiClient,
    logger,
    user_request_id: str,
    action_id: str
) -> List[Dict[str, Any]]:
    """
    Phase 2B Preview: Intelligent Relationship Auto-Termination
    
    Analyzes the planned connections and determines which processor relationships
    should be auto-terminated to prevent validation errors.
    
    Logic:
    1. Get all processor relationships from NiFi
    2. Determine which relationships will be used by planned connections
    3. Auto-terminate relationships that won't be used
    4. Handle common processor patterns (e.g., HandleHttpRequest/Response pairs)
    
    Args:
        nifi_objects: List of all objects being created (includes connections)
        id_map: Mapping of component names to their created IDs
        nifi_client: NiFi client for API calls
        logger: Logger instance
        user_request_id: Request ID for logging
        action_id: Action ID for logging
        
    Returns:
        List of relationship update instructions
    """
    relationship_updates = []
    
    try:
        # Extract planned connections from the flow definition
        planned_connections = [obj for obj in nifi_objects if obj.get("type") == "connection"]
        
        # Build a map of processor name -> relationships that will be used
        used_relationships = {}
        for conn_def in planned_connections:
            source_name = conn_def.get("source")
            relationships = conn_def.get("relationships", [])
            
            if source_name and relationships:
                if source_name not in used_relationships:
                    used_relationships[source_name] = set()
                used_relationships[source_name].update(relationships)
        
        logger.debug(f"Used relationships map: {used_relationships}")
        
        # For each created processor, determine auto-termination strategy
        for processor_name, processor_id in id_map.items():
            logger.debug(f"Analyzing relationships for processor '{processor_name}' ({processor_id})")
            
            try:
                # Get processor details from NiFi to see available relationships
                processor_details = await nifi_client.get_processor(processor_id)
                processor_component = processor_details.get("component", {})
                available_relationships = processor_component.get("relationships", [])
                processor_type = processor_component.get("type", "")
                
                if not available_relationships:
                    logger.debug(f"No relationships found for processor '{processor_name}'")
                    continue
                
                # Get relationships that will be used by connections
                used_rels_for_processor = used_relationships.get(processor_name, set())
                
                # Determine which relationships to auto-terminate
                relationships_to_terminate = []
                
                for rel in available_relationships:
                    rel_name = rel.get("name")
                    if not rel_name:
                        continue
                    
                    # Skip relationships that will be used by connections
                    if rel_name in used_rels_for_processor:
                        logger.debug(f"Keeping relationship '{rel_name}' for processor '{processor_name}' (used by connection)")
                        continue
                    
                    # Apply processor-specific auto-termination rules
                    should_auto_terminate = _should_auto_terminate_relationship(
                        processor_type=processor_type,
                        relationship_name=rel_name,
                        processor_name=processor_name,
                        planned_connections=planned_connections
                    )
                    
                    if should_auto_terminate:
                        relationships_to_terminate.append(rel_name)
                        logger.debug(f"Auto-terminating relationship '{rel_name}' for processor '{processor_name}'")
                
                # Add to update list if we have relationships to terminate
                if relationships_to_terminate:
                    relationship_updates.append({
                        "processor_id": processor_id,
                        "processor_name": processor_name,
                        "processor_type": processor_type,
                        "relationships_to_terminate": relationships_to_terminate,
                        "used_relationships": list(used_rels_for_processor)
                    })
                    
            except Exception as e:
                logger.warning(f"Failed to analyze relationships for processor '{processor_name}': {e}")
                continue
        
        logger.info(f"Relationship analysis complete. {len(relationship_updates)} processors need relationship updates.")
        return relationship_updates
        
    except Exception as e:
        logger.error(f"Error during relationship analysis: {e}")
        return []


def _should_auto_terminate_relationship(
    processor_type: str,
    relationship_name: str,
    processor_name: str,
    planned_connections: List[Dict[str, Any]]
) -> bool:
    """
    Determine if a specific relationship should be auto-terminated based on processor type and flow context.
    
    Args:
        processor_type: The full processor class name
        relationship_name: The name of the relationship to evaluate
        processor_name: The name of the processor instance
        planned_connections: List of all planned connections in the flow
        
    Returns:
        True if the relationship should be auto-terminated, False otherwise
    """
    
    # Processor-specific auto-termination rules
    AUTO_TERMINATE_RULES = {
        # Standard processors that commonly need auto-termination
        "org.apache.nifi.processors.standard.LogAttribute": {
            "success": True  # LogAttribute success is usually auto-terminated
        },
        "org.apache.nifi.processors.standard.EvaluateJsonPath": {
            "failure": True,   # Usually auto-terminate failure
            "unmatched": True  # Usually auto-terminate unmatched
        },
        "org.apache.nifi.processors.script.ExecuteScript": {
            "failure": True,   # Usually auto-terminate script failures  
            "success": True    # FIXED: Auto-terminate success when not connected
        },
        "org.apache.nifi.processors.standard.HandleHttpResponse": {
            "success": True,  # HTTP responses usually auto-terminate success
            "failure": True   # and failure
        },
        "org.apache.nifi.processors.standard.HandleHttpRequest": {
            # FIXED: Handle HTTP request relationships more intelligently
            # Note: success/failure will be handled by special logic below
        },
        "org.apache.nifi.processors.standard.UpdateAttribute": {
            "success": True   # UpdateAttribute success usually auto-terminated
        }
    }
    
    # Check processor-specific rules
    processor_rules = AUTO_TERMINATE_RULES.get(processor_type, {})
    if relationship_name in processor_rules:
        return processor_rules[relationship_name]
    
    # General rules for common relationship names
    GENERAL_AUTO_TERMINATE_RELATIONSHIPS = {
        "failure",     # Failure relationships often auto-terminated
        "original",    # Original relationships often auto-terminated
        "retry",       # Retry relationships often auto-terminated
    }
    
    if relationship_name in GENERAL_AUTO_TERMINATE_RELATIONSHIPS:
        return True
    
    # ENHANCED: Special case handling for HTTP processors
    if "HandleHttpRequest" in processor_type or "HandleHttpResponse" in processor_type:
        # For HTTP processors, check if the relationship is actually being used
        for conn in planned_connections:
            if conn.get("source") == processor_name and relationship_name in conn.get("relationships", []):
                # This relationship is being used by a connection - don't auto-terminate
                return False
        
        # If relationship is not being used, auto-terminate it
        # This fixes the gap where HTTP processor relationships weren't being auto-terminated
        return True
    
    # ENHANCED: Default logic - auto-terminate unused relationships for most processors
    # This is less conservative than before and matches NiFi's validation expectations
    
    # Exception: Never auto-terminate these critical relationships without explicit rules
    CRITICAL_RELATIONSHIPS = {
        "success",     # Only auto-terminate success if processor-specific rule allows it
        "original"     # Keep original unless specifically configured
    }
    
    # For critical relationships, only auto-terminate if there's an explicit rule
    if relationship_name in CRITICAL_RELATIONSHIPS:
        return False
    
    # For all other relationships, auto-terminate if unused (less conservative default)
    return True


def _suggest_missing_service_reference(
    processor_type: str, 
    property_name: str, 
    process_group_id: str, 
    service_map: Dict[str, str]
) -> Optional[Dict[str, str]]:
    """
    Suggest appropriate service references for missing required services.
    
    This function provides intelligent suggestions when a processor requires
    a service reference but none was provided in the flow definition.
    
    Returns:
        dict with 'reference' and 'message' fields, or None if no suggestion available
    """
    # Service type suggestions based on processor type and property name
    SERVICE_SUGGESTIONS = {
        'org.apache.nifi.processors.standard.HandleHttpRequest': {
            'HTTP Context Map': {
                'service_type': 'org.apache.nifi.http.StandardHttpContextMap',
                'suggested_name': 'HttpContextMap'
            }
        },
        'org.apache.nifi.processors.standard.HandleHttpResponse': {
            'HTTP Context Map': {
                'service_type': 'org.apache.nifi.http.StandardHttpContextMap', 
                'suggested_name': 'HttpContextMap'
            }
        }
    }
    
    processor_suggestions = SERVICE_SUGGESTIONS.get(processor_type, {})
    property_suggestion = processor_suggestions.get(property_name)
    
    if not property_suggestion:
        return None
    
    suggested_name = property_suggestion['suggested_name']
    
    # Check if a service with the suggested name already exists in service_map
    if suggested_name in service_map:
        return {
            'reference': service_map[suggested_name],
            'message': f"Found existing service '{suggested_name}' in current flow"
        }
    
    # Check for similar service names in service_map (case-insensitive fuzzy match)
    for service_name, service_id in service_map.items():
        if suggested_name.lower() in service_name.lower() or service_name.lower() in suggested_name.lower():
            return {
                'reference': service_id,
                'message': f"Found similar service '{service_name}' in current flow"
            }
    
    # No existing service found - suggest creating one
    return {
        'reference': f"@{suggested_name}",
        'message': f"Create {property_suggestion['service_type']} named '{suggested_name}'"
    }


# ✅ FIX 1: Type Discovery and Validation Functions

async def _validate_processor_type_exists(processor_type: str, nifi_client: 'NiFiClient', user_request_id: str, action_id: str) -> bool:
    """
    Validate that a processor type exists in the NiFi instance.
    
    Args:
        processor_type: The processor type to validate
        nifi_client: NiFi client instance
        user_request_id: Request ID for logging
        action_id: Action ID for logging
        
    Returns:
        True if processor type exists, False otherwise
    """
    try:
        # Call NiFi client directly instead of using helper function that requires MCP context
        all_types = await nifi_client.get_processor_types()
        
        # Check if the exact type exists
        for proc_type in all_types:
            if proc_type.get("type") == processor_type:
                return True
        
        return False
    except Exception:
        return False


async def _validate_controller_service_type_exists(service_type: str, nifi_client: 'NiFiClient', user_request_id: str, action_id: str) -> bool:
    """
    Validate that a controller service type exists in the NiFi instance.
    
    Args:
        service_type: The service type to validate
        nifi_client: NiFi client instance
        user_request_id: Request ID for logging
        action_id: Action ID for logging
        
    Returns:
        True if service type exists, False otherwise
    """
    try:
        # Call NiFi client directly instead of using helper function that requires MCP context
        all_types = await nifi_client.get_controller_service_types(user_request_id=user_request_id, action_id=action_id)
        
        # Check if the exact type exists
        for service_type_info in all_types:
            if service_type_info.get("type") == service_type:
                return True
        
        return False
    except Exception:
        return False


async def _get_processor_type_suggestions(processor_name: str, nifi_client: 'NiFiClient', max_suggestions: int = 5) -> List[Dict[str, Any]]:
    """
    Get processor type suggestions based on processor name.
    
    Args:
        processor_name: The processor name to find suggestions for
        nifi_client: NiFi client instance
        max_suggestions: Maximum number of suggestions to return
        
    Returns:
        List of processor type suggestions with type and description
    """
    try:
        # Call NiFi client directly to get all processor types
        all_types = await nifi_client.get_processor_types()
        
        # Search for matches based on simple name
        matches = []
        search_name_lower = processor_name.lower()
        
        for proc_type in all_types:
            # Extract relevant fields for matching
            title = proc_type.get("title", "").lower()
            type_str = proc_type.get("type", "").lower()
            description = proc_type.get("description", "").lower()
            tags = [tag.lower() for tag in proc_type.get("tags", [])]
            
            # Check if the search term matches
            name_match_found = (
                search_name_lower in title or
                search_name_lower in type_str or
                search_name_lower in description or
                any(search_name_lower in tag for tag in tags)
            )
            
            if name_match_found:
                matches.append({
                    "type": proc_type.get("type"),
                    "description": proc_type.get("description", ""),
                    "tags": proc_type.get("tags", [])
                })
                
                if len(matches) >= max_suggestions:
                    break
        
        return matches
    except Exception:
        return []


async def _get_controller_service_type_suggestions(service_name: str, nifi_client: 'NiFiClient', user_request_id: str, action_id: str, max_suggestions: int = 5) -> List[Dict[str, Any]]:
    """
    Get controller service type suggestions based on service name.
    
    Args:
        service_name: The service name to find suggestions for
        nifi_client: NiFi client instance
        user_request_id: Request ID for logging
        action_id: Action ID for logging
        max_suggestions: Maximum number of suggestions to return
        
    Returns:
        List of service type suggestions with type and description
    """
    try:
        # Call NiFi client directly to get all service types
        all_types = await nifi_client.get_controller_service_types(user_request_id=user_request_id, action_id=action_id)
        
        # Search for matches based on simple name
        matches = []
        search_name_lower = service_name.lower()
        
        for service_type in all_types:
            # Extract relevant fields for matching
            title = service_type.get("title", "").lower()
            type_str = service_type.get("type", "").lower()
            description = service_type.get("description", "").lower()
            tags = [tag.lower() for tag in service_type.get("tags", [])]
            
            # Check if the search term matches
            name_match_found = (
                search_name_lower in title or
                search_name_lower in type_str or
                search_name_lower in description or
                any(search_name_lower in tag for tag in tags)
            )
            
            if name_match_found:
                matches.append({
                    "type": service_type.get("type"),
                    "description": service_type.get("description", ""),
                    "tags": service_type.get("tags", [])
                })
                
                if len(matches) >= max_suggestions:
                    break
        
        return matches
    except Exception:
        return []


async def _validate_and_suggest_types_upfront(nifi_objects: List[Dict[str, Any]], nifi_client: 'NiFiClient', logger, user_request_id: str, action_id: str) -> tuple[bool, Dict[str, Any]]:
    """
    Validate all processor and service types upfront and provide suggestions if invalid.
    
    Args:
        nifi_objects: List of objects to create
        nifi_client: NiFi client instance
        logger: Logger instance
        user_request_id: Request ID for logging
        action_id: Action ID for logging
        
    Returns:
        Tuple of (all_valid, error_response_if_invalid)
    """
    invalid_types = []
    suggestions = {}
    
    # Check processor types
    processors = [obj for obj in nifi_objects if obj.get("type") == "processor"]
    for processor in processors:
        processor_type = processor.get("processor_type")
        if not processor_type:
            invalid_types.append({
                "object_name": processor.get("name", "unnamed"),
                "object_type": "processor",
                "issue": "Missing 'processor_type' field"
            })
            continue
            
        is_valid = await _validate_processor_type_exists(processor_type, nifi_client, user_request_id, action_id)
        if not is_valid:
            # Extract simple name for suggestions
            simple_name = processor_type.split('.')[-1] if '.' in processor_type else processor_type
            type_suggestions = await _get_processor_type_suggestions(simple_name, nifi_client)
            
            invalid_types.append({
                "object_name": processor.get("name", "unnamed"),
                "object_type": "processor", 
                "invalid_type": processor_type,
                "issue": f"Processor type '{processor_type}' not found in NiFi instance"
            })
            suggestions[processor.get("name", "unnamed")] = type_suggestions
    
    # Check controller service types
    services = [obj for obj in nifi_objects if obj.get("type") == "controller_service"]
    for service in services:
        service_type = service.get("service_type")
        if not service_type:
            invalid_types.append({
                "object_name": service.get("name", "unnamed"),
                "object_type": "controller_service",
                "issue": "Missing 'service_type' field"
            })
            continue
            
        is_valid = await _validate_controller_service_type_exists(service_type, nifi_client, user_request_id, action_id)
        if not is_valid:
            # Extract simple name for suggestions
            simple_name = service_type.split('.')[-1] if '.' in service_type else service_type
            type_suggestions = await _get_controller_service_type_suggestions(simple_name, nifi_client, user_request_id, action_id)
            
            invalid_types.append({
                "object_name": service.get("name", "unnamed"),
                "object_type": "controller_service",
                "invalid_type": service_type,
                "issue": f"Controller service type '{service_type}' not found in NiFi instance"
            })
            suggestions[service.get("name", "unnamed")] = type_suggestions
    
    if invalid_types:
        # Create detailed results for test compatibility
        detailed_results = []
        for invalid_type in invalid_types:
            detailed_results.append({
                "status": "error",
                "message": invalid_type["issue"],
                "object_type": invalid_type["object_type"],
                "name": invalid_type["object_name"],
                "invalid_type": invalid_type.get("invalid_type"),
                "suggestions": suggestions.get(invalid_type["object_name"], [])
            })
        
        error_response = {
            "status": "error",
            "message": f"Invalid types found for {len(invalid_types)} objects. Use suggested types or discover valid types first.",
            "invalid_types": invalid_types,
            "type_suggestions": suggestions,
            "suggested_action": "Use 'lookup_nifi_processor_type' or 'get_controller_service_types' to discover valid types",
            "detailed_results": detailed_results,  # Add this for test compatibility
            "summary": {
                "objects_processed": 0,
                "controller_services_created": 0,
                "controller_services_enabled": 0,
                "processors_created": 0,
                "connections_created": 0,
                "total_errors": len(invalid_types),
                "total_warnings": 0
            }
        }
        return False, error_response
    
    return True, {}


# ✅ FIX 3: Enhanced EL Validation for RouteOnAttribute

def _validate_route_on_attribute_properties(properties: Dict[str, Any]) -> tuple[Dict[str, Any], List[str], List[str]]:
    """
    Enhanced validation specifically for RouteOnAttribute dynamic properties.
    
    Handles complex EL patterns and nested expressions that are not supported.
    
    Args:
        properties: The processor properties to validate
        
    Returns:
        Tuple of (validated_properties, warnings, errors)
    """
    validated_properties = properties.copy()
    warnings = []
    errors = []
    
    for prop_name, prop_value in properties.items():
        # Skip non-EL properties
        if prop_name in ["Routing Strategy"]:
            continue
            
        if not isinstance(prop_value, str) or not prop_value.startswith("${"):
            continue
            
        # ENHANCED: Detect and fix nested expressions
        if prop_value.count("${") > 1:
            warnings.append(f"Property '{prop_name}' has nested expressions which are not supported in NiFi EL")
            
            # AUTO-FIX: Try to flatten simple nested cases
            flattened_value = _flatten_route_on_attribute_expression(prop_value)
            if flattened_value != prop_value:
                validated_properties[prop_name] = flattened_value
                warnings.append(f"Auto-corrected nested EL in '{prop_name}': {prop_value} → {flattened_value}")
            else:
                errors.append(f"Cannot auto-correct complex nested EL in '{prop_name}': {prop_value}")
                continue
        
        # Validate the (possibly corrected) expression
        el_warnings = _validate_nifi_expression_language(validated_properties[prop_name])
        if el_warnings:
            warnings.extend([f"EL in '{prop_name}': {w}" for w in el_warnings])
    
    return validated_properties, warnings, errors


def _flatten_route_on_attribute_expression(expression: str) -> str:
    """
    Attempt to flatten nested expressions in RouteOnAttribute properties.
    
    Args:
        expression: The expression to flatten
        
    Returns:
        Flattened expression or original if flattening not possible
    """
    # Common pattern: ${attr1:ne(''):and(${attr2:ne('')}:and(${attr3:ne('')}))}
    # Should become: ${attr1:ne(''):and(attr2:ne('')):and(attr3:ne(''))}
    
    # Simple flattening: remove nested ${ } for attribute references
    import re
    
    # Pattern to match nested attribute references
    nested_pattern = r'\$\{([^}:]+):([^}]+)\}'
    
    # Extract all attribute references
    matches = re.findall(nested_pattern, expression)
    if not matches:
        return expression
    
    # Try to build a flattened expression
    try:
        # For simple cases like attribute existence checks
        if all(':ne(' in match[1] for match in matches):
            # Build flattened version
            conditions = []
            for attr_name, condition in matches:
                conditions.append(f"{attr_name}:{condition}")
            
            flattened = "${" + ":and(".join(conditions) + ")" + ")" * (len(conditions) - 1) + "}"
            return flattened
    except Exception:
        pass
    
    return expression


# ✅ FIX 4: Enhanced Property Schema Validation

async def _get_processor_property_schema(processor_type: str, nifi_client: 'NiFiClient', user_request_id: str, action_id: str) -> Dict[str, Any]:
    """
    Get the property schema for a processor type from NiFi.
    
    Args:
        processor_type: The processor type
        nifi_client: NiFi client instance
        user_request_id: Request ID for logging
        action_id: Action ID for logging
        
    Returns:
        Dictionary mapping property names to their schema information
    """
    # For now, return a basic schema based on known processors
    # In a full implementation, this would query NiFi's API for processor details
    
    basic_schemas = {
        "org.apache.nifi.processors.standard.RouteOnAttribute": {
            "Routing Strategy": {"required": True, "allowed_values": ["Route to Property name", "Route to 'matched' if all match"]},
            # Dynamic properties are validated separately
        },
        "org.apache.nifi.processors.standard.ReplaceText": {
            "Search Value": {"required": False, "deprecated": True, "replacement": "Regular Expression"},
            "Regular Expression": {"required": False},
            "Replacement Value": {"required": False},
            "Replacement Strategy": {"required": True, "allowed_values": ["Always Replace", "Replace Text", "Prepend", "Append", "Regex Replace", "Literal Replace"]},
            "Evaluation Mode": {"required": True, "allowed_values": ["Line-by-Line", "Entire text"]},
        },
        "org.apache.nifi.processors.script.ExecuteScript": {
            "Script Engine": {"required": True, "allowed_values": ["Clojure", "ECMAScript", "Groovy", "lua", "python", "ruby"]},
            "Script File": {"required": False},
            "Script Body": {"required": False},
            "Module Directory": {"required": False},
        }
    }
    
    return basic_schemas.get(processor_type, {})


async def _validate_processor_properties_upfront(processor_type: str, properties: Dict[str, Any], nifi_client: 'NiFiClient', user_request_id: str, action_id: str) -> tuple[Dict[str, Any], List[str], List[str]]:
    """
    Validate processor properties upfront using schema validation.
    
    Args:
        processor_type: The processor type
        properties: Properties to validate
        nifi_client: NiFi client instance
        user_request_id: Request ID for logging
        action_id: Action ID for logging
        
    Returns:
        Tuple of (validated_properties, warnings, errors)
    """
    validated_props = properties.copy()
    warnings = []
    errors = []
    
    # Get property schema
    property_schema = await _get_processor_property_schema(processor_type, nifi_client, user_request_id, action_id)
    
    # Special handling for RouteOnAttribute
    if processor_type == "org.apache.nifi.processors.standard.RouteOnAttribute":
        validated_props, route_warnings, route_errors = _validate_route_on_attribute_properties(validated_props)
        warnings.extend(route_warnings)
        errors.extend(route_errors)
    
    # Validate against schema
    for prop_name, prop_value in list(validated_props.items()):
        if prop_name in property_schema:
            schema = property_schema[prop_name]
            
            # Check deprecated properties
            if schema.get("deprecated"):
                replacement = schema.get("replacement")
                if replacement and replacement not in validated_props:
                    validated_props[replacement] = prop_value
                    warnings.append(f"Property '{prop_name}' is deprecated, mapped to '{replacement}'")
                del validated_props[prop_name]
                continue
            
            # Check allowed values
            allowed_values = schema.get("allowed_values")
            if allowed_values and prop_value not in allowed_values:
                # Try to find close match
                close_match = None
                for allowed in allowed_values:
                    if prop_value.lower() == allowed.lower():
                        close_match = allowed
                        break
                
                if close_match:
                    validated_props[prop_name] = close_match
                    warnings.append(f"Auto-corrected '{prop_name}': '{prop_value}' → '{close_match}'")
                else:
                    errors.append(f"Property '{prop_name}' value '{prop_value}' not in allowed values: {allowed_values}")
    
    return validated_props, warnings, errors


@smart_parameter_validation
@mcp.tool()
@tool_phases(["Create"])
async def create_nifi_connections(
    connections: List[ConnectionDefinition],
    process_group_id: Optional[str] = None
) -> List[Dict]:
    """
    Creates NiFi connections using component names OR UUIDs with automatic resolution.
    
    This unified tool accepts multiple input formats and automatically handles the complexity
    of UUID resolution, duplicate name detection, and component mapping. No manual UUID lookup required.
    
    INPUT FORMATS SUPPORTED:
    1. **UUIDs** (PREFERRED): Direct component UUIDs - always unambiguous
    2. **Component Names**: Resolved to UUIDs automatically
    3. **Type-Specific Names**: "name:type" format for disambiguation
    
    DUPLICATE NAME HANDLING:
    - UUIDs always take precedence (no ambiguity possible)
    - Same-type duplicates (e.g., two processors named "LogData") → Error with clear message
    - Cross-type duplicates (e.g., processor and port both named "DataInput") → Auto-resolved with type-specific keys
    - Ambiguous cases → Suggests using UUIDs or "name:type" format
    
    Args:
        connections: List of connection definitions using component names OR UUIDs. Each dictionary must contain:
            - source_name (str): Name OR UUID of the source component (UUIDs take precedence)
            - target_name (str): Name OR UUID of the target component (UUIDs take precedence)
            - relationships (list): List of relationship names to connect
            - process_group_id (str, optional): Process group to search in (defaults to parameter)
            
        process_group_id: Default process group to search for components (optional)
            
    Example:
    ```python
    connections = [
        {
            "source_name": "HandleHttpRequest",  # Use component name
            "target_name": "LogRawInput", 
            "relationships": ["success"]
        },
        {
            "source_name": "123e4567-e89b-12d3-a456-426614174000",  # Use UUID directly
            "target_name": "ExecuteScript",
            "relationships": ["success"]
        },
        {
            "source_name": "DataInput:processor",  # Disambiguate by type if needed
            "target_name": "456e7890-e89b-12d3-a456-426614174001",  # Mix names and UUIDs
            "relationships": ["success"]
        }
    ]
            result = create_nifi_connections(connections, process_group_id="root")
    ```
    
    Returns:
        List of connection creation results with smart resolution details.
    """
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
    
    if not connections:
        raise ToolError("The 'connections' list cannot be empty.")
    if not isinstance(connections, list):
        raise ToolError("Invalid 'connections' type. Expected a list of dictionaries.")
    
    # Handle legacy parameter format conversion (source_id/target_id -> source_name/target_name)
    for i, conn in enumerate(connections):
        if isinstance(conn, dict):
            has_legacy_params = "source_id" in conn and "target_id" in conn
            has_new_params = "source_name" in conn and "target_name" in conn
            
            if has_legacy_params and not has_new_params:
                # Legacy format - auto-convert, preserving all other fields
                new_conn = conn.copy()  # Preserve all existing fields
                new_conn["source_name"] = new_conn.pop("source_id")  # Rename source_id to source_name
                new_conn["target_name"] = new_conn.pop("target_id")   # Rename target_id to target_name
                connections[i] = new_conn
                local_logger.info(f"Connection {i} auto-converted from legacy 'source_id/target_id' to 'source_name/target_name' format")
    
    local_logger.info(f"Creating {len(connections)} connections using smart name resolution")
    
    # Build a comprehensive component name->UUID mapping
    component_map = {}
    search_process_groups = set()
    
    # Collect all process groups to search
    for conn in connections:
        pg_id = conn.get("process_group_id", process_group_id or "root")
        search_process_groups.add(pg_id)
    
    # Build name mapping for each process group
    for pg_id in search_process_groups:
        try:
            local_logger.info(f"Building component map for process group: {pg_id}")
            
            # Get processors
            processors = await list_nifi_objects(
                object_type="processors",
                process_group_id=pg_id,
                search_scope="current_group"
            )
            # Handle the flat list response structure
            for proc in processors:
                if isinstance(proc, dict):
                    name = proc.get("name")
                    proc_id = proc.get("id")
                    if name and proc_id:
                        key = f"{pg_id}:{name}"
                        if key in component_map:
                            # Handle duplicate names by creating type-specific keys
                            existing_type = component_map[key]["type"]
                            if existing_type != "processor":
                                # Different types - create type-specific keys
                                component_map[f"{pg_id}:{name}:processor"] = {
                                    "id": proc_id,
                                    "type": "processor", 
                                    "name": name,
                                    "process_group_id": pg_id
                                }
                                # Also update the existing entry with type suffix
                                existing_entry = component_map.pop(key)
                                component_map[f"{pg_id}:{name}:{existing_entry['type']}"] = existing_entry
                                local_logger.warning(f"Duplicate name '{name}' found - created type-specific keys")
                            else:
                                # Same type duplicates - this is a real problem
                                existing_id = component_map[key]["id"]
                                local_logger.error(f"Duplicate processor name '{name}' in process group {pg_id}: existing={existing_id}, new={proc_id}")
                                raise ToolError(f"Duplicate processor name '{name}' found in process group {pg_id}. Names must be unique within each process group.")
                        else:
                            component_map[key] = {
                                "id": proc_id,
                                "type": "processor",
                                "name": name,
                                "process_group_id": pg_id
                            }
            
            # Get all ports (both input and output)
            all_ports = await list_nifi_objects(
                object_type="ports", 
                process_group_id=pg_id,
                search_scope="current_group"
            )
            # Handle the flat list response structure and filter by type
            for port in all_ports:
                if isinstance(port, dict):
                    name = port.get("name")
                    port_id = port.get("id")
                    port_type = port.get("type")  # "INPUT_PORT" or "OUTPUT_PORT"
                    if name and port_id and port_type:
                        # Normalize port type for component map
                        normalized_type = "input_port" if port_type == "INPUT_PORT" else "output_port"
                        
                        key = f"{pg_id}:{name}"
                        if key in component_map:
                            # Handle duplicate names by creating type-specific keys
                            existing_type = component_map[key]["type"]
                            if existing_type != normalized_type:
                                # Different types - create type-specific keys
                                component_map[f"{pg_id}:{name}:{normalized_type}"] = {
                                    "id": port_id,
                                    "type": normalized_type,
                                    "name": name,
                                    "process_group_id": pg_id
                                }
                                # Also update the existing entry with type suffix
                                existing_entry = component_map.pop(key)
                                component_map[f"{pg_id}:{name}:{existing_entry['type']}"] = existing_entry
                                local_logger.warning(f"Duplicate name '{name}' found - created type-specific keys")
                            else:
                                # Same type duplicates - this is a real problem
                                existing_id = component_map[key]["id"]
                                local_logger.error(f"Duplicate {normalized_type} name '{name}' in process group {pg_id}: existing={existing_id}, new={port_id}")
                                raise ToolError(f"Duplicate {normalized_type} name '{name}' found in process group {pg_id}. Names must be unique within each process group.")
                        else:
                            component_map[key] = {
                                "id": port_id,
                                "type": normalized_type,
                                "name": name,
                                "process_group_id": pg_id
                            }
                        
        except Exception as e:
            local_logger.warning(f"Failed to build component map for process group {pg_id}: {e}")
    
    local_logger.info(f"Built component map with {len(component_map)} components")
    
    # Convert name-based connections to UUID-based connections
    uuid_connections = []
    resolution_details = []
    
    for i, conn in enumerate(connections):
        pg_id = conn.get("process_group_id", process_group_id or "root")
        source_name = conn.get("source_name")
        target_name = conn.get("target_name")
        relationships = conn.get("relationships", [])
        
        if not source_name or not target_name:
            raise ToolError(f"Connection {i} missing required 'source_name' and/or 'target_name'")
        # Note: Empty relationships are allowed for port connections, validation happens in _create_nifi_connection_single
        
        def is_uuid(value: str) -> bool:
            """Check if a string is a valid UUID format."""
            import re
            uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)
            return bool(uuid_pattern.match(value))
        
        def resolve_component(component_name: str, pg_id: str, component_role: str):
            """Resolve component name or UUID to component info, handling duplicates and type-specific keys."""
            # Check if input is already a UUID
            if is_uuid(component_name):
                # Search for this UUID in the component map
                for key, info in component_map.items():
                    if info["id"] == component_name:
                        local_logger.info(f"Resolved {component_role} UUID '{component_name}' to component '{info['name']}' ({info['type']})")
                        return key, info
                # UUID not found in our component map
                raise ToolError(f"{component_role.capitalize()} UUID '{component_name}' not found in process group '{pg_id}' or its components")
            
            # Try direct lookup first
            direct_key = f"{pg_id}:{component_name}"
            if direct_key in component_map:
                return direct_key, component_map[direct_key]
            
            # Try root process group fallback
            if pg_id != "root":
                root_key = f"root:{component_name}"
                if root_key in component_map:
                    return root_key, component_map[root_key]
            
            # Look for type-specific keys (handles duplicate names across types)
            type_specific_keys = [key for key in component_map.keys() 
                                if key.startswith(f"{pg_id}:{component_name}:") or 
                                   (pg_id != "root" and key.startswith(f"root:{component_name}:"))]
            
            if len(type_specific_keys) == 1:
                # Only one type-specific match found
                key = type_specific_keys[0]
                local_logger.info(f"Resolved {component_role} '{component_name}' to type-specific key: {key}")
                return key, component_map[key]
            elif len(type_specific_keys) > 1:
                # Multiple type-specific matches - need disambiguation
                available_types = [key.split(":")[-1] for key in type_specific_keys]
                raise ToolError(f"Ambiguous {component_role} component '{component_name}' - multiple types found: {available_types}. Use format 'name:type' (e.g., 'DataInput:processor' or 'DataInput:input_port')")
            
            # No matches found - provide helpful error
            available_components = []
            search_prefixes = [f"{pg_id}:"]
            if pg_id != "root":
                search_prefixes.append("root:")
            
            for prefix in search_prefixes:
                for key in component_map.keys():
                    if key.startswith(prefix):
                        # Extract just the name part (handle both simple and type-specific keys)
                        parts = key[len(prefix):].split(":")
                        name_part = parts[0]
                        if len(parts) > 1:
                            # Type-specific key
                            available_components.append(f"{name_part}:{parts[1]}")
                        else:
                            # Simple key
                            available_components.append(name_part)
            
            available_components = sorted(set(available_components))
            raise ToolError(f"{component_role.capitalize()} component '{component_name}' not found in process group '{pg_id}'. Available: {available_components}")
        
        # Handle source component (UUID, name, or name:type)
        if is_uuid(source_name):
            # Direct UUID resolution
            source_key, source_info = resolve_component(source_name, pg_id, "source")
        else:
            source_name_parts = source_name.split(":")
            if len(source_name_parts) == 2:
                # Type-specific name provided (e.g., "DataInput:processor")
                base_source_name, source_type = source_name_parts
                source_key = f"{pg_id}:{base_source_name}:{source_type}"
                if source_key not in component_map:
                    if pg_id != "root":
                        source_key = f"root:{base_source_name}:{source_type}"
                if source_key not in component_map:
                    raise ToolError(f"Source component '{source_name}' not found")
                source_info = component_map[source_key]
            else:
                # Regular name resolution
                source_key, source_info = resolve_component(source_name, pg_id, "source")
        
        # Handle target component (UUID, name, or name:type)
        if is_uuid(target_name):
            # Direct UUID resolution
            target_key, target_info = resolve_component(target_name, pg_id, "target")
        else:
            target_name_parts = target_name.split(":")
            if len(target_name_parts) == 2:
                # Type-specific name provided (e.g., "DataInput:processor")
                base_target_name, target_type = target_name_parts
                target_key = f"{pg_id}:{base_target_name}:{target_type}"
                if target_key not in component_map:
                    if pg_id != "root":
                        target_key = f"root:{base_target_name}:{target_type}"
                if target_key not in component_map:
                    raise ToolError(f"Target component '{target_name}' not found")
                target_info = component_map[target_key]
            else:
                # Regular name resolution
                target_key, target_info = resolve_component(target_name, pg_id, "target")
        

        
        # Create UUID-based connection
        uuid_connection = {
            "source_id": source_info["id"],
            "target_id": target_info["id"],
            "relationships": relationships
        }
        uuid_connections.append(uuid_connection)
        
        # Track resolution details
        resolution_details.append({
            "index": i,
            "source_resolution": {
                "name": source_name,
                "resolved_id": source_info["id"],
                "type": source_info["type"]
            },
            "target_resolution": {
                "name": target_name, 
                "resolved_id": target_info["id"],
                "type": target_info["type"]
            },
            "relationships": relationships
        })
        
        local_logger.info(f"Connection {i}: '{source_name}' ({source_info['id']}) -> '{target_name}' ({target_info['id']}) via {relationships}")
    
    # Create connections using the single connection function
    try:
        creation_results = []
        for uuid_conn in uuid_connections:
            result = await _create_nifi_connection_single(
                source_id=uuid_conn["source_id"],
                target_id=uuid_conn["target_id"],
                relationships=uuid_conn["relationships"]
            )
            creation_results.append(result)
        
        # Enhance results with smart resolution details
        enhanced_results = []
        for i, result in enumerate(creation_results):
            enhanced_result = result.copy()
            enhanced_result["smart_resolution"] = resolution_details[i]
            enhanced_result["original_request"] = connections[i]
            enhanced_results.append(enhanced_result)
        
        local_logger.info(f"Smart connection creation completed: {len(enhanced_results)} connections processed")
        return enhanced_results
        
    except Exception as e:
        local_logger.error(f"Failed to create connections after name resolution: {e}")
        raise ToolError(f"Connection creation failed after successful name resolution: {e}")


