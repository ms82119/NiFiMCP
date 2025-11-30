"""
Documentation workflow nodes for hierarchical NiFi flow analysis.

This module implements the bottom-up hierarchical approach:
1. InitializeDocNode - Setup state structures
2. DiscoveryNode - Discover components and build PG tree
3. HierarchicalAnalysisNode - Analyze from leaves to root
4. DocumentationNode - Generate final documentation

Copy this file to: nifi_mcp_server/workflows/nodes/doc_nodes.py
"""

from typing import Dict, Any, List, Optional, Set
import time
import json
import re

from .async_nifi_node import AsyncNiFiWorkflowNode
from ..core.event_system import EventTypes
from config.settings import get_documentation_workflow_config
from .component_formatter import (
    format_component_reference,
    format_processor_reference,
    format_component_for_table,
    format_destination_reference
)
from .io_extraction import (
    extract_io_endpoints_detailed,
    extract_io_basic
)
from .error_handling import extract_error_handling
from .analysis_helpers import (
    analyze_pg_direct,
    analyze_pg_with_summaries,
    analyze_virtual_group,
    create_virtual_groups,
    categorize_processors,
    format_connections_for_prompt,
    build_connectivity_summary,
    get_pg_processors
)
from .doc_generation_helpers import (
    build_aggregated_io_table,
    build_error_handling_table,
    build_hierarchical_doc,
    generate_executive_summary,
    generate_hierarchical_diagram,
    assemble_hierarchical_document,
    validate_output
)


class InitializeDocNode(AsyncNiFiWorkflowNode):
    """
    Initialize documentation workflow state.
    
    Validates input parameters, initializes empty data structures,
    and prepares shared state for hierarchical bottom-up processing.
    """
    
    def __init__(self):
        super().__init__(
            name="initialize_doc",
            description="Initialize documentation workflow state",
            allowed_phases=["Review"]
        )
        self.successors = {}  # Will be wired in workflow definition
    
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        """Extract and validate input parameters."""
        # Defensive check: ensure shared is a dict
        if not isinstance(shared, dict):
            self.bound_logger.error(f"InitializeDocNode.prep_async: shared is not a dict! Type: {type(shared)}")
            raise ValueError(f"shared must be a dict in prep_async, got {type(shared).__name__}")
        
        await super().prep_async(shared)
        
        return {
            "process_group_id": shared.get("process_group_id", "root"),
            "user_request_id": shared.get("user_request_id"),
            "provider": shared.get("provider", "openai"),
            "model_name": shared.get("model_name", "gpt-4"),
            "nifi_server_id": shared.get("selected_nifi_server_id"),
            "workflow_id": shared.get("workflow_id", "flow_documentation"),
            "step_id": self.name,  # Include step_id for event emission
            "config": get_documentation_workflow_config(),
            "_shared": shared,  # Pass shared state reference for skip checks
            "_is_loading_state": shared.get("skip_discovery") or shared.get("skip_analysis")  # Flag for preserving loaded state
        }
    
    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        """Initialize all state structures."""
        self.bound_logger.info(f"Initializing documentation for PG: {prep_res['process_group_id']}")
        
        # Validate required fields
        if not prep_res.get("nifi_server_id"):
            return {
                "status": "error",
                "error": "No NiFi server selected"
            }
        
        config = prep_res.get("config", {})
        is_loading_state = prep_res.get("_is_loading_state", False)
        
        # Build initialized state - preserve loaded values if loading from saved state
        initialized_state = {
            # Input parameters (always set these)
            "process_group_id": prep_res["process_group_id"],
            "user_request_id": prep_res["user_request_id"],
            "provider": prep_res["provider"],
            "model_name": prep_res["model_name"],
            "nifi_server_id": prep_res["nifi_server_id"],
            "config": config,
            "workflow_id": "flow_documentation",
            "current_phase": "INIT"
        }
        
        # Only initialize empty structures if NOT loading from saved state
        # When loading, these will be preserved from the loaded shared state
        if not is_loading_state:
            initialized_state.update({
                # Discovery state
                "flow_graph": {
                    "processors": {},      # ID -> processor details (includes parent_pg_id)
                    "connections": [],     # List of connections
                    "process_groups": {},  # ID -> PG details
                    "ports": {}            # ID -> port details
                },
                "continuation_token": None,
                "discovery_complete": False,
                
                # Hierarchical tracking (built during discovery)
                "pg_tree": {},             # ID -> {id, name, parent, children, depth, processor_count}
                "max_depth": 0,            # Deepest nesting level found
                "current_depth": None,     # Set after discovery (starts at max_depth)
                
                # Accumulated summaries (key output of hierarchical analysis)
                "pg_summaries": {},        # PG ID -> summary dict
                "virtual_groups": {},      # PG ID -> list of virtual group summaries
                
                # Generation state
                "doc_sections": {
                    "summary": "",
                    "diagram": "",
                    "hierarchy_doc": "",
                    "logic_table": "",
                    "io_table": "",
                },
                "final_document": "",
                
                # Metrics
                "metrics": {
                    "workflow_start_time": time.time(),
                    "discovery": {},
                    "analysis": {"pgs_analyzed": 0, "virtual_groups_created": 0},
                    "generation": {}
                },
            })
        else:
            # When loading from saved state, preserve existing structures
            self.bound_logger.info("Preserving loaded state structures (skip_discovery or skip_analysis is set)")
            # Only ensure workflow_start_time exists if not already present
            shared = prep_res.get("_shared", {})
            if "metrics" not in shared or "workflow_start_time" not in shared.get("metrics", {}):
                initialized_state["metrics"] = {
                    "workflow_start_time": time.time(),
                }
        
        return {
            "status": "success",
            "initialized_state": initialized_state
        }
    
    async def post_async(
        self, 
        shared: Dict[str, Any], 
        prep_res: Dict[str, Any], 
        exec_res: Dict[str, Any]
    ) -> str:
        """Update shared state and determine transition."""
        
        if exec_res.get("status") == "error":
            shared["error"] = exec_res.get("error")
            return "error"
        
        # Merge initialized state into shared
        # When loading from saved state, only overwrite if the key doesn't exist in shared
        # This preserves loaded state (flow_graph, pg_tree, pg_summaries, etc.)
        initialized = exec_res.get("initialized_state", {})
        is_loading_state = shared.get("skip_discovery") or shared.get("skip_analysis")
        
        if is_loading_state:
            # Preserve loaded state - only set keys that don't exist
            for key, value in initialized.items():
                if key not in shared:
                    shared[key] = value
                # Always update these critical fields
                elif key in ["config", "workflow_id", "current_phase", "process_group_id", "user_request_id"]:
                    shared[key] = value
        else:
            # Normal flow - overwrite with initialized state
            for key, value in initialized.items():
                shared[key] = value
        
        # Emit phase complete event
        await self.emit_doc_phase_event(
            EventTypes.DOC_PHASE_COMPLETE,
            "INIT",
            shared,
            metrics={"duration_ms": 0},
            progress_message="Initialization complete, starting discovery..."
        )
        
        shared["current_phase"] = "DISCOVERY"
        self.bound_logger.info("Initialization complete, transitioning to discovery")
        
        return "default"  # -> DiscoveryNode


