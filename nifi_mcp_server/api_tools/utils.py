import asyncio
import functools
import json
from typing import List, Dict, Optional, Any, Union, Literal
from loguru import logger as _logger # Use _logger to avoid potential conflict

# Import mcp from the new core module
from ..core import mcp # Removed nifi_api_client
# REMOVED from ..server import mcp, nifi_api_client

# Removed imports for NiFi types/exceptions previously needed by ensure_authenticated
# from nifi_mcp_server.nifi_client import NiFiClient, NiFiAuthenticationError
from mcp.server.fastmcp.exceptions import ToolError

# --- Phase Tagging Decorator --- 
PHASE_TAGS_ATTR = "_tool_phases"
_tool_phase_registry: Dict[str, List[str]] = {} # Module-level registry
_mcp_only_tool_names: set = set()

# Tools that are always allowed regardless of current_nifi_phase (control tools)
CONTROL_TOOL_NAMES = {"get_nifi_session_info", "set_nifi_server", "set_nifi_phase"}

VALID_PHASES = ["Review", "Build", "Modify", "Operate", "All"]


def is_mcp_only_tool(name: str) -> bool:
    """Return True if the tool is MCP-only (hidden from web UI)."""
    return name in _mcp_only_tool_names


def _estimate_tokens(obj: Any) -> int:
    """Rough token estimate: len(json.dumps(...))/4. Uses default=str for non-serializable values."""
    try:
        return len(json.dumps(obj, default=str)) // 4
    except Exception:
        return 0


def _accumulate_session_tokens(args: tuple, kwargs: dict, result: Any) -> None:
    """Update session_total_tokens_in and session_total_tokens_out from this tool call."""
    from ..request_context import session_total_tokens_in, session_total_tokens_out
    tokens_in = _estimate_tokens({"args": args, "kwargs": kwargs})
    tokens_out = _estimate_tokens(result)
    session_total_tokens_in.set(session_total_tokens_in.get() + tokens_in)
    session_total_tokens_out.set(session_total_tokens_out.get() + tokens_out)


def _check_phase_and_call(tool_name: str, tool_phases_list: List[str], func: Any, *args: Any, **kwargs: Any) -> Any:
    """Run phase check then call func. Used by sync wrapper."""
    from ..request_context import current_nifi_phase
    phase = current_nifi_phase.get()
    if not phase or (phase and str(phase).strip().lower() == "all"):
        result = func(*args, **kwargs)
        _accumulate_session_tokens(args, kwargs, result)
        return result
    if tool_name in CONTROL_TOOL_NAMES:
        result = func(*args, **kwargs)
        _accumulate_session_tokens(args, kwargs, result)
        return result
    if not tool_phases_list:
        result = func(*args, **kwargs)
        _accumulate_session_tokens(args, kwargs, result)
        return result
    allowed = [p.lower() for p in tool_phases_list]
    if phase.strip().lower() not in allowed:
        raise ToolError(
            f"Phase is set to '{phase}'. This tool is not allowed in this phase. "
            f"Allowed phases for this tool: {', '.join(tool_phases_list)}."
        )
    result = func(*args, **kwargs)
    _accumulate_session_tokens(args, kwargs, result)
    return result


async def _check_phase_and_call_async(tool_name: str, tool_phases_list: List[str], func: Any, *args: Any, **kwargs: Any) -> Any:
    """Run phase check then await func. Used by async wrapper."""
    from ..request_context import current_nifi_phase
    phase = current_nifi_phase.get()
    if not phase or (phase and str(phase).strip().lower() == "all"):
        result = await func(*args, **kwargs)
        _accumulate_session_tokens(args, kwargs, result)
        return result
    if tool_name in CONTROL_TOOL_NAMES:
        result = await func(*args, **kwargs)
        _accumulate_session_tokens(args, kwargs, result)
        return result
    if not tool_phases_list:
        result = await func(*args, **kwargs)
        _accumulate_session_tokens(args, kwargs, result)
        return result
    allowed = [p.lower() for p in tool_phases_list]
    if phase.strip().lower() not in allowed:
        raise ToolError(
            f"Phase is set to '{phase}'. This tool is not allowed in this phase. "
            f"Allowed phases for this tool: {', '.join(tool_phases_list)}."
        )
    result = await func(*args, **kwargs)
    _accumulate_session_tokens(args, kwargs, result)
    return result


