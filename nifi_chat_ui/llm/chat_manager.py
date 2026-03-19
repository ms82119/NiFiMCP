"""
Simplified Chat Manager using the new modular LLM architecture.

This module provides a clean interface to the LLM providers through the
new modular architecture, replacing the complex monolithic chat_manager.py.
"""

from typing import List, Dict, Any, Optional
from loguru import logger

from .base import LLMResponse
from .providers.factory import LLMProviderFactory
from .mcp.client import MCPClient
from .utils.token_counter import TokenCounter
from .utils.error_handler import LLMErrorHandler


class ChatManager:
    """Simplified chat manager using the new modular LLM architecture."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the chat manager.
        
        Args:
            config: Configuration dictionary containing API keys and settings
        """
        self.config = config
        self.providers = {}
        self.mcp_client = MCPClient()
        self.token_counter = TokenCounter()
        self.logger = logger.bind(component="ChatManager")
        
        # Initialize available providers
        self._initialize_providers()
    
    def _initialize_providers(self):
        """Initialize available LLM providers based on configuration."""
        supported_providers = LLMProviderFactory.get_supported_providers()
        
        for provider_name in supported_providers:
            if LLMProviderFactory.validate_provider_config(provider_name, self.config):
                provider = LLMProviderFactory.create_provider(provider_name, self.config)
                if provider:
                    self.providers[provider_name] = provider
                    self.logger.info(f"Initialized {provider_name} provider")
                else:
                    self.logger.warning(f"Failed to initialize {provider_name} provider")
            else:
                self.logger.debug(f"Skipping {provider_name} provider - configuration invalid")
    
    def get_llm_response(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        provider: str,
        model_name: str,
        user_request_id: Optional[str] = None,
        action_id: Optional[str] = None,
        selected_nifi_server_id: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = "auto",  # Use "auto" as default to distinguish from None
        tool_results: Optional[List[Dict[str, Any]]] = None,  # Optional tool results to process
        tool_calls: Optional[List[Dict[str, Any]]] = None  # Original tool calls for ID matching
    ) -> Dict[str, Any]:
        """
        Get response from specified LLM provider.
        
        Args:
            messages: Chat messages
            system_prompt: System prompt
            provider: LLM provider name
            model_name: Model name for the provider
            user_request_id: Optional user request ID
            action_id: Optional action ID
            selected_nifi_server_id: Optional NiFi server ID
            tools: Tools to use. "auto" = fetch from MCP, None = no tools, List = use provided tools
        """
        bound_logger = self.logger.bind(user_request_id=user_request_id, action_id=action_id)
        
        try:
            # Get provider instance
            provider_instance = self.providers.get(provider)
            if not provider_instance:
                raise ValueError(f"Provider {provider} not available")
            
            # Check if provider supports tools
            if not provider_instance.supports_tools():
                bound_logger.info(f"Provider {provider} doesn't support tools - running in Q&A mode")
                tools = None
                # Add a note about Q&A mode for providers that don't support tools
                qa_note = f"\n\n**ℹ️ Q&A Mode**: You're using {provider.title()} which operates in Q&A mode only. I can provide guidance and explanations, but cannot execute NiFi operations directly. For full tool support, try OpenAI or Anthropic models."
                system_prompt += qa_note
            else:
                # Handle tools parameter correctly
                if tools == "auto":
                    # Default behavior: fetch from MCP
                    bound_logger.info("No pre-filtered tools provided, fetching from MCP")
                    # Get tools from MCP with provider-specific schema validation
                    tools = self.mcp_client.get_tools_for_provider(provider, user_request_id, selected_nifi_server_id)
                    
                    if not tools:
                        bound_logger.warning("No tools available from MCP server")
                elif tools is None:
                    # Explicitly set to None - don't fetch tools
                    bound_logger.info("Tools explicitly set to None - no tools will be used")
                    tools = None
                elif isinstance(tools, list):
                    # Pre-filtered tools provided
                    bound_logger.info(f"Using pre-filtered tools ({len(tools)} tools)")
                else:
                    # Invalid tools parameter
                    bound_logger.warning(f"Invalid tools parameter: {type(tools)}, defaulting to no tools")
                    tools = None
            
            # IMPORTANT: Even if tools are None, we should still process the LLM response
            # This ensures that text-only responses (like status reports) still get displayed
            if tools is None:
                bound_logger.info("Running in text-only mode (no tools available)")
                # Add a note about text-only mode
                text_only_note = "\n\n**ℹ️ Text-Only Mode**: Running without tool support. I can provide guidance and explanations, but cannot execute NiFi operations directly."
                system_prompt += text_only_note
                
                # Preserve historical tool messages and assistant.tool_calls; only omit the top-level tools parameter
                bound_logger.info("Preserving historical tool messages and assistant.tool_calls; only omitting top-level tools parameter")
            
            # Send message to provider, always pass model_name
            response = provider_instance.send_message(
                messages, system_prompt, model_name, tools, user_request_id, action_id
            )
            
            # If we have tool results, process them and get final response
            if tool_results and response and not response.to_dict().get("error"):
                bound_logger.info(f"Processing {len(tool_results)} tool results and getting final LLM response")
                
                # Get the original response to preserve the message structure
                original_response_dict = response.to_dict()
                
                # Create tool result messages for the LLM
                tool_result_messages = []
                
                # Extract tool call IDs from the original response
                original_tool_calls = original_response_dict.get("tool_calls", [])
                
                for i, result in enumerate(tool_results):
                    # Get the correct tool_call_id from the original tool calls
                    if i < len(original_tool_calls):
                        tool_call = original_tool_calls[i]
                        tool_call_id = tool_call.get("id", f"call_{i}")
                    else:
                        tool_call_id = f"call_{i}"
                    
                    if "error" in result:
                        tool_result_messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": f"Error: {result['error']}"
                        })
                    else:
                        # Format the tool result for the LLM
                        tool_result = result["result"]
                        if isinstance(tool_result, (list, dict)):
                            # Format structured data nicely
                            import json
                            formatted_result = json.dumps(tool_result, indent=2)
                        else:
                            formatted_result = str(tool_result)
                        
                        tool_result_messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": formatted_result
                        })
                
                # Create a new message list that includes the original assistant message with tool_calls
                # and the tool result messages
                final_messages = messages.copy()
                
                # Add the original assistant message with tool_calls if it exists
                if "tool_calls" in original_response_dict:
                    final_messages.append({
                        "role": "assistant",
                        "content": original_response_dict.get("content", ""),
                        "tool_calls": original_response_dict["tool_calls"]
                    })
                
                # Add tool results to the conversation
                final_messages.extend(tool_result_messages)
                
                # Get final response from LLM with tool results
                bound_logger.info("Requesting final response from LLM with tool results")
                final_response = provider_instance.send_message(
                    final_messages, system_prompt, model_name, None, user_request_id, action_id  # No tools for final response
                )
                
                # Use the final response instead of the original
                response = final_response
            
            # Convert to dictionary format for compatibility
            result = response.to_dict()
            
            bound_logger.info(f"Successfully got response from {provider}")
            return result
        except Exception as e:
            bound_logger.error(f"Error getting LLM response: {e}", exc_info=True)
            return {"error": str(e)}
    
    def get_available_providers(self) -> List[str]:
        """Get list of available providers."""
        return list(self.providers.keys())
    
    def get_available_models(self, provider: str) -> List[str]:
        """Get list of available models for a provider."""
        if provider in self.providers:
            return self.providers[provider].get_available_models()
        return []
    
    def is_provider_configured(self, provider: str) -> bool:
        """Check if a provider is properly configured."""
        return provider in self.providers and self.providers[provider].is_configured()
    
    def execute_tool(self, tool_name: str, arguments: Dict[str, Any], user_request_id: Optional[str] = None, selected_nifi_server_id: Optional[str] = None) -> Any:
        """
        Execute an MCP tool.
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            user_request_id: Optional user request ID for logging
            selected_nifi_server_id: Optional NiFi server ID
            
        Returns:
            Tool execution result
        """
        return self.mcp_client.execute_tool(tool_name, arguments, user_request_id, selected_nifi_server_id)
    
    def get_tools(self, provider: str, user_request_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get tools for a specific provider with schema validation.
        
        Args:
            provider: LLM provider name
            user_request_id: Optional user request ID for logging
            
        Returns:
            List of tool definitions with provider-specific schema validation
        """
        return self.mcp_client.get_tools_for_provider(provider, user_request_id) 