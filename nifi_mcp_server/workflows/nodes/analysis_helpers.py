"""
Analysis helper functions for hierarchical NiFi flow analysis.

This module provides helper functions for analyzing process groups,
categorizing processors, formatting connections, and creating virtual groups.
"""

from typing import Dict, Any, List, Optional, Callable
import json
import re

from .io_extraction import extract_io_endpoints_detailed, extract_io_basic
from .error_handling import extract_error_handling


def get_pg_processors(
    pg_id: str,
    prep_res: Dict[str, Any]
) -> List[Dict]:
    """Get processors that belong directly to this PG (not nested)."""
    all_processors = prep_res["flow_graph"].get("processors", {})
    
    return [
        proc for proc in all_processors.values()
        if proc.get("_parent_pg_id") == pg_id
    ]


def get_pg_connections_for_processors(
    processor_ids: set,
    prep_res: Dict[str, Any]
) -> List[Dict]:
    """Get connections where sourceId or destinationId matches any processor in the set."""
    all_connections = prep_res["flow_graph"].get("connections", [])
    matching_connections = []
    
    for conn in all_connections:
        # Try nested structure first
        comp = conn.get("component", {})
        source = comp.get("source", {})
        dest = comp.get("destination", {})
        
        if source or dest:
            source_id = source.get("id")
            dest_id = dest.get("id")
        else:
            # Use flat structure
            source_id = conn.get("sourceId")
            dest_id = conn.get("destinationId")
        
        # Include connection if either source or destination matches our processors
        if source_id in processor_ids or dest_id in processor_ids:
            matching_connections.append(conn)
    
    return matching_connections


def format_processors_for_prompt(
    processors: List[Dict]
) -> List[Dict]:
    """Format processors with all data from flow_graph for prompt.
    
    Includes all fields stored during discovery:
    - id, name, type, state
    - properties (full dict from discovery)
    - relationships
    - position, validationStatus, validationErrors
    - Any other fields captured during discovery
    """
    formatted = []
    for proc in processors:
        # Get processor data - handle both nested (component) and flat structures
        proc_id = proc.get("id", "")
        proc_component = proc.get("component", {})
        
        # Build processor dict with all available data
        proc_data = {
            "id": proc_id if proc_id else "?",
            "name": proc_component.get("name", proc.get("name", "?")),
            "type": proc_component.get("type", proc.get("type", "?")),
            "state": proc_component.get("state", proc.get("state", "UNKNOWN")),
        }
        
        # Include properties if available (from discovery phase)
        if "properties" in proc:
            proc_data["properties"] = proc.get("properties", {})
        elif proc_component.get("config", {}).get("properties"):
            proc_data["properties"] = proc_component.get("config", {}).get("properties", {})
        
        # Include relationships if available
        if "relationships" in proc:
            proc_data["relationships"] = proc.get("relationships", [])
        elif proc_component.get("relationships"):
            proc_data["relationships"] = proc_component.get("relationships", [])
        
        # Include validation info if available
        if "validationStatus" in proc:
            proc_data["validationStatus"] = proc.get("validationStatus")
        if "validationErrors" in proc:
            proc_data["validationErrors"] = proc.get("validationErrors", [])
        
        # Include position if available
        if "position" in proc:
            proc_data["position"] = proc.get("position")
        
        # Include any other fields that might be useful
        if "runStatus" in proc:
            proc_data["runStatus"] = proc.get("runStatus")
        if "comments" in proc_component:
            proc_data["comments"] = proc_component.get("comments", "")
        
        formatted.append(proc_data)
    
    return formatted


def format_connections_simple(
    connections: List[Dict]
) -> List[Dict]:
    """Format connections as simple JSON for prompt."""
    formatted = []
    for conn in connections:
        # Try nested structure first
        comp = conn.get("component", {})
        source = comp.get("source", {})
        dest = comp.get("destination", {})
        
        if source or dest:
            source_id = source.get("id", "")
            dest_id = dest.get("id", "")
            source_name = source.get("name", "?")
            dest_name = dest.get("name", "?")
            relationships = comp.get("selectedRelationships", [])
        else:
            # Use flat structure
            source_id = conn.get("sourceId", "")
            dest_id = conn.get("destinationId", "")
            source_name = conn.get("sourceName", "?")
            dest_name = conn.get("destinationName", "?")
            relationships = conn.get("selectedRelationships", []) or []
        
        formatted.append({
            "from": source_name,
            "from_id": source_id if source_id else "?",
            "to": dest_name,
            "to_id": dest_id if dest_id else "?",
            "relationships": relationships if relationships else ["success"]
        })
    
    return formatted


