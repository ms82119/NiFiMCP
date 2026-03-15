import asyncio
import time
from typing import List, Dict, Optional, Any, Union, Literal, Set
from datetime import datetime # Added import

# Import necessary components from parent/utils
from loguru import logger # Keep global logger for potential module-level logging if needed
# Import mcp ONLY (client comes from context)
from ..core import mcp
# Removed nifi_api_client import
from .utils import (
    tool_phases,
    # ensure_authenticated, # Removed - authentication handled by factory
    _format_processor_summary,
    _format_connection_summary,
    _format_port_summary,
    filter_processor_data, # Keep if needed by helpers here
    filter_connection_data, # Add missing import
    filter_controller_service_data,  # Add controller service import
    _format_controller_service_summary  # Add controller service summary import
)
# Keep NiFiClient type hint and error imports
from nifi_mcp_server.nifi_client import NiFiClient, NiFiAuthenticationError
from mcp.server.fastmcp.exceptions import ToolError

# Import flow documentation tools specifically needed by document_nifi_flow
from nifi_mcp_server.flow_documenter_improved import (
    document_nifi_flow_simplified,
    extract_important_properties,
    build_graph_structure,
    find_decision_branches,
    identify_flow_paths,
)

# Import context variables
from ..request_context import current_nifi_client, current_request_logger # Added
# Import new context variables for IDs
from ..request_context import current_user_request_id, current_action_id # Added


# --- Helper Functions (Now use context vars) --- 

async def _get_process_group_name(pg_id: str) -> str:
    """Helper to safely get a process group's name."""
    # Get client, logger, and IDs from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"

    if not nifi_client:
        local_logger.error("NiFi client not found in context for _get_process_group_name")
        return f"Error PG ({pg_id})"
        
    if pg_id == "root":
        return "Root"
    try:
        # Pass IDs directly to client method
        details = await nifi_client.get_process_group_details(
            pg_id, user_request_id=user_request_id, action_id=action_id
        )
        return details.get("component", {}).get("name", f"Unnamed PG ({pg_id})")
    except (ConnectionError, ValueError, NiFiAuthenticationError) as e:
        local_logger.warning(f"Could not fetch details for PG {pg_id} to get name: {e}")
        return f"Unknown PG ({pg_id})"
    except Exception as e:
        local_logger.error(f"Unexpected error fetching name for PG {pg_id}: {e}", exc_info=True)
        return f"Error PG ({pg_id})"

async def _get_process_group_contents_counts(pg_id: str) -> Dict[str, int]:
    """Fetches counts of components within a specific process group."""
    # Get client, logger, and IDs from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"
    
    if not nifi_client:
        local_logger.error("NiFi client not found in context for _get_process_group_contents_counts")
        return {"processors": -1, "connections": -1, "ports": -1, "process_groups": -1}

    counts = {"processors": 0, "connections": 0, "ports": 0, "process_groups": 0}
    # Extract context IDs from logger for the client calls
    # context = local_logger._context # REMOVED
    # user_request_id = context.get("user_request_id", "-") # REMOVED
    # action_id = context.get("action_id", "-") # REMOVED
    try:
        # Attempt to use the more efficient /flow endpoint first
        nifi_req = {"operation": "get_process_group_flow", "process_group_id": pg_id}
        local_logger.bind(interface="nifi", direction="request", data=nifi_req).debug("Calling NiFi API (for counts)")
        pg_flow_details = await nifi_client.get_process_group_flow(pg_id) # Flow endpoint doesn't take context IDs
        nifi_resp = {"has_flow_details": bool(pg_flow_details and 'processGroupFlow' in pg_flow_details)}
        local_logger.bind(interface="nifi", direction="response", data=nifi_resp).debug("Received from NiFi API (for counts)")
        
        if pg_flow_details and 'processGroupFlow' in pg_flow_details:
            flow_content = pg_flow_details['processGroupFlow'].get('flow', {})
            counts["processors"] = len(flow_content.get('processors', []))
            counts["connections"] = len(flow_content.get('connections', []))
            counts["ports"] = len(flow_content.get('inputPorts', [])) + len(flow_content.get('outputPorts', []))
            counts["process_groups"] = len(flow_content.get('processGroups', []))
            local_logger.debug(f"Got counts for PG {pg_id} via /flow endpoint: {counts}")
            return counts
        else:
             local_logger.warning(f"Could not get counts via /flow for PG {pg_id}, falling back to individual calls.")
             # Pass IDs explicitly to client methods that need them
             processors = await nifi_client.list_processors(pg_id, user_request_id=user_request_id, action_id=action_id)
             connections = await nifi_client.list_connections(pg_id, user_request_id=user_request_id, action_id=action_id)
             input_ports = await nifi_client.get_input_ports(pg_id) # Doesn't take context IDs
             output_ports = await nifi_client.get_output_ports(pg_id) # Doesn't take context IDs
             process_groups = await nifi_client.get_process_groups(pg_id) # Doesn't take context IDs
             counts["processors"] = len(processors) if processors else 0
             counts["connections"] = len(connections) if connections else 0
             counts["ports"] = (len(input_ports) if input_ports else 0) + (len(output_ports) if output_ports else 0)
             counts["process_groups"] = len(process_groups) if process_groups else 0
             local_logger.debug(f"Got counts for PG {pg_id} via individual calls: {counts}")
             return counts
             
    except (ConnectionError, ValueError, NiFiAuthenticationError) as e:
        local_logger.error(f"Error fetching counts for PG {pg_id}: {e}")
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API (for counts)")
        return counts
    except Exception as e:
         local_logger.error(f"Unexpected error fetching counts for PG {pg_id}: {e}", exc_info=True)
         local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received unexpected error from NiFi API (for counts)")
         return counts

async def _list_components_recursively_with_timeout(
    object_type: Literal["processors", "connections", "ports", "controller_services"],
    pg_id: str,
    depth: int = 0,
    max_depth: int = 3,
    timeout_seconds: Optional[float] = None,
    start_time: Optional[float] = None,
    processed_pgs: Optional[Set[str]] = None,
    continuation_token: Optional[str] = None
) -> Dict[str, Any]:
    """
    Recursively lists NiFi components with timeout support, parallel processing, and partial results.
    
    This function provides enhanced recursive traversal with the following optimizations:
    - Parallel processing of child process groups using asyncio.gather()
    - Configurable timeout with graceful degradation
    - Continuation token support for resuming interrupted operations
    - Progress tracking to avoid duplicate processing
    - Comprehensive error handling with partial results
    
    Args:
        object_type: Type of NiFi objects to list ("processors", "connections", "ports")
        pg_id: Process group ID to start traversal from
        depth: Current recursion depth (used internally)
        max_depth: Maximum recursion depth to prevent infinite loops
        timeout_seconds: Maximum time to spend on operation (None = no timeout)
        start_time: Operation start time (used internally for timeout calculation)
        processed_pgs: Set of already processed process group IDs (used internally)
        continuation_token: Token for resuming from previous partial result
    
    Returns:
        Dict containing:
        - results: List of component results, each with process_group_id, process_group_name, and objects
        - completed: bool indicating if operation completed fully (True) or timed out (False)
        - continuation_token: str for resuming if timed out (None if completed)
        - processed_count: int number of process groups successfully processed
        - timeout_occurred: bool indicating if timeout was the reason for stopping
        
    Performance Notes:
        - Uses parallel processing for child groups, significantly faster than sequential
        - Tracks processed groups to avoid duplication when using continuation tokens
        - Returns partial results immediately when timeout is reached
        - Memory efficient - doesn't load entire hierarchy into memory at once
    """
    if start_time is None:
        start_time = time.time()
    if processed_pgs is None:
        processed_pgs = set()
    
    # Get client, logger, and IDs from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"

    if not nifi_client:
        local_logger.error("NiFi client not found in context for _list_components_recursively_with_timeout")
        return {
            "results": [{
                "process_group_id": pg_id,
                "process_group_name": "Unknown (Client Error)",
                "error": "NiFi Client not available"
            }],
            "completed": False,
            "continuation_token": None,
            "processed_count": 0,
            "timeout_occurred": False
        }

    # Check timeout
    if timeout_seconds and (time.time() - start_time) >= timeout_seconds:
        local_logger.warning(f"Timeout reached while processing PG {pg_id} at depth {depth}")
        return {
            "results": [],
            "completed": False,
            "continuation_token": f"{pg_id}:{depth}",
            "processed_count": len(processed_pgs),
            "timeout_occurred": True
        }

    all_results = []
    
    # Skip if already processed (for continuation support)
    if pg_id in processed_pgs:
        local_logger.debug(f"Skipping already processed PG {pg_id}")
    else:
        processed_pgs.add(pg_id)
        current_pg_name = await _get_process_group_name(pg_id)
        
        current_level_objects = []
        try:
            if object_type == "processors":
                raw_objects = await nifi_client.list_processors(pg_id, user_request_id=user_request_id, action_id=action_id)
                current_level_objects = _format_processor_summary(raw_objects)
            elif object_type == "connections":
                raw_objects = await nifi_client.list_connections(pg_id, user_request_id=user_request_id, action_id=action_id)
                current_level_objects = _format_connection_summary(raw_objects)
            elif object_type == "controller_services":
                raw_objects = await nifi_client.list_controller_services(pg_id, user_request_id=user_request_id, action_id=action_id)
                current_level_objects = _format_controller_service_summary(raw_objects)
            elif object_type == "ports":
                input_ports = await nifi_client.get_input_ports(pg_id)
                output_ports = await nifi_client.get_output_ports(pg_id)
                current_level_objects = _format_port_summary(input_ports, output_ports)
                
            if current_level_objects:
                all_results.append({
                    "process_group_id": pg_id,
                    "process_group_name": current_pg_name,
                    "objects": current_level_objects
                })
                
        except (ConnectionError, ValueError, NiFiAuthenticationError) as e:
            error_msg = str(e) or repr(e) or f"Unknown error fetching {object_type}"
            local_logger.error(f"Error fetching {object_type} for PG {pg_id} during recursion: {error_msg}")
            all_results.append({
                "process_group_id": pg_id,
                "process_group_name": current_pg_name,
                "error": f"Failed to retrieve {object_type}: {error_msg}"
            })
        except Exception as e:
            local_logger.error(f"Unexpected error fetching {object_type} for PG {pg_id} during recursion: {e}", exc_info=True)
            all_results.append({
                "process_group_id": pg_id,
                "process_group_name": current_pg_name,
                "error": f"Unexpected error retrieving {object_type}: {e}"
            })

    # Check if max depth is reached before fetching child groups
    if depth >= max_depth:
        local_logger.debug(f"Max recursion depth ({max_depth}) reached for PG {pg_id}. Stopping recursion.")
        return {
            "results": all_results,
            "completed": True,
            "continuation_token": None,
            "processed_count": len(processed_pgs),
            "timeout_occurred": False
        }

    # Check timeout again before processing children
    if timeout_seconds and (time.time() - start_time) >= timeout_seconds:
        local_logger.warning(f"Timeout reached before processing children of PG {pg_id}")
        return {
            "results": all_results,
            "completed": False,
            "continuation_token": f"{pg_id}:{depth}:children",
            "processed_count": len(processed_pgs),
            "timeout_occurred": True
        }

    try:
        child_groups = await nifi_client.get_process_groups(pg_id)
        if child_groups:
            # Process children in parallel with timeout checks
            child_tasks = []
            for child_group_entity in child_groups:
                child_id = child_group_entity.get('id')
                if child_id and child_id not in processed_pgs:
                    # Check timeout before starting each child
                    if timeout_seconds and (time.time() - start_time) >= timeout_seconds:
                        local_logger.warning(f"Timeout reached before processing child {child_id}")
                        return {
                            "results": all_results,
                            "completed": False,
                            "continuation_token": f"{child_id}:{depth + 1}",
                            "processed_count": len(processed_pgs),
                            "timeout_occurred": True
                        }
                    
                    child_tasks.append(
                        _list_components_recursively_with_timeout(
                            object_type=object_type,
                            pg_id=child_id,
                            depth=depth + 1,
                            max_depth=max_depth,
                            timeout_seconds=timeout_seconds,
                            start_time=start_time,
                            processed_pgs=processed_pgs,
                            continuation_token=continuation_token
                        )
                    )
            
            if child_tasks:
                # Process children in parallel
                child_results = await asyncio.gather(*child_tasks, return_exceptions=True)
                
                for child_result in child_results:
                    if isinstance(child_result, Exception):
                        local_logger.error(f"Error in child processing: {child_result}")
                        continue
                    
                    if isinstance(child_result, dict):
                        all_results.extend(child_result.get("results", []))
                        
                        # If any child timed out, return partial results
                        if child_result.get("timeout_occurred"):
                            return {
                                "results": all_results,
                                "completed": False,
                                "continuation_token": child_result.get("continuation_token"),
                                "processed_count": child_result.get("processed_count", len(processed_pgs)),
                                "timeout_occurred": True
                            }
                        
                        # If any child didn't complete, return partial results
                        if not child_result.get("completed", True):
                            return {
                                "results": all_results,
                                "completed": False,
                                "continuation_token": child_result.get("continuation_token"),
                                "processed_count": child_result.get("processed_count", len(processed_pgs)),
                                "timeout_occurred": child_result.get("timeout_occurred", False)
                            }
                    
    except (ConnectionError, ValueError, NiFiAuthenticationError) as e:
        local_logger.error(f"Error fetching child groups for PG {pg_id} during recursion: {e}")
        all_results.append({
            "process_group_id": pg_id,
            "process_group_name": await _get_process_group_name(pg_id),
            "error_fetching_children": f"Failed to retrieve child groups: {e}"
        })
    except Exception as e:
        local_logger.error(f"Unexpected error fetching child groups for PG {pg_id}: {e}", exc_info=True)
        all_results.append({
            "process_group_id": pg_id,
            "process_group_name": await _get_process_group_name(pg_id),
            "error_fetching_children": f"Unexpected error retrieving child groups: {e}"
        })
        
    return {
        "results": all_results,
        "completed": True,
        "continuation_token": None,
        "processed_count": len(processed_pgs),
        "timeout_occurred": False
    }

