import asyncio
from typing import List, Dict, Optional, Any, Union, Literal
from datetime import datetime, timedelta

# Import necessary components from parent/utils
from loguru import logger
# Import mcp ONLY
from ..core import mcp
# Removed nifi_api_client import
# Import context variables
from ..request_context import current_nifi_client, current_request_logger # Added

from .utils import (
    tool_phases,
    # ensure_authenticated # Removed
    # No other utils needed for this specific tool
)
from nifi_mcp_server.nifi_client import NiFiClient, NiFiAuthenticationError
from mcp.server.fastmcp.exceptions import ToolError
from config import settings as mcp_settings


# Rate limiting storage for expert help
_expert_help_usage = {}  # {request_id: [timestamp1, timestamp2, ...]}
_EXPERT_HELP_LIMIT = 2  # Max calls per request ID
_EXPERT_HELP_WINDOW_HOURS = 24  # Reset window

def _cleanup_expired_expert_help_usage():
    """Clean up expired rate limit entries."""
    current_time = datetime.now()
    cutoff_time = current_time - timedelta(hours=_EXPERT_HELP_WINDOW_HOURS)
    
    for request_id in list(_expert_help_usage.keys()):
        # Filter out expired timestamps
        _expert_help_usage[request_id] = [
            timestamp for timestamp in _expert_help_usage[request_id]
            if timestamp > cutoff_time
        ]
        # Remove entry if no valid timestamps remain
        if not _expert_help_usage[request_id]:
            del _expert_help_usage[request_id]

def _check_expert_help_rate_limit(request_id: str) -> bool:
    """Check if the request ID has exceeded the expert help rate limit."""
    if not request_id or request_id == "-":
        return True  # Allow if no request ID (shouldn't happen in normal usage)
    
    _cleanup_expired_expert_help_usage()
    
    usage_count = len(_expert_help_usage.get(request_id, []))
    return usage_count < _EXPERT_HELP_LIMIT

def _record_expert_help_usage(request_id: str):
    """Record an expert help usage for the given request ID."""
    if not request_id or request_id == "-":
        return
    
    if request_id not in _expert_help_usage:
        _expert_help_usage[request_id] = []
    
    _expert_help_usage[request_id].append(datetime.now())

def _format_processor_type_summary(processor_type_data: Dict) -> Dict:
    """Formats the processor type data for the lookup tool response."""
    bundle = processor_type_data.get("bundle", {})
    return {
        "type": processor_type_data.get("type"),
        "bundle_group": bundle.get("group"),
        "bundle_artifact": bundle.get("artifact"),
        "bundle_version": bundle.get("version"),
        "description": processor_type_data.get("description"),
        "tags": processor_type_data.get("tags", []), # Ensure tags is a list
    }

def _format_controller_service_type_summary(service_type_data: Dict) -> Dict:
    """Formats the controller service type data for the lookup tool response."""
    bundle = service_type_data.get("bundle", {})
    return {
        "type": service_type_data.get("type"),
        "bundle_group": bundle.get("group"),
        "bundle_artifact": bundle.get("artifact"),
        "bundle_version": bundle.get("version"),
        "description": service_type_data.get("description"),
        "tags": service_type_data.get("tags", []), # Ensure tags is a list
    }