async def create_virtual_groups(
    pg_id: str,
    processors: List[Dict],
    connections: List[Dict],
    llm_caller: Callable,
    prep_res: Dict[str, Any],
    categorizer,
    logger = None
) -> List[Dict]:
    """Use LLM to identify logical groupings in a large flat PG."""
    from ..prompts.documentation import VIRTUAL_SUBFLOW_PROMPT
    
    proc_summary = [
        {
            "id": p.get("id", "")[:8],
            "name": p.get("component", {}).get("name", p.get("name", "?")),
            "type": p.get("component", {}).get("type", p.get("type", "?")).split(".")[-1],
            "category": categorizer.categorize(
                p.get("component", {}).get("type", p.get("type", ""))
            ).value
        }
        for p in processors
    ]
    
    conn_summary = build_connectivity_summary(processors, connections)
    
    prompt = VIRTUAL_SUBFLOW_PROMPT.format(
        proc_count=len(processors),
        processor_summary=json.dumps(proc_summary, indent=2),
        connection_summary=json.dumps(conn_summary, indent=2),
        min_groups=prep_res.get("min_virtual_groups", 3),
        max_groups=prep_res.get("max_virtual_groups", 7)
    )
    
    response = await llm_caller(
        messages=[{"role": "user", "content": prompt}],
        tools=None,
        execution_state=prep_res,
        action_id=f"virtual-groups-{pg_id}"
    )
    
    if response.get("content"):
        return parse_virtual_groups(response["content"], processors)
    
    return []