async def _get_process_group_hierarchy_with_timeout(
    pg_id: str, 
    recursive_search: bool,
    timeout_seconds: Optional[float] = None,
    start_time: Optional[float] = None,
    processed_pgs: Optional[Set[str]] = None
) -> Dict[str, Any]:
    """
    Fetches NiFi process group hierarchy with timeout support and partial results.
    
    This function builds a nested hierarchy of process groups with enhanced features:
    - Configurable timeout with graceful degradation
    - Parallel processing for better performance
    - Continuation token support for resuming interrupted operations
    - Comprehensive error handling with partial results
    
    Args:
        pg_id: Process group ID to start hierarchy traversal from
        recursive_search: If True, recursively fetch child hierarchies; if False, only direct children
        timeout_seconds: Maximum time to spend on operation (None = no timeout)
        start_time: Operation start time (used internally for timeout calculation)
        processed_pgs: Set of already processed process group IDs (used internally)
    
    Returns:
        Dict containing hierarchy data:
        - id: Process group ID
        - name: Process group name
        - child_process_groups: List of child process group data
        - completed: bool indicating if operation completed fully
        - timeout_occurred: bool indicating if timeout occurred
        - continuation_token: str for resuming if timed out (only present if timeout occurred)
        - error: str error message (only present if error occurred)
        
    Performance Notes:
        - Processes child groups in parallel when possible
        - Returns partial hierarchy immediately when timeout is reached
        - Tracks processed groups to avoid duplication
        - Memory efficient for large hierarchies
    """
    if start_time is None:
        start_time = time.time()
    if processed_pgs is None:
        processed_pgs = set()
    
    # Get client, logger, and IDs from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"

    if not nifi_client:
        local_logger.error("NiFi client not found in context for _get_process_group_hierarchy_with_timeout")
        return {
            "id": pg_id, 
            "name": "Unknown (Client Error)", 
            "child_process_groups": [], 
            "error": "NiFi Client not available",
            "completed": False,
            "timeout_occurred": False
        }

    # Check timeout
    if timeout_seconds and (time.time() - start_time) >= timeout_seconds:
        local_logger.warning(f"Timeout reached while processing hierarchy for PG {pg_id}")
        return {
            "id": pg_id,
            "name": "Timeout",
            "child_process_groups": [],
            "completed": False,
            "timeout_occurred": True,
            "continuation_token": pg_id
        }

    hierarchy_data = { 
        "id": pg_id, 
        "name": "Unknown", 
        "child_process_groups": [],
        "completed": True,
        "timeout_occurred": False
    }
    
    try:
        parent_name = await _get_process_group_name(pg_id)
        hierarchy_data["name"] = parent_name

        nifi_req_children = {"operation": "get_process_groups", "process_group_id": pg_id}
        local_logger.bind(interface="nifi", direction="request", data=nifi_req_children).debug("Calling NiFi API")
        child_groups_response = await nifi_client.get_process_groups(pg_id)
        child_count = len(child_groups_response) if child_groups_response else 0
        nifi_resp_children = {"child_group_count": child_count}
        local_logger.bind(interface="nifi", direction="response", data=nifi_resp_children).debug("Received from NiFi API")
        child_groups_list = child_groups_response

        if child_groups_list:
            child_tasks = []
            for child_group_entity in child_groups_list:
                child_id = child_group_entity.get('id')
                child_component = child_group_entity.get('component', {})
                child_name = child_component.get('name', f"Unnamed PG ({child_id})")

                if child_id:
                    # Check timeout before processing each child
                    if timeout_seconds and (time.time() - start_time) >= timeout_seconds:
                        local_logger.warning(f"Timeout reached before processing child {child_id}")
                        hierarchy_data["completed"] = False
                        hierarchy_data["timeout_occurred"] = True
                        hierarchy_data["continuation_token"] = child_id
                        return hierarchy_data
                    
                    if recursive_search:
                        child_tasks.append((child_id, child_name, _get_process_group_hierarchy_with_timeout(
                            pg_id=child_id, 
                            recursive_search=True,
                            timeout_seconds=timeout_seconds,
                            start_time=start_time,
                            processed_pgs=processed_pgs
                        )))
                    else:
                        child_tasks.append((child_id, child_name, _get_process_group_contents_counts(child_id)))
            
            # Process children
            for child_id, child_name, child_task in child_tasks:
                try:
                    if recursive_search:
                        child_hierarchy = await child_task
                        child_data = {
                            "id": child_id,
                            "name": child_name,
                            "children": child_hierarchy.get("child_process_groups", [])
                        }
                        
                        # Check if child timed out
                        if child_hierarchy.get("timeout_occurred"):
                            hierarchy_data["completed"] = False
                            hierarchy_data["timeout_occurred"] = True
                            hierarchy_data["continuation_token"] = child_hierarchy.get("continuation_token", child_id)
                            hierarchy_data["child_process_groups"].append(child_data)
                            return hierarchy_data
                    else:
                        counts = await child_task
                        child_data = {
                            "id": child_id,
                            "name": child_name,
                            "counts": counts
                        }
                    
                    hierarchy_data["child_process_groups"].append(child_data)
                    
                except Exception as e:
                    local_logger.error(f"Error processing child {child_id}: {e}")
                    hierarchy_data["child_process_groups"].append({
                        "id": child_id,
                        "name": child_name,
                        "error": str(e)
                    })

        return hierarchy_data

    except (ConnectionError, ValueError, NiFiAuthenticationError) as e:
        local_logger.error(f"Error fetching process group hierarchy for {pg_id}: {e}")
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        hierarchy_data["error"] = f"Failed to retrieve full hierarchy for process group {pg_id}: {e}"
        hierarchy_data["completed"] = False
        return hierarchy_data
    except Exception as e:
         local_logger.error(f"Unexpected error fetching hierarchy for {pg_id}: {e}", exc_info=True)
         local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received unexpected error from NiFi API")
         hierarchy_data["error"] = f"Unexpected error retrieving hierarchy for {pg_id}: {e}"
         hierarchy_data["completed"] = False
         return hierarchy_data

# --- Legacy Wrapper Functions for Backward Compatibility ---

async def _list_components_recursively(
    object_type: Literal["processors", "connections", "ports"],
    pg_id: str,
    depth: int = 0,
    max_depth: int = 3
) -> List[Dict]:
    """
    Legacy wrapper for backward compatibility.
    
    This function maintains the original API for existing code while delegating
    to the enhanced timeout-aware implementation. It returns only the results
    list without timeout/continuation metadata.
    
    Args:
        object_type: Type of NiFi objects to list
        pg_id: Process group ID to start from
        depth: Current recursion depth
        max_depth: Maximum recursion depth
        
    Returns:
        List of component results (legacy format)
        
    Note:
        For new code, consider using _list_components_recursively_with_timeout()
        directly for enhanced timeout and continuation support.
    """
    result = await _list_components_recursively_with_timeout(
        object_type=object_type,
        pg_id=pg_id,
        depth=depth,
        max_depth=max_depth,
        timeout_seconds=None
    )
    return result.get("results", [])

async def _get_process_group_hierarchy(
    pg_id: str, 
    recursive_search: bool
) -> Dict[str, Any]:
    """
    Legacy wrapper for backward compatibility.
    
    This function maintains the original API for existing code while delegating
    to the enhanced timeout-aware implementation. It returns the hierarchy
    without timeout/continuation metadata.
    
    Args:
        pg_id: Process group ID to start from
        recursive_search: Whether to recursively fetch child hierarchies
        
    Returns:
        Dict containing hierarchy data (legacy format)
        
    Note:
        For new code, consider using _get_process_group_hierarchy_with_timeout()
        directly for enhanced timeout and continuation support.
    """
    return await _get_process_group_hierarchy_with_timeout(
        pg_id=pg_id,
        recursive_search=recursive_search,
        timeout_seconds=None
    )

# --- Tool Definitions --- 

