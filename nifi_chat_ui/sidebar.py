"""
Sidebar UI components for NiFi Chat UI.
"""

import streamlit as st
from typing import Dict, Any, Tuple, Optional
from loguru import logger

from mcp_handler import get_available_tools, get_nifi_servers
from st_copy_to_clipboard import st_copy_to_clipboard


def render_sidebar(config) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Render the complete sidebar and return selected provider, model, and workflow."""
    
    st.sidebar.title("Settings")
    
    # Model selection
    provider, model_name = render_model_selection(config)
    
    # NiFi server selection
    render_nifi_server_selection()
    
    # Workflow selection
    selected_workflow = render_workflow_selection()
    
    # Tool display
    render_tool_display()
    
    # Execution settings
    render_execution_settings()
    
    # History management
    render_history_management()
    
    return provider, model_name, selected_workflow


def render_model_selection(config) -> Tuple[Optional[str], Optional[str]]:
    """Render model selection UI."""
    st.markdown("---")
    
    # Build available models dictionary
    available_models = {}
    if config.OPENAI_API_KEY and config.OPENAI_MODELS:
        for model in config.OPENAI_MODELS:
            available_models[f"OpenAI: {model}"] = ("openai", model)
    if config.GOOGLE_API_KEY and config.GEMINI_MODELS:
        for model in config.GEMINI_MODELS:
            available_models[f"Google: {model}"] = ("gemini", model)
    if config.PERPLEXITY_API_KEY and config.PERPLEXITY_MODELS:
        for model in config.PERPLEXITY_MODELS:
            available_models[f"Perplexity: {model}"] = ("perplexity", model)
    if config.ANTHROPIC_API_KEY and config.ANTHROPIC_MODELS:
        for model in config.ANTHROPIC_MODELS:
            available_models[f"Anthropic: {model}"] = ("anthropic", model)
    
    provider = None
    model_name = None
    
    if not available_models:
        st.sidebar.error("No LLM models configured or API keys missing. Please check your .env file.")
    else:
        # Determine default selection
        default_selection = list(available_models.keys())[0]
        default_index = 0
        
        selected_model_display_name = st.sidebar.selectbox(
            "Select LLM Model:", 
            list(available_models.keys()), 
            index=default_index,
            key="model_select"
        )
        
        if selected_model_display_name:
            provider, model_name = available_models[selected_model_display_name]
            logger.debug(f"Selected Model: {model_name}, Provider: {provider}")
            
            # Check if provider changed and reconfigure if needed
            previous_provider = st.session_state.get("previous_provider", None)
            if provider != previous_provider:
                try:
                    st.session_state["previous_provider"] = provider
                    logger.info(f"Provider context changed to {provider} based on model selection.")
                except Exception as e:
                    logger.error(f"Error during potential reconfiguration for {provider}: {e}", exc_info=True)
        else:
            st.sidebar.error("No model selected.")
    
    return provider, model_name


def render_nifi_server_selection() -> None:
    """Render NiFi server selection UI."""
    st.markdown("---")
    
    nifi_servers = st.session_state.get("nifi_servers", [])
    if nifi_servers:
        server_options = {server["id"]: server["name"] for server in nifi_servers}
        
        # Function to update the selected server ID in session state
        def on_server_change():
            selected_id = st.session_state.nifi_server_selector
            if st.session_state.selected_nifi_server_id != selected_id:
                st.session_state.selected_nifi_server_id = selected_id
                logger.info(f"User selected NiFi Server: ID={selected_id}, Name={server_options.get(selected_id, 'Unknown')}")
        
        # Determine the index of the currently selected server for the selectbox default
        current_selected_id = st.session_state.get("selected_nifi_server_id")
        server_ids = list(server_options.keys())
        try:
            default_server_index = server_ids.index(current_selected_id) if current_selected_id in server_ids else 0
        except ValueError:
            default_server_index = 0

        st.sidebar.selectbox(
            "Target NiFi Server:",
            options=server_ids,
            format_func=lambda server_id: server_options.get(server_id, server_id),
            key="nifi_server_selector",
            index=default_server_index,
            on_change=on_server_change,
            help="Select the NiFi instance to interact with."
        )
    else:
        st.sidebar.warning("No NiFi servers configured or reachable on the backend.")


def render_workflow_selection() -> Optional[str]:
    """Render workflow selection UI."""
    st.markdown("---")
    
    # Initialize workflow session state
    if "selected_workflow" not in st.session_state:
        st.session_state.selected_workflow = None
    if "available_workflows" not in st.session_state:
        st.session_state.available_workflows = []
    
    try:
        # Get available workflows from the workflow registry
        from nifi_mcp_server.workflows.registry import get_workflow_registry
        registry = get_workflow_registry()
        workflows = registry.list_workflows(enabled_only=True)
        
        if workflows:
            # Create workflow options for the selectbox
            workflow_options = {}
            for workflow in workflows:
                display_name = f"{workflow.display_name}"
                if workflow.is_async:
                    display_name += " (Real-time)"
                workflow_options[display_name] = workflow.name
            
            # Update available workflows in session state
            st.session_state.available_workflows = list(workflow_options.keys())
            
            # Workflow selection
            selected_workflow_display = st.sidebar.selectbox(
                "Select Workflow:",
                list(workflow_options.keys()),
                help="Choose a guided workflow for structured execution"
            )
            
            # Update selected workflow in session state
            if selected_workflow_display:
                st.session_state.selected_workflow = workflow_options[selected_workflow_display]
                
                # Show workflow description
                selected_workflow_def = registry.get_workflow(st.session_state.selected_workflow)
                if selected_workflow_def:
                    st.sidebar.caption(f"📋 {selected_workflow_def.description}")
                    
                    # Show workflow phases
                    if selected_workflow_def.phases and selected_workflow_def.phases != ["All"]:
                        phases_text = ", ".join(selected_workflow_def.phases)
                        st.sidebar.caption(f"🔧 Phases: {phases_text}")
                
                return st.session_state.selected_workflow
        else:
            st.sidebar.warning("No workflows available or workflow system not configured")
            st.session_state.available_workflows = []
            st.session_state.selected_workflow = None
            
    except Exception as e:
        logger.error(f"Error loading workflows: {e}", exc_info=True)
        st.sidebar.error(f"Error loading workflows: {e}")
        st.session_state.available_workflows = []
        st.session_state.selected_workflow = None
    
    return None


def render_tool_display() -> None:
    """Render tool display UI."""
    st.markdown("---")
    st.sidebar.subheader("Available MCP Tools")
    
    # Fetch tools based on the current value of the selectbox for sidebar display
    current_sidebar_phase = st.session_state.get("selected_phase", "All")
    current_nifi_server_id_for_sidebar = st.session_state.get("selected_nifi_server_id")
    raw_tools_list = get_available_tools(
        phase=current_sidebar_phase, 
        selected_nifi_server_id=current_nifi_server_id_for_sidebar
    )
    
    if raw_tools_list:
        for tool_data in raw_tools_list: 
            if not isinstance(tool_data, dict) or tool_data.get("type") != "function" or not isinstance(tool_data.get("function"), dict):
                st.sidebar.warning(f"Skipping unexpected tool data format: {tool_data}")
                continue
            function_details = tool_data.get("function", {})
            tool_name = function_details.get('name', 'Unnamed Tool')
            tool_description = function_details.get('description', 'No description')
            parameters = function_details.get('parameters', {})
            properties = parameters.get('properties', {}) if isinstance(parameters, dict) else {}
            required_params = parameters.get('required', []) if isinstance(parameters, dict) else []
            
            with st.sidebar.expander(f"🔧 {tool_name}", expanded=False):
                st.markdown(f"**Description:** {tool_description}")
                if properties:
                    st.markdown("**Parameters:**")
                    for param_name, param_info in properties.items():
                        required = "✳️ " if param_name in required_params else ""
                        desc_lines = param_info.get('description', 'No description').split('\n')
                        first_line = desc_lines[0]
                        other_lines = desc_lines[1:]
                        st.markdown(f"- {required}`{param_name}`: {first_line}")
                        if other_lines:
                            col1, col2 = st.columns([0.05, 0.95])
                            with col2:
                                for line in other_lines:
                                    st.markdown(f"{line.strip()}")
                else:
                    st.markdown("_(No parameters specified)_")
    else:
        st.sidebar.warning("No MCP tools available or failed to retrieve.")


def render_execution_settings() -> None:
    """Render execution settings UI."""
    st.markdown("---")
    st.sidebar.subheader("Execution Settings")
    
    # Auto Prune Settings
    st.sidebar.checkbox(
        "Auto-prune History", 
        key="auto_prune_history",
        help="If checked, automatically remove older messages from the context sent to the LLM to stay below the token limit. The full history remains visible in the chat UI."
    )
    st.sidebar.selectbox(
        "Max Tokens Limit (approx)", 
        options=[4000, 8000, 16000, 32000, 64000],
        key="max_tokens_limit",
        help="The maximum approximate number of tokens to include in the request to the LLM when auto-pruning is enabled."
    )
    
    st.sidebar.number_input(
        "Max Actions per Request",
        min_value=1,
        max_value=20,
        value=10,
        key="max_loop_iterations",
        help="Maximum number of LLM actions before stopping execution"
    )


def render_history_management() -> None:
    """Render history management UI."""
    st.markdown("---")
    
    # Clear chat history
    def clear_chat_callback():
        st.session_state.messages = []
        st.session_state.current_objective = ""
        st.session_state.selected_phase = "All"
        st.session_state.conversation_total_tokens_in = 0
        st.session_state.conversation_total_tokens_out = 0
        st.session_state.conversation_total_duration = 0.0
        st.session_state.last_request_tokens_in = 0
        st.session_state.last_request_tokens_out = 0
        st.session_state.last_request_duration = 0.0
        logger.info("Chat history, objective, phase, and usage tracking cleared by user.")

    if st.sidebar.button("Clear Chat History", on_click=clear_chat_callback):
        pass
    
    # Copy conversation
    st.markdown("---")
    if st.sidebar.button("Prepare Full Conversation For Copy", key="copy_conv_btn"):
        from chat_display import ChatDisplay
        from event_handler import EventHandler
        
        # Create temporary instances for formatting
        event_handler = EventHandler()
        chat_display = ChatDisplay(event_handler)
        
        conversation_text = chat_display.format_conversation_for_copy(
            st.session_state.get("current_objective", ""),
            st.session_state.get("messages", [])
        )
        if conversation_text:
            st_copy_to_clipboard(conversation_text, key="copy_full_conversation_clipboard")
            st.sidebar.success("Click icon to copy full conversation to clipboard!")
        else:
            st.sidebar.warning("Nothing to copy.")