def parse_virtual_groups(
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


async def analyze_virtual_group(
    virtual_group: Dict,
    all_processors: List[Dict],
    nifi_tool_caller: Callable,
    llm_caller: Callable,
    prep_res: Dict[str, Any],
    parent_pg_id: str,
    io_extractor: Callable,
    error_extractor: Callable,
    categorizer,
    logger = None
) -> Dict:
    """Generate summary for a virtual group."""
    from ..prompts.documentation import PG_SUMMARY_PROMPT
    
    group_proc_ids = set(virtual_group.get("processor_ids", []))
    group_processors = [
        p for p in all_processors 
        if p.get("id") in group_proc_ids
    ]
    
    # Format processors for prompt (simple format from flow_graph)
    processors_json = format_processors_for_prompt(group_processors)
    
    # Get connections for these processors
    vg_connections = get_pg_connections_for_processors(group_proc_ids, prep_res)
    connections_json = format_connections_simple(vg_connections)
    
    # Extract IO endpoints and categorize (for aggregation, not prompt)
    # No longer need to fetch cached_proc_details - flow_graph.processors has all data
    # No longer need cached_proc_details - use flow_graph.processors directly
    io_endpoints = await io_extractor(
        group_processors, nifi_tool_caller, prep_res, categorizer, None, logger
    )
    categorized = categorize_processors(group_processors, categorizer, include_states=True)
    
    # Build prompt data for debugging
    prompt_data = {
        "pg_name": virtual_group.get("name", "Virtual Group"),
        "processor_count": len(group_processors),
        "processors_json": processors_json,
        "connections_json": connections_json,
        "children_digest": []
    }
    
    prompt = PG_SUMMARY_PROMPT.format(
        pg_name=prompt_data["pg_name"],
        processor_count=prompt_data["processor_count"],
        processors_json=json.dumps(prompt_data["processors_json"], indent=2),
        connections_json=json.dumps(prompt_data["connections_json"], indent=2),
        children_digest=json.dumps(prompt_data["children_digest"], indent=2)
    )
    
    response = await llm_caller(
        messages=[{"role": "user", "content": prompt}],
        tools=None,
        execution_state=prep_res,
        action_id=f"vg-summary-{virtual_group.get('name', 'vg')}"
    )
    
    # Extract error handling for virtual group (reuse connections already fetched)
    # No longer need cached_proc_details - use flow_graph.processors directly
    error_handling = await error_extractor(
        group_processors, vg_connections, nifi_tool_caller, prep_res, None, logger
    )
    
    return {
        "name": virtual_group.get("name"),
        "virtual": True,
        "purpose": virtual_group.get("purpose", ""),
        "summary": response.get("content", ""),
        "processor_count": len(group_processors),
        "categories": categorized,
        "io_endpoints": io_endpoints,
        "error_handling": error_handling,
        "prompt_data": prompt_data  # Store prompt data for debugging
    }


async def analyze_pg_with_summaries(
    pg: Dict,
    child_summaries: List[Dict],
    direct_processors: List[Dict],
    nifi_tool_caller: Callable,
    llm_caller: Callable,
    prep_res: Dict[str, Any],
    io_extractor: Callable,
    error_extractor: Callable,
    categorizer,
    logger = None
) -> Dict:
    """Analyze a PG using child summaries AND parent's own processors (if present)."""
    from ..prompts.documentation import PG_WITH_CHILDREN_PROMPT, PG_WITH_CHILDREN_AND_PROCESSORS_PROMPT
    
    children_digest = [
        {
            "name": cs.get("name"),
            "purpose": cs.get("purpose", cs.get("summary", "")),  # Full summary - no truncation
            "io": cs.get("io_endpoints", []),
            "error_handling": cs.get("error_handling", []),  # Include error handling context
            "virtual": cs.get("virtual", False)
        }
        for cs in child_summaries
    ]
    
    # Check if parent PG has its own processors
    has_parent_processors = direct_processors and len(direct_processors) > 0
    
    if has_parent_processors:
        # Parent has both children AND its own processors - use enhanced prompt
        # Format processors for prompt (simple format from flow_graph)
        processors_json = format_processors_for_prompt(direct_processors)
        
        # Get connections for parent's own processors
        parent_proc_ids = {p.get("id") for p in direct_processors if p.get("id")}
        pg_connections = get_pg_connections_for_processors(parent_proc_ids, prep_res)
        connections_json = format_connections_simple(pg_connections)
        
        # Extract IO endpoints from parent's processors (for aggregation, not prompt)
        # No longer need to fetch cached_proc_details - flow_graph.processors has all data
        # No longer need cached_proc_details - use flow_graph.processors directly
        parent_io_endpoints = await io_extractor(
            direct_processors, nifi_tool_caller, prep_res, categorizer, None, logger
        )
        
        # Categorize parent's processors (for aggregation, not prompt)
        categorized = categorize_processors(direct_processors, categorizer, include_states=True)
        
        # Build prompt data for debugging
        prompt_data = {
            "pg_name": pg.get("name", pg.get("id", "")[:8]),
            "child_count": len(child_summaries),
            "children_digest": children_digest,
            "processor_count": len(direct_processors),
            "processors_json": processors_json,
            "connections_json": connections_json
        }
        
        prompt = PG_WITH_CHILDREN_AND_PROCESSORS_PROMPT.format(
            pg_name=prompt_data["pg_name"],
            child_count=prompt_data["child_count"],
            children_digest=json.dumps(prompt_data["children_digest"], indent=2),
            processor_count=prompt_data["processor_count"],
            processors_json=json.dumps(prompt_data["processors_json"], indent=2),
            connections_json=json.dumps(prompt_data["connections_json"], indent=2)
        )
        
        # Aggregate IO endpoints from both parent and children
        all_io = list(parent_io_endpoints)
        parent_categories = categorized
    else:
        # Parent only has children, no own processors - use original prompt
        # Build prompt data for debugging
        prompt_data = {
            "pg_name": pg.get("name", pg.get("id", "")[:8]),
            "child_count": len(child_summaries),
            "children_digest": children_digest
        }
        
        prompt = PG_WITH_CHILDREN_PROMPT.format(
            pg_name=prompt_data["pg_name"],
            child_count=prompt_data["child_count"],
            children_digest=json.dumps(prompt_data["children_digest"], indent=2)
        )
        
        all_io = []
        parent_categories = {}
    
    # Use full name for action_id (no truncation) - helps with event correlation
    pg_name = pg.get('name', pg.get('id', ''))
    action_id = f"pg-summary-{pg_name}" if pg_name else f"pg-summary-{pg.get('id', 'unknown')[:8]}"
    response = await llm_caller(
        messages=[{"role": "user", "content": prompt}],
        tools=None,
        execution_state=prep_res,
        action_id=action_id
    )
    
    # Aggregate IO endpoints from children (if not already done for parent)
    if not has_parent_processors:
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
            if logger:
                logger.warning(f"child_summary is not a dict in analyze_pg_with_summaries: {type(cs)}")
    
        # Extract error handling for parent PG's own processors (if any)
        if has_parent_processors:
            # No longer need cached_proc_details - use flow_graph.processors directly
            parent_errors = await error_extractor(
                direct_processors, pg_connections, nifi_tool_caller, prep_res, None, logger
            )
            all_errors.extend(parent_errors)
    
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
        "processor_count": len(direct_processors) if direct_processors else 0,
        "categories": parent_categories,
        "io_endpoints": all_io,
        "error_handling": all_errors,
        "prompt_data": prompt_data  # Store prompt data for debugging
    }


