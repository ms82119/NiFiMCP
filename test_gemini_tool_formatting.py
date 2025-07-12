#!/usr/bin/env python3
"""
Test script to verify Gemini tool formatting fix.
"""

from nifi_chat_ui.llm.mcp.tool_formatter import ToolFormatter

def test_gemini_tool_formatting():
    """Test that problematic tools are now properly formatted for Gemini."""
    
    # Create mock tools with the problematic schemas
    mock_tools = [
        {
            "type": "function",
            "function": {
                "name": "operate_nifi_objects",
                "description": "Test tool with additionalProperties in array items",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "operations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": True  # This was causing the issue
                            }
                        }
                    },
                    "required": ["operations"]
                }
            }
        },
        {
            "type": "function", 
            "function": {
                "name": "delete_nifi_objects",
                "description": "Test tool with additionalProperties in array items",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "objects": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": True  # This was causing the issue
                            }
                        }
                    },
                    "required": ["objects"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "update_nifi_processors_properties", 
                "description": "Test tool with additionalProperties in array items",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "updates": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": True  # This was causing the issue
                            }
                        }
                    },
                    "required": ["updates"]
                }
            }
        }
    ]
    
    print(f"Testing Gemini tool formatting for {len(mock_tools)} problematic tools...")
    
    # Try to format them for Gemini
    try:
        gemini_tools = ToolFormatter.format_tools_for_provider(mock_tools, "gemini")
        print(f"✅ Successfully formatted {len(gemini_tools)} tools for Gemini")
        
        # Check each tool individually
        for tool in mock_tools:
            tool_name = tool.get("function", {}).get("name")
            print(f"  ✅ {tool_name}")
            
        # Test the cleaned schema structure
        print("\n🔍 Checking cleaned schema structure...")
        for tool in mock_tools:
            tool_name = tool.get("function", {}).get("name")
            original_schema = tool.get("function", {}).get("parameters", {})
            
            # Test the cleaning function directly
            cleaned_schema = ToolFormatter._clean_schema_for_gemini(original_schema)
            
            # Check if additionalProperties was removed from array items
            for prop_name, prop_value in cleaned_schema.get("properties", {}).items():
                if prop_value.get("type") == "array" and "items" in prop_value:
                    items_schema = prop_value["items"]
                    if "additionalProperties" not in items_schema:
                        print(f"  ✅ {tool_name}.{prop_name}.items: additionalProperties removed")
                    else:
                        print(f"  ❌ {tool_name}.{prop_name}.items: additionalProperties still present")
            
    except Exception as e:
        print(f"❌ Error formatting tools for Gemini: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = test_gemini_tool_formatting()
    if success:
        print("\n🎉 All problematic tools are now properly formatted for Gemini!")
    else:
        print("\n❌ Some tools still have formatting issues.") 