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
    
    # Fetch processor details first (needed for states in categorization)
    proc_ids = [p.get("id") for p in group_processors if p.get("id")]
    cached_proc_details = {}
    if proc_ids:
        try:
            result = await nifi_tool_caller(
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
                    proc_id = item.get("id")
                    proc_data = item.get("data", {})
                    # DEFENSIVE: Ensure data is a dict before storing
                    if isinstance(proc_data, dict):
                        cached_proc_details[proc_id] = proc_data
                    else:
                        if logger:
                            logger.warning(f"Processor data for {proc_id} is not a dict: {type(proc_data)}")
                        cached_proc_details[proc_id] = {}
        except Exception as e:
            if logger:
                logger.warning(f"Failed to fetch processor details for virtual group: {e}")
            cached_proc_details = {}
    
    # Categorize with states (using cached details)
    categorized = categorize_processors(group_processors, categorizer, include_states=True, cached_proc_details=cached_proc_details)
    
    io_endpoints = await io_extractor(
        group_processors,
        nifi_tool_caller,
        prep_res,
        categorizer,
        cached_proc_details,
        logger
    )
    
    # Extract business properties (routing rules, JSONPath expressions, etc.)
    business_logic = []
    for proc_id, proc_data in cached_proc_details.items():
        # DEFENSIVE: Ensure proc_data is a dict
        if not isinstance(proc_data, dict):
            if logger:
                logger.warning(f"proc_data for {proc_id} is not a dict: {type(proc_data)}")
            continue
        proc_name = proc_data.get("name", "Unknown")
        proc_type = proc_data.get("type", "")
        if proc_type:
            proc_type = proc_type.split(".")[-1]
        business_props = proc_data.get("business_properties", {})
        
        # DEFENSIVE: Ensure business_props is a dict
        if business_props and isinstance(business_props, dict):
            business_logic.append({
                "processor": proc_name,
                "type": proc_type,
                "properties": business_props
            })
    
    prompt = PG_SUMMARY_PROMPT.format(
        pg_name=virtual_group.get("name", "Virtual Group"),
        pg_purpose=virtual_group.get("purpose", ""),
        processor_count=len(group_processors),
        categories_json=json.dumps(categorized, indent=2),
        io_endpoints_json=json.dumps(io_endpoints, indent=2, default=str),
        business_logic_json=json.dumps(business_logic, indent=2, default=str) if business_logic else "[]",
        child_summaries_json="[]"
    )
    
    response = await llm_caller(
        messages=[{"role": "user", "content": prompt}],
        tools=None,
        execution_state=prep_res,
        action_id=f"vg-summary-{virtual_group.get('name', 'vg')}"
    )
    
    # Extract error handling for virtual group (connections within parent PG)
    # Note: Virtual groups are within a parent PG, so we need connections from that PG
    # Connections can have parentGroupId in component.parentGroupId OR sourceGroupId at top level
    vg_connections = []
    for c in prep_res["flow_graph"].get("connections", []):
        parent_id = c.get("component", {}).get("parentGroupId") or c.get("sourceGroupId")
        if parent_id == parent_pg_id:
            vg_connections.append(c)
    
    # Filter to only connections involving processors in this virtual group
    vg_proc_ids = set(virtual_group.get("processor_ids", []))
    vg_connections_filtered = []
    for c in vg_connections:
        # Handle both nested and flat connection structures
        comp = c.get("component", {})
        source = comp.get("source", {})
        dest = comp.get("destination", {})
        source_id = source.get("id") if source else c.get("sourceId")
        dest_id = dest.get("id") if dest else c.get("destinationId")
        if source_id in vg_proc_ids or dest_id in vg_proc_ids:
            vg_connections_filtered.append(c)
    
    error_handling = await error_extractor(
        group_processors, vg_connections_filtered, nifi_tool_caller, prep_res, cached_proc_details, logger
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
        # Fetch processor details first (needed for states in categorization)
        proc_ids = [p.get("id") for p in direct_processors if p.get("id")]
        cached_proc_details = {}
        if proc_ids:
            try:
                result = await nifi_tool_caller(
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
                if logger:
                    logger.warning(f"Failed to fetch parent processor details: {e}")
                cached_proc_details = {}
        
        # Extract IO endpoints from parent's processors
        parent_io_endpoints = await io_extractor(
            direct_processors, nifi_tool_caller, prep_res, categorizer, cached_proc_details, logger
        )
        
        # Categorize parent's processors with states
        categorized = categorize_processors(direct_processors, categorizer, include_states=True, cached_proc_details=cached_proc_details)
        
        # Extract business properties from parent's processors
        business_logic = []
        for proc_id, proc_data in cached_proc_details.items():
            proc_name = proc_data.get("name", "Unknown")
            proc_type = proc_data.get("type", "").split(".")[-1]
            business_props = proc_data.get("business_properties", {})
            
            if business_props:
                business_logic.append({
                    "processor": proc_name,
                    "type": proc_type,
                    "properties": business_props
                })
        
        # Format connections for parent's own processors
        # Connections can have parentGroupId in component.parentGroupId OR sourceGroupId at top level
        pg_connections = []
        for c in prep_res["flow_graph"].get("connections", []):
            # Check both possible structures
            parent_id = c.get("component", {}).get("parentGroupId") or c.get("sourceGroupId")
            if parent_id == pg.get("id"):
                pg_connections.append(c)
        connection_summary = format_connections_for_prompt(pg_connections, cached_proc_details)
        
        prompt = PG_WITH_CHILDREN_AND_PROCESSORS_PROMPT.format(
            pg_name=pg.get("name", pg.get("id", "")[:8]),
            child_count=len(child_summaries),
            children_digest=json.dumps(children_digest, indent=2),
            processor_count=len(direct_processors),
            categories_json=json.dumps(categorized, indent=2),
            connections_json=json.dumps(connection_summary, indent=2, default=str),
            io_endpoints_json=json.dumps(parent_io_endpoints, indent=2, default=str),
            business_logic_json=json.dumps(business_logic, indent=2, default=str) if business_logic else "[]"
        )
        
        # Aggregate IO endpoints from both parent and children
        all_io = list(parent_io_endpoints)
        parent_categories = categorized
    else:
        # Parent only has children, no own processors - use original prompt
        prompt = PG_WITH_CHILDREN_PROMPT.format(
            pg_name=pg.get("name", pg.get("id", "")[:8]),
            child_count=len(child_summaries),
            children_digest=json.dumps(children_digest, indent=2)
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
        "error_handling": all_errors
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
    
    # Fetch processor details once with doc_optimized format (includes relationships and properties)
    # This data will be reused by both IO extraction and error handling
    proc_ids = [p.get("id") for p in processors if p.get("id")]
    cached_proc_details = {}
    if proc_ids:
        try:
            result = await nifi_tool_caller(
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
                    proc_id = item.get("id")
                    proc_data = item.get("data", {})
                    # DEFENSIVE: Ensure data is a dict before storing
                    if isinstance(proc_data, dict):
                        cached_proc_details[proc_id] = proc_data
                    else:
                        if logger:
                            logger.warning(f"Processor data for {proc_id} is not a dict: {type(proc_data)}")
                        cached_proc_details[proc_id] = {}
        except Exception as e:
            if logger:
                logger.warning(f"Failed to fetch processor details for analysis: {e}")
            cached_proc_details = {}
    
    # Categorize processors with states (using cached details for accurate states)
    categorized = categorize_processors(processors, categorizer, include_states=True, cached_proc_details=cached_proc_details)
    
    # Use cached details for IO extraction
    io_endpoints = await io_extractor(
        processors, nifi_tool_caller, prep_res, categorizer, cached_proc_details, logger
    )
    
    # Extract business properties (routing rules, JSONPath expressions, etc.) from cached details
    business_logic = []
    for proc_id, proc_data in cached_proc_details.items():
        # DEFENSIVE: Ensure proc_data is a dict
        if not isinstance(proc_data, dict):
            if logger:
                logger.warning(f"proc_data for {proc_id} is not a dict: {type(proc_data)}")
            continue
        proc_name = proc_data.get("name", "Unknown")
        proc_type = proc_data.get("type", "")
        if proc_type:
            proc_type = proc_type.split(".")[-1]
        business_props = proc_data.get("business_properties", {})
        
        # DEFENSIVE: Ensure business_props is a dict
        if business_props and isinstance(business_props, dict):
            business_logic.append({
                "processor": proc_name,
                "type": proc_type,
                "properties": business_props
            })
    
    # Format connections for this PG
    # Connections can have parentGroupId in component.parentGroupId OR sourceGroupId at top level
    pg_connections = []
    for c in prep_res["flow_graph"].get("connections", []):
        parent_id = c.get("component", {}).get("parentGroupId") or c.get("sourceGroupId")
        if parent_id == pg.get("id"):
            pg_connections.append(c)
    connection_summary = format_connections_for_prompt(pg_connections, cached_proc_details)
    
    prompt = PG_SUMMARY_PROMPT.format(
        pg_name=pg.get("name", pg.get("id", "")[:8]),
        pg_purpose="",
        processor_count=len(processors),
        categories_json=json.dumps(categorized, indent=2),
        io_endpoints_json=json.dumps(io_endpoints, indent=2, default=str),
        business_logic_json=json.dumps(business_logic, indent=2, default=str) if business_logic else "[]",
        connections_json=json.dumps(connection_summary, indent=2, default=str),
        child_summaries_json="[]"
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
    
    # Extract error handling for leaf PG (reuse cached details)
    # Note: pg_connections already fetched above for prompt
    error_handling = await error_extractor(
        processors, pg_connections, nifi_tool_caller, prep_res, cached_proc_details, logger
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


def categorize_processors(
    processors: List[Dict],
    categorizer,
    include_states: bool = True,
    cached_proc_details: Optional[Dict[str, Dict]] = None
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
        
        # Get state from cached details if available, otherwise from proc dict
        proc_state = "UNKNOWN"
        if cached_proc_details and proc_id:
            proc_state = cached_proc_details.get(proc_id, {}).get("state", 
                proc.get("component", {}).get("state", proc.get("state", "UNKNOWN")))
        else:
            proc_state = proc.get("component", {}).get("state", proc.get("state", "UNKNOWN"))
        
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
    cached_proc_details: Optional[Dict[str, Dict]] = None
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
        
        # Get processor names from cached details if available
        if cached_proc_details:
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