async def analyze_pg_direct(
    pg: Dict,
    processors: List[Dict],
    nifi_tool_caller: Callable,
    llm_caller: Callable,
    prep_res: Dict[str, Any],
    io_extractor: Callable,
    error_extractor: Callable,
    categorizer,
    logger = None
) -> Dict:
    """Analyze a small leaf PG directly."""
    from ..prompts.documentation import PG_SUMMARY_PROMPT
    
    # Format processors for prompt (simple format from flow_graph)
    processors_json = format_processors_for_prompt(processors)
    
    # Get connections for these processors
    proc_ids = {p.get("id") for p in processors if p.get("id")}
    pg_connections = get_pg_connections_for_processors(proc_ids, prep_res)
    connections_json = format_connections_simple(pg_connections)
    
    # Extract IO endpoints and categorize (for aggregation, not prompt)
    # No longer need to fetch cached_proc_details - flow_graph.processors has all data
    # No longer need cached_proc_details - use flow_graph.processors directly
    io_endpoints = await io_extractor(
        processors, nifi_tool_caller, prep_res, categorizer, None, logger
    )
    categorized = categorize_processors(processors, categorizer, include_states=True)
    
    # Build prompt data for debugging
    prompt_data = {
        "pg_name": pg.get("name", pg.get("id", "")[:8]),
        "processor_count": len(processors),
        "processors_json": processors_json,
        "connections_json": connections_json,
        "children_digest": []
    }
    
    prompt = PG_SUMMARY_PROMPT.format(
        pg_name=prompt_data["pg_name"],
        processor_count=prompt_data["processor_count"],
        processors_json=json.dumps(prompt_data["processors_json"], indent=2),
        connections_json=json.dumps(prompt_data["connections_json"], indent=2),
        children_digest=json.dumps(prompt_data["children_digest"], indent=2)
    )
    
    # Use full name for action_id (no truncation) - helps with event correlation
    pg_name = pg.get('name', pg.get('id', ''))
    action_id = f"pg-direct-{pg_name}" if pg_name else f"pg-direct-{pg.get('id', 'unknown')[:8]}"
    response = await llm_caller(
        messages=[{"role": "user", "content": prompt}],
        tools=None,
        execution_state=prep_res,
        action_id=action_id
    )
    
    # Extract error handling for leaf PG
    # No longer need cached_proc_details - use flow_graph.processors directly
    error_handling = await error_extractor(
        processors, pg_connections, nifi_tool_caller, prep_res, None, logger
    )
    
    return {
        "name": pg.get("name"),
        "id": pg.get("id"),
        "virtual": False,
        "summary": response.get("content", ""),
        "processor_count": len(processors),
        "prompt_data": prompt_data,  # Store prompt data for debugging
        "categories": categorized,
        "io_endpoints": io_endpoints,
        "error_handling": error_handling
    }