def tool_phases(phases: List[str], mcp_only: bool = False):
    """Decorator to tag MCP tools with applicable operational phases. When mcp_only=True, tool is hidden from REST GET /tools (web UI). Enforces current_nifi_phase before running (control tools are always allowed)."""
    def decorator(func):
        setattr(func, PHASE_TAGS_ATTR, phases)
        _tool_phase_registry[func.__name__] = phases
        if mcp_only:
            _mcp_only_tool_names.add(func.__name__)
        _logger.trace(f"Registered phases {phases} for tool {func.__name__} mcp_only={mcp_only}")

        tool_phases_list = phases
        tool_name = func.__name__

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await _check_phase_and_call_async(tool_name, tool_phases_list, func, *args, **kwargs)
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                return _check_phase_and_call(tool_name, tool_phases_list, func, *args, **kwargs)
            return sync_wrapper
    return decorator
# -----------------------------

# Removed ensure_authenticated function
# --- Helper Function for Authentication --- 
# 
# async def ensure_authenticated(nifi_api_client: NiFiClient, logger): # Accept client and logger
#     """Helper to ensure the NiFi client is authenticated before tool use."""
#     if nifi_api_client is None:
#         raise ToolError("NiFi Client is not configured properly (check NIFI_API_URL).")
#     if not nifi_api_client.is_authenticated:
#         logger.info("NiFi client not authenticated. Attempting authentication...")
#         try:
#             await nifi_api_client.authenticate()
#             logger.info("Authentication successful via MCP tool request.")
#         except NiFiAuthenticationError as e:
#             logger.error(f"Authentication failed during tool execution: {e}")
#             # Raise ToolError, but indicate user action needed in the message
#             raise ToolError(
#                 f"NiFi authentication failed ({e}). Please ensure NIFI_USERNAME and NIFI_PASSWORD "
#                 "are correctly set in the server's environment/.env file."
#             ) from e
#         except Exception as e:
#             logger.error(f"Unexpected error during authentication: {e}", exc_info=True)
#             raise ToolError(f"An unexpected error occurred during NiFi authentication: {e}")
#     pass # Add pass to avoid syntax error if body is empty

# --- Formatting/Filtering Helpers --- 

def _format_processor_summary(processors_data):
    """Formats basic processor data from NiFi API list response."""
    formatted = []
    if processors_data: # Already a list from list_processors
        for proc in processors_data:
            # Use the existing filter function for consistency, can enhance later
            basic_info = filter_processor_data(proc) 
            formatted.append(basic_info)
    return formatted

def _format_connection_summary(connections_data):
    """Formats basic connection data from NiFi API list response."""
    formatted = []
    if connections_data: # Already a list from list_connections
        for conn in connections_data:
             # Use existing filter function
            basic_info = filter_connection_data(conn)
            formatted.append(basic_info)
    return formatted

def _format_port_summary(input_ports_data, output_ports_data):
    """Formats and combines input and output port data for summary list."""
    formatted = []
    # Process Input Ports
    if input_ports_data: # Already a list
        for port in input_ports_data:
            component = port.get('component', {})
            status = port.get('status', {})
            formatted.append({
                "id": port.get('id'),
                "name": component.get('name'),
                "type": "INPUT_PORT",
                "state": component.get('state'),
                "comments": component.get('comments'),
                "concurrent_tasks": component.get('concurrentlySchedulableTaskCount'),
                "validation_errors": component.get('validationErrors'),
                "active_thread_count": status.get('aggregateSnapshot', {}).get('activeThreadCount'), # Check nesting
                "queued_count": status.get('aggregateSnapshot', {}).get('flowFilesQueued'),
                "queued_size": status.get('aggregateSnapshot', {}).get('bytesQueued'),
            })
    # Process Output Ports
    if output_ports_data: # Already a list
        for port in output_ports_data:
            component = port.get('component', {})
            status = port.get('status', {})
            formatted.append({
                "id": port.get('id'),
                "name": component.get('name'),
                "type": "OUTPUT_PORT",
                "state": component.get('state'),
                "comments": component.get('comments'),
                "concurrent_tasks": component.get('concurrentlySchedulableTaskCount'),
                "validation_errors": component.get('validationErrors'),
                "active_thread_count": status.get('aggregateSnapshot', {}).get('activeThreadCount'), # Check nesting
                "queued_count": status.get('aggregateSnapshot', {}).get('flowFilesQueued'),
                "queued_size": status.get('aggregateSnapshot', {}).get('bytesQueued'),
            })
    return formatted

