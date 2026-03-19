"""
Message conversion utilities for different LLM providers.

This module handles conversion between different message formats used by
various LLM providers (OpenAI, Anthropic, Gemini, etc.).
"""

import json
import uuid
from typing import List, Dict, Any, Optional
from loguru import logger


def _convert_protobuf_to_dict(obj):
    """
    Convert protobuf objects to Python dictionaries/lists for JSON serialization.
    
    Handles MapComposite, RepeatedComposite, and other protobuf types that 
    Gemini generates in function call arguments.
    """
    # Handle MapComposite objects (like dictionaries)
    if hasattr(obj, 'items') and callable(obj.items):
        return {str(key): _convert_protobuf_to_dict(value) for key, value in obj.items()}
    
    # Handle RepeatedComposite objects (like lists/arrays)
    elif hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes, dict)):
        try:
            return [_convert_protobuf_to_dict(item) for item in obj]
        except TypeError:
            # If iteration fails, convert to string
            return str(obj)
    
    # Handle regular dictionaries
    elif isinstance(obj, dict):
        return {str(key): _convert_protobuf_to_dict(value) for key, value in obj.items()}
    
    # Handle regular lists
    elif isinstance(obj, list):
        return [_convert_protobuf_to_dict(item) for item in obj]
    
    # Handle primitive types (int, str, bool, float, None)
    elif obj is None or isinstance(obj, (int, str, bool, float)):
        return obj
    
    # Handle protobuf enums and other complex objects
    elif hasattr(obj, 'name'):
        return str(obj.name)
    elif hasattr(obj, 'value'):
        return obj.value
    
    # Fallback: convert to string
    else:
        logger.debug(f"Converting unknown protobuf type {type(obj)} to string: {obj}")
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
                if isinstance(content, str):
                    # If it's a string, try to parse as JSON
                    try:
                        if content.strip().startswith(("{", "[")):
                            result_content = json.loads(content)
                        else:
                            result_content = {"result": content}
                    except json.JSONDecodeError:
                        # If JSON parsing fails, treat as plain text
                        result_content = {"result": content}
                elif isinstance(content, (dict, list)):
                    # If it's already a Python object, use it directly
                    result_content = content
                else:
                    # For other types, convert to string
                    result_content = {"result": str(content)}
                
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
        content_parts = []
        tool_calls = []
        
        logger.debug(f"Converting {len(response_parts)} Gemini response parts to OpenAI format")
        
        for i, part in enumerate(response_parts):
            # Debug logging to see what we're getting
            part_type = type(part).__name__
            part_attrs = [attr for attr in dir(part) if not attr.startswith('_')]
            logger.debug(f"Part {i}: type={part_type}, attributes={part_attrs}")
            
            # Check for text content
            if hasattr(part, 'text') and part.text:
                text_content = part.text
                logger.debug(f"Found text content: {text_content[:100]}...")
                
                # Check for repeated content within the same text part
                # This can happen when Gemini generates repeated responses
                if text_content:
                    # Simple heuristic: if the text contains the same sentence repeated multiple times,
                    # try to extract just one instance
                    lines = text_content.split('\n')
                    if len(lines) > 1:
                        # Check if we have repeated lines
                        unique_lines = []
                        seen_lines = set()
                        for line in lines:
                            line_stripped = line.strip()
                            if line_stripped and line_stripped not in seen_lines:
                                unique_lines.append(line)
                                seen_lines.add(line_stripped)
                            elif line_stripped:
                                # Found repeated line - log it
                                logger.warning(f"Detected repeated line in Gemini response: {line_stripped[:100]}...")
                        
                        # If we found repeated lines, use the deduplicated version
                        if len(unique_lines) < len(lines):
                            text_content = '\n'.join(unique_lines)
                            logger.info(f"Deduplicated response content: {len(lines)} lines -> {len(unique_lines)} lines")
                    
                    # Check for repeated sentences within the text
                    sentences = text_content.split('. ')
                    if len(sentences) > 1:
                        unique_sentences = []
                        seen_sentences = set()
                        for sentence in sentences:
                            sentence_stripped = sentence.strip().rstrip('.')
                            if sentence_stripped and sentence_stripped not in seen_sentences:
                                unique_sentences.append(sentence)
                                seen_sentences.add(sentence_stripped)
                            elif sentence_stripped:
                                logger.warning(f"Detected repeated sentence in Gemini response: {sentence_stripped[:100]}...")
                        
                        # If we found repeated sentences, use the deduplicated version
                        if len(unique_sentences) < len(sentences):
                            text_content = '. '.join(unique_sentences)
                            logger.info(f"Deduplicated response content: {len(sentences)} sentences -> {len(unique_sentences)} sentences")
                    
                    content_parts.append(text_content)
            
            # Check for function calls
            if hasattr(part, 'function_call') and part.function_call:
                fc = part.function_call
                logger.debug(f"Found function call: {fc.name}")
                
                # Convert Gemini FunctionCall to OpenAI format
                try:
                    if hasattr(fc, 'args'):
                        # Handle protobuf objects (MapComposite, RepeatedComposite, etc.)
                        args = _convert_protobuf_to_dict(fc.args)
                    else:
                        args = {}
                    
                    args_str = json.dumps(args)
                    logger.debug(f"Function call args: {args_str}")
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
            else:
                # Log what attributes the part has if it doesn't have function_call
                if hasattr(part, 'function_call'):
                    logger.debug(f"Part {i} has function_call attribute but it's {part.function_call}")
                else:
                    logger.debug(f"Part {i} does not have function_call attribute")
        
        # Combine all content parts
        combined_content = '\n'.join(content_parts) if content_parts else None
        
        # Additional check for repeated content across combined parts
        if combined_content and len(content_parts) > 1:
            # Check if all parts are identical (another form of repetition)
            if all(part == content_parts[0] for part in content_parts):
                combined_content = content_parts[0]
                logger.info(f"Detected identical content parts, using single instance instead of {len(content_parts)} repetitions")
        
        logger.debug(f"Converted to: content={combined_content is not None}, tool_calls={len(tool_calls)}")
        
        return {
            "content": combined_content,
            "tool_calls": tool_calls if tool_calls else None
        } 