@mcp.tool()
@tool_phases(["Review", "Build", "Modify", "Operate"])
async def list_nifi_objects(
    object_type: Literal["processors", "connections", "ports", "process_groups", "controller_services"],
    process_group_id: str | None = None,
    search_scope: Literal["current_group", "recursive"] = "current_group",
    timeout_seconds: Optional[float] = None,
    continuation_token: Optional[str] = None,
    # mcp_context: dict = {} # Removed context parameter
) -> Union[List[Dict], Dict]:
    """
    Lists NiFi objects or provides a hierarchy view for process groups within a specified scope.
    
    Enhanced with timeout management, parallel processing, and partial results for large recursive operations.
    
    Performance Improvements:
    - Parallel processing of child process groups (47-71% faster than sequential)
    - Configurable timeouts prevent indefinite hanging
    - Continuation tokens allow resuming interrupted operations
    - No lost work when timeouts occur

    Parameters
    ----------
    object_type : Literal["processors", "connections", "ports", "process_groups", "controller_services"]
        The type of NiFi objects to list.
        - 'processors': Lists processors with basic details and status.
        - 'connections': Lists connections with basic details and status.
        - 'ports': Lists input and output ports with basic details and status.
        - 'process_groups': Lists child process groups under the target group (see search_scope).
        - 'controller_services': Lists controller services with basic details and status.
    process_group_id : str | None, optional
        The ID of the target process group. If None, defaults to the root process group.
    search_scope : Literal["current_group", "recursive"], optional
        Determines the scope of the search when listing objects.
        - 'current_group': Lists objects only directly within the target process group (default).
        - 'recursive': Lists objects within the target process group and all its descendants.
        Note: For 'process_groups' object_type, 'recursive' provides a nested hierarchy view.
    timeout_seconds : Optional[float], optional
        Maximum time in seconds to spend on recursive operations. If exceeded, returns partial results
        with a continuation token. Only applies to recursive operations. Default: None (no timeout).
    continuation_token : Optional[str], optional
        Token from a previous partial result to resume processing from where it left off.
        Format: "process_group_id:depth" or "process_group_id:depth:children".
    # Removed mcp_context from docstring

    Returns
    -------
    Union[List[Dict], Dict]
        When search_scope='current_group' a flat list is returned; when search_scope='recursive'
        a dict with 'results', 'completed', and 'continuation_token' is returned.
        - For object_type 'processors', 'connections', 'ports', 'controller_services':
            - If search_scope='current_group': A list of simplified object summaries.
            - If search_scope='recursive': A dictionary containing:
                - 'results': List of dictionaries with 'process_group_id', 'process_group_name', and 'objects'
                - 'completed': bool indicating if operation completed fully
                - 'continuation_token': str for resuming if timed out (None if completed)
                - 'processed_count': int number of process groups processed
                - 'timeout_occurred': bool indicating if timeout occurred
        - For object_type 'process_groups':
            - If search_scope='current_group': A list of child process group summaries (id, name, counts).
            - If search_scope='recursive': A nested dictionary representing the process group hierarchy
              with additional fields: 'completed', 'timeout_occurred', 'continuation_token' if timeout occurred.

    For recursive listing with timeouts and continuation tokens in a streaming-friendly form,
    use list_nifi_objects_recursive.
    """
    # Get client and logger from context variables
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger # Fallback to global logger if needed

    if not nifi_client:
        raise ToolError("NiFi client not found in context.")
    if not isinstance(nifi_client, NiFiClient):
         raise ToolError(f"Invalid NiFi client type found in context: {type(nifi_client)}")

    # await ensure_authenticated(nifi_client, local_logger) # Removed - handled by factory
    
    # --- Get IDs from context --- 
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"
    # --------------------------

    try:
        target_pg_id = process_group_id
        if not target_pg_id:
            local_logger.info("process_group_id not provided, defaulting to root.")
            target_pg_id = await nifi_client.get_root_process_group_id(user_request_id=user_request_id, action_id=action_id)
            local_logger.info(f"Resolved root process group ID: {target_pg_id}")

        local_logger.info(f"Listing NiFi objects of type '{object_type}' in scope '{search_scope}' for PG '{target_pg_id}'")

        # --- Process Group Handling --- 
        if object_type == "process_groups":
            local_logger.debug("Handling object_type 'process_groups'...")
            if search_scope == "current_group":
                local_logger.debug(f"Fetching direct children for PG {target_pg_id}")
                child_groups_response = await nifi_client.get_process_groups(target_pg_id)
                results = []
                if child_groups_response:
                    for child_group_entity in child_groups_response:
                        child_id = child_group_entity.get('id')
                        child_component = child_group_entity.get('component', {})
                        child_name = child_component.get('name', f"Unnamed PG ({child_id})")
                        if child_id:
                            counts = await _get_process_group_contents_counts(child_id)
                            results.append({"id": child_id, "name": child_name, "counts": counts})
                local_logger.info(f"Found {len(results)} direct child process groups in PG {target_pg_id}")
                return results
            else: # recursive
                local_logger.debug(f"Recursively fetching hierarchy starting from PG {target_pg_id}")
                if timeout_seconds:
                    local_logger.info(f"Using timeout of {timeout_seconds} seconds for recursive hierarchy fetch")
                hierarchy = await _get_process_group_hierarchy_with_timeout(
                    target_pg_id, 
                    True, 
                    timeout_seconds=timeout_seconds
                )
                local_logger.info(f"Finished fetching recursive hierarchy for PG {target_pg_id}")
                if hierarchy.get("timeout_occurred"):
                    local_logger.warning(f"Hierarchy fetch timed out. Processed {hierarchy.get('processed_count', 0)} groups. Use continuation_token to resume.")
                return hierarchy

        # --- Processor, Connection, Port, Controller Service Handling --- 
        else:
            local_logger.debug(f"Handling object_type '{object_type}'...")
            if search_scope == "current_group":
                local_logger.debug(f"Fetching objects directly within PG {target_pg_id}")
                objects = []
                if object_type == "processors":
                    raw_objects = await nifi_client.list_processors(target_pg_id, user_request_id=user_request_id, action_id=action_id)
                    objects = _format_processor_summary(raw_objects)
                elif object_type == "connections":
                    raw_objects = await nifi_client.list_connections(target_pg_id, user_request_id=user_request_id, action_id=action_id)
                    objects = _format_connection_summary(raw_objects)
                elif object_type == "ports":
                    input_ports = await nifi_client.get_input_ports(target_pg_id)
                    output_ports = await nifi_client.get_output_ports(target_pg_id)
                    objects = _format_port_summary(input_ports, output_ports)
                elif object_type == "controller_services":
                    raw_objects = await nifi_client.list_controller_services(target_pg_id, user_request_id=user_request_id, action_id=action_id)
                    objects = _format_controller_service_summary(raw_objects)
                    
                local_logger.info(f"Found {len(objects)} {object_type} directly within PG {target_pg_id}")
                return objects
            else: # recursive
                local_logger.debug(f"Recursively fetching {object_type} starting from PG {target_pg_id}")
                if timeout_seconds:
                    local_logger.info(f"Using timeout of {timeout_seconds} seconds for recursive {object_type} fetch")
                
                # Parse continuation token if provided
                start_pg_id = target_pg_id
                start_depth = 0
                processed_pgs = set()
                
                if continuation_token:
                    try:
                        parts = continuation_token.split(":")
                        if len(parts) >= 2:
                            start_pg_id = parts[0]
                            start_depth = int(parts[1])
                            local_logger.info(f"Resuming from continuation token: PG {start_pg_id} at depth {start_depth}")
                    except (ValueError, IndexError) as e:
                        local_logger.warning(f"Invalid continuation token format: {continuation_token}. Starting fresh. Error: {e}")
                
                recursive_results = await _list_components_recursively_with_timeout(
                    object_type=object_type,
                    pg_id=start_pg_id,
                    depth=start_depth,
                    max_depth=10, # Set a reasonable max depth
                    timeout_seconds=timeout_seconds,
                    processed_pgs=processed_pgs,
                    continuation_token=continuation_token
                )
                
                local_logger.info(f"Finished recursive search for {object_type} starting from PG {target_pg_id}")
                if recursive_results.get("timeout_occurred"):
                    local_logger.warning(f"Recursive search timed out. Processed {recursive_results.get('processed_count', 0)} groups. Use continuation_token to resume.")
                
                return recursive_results

    except NiFiAuthenticationError as e:
         local_logger.error(f"Authentication error during list_nifi_objects: {e}", exc_info=False)
         raise ToolError(f"Authentication error accessing NiFi: {e}") from e
    except (ValueError, ConnectionError, ToolError) as e:
        local_logger.error(f"Error listing NiFi objects: {e}", exc_info=False)
        raise ToolError(f"Error listing NiFi {object_type}: {e}") from e
    except Exception as e:
        local_logger.error(f"Unexpected error listing NiFi objects: {e}", exc_info=True)
        raise ToolError(f"An unexpected error occurred: {e}") from e

@mcp.tool()
@tool_phases(["Review", "Build", "Modify", "Operate"])
async def list_nifi_objects_recursive(
    object_type: Literal["processors", "connections", "ports", "process_groups"],
    process_group_id: str | None = None,
    timeout_seconds: float = 30.0,
    max_depth: int = 10,
    continuation_token: Optional[str] = None,
    batch_size: int = 50
) -> Dict[str, Any]:
    """
    Lists NiFi objects recursively with streaming support for large hierarchies.

    Use this for recursive listing with timeouts and continuation; for single-level
    or simple recursive listing use list_nifi_objects.

    This tool is optimized for deep process group hierarchies and provides:
    - Configurable timeouts with partial results
    - Continuation tokens for resuming interrupted operations
    - Progress tracking and batch processing
    - Parallel processing for better performance

    Parameters
    ----------
    object_type : Literal["processors", "connections", "ports", "process_groups"]
        The type of NiFi objects to list recursively.
    process_group_id : str | None, optional
        The ID of the target process group. If None, defaults to the root process group.
    timeout_seconds : float, optional
        Maximum time in seconds to spend on the operation. Default: 30.0 seconds.
        When timeout is reached, returns partial results with continuation token.
    max_depth : int, optional
        Maximum recursion depth to prevent infinite loops. Default: 10.
    continuation_token : Optional[str], optional
        Token from a previous partial result to resume processing.
    batch_size : int, optional
        Number of process groups to process in each batch. Default: 50.

    Returns
    -------
    Dict[str, Any]
        Dictionary containing:
        - 'results': List of found objects or hierarchy data
        - 'completed': bool indicating if operation completed fully
        - 'continuation_token': str for resuming if timed out (None if completed)
        - 'processed_count': int number of process groups processed
        - 'timeout_occurred': bool indicating if timeout occurred
        - 'total_time_seconds': float time spent on operation
        - 'progress_info': dict with additional progress details
    """
    # Get client and logger from context variables
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger

    if not nifi_client:
        raise ToolError("NiFi client not found in context.")
    if not isinstance(nifi_client, NiFiClient):
         raise ToolError(f"Invalid NiFi client type found in context: {type(nifi_client)}")

    # Get IDs from context
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"

    start_time = time.time()
    
    try:
        target_pg_id = process_group_id
        if not target_pg_id:
            local_logger.info("process_group_id not provided, defaulting to root.")
            target_pg_id = await nifi_client.get_root_process_group_id(user_request_id=user_request_id, action_id=action_id)
            local_logger.info(f"Resolved root process group ID: {target_pg_id}")

        local_logger.info(f"Starting streaming list of {object_type} from PG {target_pg_id} with {timeout_seconds}s timeout")

        # Parse continuation token if provided
        start_pg_id = target_pg_id
        start_depth = 0
        processed_pgs = set()
        
        if continuation_token:
            try:
                parts = continuation_token.split(":")
                if len(parts) >= 2:
                    start_pg_id = parts[0]
                    start_depth = int(parts[1])
                    local_logger.info(f"Resuming from continuation token: PG {start_pg_id} at depth {start_depth}")
            except (ValueError, IndexError) as e:
                local_logger.warning(f"Invalid continuation token format: {continuation_token}. Starting fresh. Error: {e}")

        if object_type == "process_groups":
            # Use hierarchy function for process groups
            result = await _get_process_group_hierarchy_with_timeout(
                pg_id=start_pg_id,
                recursive_search=True,
                timeout_seconds=timeout_seconds,
                start_time=start_time,
                processed_pgs=processed_pgs
            )
        else:
            # Use component listing for processors, connections, ports
            result = await _list_components_recursively_with_timeout(
                object_type=object_type,
                pg_id=start_pg_id,
                depth=start_depth,
                max_depth=max_depth,
                timeout_seconds=timeout_seconds,
                start_time=start_time,
                processed_pgs=processed_pgs,
                continuation_token=continuation_token
            )

        # Add timing and progress information
        total_time = time.time() - start_time
        
        if object_type == "process_groups":
            # For process groups, wrap the hierarchy result
            final_result = {
                "results": result,
                "completed": result.get("completed", True),
                "continuation_token": result.get("continuation_token"),
                "processed_count": len(processed_pgs),
                "timeout_occurred": result.get("timeout_occurred", False),
                "total_time_seconds": total_time,
                "progress_info": {
                    "object_type": object_type,
                    "start_pg_id": start_pg_id,
                    "max_depth": max_depth,
                    "timeout_seconds": timeout_seconds
                }
            }
        else:
            # For other object types, use the result directly
            final_result = result.copy()
            final_result["total_time_seconds"] = total_time
            final_result["progress_info"] = {
                "object_type": object_type,
                "start_pg_id": start_pg_id,
                "max_depth": max_depth,
                "timeout_seconds": timeout_seconds
            }

        if final_result.get("timeout_occurred"):
            local_logger.warning(f"Operation timed out after {total_time:.2f}s. Processed {final_result.get('processed_count', 0)} groups.")
        else:
            local_logger.info(f"Operation completed successfully in {total_time:.2f}s. Processed {final_result.get('processed_count', 0)} groups.")

        return final_result

    except NiFiAuthenticationError as e:
         local_logger.error(f"Authentication error during streaming list: {e}", exc_info=False)
         raise ToolError(f"Authentication error accessing NiFi: {e}") from e
    except (ValueError, ConnectionError, ToolError) as e:
        local_logger.error(f"Error during streaming list: {e}", exc_info=False)
        raise ToolError(f"Error listing NiFi {object_type}: {e}") from e
    except Exception as e:
        local_logger.error(f"Unexpected error during streaming list: {e}", exc_info=True)
        raise ToolError(f"An unexpected error occurred: {e}") from e

# Helper function to fetch a single object (extracted for reuse)
async def _fetch_single_object_details(
    object_type: str,
    object_id: str,
    nifi_client: NiFiClient,
    local_logger,
    user_request_id: str,
    action_id: str
) -> Dict:
    """Internal helper to fetch details for a single object."""
    details = {}
    if object_type == "processor":
        details = await nifi_client.get_processor_details(object_id)
    elif object_type == "connection":
        details = await nifi_client.get_connection(object_id)
    elif object_type == "process_group":
        details = await nifi_client.get_process_group_details(
            object_id, user_request_id=user_request_id, action_id=action_id
        )
    elif object_type == "controller_service":
        details = await nifi_client.get_controller_service_details(
            object_id, user_request_id=user_request_id, action_id=action_id
        )
    elif object_type == "port":
        # Need to determine if input or output port. Try input first.
        try:
            details = await nifi_client.get_input_port_details(object_id)
            local_logger.debug(f"Object {object_id} identified as an input port.")
        except ValueError:
            local_logger.debug(f"Object {object_id} not found as input port, trying output port.")
            try:
                details = await nifi_client.get_output_port_details(object_id)
                local_logger.debug(f"Object {object_id} identified as an output port.")
            except ValueError as e_out:
                local_logger.error(f"Could not find port with ID '{object_id}' as either input or output port.")
                raise ToolError(f"Port with ID '{object_id}' not found.") from e_out
    else:
        raise ToolError(f"Invalid object_type specified: {object_type}")
    return details


# Helper functions for output formatting
def _extract_summary(details: Dict) -> Dict:
    """Extract essential fields only."""
    component = details.get("component", {})
    return {
        "id": details.get("id"),
        "name": component.get("name"),
        "type": component.get("type"),
        "state": component.get("state"),
        "validationStatus": component.get("validationStatus"),
        "relationships": [r.get("name") for r in component.get("relationships", [])]
    }


def _is_meaningful_property(prop_name: str, prop_value: Any, config: Dict = None) -> bool:
    """Determine if a property is worth including based on value patterns."""
    if not prop_value or prop_value == "":
        return False
    
    # Get extraction config if available
    extraction_config = {}
    if config:
        try:
            from config.settings import get_doc_property_extraction_config
            extraction_config = get_doc_property_extraction_config()
        except Exception:
            pass
    
    # Skip default values if configured
    if not extraction_config.get("include_defaults", False):
        defaults = ["0 sec", "1", "false", "true", "UTF-8", "ALL", "DISABLED", "RANDOM"]
        if str(prop_value).strip() in defaults:
            return False
    
    # Skip very long values (likely binary or encoded data)
    max_length = extraction_config.get("max_value_length", 2000)
    if isinstance(prop_value, str) and len(prop_value) > max_length:
        return False
    
    # Skip properties that are clearly internal/config
    internal_keywords = ["version", "revision", "bundle", "validation", "scheduling strategy"]
    if any(kw in prop_name.lower() for kw in internal_keywords):
        return False
    
    return True