def filter_processor_data(processor):
    """Extract only the essential fields from a processor object"""
    component = processor.get("component", {}) # Added safety get
    status = processor.get("status", {}) # Added safety get
    config = component.get("config", {}) # Get config dict
    properties = config.get("properties", {}) # Get properties from config

    return {
        "id": processor.get("id"),
        "name": component.get("name"),
        "type": component.get("type"),
        "state": component.get("state"),
        "position": processor.get("position"),
        "runStatus": status.get("runStatus"), # Use the safer status get
        "validationStatus": component.get("validationStatus"),
        "validationErrors": component.get("validationErrors", []),
        "relationships": component.get("relationships", []), # Use safer component get
        "properties": properties, # Add properties here

    }

def filter_created_processor_data(processor_entity):
    """Extract essential fields from a newly created processor entity, including properties and relationships"""
    component = processor_entity.get("component", {})
    revision = processor_entity.get("revision", {})
    config = component.get("config", {})
    properties = config.get("properties", {})
    
    return {
        "id": processor_entity.get("id"),
        "name": component.get("name"),
        "type": component.get("type"),
        "position": processor_entity.get("position"), # Position is top-level in creation response
        "validationStatus": component.get("validationStatus"),
        "validationErrors": component.get("validationErrors"),
        "properties": properties, # Include properties for LLM context
        "relationships": component.get("relationships", []), # Include relationships for LLM context
        "version": revision.get("version"), # Get version from revision dict
    }

def filter_connection_data(connection_entity):
    """Extract only the essential identification fields from a connection entity."""
    # Access nested component data safely
    component = connection_entity.get("component", {})
    source = component.get("source", {})
    destination = component.get("destination", {})

    return {
        "id": connection_entity.get("id"),
        "uri": connection_entity.get("uri"),
        "sourceId": source.get("id"),
        "sourceGroupId": source.get("groupId"),
        "sourceType": source.get("type"),
        "sourceName": source.get("name"),
        "destinationId": destination.get("id"),
        "destinationGroupId": destination.get("groupId"),
        "destinationType": destination.get("type"),
        "destinationName": destination.get("name"),
        "name": component.get("name"), # Get connection name safely
        "selectedRelationships": component.get("selectedRelationships"),
        "availableRelationships": component.get("availableRelationships"),
    }

def filter_port_data(port_entity):
    """Extract essential fields from a port entity (input or output)."""
    component = port_entity.get("component", {})
    revision = port_entity.get("revision", {})
    return {
        "id": port_entity.get("id"),
        "name": component.get("name"),
        "type": component.get("type"), # Will be INPUT_PORT or OUTPUT_PORT
        "state": component.get("state"),
        "position": port_entity.get("position"),
        "comments": component.get("comments"),
        "allowRemoteAccess": component.get("allowRemoteAccess"),
        "concurrentlySchedulableTaskCount": component.get("concurrentlySchedulableTaskCount"),
        "validationStatus": component.get("validationStatus"),
        "validationErrors": component.get("validationErrors"),
        "version": revision.get("version"),
    }

def filter_process_group_data(pg_entity):
    """Extract essential fields from a process group entity."""
    component = pg_entity.get("component", {})
    revision = pg_entity.get("revision", {})
    status = pg_entity.get("status", {}).get("aggregateSnapshot", {}) # Status is nested
    return {
        "id": pg_entity.get("id"),
        "name": component.get("name"),
        "position": pg_entity.get("position"),
        "comments": component.get("comments"),
        "parameterContext": component.get("parameterContext", {}).get("id"), # Just the ID
        "flowfileConcurrency": component.get("flowfileConcurrency"),
        "flowfileOutboundPolicy": component.get("flowfileOutboundPolicy"),
        # Basic status counts if available from creation response
        "runningCount": status.get("runningCount"), 
        "stoppedCount": status.get("stoppedCount"),
        "invalidCount": status.get("invalidCount"),
        "disabledCount": status.get("disabledCount"),
        "activeRemotePortCount": status.get("activeRemotePortCount"),
        "inactiveRemotePortCount": status.get("inactiveRemotePortCount"),
        "version": revision.get("version"),
    }

