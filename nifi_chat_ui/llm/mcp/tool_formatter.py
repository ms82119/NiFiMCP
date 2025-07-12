"""
Tool formatting utilities for different LLM providers.

This module handles formatting of MCP tools for different LLM providers.
"""

from typing import List, Dict, Any, Optional
from loguru import logger


class ToolFormatter:
    """Format tools for different LLM providers."""
    
    @staticmethod
    def format_tools_for_provider(tools: List[Dict[str, Any]], provider: str) -> Any:
        """
        Format tools for a specific LLM provider.
        
        Args:
            tools: List of tool definitions from MCP server
            provider: LLM provider name
            
        Returns:
            Provider-specific tool format
        """
        provider_lower = provider.lower()
        
        if provider_lower == "openai":
            return ToolFormatter._format_for_openai(tools)
        elif provider_lower == "perplexity":
            return ToolFormatter._format_for_perplexity(tools)
        elif provider_lower == "anthropic":
            return ToolFormatter._format_for_anthropic(tools)
        elif provider_lower == "gemini":
            return ToolFormatter._format_for_gemini(tools)
        else:
            logger.warning(f"Unknown provider '{provider}', using OpenAI format")
            return ToolFormatter._format_for_openai(tools)
    
    @staticmethod
    def _format_for_openai(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Format tools for OpenAI (OpenAI-compatible format)."""
        # OpenAI format matches our API format directly, but clean up the schema
        cleaned_tools = []
        for tool in tools:
            if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
                function_def = tool["function"]
                
                # Clean up parameters if present
                if "parameters" in function_def and isinstance(function_def["parameters"], dict):
                    params = function_def["parameters"]
                    # Remove top-level additionalProperties field which might cause issues
                    params.pop("additionalProperties", None)
                    
                    # Clean individual properties
                    if "properties" in params and isinstance(params["properties"], dict):
                        props = params["properties"]
                        for prop_name, prop_value in props.items():
                            # Ensure prop_value is a dict before cleaning
                            if isinstance(prop_value, dict):
                                prop_value.pop("additionalProperties", None)
                            # If prop_value is not a dict or becomes empty after cleaning, set default type
                            if not isinstance(prop_value, dict) or not prop_value:
                                props[prop_name] = {"type": "string"}
                
                cleaned_tools.append(tool)
        
        return cleaned_tools
    
    @staticmethod
    def _format_for_perplexity(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Format tools for Perplexity (OpenAI-compatible format)."""
        # Perplexity uses the same format as OpenAI
        return ToolFormatter._format_for_openai(tools)
    
    @staticmethod
    def _format_for_anthropic(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Format tools for Anthropic."""
        # Convert OpenAI format to Anthropic format
        anthropic_tools = []
        
        for tool_data in tools:
            func_details = tool_data.get("function", {})
            name = func_details.get("name")
            description = func_details.get("description")
            parameters_schema = func_details.get("parameters") 
            
            if not name or not description:
                continue
                
            # Anthropic expects tools WITHOUT any "type" field
            # Just the direct tool definition with name, description, and input_schema
            anthropic_tool = {
                "name": name,
                "description": description,
                "input_schema": parameters_schema or {"type": "object", "properties": {}}
            }
            
            anthropic_tools.append(anthropic_tool)
        
        return anthropic_tools
    
    @staticmethod
    def _format_for_gemini(tools: List[Dict[str, Any]]) -> List[Any]:
        """Format tools for Gemini (FunctionDeclaration objects)."""
        try:
            import google.generativeai.types as genai_types
        except ImportError:
            logger.error("Google Generative AI types not available for Gemini tool formatting")
            return []
        
        gemini_tools = []
        
        for tool_data in tools:
            func_details = tool_data.get("function", {})
            name = func_details.get("name")
            description = func_details.get("description")
            parameters_schema = func_details.get("parameters")
            
            if not name or not description:
                logger.warning(f"Skipping tool without name or description: {tool_data}")
                continue
            
            try:
                # Clean the parameters schema for Gemini compatibility
                cleaned_schema = ToolFormatter._clean_schema_for_gemini(parameters_schema)
                
                # Create Gemini FunctionDeclaration object
                function_declaration = genai_types.FunctionDeclaration(
                    name=name,
                    description=description,
                    parameters=cleaned_schema
                )
                gemini_tools.append(function_declaration)
                logger.debug(f"Created Gemini FunctionDeclaration for: {name}")
                
            except Exception as e:
                logger.error(f"Failed to create FunctionDeclaration for {name}: {e}")
                continue
        
        logger.info(f"Formatted {len(gemini_tools)} tools for Gemini")
        return gemini_tools
    
    @staticmethod
    def _clean_schema_for_gemini(schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Clean schema to be compatible with Gemini FunctionDeclaration."""
        if not schema or not isinstance(schema, dict):
            return {"type": "object", "properties": {}}
        
        # Create a deep copy to avoid modifying the original
        cleaned = schema.copy()
        
        # Remove unsupported fields
        cleaned.pop("additionalProperties", None)
        cleaned.pop("$schema", None)
        cleaned.pop("title", None)
        cleaned.pop("description", None)
        cleaned.pop("$ref", None)  # Remove $ref fields which Gemini doesn't support
        
        # Clean properties if they exist
        if "properties" in cleaned and isinstance(cleaned["properties"], dict):
            props = cleaned["properties"]
            for prop_name, prop_value in list(props.items()):
                if isinstance(prop_value, dict):
                    # Remove unsupported fields from each property
                    prop_value.pop("additionalProperties", None)
                    prop_value.pop("$schema", None)
                    prop_value.pop("title", None)
                    prop_value.pop("description", None)
                    prop_value.pop("$ref", None)  # Remove $ref fields
                    
                    # Ensure required fields are present
                    if "type" not in prop_value:
                        prop_value["type"] = "string"
                    
                    # Clean array items if they exist
                    if "items" in prop_value and isinstance(prop_value["items"], dict):
                        items_schema = prop_value["items"]
                        
                        # If items is just a $ref, replace it with the appropriate schema
                        if "$ref" in items_schema and len(items_schema) == 1:
                            ref_path = items_schema["$ref"]
                            # Map common TypedDict references to their schemas
                            replacement_schema = ToolFormatter._get_typeddict_schema(ref_path)
                            if replacement_schema:
                                prop_value["items"] = replacement_schema
                            else:
                                # Fallback to generic object schema
                                prop_value["items"] = {
                                    "type": "object",
                                    "properties": {}
                                }
                        else:
                            # Clean existing items schema
                            items_schema.pop("additionalProperties", None)
                            items_schema.pop("$schema", None)
                            items_schema.pop("title", None)
                            items_schema.pop("description", None)
                            items_schema.pop("$ref", None)  # Remove $ref fields
                            
                            # Ensure items have a type
                            if "type" not in items_schema:
                                items_schema["type"] = "object"
                            
                            # Clean nested properties in items if they exist
                            if "properties" in items_schema and isinstance(items_schema["properties"], dict):
                                nested_props = items_schema["properties"]
                                for nested_name, nested_value in list(nested_props.items()):
                                    if isinstance(nested_value, dict):
                                        nested_value.pop("additionalProperties", None)
                                        nested_value.pop("$schema", None)
                                        nested_value.pop("title", None)
                                        nested_value.pop("description", None)
                                        nested_value.pop("$ref", None)  # Remove $ref fields
                                        if "type" not in nested_value:
                                            nested_value["type"] = "string"
                    
                    # Clean nested properties if they exist
                    if "properties" in prop_value and isinstance(prop_value["properties"], dict):
                        nested_props = prop_value["properties"]
                        for nested_name, nested_value in list(nested_props.items()):
                            if isinstance(nested_value, dict):
                                nested_value.pop("additionalProperties", None)
                                nested_value.pop("$schema", None)
                                nested_value.pop("title", None)
                                nested_value.pop("description", None)
                                nested_value.pop("$ref", None)  # Remove $ref fields
                                if "type" not in nested_value:
                                    nested_value["type"] = "string"
                else:
                    # If property value is not a dict, replace with default
                    props[prop_name] = {"type": "string"}
        
        # Ensure required fields are present
        if "type" not in cleaned:
            cleaned["type"] = "object"
        if "properties" not in cleaned:
            cleaned["properties"] = {}
        
        return cleaned
    
    @staticmethod
    def _get_typeddict_schema(ref_path: str) -> Optional[Dict[str, Any]]:
        """Get the schema for a TypedDict reference."""
        # Map common TypedDict references to their schemas
        schema_map = {
            "#/$defs/OperationRequest": {
                "type": "object",
                "properties": {
                    "object_type": {
                        "type": "string",
                        "enum": ["processor", "port", "process_group", "controller_service"]
                    },
                    "object_id": {"type": "string"},
                    "operation_type": {
                        "type": "string", 
                        "enum": ["start", "stop", "enable", "disable"]
                    },
                    "name": {"type": "string"}
                },
                "required": ["object_type", "object_id", "operation_type"]
            },
            "#/$defs/DeleteRequest": {
                "type": "object",
                "properties": {
                    "object_type": {
                        "type": "string",
                        "enum": ["processor", "connection", "port", "process_group", "controller_service"]
                    },
                    "object_id": {"type": "string"},
                    "name": {"type": "string"}
                },
                "required": ["object_type", "object_id"]
            },
            "#/$defs/ProcessorPropertyUpdate": {
                "type": "object",
                "properties": {
                    "processor_id": {"type": "string"},
                    "properties": {"type": "object"},
                    "name": {"type": "string"}
                },
                "required": ["processor_id", "properties"]
            },
            "#/$defs/ProcessorDefinition": {
                "type": "object",
                "properties": {
                    "processor_type": {"type": "string"},
                    "name": {"type": "string"},
                    "position_x": {"type": "integer"},
                    "position_y": {"type": "integer"},
                    "position": {"type": "object"},
                    "process_group_id": {"type": "string"},
                    "properties": {"type": "object"}
                },
                "required": ["processor_type", "name"]
            },
            "#/$defs/PortDefinition": {
                "type": "object",
                "properties": {
                    "port_type": {
                        "type": "string",
                        "enum": ["input", "output"]
                    },
                    "name": {"type": "string"},
                    "process_group_id": {"type": "string"}
                },
                "required": ["port_type", "name"]
            },
            "#/$defs/ControllerServiceDefinition": {
                "type": "object",
                "properties": {
                    "service_type": {"type": "string"},
                    "name": {"type": "string"},
                    "properties": {"type": "object"}
                },
                "required": ["service_type", "name"]
            },
            "#/$defs/ConnectionDefinition": {
                "type": "object",
                "properties": {
                    "source_name": {"type": "string"},
                    "target_name": {"type": "string"},
                    "relationships": {"type": "array", "items": {"type": "string"}},
                    "process_group_id": {"type": "string"}
                },
                "required": ["source_name", "target_name", "relationships"]
            },
            "#/$defs/NifiObjectDefinition": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["controller_service", "processor", "connection"]
                    },
                    "service_type": {"type": "string"},
                    "name": {"type": "string"},
                    "properties": {"type": "object"},
                    "processor_type": {"type": "string"},
                    "position_x": {"type": "integer"},
                    "position_y": {"type": "integer"},
                    "position": {"type": "object"},
                    "process_group_id": {"type": "string"},
                    "source_name": {"type": "string"},
                    "target_name": {"type": "string"},
                    "relationships": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["type", "name"]
            }
        }
        
        return schema_map.get(ref_path) 