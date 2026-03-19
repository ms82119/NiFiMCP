"""
Batch mode extensions for get_nifi_object_details.

Apply these changes to: nifi_mcp_server/api_tools/review.py

Extends get_nifi_object_details to support batch retrieval of multiple objects.
"""

import asyncio
from typing import Dict, List, Literal, Any


# =============================================================================
# EXTENDED FUNCTION SIGNATURE
# =============================================================================

EXTENDED_SIGNATURE = """
# Update get_nifi_object_details signature in review.py:

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
    \"\"\"
    Retrieves detailed information for one or more NiFi objects.

    Parameters
    ----------
    object_type : Literal[...]
        The type of NiFi objects to retrieve.
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
    \"\"\"
"""


# =============================================================================
# IMPLEMENTATION LOGIC
# =============================================================================

BATCH_IMPLEMENTATION = """
# Replace existing get_nifi_object_details implementation with:

async def get_nifi_object_details(
    object_type: Literal["processor", "connection", "port", "process_group", "controller_service"],
    object_id: str | None = None,
    object_ids: List[str] | None = None,
    output_format: Literal["full", "summary", "doc_optimized"] = "full",
    include_properties: bool = True,
    max_parallel: int = 5
) -> Dict | List[Dict]:
    
    # Validation
    if object_id and object_ids:
        raise ToolError("Provide either object_id or object_ids, not both.")
    if not object_id and not object_ids:
        raise ToolError("Must provide either object_id or object_ids.")
    
    # Single ID mode (backward compatible)
    if object_id:
        result = await _fetch_single_object_details(object_type, object_id)
        return _format_output(result, output_format, include_properties)
    
    # Batch mode
    local_logger.info(f"Batch fetching {len(object_ids)} {object_type} details")
    
    # Use semaphore to limit parallel requests
    semaphore = asyncio.Semaphore(max_parallel)
    
    async def fetch_with_semaphore(obj_id: str) -> Dict:
        async with semaphore:
            try:
                result = await _fetch_single_object_details(object_type, obj_id)
                return {
                    "id": obj_id,
                    "status": "success",
                    "data": _format_output(result, output_format, include_properties)
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
"""


# =============================================================================
# OUTPUT FORMATTING FUNCTIONS
# =============================================================================

OUTPUT_FORMATTERS = """
# Add these helper functions to review.py:

def _format_output(
    details: Dict, 
    output_format: str, 
    include_properties: bool
) -> Dict:
    \"\"\"Format object details based on output format.\"\"\"
    
    if output_format == "full":
        if not include_properties:
            details = _remove_properties(details)
        return details
    
    elif output_format == "summary":
        return _extract_summary(details)
    
    elif output_format == "doc_optimized":
        return _extract_doc_optimized(details)
    
    return details


def _extract_summary(details: Dict) -> Dict:
    \"\"\"Extract essential fields only.\"\"\"
    component = details.get("component", {})
    return {
        "id": details.get("id"),
        "name": component.get("name"),
        "type": component.get("type"),
        "state": component.get("state"),
        "validationStatus": component.get("validationStatus"),
        "relationships": [r.get("name") for r in component.get("relationships", [])]
    }


def _extract_doc_optimized(details: Dict) -> Dict:
    \"\"\"Extract fields needed for documentation.\"\"\"
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
    
    return {
        "id": details.get("id"),
        "name": component.get("name"),
        "type": component.get("type"),
        "state": component.get("state"),
        "comments": component.get("comments", ""),
        "expressions": expressions,
        "relationships": relationships,
        "business_properties": _extract_business_properties(component)
    }


def _extract_business_properties(component: Dict) -> Dict:
    \"\"\"Extract business-relevant properties based on processor type.\"\"\"
    proc_type = component.get("type", "").split(".")[-1]
    config = component.get("config", {})
    properties = config.get("properties", {})
    
    result = {}
    
    if proc_type == "RouteOnAttribute":
        for prop, value in properties.items():
            if prop not in ["Routing Strategy"] and value:
                result[prop] = value
                
    elif proc_type in ["ExecuteScript", "ExecuteGroovyScript"]:
        script = properties.get("Script Body", "")
        if script:
            result["script_preview"] = script[:500] + ("..." if len(script) > 500 else "")
            result["script_length"] = len(script)
            
    elif proc_type == "JoltTransformJSON":
        spec = properties.get("Jolt Specification", "")
        if spec:
            result["jolt_spec"] = spec
            
    elif proc_type == "QueryRecord":
        for prop, value in properties.items():
            if value and ("query" in prop.lower() or "sql" in prop.lower()):
                result[prop] = value
                
    elif proc_type == "InvokeHTTP":
        result["url"] = properties.get("Remote URL", "")
        result["method"] = properties.get("HTTP Method", "GET")
        
    elif proc_type in ["GetFile", "PutFile"]:
        result["directory"] = properties.get("Directory", "")
        result["file_filter"] = properties.get("File Filter", "")
        
    elif proc_type in ["ConsumeKafka", "PublishKafka", "ConsumeKafka_2_6", "PublishKafka_2_6"]:
        result["topic"] = properties.get("topic", properties.get("Topic Name(s)", ""))
        result["bootstrap_servers"] = properties.get("bootstrap.servers", "")
    
    return result
"""