class DiscoveryNode(AsyncNiFiWorkflowNode):
    """
    Discover NiFi flow components with pagination.
    
    Key responsibilities:
    1. Discover all processors, connections, and process groups
    2. Build pg_tree structure with parent/child relationships
    3. Calculate depth for each PG (for bottom-up processing)
    4. Track processor counts per PG (for virtual grouping decisions)
    """
    
    def __init__(self):
        super().__init__(
            name="discovery_node",
            description="Discover flow components and build PG hierarchy",
            allowed_phases=["Review"]
        )
        self.successors = {}
    
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare discovery context."""
        # Defensive check: ensure shared is a dict
        if not isinstance(shared, dict):
            self.bound_logger.error(f"DiscoveryNode.prep_async: shared is not a dict! Type: {type(shared)}")
            raise ValueError(f"shared must be a dict in prep_async, got {type(shared).__name__}")
        
        await super().prep_async(shared)
        # Store shared state reference for token cost tracking
        self.set_shared_state(shared)
        
        config = shared.get("config", {})
        if not isinstance(config, dict):
            self.bound_logger.warning(f"config is not a dict, using empty dict. Type: {type(config)}")
            config = {}
        config = config.get("discovery", {})
        
        return {
            "process_group_id": shared.get("process_group_id", "root"),
            "continuation_token": shared.get("continuation_token"),
            "flow_graph": shared.get("flow_graph", {}),
            "pg_tree": shared.get("pg_tree", {}),
            "nifi_server_id": shared.get("nifi_server_id"),
            "user_request_id": shared.get("user_request_id"),
            "workflow_id": shared.get("workflow_id", "flow_documentation"),
            "step_id": self.name,  # Include step_id for event emission
            "current_phase": shared.get("current_phase", "DISCOVERY"),  # Include current phase for event emission
            
            # Configuration
            "timeout_seconds": config.get("timeout_seconds", 120),
            "max_depth": config.get("max_depth", 10),
            "batch_size": config.get("batch_size", 50),
            
            # Metrics
            "chunk_start_time": time.time(),
            "discovery_start_time": shared.get("metrics", {}).get(
                "discovery", {}
            ).get("start_time", time.time()),
            
            "_shared": shared  # Pass shared state reference for skip checks
        }
    
    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        """Execute discovery - fetch components and build hierarchy."""
        
        # Check if discovery should be skipped
        shared = prep_res.get("_shared", {})
        if shared.get("skip_discovery"):
            self.bound_logger.info("Discovery phase skipped - using loaded shared state")
            return {
                "status": "skipped",
                "message": "Discovery skipped - using loaded shared state",
                "flow_graph": shared.get("flow_graph", {}),
                "pg_tree": shared.get("pg_tree", {}),
                "discovery_complete": True
            }
        
        pg_id = prep_res["process_group_id"]
        token = prep_res.get("continuation_token")
        
        self.bound_logger.info(
            f"Discovery chunk: PG={pg_id}, token={token is not None}"
        )
        
        # 1. Fetch processors with streaming
        try:
            result = await self.call_nifi_tool(
                "list_nifi_objects_with_streaming",
                {
                    "object_type": "processors",
                    "process_group_id": pg_id,
                    "timeout_seconds": prep_res["timeout_seconds"],
                    "max_depth": prep_res["max_depth"],
                    "continuation_token": token,
                    "batch_size": prep_res["batch_size"]
                },
                prep_res
            )
            
            if isinstance(result, str):
                result = json.loads(result)
            
            # Check for errors in the result before processing
            errors_in_result = []
            if isinstance(result, dict):
                for pg_result in result.get("results", []):
                    if "error" in pg_result:
                        errors_in_result.append({
                            "pg_id": pg_result.get("process_group_id", "unknown"),
                            "pg_name": pg_result.get("process_group_name", "unknown"),
                            "error": pg_result.get("error", "Unknown error")
                        })
            
            # Log errors but continue if we have some data
            if errors_in_result:
                error_summary = "; ".join([f"{e['pg_name']}: {e['error'][:100]}" for e in errors_in_result[:3]])
                self.bound_logger.warning(f"Some errors during processor discovery: {error_summary}")
            
        except Exception as e:
            error_str = str(e)
            # Check for authentication errors - fail fast, don't retry
            if "401" in error_str or "Session Expired" in error_str or "Authentication" in error_str or "expired" in error_str.lower():
                self.bound_logger.error(f"Authentication error during discovery: {e}")
                return {
                    "status": "error",
                    "error": f"Authentication failed: {error_str}. Please provide a new OIDC token.",
                    "error_type": "authentication",
                    "should_retry": False  # Don't retry auth errors
                }
            self.bound_logger.error(f"Discovery failed: {e}")
            return {"status": "error", "error": str(e), "should_retry": True}
        
        # Extract processors with parent PG tracking
        new_processors = {}
        pg_processor_counts = {}
        
        # Check for errors in the result
        errors_in_result = []
        if isinstance(result, dict):
            for pg_result in result.get("results", []):
                if "error" in pg_result:
                    errors_in_result.append({
                        "pg_id": pg_result.get("process_group_id", "unknown"),
                        "pg_name": pg_result.get("process_group_name", "unknown"),
                        "error": pg_result.get("error", "Unknown error")
                    })
                else:
                    # Only process non-error results
                    parent_pg_id = pg_result.get("process_group_id", pg_id)
                    for proc in pg_result.get("objects", []):
                        proc_id = proc.get("id")
                        if proc_id:
                            proc["_parent_pg_id"] = parent_pg_id
                            new_processors[proc_id] = proc
                            pg_processor_counts[parent_pg_id] = \
                                pg_processor_counts.get(parent_pg_id, 0) + 1
            
            has_more = not result.get("completed", True)
            new_token = result.get("continuation_token")
        
        # If we have errors and no processors found, this is critical
        if errors_in_result and len(new_processors) == 0:
            error_summary = "; ".join([f"{e['pg_name']} ({e['pg_id'][:8]}): {e['error']}" for e in errors_in_result[:3]])
            self.bound_logger.error(f"Critical discovery errors with no data recovered: {error_summary}")
            return {
                "status": "error",
                "error": f"Failed to discover flow components. Errors: {error_summary}",
                "error_type": "discovery_failure",
                "should_retry": False
            }
        
        # Log warnings for errors but continue if we have some data
        if errors_in_result:
            error_summary = "; ".join([f"{e['pg_name']}: {e['error'][:100]}" for e in errors_in_result[:3]])
            self.bound_logger.warning(f"Some errors during processor discovery (but continuing with partial data): {error_summary}")
            
        elif isinstance(result, list):
            for proc in result:
                proc_id = proc.get("id")
                if proc_id:
                    proc["_parent_pg_id"] = pg_id
                    new_processors[proc_id] = proc
                    pg_processor_counts[pg_id] = pg_processor_counts.get(pg_id, 0) + 1
            has_more = False
            new_token = None
        else:
            has_more = False
            new_token = None
        
        # 2. Fetch process groups to build hierarchy
        root_pg_name = None
        try:
            pg_result = await self.call_nifi_tool(
                "list_nifi_objects",
                {
                    "object_type": "process_groups",
                    "process_group_id": pg_id,
                    "search_scope": "recursive"
                },
                prep_res
            )
            
            if isinstance(pg_result, str):
                pg_result = json.loads(pg_result)
            
            process_groups = []
            if isinstance(pg_result, dict):
                # Root PG info is at top level of response
                root_pg_name = pg_result.get("name")
                # Child PGs are in nested structure
                if "child_process_groups" in pg_result:
                    # Flatten child_process_groups structure
                    def flatten_pgs(pg_list, parent_id=None):
                        result = []
                        for pg in pg_list:
                            pg_obj = {
                                "id": pg.get("id"),
                                "component": {
                                    "id": pg.get("id"),
                                    "name": pg.get("name"),
                                    "parentGroupId": parent_id or pg_id
                                }
                            }
                            result.append(pg_obj)
                            # Recursively add children
                            if pg.get("children"):
                                result.extend(flatten_pgs(pg.get("children", []), pg.get("id")))
                        return result
                    process_groups = flatten_pgs(pg_result.get("child_process_groups", []))
                else:
                    # Fallback: check results array
                    for r in pg_result.get("results", []):
                        process_groups.extend(r.get("objects", []))
            elif isinstance(pg_result, list):
                process_groups = pg_result
                
        except Exception as e:
            error_str = str(e)
            # Check for authentication errors - fail fast
            if "401" in error_str or "Session Expired" in error_str or "Authentication" in error_str or "expired" in error_str.lower():
                self.bound_logger.error(f"Authentication error during PG fetch: {e}")
                raise  # Re-raise to stop workflow
            self.bound_logger.warning(f"PG fetch failed: {e}")
            process_groups = []
        
        # 3. Fetch connections
        try:
            conn_result = await self.call_nifi_tool(
                "list_nifi_objects",
                {
                    "object_type": "connections",
                    "process_group_id": pg_id,
                    "search_scope": "recursive"
                },
                prep_res
            )
            
            if isinstance(conn_result, str):
                conn_result = json.loads(conn_result)
            
            connections = []
            if isinstance(conn_result, dict):
                for r in conn_result.get("results", []):
                    connections.extend(r.get("objects", []))
            elif isinstance(conn_result, list):
                connections = conn_result
                
        except Exception as e:
            error_str = str(e)
            # Check for authentication errors - fail fast
            if "401" in error_str or "Session Expired" in error_str or "Authentication" in error_str or "expired" in error_str.lower():
                self.bound_logger.error(f"Authentication error during connection fetch: {e}")
                raise  # Re-raise to stop workflow
            self.bound_logger.warning(f"Connection fetch failed: {e}")
            connections = []
        
        # 3.5. Fetch ports (input and output)
        ports = {}
        try:
            port_result = await self.call_nifi_tool(
                "list_nifi_objects",
                {
                    "object_type": "ports",
                    "process_group_id": pg_id,
                    "search_scope": "recursive"
                },
                prep_res
            )
            
            if isinstance(port_result, str):
                port_result = json.loads(port_result)
            
            if isinstance(port_result, dict):
                for r in port_result.get("results", []):
                    for port in r.get("objects", []):
                        port_id = port.get("id")
                        if port_id:
                            ports[port_id] = port
            elif isinstance(port_result, list):
                for port in port_result:
                    port_id = port.get("id")
                    if port_id:
                        ports[port_id] = port
                        
        except Exception as e:
            error_str = str(e)
            # Check for authentication errors - fail fast
            if "401" in error_str or "Session Expired" in error_str or "Authentication" in error_str or "expired" in error_str.lower():
                self.bound_logger.error(f"Authentication error during port fetch: {e}")
                raise  # Re-raise to stop workflow
            self.bound_logger.warning(f"Port fetch failed: {e}")
            ports = {}
        
        # 3.6. Extract ports from connections (they're referenced there)
        # Also collect port IDs from connections for later fetching
        port_ids_from_connections = set()
        for conn in connections:
            if conn.get("sourceType") in ["INPUT_PORT", "OUTPUT_PORT"]:
                port_ids_from_connections.add(conn.get("sourceId"))
            if conn.get("destinationType") in ["INPUT_PORT", "OUTPUT_PORT"]:
                port_ids_from_connections.add(conn.get("destinationId"))
        
        # 3.7. Fix processor counts for PGs that had errors using flow endpoint fallback
        # This handles cases where list_processors fails but processors still exist (e.g., invalid processors)
        # Use the flow endpoint with uiOnly=true which works even when individual endpoints fail
        for error_info in errors_in_result:
            error_pg_id = error_info.get("pg_id")
            if error_pg_id and error_pg_id not in pg_processor_counts:
                # Try to get processor count and data from flow endpoint
                try:
                    from nifi_mcp_server.core import get_nifi_client
                    nifi_client = await get_nifi_client(
                        prep_res.get("nifi_server_id"),
                        bound_logger=self.bound_logger
                    )
                    flow_data = await nifi_client.get_process_group_flow(error_pg_id, ui_only=True)
                    if flow_data and "processGroupFlow" in flow_data:
                        flow = flow_data["processGroupFlow"].get("flow", {})
                        processor_count = len(flow.get("processors", []))
                        if processor_count > 0:
                            pg_processor_counts[error_pg_id] = processor_count
                            self.bound_logger.info(
                                f"Fixed processor count for PG {error_pg_id} using flow endpoint: {processor_count}"
                            )
                            # Also extract processors from flow endpoint if we didn't get them before
                            for proc in flow.get("processors", []):
                                proc_id = proc.get("id")
                                if proc_id and proc_id not in new_processors:
                                    proc_component = proc.get("component", {})
                                    # Normalize processor structure to match expected format
                                    new_processors[proc_id] = {
                                        "id": proc_id,
                                        "name": proc_component.get("name"),
                                        "type": proc_component.get("type"),
                                        "state": proc_component.get("state"),
                                        "validationStatus": proc_component.get("validationStatus"),
                                        "validationErrors": proc_component.get("validationErrors", []),
                                        "relationships": proc_component.get("relationships", []),
                                        "properties": proc_component.get("config", {}).get("properties", {}),
                                        "_parent_pg_id": error_pg_id
                                    }
                            # Extract ports from flow endpoint if available
                            for input_port in flow.get("inputPorts", []):
                                port_id = input_port.get("id")
                                if port_id:
                                    port_component = input_port.get("component", {})
                                    ports[port_id] = {
                                        "id": port_id,
                                        "name": port_component.get("name"),
                                        "type": "INPUT_PORT",
                                        "state": port_component.get("state"),
                                        "parentGroupId": port_component.get("parentGroupId", error_pg_id)
                                    }
                            for output_port in flow.get("outputPorts", []):
                                port_id = output_port.get("id")
                                if port_id:
                                    port_component = output_port.get("component", {})
                                    ports[port_id] = {
                                        "id": port_id,
                                        "name": port_component.get("name"),
                                        "type": "OUTPUT_PORT",
                                        "state": port_component.get("state"),
                                        "parentGroupId": port_component.get("parentGroupId", error_pg_id)
                                    }
                except Exception as fallback_error:
                    self.bound_logger.warning(
                        f"Flow endpoint fallback failed for PG {error_pg_id}: {fallback_error}"
                    )
        
        # 4. Build PG tree structure
        # root_pg_name was already extracted from API response above (line 286)
        pg_tree = self._build_pg_tree(pg_id, process_groups, pg_processor_counts, root_pg_name)
        
        elapsed_ms = int((time.time() - prep_res["chunk_start_time"]) * 1000)
        
        return {
            "status": "success" if not has_more else "continue",
            "new_processors": new_processors,
            "new_connections": connections,
            "new_ports": ports,
            "process_groups": process_groups,
            "pg_tree": pg_tree,
            "continuation_token": new_token,
            "has_more": has_more,
            "metrics": {
                "processors_found": len(new_processors),
                "connections_found": len(connections),
                "ports_found": len(ports),
                "process_groups_found": len(process_groups),
                "chunk_elapsed_ms": elapsed_ms
            }
        }
    
    def _build_pg_tree(
        self, 
        root_pg_id: str, 
        process_groups: List[Dict],
        processor_counts: Dict[str, int],
        root_pg_name: Optional[str] = None
    ) -> Dict[str, Dict]:
        """Build hierarchical PG tree with depth tracking."""
        tree = {}
        
        # Add root - use actual name if available, otherwise use ID prefix
        tree[root_pg_id] = {
            "id": root_pg_id,
            "name": root_pg_name or root_pg_id[:8],
            "parent": None,
            "children": [],
            "depth": 0,
            "processor_count": processor_counts.get(root_pg_id, 0)
        }
        
        # Build parent->children relationships
        for pg in process_groups:
            pg_id = pg.get("id")
            parent_id = pg.get("component", {}).get("parentGroupId", root_pg_id)
            name = pg.get("component", {}).get("name", pg_id[:8])
            
            if pg_id and pg_id != root_pg_id:
                tree[pg_id] = {
                    "id": pg_id,
                    "name": name,
                    "parent": parent_id,
                    "children": [],
                    "depth": 0,
                    "processor_count": processor_counts.get(pg_id, 0)
                }
                
                if parent_id in tree:
                    tree[parent_id]["children"].append(pg_id)
        
        # Calculate depths (BFS from root)
        self._calculate_depths(tree, root_pg_id)
        
        return tree
    
    def _calculate_depths(self, tree: Dict, root_id: str):
        """Calculate depth for each PG using BFS."""
        queue = [(root_id, 0)]
        
        while queue:
            pg_id, depth = queue.pop(0)
            if pg_id in tree:
                tree[pg_id]["depth"] = depth
                for child_id in tree[pg_id]["children"]:
                    queue.append((child_id, depth + 1))
    
    async def post_async(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any]
    ) -> str:
        """Update state and determine transition."""
        
        if exec_res.get("status") == "error":
            error_msg = exec_res.get("error", "")
            error_type = exec_res.get("error_type", "")
            
            # Authentication errors should fail immediately, no retries
            if error_type == "authentication" or "401" in error_msg or "Session Expired" in error_msg or "Authentication" in error_msg:
                self.bound_logger.error(f"Authentication error detected: {error_msg}")
                shared["error"] = error_msg
                shared["error_type"] = "authentication"
                return "error"  # Stop workflow immediately
            
            # Other errors can retry
            if exec_res.get("should_retry"):
                shared["discovery_retries"] = shared.get("discovery_retries", 0) + 1
                if shared["discovery_retries"] < 3:
                    self.bound_logger.warning("Discovery failed, retrying...")
                    return "continue"
            shared["error"] = error_msg
            return "error"
        
        # If discovery was skipped, use the loaded data
        if exec_res.get("status") == "skipped":
            self.bound_logger.info("Discovery was skipped - preserving loaded shared state")
            # The shared state should already have flow_graph and pg_tree from initial_shared_state
            # Ensure discovery_complete is set and calculate depths from pg_tree
            shared["discovery_complete"] = True
            
            # Calculate max_depth and current_depth from pg_tree (same as normal discovery completion)
            pg_tree = shared.get("pg_tree", {})
            if isinstance(pg_tree, dict) and pg_tree:
                max_depth = max(
                    (pg.get("depth", 0) for pg in pg_tree.values() if isinstance(pg, dict)), 
                    default=0
                )
                shared["max_depth"] = max_depth
                shared["current_depth"] = max_depth  # Start from deepest (leaves)
                self.bound_logger.info(f"Calculated depths from loaded pg_tree: max_depth={max_depth}, current_depth={max_depth}")
            else:
                # Fallback if pg_tree is missing or invalid
                self.bound_logger.warning("pg_tree missing or invalid in skipped discovery, using defaults")
                shared["max_depth"] = 0
                shared["current_depth"] = 0
            
            shared["current_phase"] = "ANALYSIS"
            return "complete"  # -> HierarchicalAnalysisNode
        
        # Validate that we have essential data before merging
        new_processors = exec_res.get("new_processors", {})
        new_connections = exec_res.get("new_connections", [])
        process_groups = exec_res.get("process_groups", [])
        pg_tree = exec_res.get("pg_tree", {})
        
        # Check if root PG is in the tree
        root_pg_id = shared.get("process_group_id", "root")
        if root_pg_id not in pg_tree and pg_tree:
            self.bound_logger.warning(f"Root PG {root_pg_id} not found in pg_tree. Available: {list(pg_tree.keys())[:5]}")
        
        # If we have no data at all after discovery, this is critical
        total_components = len(new_processors) + len(new_connections) + len(process_groups)
        existing_components = len(shared.get("flow_graph", {}).get("processors", {}))
        
        if total_components == 0 and existing_components == 0:
            self.bound_logger.error("Discovery completed with no components found. This is a critical failure.")
            shared["error"] = "Discovery phase found no components (processors, connections, or process groups). Cannot proceed with analysis."
            return "error"
        
        # Merge discovered data
        flow_graph = shared.get("flow_graph", {})
        
        processors = flow_graph.get("processors", {})
        processors.update(new_processors)
        flow_graph["processors"] = processors
        
        existing_conn_ids = {c.get("id") for c in flow_graph.get("connections", [])}
        for conn in exec_res.get("new_connections", []):
            if conn.get("id") not in existing_conn_ids:
                flow_graph.setdefault("connections", []).append(conn)
        
        pgs = flow_graph.get("process_groups", {})
        for pg in exec_res.get("process_groups", []):
            pgs[pg.get("id")] = pg
        flow_graph["process_groups"] = pgs
        
        # Merge ports
        existing_ports = flow_graph.get("ports", {})
        new_ports = exec_res.get("new_ports", {})
        existing_ports.update(new_ports)
        flow_graph["ports"] = existing_ports
        
        shared["flow_graph"] = flow_graph
        
        # Merge PG tree
        existing_tree = shared.get("pg_tree", {})
        new_tree = exec_res.get("pg_tree", {})
        
        # Defensive check: ensure both are dicts
        if not isinstance(existing_tree, dict):
            self.bound_logger.warning(
                f"existing_tree is not a dict, resetting. Type: {type(existing_tree)}"
            )
            existing_tree = {}
        
        if not isinstance(new_tree, dict):
            self.bound_logger.warning(
                f"new_tree from exec_res is not a dict, skipping merge. Type: {type(new_tree)}"
            )
            new_tree = {}
        
        existing_tree.update(new_tree)
        shared["pg_tree"] = existing_tree
        
        shared["continuation_token"] = exec_res.get("continuation_token")
        
        # Update metrics
        metrics = shared.get("metrics", {}).get("discovery", {})
        metrics["api_calls"] = metrics.get("api_calls", 0) + 3
        metrics["total_processors"] = len(flow_graph.get("processors", {}))
        metrics["total_connections"] = len(flow_graph.get("connections", []))
        metrics["total_process_groups"] = len(shared.get("pg_tree", {}))
        shared.setdefault("metrics", {})["discovery"] = metrics
        
        await self.emit_doc_phase_event(
            EventTypes.DOC_DISCOVERY_CHUNK,
            "DISCOVERY",
            shared,
            metrics=exec_res.get("metrics", {}),
            progress_message=f"Discovered {metrics['total_processors']} processors in "
                           f"{metrics['total_process_groups']} process groups..."
        )
        
        if exec_res.get("has_more"):
            return "continue"  # Self-loop
        
        # Discovery complete - set starting point for hierarchical analysis
        pg_tree = shared.get("pg_tree", {})
        
        # Defensive check: ensure pg_tree is a dict
        if not isinstance(pg_tree, dict):
            self.bound_logger.error(
                f"pg_tree is not a dict after discovery! Type: {type(pg_tree)}, Value: {pg_tree}"
            )
            shared["error"] = f"pg_tree must be a dict, got {type(pg_tree).__name__}"
            return "error"
        
        max_depth = max(
            (pg.get("depth", 0) for pg in pg_tree.values() if isinstance(pg, dict)), 
            default=0
        )
        shared["max_depth"] = max_depth
        shared["current_depth"] = max_depth  # Start from deepest (leaves)
        
        shared["discovery_complete"] = True
        shared["current_phase"] = "ANALYSIS"
        
        discovery_duration = time.time() - prep_res.get("discovery_start_time", time.time())
        metrics["total_duration_ms"] = int(discovery_duration * 1000)
        
        await self.emit_doc_phase_event(
            EventTypes.DOC_PHASE_COMPLETE,
            "DISCOVERY",
            shared,
            metrics=metrics,
            progress_message=f"Discovery complete: {metrics['total_processors']} processors, "
                           f"{metrics['total_process_groups']} PGs, max depth {max_depth}"
        )
        
        self.bound_logger.info(
            f"Discovery complete: {metrics['total_processors']} processors, "
            f"{metrics['total_process_groups']} PGs, max depth {max_depth}"
        )
        
        # Save shared state snapshot for debugging
        try:
            import os
            from pathlib import Path
            
            debug_dir = Path("logs/debug")
            debug_dir.mkdir(parents=True, exist_ok=True)
            
            request_id = shared.get("user_request_id", "unknown")
            snapshot_file = debug_dir / f"discovery_shared_state_{request_id}.json"
            
            # Create a serializable copy (remove non-serializable objects)
            snapshot = {}
            for key, value in shared.items():
                try:
                    json.dumps(value)  # Test if serializable
                    snapshot[key] = value
                except (TypeError, ValueError):
                    # Store type and size info for non-serializable objects
                    snapshot[key] = {
                        "_type": type(value).__name__,
                        "_size": len(value) if hasattr(value, "__len__") else "unknown",
                        "_note": "Non-serializable object - see workflow.log for details"
                    }
            
            with open(snapshot_file, 'w') as f:
                json.dump(snapshot, f, indent=2, default=str)
            
            self.bound_logger.info(f"Saved discovery shared state snapshot to {snapshot_file}")
        except Exception as e:
            self.bound_logger.warning(f"Failed to save discovery shared state snapshot: {e}")
        
        return "complete"  # -> HierarchicalAnalysisNode


class HierarchicalAnalysisNode(AsyncNiFiWorkflowNode):
    """
    Analyze process groups hierarchically from leaves to root.
    
    Key algorithm:
    1. Process all PGs at current_depth
    2. For each PG:
       a. If large (>threshold processors): Create virtual sub-groups first
       b. Get child PG summaries (already computed in previous iterations)
       c. Get virtual group summaries (if created)
       d. Analyze PG using summaries (not raw child processors)
       e. Store summary for parent to use
    3. Decrement current_depth, self-loop until depth 0
    4. Root analysis produces final aggregated understanding
    """
    
    def __init__(self):
        super().__init__(
            name="hierarchical_analysis_node",
            description="Analyze PGs bottom-up with virtual grouping",
            allowed_phases=["Review"]
        )
        self.successors = {}
        
        # Initialize helpers (lazy import to avoid circular deps)
        self.categorizer = None
        self.token_counter = None
    
    def _init_helpers(self):
        """Lazy initialization of helpers."""
        if self.categorizer is None:
            from nifi_mcp_server.processor_categories import get_categorizer
            from nifi_chat_ui.llm.utils.token_counter import TokenCounter
            
            self.categorizer = get_categorizer()
            self.token_counter = TokenCounter()
    
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare analysis context for current depth level."""
        # Defensive check: ensure shared is a dict
        if not isinstance(shared, dict):
            self.bound_logger.error(f"HierarchicalAnalysisNode.prep_async: shared is not a dict! Type: {type(shared)}")
            raise ValueError(f"shared must be a dict in prep_async, got {type(shared).__name__}")
        
        await super().prep_async(shared)
        # Store shared state reference for token cost tracking
        self.set_shared_state(shared)
        self._init_helpers()
        
        config = shared.get("config", {})
        if not isinstance(config, dict):
            self.bound_logger.warning(f"config is not a dict, using empty dict. Type: {type(config)}")
            config = {}
        config = config.get("analysis", {})
        
        # Defensive check: ensure pg_tree is a dict
        pg_tree = shared.get("pg_tree", {})
        if not isinstance(pg_tree, dict):
            self.bound_logger.error(
                f"pg_tree in shared state is not a dict! Type: {type(pg_tree)}, Value: {str(pg_tree)[:200]}"
            )
            pg_tree = {}  # Reset to empty dict to prevent downstream errors
        
        return {
            "flow_graph": shared.get("flow_graph", {}),
            "pg_tree": pg_tree,
            "pg_summaries": shared.get("pg_summaries", {}),
            "virtual_groups": shared.get("virtual_groups", {}),
            "current_depth": shared.get("current_depth", 0),
            "max_depth": shared.get("max_depth", 0),
            "provider": shared.get("provider", "openai"),
            "model_name": shared.get("model_name", "gpt-4"),
            "nifi_server_id": shared.get("nifi_server_id"),
            "user_request_id": shared.get("user_request_id"),
            "workflow_id": shared.get("workflow_id", "flow_documentation"),
            "step_id": self.name,  # Include step_id for event emission
            "current_phase": shared.get("current_phase", "ANALYSIS"),  # Include current phase for event emission
            
            # Configuration
            "large_pg_threshold": config.get("large_pg_threshold", 25),
            "max_tokens_per_analysis": config.get("max_tokens_per_analysis", 8000),
            "min_virtual_groups": config.get("min_virtual_groups", 3),
            "max_virtual_groups": config.get("max_virtual_groups", 7),
            
            "level_start_time": time.time(),
            
            "_shared": shared  # Pass shared state reference for skip checks
        }
    
    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        """Process all PGs at current depth level."""
        
        # Check if analysis should be skipped
        shared = prep_res.get("_shared", {})
        if shared.get("skip_analysis"):
            self.bound_logger.info("Analysis phase skipped - using loaded shared state")
            return {
                "status": "skipped",
                "message": "Analysis skipped - using loaded shared state",
                "new_summaries": shared.get("pg_summaries", {}),
                "new_virtual_groups": shared.get("virtual_groups", {})
            }
        
        current_depth = prep_res.get("current_depth")
        if current_depth is None:
            # Defensive: if current_depth is None, try to get from shared state or default to 0
            current_depth = shared.get("current_depth", 0)
            self.bound_logger.warning(f"current_depth was None in prep_res, using {current_depth} from shared state")
        
        pg_tree = prep_res.get("pg_tree", {})
        
        # Use workflow_logger which writes to workflow.log
        self.workflow_logger.bind(
            interface="workflow",
            data={
                "node_name": self.name,
                "action": "exec",
                "current_depth": current_depth,
                "pg_tree_size": len(pg_tree) if isinstance(pg_tree, dict) else 0,
                "pg_tree_type": type(pg_tree).__name__
            }
        ).info(f"HierarchicalAnalysisNode.exec_async starting: depth={current_depth}, tree_size={len(pg_tree) if isinstance(pg_tree, dict) else 0}")
        
        # Defensive check: ensure pg_tree is a dict, not a string
        if not isinstance(pg_tree, dict):
            self.bound_logger.error(
                f"pg_tree is not a dict! Type: {type(pg_tree)}, Value: {pg_tree}"
            )
            return {
                "status": "error",
                "error": f"pg_tree must be a dict, got {type(pg_tree).__name__}"
            }
        
        threshold = prep_res["large_pg_threshold"]
        
        # Debug: Log all PG depths
        all_depths = [pg.get('depth') for pg in pg_tree.values() if isinstance(pg, dict)]
        
        pgs_at_depth = [
            pg for pg in pg_tree.values() 
            if isinstance(pg, dict) and pg.get("depth") == current_depth
        ]
        
        # Use workflow_logger for visibility in workflow.log
        self.workflow_logger.bind(
            interface="workflow",
            data={
                "node_name": self.name,
                "action": "exec",
                "current_depth": current_depth,
                "pgs_at_depth_count": len(pgs_at_depth),
                "all_depths": sorted(set(all_depths)),
                "pg_tree_size": len(pg_tree)
            }
        ).info(f"Analyzing depth {current_depth}: {len(pgs_at_depth)} process groups (all depths: {sorted(set(all_depths))})")
        
        if len(pgs_at_depth) == 0:
            self.workflow_logger.bind(
                interface="workflow",
                data={
                    "node_name": self.name,
                    "action": "exec",
                    "current_depth": current_depth,
                    "all_depths": sorted(set(all_depths)),
                    "pg_tree_size": len(pg_tree)
                }
            ).warning(f"No PGs found at depth {current_depth}. Available depths: {sorted(set(all_depths))}")
            # Return early with empty summaries if no PGs at this depth
            return {
                "status": "success",
                "new_summaries": {},
                "new_virtual_groups": {},
                "metrics": {
                    "depth": current_depth,
                    "pgs_at_depth": 0,
                    "virtual_groups_created": 0,
                    "llm_calls": 0,
                    "level_duration_ms": 0
                }
            }
        
        new_summaries = {}
        new_virtual_groups = {}
        metrics = {
            "depth": current_depth,
            "pgs_at_depth": len(pgs_at_depth),
            "virtual_groups_created": 0,
            "llm_calls": 0
        }
        
        # Wrap the analysis loop in try/except to catch and handle exceptions gracefully
        try:
            for pg in pgs_at_depth:
                pg_id = pg["id"]
                pg_name = pg.get("name", pg_id[:8])
                proc_count = pg.get("processor_count", 0)
                
                self.workflow_logger.bind(
                    interface="workflow",
                    data={
                        "node_name": self.name,
                        "action": "exec",
                        "pg_id": pg_id,
                        "pg_name": pg_name,
                        "proc_count": proc_count
                    }
                ).info(f"  Analyzing PG '{pg_name}' ({proc_count} processors)")
                
                # Get this PG's direct processors
                direct_processors = get_pg_processors(pg_id, prep_res)
                self.workflow_logger.bind(
                    interface="workflow",
                    data={"node_name": self.name, "action": "exec", "pg_name": pg_name}
                ).info(f"    Found {len(direct_processors)} direct processors for '{pg_name}'")
                
                # Get child PG summaries (already computed)
                # DEFENSIVE: Validate each summary is a dict before including
                child_summaries = []
                for child_id in pg.get("children", []):
                    if child_id in prep_res["pg_summaries"]:
                        child_summary = prep_res["pg_summaries"][child_id]
                        if isinstance(child_summary, dict):
                            child_summaries.append(child_summary)
                        else:
                            self.bound_logger.warning(
                                f"Child summary for {child_id} is not a dict: {type(child_summary)}. Skipping."
                            )
                
                # Check if we need virtual grouping (large PG)
                virtual_group_summaries = []
                if proc_count > threshold:
                    self.bound_logger.info(
                        f"    Large PG detected ({proc_count} > {threshold}), "
                        f"creating virtual groups..."
                    )
                    
                    # Get connections for this PG
                    connections = []
                    for c in prep_res["flow_graph"].get("connections", []):
                        parent_id = c.get("component", {}).get("parentGroupId") or c.get("sourceGroupId")
                        if parent_id == pg_id:
                            connections.append(c)
                    
                    virtual_groups = await create_virtual_groups(
                        pg_id, direct_processors, connections,
                        self.call_llm_async, prep_res, self.categorizer, self.bound_logger
                    )
                    
                    if virtual_groups:
                        new_virtual_groups[pg_id] = virtual_groups
                        metrics["virtual_groups_created"] += len(virtual_groups)
                        
                        for vg in virtual_groups:
                            vg_summary = await analyze_virtual_group(
                                vg, direct_processors,
                                self.call_nifi_tool, self.call_llm_async, prep_res, pg_id,
                                extract_io_endpoints_detailed, extract_error_handling,
                                self.categorizer, self.bound_logger
                            )
                            virtual_group_summaries.append(vg_summary)
                            metrics["llm_calls"] += 1
                
                # Combine all child summaries (real + virtual)
                all_child_summaries = child_summaries + virtual_group_summaries
                
                # Analyze this PG
                self.workflow_logger.bind(
                    interface="workflow",
                    data={"node_name": self.name, "action": "exec", "pg_name": pg_name}
                ).info(f"    Calling analysis method for '{pg_name}' (has_children={len(all_child_summaries) > 0})")
                
                try:
                    if all_child_summaries:
                        # Parent PG has children - include both children AND parent's own processors
                        summary = await analyze_pg_with_summaries(
                            pg, all_child_summaries, direct_processors,
                            self.call_nifi_tool, self.call_llm_async, prep_res,
                            extract_io_endpoints_detailed, extract_error_handling,
                            self.categorizer, self.bound_logger
                        )
                    else:
                        # Leaf PG - only has its own processors
                        summary = await analyze_pg_direct(
                            pg, direct_processors,
                            self.call_nifi_tool, self.call_llm_async, prep_res,
                            extract_io_endpoints_detailed, extract_error_handling,
                            self.categorizer, self.bound_logger
                        )
                    self.workflow_logger.bind(
                        interface="workflow",
                        data={"node_name": self.name, "action": "exec", "pg_name": pg_name}
                    ).info(f"    Analysis complete for '{pg_name}', summary keys: {list(summary.keys()) if isinstance(summary, dict) else 'NOT_A_DICT'}")
                except Exception as pg_error:
                    self.workflow_logger.bind(
                        interface="workflow",
                        data={"node_name": self.name, "action": "exec", "pg_name": pg_name}
                    ).error(f"    ERROR analyzing PG '{pg_name}': {pg_error}", exc_info=True)
                    raise  # Re-raise to be caught by outer try/except
                
                # Extract error handling information (lightweight analysis)
                # Include error handling from parent's own processors (if any)
                # Connections can have parentGroupId in component.parentGroupId OR sourceGroupId at top level
                pg_connections = []
                for c in prep_res["flow_graph"].get("connections", []):
                    parent_id = c.get("component", {}).get("parentGroupId") or c.get("sourceGroupId")
                    if parent_id == pg_id:
                        pg_connections.append(c)
                parent_error_handling = []
                if direct_processors:
                    parent_error_handling = await extract_error_handling(
                        direct_processors, pg_connections,
                        self.call_nifi_tool, prep_res, None, self.bound_logger
                    )
                # summary["error_handling"] already contains errors from children (if any)
                # Add parent's error handling
                if isinstance(summary.get("error_handling"), list):
                    summary["error_handling"].extend(parent_error_handling)
                else:
                    summary["error_handling"] = parent_error_handling
                
                # Aggregate error handling from children (if not already in summary)
                for child_summary in all_child_summaries:
                    # DEFENSIVE: Ensure child_summary is a dict before calling .get()
                    if isinstance(child_summary, dict):
                        child_errors = child_summary.get("error_handling", [])
                        if isinstance(child_errors, list):
                            summary["error_handling"].extend(child_errors)
                    else:
                        self.bound_logger.warning(
                            f"child_summary is not a dict: {type(child_summary)}. Skipping error aggregation."
                        )
                
                metrics["llm_calls"] += 1
                new_summaries[pg_id] = summary
                
                await self.emit_doc_phase_event(
                    EventTypes.DOC_ANALYSIS_BATCH,
                    "ANALYSIS",
                    prep_res,
                    metrics={"pg_name": pg_name, "depth": current_depth},
                    progress_message=f"Analyzed '{pg_name}' at depth {current_depth}"
                )
        except Exception as e:
            # Log the full exception with traceback for debugging
            import traceback
            tb_str = traceback.format_exc()
            self.workflow_logger.bind(
                interface="workflow",
                data={
                    "node_name": self.name,
                    "action": "exec",
                    "current_depth": current_depth,
                    "error": str(e),
                    "traceback": tb_str
                }
            ).error(f"ERROR during analysis at depth {current_depth}: {e}")
            self.bound_logger.error(
                f"Error during analysis at depth {current_depth}: {e}",
                exc_info=True
            )
            return {
                "status": "error",
                "error": f"Analysis failed at depth {current_depth}: {str(e)}",
                "error_type": "analysis_exception",
                "partial_summaries": new_summaries,  # Return what we have so far
                "metrics": metrics,
                "traceback": tb_str
            }
        
        elapsed_ms = int((time.time() - prep_res["level_start_time"]) * 1000)
        metrics["level_duration_ms"] = elapsed_ms
        
        return {
            "status": "success",
            "new_summaries": new_summaries,
            "new_virtual_groups": new_virtual_groups,
            "metrics": metrics
        }
    
    async def post_async(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any]
    ) -> str:
        """Update state and determine next transition."""
        
        # DEFENSIVE: Ensure exec_res is a dict before calling .get()
        if not isinstance(exec_res, dict):
            self.bound_logger.error(f"exec_res is not a dict in HierarchicalAnalysisNode.post_async! Type: {type(exec_res)}")
            shared["error"] = f"Invalid exec_res type: {type(exec_res).__name__}"
            return "error"
        
        if exec_res.get("status") == "error":
            shared["error"] = exec_res.get("error")
            return "error"
        
        # If analysis was skipped, use the loaded data
        if exec_res.get("status") == "skipped":
            self.bound_logger.info("Analysis was skipped - preserving loaded shared state")
            # The shared state should already have pg_summaries and virtual_groups from initial_shared_state
            # Just ensure current_phase is updated
            shared["current_phase"] = "GENERATION"
            return "complete"  # -> DocumentationNode
        
        pg_summaries = shared.get("pg_summaries", {})
        if not isinstance(pg_summaries, dict):
            self.bound_logger.error(f"pg_summaries is not a dict! Type: {type(pg_summaries)}")
            pg_summaries = {}
        
        new_summaries = exec_res.get("new_summaries", {})
        if not isinstance(new_summaries, dict):
            self.bound_logger.error(f"new_summaries is not a dict! Type: {type(new_summaries)}")
            new_summaries = {}
        
        # DEFENSIVE: Validate each summary is a dict before storing
        validated_summaries = {}
        for pg_id, summary in new_summaries.items():
            if isinstance(summary, dict):
                validated_summaries[pg_id] = summary
            else:
                self.bound_logger.warning(
                    f"Summary for PG {pg_id} is not a dict: {type(summary)}. Skipping storage."
                )
        
        # Save analysis shared state snapshot for debugging (similar to discovery phase)
        try:
            from pathlib import Path
            request_id = prep_res.get("user_request_id", "unknown")
            debug_dir = Path("logs/debug")
            debug_dir.mkdir(parents=True, exist_ok=True)
            
            snapshot_file = debug_dir / f"analysis_shared_state_{request_id}.json"
            
            # Create a serializable copy (remove non-serializable objects)
            snapshot = {}
            for key, value in shared.items():
                try:
                    json.dumps(value)  # Test if serializable
                    snapshot[key] = value
                except (TypeError, ValueError):
                    # Store type and size info for non-serializable objects
                    snapshot[key] = {
                        "_type": type(value).__name__,
                        "_size": len(value) if hasattr(value, "__len__") else "unknown",
                        "_note": "Non-serializable object - see workflow.log for details"
                    }
            
            with open(snapshot_file, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2, default=str)
            
            self.bound_logger.info(f"Saved analysis shared state snapshot to {snapshot_file}")
        except Exception as e:
            self.bound_logger.warning(f"Failed to save analysis shared state snapshot: {e}")
        
        # Diagnostic logging to debug pg_summaries data flow
        self.bound_logger.info(
            f"AnalysisNode post: new_summaries count = {len(new_summaries)}, validated = {len(validated_summaries)}"
        )
        self.bound_logger.info(
            f"AnalysisNode post: new_summaries keys = {list(new_summaries.keys())}"
        )
        self.bound_logger.info(
            f"AnalysisNode post: shared pg_summaries count before update = {len(pg_summaries)}"
        )
        
        pg_summaries.update(validated_summaries)
        shared["pg_summaries"] = pg_summaries
        
        self.bound_logger.info(
            f"AnalysisNode post: shared pg_summaries count after update = {len(shared.get('pg_summaries', {}))}"
        )
        self.bound_logger.info(
            f"AnalysisNode post: final pg_summaries keys = {list(shared.get('pg_summaries', {}).keys())}"
        )
        
        virtual_groups = shared.get("virtual_groups", {})
        virtual_groups.update(exec_res.get("new_virtual_groups", {}))
        shared["virtual_groups"] = virtual_groups
        
        analysis_metrics = shared.get("metrics", {}).get("analysis", {})
        analysis_metrics["pgs_analyzed"] = len(pg_summaries)
        analysis_metrics["virtual_groups_created"] = sum(
            len(vg) for vg in virtual_groups.values()
        )
        shared.setdefault("metrics", {})["analysis"] = analysis_metrics
        
        current_depth = prep_res["current_depth"]
        
        await self.emit_doc_phase_event(
            EventTypes.DOC_ANALYSIS_BATCH,
            "ANALYSIS",
            shared,
            metrics=exec_res.get("metrics", {}),
            progress_message=f"Completed depth {current_depth}, "
                           f"{len(exec_res.get('new_summaries', {}))} PGs analyzed"
        )
        
        if current_depth > 0:
            shared["current_depth"] = current_depth - 1
            self.bound_logger.info(f"Moving to depth {current_depth - 1}")
            return "next_level"  # Self-loop to process next level
        
        shared["current_phase"] = "GENERATION"
        
        await self.emit_doc_phase_event(
            EventTypes.DOC_PHASE_COMPLETE,
            "ANALYSIS",
            shared,
            metrics=analysis_metrics,
            progress_message=f"Hierarchical analysis complete: "
                           f"{analysis_metrics['pgs_analyzed']} PGs, "
                           f"{analysis_metrics['virtual_groups_created']} virtual groups"
        )
        
        self.bound_logger.info(
            f"Hierarchical analysis complete: {analysis_metrics['pgs_analyzed']} PGs"
        )
        
        return "complete"  # -> DocumentationNode


