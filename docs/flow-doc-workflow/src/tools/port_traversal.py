"""
Port-to-port connection traversal for cross-PG flow documentation.

Add these functions to: nifi_mcp_server/flow_documenter_improved.py
"""

from typing import Dict, List


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

