"""
Gemini LLM Provider implementation using Google ADK.

This module implements the LLMProvider interface for Gemini using Google's Agent Development Kit (ADK),
which provides built-in MCP support and eliminates manual schema conversion issues.
"""

from typing import List, Dict, Any, Optional
import json
from loguru import logger

from ..base import LLMProvider, LLMResponse
from ..utils.token_counter import TokenCounter
from ..utils.error_handler import LLMErrorHandler

# Import Google ADK components
try:
    from google.adk.agents import LlmAgent
    from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StdioServerParameters
    from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StdioServerParameters
    ADK_AVAILABLE = True
except ImportError:
    ADK_AVAILABLE = False
    logger.warning("Google ADK not available, falling back to manual implementation")


class GeminiClient(LLMProvider):
    """Gemini LLM Provider implementation using Google ADK for MCP integration."""
    
    def __init__(self, config: Dict[str, Any]):
        if "gemini" in config and isinstance(config["gemini"], dict):
            gemini_config = config["gemini"]
            api_key = gemini_config.get("api_key")
            self.available_models = gemini_config.get("models", ["gemini-1.5-pro", "gemini-1.5-flash"])
        else:
            api_key = config.get("GOOGLE_API_KEY")
            self.available_models = config.get("GEMINI_MODELS", ["gemini-1.5-pro", "gemini-1.5-flash"])
        super().__init__(api_key)
        
        if not api_key:
            raise ValueError("GOOGLE_API_KEY is required for Gemini")
        
        self.token_counter = TokenCounter()
        self.logger = logger.bind(provider="Gemini")
        
        # Initialize ADK agent if available
        self.adk_agent = None
        if ADK_AVAILABLE:
            self._initialize_adk_agent(config)
    
    def _initialize_adk_agent(self, config: Dict[str, Any]):
        """Initialize the ADK agent with MCP toolset."""
        try:
            # Note: Our MCP server is HTTP-based (FastAPI), not stdio-based
            # ADK's MCPToolset expects stdio servers, so we'll use the manual implementation
            # for now and let ADK handle the Gemini model integration
            self.logger.info("MCP server is HTTP-based, using manual implementation with ADK Gemini model")
            
            # For now, we'll use the manual implementation but with ADK's model
            # This gives us the benefits of ADK's model handling while working with our HTTP MCP server
            self.adk_agent = None
            
        except Exception as e:
            self.logger.error(f"Failed to initialize ADK agent: {e}")
            self.adk_agent = None
    
    def send_message(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        model_name: str,
        tools: Optional[List[Any]] = None,
        user_request_id: Optional[str] = None,
        action_id: Optional[str] = None
    ) -> LLMResponse:
        """Send message to Gemini using ADK for proper MCP integration."""
        bound_logger = self.logger.bind(user_request_id=user_request_id, action_id=action_id)
        
        # LLM Debug Logging - Log the request being sent to Gemini
        llm_request_data = {
            "provider": "gemini",
            "model": model_name,
            "messages": messages,
            "tools": tools
        }
        self.logger.bind(interface="llm", direction="request", data=llm_request_data, user_request_id=user_request_id).debug("Calling Gemini LLM")
        
        try:
            if self.adk_agent and ADK_AVAILABLE:
                response = self._send_with_adk(messages, system_prompt, model_name, bound_logger)
            else:
                # Fallback to manual implementation if ADK is not available
                response = self._send_with_manual_implementation(messages, system_prompt, model_name, tools, bound_logger)
            
            # LLM Debug Logging - Log the response received from Gemini
            llm_response_data = {
                "content_length": len(response.content) if response.content else 0,
                "tool_calls_count": len(response.tool_calls) if response.tool_calls else 0,
                "token_count_in": response.token_count_in,
                "token_count_out": response.token_count_out,
                "error": response.error,
                "full_response": response.content
            }
            self.logger.bind(interface="llm", direction="response", data=llm_response_data, user_request_id=user_request_id).debug("llm-response")
            
            return response
                
        except Exception as e:
            bound_logger.error(f"Gemini API error: {e}", exc_info=True)
            # LLM Debug Logging - Log the error response
            llm_error_data = {
                "error": str(e),
                "token_count_in": 0,
                "token_count_out": 0
            }
            self.logger.bind(interface="llm", direction="response", data=llm_error_data, user_request_id=user_request_id).debug("Received error from Gemini LLM")
            
            error_message = LLMErrorHandler.handle_error(e, "Gemini")
            return LLMResponse(
                content=None,
                tool_calls=None,
                token_count_in=0,
                token_count_out=0,
                error=error_message
            )
    
    def _send_with_adk(self, messages: List[Dict[str, Any]], system_prompt: str, model_name: str, bound_logger) -> LLMResponse:
        """Send message using Google ADK for proper MCP integration."""
        try:
            # Convert messages to ADK format
            adk_messages = self._convert_messages_to_adk_format(messages)
            
            # Update agent instruction with system prompt
            self.adk_agent.instruction = system_prompt
            
            # Run the agent
            bound_logger.info(f"Sending message to ADK agent with model: {model_name}")
            response = self.adk_agent.run(adk_messages)
            
            # Parse ADK response
            content = response.content if hasattr(response, 'content') else None
            tool_calls = self._extract_tool_calls_from_adk_response(response)
            
            # Calculate token counts
            token_count_in = self.token_counter.calculate_input_tokens(
                messages, "gemini", model_name, tools=None
            )
            token_count_out = self.token_counter.count_tokens_gemini(content or "")
            
            return LLMResponse(
                content=content,
                tool_calls=tool_calls,
                token_count_in=token_count_in,
                token_count_out=token_count_out
            )
            
        except Exception as e:
            bound_logger.error(f"ADK execution error: {e}", exc_info=True)
            raise
    
    def _send_with_manual_implementation(self, messages: List[Dict[str, Any]], system_prompt: str, model_name: str, tools: Optional[List[Any]], bound_logger) -> LLMResponse:
        """Fallback to manual implementation with enhanced error diagnostics."""
        bound_logger.warning("Using manual Gemini implementation (ADK not available)")
        
        # Import here to avoid dependency issues
        import google.generativeai as genai
        from ..utils.message_converter import MessageConverter
        
        # Configure Gemini
        genai.configure(api_key=self.api_key)
        
        # Convert messages to Gemini format
        gemini_history = MessageConverter.convert_to_gemini_format(messages)
        
        # Create model instance
        model_instance = genai.GenerativeModel(
            model_name,
            system_instruction=system_prompt
        )
        
        # Calculate input tokens for both success and error cases
        token_count_in = self.token_counter.calculate_input_tokens(
            messages, "gemini", model_name, tools
        )
        
        # Initialize function_declarations in broader scope for error handling
        function_declarations = None
        
        try:
            # Enhanced debugging for tools
            if tools:
                bound_logger.debug(f"Received {len(tools)} raw tools from MCP for formatting")
                for i, tool in enumerate(tools[:3]):  # Log first 3 tools
                    # Extract tool name from MCP format
                    tool_name = tool.get("function", {}).get("name", "unknown") if isinstance(tool, dict) else getattr(tool, 'name', 'unknown')
                    bound_logger.debug(f"Raw tool {i+1}: {tool_name}")
            else:
                bound_logger.debug("No tools being sent to Gemini model")
            
            # Generate content with enhanced error handling
            # For Gemini, tools need to be formatted and wrapped in a list of Tool objects
            formatted_tools = None
            if tools:
                # Format tools using the ToolFormatter first
                function_declarations = self.format_tools(tools)
                formatted_tools = [genai.types.Tool(function_declarations=function_declarations)]
                bound_logger.debug(f"Formatted and wrapped {len(function_declarations)} function declarations in Tool object")
            
            # LLM Debug Logging - Log the request being sent to Gemini
            llm_request_data = {
                "provider": "gemini",
                "model": model_name,
                "messages": messages,
                "tools": [{"name": getattr(t, 'name', 'unknown'), "description": getattr(t, 'description', 'N/A')} for t in function_declarations] if function_declarations else None
            }
            bound_logger.bind(interface="llm", direction="request", data=llm_request_data).debug("Calling Gemini LLM")
            
            # Create generation config with appropriate settings
            generation_config = genai.types.GenerationConfig(
                temperature=0.3,
            )
            
            # Enable function calling if tools are provided
            if formatted_tools:
                # Some models support explicit function calling mode
                try:
                    generation_config.function_calling_mode = "AUTO"
                    bound_logger.debug("Set function calling mode to AUTO")
                except:
                    # If function_calling_mode is not supported, continue without it
                    bound_logger.debug("Function calling mode not supported, continuing without it")
            
            response = model_instance.generate_content(
                gemini_history,
                tools=formatted_tools,
                generation_config=generation_config
            )
            
            # Debug log the raw response structure
            bound_logger.debug(f"Raw response type: {type(response)}")
            if hasattr(response, 'candidates'):
                bound_logger.debug(f"Response has {len(response.candidates)} candidates")
                for i, candidate in enumerate(response.candidates):
                    if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                        bound_logger.debug(f"Candidate {i} has {len(candidate.content.parts)} parts")
            else:
                bound_logger.debug("Response has no candidates attribute")
            
            # Enhanced response validation and parsing
            parsed_response = self._parse_gemini_response_with_diagnostics(
                response, tools, token_count_in, bound_logger, function_declarations
            )
            
            # LLM Debug Logging - Log the response received from Gemini
            llm_response_data = {
                "content_length": len(parsed_response.content) if parsed_response.content else 0,
                "tool_calls_count": len(parsed_response.tool_calls) if parsed_response.tool_calls else 0,
                "token_count_in": parsed_response.token_count_in,
                "token_count_out": parsed_response.token_count_out,
                "error": parsed_response.error,
                "full_response": parsed_response.content if parsed_response.content else None
            }
            bound_logger.bind(interface="llm", direction="response", data=llm_response_data).debug("llm-response")
            
            return parsed_response
            
        except Exception as e:
            bound_logger.error(f"Gemini API call failed: {e}", exc_info=True)
            
            # LLM Debug Logging - Log the error response
            llm_error_data = {
                "error": str(e),
                "token_count_in": token_count_in,
                "token_count_out": 0
            }
            bound_logger.bind(interface="llm", direction="response", data=llm_error_data).debug("Received error from Gemini LLM")
            
            return LLMResponse(
                content=None,
                tool_calls=None,
                token_count_in=token_count_in,
                token_count_out=0,
                error=f"Gemini API error: {str(e)}"
            )
    
    def _convert_messages_to_adk_format(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert OpenAI format messages to ADK format."""
        adk_messages = []
        
        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")
            
            # Convert roles
            if role == "user":
                adk_role = "user"
            elif role == "assistant":
                adk_role = "assistant"
            elif role == "tool":
                adk_role = "tool"
            else:
                adk_role = "user"
            
            adk_messages.append({
                "role": adk_role,
                "content": content
            })
        
        return adk_messages
    
    def _extract_tool_calls_from_adk_response(self, response) -> Optional[List[Dict[str, Any]]]:
        """Extract tool calls from ADK response."""
        tool_calls = []
        
        try:
            # ADK response structure may vary, try different approaches
            if hasattr(response, 'tool_calls'):
                for tool_call in response.tool_calls:
                    tool_calls.append({
                        "id": str(tool_call.get("id", "")),
                        "type": "function",
                        "function": {
                            "name": tool_call.get("name", ""),
                            "arguments": json.dumps(tool_call.get("arguments", {}))
                        }
                    })
            elif hasattr(response, 'content') and isinstance(response.content, str):
                # Try to parse tool calls from content if they're embedded
                # This is a fallback for when ADK doesn't provide structured tool calls
                pass
                
        except Exception as e:
            self.logger.warning(f"Failed to extract tool calls from ADK response: {e}")
        
        return tool_calls if tool_calls else None
    
    def _parse_gemini_response_with_diagnostics(self, response, tools, token_count_in, bound_logger, function_declarations=None) -> LLMResponse:
        """
        Parse Gemini response with comprehensive error diagnostics.
        
        This method provides detailed analysis when Gemini encounters issues,
        especially MALFORMED_FUNCTION_CALL errors that indicate tool schema problems.
        """
        from ..utils.message_converter import MessageConverter
        import uuid
        
        # Check response structure before processing
        if not hasattr(response, 'parts'):
            bound_logger.error(f"Unexpected Gemini response structure - missing 'parts' attribute. Response type: {type(response)}")
            
            # Check for candidates and extract parts from first candidate if available
            if hasattr(response, 'candidates'):
                bound_logger.debug(f"Response has {len(response.candidates)} candidates")
                
                if response.candidates and hasattr(response.candidates[0], 'content'):
                    candidate_content = response.candidates[0].content
                    if hasattr(candidate_content, 'parts'):
                        bound_logger.debug(f"Using parts from first candidate instead")
                        response_parts = candidate_content.parts
                    else:
                        bound_logger.error("Candidate content doesn't have parts either")
                        response_parts = []
                else:
                    bound_logger.error("No usable content in candidates")
                    response_parts = []
            else:
                bound_logger.error("No candidates available in response")
                response_parts = []
        else:
            response_parts = response.parts
        
        # Parse the response parts with error handling
        if response_parts:
            bound_logger.debug(f"Processing {len(response_parts)} parts from response")
            
            # Parse using existing converter
            parsed_response = MessageConverter.convert_gemini_response_to_openai_format(response_parts)
            
            # Calculate output tokens
            token_count_out = self.token_counter.count_tokens_gemini(
                parsed_response.get("content", "") or ""
            )
            
            return LLMResponse(
                content=parsed_response.get("content"),
                tool_calls=parsed_response.get("tool_calls"),
                token_count_in=token_count_in,
                token_count_out=token_count_out
            )
        else:
            # ENHANCED ERROR DETECTION - No parts found is a critical error
            bound_logger.error("CRITICAL: No parts found in Gemini response - investigating cause...")
            
            # Check for finish_reason indicating why response failed
            finish_reasons = []
            malformed_function_call_detected = False
            
            if hasattr(response, 'candidates') and response.candidates:
                for i, candidate in enumerate(response.candidates):
                    if hasattr(candidate, 'finish_reason'):
                        finish_reason = str(candidate.finish_reason)
                        finish_reasons.append(f"Candidate {i}: {finish_reason}")
                        
                        if 'MALFORMED_FUNCTION_CALL' in finish_reason:
                            malformed_function_call_detected = True
                            bound_logger.error(f"MALFORMED_FUNCTION_CALL detected in candidate {i}")
                        elif 'SAFETY' in finish_reason:
                            bound_logger.error(f"Safety filter triggered in candidate {i}")
                        elif 'MAX_TOKENS' in finish_reason:
                            bound_logger.error(f"Max tokens exceeded in candidate {i}")
                        elif 'STOP' in finish_reason:
                            bound_logger.debug(f"Normal stop condition in candidate {i}")
                        else:
                            bound_logger.error(f"Unknown finish reason: {finish_reason}")
            
            # Log comprehensive diagnostic information
            diagnostic_info = {
                "response_type": str(type(response)),
                "finish_reasons": finish_reasons,
                "malformed_function_call": malformed_function_call_detected,
                "has_candidates": hasattr(response, 'candidates'),
                "candidate_count": len(response.candidates) if hasattr(response, 'candidates') and response.candidates else 0,
                "tools_provided": len(tools) if tools else 0,
                "tools_names": [getattr(t, 'name', 'unknown') for t in tools] if tools else [],
            }
            
            bound_logger.bind(diagnostic_data=diagnostic_info).error("Gemini response diagnostic information")
            
            # Specific error handling based on detected issues
            if malformed_function_call_detected:
                error_msg = "MALFORMED_FUNCTION_CALL: Gemini could not parse the function call arguments. This likely indicates a tool schema issue."
                bound_logger.error(error_msg)
                
                if tools:
                    # Use function_declarations since tools at this point are FunctionDeclaration objects  
                    if function_declarations:
                        tool_names = [getattr(t, 'name', 'unknown') for t in function_declarations]
                        bound_logger.error(f"Problem likely with one of these tools: {tool_names}")
                        
                        # Enhanced tool schema analysis
                        self._analyze_tool_schemas_for_issues(function_declarations, bound_logger)
                    else:
                        bound_logger.error("Problem with tools but function_declarations not available for analysis")
                
                return LLMResponse(
                    content=None,
                    tool_calls=None,
                    token_count_in=token_count_in,
                    token_count_out=0,
                    error="Function call schema error: Gemini detected malformed function call arguments. Please check tool parameter schemas."
                )
            
            elif finish_reasons and any('SAFETY' in reason for reason in finish_reasons):
                bound_logger.error("SAFETY: Gemini's safety filters blocked the response.")
                return LLMResponse(
                    content=None,
                    tool_calls=None,
                    token_count_in=token_count_in,
                    token_count_out=0,
                    error="Safety filter triggered: Gemini blocked the response due to safety concerns."
                )
            
            elif finish_reasons and any('MAX_TOKENS' in reason for reason in finish_reasons):
                bound_logger.error("MAX_TOKENS: Gemini response was truncated due to token limit.")
                return LLMResponse(
                    content=None,
                    tool_calls=None,
                    token_count_in=token_count_in,
                    token_count_out=0,
                    error="Token limit exceeded: Gemini response was truncated."
                )
            
            else:
                # Check if this is a context-related empty response
                if token_count_in > 8000:  # High token count suggests context overflow
                    error_msg = f"CONTEXT_OVERFLOW: Gemini returned empty response likely due to excessive context ({token_count_in} tokens). Finish reasons: {finish_reasons}"
                    bound_logger.error(error_msg)
                    return LLMResponse(
                        content=None,
                        tool_calls=None,
                        token_count_in=token_count_in,
                        token_count_out=0,
                        error=f"Empty response from Gemini: {error_msg}"
                    )
                else:
                    error_msg = f"UNKNOWN: Gemini returned empty response for unknown reasons. Finish reasons: {finish_reasons}"
                    bound_logger.error(error_msg)
                    return LLMResponse(
                        content=None,
                        tool_calls=None,
                        token_count_in=token_count_in,
                        token_count_out=0,
                        error=f"Empty response from Gemini: {error_msg}"
                    )
    
    def _analyze_tool_schemas_for_issues(self, tools, bound_logger):
        """
        Analyze tool schemas to identify potential issues that could cause MALFORMED_FUNCTION_CALL.
        
        This method examines tool parameter schemas for common problems that cause Gemini
        to fail when generating function calls.
        """
        for i, tool in enumerate(tools):
            tool_name = getattr(tool, 'name', 'unknown')
            tool_params = getattr(tool, 'parameters', None)
            
            bound_logger.error(f"Tool {i+1}/{len(tools)} '{tool_name}' schema: {str(tool_params)[:500]}...")
            
            if tool_params:
                schema_issues = []
                
                # Handle protobuf objects properly - they don't have .get() method
                # Convert to dict-like access for analysis
                try:
                    # Check for empty or missing required fields
                    properties = None
                    if hasattr(tool_params, 'properties'):
                        properties = tool_params.properties
                    elif isinstance(tool_params, dict):
                        properties = tool_params.get('properties')
                    
                    if not properties:
                        schema_issues.append("No properties defined")
                    else:
                        # Check for complex nested objects that Gemini might struggle with
                        if isinstance(properties, dict):
                            # Regular dict access
                            for prop_name, prop_def in properties.items():
                                if isinstance(prop_def, dict):
                                    prop_type = prop_def.get('type_') or prop_def.get('type')
                                    
                                    if prop_type == 'OBJECT' and not prop_def.get('properties'):
                                        schema_issues.append(f"Property '{prop_name}' is OBJECT type but lacks properties definition")
                                    elif prop_type == 'ARRAY':
                                        items = prop_def.get('items', {})
                                        if isinstance(items, dict) and (items.get('type_') == 'OBJECT' or items.get('type') == 'object') and not items.get('properties'):
                                            schema_issues.append(f"Property '{prop_name}' array items lack properties definition")
                                    
                                    # Check for missing required type field
                                    if not prop_type:
                                        schema_issues.append(f"Property '{prop_name}' missing type definition")
                        else:
                            # Protobuf-like object - safer to just note it exists
                            bound_logger.debug(f"Tool '{tool_name}' has protobuf-style properties object")
                    
                except Exception as e:
                    bound_logger.error(f"Error analyzing schema for tool '{tool_name}': {e}")
                    schema_issues.append(f"Schema analysis failed: {e}")
                
                if schema_issues:
                    bound_logger.error(f"Potential schema issues in '{tool_name}': {', '.join(schema_issues)}")
                else:
                    bound_logger.debug(f"Tool '{tool_name}' schema appears valid")
            else:
                bound_logger.error(f"Tool '{tool_name}' has no parameters schema!")
    
    def format_tools(self, tools: List[Dict[str, Any]]) -> Any:
        """Format tools for Gemini using ToolFormatter."""
        from ..mcp.tool_formatter import ToolFormatter
        return ToolFormatter.format_tools_for_provider(tools, "gemini")
    
    def is_configured(self) -> bool:
        """Check if Gemini is properly configured."""
        return bool(self.api_key)
    
    def get_available_models(self) -> List[str]:
        """Get list of available Gemini models."""
        return self.available_models
    
    def supports_tools(self) -> bool:
        """Check if this provider supports function calling/tools."""
        # We support tools both via ADK and manual implementation
        return True 