class DocumentationNode(AsyncNiFiWorkflowNode):
    """
    Generate final documentation from hierarchical PG summaries.
    
    Uses the pg_summaries built during hierarchical analysis to:
    1. Generate executive summary from root PG summary
    2. Generate hierarchical Mermaid diagram
    3. Build hierarchical documentation sections
    4. Aggregate all IO endpoints
    5. Assemble final Markdown document with hierarchy
    """
    
    def __init__(self):
        super().__init__(
            name="documentation_node",
            description="Generate final documentation from hierarchical summaries",
            allowed_phases=["Review"]
        )
        self.successors = {}  # Terminal node
    
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare generation context."""
        # Defensive check: ensure shared is a dict
        if not isinstance(shared, dict):
            self.bound_logger.error(f"DocumentationNode.prep_async: shared is not a dict! Type: {type(shared)}")
            raise ValueError(f"shared must be a dict in prep_async, got {type(shared).__name__}")
        
        await super().prep_async(shared)
        # Store shared state reference for token cost tracking
        self.set_shared_state(shared)
        
        config = shared.get("config", {})
        if not isinstance(config, dict):
            self.bound_logger.warning(f"config is not a dict, using empty dict. Type: {type(config)}")
            config = {}
        config = config.get("generation", {})
        pg_summaries = shared.get("pg_summaries", {})
        root_pg_id = shared.get("process_group_id", "root")
        
        # Diagnostic logging to debug pg_summaries data flow
        self.bound_logger.info(
            f"DocumentationNode prep: pg_summaries count = {len(pg_summaries)}"
        )
        self.bound_logger.info(
            f"DocumentationNode prep: pg_summaries keys = {list(pg_summaries.keys())}"
        )
        self.bound_logger.info(
            f"DocumentationNode prep: root_pg_id = {root_pg_id}"
        )
        
        # Log sample summary data to verify quality
        if pg_summaries:
            sample_pg_id = list(pg_summaries.keys())[0]
            sample_summary = pg_summaries.get(sample_pg_id, {})
            if isinstance(sample_summary, dict):
                self.bound_logger.info(
                    f"DocumentationNode prep: Sample PG summary - "
                    f"name={sample_summary.get('name')}, "
                    f"has_summary={bool(sample_summary.get('summary'))}, "
                    f"io_endpoints={len(sample_summary.get('io_endpoints', []))}, "
                    f"error_handling={len(sample_summary.get('error_handling', []))}"
                )
            else:
                self.bound_logger.warning(
                    f"DocumentationNode prep: Sample PG summary is not a dict! Type: {type(sample_summary)}"
                )
        
        self.bound_logger.info(
            f"DocumentationNode prep: root_pg_id in pg_summaries = {root_pg_id in pg_summaries}"
        )
        if root_pg_id in pg_summaries:
            root_summary = pg_summaries[root_pg_id]
            self.bound_logger.info(
                f"DocumentationNode prep: root summary has 'summary' field = {'summary' in root_summary}, "
                f"summary length = {len(root_summary.get('summary', ''))}"
            )
        else:
            self.bound_logger.warning(
                f"DocumentationNode prep: Root PG {root_pg_id} NOT found in pg_summaries! "
                f"Available keys: {list(pg_summaries.keys())}"
            )
        
        return {
            "flow_graph": shared.get("flow_graph", {}),
            "pg_tree": shared.get("pg_tree", {}),
            "pg_summaries": pg_summaries,
            "virtual_groups": shared.get("virtual_groups", {}),
            "process_group_id": root_pg_id,
            "provider": shared.get("provider", "openai"),
            "model_name": shared.get("model_name", "gpt-4"),
            "user_request_id": shared.get("user_request_id"),
            "workflow_id": shared.get("workflow_id", "flow_documentation"),
            "step_id": self.name,  # Include step_id for event emission
            "current_phase": shared.get("current_phase", "GENERATION"),  # Include current phase for event emission
            
            "max_mermaid_nodes": config.get("max_mermaid_nodes", 50),
            "summary_max_words": config.get("summary_max_words", 500),
            "include_all_io": config.get("include_all_io", True),
            
            "generation_start_time": time.time()
        }
    
    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        """Generate all documentation sections."""
        pg_summaries = prep_res.get("pg_summaries", {})
        pg_tree = prep_res.get("pg_tree", {})
        root_pg_id = prep_res.get("process_group_id", "root")
        
        self.bound_logger.info(
            f"Generating documentation from {len(pg_summaries)} PG summaries..."
        )
        
        # Validation: Check if pg_summaries is empty
        if not pg_summaries:
            # Check if there was an authentication error earlier (from prep_res which contains shared state)
            error_type = prep_res.get("error_type", "")
            error_msg_from_prep = prep_res.get("error", "")
            auth_error = (
                error_type == "authentication" or 
                "401" in str(error_msg_from_prep) or 
                "Session Expired" in str(error_msg_from_prep)
            )
            
            if auth_error:
                error_msg = f"Authentication failed during workflow execution: {error_msg_from_prep or 'Token expired or invalid'}. Please provide a new OIDC token."
            else:
                error_msg = "No process group summaries available. Analysis phase may have failed."
            
            self.bound_logger.error(f"pg_summaries is empty! {error_msg}")
            return {
                "status": "error",
                "error": error_msg,
                "error_type": "authentication" if auth_error else "data_missing",
                "doc_sections": {},
                "final_document": "",
                "validation_issues": ["No summaries available for documentation generation"],
                "metrics": {
                    "duration_ms": 0,
                    "document_size_bytes": 0,
                    "sections_generated": 0,
                    "validation_issues": 1,
                    "pgs_documented": 0
                }
            }
        
        # Validation: Check if root PG summary exists
        if root_pg_id not in pg_summaries:
            self.bound_logger.warning(
                f"Root PG {root_pg_id} not found in pg_summaries. "
                f"Available keys: {list(pg_summaries.keys())}. "
                f"Will attempt to generate documentation with available summaries."
            )
        
        sections = {}
        
        # 1. Executive Summary
        root_summary = pg_summaries.get(root_pg_id, {})
        self.bound_logger.info(f"Generating executive summary for root PG {root_pg_id}...")
        sections["summary"] = await generate_executive_summary(
            root_summary, pg_summaries, self.call_llm_async, prep_res, self.bound_logger
        )
        self.bound_logger.info(f"Executive summary generated: {len(sections['summary'])} chars")
        await self._emit_section_event("summary", prep_res)
        
        # 2. Hierarchical Mermaid Diagram
        self.bound_logger.info("Generating hierarchical Mermaid diagram...")
        sections["diagram"] = await generate_hierarchical_diagram(
            pg_tree, pg_summaries, self.call_llm_async, prep_res, self.bound_logger
        )
        self.bound_logger.info(f"Mermaid diagram generated: {len(sections['diagram'])} chars")
        await self._emit_section_event("diagram", prep_res)
        
        # 3. Hierarchical Documentation
        self.bound_logger.info("Building hierarchical documentation...")
        virtual_groups = prep_res.get("virtual_groups", {})
        sections["hierarchy_doc"] = build_hierarchical_doc(
            root_pg_id, pg_tree, pg_summaries, virtual_groups
        )
        self.bound_logger.info(f"Hierarchical doc generated: {len(sections['hierarchy_doc'])} chars")
        await self._emit_section_event("hierarchy_doc", prep_res)
        
        # 4. Aggregated IO Endpoints
        self.bound_logger.info("Building aggregated IO endpoints table...")
        sections["io_table"] = build_aggregated_io_table(pg_summaries)
        self.bound_logger.info(f"IO table generated: {len(sections['io_table'])} chars")
        await self._emit_section_event("io_table", prep_res)
        
        # 5. Error Handling Analysis
        self.bound_logger.info("Building error handling table...")
        sections["error_handling_table"] = build_error_handling_table(pg_summaries)
        self.bound_logger.info(f"Error handling table generated: {len(sections['error_handling_table'])} chars")
        await self._emit_section_event("error_handling_table", prep_res)
        
        # 6. Assemble final document
        self.bound_logger.info("Assembling final document from all sections...")
        pg_name = pg_tree.get(root_pg_id, {}).get("name", root_pg_id)
        final_document = assemble_hierarchical_document(
            pg_name,
            sections.get("summary", ""),
            sections.get("diagram", ""),
            sections.get("hierarchy_doc", ""),
            sections.get("io_table", ""),
            sections.get("error_handling_table", "")
        )
        self.bound_logger.info(f"Final document assembled: {len(final_document)} chars, {len(sections)} sections")
        
        validation_issues = validate_output(final_document, pg_summaries)
        
        elapsed_ms = int((time.time() - prep_res["generation_start_time"]) * 1000)
        
        return {
            "status": "success",
            "doc_sections": sections,
            "final_document": final_document,
            "validation_issues": validation_issues,
            "metrics": {
                "duration_ms": elapsed_ms,
                "document_size_bytes": len(final_document.encode('utf-8')),
                "sections_generated": len(sections),
                "validation_issues": len(validation_issues),
                "pgs_documented": len(pg_summaries),
                "error_handling_entries": sum(
                    len(s.get("error_handling", [])) 
                    for s in pg_summaries.values()
                )
            }
        }
    
    async def _emit_section_event(self, section: str, prep_res: Dict):
        """Emit event for section generation."""
        await self.emit_doc_phase_event(
            EventTypes.DOC_GENERATION_SECTION,
            "GENERATION",
            prep_res,
            metrics={"section": section},
            progress_message=f"Generated {section} section..."
        )
    
    async def post_async(
        self,
        shared: Dict[str, Any],
        prep_res: Dict[str, Any],
        exec_res: Dict[str, Any]
    ) -> str:
        """Store results and complete workflow."""
        
        # Check for errors from previous nodes or this node
        error = exec_res.get("error") or shared.get("error")
        if exec_res.get("status") == "error" or error:
            error_msg = exec_res.get("error", shared.get("error", "Unknown error"))
            shared["error"] = error_msg
            shared["final_document"] = f"# Documentation Generation Failed\n\n**Error:** {error_msg}\n\nThis workflow encountered an error and could not generate documentation."
            shared["doc_sections"] = {}
            shared["validation_issues"] = [f"Workflow error: {error_msg}"]
            
            self.bound_logger.error(f"Documentation workflow failed: {error_msg}")
            
            await self.emit_doc_phase_event(
                EventTypes.DOC_PHASE_COMPLETE,
                "ERROR",
                shared,
                metrics={"error": error_msg},
                progress_message=f"Workflow failed: {error_msg}"
            )
            
            # Emit workflow error event
            from ..core.event_system import emit_workflow_error
            await emit_workflow_error(
                shared.get("workflow_id", "flow_documentation"),
                "documentation_node",
                {"error": error_msg},
                shared.get("user_request_id")
            )
            
            return "error"  # Terminal - no successors
        
        shared["doc_sections"] = exec_res.get("doc_sections", {})
        shared["final_document"] = exec_res.get("final_document", "")
        shared["validation_issues"] = exec_res.get("validation_issues", [])
        
        shared.setdefault("metrics", {})["generation"] = exec_res.get("metrics", {})
        
        workflow_start = shared.get("metrics", {}).get("workflow_start_time", time.time())
        total_duration = int((time.time() - workflow_start) * 1000)
        shared["metrics"]["total_duration_ms"] = total_duration
        
        await self.emit_doc_phase_event(
            EventTypes.DOC_PHASE_COMPLETE,
            "GENERATION",
            shared,
            metrics={
                **exec_res.get("metrics", {}),
                "total_workflow_duration_ms": total_duration
            },
            progress_message="Documentation complete!"
        )
        
        for issue in exec_res.get("validation_issues", []):
            self.bound_logger.warning(f"Validation: {issue}")
        
        shared["current_phase"] = "COMPLETE"
        self.bound_logger.info(
            f"Documentation complete: {exec_res['metrics']['document_size_bytes']} bytes, "
            f"{exec_res['metrics']['pgs_documented']} PGs documented"
        )
        
        # Save generation shared state snapshot for debugging (if enabled)
        from config.settings import should_save_generation_shared_state
        if should_save_generation_shared_state():
            try:
                from pathlib import Path
                request_id = prep_res.get("user_request_id", "unknown")
                debug_dir = Path("logs/debug")
                debug_dir.mkdir(parents=True, exist_ok=True)
                
                snapshot_file = debug_dir / f"generation_shared_state_{request_id}.json"
                
                # Create a serializable copy (remove non-serializable objects)
                snapshot = {}
                for key, value in shared.items():
                    try:
                        json.dumps(value)  # Test if serializable
                        snapshot[key] = value
                    except (TypeError, ValueError):
                        # Skip non-serializable values (like function references)
                        snapshot[key] = f"<non-serializable: {type(value).__name__}>"
                
                with open(snapshot_file, "w") as f:
                    json.dump(snapshot, f, indent=2, default=str)
                
                self.bound_logger.info(f"Saved generation shared state snapshot to {snapshot_file}")
            except Exception as e:
                self.bound_logger.warning(f"Failed to save generation shared state snapshot: {e}")
        
        # Emit WORKFLOW_COMPLETE event so UI receives completion signal
        from ..core.event_system import emit_workflow_complete
        await emit_workflow_complete(
            shared.get("workflow_id", "flow_documentation"),
            self.name,
            {
                "workflow_name": "flow_documentation",
                "final_document": shared.get("final_document", ""),
                "metrics": shared.get("metrics", {}),
                "validation_issues": shared.get("validation_issues", []),
                "pgs_documented": exec_res.get("metrics", {}).get("pgs_documented", 0),
                "total_duration_ms": total_duration,
                "document_size_bytes": exec_res.get("metrics", {}).get("document_size_bytes", 0)
            },
            shared.get("user_request_id")
        )
        
        return "default"  # Terminal - empty successors