def filter_drop_request_data(drop_request: Dict[str, Any]) -> Dict[str, Any]:
    """Filters and formats drop request data for API responses."""
    if not drop_request:
        return {}
    
    return {
        "id": drop_request.get("id"),
        "uri": drop_request.get("uri"),
        "lastUpdated": drop_request.get("lastUpdated"),
        "finished": drop_request.get("finished", False),
        "failureReason": drop_request.get("failureReason"),
        "percentCompleted": drop_request.get("percentCompleted", 0),
        "currentCount": drop_request.get("currentCount", 0),
        "currentSize": drop_request.get("currentSize", 0),
        "current": drop_request.get("current", 0),
        "originalCount": drop_request.get("originalCount", 0),
        "originalSize": drop_request.get("originalSize", 0),
        "dropped": drop_request.get("dropped", 0),
        "state": drop_request.get("state", "UNKNOWN"),
        "queueSize": {
            "byteCount": drop_request.get("queueSize", {}).get("byteCount", 0),
            "objectCount": drop_request.get("queueSize", {}).get("objectCount", 0)
        }
    }

def format_drop_request_summary(drop_results: Dict[str, Any]) -> Dict[str, Any]:
    """Formats the summary of drop request results for API responses."""
    if not drop_results:
        return {
            "success": False,
            "message": "No drop request results provided",
            "summary": {}
        }

    total_connections = len(drop_results.get("results", []))
    successful_drops = sum(1 for r in drop_results.get("results", []) if r.get("success", False))
    total_dropped = sum(r.get("dropped_count", 0) for r in drop_results.get("results", []) if r.get("success", False))
    
    failed_connections = []
    for result in drop_results.get("results", []):
        if not result.get("success", False):
            failed_connections.append({
                "connection_id": result.get("connection_id"),
                "error": result.get("error", "Unknown error")
            })

    return {
        "success": drop_results.get("success", False),
        "message": drop_results.get("message", ""),
        "summary": {
            "total_connections": total_connections,
            "successful_drops": successful_drops,
            "failed_drops": total_connections - successful_drops,
            "total_flowfiles_dropped": total_dropped,
            "failed_connections": failed_connections if failed_connections else None
        }
    }

def filter_controller_service_data(controller_service_entity: Dict) -> Dict:
    """Extract essential fields from a controller service entity."""
    component = controller_service_entity.get("component", {})
    revision = controller_service_entity.get("revision", {})
    config = component.get("config", {})
    properties = config.get("properties", {})
    
    return {
        "id": controller_service_entity.get("id"),
        "name": component.get("name"),
        "type": component.get("type"),
        "state": component.get("state"),
        "comments": component.get("comments"),
        "validationStatus": component.get("validationStatus"),
        "validationErrors": component.get("validationErrors", []),
        "properties": properties,
        "referencingComponents": component.get("referencingComponents", []),
        "version": revision.get("version"),
        "bundle": component.get("bundle", {}),
        "controllerServiceApis": component.get("controllerServiceApis", []),
    }

def _format_controller_service_summary(controller_services_data):
    """Formats basic controller service data from NiFi API list response."""
    formatted = []
    if controller_services_data:  # Should be a list from list_controller_services
        for cs in controller_services_data:
            # Use the existing filter function for consistency
            basic_info = filter_controller_service_data(cs)
            formatted.append(basic_info)
    return formatted