@mcp.tool()
@tool_phases(["Review","Build", "Modify"])
async def lookup_nifi_processor_types(
    processor_names: List[str],
    bundle_artifact_filter: str | None = None
) -> List[Dict]:
    """
    Looks up available NiFi processor types by display names, returning key details including the full class name.
    
    This tool efficiently handles multiple processor name lookups in a single request,
    ideal for building complex flows that require multiple processor types.

    Args:
        processor_names: List of processor display names (e.g., ['GenerateFlowFile', 'LogAttribute']). Case-insensitive.
        bundle_artifact_filter: Optional. Filters by bundle artifact (e.g., 'nifi-standard-nar'). Case-insensitive.
        
    Example:
    ```python
    processor_names = [
        "GenerateFlowFile",
        "LogAttribute", 
        "InvokeHTTP",
        "UpdateAttribute"
    ]
    ```

    Returns:
        List of dictionaries, each containing:
        - query: The original processor name that was searched
        - matches: List of matching processor types (empty if no matches)
        - match_count: Number of matches found
    """
    # Get client and logger from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
    
    if not processor_names:
        raise ToolError("The 'processor_names' list cannot be empty.")
    if not isinstance(processor_names, list):
        raise ToolError("Invalid 'processor_names' type. Expected a list of strings.")
         
    # Authentication handled by factory
    local_logger = local_logger.bind(processor_names=processor_names, bundle_artifact_filter=bundle_artifact_filter)
    
    local_logger.info(f"Looking up processor type details for {len(processor_names)} processor names")
    try:
        nifi_req = {"operation": "get_processor_types"}
        local_logger.bind(interface="nifi", direction="request", data=nifi_req).debug("Calling NiFi API")
        all_types = await nifi_client.get_processor_types()
        nifi_resp = {"processor_type_count": len(all_types)}
        local_logger.bind(interface="nifi", direction="response", data=nifi_resp).debug("Received from NiFi API")

        filter_artifact_lower = bundle_artifact_filter.lower() if bundle_artifact_filter else None
        results = []
        
        # Process each processor name in the request
        for processor_name in processor_names:
            processor_name = str(processor_name).strip()
            if not processor_name:
                continue
                
            matches = []
            search_name_lower = processor_name.lower()

            for proc_type in all_types:
                # Extract relevant fields, ensuring they are strings and lowercased
                title = proc_type.get("title", "").lower()
                type_str = proc_type.get("type", "").lower()
                description = proc_type.get("description", "").lower()
                tags = [tag.lower() for tag in proc_type.get("tags", [])] # Lowercase all tags

                # Check if the search term is present in any relevant field
                name_match_found = (
                    search_name_lower in title or
                    search_name_lower in type_str or
                    search_name_lower in description or
                    any(search_name_lower in tag for tag in tags)
                )

                if name_match_found:
                    # Apply bundle artifact filter if specified
                    if filter_artifact_lower:
                        bundle = proc_type.get("bundle", {})
                        artifact = bundle.get("artifact", "").lower()
                        if artifact == filter_artifact_lower: # Check lowercase artifact
                            matches.append(_format_processor_type_summary(proc_type))
                    else:
                        matches.append(_format_processor_type_summary(proc_type))

            # Add result for this processor name
            result = {
                "query": processor_name,
                "matches": matches,
                "match_count": len(matches)
            }
            results.append(result)
            local_logger.debug(f"Found {len(matches)} match(es) for '{processor_name}'")

        total_matches = sum(r["match_count"] for r in results)
        local_logger.info(f"Completed lookup for {len(processor_names)} processor names, {total_matches} total matches found")
        return results
        
    except (NiFiAuthenticationError, ConnectionError, ToolError) as e:
        local_logger.error(f"API/Tool error looking up processor types: {e}", exc_info=False)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        raise ToolError(f"Failed to lookup NiFi processor types: {e}")
    except Exception as e:
        local_logger.error(f"Unexpected error looking up processor types: {e}", exc_info=True)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received unexpected error from NiFi API")
        raise ToolError(f"An unexpected error occurred: {e}")


