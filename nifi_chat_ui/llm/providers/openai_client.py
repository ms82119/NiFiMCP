"""
OpenAI LLM Provider implementation.

This module implements the LLMProvider interface for OpenAI using the OpenAI SDK.
"""

from typing import List, Dict, Any, Optional
from openai import OpenAI
from loguru import logger
from ..base import LLMProvider, LLMResponse
from ..utils.token_counter import TokenCounter
from ..mcp.tool_formatter import ToolFormatter


class OpenAIClient(LLMProvider):
    """OpenAI LLM Provider implementation."""
    
    def __init__(self, config: Dict[str, Any]):
        if "openai" in config:
            openai_config = config["openai"]
            api_key = openai_config.get("api_key")
            self.available_models = openai_config.get("models", ["gpt-4", "gpt-3.5-turbo"])
        else:
            api_key = config.get("OPENAI_API_KEY")
            self.available_models = config.get("OPENAI_MODELS", ["gpt-4", "gpt-3.5-turbo"])
        super().__init__(api_key)
        self.client = OpenAI(api_key=api_key)
        self.token_counter = TokenCounter()
        self.logger = logger.bind(provider="OpenAI")
    
    def send_message(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        model_name: str,
        tools: Optional[List[Any]] = None,
        user_request_id: Optional[str] = None,
        action_id: Optional[str] = None
    ) -> LLMResponse:
        # Prepend system prompt as the first message if not already present
        openai_messages = messages.copy()
        if not openai_messages or openai_messages[0]["role"] != "system":
            openai_messages.insert(0, {"role": "system", "content": system_prompt})
        
        # LLM Debug Logging - Log the request being sent to OpenAI
        llm_request_data = {
            "provider": "openai",
            "model": model_name,
            "messages": openai_messages,
            "tools": tools
        }
        self.logger.bind(interface="llm", direction="request", data=llm_request_data, user_request_id=user_request_id, action_id=action_id).debug("Calling OpenAI LLM")
        
        try:
            response = self.client.chat.completions.create(
                model=model_name,
                messages=openai_messages,
                tools=tools if tools else None,
                tool_choice="auto" if tools else None,
            )
            response_message = response.choices[0].message
            response_content = response_message.content
            response_tool_calls = response_message.tool_calls
            if response_tool_calls:
                response_tool_calls = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                    } for tc in response_tool_calls
                ]
            token_count_in = getattr(response.usage, 'prompt_tokens', 0)
            token_count_out = getattr(response.usage, 'completion_tokens', 0)
            
            # LLM Debug Logging - Log the response received from OpenAI
            llm_response_data = {
                "content_length": len(response_content) if response_content else 0,
                "tool_calls_count": len(response_tool_calls) if response_tool_calls else 0,
                "token_count_in": token_count_in,
                "token_count_out": token_count_out,
                "error": None,
                "full_response": response_content
            }
            self.logger.bind(interface="llm", direction="response", data=llm_response_data, user_request_id=user_request_id, action_id=action_id).debug("llm-response")
            
            return LLMResponse(
                content=response_content,
                tool_calls=response_tool_calls,
                token_count_in=token_count_in,
                token_count_out=token_count_out
            )
        except Exception as e:
            self.logger.error(f"OpenAI API error: {e}")
            # LLM Debug Logging - Log the error response
            llm_error_data = {
                "error": str(e),
                "token_count_in": 0,
                "token_count_out": 0
            }
            self.logger.bind(interface="llm", direction="response", data=llm_error_data, user_request_id=user_request_id, action_id=action_id).debug("Received error from OpenAI LLM")
            raise
    
    def format_tools(self, tools: List[Dict[str, Any]]) -> Any:
        return ToolFormatter.format_tools_for_provider(tools, "openai")
    
    def is_configured(self) -> bool:
        return bool(self.api_key)
    
    def get_available_models(self) -> List[str]:
        return self.available_models 