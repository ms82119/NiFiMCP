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
            "config": get_documentation_workflow_config()
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
        
        return {
            "status": "success",
            "initialized_state": {
                # Input parameters
                "process_group_id": prep_res["process_group_id"],
                "user_request_id": prep_res["user_request_id"],
                "provider": prep_res["provider"],
                "model_name": prep_res["model_name"],
                "nifi_server_id": prep_res["nifi_server_id"],
                
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
                
                # Configuration
                "config": config,
                
                # Workflow context
                "workflow_id": "flow_documentation",
                "current_phase": "INIT"
            }
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
        initialized = exec_res.get("initialized_state", {})
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
            "workflow_id": shared.get("workflow_id"),
            
            # Configuration
            "timeout_seconds": config.get("timeout_seconds", 120),
            "max_depth": config.get("max_depth", 10),
            "batch_size": config.get("batch_size", 50),
            
            # Metrics
            "chunk_start_time": time.time(),
            "discovery_start_time": shared.get("metrics", {}).get(
                "discovery", {}
            ).get("start_time", time.time())
        }
    
    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        """Execute discovery - fetch components and build hierarchy."""
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
            "workflow_id": shared.get("workflow_id"),
            
            # Configuration
            "large_pg_threshold": config.get("large_pg_threshold", 25),
            "max_tokens_per_analysis": config.get("max_tokens_per_analysis", 8000),
            "min_virtual_groups": config.get("min_virtual_groups", 3),
            "max_virtual_groups": config.get("max_virtual_groups", 7),
            
            "level_start_time": time.time()
        }
    
    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        """Process all PGs at current depth level."""
        current_depth = prep_res["current_depth"]
        pg_tree = prep_res.get("pg_tree", {})
        
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
        
        pgs_at_depth = [
            pg for pg in pg_tree.values() 
            if isinstance(pg, dict) and pg.get("depth") == current_depth
        ]
        
        self.bound_logger.info(
            f"Analyzing depth {current_depth}: {len(pgs_at_depth)} process groups"
        )
        
        new_summaries = {}
        new_virtual_groups = {}
        metrics = {
            "depth": current_depth,
            "pgs_at_depth": len(pgs_at_depth),
            "virtual_groups_created": 0,
            "llm_calls": 0
        }
        
        for pg in pgs_at_depth:
            pg_id = pg["id"]
            pg_name = pg.get("name", pg_id[:8])
            proc_count = pg.get("processor_count", 0)
            
            self.bound_logger.info(f"  Analyzing PG '{pg_name}' ({proc_count} processors)")
            
            # Get this PG's direct processors
            direct_processors = self._get_pg_processors(pg_id, prep_res)
            
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
                
                virtual_groups = await self._create_virtual_groups(
                    pg_id, direct_processors, prep_res
                )
                
                if virtual_groups:
                    new_virtual_groups[pg_id] = virtual_groups
                    metrics["virtual_groups_created"] += len(virtual_groups)
                    
                    for vg in virtual_groups:
                        vg_summary = await self._analyze_virtual_group(
                            vg, direct_processors, prep_res, pg_id  # Pass parent PG ID
                        )
                        virtual_group_summaries.append(vg_summary)
                        metrics["llm_calls"] += 1
            
            # Combine all child summaries (real + virtual)
            all_child_summaries = child_summaries + virtual_group_summaries
            
            # Analyze this PG
            if all_child_summaries:
                summary = await self._analyze_pg_with_summaries(
                    pg, all_child_summaries, prep_res
                )
            else:
                summary = await self._analyze_pg_direct(
                    pg, direct_processors, prep_res
                )
            
            # Extract error handling information (lightweight analysis)
            pg_connections = [
                c for c in prep_res["flow_graph"].get("connections", [])
                if c.get("component", {}).get("parentGroupId") == pg_id
            ]
            error_handling = await self._extract_error_handling(
                direct_processors, pg_connections, prep_res
            )
            summary["error_handling"] = error_handling
            
            # Aggregate error handling from children
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
        
        elapsed_ms = int((time.time() - prep_res["level_start_time"]) * 1000)
        metrics["level_duration_ms"] = elapsed_ms
        
        return {
            "status": "success",
            "new_summaries": new_summaries,
            "new_virtual_groups": new_virtual_groups,
            "metrics": metrics
        }
    
    def _get_pg_processors(
        self, 
        pg_id: str, 
        prep_res: Dict[str, Any]
    ) -> List[Dict]:
        """Get processors that belong directly to this PG (not nested)."""
        all_processors = prep_res["flow_graph"].get("processors", {})
        
        return [
            proc for proc in all_processors.values()
            if proc.get("_parent_pg_id") == pg_id
        ]
    
    async def _create_virtual_groups(
        self,
        pg_id: str,
        processors: List[Dict],
        prep_res: Dict[str, Any]
    ) -> List[Dict]:
        """Use LLM to identify logical groupings in a large flat PG."""
        from ..prompts.documentation import VIRTUAL_SUBFLOW_PROMPT
        
        connections = [
            c for c in prep_res["flow_graph"].get("connections", [])
            if c.get("component", {}).get("parentGroupId") == pg_id
        ]
        
        proc_summary = [
            {
                "id": p.get("id", "")[:8],
                "name": p.get("component", {}).get("name", p.get("name", "?")),
                "type": p.get("component", {}).get("type", p.get("type", "?")).split(".")[-1],
                "category": self.categorizer.categorize(
                    p.get("component", {}).get("type", p.get("type", ""))
                ).value
            }
            for p in processors
        ]
        
        conn_summary = self._build_connectivity_summary(processors, connections)
        
        prompt = VIRTUAL_SUBFLOW_PROMPT.format(
            proc_count=len(processors),
            processor_summary=json.dumps(proc_summary, indent=2),
            connection_summary=json.dumps(conn_summary, indent=2),
            min_groups=prep_res.get("min_virtual_groups", 3),
            max_groups=prep_res.get("max_virtual_groups", 7)
        )
        
        response = await self.call_llm_async(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            execution_state=prep_res,
            action_id=f"virtual-groups-{pg_id[:8]}"
        )
        
        if response.get("content"):
            return self._parse_virtual_groups(response["content"], processors)
        
        return []
    
    def _parse_virtual_groups(
        self, 
        content: str, 
        processors: List[Dict]
    ) -> List[Dict]:
        """Parse LLM response for virtual group definitions."""
        json_match = re.search(r'\[[\s\S]*\]', content)
        if json_match:
            try:
                groups = json.loads(json_match.group())
                
                proc_id_map = {p.get("id", "")[:8]: p.get("id") for p in processors}
                
                for group in groups:
                    full_ids = []
                    for short_id in group.get("processor_ids", []):
                        if short_id in proc_id_map:
                            full_ids.append(proc_id_map[short_id])
                    group["processor_ids"] = full_ids
                    group["virtual"] = True
                
                return groups
                
            except json.JSONDecodeError:
                pass
        
        return []
    
    async def _analyze_virtual_group(
        self,
        virtual_group: Dict,
        all_processors: List[Dict],
        prep_res: Dict[str, Any],
        parent_pg_id: str
    ) -> Dict:
        """Generate summary for a virtual group."""
        from ..prompts.documentation import PG_SUMMARY_PROMPT
        
        group_proc_ids = set(virtual_group.get("processor_ids", []))
        group_processors = [
            p for p in all_processors 
            if p.get("id") in group_proc_ids
        ]
        
        categorized = self._categorize_processors(group_processors)
        io_endpoints = await self._extract_io_endpoints_detailed(group_processors, prep_res)
        
        prompt = PG_SUMMARY_PROMPT.format(
            pg_name=virtual_group.get("name", "Virtual Group"),
            pg_purpose=virtual_group.get("purpose", ""),
            processor_count=len(group_processors),
            categories_json=json.dumps(categorized, indent=2),
            io_endpoints_json=json.dumps(io_endpoints, indent=2, default=str),
            child_summaries_json="[]"
        )
        
        response = await self.call_llm_async(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            execution_state=prep_res,
            action_id=f"vg-summary-{virtual_group.get('name', 'vg')[:10]}"
        )
        
        # Extract error handling for virtual group (connections within parent PG)
        # Note: Virtual groups are within a parent PG, so we need connections from that PG
        vg_connections = [
            c for c in prep_res["flow_graph"].get("connections", [])
            if c.get("component", {}).get("parentGroupId") == parent_pg_id
        ]
        # Filter to only connections involving processors in this virtual group
        vg_proc_ids = set(virtual_group.get("processor_ids", []))
        vg_connections_filtered = [
            c for c in vg_connections
            if c.get("component", {}).get("source", {}).get("id") in vg_proc_ids
            or c.get("component", {}).get("destination", {}).get("id") in vg_proc_ids
        ]
        error_handling = await self._extract_error_handling(
            group_processors, vg_connections_filtered, prep_res
        )
        
        return {
            "name": virtual_group.get("name"),
            "virtual": True,
            "purpose": virtual_group.get("purpose", ""),
            "summary": response.get("content", ""),
            "processor_count": len(group_processors),
            "categories": categorized,
            "io_endpoints": io_endpoints,
            "error_handling": error_handling
        }
    
    async def _analyze_pg_with_summaries(
        self,
        pg: Dict,
        child_summaries: List[Dict],
        prep_res: Dict[str, Any]
    ) -> Dict:
        """Analyze a PG using child summaries (not raw processors)."""
        from ..prompts.documentation import PG_WITH_CHILDREN_PROMPT
        
        children_digest = [
            {
                "name": cs.get("name"),
                "purpose": cs.get("purpose", cs.get("summary", "")[:200]),
                "io": cs.get("io_endpoints", []),
                "virtual": cs.get("virtual", False)
            }
            for cs in child_summaries
        ]
        
        prompt = PG_WITH_CHILDREN_PROMPT.format(
            pg_name=pg.get("name", pg.get("id", "")[:8]),
            child_count=len(child_summaries),
            children_digest=json.dumps(children_digest, indent=2)
        )
        
        response = await self.call_llm_async(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            execution_state=prep_res,
            action_id=f"pg-summary-{pg.get('name', pg.get('id', ''))[:10]}"
        )
        
        all_io = []
        all_errors = []
        for cs in child_summaries:
            # DEFENSIVE: Ensure cs is a dict before calling .get()
            if isinstance(cs, dict):
                io_endpoints = cs.get("io_endpoints", [])
                error_handling = cs.get("error_handling", [])
                if isinstance(io_endpoints, list):
                    all_io.extend(io_endpoints)
                if isinstance(error_handling, list):
                    all_errors.extend(error_handling)
            else:
                self.bound_logger.warning(f"child_summary is not a dict in _analyze_pg_with_summaries: {type(cs)}")
        
        # DEFENSIVE: Build children list safely
        children_names = []
        for cs in child_summaries:
            if isinstance(cs, dict):
                children_names.append(cs.get("name", "Unknown"))
            else:
                children_names.append("Unknown")
        
        return {
            "name": pg.get("name"),
            "id": pg.get("id"),
            "virtual": False,
            "summary": response.get("content", "") if isinstance(response, dict) else "",
            "child_count": len(child_summaries),
            "children": children_names,
            "io_endpoints": all_io,
            "error_handling": all_errors
        }
    
    async def _analyze_pg_direct(
        self,
        pg: Dict,
        processors: List[Dict],
        prep_res: Dict[str, Any]
    ) -> Dict:
        """Analyze a small leaf PG directly."""
        from ..prompts.documentation import PG_SUMMARY_PROMPT
        
        categorized = self._categorize_processors(processors)
        
        # Fetch processor details once with doc_optimized format (includes relationships and properties)
        # This data will be reused by both IO extraction and error handling
        proc_ids = [p.get("id") for p in processors if p.get("id")]
        cached_proc_details = {}
        if proc_ids:
            try:
                result = await self.call_nifi_tool(
                    "get_nifi_object_details",
                    {
                        "object_type": "processor",
                        "object_ids": proc_ids,
                        "output_format": "doc_optimized",
                        "include_properties": True
                    },
                    prep_res
                )
                if isinstance(result, str):
                    result = json.loads(result)
                if not isinstance(result, list):
                    result = [result] if isinstance(result, dict) else []
                for item in result:
                    if isinstance(item, dict) and item.get("status") == "success":
                        cached_proc_details[item.get("id")] = item.get("data", {})
            except Exception as e:
                self.bound_logger.warning(f"Failed to fetch processor details for analysis: {e}")
                cached_proc_details = {}
        
        # Use cached details for IO extraction
        io_endpoints = await self._extract_io_endpoints_detailed(processors, prep_res, cached_proc_details)
        
        prompt = PG_SUMMARY_PROMPT.format(
            pg_name=pg.get("name", pg.get("id", "")[:8]),
            pg_purpose="",
            processor_count=len(processors),
            categories_json=json.dumps(categorized, indent=2),
            io_endpoints_json=json.dumps(io_endpoints, indent=2, default=str),
            child_summaries_json="[]"
        )
        
        response = await self.call_llm_async(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            execution_state=prep_res,
            action_id=f"pg-direct-{pg.get('name', pg.get('id', ''))[:10]}"
        )
        
        # Extract error handling for leaf PG (reuse cached details)
        pg_connections = [
            c for c in prep_res["flow_graph"].get("connections", [])
            if c.get("component", {}).get("parentGroupId") == pg.get("id")
        ]
        error_handling = await self._extract_error_handling(
            processors, pg_connections, prep_res, cached_proc_details
        )
        
        return {
            "name": pg.get("name"),
            "id": pg.get("id"),
            "virtual": False,
            "summary": response.get("content", ""),
            "processor_count": len(processors),
            "categories": categorized,
            "io_endpoints": io_endpoints,
            "error_handling": error_handling
        }
    
    def _categorize_processors(
        self, 
        processors: List[Dict]
    ) -> Dict[str, List[str]]:
        """Categorize processors and return category -> names mapping."""
        categorized = {}
        
        for proc in processors:
            proc_type = proc.get("component", {}).get("type", proc.get("type", ""))
            proc_name = proc.get("component", {}).get("name", proc.get("name", "?"))
            category = self.categorizer.categorize(proc_type).value
            
            if category not in categorized:
                categorized[category] = []
            categorized[category].append(proc_name)
        
        return categorized
    
    async def _extract_io_endpoints_detailed(
        self,
        processors: List[Dict],
        prep_res: Dict[str, Any],
        cached_proc_details: Optional[Dict[str, Dict]] = None
    ) -> List[Dict]:
        """
        Extract detailed IO endpoint information including file paths, URLs, topics, etc.
        
        This is a critical function - it captures all external interaction points
        that must be documented (files, URLs, databases, message queues, etc.).
        
        Args:
            processors: List of processor dicts
            prep_res: Preparation context
            cached_proc_details: Optional cached processor details to avoid duplicate API calls
        """
        io_categories = ["IO_READ", "IO_WRITE"]
        io_processors = []
        
        # Identify IO processors
        for proc in processors:
            proc_type = proc.get("component", {}).get("type", proc.get("type", ""))
            category = self.categorizer.categorize(proc_type).value
            
            if category in io_categories:
                io_processors.append({
                    "id": proc.get("id"),
                    "name": proc.get("component", {}).get("name", proc.get("name", "?")),
                    "type": proc_type,
                    "category": category
                })
        
        if not io_processors:
            return []
        
        # Fetch detailed info for IO processors (using batch mode from Phase 2)
        proc_ids = [p["id"] for p in io_processors]
        
        # Skip API call if no IO processors found
        if not proc_ids:
            return []
        
        # Use cached details if available, otherwise fetch
        proc_details_map = {}
        if cached_proc_details:
            proc_details_map = {pid: cached_proc_details.get(pid, {}) for pid in proc_ids}
            # Check if we have all the data we need (must have component.config.properties)
            missing_ids = [
                pid for pid in proc_ids 
                if not proc_details_map.get(pid, {}).get("component", {}).get("config", {}).get("properties")
            ]
            if missing_ids:
                # Fetch missing ones
                try:
                    result = await self.call_nifi_tool(
                        "get_nifi_object_details",
                        {
                            "object_type": "processor",
                            "object_ids": missing_ids,
                            "output_format": "doc_optimized",
                            "include_properties": True
                        },
                        prep_res
                    )
                    if isinstance(result, str):
                        import json
                        result = json.loads(result)
                    if not isinstance(result, list):
                        result = [result] if isinstance(result, dict) else []
                    for item in result:
                        if isinstance(item, dict) and item.get("status") == "success":
                            proc_details_map[item.get("id")] = item.get("data", {})
                except Exception as e:
                    self.bound_logger.warning(f"Failed to fetch missing processor details: {e}")
        else:
            # No cache, fetch all
            try:
                result = await self.call_nifi_tool(
                    "get_nifi_object_details",
                    {
                        "object_type": "processor",
                        "object_ids": proc_ids,
                        "output_format": "doc_optimized",  # Gets business_properties
                        "include_properties": True
                    },
                    prep_res
                )
                
                if isinstance(result, str):
                    import json
                    result = json.loads(result)
                
                # DEFENSIVE: Ensure result is iterable (list)
                if not isinstance(result, list):
                    self.bound_logger.warning(f"IO tool result is not a list: {type(result)}")
                    result = [result] if isinstance(result, dict) else []
                
                # Map results by ID
                for item in result:
                    # DEFENSIVE: Ensure item is a dict before calling .get()
                    if isinstance(item, dict) and item.get("status") == "success":
                        proc_details_map[item.get("id")] = item.get("data", {})
            
            except Exception as e:
                self.bound_logger.warning(f"Failed to fetch detailed IO endpoints: {e}")
                # Fallback to basic extraction
                return self._extract_io_basic(processors)
        
        # Build detailed endpoint info
        endpoints = []
        
        # Extract endpoint details for each IO processor
        for io_proc in io_processors:
                proc_id = io_proc["id"]
                details = proc_details_map.get(proc_id, {})
                component = details.get("component", details)
                config = component.get("config", {})
                properties = config.get("properties", {})
                business_props = details.get("business_properties", {})
                
                # Format processor reference with name, type, ID
                # Ensure we have the right structure for format_processor_reference
                proc_for_formatting = {"id": proc_id}
                if "component" in details:
                    proc_for_formatting["component"] = details["component"]
                elif component and component != details:
                    # component is already extracted, wrap it
                    proc_for_formatting["component"] = component
                else:
                    # Fallback: use what we have from io_proc
                    proc_for_formatting["component"] = {
                        "name": io_proc["name"],
                        "type": io_proc["type"],
                        "id": proc_id
                    }
                
                proc_formatted = format_processor_reference(proc_for_formatting)
                
                endpoint = {
                    "processor": io_proc["name"],
                    "processor_type": io_proc["type"].split(".")[-1],
                    "processor_id": proc_id[:8] if proc_id else "N/A",
                    "processor_reference": proc_formatted,  # Full formatted reference
                    "type": io_proc["type"].split(".")[-1],  # Keep for backward compat
                    "direction": "INPUT" if io_proc["category"] == "IO_READ" else "OUTPUT",
                    "endpoint_details": {}
                }
                
                # Extract endpoint-specific details based on processor type
                proc_type_simple = io_proc["type"].split(".")[-1]
                
                # File-based processors
                if proc_type_simple in ["GetFile", "PutFile", "ListFile"]:
                    endpoint["endpoint_details"] = {
                        "type": "file_system",
                        "directory": properties.get("Directory", business_props.get("directory", "")),
                        "file_filter": properties.get("File Filter", business_props.get("file_filter", "")),
                        "path": properties.get("Directory", "")
                    }
                
                # SFTP processors
                elif proc_type_simple in ["GetSFTP", "PutSFTP", "ListSFTP"]:
                    endpoint["endpoint_details"] = {
                        "type": "sftp",
                        "hostname": properties.get("Hostname", ""),
                        "port": properties.get("Port", ""),
                        "directory": properties.get("Remote Directory", ""),
                        "username": properties.get("Username", "")
                    }
                
                # HTTP client processors (outbound)
                elif proc_type_simple in ["InvokeHTTP", "GetHTTP", "PostHTTP"]:
                    endpoint["endpoint_details"] = {
                        "type": "http",
                        "url": properties.get("Remote URL", business_props.get("url", "")),
                        "method": properties.get("HTTP Method", business_props.get("method", "GET")),
                        "ssl_context": properties.get("SSL Context Service", "")
                    }
                
                # HTTP server processors (inbound listener)
                elif proc_type_simple == "HandleHttpRequest":
                    # Build allowed methods list
                    allowed_methods = []
                    if properties.get("Allow GET", "").lower() == "true":
                        allowed_methods.append("GET")
                    if properties.get("Allow POST", "").lower() == "true":
                        allowed_methods.append("POST")
                    if properties.get("Allow PUT", "").lower() == "true":
                        allowed_methods.append("PUT")
                    if properties.get("Allow DELETE", "").lower() == "true":
                        allowed_methods.append("DELETE")
                    if properties.get("Allow HEAD", "").lower() == "true":
                        allowed_methods.append("HEAD")
                    if properties.get("Allow OPTIONS", "").lower() == "true":
                        allowed_methods.append("OPTIONS")
                    # Check for additional methods
                    additional_methods = properties.get("Additional HTTP Methods")
                    if additional_methods:
                        allowed_methods.extend([m.strip() for m in additional_methods.split(",") if m.strip()])
                    
                    # Build URL from port and path
                    port = properties.get("Listening Port", "")
                    hostname = properties.get("Hostname", "")
                    path = properties.get("Allowed Paths", "")
                    protocol = "https" if properties.get("SSL Context Service") else "http"
                    
                    # Construct full URL
                    if hostname:
                        base_url = f"{protocol}://{hostname}:{port}" if port else f"{protocol}://{hostname}"
                    else:
                        base_url = f"{protocol}://localhost:{port}" if port else f"{protocol}://localhost"
                    
                    full_url = f"{base_url}{path}" if path else base_url
                    
                    endpoint["endpoint_details"] = {
                        "type": "http_server",
                        "url": full_url,
                        "port": port,
                        "hostname": hostname or "localhost",
                        "path": path,
                        "protocol": protocol,
                        "allowed_methods": allowed_methods,
                        "ssl_context": properties.get("SSL Context Service"),
                        "client_authentication": properties.get("Client Authentication", "No Authentication")
                    }
                
                # HTTP server processors (outbound response)
                elif proc_type_simple == "HandleHttpResponse":
                    status_code = properties.get("HTTP Status Code", "")
                    # Status code might be an expression, try to extract numeric value
                    status_code_value = status_code
                    if isinstance(status_code, str) and not status_code.isdigit():
                        # Try to extract number from expression like "${code:defaultValue('200')}"
                        import re
                        match = re.search(r'\d+', status_code)
                        if match:
                            status_code_value = match.group()
                    
                    endpoint["endpoint_details"] = {
                        "type": "http_response",
                        "status_code": status_code_value,
                        "status_code_expression": status_code if status_code != status_code_value else None,
                        "http_context_map": properties.get("HTTP Context Map"),
                        "response_attributes_regex": properties.get("Attributes to add to the HTTP Response (Regex)")
                    }
                
                # Kafka processors
                elif "Kafka" in proc_type_simple or proc_type_simple in ["ConsumeKafka", "PublishKafka"]:
                    endpoint["endpoint_details"] = {
                        "type": "kafka",
                        "topic": properties.get("topic", properties.get("Topic Name(s)", business_props.get("topic", ""))),
                        "bootstrap_servers": properties.get("bootstrap.servers", business_props.get("bootstrap_servers", "")),
                        "consumer_group": properties.get("Group ID", "")
                    }
                
                # Database processors
                elif proc_type_simple in ["QueryDatabaseTable", "PutDatabaseRecord", "ExecuteSQL"]:
                    endpoint["endpoint_details"] = {
                        "type": "database",
                        "connection_url": properties.get("Database Connection URL", ""),
                        "table": properties.get("Table Name", ""),
                        "connection_pool": properties.get("Database Connection Pooling Service", "")
                    }
                
                # JMS processors
                elif proc_type_simple in ["ConsumeJMS", "PublishJMS", "GetJMSQueue", "PutJMS"]:
                    endpoint["endpoint_details"] = {
                        "type": "jms",
                        "destination": properties.get("Destination Name", ""),
                        "connection_factory": properties.get("Connection Factory Service", "")
                    }
                
                # S3 processors
                elif "S3" in proc_type_simple:
                    endpoint["endpoint_details"] = {
                        "type": "s3",
                        "bucket": properties.get("Bucket", ""),
                        "object_key": properties.get("Object Key", ""),
                        "region": properties.get("Region", "")
                    }
                
                # MongoDB processors
                elif proc_type_simple in ["GetMongo", "PutMongo"]:
                    endpoint["endpoint_details"] = {
                        "type": "mongodb",
                        "uri": properties.get("Mongo URI", ""),
                        "database": properties.get("Mongo Database Name", ""),
                        "collection": properties.get("Mongo Collection Name", "")
                    }
                
                # Elasticsearch processors
                elif proc_type_simple in ["GetElasticsearch", "PutElasticsearchRecord"]:
                    endpoint["endpoint_details"] = {
                        "type": "elasticsearch",
                        "url": properties.get("Elasticsearch URL", ""),
                        "index": properties.get("Index", ""),
                        "type": properties.get("Type", "")
                    }
                
                # Generic fallback - try to extract any URL/path-like properties
                else:
                    endpoint["endpoint_details"] = {
                        "type": "generic",
                        "properties": {}
                    }
                    # Look for common endpoint property names
                    for key in ["Remote URL", "URL", "Hostname", "Directory", "Path", 
                               "Connection URL", "Endpoint", "Address"]:
                        if key in properties and properties[key]:
                            endpoint["endpoint_details"]["properties"][key] = properties[key]
                
                endpoints.append(endpoint)
        
        return endpoints
    
    def _extract_io_basic(
        self,
        processors: List[Dict]
    ) -> List[Dict]:
        """Fallback basic IO extraction if detailed fetch fails."""
        io_categories = ["IO_READ", "IO_WRITE"]
        endpoints = []
        
        for proc in processors:
            proc_type = proc.get("component", {}).get("type", proc.get("type", ""))
            category = self.categorizer.categorize(proc_type).value
            
            if category in io_categories:
                proc_name = proc.get("component", {}).get("name", proc.get("name", "?"))
                proc_id = proc.get("id", "")
                proc_formatted = format_processor_reference(proc)
                
                endpoints.append({
                    "processor": proc_name,
                    "processor_type": proc_type.split(".")[-1],
                    "processor_id": proc_id[:8] if proc_id else "N/A",
                    "processor_reference": proc_formatted,
                    "type": proc_type.split(".")[-1],  # Keep for backward compat
                    "direction": "INPUT" if category == "IO_READ" else "OUTPUT",
                    "endpoint_details": {"type": "unknown", "note": "Details not available"}
                })
        
        return endpoints
    
    def _extract_io_from_categorized(
        self,
        categorized: Dict[str, List[str]],
        processors: List[Dict]
    ) -> List[Dict]:
        """
        DEPRECATED: Use _extract_io_endpoints_detailed() instead.
        
        Kept for backward compatibility but should be replaced.
        """
        return self._extract_io_basic(processors)
    
    async def _extract_error_handling(
        self,
        processors: List[Dict],
        connections: List[Dict],
        prep_res: Dict[str, Any],
        cached_proc_details: Optional[Dict[str, Dict]] = None
    ) -> List[Dict]:
        """
        Extract error handling information from processors and connections.
        
        Lightweight analysis that identifies:
        - Processors with error/failure relationships
        - Whether errors are handled (connected) or ignored (auto-terminated)
        - Where errors are routed to
        
        This is a lightweight initial implementation that can be expanded later
        with retry analysis, error handling patterns, etc.
        """
        error_handling = []
        
        # Build connection map: source_id -> {relationship -> dest_id}
        connection_map = {}
        for conn in connections:
            comp = conn.get("component", {})
            source = comp.get("source", {})
            dest = comp.get("destination", {})
            source_id = source.get("id")
            dest_id = dest.get("id")
            relationships = comp.get("selectedRelationships", [])
            
            if source_id:
                if source_id not in connection_map:
                    connection_map[source_id] = {}
                for rel in relationships:
                    connection_map[source_id][rel] = dest_id
        
        # Get detailed processor info to access relationships
        proc_ids = [p.get("id") for p in processors]
        
        # Skip API call if no processors
        if not proc_ids:
            return []
        
        # Use cached details if available, otherwise fetch
        proc_details_map = {}
        if cached_proc_details:
            proc_details_map = {pid: cached_proc_details.get(pid, {}) for pid in proc_ids}
            # Check if we have relationships data
            missing_ids = [
                pid for pid in proc_ids 
                if not proc_details_map.get(pid, {}).get("component", {}).get("relationships")
            ]
            if missing_ids:
                # Fetch missing ones with summary format (lightweight - just need relationships)
                try:
                    result = await self.call_nifi_tool(
                        "get_nifi_object_details",
                        {
                            "object_type": "processor",
                            "object_ids": missing_ids,
                            "output_format": "summary",
                            "include_properties": False
                        },
                        prep_res
                    )
                    if isinstance(result, str):
                        import json
                        result = json.loads(result)
                    if not isinstance(result, list):
                        result = [result] if isinstance(result, dict) else []
                    for item in result:
                        if isinstance(item, dict) and item.get("status") == "success":
                            proc_details_map[item.get("id")] = item.get("data", {})
                except Exception as e:
                    self.bound_logger.warning(f"Failed to fetch missing processor details for error analysis: {e}")
        else:
            # No cache, fetch all with summary format (lightweight - just need relationships)
            try:
                result = await self.call_nifi_tool(
                    "get_nifi_object_details",
                    {
                        "object_type": "processor",
                        "object_ids": proc_ids,
                        "output_format": "summary",  # Lightweight - just need relationships
                        "include_properties": False
                    },
                    prep_res
                )
                
                if isinstance(result, str):
                    import json
                    result = json.loads(result)
                
                # DEFENSIVE: Ensure result is iterable (list)
                if not isinstance(result, list):
                    self.bound_logger.warning(f"Tool result is not a list: {type(result)}")
                    result = [result] if isinstance(result, dict) else []
                
                # Map processor details by ID
                for item in result:
                    # DEFENSIVE: Ensure item is a dict before calling .get()
                    if isinstance(item, dict) and item.get("status") == "success":
                        proc_details_map[item.get("id")] = item.get("data", {})
            
            except Exception as e:
                self.bound_logger.warning(f"Failed to fetch processor details for error analysis: {e}")
                # Fallback to basic processor data
                proc_details_map = {
                    p.get("id"): p.get("component", p)
                    for p in processors
                }
        
        # Analyze each processor for error handling
        for proc in processors:
            proc_id = proc.get("id")
            proc_name = proc.get("component", {}).get("name", proc.get("name", "?"))
            proc_type = proc.get("component", {}).get("type", proc.get("type", ""))
            
            # Get relationships from detailed data or fallback
            details = proc_details_map.get(proc_id, {})
            component = details.get("component", details)
            relationships = component.get("relationships", [])
            
            if not relationships:
                # Try fallback
                relationships = proc.get("component", {}).get("relationships", [])
            
            # Normalize relationships to dicts with at least a 'name' field
            normalized_relationships = []
            for rel in relationships or []:
                if isinstance(rel, dict):
                    # Already in expected format
                    name = rel.get("name", "")
                    auto_term = rel.get("autoTerminate", False)
                else:
                    # NiFi often returns relationships as plain strings
                    name = str(rel)
                    auto_term = False
                normalized_relationships.append({
                    "name": name,
                    "autoTerminate": auto_term
                })
            
            # Look for error-related relationships
            error_keywords = ["failure", "error", "retry", "invalid", "unmatched", "exception"]
            error_relationships = [
                rel for rel in normalized_relationships
                if any(keyword in (rel.get("name", "") or "").lower() for keyword in error_keywords)
            ]
            
            if not error_relationships:
                continue
            
                # Analyze each error relationship
            for rel in error_relationships:
                rel_name = rel.get("name", "")
                auto_terminated = rel.get("autoTerminate", False)
                
                # Check if relationship is connected
                is_handled = False
                destination = None
                destination_type = None
                destination_id = None
                
                if proc_id in connection_map and rel_name in connection_map[proc_id]:
                    # Error is routed somewhere
                    dest_id = connection_map[proc_id][rel_name]
                    is_handled = True
                    destination_id = dest_id
                    
                    # Find destination processor name and type
                    for dest_proc in processors:
                        if dest_proc.get("id") == dest_id:
                            dest_comp = dest_proc.get("component", {})
                            destination = dest_comp.get("name", dest_proc.get("name", "Unknown"))
                            destination_type = dest_comp.get("type", "")
                            break
                    
                    if not destination:
                        # Might be a port or other component - check connections
                        for conn in connections:
                            comp = conn.get("component", {})
                            dest_comp = comp.get("destination", {})
                            if dest_comp.get("id") == dest_id:
                                destination_type = dest_comp.get("type", "")
                                destination = dest_comp.get("name", "Unknown")
                                break
                        
                        if not destination:
                            destination = "Unknown"
                            destination_type = "UNKNOWN"
                    
                    # Format destination reference with name, type, ID
                    destination_formatted = format_destination_reference(
                        dest_id, destination, destination_type
                    )
                            
                elif auto_terminated:
                    # Error is auto-terminated (ignored)
                    destination_formatted = "IGNORED (auto-terminated)"
                    is_handled = False
                else:
                    # Error relationship exists but not connected and not auto-terminated
                    # This is a potential issue - error not handled
                    destination_formatted = "NOT HANDLED"
                    is_handled = False
                
                # Format processor reference with name, type, ID
                proc_formatted = format_processor_reference(proc)
                
                error_handling.append({
                    "processor": proc_name,
                    "processor_type": proc_type.split(".")[-1],
                    "processor_id": proc_id[:8] if proc_id else "N/A",
                    "processor_reference": proc_formatted,  # Full formatted reference
                    "error_relationship": rel_name,
                    "handled": is_handled,
                    "destination": destination_formatted,  # Formatted with name, type, ID
                    "destination_id": destination_id[:8] if destination_id else None,
                    "auto_terminated": auto_terminated
                })
        
        return error_handling
    
    def _build_connectivity_summary(
        self,
        processors: List[Dict],
        connections: List[Dict]
    ) -> List[Dict]:
        """Build simplified connectivity for LLM."""
        proc_id_to_name = {
            p.get("id"): p.get("component", {}).get("name", p.get("name", "?"))
            for p in processors
        }
        
        summary = []
        for conn in connections[:50]:
            comp = conn.get("component", {})
            source_id = comp.get("source", {}).get("id", "")
            dest_id = comp.get("destination", {}).get("id", "")
            
            summary.append({
                "from": proc_id_to_name.get(source_id, source_id[:8]),
                "to": proc_id_to_name.get(dest_id, dest_id[:8]),
                "relationship": comp.get("selectedRelationships", ["?"])[0]
            })
        
        return summary
    
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
            "workflow_id": shared.get("workflow_id"),
            
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
        sections["summary"] = await self._generate_executive_summary(
            root_summary, pg_summaries, prep_res
        )
        await self._emit_section_event("summary", prep_res)
        
        # 2. Hierarchical Mermaid Diagram
        sections["diagram"] = await self._generate_hierarchical_diagram(
            pg_tree, pg_summaries, prep_res
        )
        await self._emit_section_event("diagram", prep_res)
        
        # 3. Hierarchical Documentation
        sections["hierarchy_doc"] = self._build_hierarchical_doc(
            root_pg_id, pg_tree, pg_summaries, prep_res
        )
        await self._emit_section_event("hierarchy_doc", prep_res)
        
        # 4. Aggregated IO Endpoints
        sections["io_table"] = self._build_aggregated_io_table(pg_summaries)
        await self._emit_section_event("io_table", prep_res)
        
        # 5. Error Handling Analysis
        sections["error_handling_table"] = self._build_error_handling_table(pg_summaries)
        await self._emit_section_event("error_handling_table", prep_res)
        
        # 6. Assemble final document
        final_document = self._assemble_hierarchical_document(sections, prep_res)
        
        validation_issues = self._validate_output(final_document, pg_summaries)
        
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
    
    async def _generate_executive_summary(
        self,
        root_summary: Dict,
        all_summaries: Dict[str, Dict],
        prep_res: Dict[str, Any]
    ) -> str:
        """Generate executive summary from hierarchical summaries."""
        from ..prompts.documentation import HIERARCHICAL_SUMMARY_PROMPT
        
        all_io = []
        for summary in all_summaries.values():
            all_io.extend(summary.get("io_endpoints", []))
        
        seen = set()
        unique_io = []
        for io in all_io:
            key = f"{io.get('processor')}:{io.get('direction')}"
            if key not in seen:
                seen.add(key)
                unique_io.append(io)
        
        hierarchy_overview = []
        for pg_id, summary in all_summaries.items():
            if not summary.get("virtual"):
                hierarchy_overview.append({
                    "name": summary.get("name"),
                    "purpose": summary.get("summary", "")[:150] + "..."
                })
        
        prompt = HIERARCHICAL_SUMMARY_PROMPT.format(
            max_words=prep_res.get("summary_max_words", 500),
            root_summary=root_summary.get("summary", "No summary available"),
            io_endpoints=json.dumps(unique_io, indent=2),
            hierarchy_overview=json.dumps(hierarchy_overview, indent=2)
        )
        
        response = await self.call_llm_async(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            execution_state=prep_res,
            action_id="generation-exec-summary"
        )
        
        return response.get("content", "Summary generation failed.")
    
    async def _generate_hierarchical_diagram(
        self,
        pg_tree: Dict[str, Dict],
        pg_summaries: Dict[str, Dict],
        prep_res: Dict[str, Any]
    ) -> str:
        """Generate Mermaid diagram showing PG hierarchy."""
        from ..prompts.documentation import HIERARCHICAL_DIAGRAM_PROMPT
        
        hierarchy = []
        for pg_id, pg in pg_tree.items():
            summary = pg_summaries.get(pg_id, {})
            hierarchy.append({
                "id": pg_id[:8],
                "name": pg.get("name", pg_id[:8]),
                "parent": pg.get("parent", "")[:8] if pg.get("parent") else None,
                "purpose": summary.get("summary", "")[:50],
                "has_io": bool(summary.get("io_endpoints"))
            })
        
        for pg_id, virtual_groups in prep_res.get("virtual_groups", {}).items():
            for vg in virtual_groups:
                hierarchy.append({
                    "id": f"vg-{vg.get('name', 'group')[:6]}",
                    "name": vg.get("name"),
                    "parent": pg_id[:8],
                    "purpose": vg.get("purpose", "")[:50],
                    "virtual": True
                })
        
        prompt = HIERARCHICAL_DIAGRAM_PROMPT.format(
            hierarchy_json=json.dumps(hierarchy, indent=2),
            max_nodes=prep_res.get("max_mermaid_nodes", 50)
        )
        
        response = await self.call_llm_async(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            execution_state=prep_res,
            action_id="generation-diagram"
        )
        
        content = response.get("content", "")
        
        mermaid_match = re.search(r'```mermaid\s*([\s\S]*?)```', content)
        if mermaid_match:
            return mermaid_match.group(1).strip()
        
        if content.strip().startswith("graph") or content.strip().startswith("flowchart"):
            return content.strip()
        
        return "graph TD\n    A[Flow] --> B[Documentation Failed]"
    
    def _build_hierarchical_doc(
        self,
        root_pg_id: str,
        pg_tree: Dict[str, Dict],
        pg_summaries: Dict[str, Dict],
        prep_res: Dict[str, Any],
        depth: int = 0
    ) -> str:
        """Build nested documentation sections from hierarchy."""
        lines = []
        
        pg = pg_tree.get(root_pg_id, {})
        summary = pg_summaries.get(root_pg_id, {})
        
        header_level = min(depth + 2, 4)
        header = "#" * header_level
        
        pg_name = pg.get("name", root_pg_id[:8])
        lines.append(f"{header} {pg_name}")
        lines.append("")
        
        if summary.get("summary"):
            lines.append(summary["summary"])
            lines.append("")
        
        io_endpoints = summary.get("io_endpoints", [])
        if io_endpoints:
            lines.append(f"**Data Flow:**")
            inputs = [e for e in io_endpoints if e.get("direction") == "INPUT"]
            outputs = [e for e in io_endpoints if e.get("direction") == "OUTPUT"]
            if inputs:
                lines.append(f"- Inputs: {', '.join(e.get('processor', '?') for e in inputs)}")
            if outputs:
                lines.append(f"- Outputs: {', '.join(e.get('processor', '?') for e in outputs)}")
            lines.append("")
        
        virtual_groups = prep_res.get("virtual_groups", {}).get(root_pg_id, [])
        if virtual_groups:
            lines.append(f"**Logical Components:**")
            for vg in virtual_groups:
                lines.append(f"- **{vg.get('name')}**: {vg.get('purpose', '')}")
            lines.append("")
        
        children = pg.get("children", [])
        if children:
            for child_id in children:
                child_doc = self._build_hierarchical_doc(
                    child_id, pg_tree, pg_summaries, prep_res, depth + 1
                )
                lines.append(child_doc)
        
        return "\n".join(lines)
    
    def _build_aggregated_io_table(
        self, 
        pg_summaries: Dict[str, Dict]
    ) -> str:
        """
        Build detailed aggregated IO table with endpoint specifics.
        
        CRITICAL: This section must include all endpoint details (paths, URLs, topics, etc.)
        as these are essential for understanding external interactions.
        """
        all_inputs = []
        all_outputs = []
        
        for pg_id, summary in pg_summaries.items():
            pg_name = summary.get("name", pg_id[:8])
            for io in summary.get("io_endpoints", []):
                io_with_pg = {**io, "pg": pg_name}
                if io.get("direction") == "INPUT":
                    all_inputs.append(io_with_pg)
                else:
                    all_outputs.append(io_with_pg)
        
        if not all_inputs and not all_outputs:
            return "*No external IO endpoints identified.*"
        
        lines = []
        
        if all_inputs:
            lines.append("### Data Inputs")
            lines.append("")
            for io in all_inputs:
                endpoint_details = io.get("endpoint_details", {})
                endpoint_type = endpoint_details.get("type", "unknown")
                
                # Use formatted reference (name, type, ID)
                proc_ref = io.get("processor_reference", 
                    f"{io.get('processor')} ({io.get('processor_type')}) [id:{io.get('processor_id')}]")
                
                lines.append(f"#### {proc_ref}")
                lines.append(f"**Process Group:** {io.get('pg')}")
                
                # Format endpoint details based on type
                if endpoint_type == "file_system":
                    lines.append(f"- **Directory:** `{endpoint_details.get('directory', 'N/A')}`")
                    if endpoint_details.get("file_filter"):
                        lines.append(f"- **File Filter:** `{endpoint_details.get('file_filter')}`")
                
                elif endpoint_type == "sftp":
                    lines.append(f"- **Host:** `{endpoint_details.get('hostname', 'N/A')}:{endpoint_details.get('port', 'N/A')}`")
                    lines.append(f"- **Directory:** `{endpoint_details.get('directory', 'N/A')}`")
                    if endpoint_details.get("username"):
                        lines.append(f"- **Username:** `{endpoint_details.get('username')}`")
                
                elif endpoint_type == "http":
                    lines.append(f"- **URL:** `{endpoint_details.get('url', 'N/A')}`")
                    lines.append(f"- **Method:** `{endpoint_details.get('method', 'N/A')}`")
                    if endpoint_details.get("ssl_context"):
                        lines.append(f"- **SSL Context:** `{endpoint_details.get('ssl_context')}`")
                
                elif endpoint_type == "http_server":
                    lines.append(f"- **URL:** `{endpoint_details.get('url', 'N/A')}`")
                    lines.append(f"- **Port:** `{endpoint_details.get('port', 'N/A')}`")
                    lines.append(f"- **Path:** `{endpoint_details.get('path', '/')}`")
                    if endpoint_details.get("allowed_methods"):
                        methods = ", ".join(endpoint_details.get("allowed_methods", []))
                        lines.append(f"- **Allowed Methods:** `{methods}`")
                    if endpoint_details.get("ssl_context"):
                        lines.append(f"- **SSL Context:** `{endpoint_details.get('ssl_context')}`")
                    if endpoint_details.get("client_authentication") and endpoint_details.get("client_authentication") != "No Authentication":
                        lines.append(f"- **Client Authentication:** `{endpoint_details.get('client_authentication')}`")
                
                elif endpoint_type == "http_response":
                    lines.append(f"- **Status Code:** `{endpoint_details.get('status_code', 'N/A')}`")
                    if endpoint_details.get("status_code_expression"):
                        lines.append(f"- **Status Code Expression:** `{endpoint_details.get('status_code_expression')}`")
                    if endpoint_details.get("http_context_map"):
                        lines.append(f"- **HTTP Context Map:** `{endpoint_details.get('http_context_map')}`")
                
                elif endpoint_type == "kafka":
                    lines.append(f"- **Topic:** `{endpoint_details.get('topic', 'N/A')}`")
                    lines.append(f"- **Bootstrap Servers:** `{endpoint_details.get('bootstrap_servers', 'N/A')}`")
                    if endpoint_details.get("consumer_group"):
                        lines.append(f"- **Consumer Group:** `{endpoint_details.get('consumer_group')}`")
                
                elif endpoint_type == "database":
                    lines.append(f"- **Connection:** `{endpoint_details.get('connection_url', 'N/A')}`")
                    if endpoint_details.get("table"):
                        lines.append(f"- **Table:** `{endpoint_details.get('table')}`")
                
                elif endpoint_type == "jms":
                    lines.append(f"- **Destination:** `{endpoint_details.get('destination', 'N/A')}`")
                
                elif endpoint_type == "s3":
                    lines.append(f"- **Bucket:** `{endpoint_details.get('bucket', 'N/A')}`")
                    if endpoint_details.get("object_key"):
                        lines.append(f"- **Object Key:** `{endpoint_details.get('object_key')}`")
                
                elif endpoint_type == "mongodb":
                    lines.append(f"- **Database:** `{endpoint_details.get('database', 'N/A')}`")
                    lines.append(f"- **Collection:** `{endpoint_details.get('collection', 'N/A')}`")
                
                elif endpoint_type == "elasticsearch":
                    lines.append(f"- **URL:** `{endpoint_details.get('url', 'N/A')}`")
                    lines.append(f"- **Index:** `{endpoint_details.get('index', 'N/A')}`")
                
                else:
                    # Generic fallback - show all properties
                    props = endpoint_details.get("properties", {})
                    if props:
                        for key, value in props.items():
                            if value:
                                lines.append(f"- **{key}:** `{value}`")
                    else:
                        lines.append("- *Endpoint details not available*")
                
                lines.append("")
        
        if all_outputs:
            lines.append("### Data Outputs")
            lines.append("")
            for io in all_outputs:
                endpoint_details = io.get("endpoint_details", {})
                endpoint_type = endpoint_details.get("type", "unknown")
                
                # Use formatted reference (name, type, ID)
                proc_ref = io.get("processor_reference",
                    f"{io.get('processor')} ({io.get('processor_type')}) [id:{io.get('processor_id')}]")
                
                lines.append(f"#### {proc_ref}")
                lines.append(f"**Process Group:** {io.get('pg')}")
                
                # Same formatting logic as inputs
                if endpoint_type == "file_system":
                    lines.append(f"- **Directory:** `{endpoint_details.get('directory', 'N/A')}`")
                    if endpoint_details.get("file_filter"):
                        lines.append(f"- **File Filter:** `{endpoint_details.get('file_filter')}`")
                
                elif endpoint_type == "sftp":
                    lines.append(f"- **Host:** `{endpoint_details.get('hostname', 'N/A')}:{endpoint_details.get('port', 'N/A')}`")
                    lines.append(f"- **Directory:** `{endpoint_details.get('directory', 'N/A')}`")
                
                elif endpoint_type == "http":
                    lines.append(f"- **URL:** `{endpoint_details.get('url', 'N/A')}`")
                    lines.append(f"- **Method:** `{endpoint_details.get('method', 'N/A')}`")
                    if endpoint_details.get("ssl_context"):
                        lines.append(f"- **SSL Context:** `{endpoint_details.get('ssl_context')}`")
                
                elif endpoint_type == "http_server":
                    lines.append(f"- **URL:** `{endpoint_details.get('url', 'N/A')}`")
                    lines.append(f"- **Port:** `{endpoint_details.get('port', 'N/A')}`")
                    lines.append(f"- **Path:** `{endpoint_details.get('path', '/')}`")
                    if endpoint_details.get("allowed_methods"):
                        methods = ", ".join(endpoint_details.get("allowed_methods", []))
                        lines.append(f"- **Allowed Methods:** `{methods}`")
                    if endpoint_details.get("ssl_context"):
                        lines.append(f"- **SSL Context:** `{endpoint_details.get('ssl_context')}`")
                    if endpoint_details.get("client_authentication") and endpoint_details.get("client_authentication") != "No Authentication":
                        lines.append(f"- **Client Authentication:** `{endpoint_details.get('client_authentication')}`")
                
                elif endpoint_type == "http_response":
                    lines.append(f"- **Status Code:** `{endpoint_details.get('status_code', 'N/A')}`")
                    if endpoint_details.get("status_code_expression"):
                        lines.append(f"- **Status Code Expression:** `{endpoint_details.get('status_code_expression')}`")
                    if endpoint_details.get("http_context_map"):
                        lines.append(f"- **HTTP Context Map:** `{endpoint_details.get('http_context_map')}`")
                
                elif endpoint_type == "kafka":
                    lines.append(f"- **Topic:** `{endpoint_details.get('topic', 'N/A')}`")
                    lines.append(f"- **Bootstrap Servers:** `{endpoint_details.get('bootstrap_servers', 'N/A')}`")
                
                elif endpoint_type == "database":
                    lines.append(f"- **Connection:** `{endpoint_details.get('connection_url', 'N/A')}`")
                    if endpoint_details.get("table"):
                        lines.append(f"- **Table:** `{endpoint_details.get('table')}`")
                
                elif endpoint_type == "jms":
                    lines.append(f"- **Destination:** `{endpoint_details.get('destination', 'N/A')}`")
                
                elif endpoint_type == "s3":
                    lines.append(f"- **Bucket:** `{endpoint_details.get('bucket', 'N/A')}`")
                    if endpoint_details.get("object_key"):
                        lines.append(f"- **Object Key:** `{endpoint_details.get('object_key')}`")
                
                elif endpoint_type == "mongodb":
                    lines.append(f"- **Database:** `{endpoint_details.get('database', 'N/A')}`")
                    lines.append(f"- **Collection:** `{endpoint_details.get('collection', 'N/A')}`")
                
                elif endpoint_type == "elasticsearch":
                    lines.append(f"- **URL:** `{endpoint_details.get('url', 'N/A')}`")
                    lines.append(f"- **Index:** `{endpoint_details.get('index', 'N/A')}`")
                
                else:
                    props = endpoint_details.get("properties", {})
                    if props:
                        for key, value in props.items():
                            if value:
                                lines.append(f"- **{key}:** `{value}`")
                    else:
                        lines.append("- *Endpoint details not available*")
                
                lines.append("")
        
        return "\n".join(lines)
    
    def _build_error_handling_table(
        self,
        pg_summaries: Dict[str, Dict]
    ) -> str:
        """
        Build error handling table showing where errors are handled or ignored.
        
        Lightweight initial implementation - can be expanded later with:
        - Retry policies
        - Error handling patterns
        - Dead letter queue analysis
        """
        all_errors = []
        
        for pg_id, summary in pg_summaries.items():
            pg_name = summary.get("name", pg_id[:8])
            for error in summary.get("error_handling", []):
                error_with_pg = {**error, "pg": pg_name}
                all_errors.append(error_with_pg)
        
        if not all_errors:
            return "*No error handling relationships identified.*"
        
        # Group by handled/ignored status
        handled_errors = [e for e in all_errors if e.get("handled")]
        ignored_errors = [e for e in all_errors if e.get("auto_terminated")]
        unhandled_errors = [e for e in all_errors if not e.get("handled") and not e.get("auto_terminated")]
        
        lines = []
        
        if handled_errors:
            lines.append("### Handled Errors")
            lines.append("")
            lines.append("| Process Group | Processor | Error Relationship | Destination |")
            lines.append("|---------------|-----------|---------------------|-------------|")
            for error in handled_errors:
                # Use formatted references (name, type, ID)
                proc_ref = error.get("processor_reference", 
                    f"{error.get('processor')} ({error.get('processor_type')}) [id:{error.get('processor_id')}]")
                dest_ref = error.get("destination", "Unknown")
                
                lines.append(
                    f"| {error.get('pg')} | `{proc_ref}` | "
                    f"`{error.get('error_relationship')}` | `{dest_ref}` |"
                )
            lines.append("")
        
        if ignored_errors:
            lines.append("### Ignored Errors (Auto-Terminated)")
            lines.append("")
            lines.append("| Process Group | Processor | Error Relationship |")
            lines.append("|---------------|-----------|---------------------|")
            for error in ignored_errors:
                # Use formatted reference (name, type, ID)
                proc_ref = error.get("processor_reference",
                    f"{error.get('processor')} ({error.get('processor_type')}) [id:{error.get('processor_id')}]")
                
                lines.append(
                    f"| {error.get('pg')} | `{proc_ref}` | "
                    f"`{error.get('error_relationship')}` |"
                )
            lines.append("")
            lines.append("*Note: These errors are automatically terminated and not routed anywhere.*")
            lines.append("")
        
        if unhandled_errors:
            lines.append("### ⚠️ Unhandled Errors")
            lines.append("")
            lines.append("| Process Group | Processor | Error Relationship |")
            lines.append("|---------------|-----------|---------------------|")
            for error in unhandled_errors:
                # Use formatted reference (name, type, ID)
                proc_ref = error.get("processor_reference",
                    f"{error.get('processor')} ({error.get('processor_type')}) [id:{error.get('processor_id')}]")
                
                lines.append(
                    f"| {error.get('pg')} | `{proc_ref}` | "
                    f"`{error.get('error_relationship')}` |"
                )
            lines.append("")
            lines.append("*Warning: These error relationships exist but are not connected or auto-terminated.*")
            lines.append("*This may indicate missing error handling logic.*")
            lines.append("")
        
        return "\n".join(lines)
    
    def _assemble_hierarchical_document(
        self, 
        sections: Dict, 
        prep_res: Dict
    ) -> str:
        """Assemble final hierarchical Markdown document."""
        pg_id = prep_res.get("process_group_id", "root")
        pg_name = prep_res.get("pg_tree", {}).get(pg_id, {}).get("name", pg_id)
        
        # Extract default diagram to avoid backslash in f-string
        default_diagram = 'graph TD\n    A[Error]'
        diagram_content = sections.get('diagram', default_diagram)
        
        doc = f"""# NiFi Flow Documentation

## Process Group: {pg_name}

---

## Executive Summary

{sections.get('summary', 'Summary not available.')}

---

## Flow Architecture

```mermaid
{diagram_content}
```

---

## Detailed Breakdown

{sections.get('hierarchy_doc', 'No hierarchy documentation available.')}

---

## External Interactions

{sections.get('io_table', 'No IO endpoints identified.')}

---

## Error Handling

{sections.get('error_handling_table', 'No error handling relationships identified.')}

---

*Generated by NiFi Flow Documentation Workflow (Hierarchical Analysis)*
"""
        return doc
    
    def _validate_output(
        self, 
        document: str, 
        pg_summaries: Dict
    ) -> List[str]:
        """Validate generated output quality."""
        issues = []
        
        uuid_pattern = r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'
        if re.search(uuid_pattern, document):
            issues.append("Raw UUIDs found in document - consider using names instead")
        
        mermaid_match = re.search(r'```mermaid\s*([\s\S]*?)```', document)
        if mermaid_match:
            mermaid_content = mermaid_match.group(1)
            if not (mermaid_content.strip().startswith("graph") or 
                   mermaid_content.strip().startswith("flowchart")):
                issues.append("Mermaid diagram may have invalid syntax")
        
        if len(pg_summaries) > 1:
            mentioned_pgs = sum(1 for pg in pg_summaries.values() 
                               if pg.get("name", "") in document)
            if mentioned_pgs < len(pg_summaries) * 0.5:
                issues.append("Less than half of PGs mentioned in documentation")
        
        return issues
    
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

