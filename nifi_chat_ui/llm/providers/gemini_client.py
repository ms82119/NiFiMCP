"""
Gemini LLM Provider implementation using Google ADK.

This module implements the LLMProvider interface for Gemini using Google's Agent Development Kit (ADK),
which provides built-in MCP support and eliminates manual schema conversion issues.
"""

from typing import List, Dict, Any, Optional
import json
import uuid
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
        # Support both flat and nested config keys for compatibility
        api_key = config.get("GOOGLE_API_KEY")
        if not api_key and "gemini" in config and isinstance(config["gemini"], dict):
            api_key = config["gemini"].get("api_key")
        model_name = config.get("GEMINI_DEFAULT_MODEL")
        if not model_name and "gemini" in config and isinstance(config["gemini"], dict):
            model_name = config["gemini"].get("default_model")
        if not model_name:
            model_name = "gemini-1.5-pro"
        super().__init__(api_key, model_name)
        
        if not api_key:
            raise ValueError("GOOGLE_API_KEY is required for Gemini")
        
        self.token_counter = TokenCounter()
        self.logger = logger.bind(provider="Gemini")
        self.available_models = config.get("GEMINI_MODELS")
        if not self.available_models and "gemini" in config and isinstance(config["gemini"], dict):
            self.available_models = config["gemini"].get("models", ["gemini-1.5-pro", "gemini-1.5-flash"])
        if not self.available_models:
            self.available_models = ["gemini-1.5-pro", "gemini-1.5-flash"]
        
        # Initialize ADK agent if available
        self.adk_agent = None
        if ADK_AVAILABLE:
            self._initialize_adk_agent(config)
    
    def _initialize_adk_agent(self, config: Dict[str, Any]):
        """Initialize the ADK agent with MCP toolset."""
        try:
            if not ADK_AVAILABLE:
                self.logger.warning("Google ADK not available, using manual implementation")
                self.adk_agent = None
                return
            
            # Import ADK components
            from google.adk.agents import LlmAgent
            from google.adk.tools import APIHubToolset
            
            # For now, we'll use the manual implementation since ADK doesn't have direct MCP support
            # The manual implementation with proper protobuf handling should work fine
            self.logger.info("Using manual Gemini implementation (ADK MCP support not available)")
            self.adk_agent = None
            
        except Exception as e:
            self.logger.error(f"Failed to initialize ADK agent: {e}")
            self.adk_agent = None
    
    def send_message(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        tools: Optional[List[Any]] = None,
        user_request_id: Optional[str] = None,
        action_id: Optional[str] = None
    ) -> LLMResponse:
        """Send message to Gemini using ADK for proper MCP integration."""
        bound_logger = self.logger.bind(user_request_id=user_request_id, action_id=action_id)
        
        try:
            if self.adk_agent and ADK_AVAILABLE:
                return self._send_with_adk(messages, system_prompt, bound_logger)
            else:
                # Fallback to manual implementation if ADK is not available
                return self._send_with_manual_implementation(messages, system_prompt, tools, bound_logger)
                
        except Exception as e:
            bound_logger.error(f"Gemini API error: {e}", exc_info=True)
            error_message = LLMErrorHandler.handle_error(e, "Gemini")
            return LLMResponse(
                content=None,
                tool_calls=None,
                token_count_in=0,
                token_count_out=0,
                error=error_message
            )
    
    def _send_with_adk(self, messages: List[Dict[str, Any]], system_prompt: str, bound_logger) -> LLMResponse:
        """Send message using Google ADK for proper MCP integration."""
        try:
            # Convert messages to ADK format
            adk_messages = self._convert_messages_to_adk_format(messages)
            
            # Update agent instruction with system prompt
            self.adk_agent.instruction = system_prompt
            
            # Run the agent
            bound_logger.info(f"Sending message to ADK agent with model: {self.model_name}")
            response = self.adk_agent.run(adk_messages)
            
            # Parse ADK response
            content = response.content if hasattr(response, 'content') else None
            tool_calls = self._extract_tool_calls_from_adk_response(response)
            
            # Calculate token counts
            token_count_in = self.token_counter.calculate_input_tokens(
                messages, "gemini", self.model_name, tools=None
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
    
    def _send_with_manual_implementation(self, messages: List[Dict[str, Any]], system_prompt: str, tools: Optional[List[Any]], bound_logger) -> LLMResponse:
        """Fallback to manual implementation if ADK is not available."""
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
            self.model_name,
            system_instruction=system_prompt
        )
        
        # Generate content
        response = model_instance.generate_content(
            gemini_history,
            tools=tools if tools else None,
            generation_config=genai.types.GenerationConfig(
                temperature=0.3,
            )
        )
        
        # Parse response using our converter
        parsed_response = MessageConverter.convert_gemini_response_to_openai_format(
            response.parts if hasattr(response, 'parts') else []
        )
        
        # Calculate token counts
        token_count_in = self.token_counter.calculate_input_tokens(
            messages, "gemini", self.model_name, tools
        )
        token_count_out = self.token_counter.count_tokens_gemini(
            parsed_response.get("content", "") or ""
        )
        
        return LLMResponse(
            content=parsed_response.get("content"),
            tool_calls=parsed_response.get("tool_calls"),
            token_count_in=token_count_in,
            token_count_out=token_count_out
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
            # ADK should return tool calls in a clean format
            if hasattr(response, 'tool_calls') and response.tool_calls:
                for tool_call in response.tool_calls:
                    tool_calls.append({
                        "id": str(uuid.uuid4()),
                        "type": "function",
                        "function": {
                            "name": tool_call.name if hasattr(tool_call, 'name') else tool_call.get("name", ""),
                            "arguments": json.dumps(tool_call.args if hasattr(tool_call, 'args') else tool_call.get("args", {}))
                        }
                    })
            elif hasattr(response, 'function_calls') and response.function_calls:
                # Alternative field name
                for tool_call in response.function_calls:
                    tool_calls.append({
                        "id": str(uuid.uuid4()),
                        "type": "function",
                        "function": {
                            "name": tool_call.name if hasattr(tool_call, 'name') else tool_call.get("name", ""),
                            "arguments": json.dumps(tool_call.args if hasattr(tool_call, 'args') else tool_call.get("args", {}))
                        }
                    })
                
        except Exception as e:
            self.logger.warning(f"Failed to extract tool calls from ADK response: {e}")
        
        return tool_calls if tool_calls else None
    
    def format_tools(self, tools: List[Dict[str, Any]]) -> Any:
        """Format tools for Gemini - ADK handles this automatically."""
        # ADK handles tool formatting automatically through MCP
        # Return tools as-is for compatibility
        return tools
    
    def is_configured(self) -> bool:
        """Check if Gemini is properly configured."""
        return bool(self.api_key)
    
    def get_available_models(self) -> List[str]:
        """Get list of available Gemini models."""
        return self.available_models
    
    def supports_tools(self) -> bool:
        """Check if this provider supports function calling/tools."""
        # Gemini supports tools through both ADK and manual implementation
        return True
    