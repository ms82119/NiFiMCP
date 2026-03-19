"""
Error handling analysis logic for NiFi flow documentation.

This module extracts error handling information from processors and connections,
identifying how errors are handled, routed, or ignored.
"""

from typing import Dict, Any, List, Optional, Callable
import json

from .component_formatter import format_processor_reference, format_destination_reference


def _build_connection_map(connections: List[Dict]) -> Dict[str, Dict[str, str]]:
    """
    Build connection map: source_id -> {relationship -> dest_id}.
    
    Handles both nested (component.source/destination) and flat (top-level sourceId/destinationId) structures.
    """
    connection_map = {}
    for conn in connections:
        # Try nested structure first
        comp = conn.get("component", {})
        source = comp.get("source", {})
        dest = comp.get("destination", {})
        
        # If nested structure exists, use it
        if source or dest:
            source_id = source.get("id")
            dest_id = dest.get("id")
            relationships = comp.get("selectedRelationships", [])
        else:
            # Use flat structure (top-level fields)
            source_id = conn.get("sourceId")
            dest_id = conn.get("destinationId")
            relationships = conn.get("selectedRelationships", []) or []
        
        if source_id:
            if source_id not in connection_map:
                connection_map[source_id] = {}
            for rel in relationships:
                connection_map[source_id][rel] = dest_id
    
    return connection_map


def _normalize_relationships(relationships: List) -> List[Dict[str, Any]]:
    """
    Normalize relationships to dicts with at least a 'name' field.
    
    Handles both dict format and plain string format from NiFi API.
    """
    normalized = []
    for rel in relationships or []:
        if isinstance(rel, dict):
            # Already in expected format
            name = rel.get("name", "")
            auto_term = rel.get("autoTerminate", False)
        else:
            # NiFi often returns relationships as plain strings
            name = str(rel)
            auto_term = False
        normalized.append({
            "name": name,
            "autoTerminate": auto_term
        })
    return normalized


async def extract_error_handling(
    processors: List[Dict],
    connections: List[Dict],
    nifi_tool_caller: Callable,
    prep_res: Dict[str, Any],
    cached_proc_details: Optional[Dict[str, Dict]] = None,
    logger = None
) -> List[Dict]:
    """
    Extract error handling information from processors and connections.
    
    Lightweight analysis that identifies:
    - Processors with error/failure relationships
    - Whether errors are handled (connected) or ignored (auto-terminated)
    - Where errors are routed to
    
    This is a lightweight initial implementation that can be expanded later
    with retry analysis, error handling patterns, etc.
    
    Args:
        processors: List of processor dicts
        connections: List of connection dicts
        nifi_tool_caller: Async callable for calling get_nifi_object_details
        prep_res: Preparation context
        cached_proc_details: Optional cached processor details
        logger: Logger instance for logging
    
    Returns:
        List of error handling dicts with processor info and error routing details
    """
    error_handling = []
    
    # Build connection map
    connection_map = _build_connection_map(connections)
    
    # Build processor map from flow_graph.processors (has all data from discovery)
    proc_details_map = {}
    for proc in processors:
        proc_id = proc.get("id")
        if proc_id:
            # flow_graph.processors already has relationships, state, name, etc.
            proc_details_map[proc_id] = proc
    
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
        
        # Normalize relationships
        normalized_relationships = _normalize_relationships(relationships)
        
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
                        # Handle both nested and flat connection structures
                        comp = conn.get("component", {})
                        dest_comp = comp.get("destination", {})
                        
                        # Check nested structure first
                        if dest_comp:
                            conn_dest_id = dest_comp.get("id")
                        else:
                            # Use flat structure
                            conn_dest_id = conn.get("destinationId")
                        
                        if conn_dest_id == dest_id:
                            # Get destination name and type
                            if dest_comp:
                                destination_type = dest_comp.get("type", "")
                                destination = dest_comp.get("name", "Unknown")
                            else:
                                destination_type = conn.get("destinationType", "UNKNOWN")
                                destination = conn.get("destinationName", "Unknown")
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
                "processor_id": proc_id if proc_id else "N/A",  # Full UUID
                "processor_reference": proc_formatted,  # Full formatted reference (uses short ID in format)
                "error_relationship": rel_name,
                "handled": is_handled,
                "destination": destination_formatted,  # Formatted with name, type, ID
                "destination_id": destination_id if destination_id else None,  # Full UUID
                "auto_terminated": auto_terminated
            })
    
    return error_handling