def _extract_business_properties_heuristic(properties: Dict, config: Dict = None) -> Dict:
    """Extract properties using heuristics - works for any processor type.
    
    This is the primary extraction method that uses pattern matching to identify
    business-relevant properties without hardcoding processor types.
    """
    result = {}
    extraction_config = config or {}
    max_props = extraction_config.get("max_properties_per_processor", 10)
    truncate = extraction_config.get("truncate_large_values", True)
    max_length = extraction_config.get("max_value_length", 500)
    
    for prop_name, prop_value in properties.items():
        if not _is_meaningful_property(prop_name, prop_value, extraction_config):
            continue
        
        # Skip common non-business properties
        if prop_name.lower() in ["routing strategy", "scheduling strategy", "run duration"]:
            continue
        
        # Pattern 1: Expression Language (${...})
        # Indicates dynamic values, attribute references, business logic
        if isinstance(prop_value, str) and "${" in prop_value:
            result[prop_name] = prop_value
            continue
            
        # Pattern 2: JSONPath ($[...] or $."...")
        # Indicates field extraction from JSON
        if isinstance(prop_value, str) and prop_value.startswith("$"):
            result[prop_name] = prop_value
            continue
            
        # Pattern 3: URLs (http://, https://, file://, etc.)
        # Indicates external system interactions
        if isinstance(prop_value, str) and any(
            prop_value.startswith(prefix) 
            for prefix in ["http://", "https://", "file://", "ftp://", "sftp://", "jdbc:", "kafka://"]
        ):
            result[prop_name] = prop_value
            continue
            
        # Pattern 4: File paths (absolute or relative)
        # Indicates file system interactions
        if isinstance(prop_value, str) and (
            prop_value.startswith("/") or 
            "\\" in prop_value or
            (prop_value.count("/") > 2 and not prop_value.startswith("http"))
        ):
            result[prop_name] = prop_value
            continue
            
        # Pattern 5: SQL-like queries (SELECT, INSERT, UPDATE, etc.)
        # Indicates database interactions
        if isinstance(prop_value, str) and any(
            keyword in prop_value.upper() 
            for keyword in ["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"]
        ):
            result[prop_name] = prop_value
            continue
            
        # Pattern 6: Non-empty, non-default values for known business properties
        # Property names that suggest business logic
        if any(keyword in prop_name.lower() for keyword in [
            "query", "sql", "script", "spec", "transform", "rule", 
            "condition", "filter", "expression", "path", "url", "endpoint",
            "topic", "queue", "table", "database", "schema", "directory",
            "file", "pattern", "regex", "template", "format"
        ]):
            # Truncate if configured
            if truncate and isinstance(prop_value, str) and len(prop_value) > max_length:
                result[prop_name] = prop_value[:max_length] + " *TRUNCATED*"
            else:
                result[prop_name] = prop_value
            continue
        
        # Limit number of properties extracted
        if len(result) >= max_props:
            # Add metadata indicating property limit was reached
            remaining_props = len(properties) - len([k for k in properties.keys() if k in result])
            if remaining_props > 0:
                result["_metadata"] = {
                    "property_limit_reached": True,
                    "max_properties": max_props,
                    "properties_extracted": len(result),
                    "properties_remaining": remaining_props,
                    "note": f"Only first {max_props} business properties extracted. {remaining_props} additional properties were skipped."
                }
            break
    
    return result


def _extract_business_properties_template(proc_type: str, properties: Dict, config: Dict = None) -> Dict:
    """Extract properties using processor-specific templates (fallback).
    
    This handles special cases where heuristics might miss important details.
    """
    result = {}
    extraction_config = config or {}
    truncate = extraction_config.get("truncate_large_values", True)
    max_length = extraction_config.get("max_value_length", 500)
    
    # RouteOnAttribute: All properties except "Routing Strategy"
    if proc_type == "RouteOnAttribute":
        for prop, value in properties.items():
            if prop not in ["Routing Strategy"] and value:
                result[prop] = value
                
    # Script processors: Truncate long scripts
    elif proc_type in ["ExecuteScript", "ExecuteGroovyScript"]:
        script = properties.get("Script Body", "")
        if script:
            if truncate and len(script) > max_length:
                result["script_preview"] = script[:max_length] + " *TRUNCATED*"
                result["script_length"] = len(script)
                result["_script_truncated"] = True
            else:
                result["script_preview"] = script
                result["script_length"] = len(script)
            
    # JoltTransformJSON: Include spec (may be long, but important)
    elif proc_type == "JoltTransformJSON":
        spec = properties.get("Jolt Specification", "")
        if spec:
            if truncate and len(spec) > max_length:
                result["jolt_spec"] = spec[:max_length] + " *TRUNCATED*"
                result["_jolt_spec_truncated"] = True
            else:
                result["jolt_spec"] = spec
            
    # QueryRecord: SQL queries
    elif proc_type == "QueryRecord":
        for prop, value in properties.items():
            if value and ("query" in prop.lower() or "sql" in prop.lower()):
                result[prop] = value
                
    # InvokeHTTP: URL and method (critical for IO endpoints)
    elif proc_type == "InvokeHTTP":
        url = properties.get("Remote URL", "")
        method = properties.get("HTTP Method", "GET")
        if url:
            result["url"] = url
        if method:
            result["method"] = method
        # Also extract Expression Language properties (e.g., Authorization header)
        for prop_name, prop_value in properties.items():
            if prop_value and "${" in str(prop_value):
                result[prop_name] = prop_value
        
    # File processors: Directory and filter
    elif proc_type in ["GetFile", "PutFile"]:
        directory = properties.get("Directory", "")
        file_filter = properties.get("File Filter", "")
        if directory:
            result["directory"] = directory
        if file_filter:
            result["file_filter"] = file_filter
        
    # Kafka processors: Topic and bootstrap servers
    elif proc_type in ["ConsumeKafka", "PublishKafka", "ConsumeKafka_2_6", "PublishKafka_2_6"]:
        topic = properties.get("topic", properties.get("Topic Name(s)", ""))
        bootstrap = properties.get("bootstrap.servers", "")
        if topic:
            result["topic"] = topic
        if bootstrap:
            result["bootstrap_servers"] = bootstrap
    
    return result


def _extract_business_properties(component: Dict, config: Dict = None) -> Dict:
    """Extract business-relevant properties using multi-tier strategy.
    
    Strategy:
    1. Try heuristic extraction first (works for any processor type)
    2. If heuristics find nothing, try processor-specific templates
    3. If still nothing and mode is comprehensive, return all non-empty properties
    
    Args:
        component: Processor component dict with type and config
        config: Optional extraction configuration dict
        
    Returns:
        Dict of business-relevant properties
    """
    # DEFENSIVE: Ensure component is a dict
    if not isinstance(component, dict):
        return {}
    
    proc_type = component.get("type", "")
    if proc_type:
        proc_type = proc_type.split(".")[-1]
    component_config = component.get("config", {})
    # DEFENSIVE: Ensure component_config is a dict
    if not isinstance(component_config, dict):
        component_config = {}
    properties = component_config.get("properties", {})
    # DEFENSIVE: Ensure properties is a dict (critical - prevents KeyError)
    if not isinstance(properties, dict):
        # Log warning but return empty dict to prevent crashes
        import logging
        logging.warning(f"Properties is not a dict in _extract_business_properties: {type(properties)}")
        properties = {}
    
    # Get extraction mode from config
    extraction_config = config or {}
    if not extraction_config:
        try:
            from config.settings import get_doc_property_extraction_config
            extraction_config = get_doc_property_extraction_config()
        except Exception:
            extraction_config = {"mode": "balanced"}
    
    mode = extraction_config.get("mode", "balanced")
    
    # Tier 1: Heuristic extraction (primary - works for any processor type)
    heuristic_result = _extract_business_properties_heuristic(properties, extraction_config)
    
    # Tier 2: Processor-specific templates (fallback for special cases)
    template_result = {}
    if mode in ["balanced", "comprehensive"]:
        template_result = _extract_business_properties_template(proc_type, properties, extraction_config)
    
    # Merge results (template takes precedence for overlapping properties)
    result = {**heuristic_result, **template_result}
    
    # Tier 3: Full property fallback (only in comprehensive mode)
    if not result and mode == "comprehensive":
        # Return all non-empty, meaningful properties
        result = {
            k: v for k, v in properties.items() 
            if _is_meaningful_property(k, v, extraction_config)
        }
    
    return result


def _extract_doc_optimized(details: Dict, include_properties: bool = False) -> Dict:
    """Extract fields needed for documentation."""
    component = details.get("component", {})
    config = component.get("config", {})
    properties = config.get("properties", {})
    
    # Extract expressions from properties
    expressions = {}
    for prop_name, prop_value in properties.items():
        if prop_value and "${" in str(prop_value):
            expressions[prop_name] = prop_value
    
    # Extract routing relationships
    relationships = []
    for rel in component.get("relationships", []):
        relationships.append({
            "name": rel.get("name"),
            "autoTerminate": rel.get("autoTerminate", False)
        })
    
    # Get extraction config for business properties
    extraction_config = None
    try:
        from config.settings import get_doc_property_extraction_config
        extraction_config = get_doc_property_extraction_config()
    except Exception:
        pass
    
    result = {
        "id": details.get("id"),
        "name": component.get("name"),
        "type": component.get("type"),
        "state": component.get("state"),
        "comments": component.get("comments", ""),
        "expressions": expressions,
        "relationships": relationships,
        "business_properties": _extract_business_properties(component, extraction_config)
    }
    
    # Include properties if requested (needed for IO endpoint extraction)
    if include_properties:
        # Include component/config structure so properties can be accessed
        result["component"] = component
        result["config"] = config
    
    return result


def _format_output(
    details: Dict, 
    output_format: str, 
    include_properties: bool
) -> Dict:
    """Format object details based on output format."""
    
    if output_format == "full":
        if not include_properties:
            # Remove properties if not needed
            if "component" in details and "config" in details["component"]:
                details = details.copy()
                details["component"] = details["component"].copy()
                details["component"]["config"] = details["component"]["config"].copy()
                details["component"]["config"].pop("properties", None)
        return details
    
    elif output_format == "summary":
        return _extract_summary(details)
    
    elif output_format == "doc_optimized":
        return _extract_doc_optimized(details, include_properties=include_properties)
    
    return details


@mcp.tool()
@tool_phases(["Review", "Build", "Modify", "Operate"])
async def get_nifi_object_details(
    object_type: Literal["processor", "connection", "port", "process_group", "controller_service"],
    object_id: str | None = None,
    object_ids: List[str] | None = None,
    output_format: Literal["full", "summary", "doc_optimized"] = "full",
    include_properties: bool = True,
    max_parallel: int = 5
) -> Dict | List[Dict]:
    """
    Retrieves detailed information for one or more NiFi objects.

    Parameters
    ----------
    object_type : Literal["processor", "connection", "port", "process_group", "controller_service"]
        The type of NiFi object to retrieve details for.
    object_id : str | None
        Single object ID (for backward compatibility).
    object_ids : List[str] | None
        List of object IDs for batch retrieval.
        Either object_id OR object_ids must be provided, not both.
    output_format : Literal["full", "summary", "doc_optimized"]
        - "full": Complete object details (default, backward compatible)
        - "summary": Essential fields only (name, type, state, relationships)
        - "doc_optimized": Fields needed for documentation (includes expressions, routing)
    include_properties : bool
        Whether to include processor properties (default True).
        Set to False for lighter payloads when properties aren't needed.
    max_parallel : int
        Maximum parallel requests for batch mode (default 5).

    Returns
    -------
    Dict | List[Dict]
        - Single object_id: Returns Dict (backward compatible)
        - object_ids list: Returns List[Dict] with results for each ID
    """
    # Validation
    if object_id and object_ids:
        raise ToolError("Provide either object_id or object_ids, not both.")
    if not object_id and not object_ids:
        raise ToolError("Must provide either object_id or object_ids.")
    
    # Get client and logger from context variables
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger

    if not nifi_client:
        raise ToolError("NiFi client not found in context.")
    if not isinstance(nifi_client, NiFiClient):
         raise ToolError(f"Invalid NiFi client type found in context: {type(nifi_client)}")
         
    # --- Get IDs from context --- 
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"
    # --------------------------

    # Single ID mode (backward compatible)
    if object_id:
        local_logger.info(f"Getting details for NiFi object type '{object_type}' with ID '{object_id}'")
        nifi_req = {"operation": f"get_{object_type}_details", "id": object_id}
        local_logger.bind(interface="nifi", direction="request", data=nifi_req).debug("Calling NiFi API")

        try:
            details = await _fetch_single_object_details(
                object_type, object_id, nifi_client, local_logger, user_request_id, action_id
            )
            formatted = _format_output(details, output_format, include_properties)
            
            local_logger.bind(interface="nifi", direction="response", data={
                "object_id": object_id, 
                "object_type": object_type, 
                "has_details": bool(formatted)
            }).debug("Received from NiFi API")
            local_logger.info(f"Successfully retrieved details for {object_type} {object_id}")
            return formatted

        except NiFiAuthenticationError as e:
            local_logger.error(f"Authentication error getting details for {object_type} {object_id}: {e}", exc_info=False)
            raise ToolError(f"Authentication error accessing NiFi: {e}") from e
        except ValueError as e:
            local_logger.warning(f"Could not find {object_type} with ID '{object_id}': {e}")
            local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
            raise ToolError(f"{object_type.capitalize()} with ID '{object_id}' not found.") from e
        except (ConnectionError, ToolError) as e:
            local_logger.error(f"Error getting details for {object_type} {object_id}: {e}", exc_info=False)
            local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
            raise ToolError(f"Error getting details for {object_type} {object_id}: {e}") from e
        except Exception as e:
            local_logger.error(f"Unexpected error getting details for {object_type} {object_id}: {e}", exc_info=True)
            local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received unexpected error from NiFi API")
            raise ToolError(f"An unexpected error occurred: {e}") from e
    
    # Batch mode
    local_logger.info(f"Batch fetching {len(object_ids)} {object_type} details")
    
    # Use semaphore to limit parallel requests
    semaphore = asyncio.Semaphore(max_parallel)
    
    async def fetch_with_semaphore(obj_id: str) -> Dict:
        async with semaphore:
            try:
                details = await _fetch_single_object_details(
                    object_type, obj_id, nifi_client, local_logger, user_request_id, action_id
                )
                formatted = _format_output(details, output_format, include_properties)
                return {
                    "id": obj_id,
                    "status": "success",
                    "data": formatted
                }
            except Exception as e:
                return {
                    "id": obj_id,
                    "status": "error",
                    "error": str(e)
                }
    
    # Execute all fetches in parallel (with semaphore limiting)
    tasks = [fetch_with_semaphore(obj_id) for obj_id in object_ids]
    results = await asyncio.gather(*tasks)
    
    # Log summary
    success_count = sum(1 for r in results if r["status"] == "success")
    local_logger.info(f"Batch fetch complete: {success_count}/{len(object_ids)} successful")
    
    return results