@mcp.tool()
@tool_phases(["Build", "Modify"])
async def get_controller_service_types(
    service_name: str | None = None,
    bundle_artifact_filter: str | None = None
) -> Union[List[Dict], Dict]:
    """
    Retrieves available NiFi controller service types, optionally filtered by name or bundle.
    
    This tool provides type discovery capabilities for controller services and supports
    enhanced error handling when invalid service types are provided to creation tools.

    Args:
        service_name: Optional. Filter by service display name (e.g., 'DBCPConnectionPool'). Case-insensitive.
        bundle_artifact_filter: Optional. Filter by bundle artifact (e.g., 'nifi-dbcp-service-nar'). Case-insensitive.

    Returns:
        - If service_name provided and one match: A dictionary with service type details.
        - If service_name provided and multiple matches: A list of matching dictionaries.
        - If service_name provided and no matches: An empty list.
        - If no service_name provided: A list of all available controller service types.
        
    Each service type dictionary includes:
        - type: Full Java class name
        - bundle_group: Bundle group identifier  
        - bundle_artifact: Bundle artifact name
        - bundle_version: Bundle version
        - description: Service description
        - tags: List of associated tags
    """
    # Get client and logger from context
    nifi_client: Optional[NiFiClient] = current_nifi_client.get()
    local_logger = current_request_logger.get() or logger
    if not nifi_client:
        raise ToolError("NiFi client context is not set. This tool requires the X-Nifi-Server-Id header.")
    if not local_logger:
         raise ToolError("Request logger context is not set.")
         
    # Authentication handled by factory
    from ..request_context import current_user_request_id, current_action_id
    user_request_id = current_user_request_id.get() or "-"
    action_id = current_action_id.get() or "-"
    
    local_logger = local_logger.bind(service_name=service_name, bundle_artifact_filter=bundle_artifact_filter)
    
    if service_name:
        local_logger.info(f"Looking up controller service type details for name: '{service_name}'")
    else:
        local_logger.info("Retrieving all available controller service types")
        
    try:
        nifi_req = {"operation": "get_controller_service_types"}
        local_logger.bind(interface="nifi", direction="request", data=nifi_req).debug("Calling NiFi API")
        all_types = await nifi_client.get_controller_service_types(user_request_id=user_request_id, action_id=action_id)
        nifi_resp = {"controller_service_type_count": len(all_types)}
        local_logger.bind(interface="nifi", direction="response", data=nifi_resp).debug("Received from NiFi API")

        if not service_name:
            # Return all types, optionally filtered by bundle
            results = []
            filter_artifact_lower = bundle_artifact_filter.lower() if bundle_artifact_filter else None
            
            for service_type in all_types:
                if filter_artifact_lower:
                    bundle = service_type.get("bundle", {})
                    artifact = bundle.get("artifact", "").lower()
                    if artifact == filter_artifact_lower:
                        results.append(_format_controller_service_type_summary(service_type))
                else:
                    results.append(_format_controller_service_type_summary(service_type))
                    
            local_logger.info(f"Returning {len(results)} controller service type(s)")
            return results

        # Search for specific service name
        matches = []
        search_name_lower = service_name.lower()
        filter_artifact_lower = bundle_artifact_filter.lower() if bundle_artifact_filter else None

        for service_type in all_types:
            # Extract relevant fields, ensuring they are strings and lowercased
            title = service_type.get("title", "").lower()
            type_str = service_type.get("type", "").lower()
            description = service_type.get("description", "").lower()
            tags = [tag.lower() for tag in service_type.get("tags", [])] # Lowercase all tags

            # Check if the search term is present in any relevant field
            name_match_found = (
                search_name_lower in title or
                search_name_lower in type_str or
                search_name_lower in description or
                any(search_name_lower in tag for tag in tags)
            )

            if name_match_found:
                # Apply bundle artifact filter if specified
                if filter_artifact_lower:
                    bundle = service_type.get("bundle", {})
                    artifact = bundle.get("artifact", "").lower()
                    if artifact == filter_artifact_lower: # Check lowercase artifact
                        matches.append(_format_controller_service_type_summary(service_type))
                else:
                    matches.append(_format_controller_service_type_summary(service_type))

        local_logger.info(f"Found {len(matches)} match(es) for '{service_name}'")
        
        if len(matches) == 1:
            return matches[0]
        else:
            return matches
        
    except (NiFiAuthenticationError, ConnectionError, ToolError) as e:
        local_logger.error(f"API/Tool error looking up controller service types: {e}", exc_info=False)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received error from NiFi API")
        raise ToolError(f"Failed to lookup NiFi controller service types: {e}")
    except Exception as e:
        local_logger.error(f"Unexpected error looking up controller service types: {e}", exc_info=True)
        local_logger.bind(interface="nifi", direction="response", data={"error": str(e)}).debug("Received unexpected error from NiFi API")
        raise ToolError(f"An unexpected error occurred: {e}")


