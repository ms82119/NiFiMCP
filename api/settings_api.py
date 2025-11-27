"""
Settings API endpoints for configuration data.

This module provides REST API endpoints for retrieving
configuration data like models, NiFi servers, workflows, and tools.
"""

from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException, Body
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
        from nifi_mcp_server.core import get_nifi_client
        
        # Get server configuration
        server_config = settings.get_nifi_server_config(server_id)
        if not server_config:
            raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
        
        nifi_client = None
        try:
            # Use the factory function which handles credential callbacks
            nifi_client = await get_nifi_client(server_id, bound_logger=logger)
            
            # Try to get process groups (root level) to verify connection
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
        finally:
            # Ensure client is closed to prevent resource leaks
            if nifi_client:
                try:
                    await nifi_client.close()
                except Exception as close_error:
                    logger.warning(f"Error closing NiFi client: {close_error}")
            
    except HTTPException:
        # Re-raise HTTP exceptions as-is (e.g., 404 for server not found)
        raise
    except Exception as e:
        logger.error(f"Error checking NiFi server health: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nifi-servers/{server_id}/credentials")
async def submit_credentials(server_id: str, credentials: dict = Body(...)):
    """Submit credentials for a NiFi server.
    
    This endpoint allows the frontend to submit username/password
    or access token when authentication is required.
    
    For OIDC servers, accepts:
    - token: Access token string
    
    For NiFi 1.x servers, accepts:
    - username: Username string
    - password: Password string
    """
    try:
        from nifi_mcp_server.core import set_credentials
        from nifi_mcp_server.token_store import set_token
        
        # Check if this is a token (for OIDC) or username/password
        token = credentials.get("token")
        username = credentials.get("username")
        password = credentials.get("password")
        
        if token:
            # Store token for OIDC authentication
            token_trimmed = token.strip()
            logger.info(f"Storing access token for server {server_id} (token length: {len(token_trimmed)})")
            set_token(server_id, token_trimmed)
            logger.info(f"Access token stored successfully for server {server_id}")
        elif username and password:
            # Store username/password for standard authentication
            set_credentials(server_id, username, password)
            logger.info(f"Credentials stored for server {server_id}")
        else:
            raise HTTPException(
                status_code=400, 
                detail="Either 'token' (for OIDC) or 'username' and 'password' (for standard auth) are required"
            )
        
        return {
            "status": "success",
            "message": "Credentials stored successfully"
        }
    except Exception as e:
        logger.error(f"Error storing credentials: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/nifi-servers/{server_id}/credentials")
async def clear_server_credentials(server_id: str):
    """Clear stored credentials for a NiFi server."""
    try:
        from nifi_mcp_server.core import clear_credentials
        
        clear_credentials(server_id)
        logger.info(f"Credentials cleared for server {server_id}")
        return {
            "status": "success",
            "message": "Credentials cleared successfully"
        }
    except Exception as e:
        logger.error(f"Error clearing credentials: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nifi-servers/{server_id}/auth-config")
async def get_server_auth_config(server_id: str):
    """Get authentication configuration for a NiFi server."""
    client = None
    try:
        from nifi_mcp_server.nifi_client import NiFiClient
        
        server_config = settings.get_nifi_server_config(server_id)
        if not server_config:
            raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
        
        base_url = server_config.get('url')
        if not base_url:
            raise HTTPException(status_code=400, detail="Server URL not configured")
        
        # Create a temporary client to check auth config
        client = NiFiClient(
            base_url=base_url,
            tls_verify=server_config.get('tls_verify', True)
        )
        auth_config = await client.get_authentication_config()
        
        return auth_config
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Error getting auth config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Ensure client is closed even if an exception occurs
        if client:
            try:
                await client.close()
            except Exception as close_error:
                logger.warning(f"Error closing NiFi client in auth-config endpoint: {close_error}")
