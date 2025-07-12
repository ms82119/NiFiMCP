"""
Message conversion utilities for different LLM providers.

This module handles conversion between different message formats used by
various LLM providers (OpenAI, Anthropic, Gemini, etc.).
"""

import json
import uuid
from typing import List, Dict, Any, Optional
from loguru import logger

try:
    from google.protobuf.json_format import MessageToDict
except ImportError:
    MessageToDict = None


def _convert_protobuf_to_dict(obj):
    """
    Recursively convert protobuf objects (including MapComposite) to JSON-serializable dictionaries.
    
    Args:
        obj: The object to convert
        
    Returns:
        A JSON-serializable representation of the object
    """
    # Handle None
    if obj is None:
        return None
    
    # Handle primitive types that are already JSON-serializable
    if isinstance(obj, (str, int, float, bool)):
        return obj
    
    # Handle MapComposite and similar dict-like protobuf objects
    if hasattr(obj, 'items') and callable(obj.items):
        try:
            result = {}
            for key, value in obj.items():
                result[key] = _convert_protobuf_to_dict(value)
            return result
        except Exception as e:
            logger.warning(f"Failed to convert dict-like protobuf object: {e}")
            return str(obj)
    
    # Handle lists and list-like objects
    if isinstance(obj, (list, tuple)) or (hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes))):
        try:
            return [_convert_protobuf_to_dict(item) for item in obj]
        except Exception as e:
            logger.warning(f"Failed to convert list-like protobuf object: {e}")
            return str(obj)
    
    # Handle regular dictionaries
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            result[key] = _convert_protobuf_to_dict(value)
        return result
    
    # Handle protobuf messages using MessageToDict if available
    if hasattr(obj, 'DESCRIPTOR') and MessageToDict:
        try:
            return MessageToDict(obj, preserving_proto_field_name=True)
        except Exception as e:
            logger.warning(f"Failed to convert protobuf message with MessageToDict: {e}")
    
    # Handle other protobuf-like objects by checking for common protobuf attributes
    if hasattr(obj, '_pb') or str(type(obj)).startswith('<proto.') or 'proto.marshal' in str(type(obj)):
        try:
            # Try to convert to dict if it has dict-like behavior
            if hasattr(obj, 'items') and callable(obj.items):
                result = {}
                for key, value in obj.items():
                    result[key] = _convert_protobuf_to_dict(value)
                return result
            # Try to convert to list if it has list-like behavior
            elif hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes)):
                return [_convert_protobuf_to_dict(item) for item in obj]
            else:
                # Last resort: return string representation
                return str(obj)
        except Exception as e:
            logger.warning(f"Failed to convert protobuf-like object: {e}")
            return str(obj)
    
    # For everything else, test if it's JSON serializable
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        # Fall back to string representation
        return str(obj)