@mcp.tool()
@tool_phases(["Build", "Modify", "Review", "Query"])
async def ask_nifi_expert(question: str) -> str:
    """
    Gets expert help from a configured LLM for complex questions or problems.

    Uses a configured LLM (separate from the main chat) to answer NiFi-related questions.

    Use this when you need additional expertise or a different perspective on a challenging
    NiFi configuration, debugging, or design question. The expert model can provide insights
    without access to the current conversation history.
    
    Rate limited to 2 calls per request session. If the limit is exceeded, explain your
    problem directly to the user instead.

    Args:
        question: The specific question or problem you need expert help with. Be clear and detailed.

    Returns:
        Expert response as plain text, or an error message if the service is unavailable.
    """
    # Get logger from context (no NiFi client needed for this tool)
    local_logger = current_request_logger.get() or logger
    if not local_logger:
        raise ToolError("Request logger context is not set.")
    
    # Get request context for rate limiting
    from config.logging_setup import request_context
    context_data = request_context.get()
    request_id = context_data.get('user_request_id', '-') if context_data else '-'
    
    local_logger = local_logger.bind(question_preview=question[:100] + "..." if len(question) > 100 else question)
    local_logger.info(f"Expert help requested for request_id: {request_id}")
    
    # Check if expert help is available
    if not mcp_settings.is_expert_help_available():
        provider, model = mcp_settings.get_expert_help_config()
        if not provider or not model:
            error_msg = "Expert help is not configured. Please set 'llm.expert_help_model.provider' and 'llm.expert_help_model.model' in config.yaml"
        else:
            error_msg = f"Expert help is configured ({provider}:{model}) but the API key for {provider} is not available."
        local_logger.warning(error_msg)
        return f"Expert help unavailable: {error_msg}"
    
    # Check rate limiting
    if not _check_expert_help_rate_limit(request_id):
        usage_count = len(_expert_help_usage.get(request_id, []))
        error_msg = f"Expert help rate limit exceeded ({usage_count}/{_EXPERT_HELP_LIMIT} calls used). Please explain your current problem or question directly to the user instead of using expert help."
        local_logger.warning(error_msg)
        return error_msg
    
    # Record usage
    _record_expert_help_usage(request_id)
    
    # Validate question
    if not question or not question.strip():
        raise ToolError("Question cannot be empty.")
    
    if len(question) > 2000:  # Reasonable limit
        raise ToolError("Question is too long. Please summarize your question to under 2000 characters.")
    
    try:
        provider, model = mcp_settings.get_expert_help_config()
        local_logger.info(f"Calling expert help: {provider}:{model}")
        
        # Simple message format - just the question as a user message
        messages = [{"role": "user", "content": question.strip()}]
        
        # Call the expert model directly using the appropriate provider
        expert_request_data = {
            "provider": provider,
            "model": model,
            "messages": messages,
            "tools": None
        }
        local_logger.bind(interface="llm", direction="request", data=expert_request_data).debug("Calling Expert LLM")
        
        # Initialize the appropriate client and make the call directly
        if provider.lower() == "perplexity":
            from openai import OpenAI
            import config.settings as config
            
            if not config.PERPLEXITY_API_KEY:
                return "Expert help unavailable: Perplexity API key not configured."
            
            client = OpenAI(
                api_key=config.PERPLEXITY_API_KEY,
                base_url="https://api.perplexity.ai",
                timeout=60.0,
                max_retries=2
            )
            
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7,
                max_tokens=1000
            )
            
            content = response.choices[0].message.content if response.choices else ""
            
        elif provider.lower() == "openai":
            from openai import OpenAI
            import config.settings as config
            
            if not config.OPENAI_API_KEY:
                return "Expert help unavailable: OpenAI API key not configured."
            
            client = OpenAI(
                api_key=config.OPENAI_API_KEY,
                timeout=60.0,
                max_retries=2
            )
            
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7,
                max_tokens=1000
            )
            
            content = response.choices[0].message.content if response.choices else ""
            
        elif provider.lower() == "anthropic":
            try:
                import anthropic
                import config.settings as config
                
                if not config.ANTHROPIC_API_KEY:
                    return "Expert help unavailable: Anthropic API key not configured."
                
                client = anthropic.Anthropic(
                    api_key=config.ANTHROPIC_API_KEY,
                    timeout=60.0,
                    max_retries=2
                )
                
                response = client.messages.create(
                    model=model,
                    max_tokens=1000,
                    temperature=0.7,
                    messages=messages
                )
                
                content = response.content[0].text if response.content else ""
                
            except ImportError:
                return "Expert help unavailable: Anthropic library not installed."
            
        elif provider.lower() == "gemini":
            try:
                import google.generativeai as genai
                import config.settings as config
                
                if not config.GOOGLE_API_KEY:
                    return "Expert help unavailable: Google API key not configured."
                
                genai.configure(api_key=config.GOOGLE_API_KEY)
                model_instance = genai.GenerativeModel(model)
                
                # Convert messages to Gemini format
                prompt = messages[0]["content"] if messages else question
                response = model_instance.generate_content(prompt)
                
                content = response.text if response.text else ""
                
            except ImportError:
                return "Expert help unavailable: Google Generative AI library not installed."
            
        else:
            return f"Expert help unavailable: Unsupported provider '{provider}'."
        
        response = content
        
        expert_response_data = {"content_length": len(response) if response else 0}
        local_logger.bind(interface="llm", direction="response", data=expert_response_data).debug("Received from Expert LLM")
        
        if not response:
            return "Expert help received an empty response. Please try rephrasing your question."
        
        local_logger.info(f"Expert help completed successfully for request_id: {request_id}")
        return response
        
    except Exception as e:
        local_logger.error(f"Error getting expert help: {e}", exc_info=True)
        expert_error_data = {"error": str(e)}
        local_logger.bind(interface="llm", direction="response", data=expert_error_data).debug("Received error from Expert LLM")
        return f"Expert help encountered an error: {e}. Please try again or ask your question directly."
