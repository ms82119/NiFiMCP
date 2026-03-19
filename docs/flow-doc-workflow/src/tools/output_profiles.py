"""
Output format profiles for token-optimized data retrieval.

Add to: nifi_mcp_server/flow_documenter_improved.py (or separate file)
"""

from typing import Dict, Any


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