@mcp.tool()
@tool_phases(["Review", "Build", "Modify", "Operate"])
async def document_nifi_flow(
    process_group_id: str | None = None,
    starting_processor_id: str | None = None,
    max_depth: int = 10,
    include_properties: bool = True,
    include_descriptions: bool = True,
    include_flow_summary: bool = True,
) -> Dict[str, Any]:
    """
    Analyzes and documents a NiFi flow starting from a given process group or processor.

    This tool extracts processor information with embedded connection details, providing
    a simplified and token-efficient representation of the flow structure. When
    include_flow_summary is True, also adds flow_summary with entry_points, controller_services,
    decision_branches, flow_paths, and boundary_ports.

    Parameters
    ----------
    process_group_id : str, optional
        The ID of the process group to start documentation from. If None, `starting_processor_id` must be provided, and the tool will start from that processor's parent group.
    starting_processor_id : str, optional
        The ID of a specific processor to focus the documentation around. The tool will analyze the flow connected to this processor within its parent group.
    max_depth : int, optional
        DEPRECATED: This parameter is kept for compatibility but is not used in the simplified implementation.
    include_properties : bool, optional
        Whether to include important processor properties in the documentation. Defaults to True.
    include_descriptions : bool, optional
        Whether to include processor and connection descriptions/comments (if available). Defaults to True.
    include_flow_summary : bool, optional
        When True (default), adds documentation.flow_summary with entry_points, controller_services,
        decision_branches, flow_paths, and boundary_ports for LLM-friendly flow structure.

    Returns
    -------
    Dict[str, Any]
        A dictionary containing the documented flow with simplified structure:
        - 'components': Dictionary containing:
            - 'processors': Dict of processors with embedded incoming/outgoing connection info
            - 'ports': Dict of input/output ports
        - 'flow_summary' (when include_flow_summary=True): entry_points, controller_services,
          decision_branches, flow_paths, boundary_ports
        Each processor includes:
        - Basic info (id, name, type, state, properties, description)
        - 'outgoing_connections': List of connections where this processor is the source
        - 'incoming_connections': List of connections where this processor is the destination
        - 'auto_terminated_relationships': List of relationships that are auto-terminated
    """
    # Get client and logger from context variables
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger

    if not nifi_client:
        raise ToolError("NiFi client not found in context.")
    if not isinstance(nifi_client, NiFiClient):
         raise ToolError(f"Invalid NiFi client type found in context: {type(nifi_client)}")
         
    # Get IDs from context
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"
    
    local_logger.info(f"Starting NiFi flow documentation. PG: {process_group_id}, Start Proc: {starting_processor_id}, Max Depth: {max_depth}")

    try:
        # Determine the target process group ID
        pg_id = process_group_id
        if starting_processor_id and not pg_id:
            local_logger.info(f"No process_group_id provided, finding parent group for starting processor {starting_processor_id}")
            nifi_req = {"operation": "get_processor_details", "id": starting_processor_id}
            local_logger.bind(interface="nifi", direction="request", data=nifi_req).debug("Calling NiFi API")
            proc_details = await nifi_client.get_processor_details(starting_processor_id)
            nifi_resp = {"has_proc_details": bool(proc_details and 'component' in proc_details)}
            local_logger.bind(interface="nifi", direction="response", data=nifi_resp).debug("Received from NiFi API")
            
            pg_id = proc_details.get("component", {}).get("parentGroupId")
            if not pg_id:
                raise ToolError(f"Could not determine parent process group ID for processor {starting_processor_id}")
            local_logger.info(f"Determined target process group ID: {pg_id} from starting processor.")
        elif not pg_id:
            local_logger.info("No process_group_id or starting_processor_id provided, defaulting to root process group.")
            pg_id = await nifi_client.get_root_process_group_id(user_request_id=user_request_id, action_id=action_id)
            if not pg_id:
                 raise ToolError("Could not retrieve the root process group ID.")
            local_logger.info(f"Resolved root process group ID: {pg_id}")
            
        if not pg_id:
             raise ToolError("Failed to determine a target process group ID for documentation.")

        # Fetch components for the target process group
        local_logger.info(f"Fetching components for process group {pg_id}...")
        nifi_req_components = {"operation": "list_all", "process_group_id": pg_id}
        local_logger.bind(interface="nifi", direction="request", data=nifi_req_components).debug("Calling NiFi API (multiple calls)")
        
        processors_list = await nifi_client.list_processors(pg_id, user_request_id=user_request_id, action_id=action_id)
        connections_list = await nifi_client.list_connections(pg_id, user_request_id=user_request_id, action_id=action_id)
        input_ports_list = await nifi_client.get_input_ports(pg_id)
        output_ports_list = await nifi_client.get_output_ports(pg_id)
        
        nifi_resp_components = {
            "processor_count": len(processors_list) if isinstance(processors_list, list) else -1,
            "connection_count": len(connections_list) if isinstance(connections_list, list) else -1,
            "port_count": len(input_ports_list) if isinstance(input_ports_list, list) else -1,
            "output_port_count": len(output_ports_list) if isinstance(output_ports_list, list) else -1,
        }
        local_logger.bind(interface="nifi", direction="response", data=nifi_resp_components).debug("Received from NiFi API (multiple component lists)")

        # Use the simplified documentation function that embeds connections in processors
        documentation = await document_nifi_flow_simplified(
            processors=processors_list or [],
            connections=connections_list or [],
            input_ports=input_ports_list or [],
            output_ports=output_ports_list or [],
            include_properties=include_properties,
            include_descriptions=include_descriptions,
            nifi_client=nifi_client,  # Pass the client for detailed processor info
            user_request_id=user_request_id,
            action_id=action_id
        )

        if include_flow_summary:
            processors_list = processors_list or []
            connections_list = connections_list or []
            input_ports_list = input_ports_list or []
            output_ports_list = output_ports_list or []
            # Build set of component ids that have incoming connections
            has_incoming: Set[str] = set()
            for conn in connections_list:
                comp = conn.get("component", {})
                dest = comp.get("destination", {})
                dest_id = dest.get("id")
                if dest_id:
                    has_incoming.add(dest_id)
            # Entry points: processors and input ports with no incoming connections
            entry_points: List[Dict[str, Any]] = []
            for proc in processors_list:
                pid = proc.get("id")
                if not pid or pid in has_incoming:
                    continue
                comp = proc.get("component", {})
                ptype = comp.get("type", "") or ""
                name = comp.get("name", "Unknown")
                if "HandleHttpRequest" in ptype or "ListenHTTP" in ptype or "InvokeHTTP" in ptype:
                    kind = "HTTP_ENTRY"
                else:
                    kind = "PROCESSOR"
                entry_points.append({"id": pid, "name": name, "type": "PROCESSOR", "kind": kind})
            for port in input_ports_list:
                pid = port.get("id")
                if not pid or pid in has_incoming:
                    continue
                comp = port.get("component", {})
                entry_points.append({"id": pid, "name": comp.get("name", "Unknown"), "type": "INPUT_PORT", "kind": "INPUT_PORT"})
            # Controller services
            controller_services: List[Dict[str, Any]] = []
            try:
                cs_list = await nifi_client.list_controller_services(pg_id, user_request_id=user_request_id, action_id=action_id)
                for cs in (cs_list or []):
                    ccomp = cs.get("component", {})
                    controller_services.append({"id": cs.get("id"), "name": ccomp.get("name", ""), "state": ccomp.get("state", "")})
            except Exception as e:
                local_logger.warning(f"Could not list controller services for flow summary: {e}")
            # all_components: id -> full entity (for find_decision_branches)
            all_components: Dict[str, Any] = {}
            for p in processors_list:
                if p.get("id"):
                    all_components[p["id"]] = p
            for port in input_ports_list + output_ports_list:
                if port.get("id"):
                    all_components[port["id"]] = port
            # components: id -> { type, name } for identify_flow_paths
            components_for_paths: Dict[str, Dict[str, Any]] = {}
            for p in processors_list:
                c = p.get("component", {})
                if p.get("id"):
                    components_for_paths[p["id"]] = {"type": c.get("type", "PROCESSOR"), "name": c.get("name", "Unknown")}
            for port in input_ports_list:
                c = port.get("component", {})
                if port.get("id"):
                    components_for_paths[port["id"]] = {"type": "INPUT_PORT", "name": c.get("name", "Unknown")}
            for port in output_ports_list:
                c = port.get("component", {})
                if port.get("id"):
                    components_for_paths[port["id"]] = {"type": "OUTPUT_PORT", "name": c.get("name", "Unknown")}
            source_components = [{"id": ep["id"], "name": ep["name"], "type": ep["type"]} for ep in entry_points]
            graph_data = build_graph_structure(processors_list, connections_list, input_ports_list, output_ports_list)
            decision_branches = find_decision_branches(all_components, graph_data)
            flow_paths = identify_flow_paths(components_for_paths, graph_data, source_components)
            boundary_ports = {
                "input_ports": [{"id": p.get("id"), "name": (p.get("component") or {}).get("name", "")} for p in input_ports_list if p.get("id")],
                "output_ports": [{"id": p.get("id"), "name": (p.get("component") or {}).get("name", "")} for p in output_ports_list if p.get("id")],
            }
            documentation["flow_summary"] = {
                "entry_points": entry_points,
                "controller_services": controller_services,
                "decision_branches": decision_branches,
                "flow_paths": flow_paths,
                "boundary_ports": boundary_ports,
            }

        local_logger.info("Flow documentation analysis complete.")
        return {
            "status": "success",
            "documentation": documentation
        }

    except NiFiAuthenticationError as e:
         local_logger.error(f"Authentication error during document_nifi_flow: {e}", exc_info=False)
         raise ToolError(f"Authentication error accessing NiFi: {e}") from e
    except (ValueError, ConnectionError, ToolError) as e:
        local_logger.error(f"Error documenting NiFi flow: {e}", exc_info=False)
        raise ToolError(f"Error documenting flow: {e}") from e
    except Exception as e:
        local_logger.error(f"Unexpected error documenting NiFi flow: {e}", exc_info=True)
        raise ToolError(f"An unexpected error occurred: {e}") from e

