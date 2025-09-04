"""
Token counting utilities for different LLM providers.

This module consolidates token counting logic from the original chat_manager.py
and provides provider-specific token counting methods.
"""

import tiktoken
from typing import List, Dict, Any, Optional
from loguru import logger


class TokenCounter:
    """Centralized token counting for all LLM providers."""
    
    def __init__(self):
        self.tiktoken_available = self._check_tiktoken_availability()
        if not self.tiktoken_available:
            logger.warning("tiktoken not available, using approximation for token counting")
    
    def _check_tiktoken_availability(self) -> bool:
        """Check if tiktoken is available for accurate token counting."""
        try:
            import tiktoken
            return True
        except ImportError:
            return False
    
    def count_tokens_openai(self, text: str, model: str) -> int:
        """
        Count tokens using OpenAI's tiktoken library.
        
        Args:
            text: Text to count tokens for
            model: OpenAI model name for encoding selection
            
        Returns:
            Number of tokens
        """
        if not self.tiktoken_available:
            # Fallback to approximation if tiktoken isn't available
            return len(text.split())
        
        encoding = None
        try:
            # First, try the standard model mapping
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            # If standard mapping fails, try the common encoding for GPT-4 and new models
            try:
                encoding = tiktoken.get_encoding("cl100k_base")
            except Exception as e:
                logger.error(f"Failed to get 'cl100k_base' encoding: {e}. Using approximation.")
                return len(text.split())
        except Exception as e:
            logger.warning(f"Unexpected error getting tiktoken encoding for model {model}: {e}. Using approximation.")
            return len(text.split())

        # If we successfully got an encoding
        if encoding:
            try:
                return len(encoding.encode(text))
            except Exception as e:
                logger.warning(f"Error encoding text with tiktoken for model {model}: {e}. Using approximation.")
                return len(text.split())
        else:
            logger.error(f"Failed to obtain any tiktoken encoding for model {model}. Using approximation.")
            return len(text.split())
    
    def count_tokens_gemini(self, text: str) -> int:
        """
        Approximate token count for Gemini models.
        
        Args:
            text: Text to count tokens for
            
        Returns:
            Approximate number of tokens (character-based estimate)
        """
        # For Gemini, we'll use a character-based estimate since there's no official tokenizer
        # Roughly 4 characters per token is a common approximation
        return len(text) // 4
    
    def count_tokens_perplexity(self, text: str, model: str) -> int:
        """
        Count tokens for Perplexity models using OpenAI's tiktoken.
        
        Args:
            text: Text to count tokens for
            model: Perplexity model name
            
        Returns:
            Number of tokens
        """
        # Perplexity uses OpenAI-compatible API, so we can use the same tokenizer approach
        return self.count_tokens_openai(text, model)
    
    def count_tokens_anthropic(self, text: str) -> int:
        """
        Approximate token count for Anthropic models.
        
        Args:
            text: Text to count tokens for
            
        Returns:
            Approximate number of tokens (character-based estimate)
        """
        # For Anthropic, we'll use a character-based estimate since there's no official tokenizer
        # Roughly 4 characters per token is a common approximation
        return len(text) // 4
    
    def calculate_input_tokens(
        self,
        messages: List[Dict],
        provider: str,
        model_name: str,
        tools: Optional[List[Any]] = None
    ) -> int:
        """
        Calculate total input tokens based on message history, provider, and tools.
        
        Args:
            messages: List of message dictionaries
            provider: LLM provider name
            model_name: Model name for provider-specific token counting
            tools: Optional list of tool definitions
            
        Returns:
            Total number of input tokens
        """
        total_tokens = 0
        provider_lower = provider.lower()

        # Calculate tokens for messages
        for message in messages:
            content = message.get("content", "")
            if isinstance(content, str):
                if provider_lower == "openai":
                    total_tokens += self.count_tokens_openai(content, model_name)
                elif provider_lower == "perplexity":
                    total_tokens += self.count_tokens_perplexity(content, model_name)
                elif provider_lower == "anthropic":
                    total_tokens += self.count_tokens_anthropic(content)
                else:  # gemini or others
                    total_tokens += self.count_tokens_gemini(content)
            elif message.get("role") == "tool":
                # Simple approximation for tool results
                total_tokens += len(str(content)) // 4
            elif isinstance(message.get("tool_calls"), list):
                # Simple approximation for tool requests
                import json
                total_tokens += len(json.dumps(message["tool_calls"])) // 4
            
        # Calculate tokens for tool definitions
        if tools is not None:  # Changed from 'if tools:' to 'if tools is not None:'
            tools_str = ""
            try:
                import json
                if provider_lower in ["openai", "perplexity"]:
                    # OpenAI and Perplexity tools are already JSON-serializable dicts
                    tools_str = json.dumps(tools)
                elif provider_lower == "anthropic":
                    # Anthropic tools are dicts with input_schema, should be JSON-serializable
                    tools_str = json.dumps(tools)
                elif provider_lower == "gemini":
                    # Gemini tools are FunctionDeclaration objects, need safe serialization
                    tool_dicts = []
                    for declaration in tools:
                        # Basic dict representation for token counting
                        tool_dicts.append({
                            "name": getattr(declaration, 'name', ''),
                            "description": getattr(declaration, 'description', ''),
                        })
                    tools_str = json.dumps(tool_dicts)
                else:
                    logger.warning(f"Token calculation for tools not implemented for provider: {provider}")

                # Count tokens for the serialized tool string
                if tools_str:
                    tool_tokens = 0
                    if provider_lower == "openai":
                        tool_tokens = self.count_tokens_openai(tools_str, model_name)
                    elif provider_lower == "perplexity":
                        tool_tokens = self.count_tokens_perplexity(tools_str, model_name)
                    elif provider_lower == "anthropic":
                        tool_tokens = self.count_tokens_anthropic(tools_str)
                    else:  # Assume Gemini or other
                        tool_tokens = self.count_tokens_gemini(tools_str)
                    
                    total_tokens += tool_tokens
                    
            except Exception as e:
                logger.warning(f"Error estimating token count for tool definitions: {e}")
        else:
            # Log when tools is None for debugging
            logger.debug("No tools provided for token calculation")

        return total_tokens 