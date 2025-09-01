"""
Settings API endpoints for configuration data.

This module provides REST API endpoints for retrieving
configuration data like models, NiFi servers, workflows, and tools.
"""

from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException
from loguru import logger

from config import settings

router = APIRouter(prefix="/api/settings", tags=["Settings"])


@router.get("/models")
async def get_models():
    """Get available LLM models."""
    try:
        models = []
        
        # Add OpenAI models
        if settings.OPENAI_API_KEY and settings.OPENAI_MODELS:
            for model in settings.OPENAI_MODELS:
                models.append({
                    "provider": "openai",
                    "name": model,
                    "display_name": f"OpenAI: {model}"
                })
        
        # Add Google models
        if settings.GOOGLE_API_KEY and settings.GEMINI_MODELS:
            for model in settings.GEMINI_MODELS:
                models.append({
                    "provider": "gemini",
                    "name": model,
                    "display_name": f"Google: {model}"
                })
        
        # Add Perplexity models
        if settings.PERPLEXITY_API_KEY and settings.PERPLEXITY_MODELS:
            for model in settings.PERPLEXITY_MODELS:
                models.append({
                    "provider": "perplexity",
                    "name": model,
                    "display_name": f"Perplexity: {model}"
                })
        
        # Add Anthropic models
        if settings.ANTHROPIC_API_KEY and settings.ANTHROPIC_MODELS:
            for model in settings.ANTHROPIC_MODELS:
                models.append({
                    "provider": "anthropic",
                    "name": model,
                    "display_name": f"Anthropic: {model}"
                })
        
        return models
        
    except Exception as e:
        logger.error(f"Error getting models: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nifi-servers")
async def get_nifi_servers():
    """Get available NiFi servers."""
    try:
        nifi_servers = settings.get_nifi_servers()
        
        servers = []
        for server_info in nifi_servers:
            # Handle different return formats
            if isinstance(server_info, (list, tuple)):
                if len(server_info) >= 2:
                    server_id, server_name = server_info[0], server_info[1]
                else:
                    continue
            elif isinstance(server_info, dict):
                server_id = server_info.get('id')
                server_name = server_info.get('name')
            else:
                continue
                
            servers.append({
                "id": server_id,
                "name": server_name,
                "config": settings.get_nifi_server_config(server_id)
            })
        
        return servers
        
    except Exception as e:
        logger.error(f"Error getting NiFi servers: {e}", exc_info=True)
        # Return empty list instead of error for now
        return []


@router.get("/workflows")
async def get_workflows():
    """Get available workflows."""
    try:
        # Import here to avoid circular imports
        from nifi_mcp_server.workflows.registry import get_workflow_registry
        
        registry = get_workflow_registry()
        workflows = registry.list_workflows(enabled_only=True)
        
        workflow_list = []
        for workflow in workflows:
            workflow_list.append({
                "name": workflow.name,
                "display_name": workflow.display_name,
                "description": workflow.description,
                "phases": workflow.phases,
                "is_async": workflow.is_async
            })
        
        return workflow_list
        
    except Exception as e:
        logger.error(f"Error getting workflows: {e}", exc_info=True)
        # Return empty list instead of error for now
        return []


@router.get("/tools")
async def get_tools():
    """Get available MCP tools."""
    try:
        # Import here to avoid circular imports
        from nifi_chat_ui.mcp_handler import get_available_tools
        
        # Get the first available NiFi server ID for tools
        nifi_servers = settings.get_nifi_servers()
        selected_nifi_server_id = None
        
        if nifi_servers:
            # Get the first server ID
            if isinstance(nifi_servers[0], (list, tuple)) and len(nifi_servers[0]) >= 1:
                selected_nifi_server_id = nifi_servers[0][0]
            elif isinstance(nifi_servers[0], dict):
                selected_nifi_server_id = nifi_servers[0].get('id')
        
        logger.info(f"Fetching tools for NiFi server: {selected_nifi_server_id}")
        tools = get_available_tools(phase="All", selected_nifi_server_id=selected_nifi_server_id)
        logger.info(f"Retrieved {len(tools)} tools")
        return tools
        
    except Exception as e:
        logger.error(f"Error getting tools: {e}", exc_info=True)
        # Return empty list instead of error for now
        return []


@router.get("/config")
async def get_config():
    """Get application configuration."""
    try:
        return {
            "feature_auto_stop": settings.get_feature_auto_stop_enabled(),
            "feature_auto_delete": settings.get_feature_auto_delete_enabled(),
            "feature_auto_purge": settings.get_feature_auto_purge_enabled(),
            "llm_enqueue_enabled": settings.get_llm_enqueue_enabled(),
            "interface_debug_enabled": settings.get_interface_debug_enabled(),
            "workflow_execution_mode": settings.get_workflow_execution_mode(),
            "workflow_action_limit": settings.get_workflow_action_limit(),
            "workflow_retry_attempts": settings.get_workflow_retry_attempts(),
            "enabled_workflows": settings.get_enabled_workflows(),
            "expert_help_available": settings.is_expert_help_available(),
            "expert_provider": settings.expert_provider,
            "expert_model": settings.expert_model
        }
        
    except Exception as e:
        logger.error(f"Error getting config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nifi-server-health/{server_id}")
async def check_nifi_server_health(server_id: str):
    """Check the health of a specific NiFi server by attempting to connect and get process groups."""
    try:
        from nifi_mcp_server.nifi_client import NiFiClient
        
        # Get server configuration
        server_config = settings.get_nifi_server_config(server_id)
        if not server_config:
            raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
        
        # Extract connection details
        # Support multiple config key variants for the NiFi API URL
        base_url = (
            server_config.get('base_url')
            or server_config.get('api_url')
            or server_config.get('url')
        )
        username = server_config.get('username')
        password = server_config.get('password')
        tls_verify = server_config.get('tls_verify', True)
        
        if not base_url:
            raise HTTPException(status_code=400, detail="Server base_url not configured")
        
        # Create NiFi client and test connection
        nifi_client = NiFiClient(base_url, username, password, tls_verify)
        
        try:
            # Try to authenticate and get process groups (root level)
            await nifi_client.authenticate()
            await nifi_client.get_process_groups("root")
            
            return {
                "server_id": server_id,
                "status": "healthy",
                "message": "Successfully connected and retrieved data"
            }
            
        except Exception as nifi_error:
            logger.warning(f"NiFi server {server_id} health check failed: {nifi_error}")
            return {
                "server_id": server_id,
                "status": "unhealthy",
                "message": f"Connection failed: {str(nifi_error)}"
            }
            
    except Exception as e:
        logger.error(f"Error checking NiFi server health: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
