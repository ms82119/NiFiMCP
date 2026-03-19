"""
Component formatting utilities for unambiguous component references in documentation.

This module provides consistent formatting for components (processors, ports, etc.)
to ensure they are unambiguously identified with name, type, and component ID.
"""

from typing import Dict, Any, Optional


def format_component_reference(
    component: Dict[str, Any],
    component_id: Optional[str] = None,
    short_id_length: int = 8
) -> str:
    """
    Format a component reference with name, type, and ID for unambiguous identification.
    
    Format: "ComponentName (TypeName) [id:xxxx]"
    
    Args:
        component: Component dict with 'component' key or direct component data
        component_id: Explicit component ID (if not in component dict)
        short_id_length: Length of shortened ID to display (default 8)
    
    Returns:
        Formatted string like: "GetFile (GetFile) [id:12345678]"
    """
    # Extract component data
    if "component" in component:
        comp_data = component["component"]
        comp_id = component.get("id") or component_id
    else:
        comp_data = component
        comp_id = component_id or comp_data.get("id")
    
    # Get name
    name = comp_data.get("name", "Unknown")
    
    # Get type (simplified - just the class name)
    comp_type = comp_data.get("type", "")
    if comp_type:
        type_simple = comp_type.split(".")[-1]
    else:
        type_simple = "Unknown"
    
    # Format ID
    if comp_id:
        short_id = comp_id[:short_id_length] if len(comp_id) > short_id_length else comp_id
        return f"{name} ({type_simple}) [id:{short_id}]"
    else:
        return f"{name} ({type_simple})"


def format_processor_reference(
    processor: Dict[str, Any],
    short_id_length: int = 8
) -> str:
    """
    Convenience function for processor references.
    
    Args:
        processor: Processor dict
        short_id_length: Length of shortened ID
    
    Returns:
        Formatted string
    """
    return format_component_reference(processor, short_id_length=short_id_length)


def format_component_for_table(
    component: Dict[str, Any],
    component_id: Optional[str] = None,
    short_id_length: int = 8
) -> Dict[str, str]:
    """
    Format component data for table display.
    
    Returns:
        Dict with 'name', 'type', 'id' keys
    """
    if "component" in component:
        comp_data = component["component"]
        comp_id = component.get("id") or component_id
    else:
        comp_data = component
        comp_id = component_id or comp_data.get("id")
    
    name = comp_data.get("name", "Unknown")
    comp_type = comp_data.get("type", "")
    type_simple = comp_type.split(".")[-1] if comp_type else "Unknown"
    
    if comp_id:
        short_id = comp_id[:short_id_length] if len(comp_id) > short_id_length else comp_id
    else:
        short_id = "N/A"
    
    return {
        "name": name,
        "type": type_simple,
        "id": short_id,
        "full_reference": f"{name} ({type_simple}) [id:{short_id}]"
    }


def format_destination_reference(
    destination_id: str,
    destination_name: Optional[str] = None,
    destination_type: Optional[str] = None,
    short_id_length: int = 8
) -> str:
    """
    Format a destination reference when we only have ID/name/type.
    
    Used for error handling destinations that might be processors, ports, etc.
    """
    name = destination_name or "Unknown"
    type_simple = destination_type.split(".")[-1] if destination_type else "Unknown"
    short_id = destination_id[:short_id_length] if len(destination_id) > short_id_length else destination_id
    
    return f"{name} ({type_simple}) [id:{short_id}]"