class MessageConverter:
    """Convert messages between different LLM provider formats."""
    
    @staticmethod
    def convert_to_anthropic_format(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert OpenAI format messages to Anthropic format.
        
        Args:
            messages: List of messages in OpenAI format
            
        Returns:
            List of messages in Anthropic format
        """
        anthropic_messages = []
        
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            
            if role == "system":
                # Skip system messages here - they're passed separately in Anthropic
                continue
            elif role == "user":
                anthropic_messages.append({
                    "role": "user",
                    "content": content or ""
                })
            elif role == "assistant":
                # Assistant messages may have content and/or tool_calls
                assistant_content = []
                
                # Add text content if present
                if content:
                    assistant_content.append({
                        "type": "text",
                        "text": content
                    })
                
                # Convert tool calls to Anthropic format
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        function = tc.get("function", {})
                        # Parse arguments string to dict if needed
                        arguments = function.get("arguments", "{}")
                        if isinstance(arguments, str):
                            try:
                                arguments = json.loads(arguments)
                            except json.JSONDecodeError:
                                logger.warning(f"Failed to parse tool call arguments: {arguments}")
                                arguments = {}
                        elif isinstance(arguments, dict):
                            # Arguments are already a dictionary, use as-is
                            pass
                        else:
                            logger.warning(f"Unexpected arguments type: {type(arguments)}, using empty dict")
                            arguments = {}
                        
                        assistant_content.append({
                            "type": "tool_use",
                            "id": tc.get("id", str(uuid.uuid4())),
                            "name": function.get("name", ""),
                            "input": arguments
                        })
                
                if assistant_content:
                    anthropic_messages.append({
                        "role": "assistant",
                        "content": assistant_content
                    })
            elif role == "tool":
                # Tool results become user messages in Anthropic format
                tool_call_id = msg.get("tool_call_id")
                anthropic_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": content or ""
                        }
                    ]
                })
        
        return anthropic_messages
    
    @staticmethod
    def convert_to_gemini_format(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert OpenAI format messages to Gemini format.
        
        Args:
            messages: List of messages in OpenAI format
            
        Returns:
            List of messages in Gemini format
        """
        gemini_history = []
        tool_id_to_name_map = {}
        
        # First pass: identify assistant tool calls and build ID-to-name mapping
        for msg in messages:
            if msg["role"] == "assistant" and "tool_calls" in msg:
                for tc in msg.get("tool_calls", []):
                    if tc.get("id") and tc.get("function", {}).get("name"):
                        tool_id_to_name_map[tc["id"]] = tc["function"]["name"]

        # Second pass: convert messages to Gemini format
        for msg in messages:
            role = msg["role"]
            content = msg.get("content")
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id")
            
            # Convert OpenAI roles to Gemini roles
            if role == "user":
                gemini_role = "user"
            elif role == "assistant":
                gemini_role = "model"
            elif role == "tool":
                gemini_role = "function"
            else:
                logger.warning(f"Unknown role in message: {role}, defaulting to user")
                gemini_role = "user"
            
            # For regular text messages
            if role in ["user", "assistant"] and content:
                gemini_history.append({"role": gemini_role, "parts": [content]})

            # Handle Assistant requesting tool calls
            if role == "assistant" and tool_calls:
                parts = []
                if content:
                    parts.append(content)
                
                # Create function call parts
                for tc in tool_calls:
                    function_call = tc.get("function")
                    if function_call:
                        try:
                            args = json.loads(function_call.get("arguments", "{}"))
                        except json.JSONDecodeError:
                            args = {}
                            logger.warning(f"Failed to parse arguments for function {function_call.get('name')}")
                        
                        fc_dict = {
                            "function_call": {
                                "name": function_call.get("name"),
                                "args": args
                            }
                        }
                        parts.append(fc_dict)
                
                if parts:
                    gemini_history.append({"role": gemini_role, "parts": parts})
            
            # Handle Tool execution results
            elif role == "tool" and tool_call_id:
                function_name = tool_id_to_name_map.get(tool_call_id)
                
                if not function_name:
                    logger.warning(f"Could not find function name for tool_call_id: {tool_call_id}")
                    function_name = f"unknown_function_{tool_call_id[-6:]}"
                
                # Parse the content
                try:
                    if isinstance(content, str):
                        if content.strip().startswith(("{", "[")):
                            result_content = json.loads(content)
                        else:
                            result_content = {"result": content}
                    else:
                        result_content = {"result": str(content)}
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse tool result as JSON: {content[:100]}...")
                    result_content = {"result": content}
                
                # Convert lists to dictionary structure for Gemini
                if isinstance(result_content, list):
                    result_content = {"results": result_content}
                
                gemini_history.append({
                    "role": "function",
                    "parts": [{
                        "function_response": {
                            "name": function_name,
                            "response": result_content
                        }
                    }]
                })
        
        return gemini_history
    
    @staticmethod
    def convert_anthropic_response_to_openai_format(response_content: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Convert Anthropic response back to OpenAI-compatible format.
        
        Args:
            response_content: Anthropic response content list
            
        Returns:
            Dictionary with content and tool_calls in OpenAI format
        """
        content = ""
        tool_calls = []
        
        for content_block in response_content:
            if hasattr(content_block, 'type'):
                if content_block.type == "text":
                    content += getattr(content_block, 'text', '')
                elif content_block.type == "tool_use":
                    # Convert to OpenAI format
                    tool_call = {
                        "id": getattr(content_block, 'id', str(uuid.uuid4())),
                        "type": "function",
                        "function": {
                            "name": getattr(content_block, 'name', ''),
                            "arguments": json.dumps(getattr(content_block, 'input', {}))
                        }
                    }
                    tool_calls.append(tool_call)
        
        return {
            "content": content or None,
            "tool_calls": tool_calls if tool_calls else None
        }
    
    @staticmethod
    def convert_gemini_response_to_openai_format(response_parts: List[Any]) -> Dict[str, Any]:
        """
        Convert Gemini response back to OpenAI-compatible format.
        
        Args:
            response_parts: Gemini response parts
            
        Returns:
            Dictionary with content and tool_calls in OpenAI format
        """
        content = None
        tool_calls = []
        
        for part in response_parts:
            if hasattr(part, 'text') and part.text:
                content = part.text
            
            if hasattr(part, 'function_call') and part.function_call:
                fc = part.function_call
                
                # Convert Gemini FunctionCall to OpenAI format
                try:
                    if hasattr(fc, 'args'):
                        # First, use our robust protobuf converter to handle all protobuf objects
                        args = _convert_protobuf_to_dict(fc.args)
                        
                        # If that didn't work, fall back to the detailed manual conversion
                        if isinstance(args, str) and args.startswith('<'):
                            # The protobuf converter failed, try manual approach
                            if hasattr(fc.args, 'DESCRIPTOR'):
                                # It's a protobuf message, use proper conversion
                                if MessageToDict:
                                    args = MessageToDict(fc.args, preserving_proto_field_name=True)
                                else:
                                    # Manual conversion for the specific structure we see
                                    args = {}
                                    if hasattr(fc.args, 'fields'):
                                        for field in fc.args.fields:
                                            key = field.key
                                            value = field.value
                                            if hasattr(value, 'string_value'):
                                                args[key] = value.string_value
                                            elif hasattr(value, 'list_value') and hasattr(value.list_value, 'values'):
                                                # Handle list values (like nifi_objects)
                                                list_values = []
                                                for list_item in value.list_value.values:
                                                    if hasattr(list_item, 'struct_value') and hasattr(list_item.struct_value, 'fields'):
                                                        struct_dict = {}
                                                        for struct_field in list_item.struct_value.fields:
                                                            struct_key = struct_field.key
                                                            struct_value = struct_field.value
                                                            if hasattr(struct_value, 'string_value'):
                                                                struct_dict[struct_key] = struct_value.string_value
                                                            elif hasattr(struct_value, 'struct_value'):
                                                                # Handle nested structs (like properties)
                                                                nested_dict = {}
                                                                if hasattr(struct_value.struct_value, 'fields'):
                                                                    for nested_field in struct_value.struct_value.fields:
                                                                        nested_key = nested_field.key
                                                                        nested_value = nested_field.value
                                                                        if hasattr(nested_value, 'string_value'):
                                                                            nested_dict[nested_key] = nested_value.string_value
                                                                struct_dict[struct_key] = nested_dict
                                                        list_values.append(struct_dict)
                                                args[key] = list_values
                                            elif hasattr(value, 'int_value'):
                                                args[key] = value.int_value
                                            elif hasattr(value, 'bool_value'):
                                                args[key] = value.bool_value
                            elif hasattr(fc.args, 'items') and callable(fc.args.items):
                                # Convert protobuf MapComposite to dict
                                args = dict(fc.args.items())
                                # Recursively convert any nested protobuf objects
                                args = _convert_protobuf_to_dict(args)
                            elif isinstance(fc.args, dict):
                                args = _convert_protobuf_to_dict(fc.args)
                            else:
                                logger.warning(f"Could not convert function call args of type {type(fc.args)}: {fc.args}")
                                args = {"raw_args": str(fc.args)}
                    else:
                        args = {}
                    
                    # Convert the args to JSON string with better error handling
                    try:
                        # First ensure all values are JSON-serializable
                        serializable_args = _convert_protobuf_to_dict(args)
                        args_str = json.dumps(serializable_args)
                    except (TypeError, ValueError) as json_error:
                        logger.error(f"JSON serialization failed even after protobuf conversion: {json_error}")
                        logger.debug(f"Problematic args: {args}")
                        # Ultimate fallback
                        args_str = "{}"
                        
                except Exception as e:
                    logger.error(f"Error converting function call args: {e}")
                    args_str = "{}"
                
                tool_calls.append({
                    "id": str(uuid.uuid4()),
                    "type": "function",
                    "function": {
                        "name": fc.name,
                        "arguments": args_str
                    }
                })
        
        return {
            "content": content,
            "tool_calls": tool_calls if tool_calls else None
        } 