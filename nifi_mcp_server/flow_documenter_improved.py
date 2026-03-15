from typing import Dict, List, Any, Optional, Set
from loguru import logger

def extract_important_properties(processor_entity: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and analyze important properties from a processor entity."""
    component = processor_entity.get("component", {})
    config = component.get("config", {})
    properties = config.get("properties", {})
    
    # Analyze properties for expressions
    expressions = {}
    for prop_name, prop_value in properties.items():
        if prop_value and "${" in str(prop_value):
            # Extract expressions like ${filename} from the value
            import re
            expr_matches = re.findall(r'\$\{([^}]+)\}', str(prop_value))
            if expr_matches:
                expressions[prop_name] = expr_matches
    
    return {
        "all_properties": properties,
        "expressions": expressions
    }

def format_connection(connection_entity: Dict[str, Any], all_components: Dict[str, Any]) -> Dict[str, Any]:
    """Format a connection entity into a simplified structure."""
    component = connection_entity.get("component", {})
    source = component.get("source", {})
    destination = component.get("destination", {})
    
    # Get names from the components map if available
    source_id = source.get("id")
    dest_id = destination.get("id")
    source_name = source.get("name", "Unknown")
    dest_name = destination.get("name", "Unknown")
    
    if source_id in all_components:
        source_comp = all_components[source_id].get("component", {})
        source_name = source_comp.get("name", source_name)
    if dest_id in all_components:
        dest_comp = all_components[dest_id].get("component", {})
        dest_name = dest_comp.get("name", dest_name)
    
    return {
        "id": connection_entity.get("id"),
        "name": component.get("name", ""),
        "source_id": source_id,
        "source_name": source_name,
        "source_type": source.get("type", "UNKNOWN"),
        "destination_id": dest_id,
        "destination_name": dest_name,
        "destination_type": destination.get("type", "UNKNOWN"),
        "relationship": component.get("selectedRelationships", [""])[0],
        "prioritizers": component.get("prioritizers", []),
        "backpressure": {
            "object_threshold": component.get("backPressureObjectThreshold", ""),
            "data_size_threshold": component.get("backPressureDataSizeThreshold", "")
        }
    }

def build_graph_structure(
    processors: List[Dict[str, Any]],
    connections: List[Dict[str, Any]],
    input_ports: List[Dict[str, Any]] = None,
    output_ports: List[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Build a graph representation of the flow."""
    outgoing_graph = {}  # id -> List[connection_details]
    incoming_graph = {}  # id -> List[connection_details]
    
    # Build both graphs
    for conn in connections:
        conn_comp = conn.get("component", {})
        source = conn_comp.get("source", {})
        dest = conn_comp.get("destination", {})
        source_id = source.get("id")
        dest_id = dest.get("id")
        
        # Add connection to outgoing graph
        if source_id:
            if source_id not in outgoing_graph:
                outgoing_graph[source_id] = []
            outgoing_graph[source_id].append(conn)
            
        # Add connection to incoming graph
        if dest_id:
            if dest_id not in incoming_graph:
                incoming_graph[dest_id] = []
            incoming_graph[dest_id].append(conn)
    
    return {
        "outgoing": outgoing_graph,
        "incoming": incoming_graph
    }

def find_decision_branches(all_components: Dict[str, Any], graph_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Identify processors that represent decision points and their branches."""
    decision_points = []
    outgoing_graph = graph_data.get("outgoing", {})
    
    for comp_id, outgoing_conns in outgoing_graph.items():
        # Skip if not enough outgoing connections
        if len(outgoing_conns) <= 1:
            continue
            
        # Get component details
        comp_entity = all_components.get(comp_id, {})
        comp = comp_entity.get("component", {})
        
        # Collect unique relationships
        relationships = set()
        for conn in outgoing_conns:
            conn_comp = conn.get("component", {})
            rel = conn_comp.get("selectedRelationships", [""])[0]
            relationships.add(rel)
        
        # If multiple relationships, this is a decision point
        if len(relationships) > 1:
            branches = []
            for conn in outgoing_conns:
                conn_comp = conn.get("component", {})
                dest = conn_comp.get("destination", {})
                rel = conn_comp.get("selectedRelationships", [""])[0]
                
                branches.append({
                    "relationship": rel,
                    "destination_id": dest.get("id"),
                    "destination_name": dest.get("name", "Unknown"),
                    "destination_type": dest.get("type", "UNKNOWN")
                })
            
            decision_points.append({
                "processor_id": comp_id,
                "processor_name": comp.get("name", "Unknown"),
                "processor_type": comp.get("type", "UNKNOWN"),
                "branches": branches
            })
    
    return decision_points

def identify_flow_paths(
    components: Dict[str, Dict[str, Any]],
    graph_data: Dict[str, Any],
    source_components: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Identify and document flow paths starting from source components."""
    flows = []
    outgoing_graph = graph_data.get("outgoing", {})
    
    def traverse_path(current_id: str, current_path: List[Dict[str, Any]], visited_set: Set[str], source_info: Dict[str, Any]):
        """Recursively traverse a flow path."""
        if current_id in visited_set:
            # End this path if we've seen this component in this path
            flows.append({
                "source": source_info["name"],
                "source_type": source_info["type"],
                "path": current_path.copy()
            })
            return
            
        visited_set.add(current_id)
        current_path.append({
            "id": current_id,
            "type": components[current_id]["type"],
            "name": components[current_id]["name"]
        })
        
        # Get outgoing connections
        outgoing_conns = outgoing_graph.get(current_id, [])
        if not outgoing_conns:
            # End of path, add it to flows
            flows.append({
                "source": source_info["name"],
                "source_type": source_info["type"],
                "path": current_path.copy()
            })
            return
            
        # Group connections by relationship
        rel_groups = {}
        for conn in outgoing_conns:
            conn_comp = conn.get("component", {})
            rel = conn_comp.get("selectedRelationships", [""])[0]
            if rel not in rel_groups:
                rel_groups[rel] = []
            rel_groups[rel].append(conn)
        
        # For each relationship group, create a new path
        for rel, conns in rel_groups.items():
            for conn in conns:
                conn_comp = conn.get("component", {})
                dest = conn_comp.get("destination", {})
                dest_id = dest.get("id")
                
                if dest_id and dest_id in components:
                    # Create a new path for this branch with its own visited set
                    new_path = current_path.copy()
                    new_visited = visited_set.copy()
                    traverse_path(dest_id, new_path, new_visited, source_info)
    
    # Start from each source component with a fresh visited set
    for source in source_components:
        source_id = source["id"]
        if source_id in components:
            source_info = {
                "name": source["name"],
                "type": source["type"]
            }
            traverse_path(source_id, [], set(), source_info)
    
    return flows

async def fetch_detailed_processor_info(processor_id: str, nifi_client, user_request_id: str = "-", action_id: str = "-") -> Optional[Dict[str, Any]]:
    """Fetch detailed processor information including configuration."""
    try:
        # This will include the full configuration with autoTerminatedRelationships
        return await nifi_client.get_processor_details(processor_id)
    except Exception as e:
        logger.warning(f"Failed to fetch detailed processor info for {processor_id}: {e}")
        return None

async def document_nifi_flow_simplified(
    processors: List[Dict[str, Any]],
    connections: List[Dict[str, Any]],
    input_ports: List[Dict[str, Any]],
    output_ports: List[Dict[str, Any]],
    include_properties: bool = True,
    include_descriptions: bool = True,
    nifi_client=None,  # Added parameter for fetching detailed info
    user_request_id: str = "-",
    action_id: str = "-"
) -> Dict[str, Any]:
    """
    Document a NiFi flow with a simplified structure that embeds connection information in processors.
    
    Args:
        processors: List of processor entities (may be from list endpoint without full config)
        connections: List of connection entities
        input_ports: List of input port entities
        output_ports: List of output port entities
        include_properties: Whether to include processor properties
        include_descriptions: Whether to include descriptions
        nifi_client: NiFi client instance for fetching detailed processor info (optional)
        user_request_id: User request ID for logging
        action_id: Action ID for logging
        
    Returns:
        Dict with the following structure:
        {
            "components": {
                "processors": {
                    id: {
                        ...existing details,
                        "outgoing_connections": [
                            {
                                "connection_id": "id",
                                "destination_name": "name", 
                                "destination_id": "id",
                                "destination_type": "PROCESSOR|INPUT_PORT|OUTPUT_PORT",
                                "relationship": "success"
                            }
                        ],
                        "incoming_connections": [
                            {
                                "connection_id": "id",
                                "source_name": "name",
                                "source_id": "id", 
                                "source_type": "PROCESSOR|INPUT_PORT|OUTPUT_PORT",
                                "relationship": "success"
                            }
                        ],
                        "auto_terminated_relationships": [
                            {
                                "relationship": "failure"
                            }
                        ]
                    }
                },
                "ports": {id: {details}}
            }
        }
    """
    # Initialize result structure
    result = {
        "components": {
            "processors": {},
            "ports": {}
        }
    }
    
    # Build lookup maps for easy access
    all_components = {}  # id -> entity (for name resolution)
    
    # Add processors to lookup
    for proc in processors:
        proc_id = proc.get("id")
        if proc_id:
            all_components[proc_id] = proc
    
    # Add ports to lookup  
    for port in input_ports + output_ports:
        port_id = port.get("id")
        if port_id:
            all_components[port_id] = port
    
    # Process processors with embedded connection info
    for proc in processors:
        proc_id = proc.get("id")
        if not proc_id:
            continue
            
        component = proc.get("component", {})
        config = component.get("config", {})
        
        # Try to fetch detailed processor info if client is available
        detailed_processor = None
        if nifi_client:
            try:
                detailed_processor = await fetch_detailed_processor_info(processor_id=proc_id, nifi_client=nifi_client, user_request_id=user_request_id, action_id=action_id)
                if detailed_processor:
                    # Use the detailed component and config
                    component = detailed_processor.get("component", component)
                    config = component.get("config", config)
                    logger.debug(f"Using detailed processor info for {proc_id}")
                else:
                    logger.debug(f"Using basic processor info for {proc_id} (detailed fetch failed)")
            except Exception as e:
                logger.warning(f"Error fetching detailed processor info for {proc_id}: {e}")
        
        # Extract basic processor info
        proc_info = {
            "id": proc_id,
            "name": component.get("name", "Unknown"),
            "type": "PROCESSOR",
            "processor_type": component.get("type", "Unknown"),
            "state": component.get("state", "UNKNOWN"),
            "outgoing_connections": [],
            "incoming_connections": [],
            "auto_terminated_relationships": []
        }
        
        # Add properties if requested
        if include_properties:
            if detailed_processor:
                # Use extract_important_properties with the detailed processor
                prop_analysis = extract_important_properties(detailed_processor)
            else:
                # Fallback to basic processor
                prop_analysis = extract_important_properties(proc)
            proc_info["properties"] = prop_analysis["all_properties"]
            proc_info["expressions"] = prop_analysis["expressions"]
        
        # Add description if requested
        if include_descriptions:
            proc_info["description"] = component.get("comments", "")
        
        # Get auto-terminated relationships from processor relationships array
        relationships = component.get("relationships", [])
        auto_terminated = set()
        for rel in relationships:
            if rel.get("autoTerminate", False):
                auto_terminated.add(rel.get("name"))
        
        # Find outgoing connections for this processor
        for conn in connections:
            conn_comp = conn.get("component", {})
            source = conn_comp.get("source", {})
            dest = conn_comp.get("destination", {})
            
            # Check if this processor is the source
            if source.get("id") == proc_id:
                dest_id = dest.get("id")
                dest_entity = all_components.get(dest_id, {})
                dest_component = dest_entity.get("component", {})
                relationship = conn_comp.get("selectedRelationships", [""])[0]
                
                proc_info["outgoing_connections"].append({
                    "connection_id": conn.get("id"),
                    "destination_name": dest_component.get("name", dest.get("name", "Unknown")),
                    "destination_id": dest_id,
                    "destination_type": dest.get("type", "UNKNOWN"),
                    "relationship": relationship
                })
        
        # Add auto-terminated relationships as separate entries
        for relationship in auto_terminated:
            proc_info["auto_terminated_relationships"].append({
                "relationship": relationship
            })
        
        # Find incoming connections for this processor
        for conn in connections:
            conn_comp = conn.get("component", {})
            source = conn_comp.get("source", {})
            dest = conn_comp.get("destination", {})
            
            # Check if this processor is the destination
            if dest.get("id") == proc_id:
                source_id = source.get("id")
                source_entity = all_components.get(source_id, {})
                source_component = source_entity.get("component", {})
                relationship = conn_comp.get("selectedRelationships", [""])[0]
                
                proc_info["incoming_connections"].append({
                    "connection_id": conn.get("id"),
                    "source_name": source_component.get("name", source.get("name", "Unknown")),
                    "source_id": source_id,
                    "source_type": source.get("type", "UNKNOWN"),
                    "relationship": relationship
                })
        
        result["components"]["processors"][proc_id] = proc_info
    
    # Process ports (keeping them as they may be useful for understanding flow entry/exit points)
    for port in input_ports + output_ports:
        port_id = port.get("id")
        if not port_id:
            continue
            
        component = port.get("component", {})
        port_info = {
            "id": port_id,
            "name": component.get("name", "Unknown"),
            "type": "PORT",
            "port_type": "INPUT_PORT" if port in input_ports else "OUTPUT_PORT",
            "state": component.get("state", "UNKNOWN")
        }
        
        if include_descriptions:
            port_info["description"] = component.get("comments", "")
        
        result["components"]["ports"][port_id] = port_info
    
    return result

# Keep the original function for backward compatibility, but mark as deprecated
def document_nifi_flow_improved(
    processors: List[Dict[str, Any]],
    connections: List[Dict[str, Any]],
    input_ports: List[Dict[str, Any]],
    output_ports: List[Dict[str, Any]],
    include_properties: bool = True,
    include_descriptions: bool = True
) -> Dict[str, Any]:
    """
    DEPRECATED: Use document_nifi_flow_simplified instead.
    This function is kept for backward compatibility but will be removed in future versions.
    """
    logger.warning("document_nifi_flow_improved is deprecated, use document_nifi_flow_simplified instead")
    return document_nifi_flow_simplified(
        processors, connections, input_ports, output_ports, 
        include_properties, include_descriptions
    )


# =============================================================================
# Port-to-Port Connection Traversal (Phase 2)
# =============================================================================

async def resolve_port_connections(
    connections: List[Dict],
    process_groups: Dict[str, Dict],
    nifi_client,
    user_request_id: str = "-",
    action_id: str = "-"
) -> List[Dict]:
    """
    Resolve connections through ports to show cross-PG data flow.
    
    Returns enriched connections with source/destination PG info.
    """
    enriched_connections = []
    
    for conn in connections:
        conn_comp = conn.get("component", {})
        source = conn_comp.get("source", {})
        dest = conn_comp.get("destination", {})
        
        enriched = {
            **conn,
            "source": source,
            "destination": dest,
            "cross_pg": False,
            "source_pg_name": None,
            "dest_pg_name": None
        }
        
        # Check if connection involves ports
        source_type = source.get("type", "")
        dest_type = dest.get("type", "")
        
        if source_type == "OUTPUT_PORT":
            # Find which PG this output port belongs to
            source_pg_id = source.get("groupId")
            if source_pg_id and source_pg_id in process_groups:
                enriched["source_pg_name"] = process_groups[source_pg_id].get("name")
                enriched["cross_pg"] = True
                
        if dest_type == "INPUT_PORT":
            # Find which PG this input port belongs to
            dest_pg_id = dest.get("groupId")
            if dest_pg_id and dest_pg_id in process_groups:
                enriched["dest_pg_name"] = process_groups[dest_pg_id].get("name")
                enriched["cross_pg"] = True
        
        enriched_connections.append(enriched)
    
    return enriched_connections


def build_cross_pg_flow_map(
    process_groups: Dict[str, Dict],
    connections: List[Dict]
) -> Dict[str, List[Dict]]:
    """
    Build a map showing data flow between process groups.
    
    Returns:
        Dict mapping "source_pg_id -> dest_pg_id" to list of connection details
    """
    flow_map = {}
    
    for conn in connections:
        if conn.get("cross_pg"):
            source_pg = conn.get("source", {}).get("groupId", "unknown")
            dest_pg = conn.get("destination", {}).get("groupId", "unknown")
            key = f"{source_pg}->{dest_pg}"
            
            if key not in flow_map:
                flow_map[key] = []
            
            flow_map[key].append({
                "connection_id": conn.get("id"),
                "source_port": conn.get("source", {}).get("name"),
                "dest_port": conn.get("destination", {}).get("name"),
                "relationship": conn.get("component", {}).get("selectedRelationships", [])
            })
    
    return flow_map


# =============================================================================
# Output Format Profiles (Phase 2)
# =============================================================================

OUTPUT_PROFILES = {
    "full": {
        "include_properties": True,
        "include_expressions": True,
        "include_connections": True,
        "include_comments": True,
        "include_position": True,
        "include_status": True,
        "max_property_value_length": None,  # No limit
    },
    "summary": {
        "include_properties": False,
        "include_expressions": False,
        "include_connections": True,
        "include_comments": False,
        "include_position": False,
        "include_status": True,
        "max_property_value_length": None,
    },
    "doc_optimized": {
        "include_properties": True,  # Only business-relevant ones
        "include_expressions": True,
        "include_connections": True,
        "include_comments": True,
        "include_position": False,
        "include_status": False,
        "max_property_value_length": 500,  # Truncate long values
    },
    "llm_analysis": {
        "include_properties": True,  # Only business-relevant ones
        "include_expressions": True,
        "include_connections": True,  # Summarized
        "include_comments": True,
        "include_position": False,
        "include_status": False,
        "max_property_value_length": 1000,
    }
}


def apply_output_profile(
    data: Dict, 
    profile_name: str = "full"
) -> Dict:
    """Apply output profile to reduce token usage."""
    profile = OUTPUT_PROFILES.get(profile_name, OUTPUT_PROFILES["full"])
    result = {}
    
    for key, value in data.items():
        # Skip excluded fields
        if key == "properties" and not profile["include_properties"]:
            continue
        if key == "expressions" and not profile["include_expressions"]:
            continue
        if key == "position" and not profile["include_position"]:
            continue
        if key in ["runStatus", "state", "status"] and not profile["include_status"]:
            continue
        if key == "comments" and not profile["include_comments"]:
            continue
        
        # Truncate long string values
        max_len = profile.get("max_property_value_length")
        if max_len and isinstance(value, str) and len(value) > max_len:
            value = value[:max_len] + f"... [truncated, {len(value)} chars total]"
        
        result[key] = value
    
    return result