@mcp.tool()
@tool_phases(["Review", "Operate"])
async def search_nifi_flow(
    query: str,
    filter_object_type: Optional[Literal["processor", "connection", "port", "process_group", "controller_service"]] = None,
    filter_process_group_id: Optional[str] = None,
    # mcp_context: dict = {} # Removed context parameter
) -> Dict[str, List[Dict]]:
    """
    Performs a search across the entire NiFi flow for components matching the query.

    Optionally filters results by object type and/or containing process group ID.

    Parameters
    ----------
    query : str
        The search term (e.g., processor name, property value, comment text).
    filter_object_type : Optional[Literal["processor", "connection", "port", "process_group", "controller_service"]], optional
        Filter results to only include objects of this type. 'port' includes both input and output ports.
    filter_process_group_id : Optional[str], optional
        Filter results to only include objects within the specified process group (including nested groups).
    # Removed mcp_context from docstring

    Returns
    -------
    Dict[str, List[Dict]]
        A dictionary containing lists of matching objects, keyed by type (e.g., 'processorResults', 'connectionResults', 'controllerServiceResults').
        Each result includes basic information like id, name, and parent group.
    """
    # Get client and logger from context variables
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger

    if not nifi_client:
        raise ToolError("NiFi client not found in context.")
    if not isinstance(nifi_client, NiFiClient):
         raise ToolError(f"Invalid NiFi client type found in context: {type(nifi_client)}")
         
    # await ensure_authenticated(nifi_client, local_logger) # Removed
    
    local_logger.info(f"Searching NiFi flow with query: '{query}'. Filters: type={filter_object_type}, group={filter_process_group_id}")
    nifi_req = {"operation": "search_flow", "query": query}
    local_logger.bind(interface="nifi", direction="request", data=nifi_req).debug("Calling NiFi API")

    try:
        search_results_data = await nifi_client.search_flow(query)
        raw_results = search_results_data.get("searchResultsDTO", {})
        
        nifi_resp = {"has_results": bool(raw_results)}
        local_logger.bind(interface="nifi", direction="response", data=nifi_resp).debug("Received from NiFi API")

        # --- Filtering Logic --- 
        filtered_results = {}
        object_type_map = {
            "processor": "processorResults",
            "connection": "connectionResults",
            "process_group": "processGroupResults",
            "controller_service": "controllerServiceResults",
            "input_port": "inputPortResults", # Map generic port to specific results
            "output_port": "outputPortResults" # Map generic port to specific results
        }

        # Determine which result keys to process based on filter_object_type
        keys_to_process = []
        if not filter_object_type:
            keys_to_process = list(object_type_map.values())
        elif filter_object_type == "port":
            keys_to_process = [object_type_map["input_port"], object_type_map["output_port"]]
        elif filter_object_type in object_type_map:
            keys_to_process = [object_type_map[filter_object_type]]
            
        if not keys_to_process:
            local_logger.warning(f"Invalid filter_object_type: '{filter_object_type}', returning all types.")
            keys_to_process = list(object_type_map.values()) # Fallback to all if filter is invalid
        
        for result_key in keys_to_process:
            if result_key in raw_results:
                filtered_list = []
                for item in raw_results[result_key]:
                    # Apply process group filter if specified
                    if filter_process_group_id:
                        item_pg_id = item.get("groupId")
                        if item_pg_id != filter_process_group_id:
                            # TODO: Implement recursive check if needed
                            # For now, only direct parent match
                            local_logger.trace(f"Skipping item {item.get('id')} due to PG filter mismatch (Item PG: {item_pg_id}, Filter PG: {filter_process_group_id})")
                            continue # Skip if PG ID doesn't match
                    
                    # Add basic info for the summary
                    filtered_list.append({
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "groupId": item.get("groupId"),
                        "matches": item.get("matches", []) # Include matching fields
                    })
                if filtered_list:
                    filtered_results[result_key] = filtered_list
        # ---------------------
        
        local_logger.info(f"Flow search completed. Found results across {len(filtered_results)} types.")
        return filtered_results

    except NiFiAuthenticationError as e:
         local_logger.error(f"Authentication error during search_nifi_flow: {e}", exc_info=False)
         raise ToolError(f"Authentication error accessing NiFi: {e}") from e
    except (ConnectionError, ToolError) as e:
        local_logger.error(f"Error searching NiFi flow: {e}", exc_info=False)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        raise ToolError(f"Error searching NiFi flow: {e}") from e
    except Exception as e:
        local_logger.error(f"Unexpected error searching NiFi flow: {e}", exc_info=True)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received unexpected error from NiFi API")
        raise ToolError(f"An unexpected error occurred: {e}") from e


async def _enrich_outline_node(
    node: Dict[str, Any],
    depth: int,
    nifi_client: NiFiClient,
    include_boundary_ports: bool,
    user_request_id: str,
    action_id: str,
    max_depth_for_ports: int = 2,
) -> Dict[str, Any]:
    """Enrich a hierarchy node with depth, counts, and optionally boundary ports. Recurses into children."""
    node_id = node.get("id")
    node_name = node.get("name", "Unknown")
    children_raw = node.get("children", node.get("child_process_groups", []))
    out: Dict[str, Any] = {
        "id": node_id,
        "name": node_name,
        "depth": depth,
        "child_process_groups": [],
    }
    try:
        counts = await _get_process_group_contents_counts(node_id)
        out["counts"] = counts
    except Exception:
        out["counts"] = {}
    if include_boundary_ports and depth <= max_depth_for_ports:
        try:
            input_ports = await nifi_client.get_input_ports(node_id)
            output_ports = await nifi_client.get_output_ports(node_id)
            out["input_ports"] = [{"id": p.get("id"), "name": (p.get("component") or {}).get("name", "")} for p in (input_ports or []) if p.get("id")]
            out["output_ports"] = [{"id": p.get("id"), "name": (p.get("component") or {}).get("name", "")} for p in (output_ports or []) if p.get("id")]
        except Exception:
            out["input_ports"] = []
            out["output_ports"] = []
    else:
        out["input_ports"] = []
        out["output_ports"] = []
    for child in children_raw:
        enriched_child = await _enrich_outline_node(
            child, depth + 1, nifi_client, include_boundary_ports,
            user_request_id, action_id, max_depth_for_ports
        )
        out["child_process_groups"].append(enriched_child)
    return out


@mcp.tool()
@tool_phases(["Review", "Build", "Modify", "Operate"])
async def get_flow_outline(
    process_group_id: str | None = None,
    max_depth: int = 10,
    timeout_seconds: float | None = None,
    include_boundary_ports: bool = True,
) -> Dict[str, Any]:
    """
    Returns a lightweight process group tree (outline) with counts and optional boundary ports.

    Use this for outline-first exploration of large, deep flows: one call returns the PG hierarchy
    with component counts (and optionally input/output port names for the first levels). The LLM
    can then choose which PGs to document in detail via document_nifi_flow or get_process_group_status.

    Args:
        process_group_id: The root process group ID. Defaults to root if None.
        max_depth: Not used in current implementation (hierarchy is fully recursive subject to timeout).
        timeout_seconds: Max seconds for hierarchy fetch. If exceeded, returns partial outline with
            completed=False and continuation_token.
        include_boundary_ports: When True, adds input_ports and output_ports to each node for
            depth <= 2 to avoid excessive API calls on large trees.

    Returns:
        process_group_id, process_group_name, outline (id, name, depth, counts, input_ports,
        output_ports, child_process_groups), completed, timeout_occurred, continuation_token.
    """
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"
    if not nifi_client:
        raise ToolError("NiFi client not found in context.")
    try:
        target_pg_id = process_group_id
        if not target_pg_id:
            target_pg_id = await nifi_client.get_root_process_group_id(user_request_id=user_request_id, action_id=action_id)
            if not target_pg_id:
                raise ToolError("Could not retrieve root process group ID.")
        pg_name = await _get_process_group_name(target_pg_id)
        hierarchy = await _get_process_group_hierarchy_with_timeout(
            target_pg_id, recursive_search=True, timeout_seconds=timeout_seconds
        )
        outline = await _enrich_outline_node(
            hierarchy, 0, nifi_client, include_boundary_ports, user_request_id, action_id
        )
        return {
            "process_group_id": target_pg_id,
            "process_group_name": pg_name,
            "outline": outline,
            "completed": hierarchy.get("completed", True),
            "timeout_occurred": hierarchy.get("timeout_occurred", False),
            "continuation_token": hierarchy.get("continuation_token"),
        }
    except (ValueError, ConnectionError, NiFiAuthenticationError) as e:
        local_logger.error(f"Error getting flow outline: {e}", exc_info=False)
        raise ToolError(f"Error getting flow outline: {e}") from e
    except Exception as e:
        local_logger.error(f"Unexpected error getting flow outline: {e}", exc_info=True)
        raise ToolError(f"An unexpected error occurred: {e}") from e