def categorize_processors(
    processors: List[Dict],
    categorizer,
    include_states: bool = True,
    cached_proc_details: Optional[Dict[str, Dict]] = None  # Deprecated - kept for backward compat but not used
) -> Dict[str, List[Dict]]:
    """Categorize processors and return category -> processor info mapping.
    
    Args:
        processors: List of processor dicts
        categorizer: ProcessorCategorizer instance
        include_states: Whether to include processor states in the output
        cached_proc_details: Optional cached processor details to get state from
        
    Returns:
        Dict mapping category -> list of processor info dicts with name and optionally state
    """
    categorized = {}
    
    for proc in processors:
        proc_id = proc.get("id")
        proc_type = proc.get("component", {}).get("type", proc.get("type", ""))
        proc_name = proc.get("component", {}).get("name", proc.get("name", "?"))
        
        # Get state from processor (flow_graph.processors has state at top level)
        proc_state = proc.get("state", proc.get("component", {}).get("state", "UNKNOWN"))
        
        category = categorizer.categorize(proc_type).value
        
        if category not in categorized:
            categorized[category] = []
        
        proc_info = {"name": proc_name}
        if include_states:
            proc_info["state"] = proc_state
        
        categorized[category].append(proc_info)
    
    return categorized


def format_connections_for_prompt(
    connections: List[Dict],
    cached_proc_details: Optional[Dict[str, Dict]] = None,  # Deprecated - kept for backward compat but not used
    flow_graph_processors: Optional[Dict[str, Dict]] = None  # Use flow_graph.processors for names
) -> List[Dict]:
    """Format connections for LLM prompt.
    
    Returns a simplified list of connections showing data flow between processors.
    
    Handles two connection formats:
    1. Nested: conn["component"]["source"] and conn["component"]["destination"]
    2. Flat: conn["sourceId"], conn["sourceName"], etc. at top level
    """
    formatted = []
    
    for conn in connections:
        # Try nested structure first (component.source/destination)
        comp = conn.get("component", {})
        source = comp.get("source", {})
        dest = comp.get("destination", {})
        
        # If nested structure exists, use it
        if source or dest:
            source_id = source.get("id")
            dest_id = dest.get("id")
            source_name = source.get("name", "?")
            dest_name = dest.get("name", "?")
            relationships = comp.get("selectedRelationships", [])
        else:
            # Use flat structure (top-level fields)
            source_id = conn.get("sourceId")
            dest_id = conn.get("destinationId")
            source_name = conn.get("sourceName", "?")
            dest_name = conn.get("destinationName", "?")
            relationships = conn.get("selectedRelationships", []) or []
        
        # Get processor names from flow_graph.processors if available
        if flow_graph_processors:
            if source_id in flow_graph_processors:
                source_name = flow_graph_processors[source_id].get("name", source_name)
            if dest_id in flow_graph_processors:
                dest_name = flow_graph_processors[dest_id].get("name", dest_name)
        elif cached_proc_details:  # Fallback for backward compat
            if source_id in cached_proc_details:
                source_name = cached_proc_details[source_id].get("name", source_name)
            if dest_id in cached_proc_details:
                dest_name = cached_proc_details[dest_id].get("name", dest_name)
        
        formatted.append({
            "from": source_name,
            "to": dest_name,
            "relationships": relationships if relationships else ["success"]
        })
    
    return formatted


def build_connectivity_summary(
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
        # Handle both nested and flat connection structures
        comp = conn.get("component", {})
        source = comp.get("source", {})
        dest = comp.get("destination", {})
        
        # If nested structure exists, use it
        if source or dest:
            source_id = source.get("id", "")
            dest_id = dest.get("id", "")
            relationships = comp.get("selectedRelationships", [])
        else:
            # Use flat structure (top-level fields)
            source_id = conn.get("sourceId", "")
            dest_id = conn.get("destinationId", "")
            relationships = conn.get("selectedRelationships", []) or []
        
        summary.append({
            "from": proc_id_to_name.get(source_id, source_id[:8] if source_id else "?"),
            "to": proc_id_to_name.get(dest_id, dest_id[:8] if dest_id else "?"),
            "relationship": relationships[0] if relationships else "?"
        })
    
    return summary