def validate_and_suggest_parameters(tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parameter validation middleware that detects common LLM mistakes and suggests corrections.
    
    This function can auto-correct obvious parameter naming mistakes and provide helpful
    suggestions for malformed parameters.
    
    Args:
        tool_name: Name of the tool being called
        parameters: The parameters passed to the tool
        
    Returns:
        Corrected parameters with suggestions
        
    Raises:
        ToolError: If parameters cannot be corrected
    """
    corrected_params = parameters.copy()
    suggestions = []
    
    # Define common parameter corrections by tool
    PARAMETER_CORRECTIONS = {
        "delete_nifi_objects": {
            "deletion_requests": "objects",
            "delete_requests": "objects", 
            "items": "objects",
            "deletions": "objects"
        },
        "operate_nifi_objects": {
            "operation_requests": "operations",
            "requests": "operations",
            "ops": "operations",
            "items": "operations"
        },
        "update_nifi_processors_properties": {
            "property_updates": "updates",
            "processor_updates": "updates",
            "props": "updates",
            "properties": "updates"
        },
        "create_nifi_connections": {
            "connection_requests": "connections",
            "conn_requests": "connections",
            "links": "connections"
        },

    }
    
    # Check for parameter name corrections
    if tool_name in PARAMETER_CORRECTIONS:
        corrections = PARAMETER_CORRECTIONS[tool_name]
        for wrong_param, correct_param in corrections.items():
            if wrong_param in corrected_params and correct_param not in corrected_params:
                # Auto-correct the parameter name
                corrected_params[correct_param] = corrected_params.pop(wrong_param)
                suggestions.append(f"Auto-corrected parameter '{wrong_param}' to '{correct_param}'")
    
    # Check for common structural mistakes
    for param_name, param_value in corrected_params.items():
        # Check for accidentally nested parameters
        if isinstance(param_value, dict) and len(param_value) == 1:
            nested_key = list(param_value.keys())[0]
            if nested_key == param_name:
                # Fix nested parameter (e.g., {"objects": {"objects": [...]}})
                corrected_params[param_name] = param_value[nested_key]
                suggestions.append(f"Fixed nested parameter structure in '{param_name}'")
        
        # Check for missing required list structure
        if param_name in ["objects", "operations", "updates", "connections"]:
            if not isinstance(param_value, list):
                if isinstance(param_value, dict):
                    # Convert single object to list
                    corrected_params[param_name] = [param_value]
                    suggestions.append(f"Converted single {param_name[:-1]} to list format")
                else:
                    raise ToolError(f"Parameter '{param_name}' must be a list, got {type(param_value)}")
    
    # Tool-specific validations
    if tool_name == "create_nifi_connections":
        connections = corrected_params.get("connections", [])
        for i, conn in enumerate(connections):
            if isinstance(conn, dict):
                # Check for legacy vs new parameter format
                has_legacy_params = "source_id" in conn and "target_id" in conn
                has_new_params = "source_name" in conn and "target_name" in conn
                
                if has_legacy_params and not has_new_params:
                    # Legacy format - auto-convert, preserving all other fields
                    new_conn = conn.copy()  # Preserve all existing fields
                    new_conn["source_name"] = new_conn.pop("source_id")  # Rename source_id to source_name
                    new_conn["target_name"] = new_conn.pop("target_id")   # Rename target_id to target_name
                    corrected_params["connections"][i] = new_conn
                    suggestions.append(f"Connection {i} auto-converted from legacy 'source_id/target_id' to 'source_name/target_name' format")
                elif not has_new_params:
                    # Missing required fields
                    if "source_name" not in conn:
                        suggestions.append(f"Connection {i} missing 'source_name' field")
                    if "target_name" not in conn:
                        suggestions.append(f"Connection {i} missing 'target_name' field")
                
                # Check for missing relationships
                if "relationships" not in conn:
                    suggestions.append(f"Connection {i} missing 'relationships' field")
    
    elif tool_name == "delete_nifi_objects":
        objects = corrected_params.get("objects", [])
        for i, obj in enumerate(objects):
            if isinstance(obj, dict):
                # Check for required fields
                if "object_type" not in obj:
                    suggestions.append(f"Object {i} missing required 'object_type' field")
                if "object_id" not in obj:
                    suggestions.append(f"Object {i} missing required 'object_id' field")
    
    elif tool_name == "operate_nifi_objects":
        operations = corrected_params.get("operations", [])
        for i, op in enumerate(operations):
            if isinstance(op, dict):
                # Check for operation/object type compatibility
                object_type = op.get("object_type")
                operation_type = op.get("operation_type")
                
                if object_type == "controller_service" and operation_type in ["start", "stop"]:
                    suggestions.append(f"Operation {i}: Use 'enable'/'disable' for controller services, not 'start'/'stop'")
                elif object_type != "controller_service" and operation_type in ["enable", "disable"]:
                    suggestions.append(f"Operation {i}: Use 'start'/'stop' for {object_type}, not 'enable'/'disable'")
    
    # Log suggestions if any were made
    if suggestions:
        from loguru import logger
        for suggestion in suggestions:
            logger.info(f"[Parameter Validation] {suggestion}")
    
    return corrected_params

def smart_parameter_validation(func):
    """
    Decorator that applies parameter validation middleware to MCP tools.
    
    Usage:
    @smart_parameter_validation
    @mcp.tool()
    async def my_tool(param1, param2):
        ...
    """
    import functools
    from inspect import signature
    
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        # Get tool name from function
        tool_name = func.__name__
        
        # Convert args to kwargs using function signature
        sig = signature(func)
        bound_args = sig.bind(*args, **kwargs)
        bound_args.apply_defaults()
        
        # Apply parameter validation
        try:
            corrected_params = validate_and_suggest_parameters(tool_name, bound_args.arguments)
            
            # Update bound arguments with corrected parameters
            for key, value in corrected_params.items():
                if key in sig.parameters:
                    bound_args.arguments[key] = value
            
            # Call original function with corrected parameters
            return await func(**bound_args.arguments)
            
        except Exception as e:
            # Re-raise with additional context
            raise ToolError(f"Parameter validation failed for {tool_name}: {e}")
    
    return wrapper