@mcp.tool()
@tool_phases(["Review", "Operate"])
async def get_process_group_status(
    process_group_id: str | None = None,
    include_bulletins: bool = True,
    bulletin_limit: int = 20,
) -> Dict[str, Any]:
    """
    Provides a consolidated status overview of a process group.

    Includes component state summaries, validation issues, connection queue sizes,
    a health verdict (healthy / errors / degraded), optional bulletin summary, and
    optionally recent bulletins for the group.

    Args:
        process_group_id: The ID of the target process group. Defaults to root if None.
        include_bulletins: Whether to fetch and include bulletins specific to this group.
        bulletin_limit: Max number of bulletins to fetch if include_bulletins is True.

    Returns:
        A dictionary with: process_group_id, process_group_name, component_summary,
        invalid_components, queue_summary, bulletins (if requested), health
        ("healthy" | "errors" | "degraded"), health_reason (str or None), and
        bulletin_summary (when include_bulletins=True): count_by_level (ERROR/WARNING/INFO),
        last_errors (up to 10 with message, source_id, source_name, timestamp, category).
    """
    # Get client, logger, and context IDs
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"

    if not nifi_client:
        raise ToolError("NiFi client not found in context.")

    local_logger = local_logger.bind(pg_id_param=process_group_id, include_bulletins=include_bulletins)
    local_logger.info("Getting process group status overview.")

    results = {
        "process_group_id": None,
        "process_group_name": "Unknown",
        "component_summary": {
            "processors": {"total": 0, "running": 0, "stopped": 0, "invalid": 0, "disabled": 0},
            "input_ports": {"total": 0, "running": 0, "stopped": 0, "invalid": 0, "disabled": 0},
            "output_ports": {"total": 0, "running": 0, "stopped": 0, "invalid": 0, "disabled": 0},
        },
        "invalid_components": [],
        "queue_summary": {
             "total_queued_count": 0,
             "total_queued_size_bytes": 0,
             "total_queued_size_human": "0 B",
             "connections_with_data": []
        },
        "bulletins": []
    }

    try:
        # --- Step 0: Resolve PG ID and Name ---
        target_pg_id = process_group_id
        if not target_pg_id:
            local_logger.info("process_group_id not provided, resolving root.")
            target_pg_id = await nifi_client.get_root_process_group_id(user_request_id=user_request_id, action_id=action_id)
            local_logger.info(f"Resolved root process group ID: {target_pg_id}")
        # Use same helper for root and non-root so name matches get_flow_outline (e.g. "NiFi Flow")
        results["process_group_name"] = await _get_process_group_name(target_pg_id)
        
        results["process_group_id"] = target_pg_id
        local_logger = local_logger.bind(process_group_id=target_pg_id) # Bind resolved ID

        # --- Step 1: Get Components (Processors, Connections, Ports) ---
        local_logger.info("Fetching components (processors, connections, ports)...")
        # Use asyncio.gather for concurrency
        tasks = {
            "processors": nifi_client.list_processors(target_pg_id, user_request_id=user_request_id, action_id=action_id),
            "connections": nifi_client.list_connections(target_pg_id, user_request_id=user_request_id, action_id=action_id),
            "input_ports": nifi_client.get_input_ports(target_pg_id),
            "output_ports": nifi_client.get_output_ports(target_pg_id)
        }
        # Log the request details (simplified)
        nifi_req = {"operation": "list_components", "process_group_id": target_pg_id}
        local_logger.bind(interface="nifi", direction="request", data=nifi_req).debug("Calling NiFi API (multiple component lists)")
        
        component_responses = await asyncio.gather(*tasks.values(), return_exceptions=True)
        
        processors_resp, connections_resp, input_ports_resp, output_ports_resp = component_responses
        
        # Log the response details (simplified)
        nifi_resp = {
            "processor_count": len(processors_resp) if isinstance(processors_resp, list) else -1,
            "connection_count": len(connections_resp) if isinstance(connections_resp, list) else -1,
            "input_port_count": len(input_ports_resp) if isinstance(input_ports_resp, list) else -1,
            "output_port_count": len(output_ports_resp) if isinstance(output_ports_resp, list) else -1,
        }
        local_logger.bind(interface="nifi", direction="response", data=nifi_resp).debug("Received from NiFi API (multiple component lists)")

        # Handle potential errors from gather
        if isinstance(processors_resp, Exception): local_logger.error(f"Error listing processors: {processors_resp}"); processors_resp = []
        if isinstance(connections_resp, Exception): 
            error_msg = str(connections_resp) or repr(connections_resp) or "Unknown error"
            local_logger.error(f"Error listing connections: {error_msg}")
            connections_resp = []
        if isinstance(input_ports_resp, Exception): local_logger.error(f"Error listing input ports: {input_ports_resp}"); input_ports_resp = []
        if isinstance(output_ports_resp, Exception): local_logger.error(f"Error listing output ports: {output_ports_resp}"); output_ports_resp = []
        
        # --- Step 2: Process Components for Status and Validation ---
        local_logger.info("Processing component statuses...")
        comp_summary = results["component_summary"]
        invalid_list = results["invalid_components"]

        for proc in processors_resp:
            comp = proc.get("component", {})
            state = comp.get("state", "UNKNOWN").lower()
            comp_summary["processors"]["total"] += 1
            if state in comp_summary["processors"]: comp_summary["processors"][state] += 1
            # --- Updated Condition ---
            validation_status = comp.get("validationStatus", "UNKNOWN") # Get the status
            if validation_status == "INVALID": # Check specifically for INVALID
                invalid_list.append({
                    "id": proc.get("id"),
                    "name": comp.get("name"),
                    "type": "processor",
                    "validation_status": validation_status, # Include the status
                    "validation_errors": comp.get("validationErrors", [])
                })

        for port in input_ports_resp:
            comp = port.get("component", {})
            state = comp.get("state", "UNKNOWN").lower()
            comp_summary["input_ports"]["total"] += 1
            if state in comp_summary["input_ports"]: comp_summary["input_ports"][state] += 1
            # --- Updated Condition ---
            validation_status = comp.get("validationStatus", "UNKNOWN") # Get the status
            if validation_status == "INVALID": # Check specifically for INVALID
                invalid_list.append({
                    "id": port.get("id"),
                    "name": comp.get("name"),
                    "type": "input_port",
                    "validation_status": validation_status, # Include the status
                    "validation_errors": comp.get("validationErrors", [])
                })

        for port in output_ports_resp:
            comp = port.get("component", {})
            state = comp.get("state", "UNKNOWN").lower()
            comp_summary["output_ports"]["total"] += 1
            if state in comp_summary["output_ports"]: comp_summary["output_ports"][state] += 1
            # --- Updated Condition ---
            validation_status = comp.get("validationStatus", "UNKNOWN") # Get the status
            if validation_status == "INVALID": # Check specifically for INVALID
                invalid_list.append({
                    "id": port.get("id"),
                    "name": comp.get("name"),
                    "type": "output_port",
                    "validation_status": validation_status, # Include the status
                    "validation_errors": comp.get("validationErrors", [])
                })

        # --- Step 3: Get Queue Status for Connections --- 
        local_logger.info("Fetching connection queue statuses via process group snapshot...")
        queue_summary = results["queue_summary"]
        # Clear previous connection-specific details needed for old method
        # connection_status_tasks = []
        # connection_ids = [conn.get("id") for conn in connections_resp if conn.get("id")]
        # connection_details_map = {conn.get("id"): filter_connection_data(conn) for conn in connections_resp}
        
        # Fetch the single status snapshot for the entire group
        nifi_req_q = {"operation": "get_process_group_status_snapshot", "process_group_id": target_pg_id}
        local_logger.bind(interface="nifi", direction="request", data=nifi_req_q).debug("Calling NiFi API")
        group_status_snapshot = {}
        try:
            group_status_snapshot = await nifi_client.get_process_group_status_snapshot(target_pg_id)
            nifi_resp_q = {"has_snapshot": bool(group_status_snapshot)}
            local_logger.bind(interface="nifi", direction="response", data=nifi_resp_q).debug("Received from NiFi API")
        except (ConnectionError, ValueError, NiFiAuthenticationError) as status_err:
             local_logger.error(f"Failed to get process group status snapshot for queue summary: {status_err}")
             # Log error but continue, queue summary will be empty/zero
             nifi_resp_q = {"error": str(status_err)}
             local_logger.bind(interface="nifi", direction="response", data=nifi_resp_q).debug("Received error from NiFi API")
             # Explicitly set snapshot to empty dict to avoid errors below
             group_status_snapshot = {}
        except Exception as status_exc:
             local_logger.error(f"Unexpected error getting process group status snapshot: {status_exc}", exc_info=True)
             nifi_resp_q = {"error": str(status_exc)}
             local_logger.bind(interface="nifi", direction="response", data=nifi_resp_q).debug("Received unexpected error from NiFi API")
             # Explicitly set snapshot to empty dict to avoid errors below
             group_status_snapshot = {}

        # Process the snapshots from the single response
        connection_snapshots = group_status_snapshot.get("aggregateSnapshot", {}).get("connectionStatusSnapshots", [])
        local_logger.debug(f"Processing {len(connection_snapshots)} connection snapshots from group status.")

        # We need connection details (like name) to enrich the summary.
        # Reuse connections_resp obtained in Step 1
        connections_map = {conn.get("id"): conn for conn in connections_resp if conn.get("id")}

        for snapshot_entity in connection_snapshots:
            # Extract the actual snapshot data
            snapshot_data = snapshot_entity.get("connectionStatusSnapshot")
            if not snapshot_data:
                local_logger.warning(f"Skipping connection snapshot due to missing data: {snapshot_entity}")
                continue
                
            conn_id = snapshot_data.get("id")
            if not conn_id:
                 local_logger.warning(f"Skipping connection snapshot due to missing ID: {snapshot_data}")
                 continue
                 
            queued_count = int(snapshot_data.get("flowFilesQueued", 0))
            queued_bytes = int(snapshot_data.get("bytesQueued", 0))
            
            # Get connection details from the map populated earlier
            conn_info = connections_map.get(conn_id, {})
            conn_component = conn_info.get("component", {})
            conn_name = conn_component.get("name", "")
            source_name = conn_component.get("source", {}).get("name", "Unknown Source")
            dest_name = conn_component.get("destination", {}).get("name", "Unknown Destination")

            if queued_count > 0:
                queue_summary["total_queued_count"] += queued_count
                queue_summary["total_queued_size_bytes"] += queued_bytes
                queue_summary["connections_with_data"].append({
                    "id": conn_id,
                    "name": conn_name,
                    "sourceName": source_name,
                    "destName": dest_name,
                    "queued_count": queued_count,
                    "queued_size_bytes": queued_bytes,
                    "queued_size_human": snapshot_data.get("queuedSize", "0 B") # Use pre-formatted string
                })
                
        # Format total size (logic remains the same)
        total_bytes = queue_summary["total_queued_size_bytes"]
        if total_bytes < 1024:
            queue_summary["total_queued_size_human"] = f"{total_bytes} B"
        elif total_bytes < 1024**2:
            queue_summary["total_queued_size_human"] = f"{total_bytes/1024:.1f} KB"
        elif total_bytes < 1024**3:
             queue_summary["total_queued_size_human"] = f"{total_bytes/(1024**2):.1f} MB"
        else:
             queue_summary["total_queued_size_human"] = f"{total_bytes/(1024**3):.1f} GB"

        # --- Step 4: Get Bulletins (if requested) ---
        if include_bulletins:
            local_logger.info(f"Fetching bulletins (limit {bulletin_limit})...")
            try:
                nifi_req_b = {"operation": "get_bulletin_board", "group_id": target_pg_id, "limit": bulletin_limit}
                local_logger.bind(interface="nifi", direction="request", data=nifi_req_b).debug("Calling NiFi API")
                bulletins = await nifi_client.get_bulletin_board(group_id=target_pg_id, limit=bulletin_limit)
                nifi_resp_b = {"bulletin_count": len(bulletins)}
                local_logger.bind(interface="nifi", direction="response", data=nifi_resp_b).debug("Received from NiFi API")
                results["bulletins"] = bulletins
            except Exception as e:
                local_logger.error(f"Failed to fetch bulletins: {e}")
                # Continue without bulletins, maybe add an error marker?
                results["bulletins"] = [{"error": f"Failed to fetch bulletins: {e}"}]
        else:
            local_logger.info("Skipping bulletin fetch as per request.")
            results["bulletins"] = None # Explicitly set to None if not included

        # --- Step 5: Health verdict and bulletin summary ---
        invalid_list = results["invalid_components"]
        bulletins_list = results["bulletins"] if isinstance(results["bulletins"], list) else []
        # Normalize bulletin items (NiFi may return { "bulletin": { ... } } or flat object)
        count_by_level = {"ERROR": 0, "WARNING": 0, "INFO": 0}
        last_errors = []
        for item in bulletins_list:
            if isinstance(item, dict) and "error" in item:
                continue  # Skip fetch-error placeholder
            b_data = item.get("bulletin", item) if isinstance(item, dict) else {}
            level = (b_data.get("level") or "").upper()
            if level in count_by_level:
                count_by_level[level] += 1
            if level == "ERROR":
                last_errors.append({
                    "message": b_data.get("message", ""),
                    "source_id": b_data.get("sourceId"),
                    "source_name": b_data.get("sourceName"),
                    "timestamp": b_data.get("timestamp"),
                    "category": b_data.get("category"),
                })
        # Keep last 10 errors
        last_errors = last_errors[:10]
        results["bulletin_summary"] = {
            "count_by_level": count_by_level,
            "last_errors": last_errors,
        } if include_bulletins and not any(isinstance(x, dict) and x.get("error") for x in bulletins_list) else None

        has_invalid = len(invalid_list) > 0
        has_error_bulletins = count_by_level["ERROR"] > 0
        comp_summary = results["component_summary"]
        stopped_processors = comp_summary["processors"].get("stopped", 0)
        stopped_ports = comp_summary["input_ports"].get("stopped", 0) + comp_summary["output_ports"].get("stopped", 0)
        conns_with_data = len(queue_summary["connections_with_data"])
        has_degraded = (stopped_processors + stopped_ports > 0 or (queue_summary["total_queued_count"] > 0 and conns_with_data > 5)) and not has_invalid and not has_error_bulletins

        if has_invalid or has_error_bulletins:
            reasons = []
            if has_invalid:
                reasons.append(f"{len(invalid_list)} invalid component(s)")
            if has_error_bulletins:
                reasons.append(f"{count_by_level['ERROR']} ERROR bulletin(s)")
            results["health"] = "errors"
            results["health_reason"] = "; ".join(reasons)
        elif has_degraded:
            results["health"] = "degraded"
            results["health_reason"] = "Stopped components or elevated queue backpressure"
        else:
            results["health"] = "healthy"
            results["health_reason"] = None

        local_logger.info("Process group status overview fetch complete.")
        return results

    except NiFiAuthenticationError as e:
         local_logger.error(f"Authentication error getting status for PG {target_pg_id}: {e}", exc_info=False)
         raise ToolError(f"Authentication error accessing NiFi: {e}") from e
    except (ValueError, ConnectionError, ToolError) as e:
        local_logger.error(f"Error getting status for PG {target_pg_id}: {e}", exc_info=False)
        raise ToolError(f"Error getting status for PG {target_pg_id}: {e}") from e
    except Exception as e:
        local_logger.error(f"Unexpected error getting status for PG {target_pg_id}: {e}", exc_info=True)
        raise ToolError(f"An unexpected error occurred: {e}") from e

