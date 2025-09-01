"""
Anthropic LLM Provider implementation.

This module implements the LLMProvider interface for Anthropic using the Anthropic SDK.
"""

from typing import List, Dict, Any, Optional
import anthropic
import json
from loguru import logger
from ..base import LLMProvider, LLMResponse
from ..utils.token_counter import TokenCounter
from ..mcp.tool_formatter import ToolFormatter
from ..utils.message_converter import MessageConverter


class AnthropicClient(LLMProvider):
    """Anthropic LLM Provider implementation."""
    
    def __init__(self, config: Dict[str, Any]):
        if "anthropic" in config:
            anthropic_config = config["anthropic"]
            api_key = anthropic_config.get("api_key")
            self.available_models = anthropic_config.get("models", ["claude-3-opus-20240229", "claude-3-sonnet-20240229"])
        else:
            api_key = config.get("ANTHROPIC_API_KEY")
            self.available_models = config.get("ANTHROPIC_MODELS", ["claude-3-opus-20240229", "claude-3-sonnet-20240229"])
        super().__init__(api_key)
        self.client = anthropic.Anthropic(api_key=api_key)
        self.token_counter = TokenCounter()
        self.logger = logger.bind(provider="Anthropic")
    
    def send_message(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        model_name: str,
        tools: Optional[List[Any]] = None,
        user_request_id: Optional[str] = None,
        action_id: Optional[str] = None
    ) -> LLMResponse:
        # Convert messages to Anthropic format
        anthropic_messages = MessageConverter.convert_to_anthropic_format(messages)
        
        # Debug: Log the tools format being sent to Anthropic
        if tools:
            self.logger.debug(f"Sending tools to Anthropic: {tools[:2]}...")  # Log first 2 tools
            if len(tools) > 0:
                self.logger.debug(f"First tool structure: {tools[0]}")
        
        # LLM Debug Logging - Log the request being sent to Anthropic
        llm_request_data = {
            "provider": "anthropic",
            "model": model_name,
            "messages": anthropic_messages,
            "tools": tools
        }
        self.logger.bind(interface="llm", direction="request", data=llm_request_data, user_request_id=user_request_id).debug("Calling Anthropic LLM")
        
        try:
            response = self.client.messages.create(
                model=model_name,
                max_tokens=4096,
                system=system_prompt,
                messages=anthropic_messages,
                tools=tools if tools else None,
            )
            # Convert Anthropic response back to OpenAI-compatible format
            content = ""
            tool_calls = []
            for content_block in response.content:
                if hasattr(content_block, 'type'):
                    if content_block.type == "text":
                        content += getattr(content_block, 'text', '')
                    elif content_block.type == "tool_use":
                        # Get the input arguments from Anthropic's tool_use block
                        input_args = getattr(content_block, 'input', {})
                        
                        # Properly JSON-encode the arguments
                        try:
                            arguments_json = json.dumps(input_args)
                        except (TypeError, ValueError) as e:
                            self.logger.warning(f"Failed to JSON encode tool arguments: {e}")
                            arguments_json = "{}"
                        
                        tool_call = {
                            "id": getattr(content_block, 'id', None),
                            "type": "function",
                            "function": {
                                "name": getattr(content_block, 'name', ''),
                                "arguments": arguments_json
                            }
                        }
                        tool_calls.append(tool_call)
            token_count_in = getattr(response.usage, 'input_tokens', 0)
            token_count_out = getattr(response.usage, 'output_tokens', 0)
            
            # LLM Debug Logging - Log the response received from Anthropic
            llm_response_data = {
                "content_length": len(content) if content else 0,
                "tool_calls_count": len(tool_calls) if tool_calls else 0,
                "token_count_in": token_count_in,
                "token_count_out": token_count_out,
                "error": None,
                "full_response": content
            }
            self.logger.bind(interface="llm", direction="response", data=llm_response_data, user_request_id=user_request_id).debug("llm-response")
            
            return LLMResponse(
                content=content or None,
                tool_calls=tool_calls if tool_calls else None,
                token_count_in=token_count_in,
                token_count_out=token_count_out
            )
        except Exception as e:
            self.logger.error(f"Anthropic API error: {e}")
            # LLM Debug Logging - Log the error response
            llm_error_data = {
                "error": str(e),
                "token_count_in": 0,
                "token_count_out": 0
            }
            self.logger.bind(interface="llm", direction="response", data=llm_error_data, user_request_id=user_request_id).debug("Received error from Anthropic LLM")
            raise
    
    def format_tools(self, tools: List[Dict[str, Any]]) -> Any:
        return ToolFormatter.format_tools_for_provider(tools, "anthropic")
    
    def is_configured(self) -> bool:
        return bool(self.api_key)
    
    def get_available_models(self) -> List[str]:
        return self.available_models 