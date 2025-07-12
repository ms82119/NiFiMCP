"""
LLM Provider Factory.

This module provides a factory pattern for creating LLM provider instances
based on configuration and provider name.
"""

import importlib
from typing import Dict, Any, Optional
from loguru import logger

from ..base import LLMProvider


class LLMProviderFactory:
    """Factory for creating LLM provider instances."""
    
    # Provider configuration mapping
    PROVIDER_CONFIG = {
        'openai': {
            'client_class': 'OpenAIClient',
            'requires_api_key': True,
            'supports_tools': True,
            'error_handler': 'extract_openai_error',
        },
        'gemini': {
            'client_class': 'GeminiClient', 
            'requires_api_key': True,
            'supports_tools': True,
            'error_handler': 'extract_gemini_error',
            'mcp_integration': 'google_adk',  # Use Google ADK
        },
        'anthropic': {
            'client_class': 'AnthropicClient',
            'requires_api_key': True, 
            'supports_tools': True,
            'error_handler': 'extract_anthropic_error',
        },
        'perplexity': {
            'client_class': 'PerplexityClient',
            'requires_api_key': True,
            'supports_tools': True,
            'error_handler': 'extract_perplexity_error',  # Compatible format
        }
    }
    
    @staticmethod
    def create_provider(provider_name: str, config: Dict[str, Any]) -> Optional[LLMProvider]:
        """
        Create LLM provider instance based on configuration.
        
        Args:
            provider_name: Name of the provider (openai, gemini, anthropic, perplexity)
            config: Configuration dictionary containing API keys and settings
            
        Returns:
            LLMProvider instance or None if creation fails
        """
        provider_name_lower = provider_name.lower()
        provider_config = LLMProviderFactory.PROVIDER_CONFIG.get(provider_name_lower)
        
        if not provider_config:
            logger.error(f"Unsupported provider: {provider_name}")
            return None
        
        try:
            # Import the provider module
            module_name = f"nifi_chat_ui.llm.providers.{provider_name_lower}_client"
            module = importlib.import_module(module_name)
            
            # Get the client class
            client_class_name = provider_config['client_class']
            client_class = getattr(module, client_class_name)
            
            # Create the provider instance
            provider = client_class(config)
            
            logger.info(f"Successfully created {provider_name} provider")
            return provider
            
        except ImportError as e:
            logger.error(f"Failed to import {provider_name} provider module: {e}")
            return None
        except AttributeError as e:
            logger.error(f"Failed to find {client_class_name} in {provider_name} module: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to create {provider_name} provider: {e}")
            return None
    
    @staticmethod
    def get_supported_providers() -> list[str]:
        """Get list of supported provider names."""
        return list(LLMProviderFactory.PROVIDER_CONFIG.keys())
    
    @staticmethod
    def get_provider_config(provider_name: str) -> Optional[Dict[str, Any]]:
        """Get configuration for a specific provider."""
        return LLMProviderFactory.PROVIDER_CONFIG.get(provider_name.lower())
    
    @staticmethod
    def validate_provider_config(provider_name: str, config: Dict[str, Any]) -> bool:
        """
        Validate that the configuration has the required fields for a provider.
        
        Args:
            provider_name: Name of the provider
            config: Configuration dictionary
            
        Returns:
            True if configuration is valid, False otherwise
        """
        provider_config = LLMProviderFactory.get_provider_config(provider_name)
        if not provider_config:
            return False
        
        # Check if API key is required and present
        if provider_config.get('requires_api_key', False):
            # Check for nested config structure (e.g., config['openai']['api_key'])
            if provider_name in config:
                provider_section = config[provider_name]
                if isinstance(provider_section, dict) and provider_section.get('api_key'):
                    logger.debug(f"Found API key for {provider_name} in nested config")
                    return True
            
            # Also check for flat structure (e.g., config['OPENAI_API_KEY'])
            api_key_field = f"{provider_name.upper()}_API_KEY"
            logger.debug(f"Checking for API key field: {api_key_field}")
            logger.debug(f"Available config keys: {list(config.keys())}")
            
            if api_key_field in config and config[api_key_field]:
                logger.debug(f"Found API key for {provider_name} in flat config")
                return True
            
            # Special case for Gemini which uses GOOGLE_API_KEY (flat) or gemini['api_key'] (nested)
            if provider_name.lower() == "gemini":
                if ("GOOGLE_API_KEY" in config and config["GOOGLE_API_KEY"]) or (
                    "gemini" in config and isinstance(config["gemini"], dict) and config["gemini"].get("api_key")):
                    logger.debug(f"Found GOOGLE_API_KEY or gemini['api_key'] for gemini in config")
                    return True
            
            logger.warning(f"Missing required API key for {provider_name}")
            return False
        
        return True 