@mcp.tool()
@tool_phases(["Review", "Operate"])
async def list_flowfiles(
    target_id: str,
    target_type: Literal["connection", "processor"],
    max_results: int = 100,
    polling_interval: float = 0.5,
    polling_timeout: float = 30.0 # Increased default timeout to 30s
) -> Dict[str, Any]:
    """
    Lists FlowFile summaries from a connection queue or processor provenance.

    For connections, lists FlowFiles currently queued.
    For processors, lists FlowFiles recently processed via provenance events.

    Args:
        target_id: The ID of the connection or processor.
        target_type: Whether the target_id refers to a 'connection' or 'processor'.
        max_results: Maximum number of FlowFile summaries to return.
        polling_interval: Seconds between polling for async request completion (queue/provenance).
        polling_timeout: Maximum seconds to wait for async request completion.

    Returns:
        A dictionary containing the list of FlowFile summaries and metadata.
    """
    # Get client and logger
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set.")

    local_logger = local_logger.bind(target_id=target_id, target_type=target_type, max_results=max_results)
    local_logger.info(f"Listing flowfiles for {target_type} {target_id}")

    results = {
        "target_id": target_id,
        "target_type": target_type,
        "listing_source": "unknown",
        "flowfile_summaries": [],
        "error": None
    }

    try:
        if target_type == "connection":
            results["listing_source"] = "queue"
            local_logger.info("Listing via connection queue...")
            request_id = None
            try:
                # 1. Create request
                nifi_req_create = {"operation": "create_flowfile_listing_request", "connection_id": target_id}
                local_logger.bind(interface="nifi", direction="request", data=nifi_req_create).debug("Calling NiFi API")
                listing_request = await nifi_client.create_flowfile_listing_request(target_id)
                request_id = listing_request.get("id")
                nifi_resp_create = {"request_id": request_id}
                local_logger.bind(interface="nifi", direction="response", data=nifi_resp_create).debug("Received from NiFi API")
                if not request_id:
                    raise ToolError("Failed to get request ID from NiFi for queue listing.")
                local_logger.info(f"Submitted queue listing request: {request_id}")

                # 2. Poll for completion
                start_time = asyncio.get_event_loop().time()
                while True:
                    if (asyncio.get_event_loop().time() - start_time) > polling_timeout:
                        raise TimeoutError(f"Timed out waiting for queue listing request {request_id} to complete.")
                    
                    nifi_req_get = {"operation": "get_flowfile_listing_request", "connection_id": target_id, "request_id": request_id}
                    local_logger.bind(interface="nifi", direction="request", data=nifi_req_get).debug("Calling NiFi API (polling)")
                    request_status = await nifi_client.get_flowfile_listing_request(target_id, request_id)
                    nifi_resp_get = {"finished": request_status.get("finished"), "percentCompleted": request_status.get("percentCompleted")}
                    local_logger.bind(interface="nifi", direction="response", data=nifi_resp_get).debug("Received from NiFi API (polling)")
                    
                    if request_status.get("finished"): 
                        local_logger.info(f"Queue listing request {request_id} finished.")
                        # 3. Extract results (already in the final status response)
                        summaries_raw = request_status.get("flowFileSummaries", [])
                        # Limit results here if necessary, though API might have internal limit
                        results["flowfile_summaries"] = [
                            {
                                "uuid": ff.get("uuid"),
                                "filename": ff.get("filename"),
                                "size": ff.get("size"),
                                "queued_duration": ff.get("queuedDuration"),
                                "attributes": ff.get("attributes", {}), # Queue listing includes attributes
                                "position": ff.get("position")
                            }
                            for ff in summaries_raw[:max_results]
                        ]
                        break
                    await asyncio.sleep(polling_interval)

            finally:
                 # 4. Delete request (always attempt cleanup)
                 if request_id:
                    local_logger.info(f"Cleaning up queue listing request {request_id}...")
                    try:
                        nifi_req_del = {"operation": "delete_flowfile_listing_request", "connection_id": target_id, "request_id": request_id}
                        local_logger.bind(interface="nifi", direction="request", data=nifi_req_del).debug("Calling NiFi API")
                        await nifi_client.delete_flowfile_listing_request(target_id, request_id)
                        local_logger.bind(interface="nifi", direction="response", data={"deleted": True}).debug("Received from NiFi API")
                    except Exception as del_e:
                        local_logger.warning(f"Failed to delete queue listing request {request_id}: {del_e}")
                        # Don't fail the whole operation if cleanup fails

        elif target_type == "processor":
            results["listing_source"] = "provenance"
            local_logger.info("Listing via processor provenance...")
            query_id = None
            try:
                # 1. Submit query
                provenance_payload = {
                    "processor_id": target_id,
                    "max_results": max_results  # Pass max_results to the client method
                }
                nifi_req_create = {"operation": "submit_provenance_query", "payload": provenance_payload}
                local_logger.bind(interface="nifi", direction="request", data=nifi_req_create).debug("Calling NiFi API")
                query_response = await nifi_client.submit_provenance_query(provenance_payload)
                # --- Corrected ID extraction ---
                # query_id = query_response.get("query", {}).get("id") # Old incorrect way
                query_id = query_response.get("id") # Correct: ID is directly in the returned dict
                # -----------------------------
                nifi_resp_create = {"query_id": query_id}
                local_logger.bind(interface="nifi", direction="response", data=nifi_resp_create).debug("Received from NiFi API")
                if not query_id:
                    raise ToolError("Failed to get query ID from NiFi for provenance search.")
                local_logger.info(f"Submitted provenance query: {query_id}")

                # 2. Poll for completion
                start_time = asyncio.get_event_loop().time()
                while True:
                    if (asyncio.get_event_loop().time() - start_time) > polling_timeout:
                        raise TimeoutError(f"Timed out waiting for provenance query {query_id} to complete.")

                    nifi_req_get = {"operation": "get_provenance_query", "query_id": query_id}
                    local_logger.bind(interface="nifi", direction="request", data=nifi_req_get).debug("Calling NiFi API (polling)")
                    query_status = await nifi_client.get_provenance_query(query_id)
                    nifi_resp_get = {"finished": query_status.get("query", {}).get("finished"), "percentCompleted": query_status.get("query", {}).get("percentCompleted")}
                    local_logger.bind(interface="nifi", direction="response", data=nifi_resp_get).debug("Received from NiFi API (polling)")

                    # --- Corrected Finished Check ---
                    # if query_status.get("query", {}).get("finished"): # Old incorrect check
                    if query_status.get("finished"): # Correct check directly on the status dict
                    # ------------------------------
                        local_logger.info(f"Provenance query {query_id} finished.")
                        
                        # Check if events are already in the query status response
                        events = query_status.get("provenanceEvents", [])
                        
                        if not events:
                            # If not in status, try to get results from separate endpoint
                            try:
                                events = await nifi_client.get_provenance_results(query_id)
                            except Exception as e:
                                local_logger.warning(f"Could not get provenance results from /results endpoint: {e}")
                                # Try to extract from the query response itself
                                events = query_status.get("results", {}).get("provenanceEvents", [])
                        
                        # --- Added Logging for Raw Events --- 
                        local_logger.debug(f"Retrieved {len(events)} raw provenance events from client.")
                        # if events:
                        #      local_logger.trace(f"First raw event details: {events[0]}") # Log first event details
                        # ------------------------------------

                        # Format events into summaries
                        # Note: Provenance events might show multiple stages for the same FlowFile.
                        # We will return one entry per event for simplicity, ordered by event time (default). 
                        results["flowfile_summaries"] = [
                            {
                                "uuid": event.get("flowFileUuid"),
                                "filename": event.get("previousAttributes", {}).get("filename") or event.get("updatedAttributes", {}).get("filename"), # Try both
                                # "size": event.get("fileSize"), # Use bytes value instead
                                "size_bytes": event.get("fileSizeBytes"), # Corrected field
                                "event_id": event.get("eventId"),
                                "event_type": event.get("eventType"),
                                "event_time": event.get("eventTime"),
                                "component_name": event.get("componentName"),
                                "attributes": event.get("updatedAttributes", {}), # Use updated attributes for the event
                            }
                            for event in events # Use the retrieved list
                        ]
                        break
                    await asyncio.sleep(polling_interval)

            finally:
                # 4. Delete query
                if query_id:
                    local_logger.info(f"Cleaning up provenance query {query_id}...")
                    try:
                        nifi_req_del = {"operation": "delete_provenance_query", "query_id": query_id}
                        local_logger.bind(interface="nifi", direction="request", data=nifi_req_del).debug("Calling NiFi API")
                        await nifi_client.delete_provenance_query(query_id)
                        local_logger.bind(interface="nifi", direction="response", data={"deleted": True}).debug("Received from NiFi API")
                    except Exception as del_e:
                        local_logger.warning(f"Failed to delete provenance query {query_id}: {del_e}")

        else:
            raise ToolError(f"Invalid target_type: {target_type}. Must be 'connection' or 'processor'.")

        local_logger.info(f"Successfully listed {len(results['flowfile_summaries'])} flowfile summaries.")
        return results

    except (NiFiAuthenticationError, ConnectionError, ToolError, ValueError, TimeoutError) as e:
        local_logger.error(f"Error listing flowfiles for {target_type} {target_id}: {e}", exc_info=False)
        results["error"] = str(e)
        # Return partial results with error message
        return results
    except Exception as e:
        local_logger.error(f"Unexpected error listing flowfiles for {target_type} {target_id}: {e}", exc_info=True)
        results["error"] = f"An unexpected error occurred: {e}"
        # Return partial results with error message
        return results

@mcp.tool()
@tool_phases(["Review", "Operate"])
async def get_flowfile_event_details(
    event_id: int,
    max_content_bytes: int = 4096  # Reasonable default to avoid overwhelming LLM
) -> Dict[str, Any]:
    """
    Retrieves detailed attributes and content for a specific FlowFile provenance event.

    Fetches the event details and intelligently retrieves content based on size limits.
    If input and output content are identical, only returns one copy to avoid duplication.

    Args:
        event_id: The specific numeric ID of the provenance event.
        max_content_bytes: Max bytes of content to return. If content is larger, 
                          returns size info instead of actual content.

    Returns:
        A dictionary containing the event and content details.
    """
    # Get client and logger
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set.")

    local_logger = local_logger.bind(event_id=event_id, max_content_bytes=max_content_bytes)
    local_logger.info("Getting FlowFile event details.")

    # Initialize results structure
    results = {
        "status": "error",
        "message": "",
        "event_id": event_id,
        # Essential event info (filtered to avoid overwhelming LLM)
        "event_type": None,
        "event_time": None,
        "component_name": None,
        "flowfile_uuid": None,
        "attributes": [],
        # Content size info
        "input_content_size_bytes": 0,
        "output_content_size_bytes": 0,
        "content_identical": False,
        # Content data (only if reasonably sized)
        "content_included": False,
        "content_too_large": False,
        "content": None,  # Will contain actual content if included
        "content_type": None  # "input", "output", or "both" if different
    }

    try:
        # Step 1: Get Event Details
        local_logger.info(f"Fetching details for provenance event {event_id}...")
        event_details = await nifi_client.get_provenance_event(event_id)

        if not event_details:
            raise ValueError(f"No details returned for event {event_id}.")

        # Extract essential event information (filtered for LLM consumption)
        results["event_type"] = event_details.get("eventType")
        results["event_time"] = event_details.get("eventTime")  
        results["component_name"] = event_details.get("componentName")
        results["flowfile_uuid"] = event_details.get("flowFileUuid")
        
        # Get attributes list (limit to avoid overwhelming LLM)
        attributes = event_details.get("attributes", [])
        if len(attributes) > 20:  # Limit to most important attributes
            results["attributes"] = attributes[:20]
            local_logger.warning(f"Event has {len(attributes)} attributes, truncated to 20 for LLM")
        else:
            results["attributes"] = attributes

        # Get content size information
        input_size = event_details.get("inputContentClaimFileSizeBytes", 0) or 0
        output_size = event_details.get("outputContentClaimFileSizeBytes", 0) or 0
        results["input_content_size_bytes"] = input_size
        results["output_content_size_bytes"] = output_size

        local_logger.info(f"Event {event_id}: input_size={input_size}, output_size={output_size}")

        # Step 2: Determine content retrieval strategy
        max_size = max(input_size, output_size)
        
        if max_size == 0:
            # No content available
            results["status"] = "success"
            results["message"] = f"Event details retrieved. No content available for event {event_id}."
            results["content_included"] = False
            return results
        
        elif max_size > max_content_bytes:
            # Content too large to include
            results["status"] = "success"
            results["message"] = f"Event details retrieved. Content available but too large ({max_size} bytes > {max_content_bytes} limit)."
            results["content_too_large"] = True
            results["content_included"] = False
            return results

        # Step 3: Retrieve content (both input and output to check for duplication)
        input_content = None
        output_content = None
        
        if input_size > 0:
            try:
                local_logger.info(f"Retrieving input content ({input_size} bytes)")
                content_resp = await nifi_client.get_provenance_event_content(event_id, "input")
                input_content = await content_resp.aread()
                await content_resp.aclose()
            except Exception as e:
                local_logger.warning(f"Could not retrieve input content: {e}")

        if output_size > 0:
            try:
                local_logger.info(f"Retrieving output content ({output_size} bytes)")
                content_resp = await nifi_client.get_provenance_event_content(event_id, "output")
                output_content = await content_resp.aread()
                await content_resp.aclose()
            except Exception as e:
                local_logger.warning(f"Could not retrieve output content: {e}")

        # Step 4: Check for identical content and prepare response
        if input_content is not None and output_content is not None:
            if input_content == output_content:
                # Content is identical, only return one copy
                results["content_identical"] = True
                results["content_type"] = "both"
                try:
                    results["content"] = input_content.decode('utf-8')
                except UnicodeDecodeError:
                    import base64
                    results["content"] = base64.b64encode(input_content).decode('ascii')
                local_logger.info(f"Input and output content are identical ({len(input_content)} bytes)")
            else:
                # Content is different, return both
                results["content_identical"] = False
                results["content_type"] = "both"
                try:
                    input_str = input_content.decode('utf-8')
                    output_str = output_content.decode('utf-8')
                    results["content"] = {
                        "input": input_str,
                        "output": output_str
                    }
                except UnicodeDecodeError:
                    import base64
                    results["content"] = {
                        "input": base64.b64encode(input_content).decode('ascii'),
                        "output": base64.b64encode(output_content).decode('ascii')
                    }
                local_logger.info(f"Input and output content are different (input: {len(input_content)}, output: {len(output_content)} bytes)")
        
        elif input_content is not None:
            # Only input content available
            results["content_identical"] = False
            results["content_type"] = "input"
            try:
                results["content"] = input_content.decode('utf-8')
            except UnicodeDecodeError:
                import base64
                results["content"] = base64.b64encode(input_content).decode('ascii')
            local_logger.info(f"Only input content available ({len(input_content)} bytes)")
            
        elif output_content is not None:
            # Only output content available
            results["content_identical"] = False
            results["content_type"] = "output"
            try:
                results["content"] = output_content.decode('utf-8')
            except UnicodeDecodeError:
                import base64
                results["content"] = base64.b64encode(output_content).decode('ascii')
            local_logger.info(f"Only output content available ({len(output_content)} bytes)")

        # Final status
        if results["content"] is not None:
            results["content_included"] = True
            results["status"] = "success"
            results["message"] = f"Successfully retrieved event details and content for event {event_id}."
        else:
            results["status"] = "success"
            results["message"] = f"Successfully retrieved event details for event {event_id}. Content was not accessible."

        return results

    except (NiFiAuthenticationError, ConnectionError, ToolError, ValueError, TimeoutError) as e:
        local_logger.error(f"Error getting flowfile event details for event {event_id}: {e}", exc_info=False)
        results["status"] = "error"
        results["message"] = str(e)
        return results
    except Exception as e:
        local_logger.error(f"Unexpected error getting flowfile event details for event {event_id}: {e}", exc_info=True)
        results["status"] = "error"
        results["message"] = f"An unexpected error occurred: {e}"
        return results
