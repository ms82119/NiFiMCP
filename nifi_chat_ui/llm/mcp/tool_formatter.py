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
                # Clean up the schema for Gemini - remove additionalProperties
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
    def _clean_schema_for_gemini(schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Clean up schema for Gemini by removing unsupported fields.
        
        Gemini uses Protocol Buffer schemas which don't support:
        - additionalProperties
        - title fields
        - default values
        - anyOf unions
        - Complex nested structures
        """
        if not isinstance(schema, dict):
            return {"type": "object", "properties": {}}
        
        # Make a deep copy to avoid modifying the original
        import copy
        cleaned = copy.deepcopy(schema)
        
        def clean_recursive(obj):
            if isinstance(obj, dict):
                # Remove Protocol Buffer incompatible fields
                obj.pop("additionalProperties", None)
                obj.pop("title", None)
                obj.pop("default", None)
                
                # Handle anyOf patterns - convert to the first non-null type
                if "anyOf" in obj:
                    any_of_types = obj["anyOf"]
                    # Find the first non-null type
                    main_type = None
                    for type_def in any_of_types:
                        if isinstance(type_def, dict) and type_def.get("type") != "null":
                            main_type = type_def
                            break
                    
                    if main_type:
                        # Replace anyOf with the main type
                        obj.pop("anyOf")
                        obj.update(main_type)
                        # Remove any incompatible fields that might have been copied
                        obj.pop("default", None)
                        obj.pop("additionalProperties", None)
                        obj.pop("title", None)
                    else:
                        # If only null types, make it a string (fallback)
                        obj.pop("anyOf")
                        obj["type"] = "string"
                
                # Recursively clean nested objects
                for key, value in obj.items():
                    clean_recursive(value)
                    
            elif isinstance(obj, list):
                for item in obj:
                    clean_recursive(item)
        
        clean_recursive(cleaned)
        
        # Validate that no incompatible fields remain
        def validate_gemini_compatibility(obj, path=""):
            if isinstance(obj, dict):
                incompatible_fields = []
                for field in ["additionalProperties", "title", "default", "anyOf"]:
                    if field in obj:
                        incompatible_fields.append(field)
                
                if incompatible_fields:
                    logger.warning(f"Gemini-incompatible fields found at {path}: {incompatible_fields}")
                
                for key, value in obj.items():
                    validate_gemini_compatibility(value, f"{path}.{key}" if path else key)
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    validate_gemini_compatibility(item, f"{path}[{i}]")
        
        validate_gemini_compatibility(cleaned)
        
        # Ensure we have a valid schema structure
        if not cleaned.get("type"):
            cleaned["type"] = "object"
        if not cleaned.get("properties"):
            cleaned["properties"] = {}
        
        # Ensure all properties have explicit type information
        if "properties" in cleaned and isinstance(cleaned["properties"], dict):
            for prop_name, prop_value in cleaned["properties"].items():
                if isinstance(prop_value, dict):
                    # If property has no type, infer it from other fields or set default
                    if "type" not in prop_value:
                        # Check if it has enum values (implies string type)
                        if "enum" in prop_value:
                            prop_value["type"] = "string"
                        # Check if it has items (implies array type)
                        elif "items" in prop_value:
                            prop_value["type"] = "array"
                        # Check if it has properties (implies object type)
                        elif "properties" in prop_value:
                            prop_value["type"] = "object"
                        # Check description for hints
                        elif prop_value.get("description", "").lower().find("boolean") != -1:
                            prop_value["type"] = "boolean"
                        elif prop_value.get("description", "").lower().find("number") != -1 or \
                             prop_value.get("description", "").lower().find("integer") != -1:
                            prop_value["type"] = "number"
                        else:
                            # Default to string for unknown types
                            prop_value["type"] = "string"
                            logger.debug(f"Added default 'string' type to property '{prop_name}' in schema")
                    
                    # Also ensure nested properties have types (recursive check)
                    if "properties" in prop_value and isinstance(prop_value["properties"], dict):
                        for nested_prop_name, nested_prop_value in prop_value["properties"].items():
                            if isinstance(nested_prop_value, dict) and "type" not in nested_prop_value:
                                # Apply same type inference logic to nested properties
                                if "enum" in nested_prop_value:
                                    nested_prop_value["type"] = "string"
                                elif "items" in nested_prop_value:
                                    nested_prop_value["type"] = "array"
                                elif "properties" in nested_prop_value:
                                    nested_prop_value["type"] = "object"
                                else:
                                    nested_prop_value["type"] = "string"
                                    logger.debug(f"Added default 'string' type to nested property '{prop_name}.{nested_prop_name}' in schema")
        
        return cleaned 