import streamlit as st
import json # Import json for formatting tool results
import uuid # Added for context IDs
import time # Added for timing tracking
from loguru import logger # Import logger directly
from st_copy_to_clipboard import st_copy_to_clipboard # Import the new component
from typing import List, Dict
from nifi_chat_ui.llm.chat_manager import ChatManager  # Import for smart pruning

# Set page config MUST be the first Streamlit call
st.set_page_config(page_title="NiFi Chat UI", layout="wide")

# --- Helper Function for Formatting Conversation --- 
def format_conversation_for_copy(objective, messages):
    """Formats the objective and chat messages into a single string for copying."""
    lines = []
    if objective and objective.strip():
        lines.append("## Overall Objective:")
        lines.append(objective.strip())
        lines.append("\n---")

    lines.append("## Conversation History:")
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        
        if role == "user" and content:
            lines.append(f"\n**User:**\n{content}")
        elif role == "assistant" and content:
            lines.append(f"\n**Assistant:**\n{content}")
        elif role == "assistant" and msg.get("tool_calls"):
            tool_names = [tc.get('function', {}).get('name', 'unknown') for tc in msg.get("tool_calls", [])]
            lines.append(f"\n**Assistant:** ⚙️ Calling tool(s): `{', '.join(tool_names)}`...")
        # Add other roles or filter as needed
        
    return "\n".join(lines)
# ---------------------------------------------

# --- Setup Logging --- 
try:
    # Use session state to ensure logging is only set up once per session, not on every script re-run
    if "logging_initialized" not in st.session_state:
        from config.logging_setup import setup_logging
        setup_logging(context='client')
        st.session_state.logging_initialized = True
        logger.info("Logging initialized for new session")
    else:
        logger.debug("Logging already initialized for this session")
except ImportError:
    print("Warning: Logging setup failed. Check config/logging_setup.py")
# ---------------------

# Restore imports
from nifi_chat_ui.llm.chat_manager import ChatManager
from nifi_chat_ui.llm.utils.token_counter import TokenCounter
from nifi_chat_ui.mcp_handler import get_available_tools, execute_mcp_tool, get_nifi_servers
# Import config from the new location
try:
    # Add parent directory to Python path so we can import config
    import sys
    import os
    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    
    from config import settings as config # Updated import
except ImportError:
    logger.error("Failed to import config.settings. Ensure config/__init__.py exists if needed, or check PYTHONPATH.")
    st.error("Configuration loading failed. Application cannot start.")
    st.stop()

# Initialize ChatManager and TokenCounter after page config
if "chat_manager" not in st.session_state:
    logger.info("Initializing ChatManager...")
    config_dict = {
        'openai': {
            'api_key': config.OPENAI_API_KEY,
            'models': config.OPENAI_MODELS
        },
        'gemini': {
            'api_key': config.GOOGLE_API_KEY,
            'models': config.GEMINI_MODELS
        },
        'anthropic': {
            'api_key': config.ANTHROPIC_API_KEY,
            'models': config.ANTHROPIC_MODELS
        },
        'perplexity': {
            'api_key': config.PERPLEXITY_API_KEY,
            'models': config.PERPLEXITY_MODELS
        },
        # Add flat keys for Gemini for compatibility
        'GOOGLE_API_KEY': config.GOOGLE_API_KEY,
        'GEMINI_MODELS': config.GEMINI_MODELS,
        'GEMINI_DEFAULT_MODEL': getattr(config, 'GEMINI_DEFAULT_MODEL', 'gemini-1.5-pro'),
    }
    st.session_state.chat_manager = ChatManager(config_dict)
    st.session_state.token_counter = TokenCounter()
    logger.info("ChatManager and TokenCounter initialized")

# --- Constants ---
MAX_LOOP_ITERATIONS = 10 # Safety break for the execution loop
SYSTEM_PROMPT_FILE = "nifi_chat_ui/system_prompt.md"

# --- Session State Initialization ---
# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = [] 

# Initialize objective
if "current_objective" not in st.session_state:
    st.session_state.current_objective = ""
    
# Initialize auto-pruning settings
if "auto_prune_history" not in st.session_state:
    st.session_state.auto_prune_history = False # Default to off
if "max_tokens_limit" not in st.session_state:
    st.session_state.max_tokens_limit = 16000 # Default token limit

# Initialize execution control state
if "llm_executing" not in st.session_state:
    st.session_state.llm_executing = False
if "stop_requested" not in st.session_state:
    st.session_state.stop_requested = False
if "pending_execution" not in st.session_state:
    st.session_state.pending_execution = None

# Initialize input counter for clearing input field
if "input_counter" not in st.session_state:
    st.session_state.input_counter = 0

# Initialize token and timing tracking
if "conversation_total_tokens_in" not in st.session_state:
    st.session_state.conversation_total_tokens_in = 0
if "conversation_total_tokens_out" not in st.session_state:
    st.session_state.conversation_total_tokens_out = 0
if "conversation_total_duration" not in st.session_state:
    st.session_state.conversation_total_duration = 0.0
if "last_request_tokens_in" not in st.session_state:
    st.session_state.last_request_tokens_in = 0
if "last_request_tokens_out" not in st.session_state:
    st.session_state.last_request_tokens_out = 0
if "last_request_duration" not in st.session_state:
    st.session_state.last_request_duration = 0.0

# --- Fetch NiFi Servers (once per session) ---
if "nifi_servers" not in st.session_state:
    st.session_state.nifi_servers = get_nifi_servers()
    if not st.session_state.nifi_servers:
        logger.warning("Failed to retrieve NiFi server list from backend, or no servers configured.")
    else:
        logger.info(f"Retrieved {len(st.session_state.nifi_servers)} NiFi server configurations.")

if "selected_nifi_server_id" not in st.session_state:
    # Default to the first server's ID if available, otherwise None
    st.session_state.selected_nifi_server_id = st.session_state.nifi_servers[0]["id"] if st.session_state.nifi_servers else None
    logger.info(f"Initial NiFi server selection set to: {st.session_state.selected_nifi_server_id}")
# ---------------------------------------------

# --- Load System Prompt --- 
def load_system_prompt():
    try:
        with open(SYSTEM_PROMPT_FILE, "r") as f:
            return f.read()
    except FileNotFoundError:
        st.error(f"Error: System prompt file not found at {SYSTEM_PROMPT_FILE}")
        return "You are a helpful NiFi assistant." # Fallback prompt
    except Exception as e:
        st.error(f"Error reading system prompt file: {e}")
        return "You are a helpful NiFi assistant."

# Store the base prompt loaded from the file
base_system_prompt = load_system_prompt()

# --- Sidebar --- 
with st.sidebar:
    st.title("Settings")
    
    # --- Model Selection (Combined) --- 
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
    
    selected_model_display_name = None
    provider = None # Will be derived from selection
    model_name = None # Will be derived from selection

    if not available_models:
        st.error("No LLM models configured or API keys missing. Please check your .env file.")
    else:
        # Determine default selection
        # Prioritize Gemini if available, otherwise first OpenAI, else first in list
        default_selection = None
        if config.GOOGLE_API_KEY and config.GEMINI_MODELS:
            default_selection = f"Google: {config.GEMINI_MODELS[0]}"
        elif config.OPENAI_API_KEY and config.OPENAI_MODELS:
             default_selection = f"OpenAI: {config.OPENAI_MODELS[0]}"
        
        # Ensure default is actually in the list
        if default_selection not in available_models:
            default_selection = available_models[0] # Fallback to first item
        
        default_index = list(available_models.keys()).index(default_selection)
        
        selected_model_display_name = st.selectbox(
            "Select LLM Model:", 
            list(available_models.keys()), 
            index=default_index,
            key="model_select" # Use a key to track selection
        )
        
        if selected_model_display_name:
            provider, model_name = available_models[selected_model_display_name]
            logger.debug(f"Selected Model: {model_name}, Provider: {provider}")
            
            # Check if provider changed and reconfigure if needed
            # (Potentially less critical now if configuration is robust)
            previous_provider = st.session_state.get("previous_provider", None)
            if provider != previous_provider:
                try:
                    # configure_llms() # Re-configure only if necessary, e.g., client state management
                    st.session_state["previous_provider"] = provider
                    logger.info(f"Provider context changed to {provider} based on model selection.")
                except Exception as e:
                    logger.error(f"Error during potential reconfiguration for {provider}: {e}", exc_info=True)
                    # st.sidebar.warning(...) # Optional warning if reconfiguration fails
        else:
             # Handle case where selection somehow becomes None (shouldn't happen with selectbox)
             st.error("No model selected.")

    # --- NiFi Server Selection --- #
    st.markdown("---") # Add separator
    nifi_servers = st.session_state.get("nifi_servers", [])
    if nifi_servers:
        server_options = {server["id"]: server["name"] for server in nifi_servers}
        
        # Function to update the selected server ID in session state
        def on_server_change():
            selected_id = st.session_state.nifi_server_selector # Get value from widget's key
            if st.session_state.selected_nifi_server_id != selected_id:
                st.session_state.selected_nifi_server_id = selected_id
                logger.info(f"User selected NiFi Server: ID={selected_id}, Name={server_options.get(selected_id, 'Unknown')}")
                # Optionally trigger a rerun or other actions if needed upon change
                # st.rerun() 
        
        # Determine the index of the currently selected server for the selectbox default
        current_selected_id = st.session_state.get("selected_nifi_server_id")
        server_ids = list(server_options.keys())
        try:
            default_server_index = server_ids.index(current_selected_id) if current_selected_id in server_ids else 0
        except ValueError:
            default_server_index = 0 # Default to first if current selection is invalid

        st.selectbox(
            "Target NiFi Server:",
            options=server_ids, # Use IDs as the actual option values
            format_func=lambda server_id: server_options.get(server_id, server_id), # Show names in dropdown
            key="nifi_server_selector", # Use a specific key for the widget itself
            index=default_server_index,
            on_change=on_server_change,
            help="Select the NiFi instance to interact with."
        )
    else:
        st.warning("No NiFi servers configured or reachable on the backend.")
    # ----------------------------- #

    # --- Workflow Configuration --- 
    st.markdown("---") # Add separator
    
    # Initialize workflow session state
    if "workflow_execution_mode" not in st.session_state:
        st.session_state.workflow_execution_mode = "unguided"  # Default to unguided
    if "selected_workflow" not in st.session_state:
        st.session_state.selected_workflow = None
    if "available_workflows" not in st.session_state:
        st.session_state.available_workflows = []
    
    # Execution Mode Selection
    execution_modes = ["unguided", "guided"]
    current_mode_index = execution_modes.index(st.session_state.workflow_execution_mode) if st.session_state.workflow_execution_mode in execution_modes else 0
    
    def on_execution_mode_change():
        new_mode = st.session_state.execution_mode_selector
        if st.session_state.workflow_execution_mode != new_mode:
            st.session_state.workflow_execution_mode = new_mode
            # Reset workflow selection when mode changes
            st.session_state.selected_workflow = None
            logger.info(f"Workflow execution mode changed to: {new_mode}")
    
    st.selectbox(
        "Execution Mode:",
        options=execution_modes,
        index=current_mode_index,
        key="execution_mode_selector",
        on_change=on_execution_mode_change,
        help="Select workflow execution mode: 'unguided' for free-form chat, 'guided' for structured workflows"
    )
    
    # === WORKFLOW SELECTION (for guided mode) ===
    if st.session_state.workflow_execution_mode == "guided":
        st.markdown("---")
        
        # Get available workflows
        try:
            from nifi_mcp_server.workflows.registry import get_workflow_registry
            registry = get_workflow_registry()
            available_workflows = registry.list_workflows(enabled_only=True)
            
            if available_workflows:
                workflow_options = {wf.name: wf.display_name for wf in available_workflows}
                workflow_names = list(workflow_options.keys())
                
                # Determine current selection
                current_workflow = st.session_state.get("selected_workflow")
                if current_workflow in workflow_names:
                    default_index = workflow_names.index(current_workflow)
                else:
                    default_index = 0
                    st.session_state.selected_workflow = workflow_names[0] if workflow_names else None
                
                def on_workflow_change():
                    new_workflow = st.session_state.workflow_selector
                    if st.session_state.selected_workflow != new_workflow:
                        st.session_state.selected_workflow = new_workflow
                        logger.info(f"Selected workflow changed to: {new_workflow}")
                
                st.selectbox(
                    "Select Workflow:",
                    options=workflow_names,
                    format_func=lambda x: workflow_options.get(x, x),
                    index=default_index,
                    key="workflow_selector",
                    on_change=on_workflow_change,
                    help="Choose the guided workflow to execute"
                )
                
                # Show workflow description
                if st.session_state.selected_workflow:
                    selected_wf = next((wf for wf in available_workflows if wf.name == st.session_state.selected_workflow), None)
                    if selected_wf:
                        # Add special indicator for async workflows
                        async_indicator = ""
                        if selected_wf.name == "async_unguided_mimic":
                            async_indicator = " ⚡ **Real-time updates enabled**"
                        
                        st.info(f"**{selected_wf.display_name}**: {selected_wf.description}{async_indicator}")
            else:
                st.warning("No guided workflows available.")
                st.session_state.selected_workflow = None
                
        except Exception as e:
            st.error(f"Failed to load workflows: {e}")
            st.session_state.selected_workflow = None
    
    # === PHASE SELECTION ===
    st.markdown("---") # Add separator
    phase_options = ["All", "Review", "Build", "Modify", "Operate"]
    # Initialize session state for selected_phase if it doesn't exist
    if "selected_phase" not in st.session_state:
        st.session_state.selected_phase = "All"
    
    st.selectbox(
        "Tool Phase Filter:",
        phase_options,
        key="selected_phase", # Bind directly to session state key
        help="Filter the tools shown below and available to the LLM by operational phase."
    )
    
    # --- Tool Display --- 
    st.markdown("---")
    st.subheader("Available MCP Tools")
    # Fetch tools based on the *current* value of the selectbox for sidebar display
    current_sidebar_phase = st.session_state.get("selected_phase", "All") # Read current value via key
    current_nifi_server_id_for_sidebar = st.session_state.get("selected_nifi_server_id") # Get selected server ID
    raw_tools_list = get_available_tools(
        phase=current_sidebar_phase, 
        selected_nifi_server_id=current_nifi_server_id_for_sidebar # Pass server ID
    )
    
    if raw_tools_list:
        for tool_data in raw_tools_list: 
            # No filtering needed here anymore, backend does it.
            if not isinstance(tool_data, dict) or tool_data.get("type") != "function" or not isinstance(tool_data.get("function"), dict):
                st.warning(f"Skipping unexpected tool data format: {tool_data}")
                continue
            function_details = tool_data.get("function", {})
            tool_name = function_details.get('name', 'Unnamed Tool')
            tool_description = function_details.get('description', 'No description')
            parameters = function_details.get('parameters', {}) # parameters schema
            properties = parameters.get('properties', {}) if isinstance(parameters, dict) else {}
            required_params = parameters.get('required', []) if isinstance(parameters, dict) else []
            
            with st.expander(f"🔧 {tool_name}", expanded=False):
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
         st.warning("No MCP tools available or failed to retrieve.")
         
    # --- Execution Settings ---
    st.markdown("---") # Separator
    st.subheader("Execution Settings")
    
    # Auto Prune Settings (moved under Execution Settings)
    st.checkbox(
        "Auto-prune History", 
        key="auto_prune_history", # Binds to session state
        help="If checked, automatically remove older messages from the context sent to the LLM to stay below the token limit. The full history remains visible in the chat UI."
    )
    st.selectbox(
        "Max Tokens Limit (approx)", 
        options=[4000, 8000, 16000, 32000, 64000],
        key="max_tokens_limit", # Binds to session state
        help="The maximum approximate number of tokens to include in the request to the LLM when auto-pruning is enabled."
    )
    
    st.number_input(
        "Max Actions per Request",
        min_value=1,
        max_value=20,
        value=MAX_LOOP_ITERATIONS,
        key="max_loop_iterations",
        help="Maximum number of LLM actions before stopping execution"
    )
    # --------------------------

    # --- History Clear --- 
    def clear_chat_callback():
        st.session_state.messages = []
        # Clear objective and flags as well
        st.session_state.current_objective = ""
        # st.session_state.run_recovery_loop = False # Removed
        # st.session_state.history_cleared_for_next_llm_call = False # Removed
        # Reset phase to default
        st.session_state.selected_phase = "All" # Reset phase on clear
        # Reset token and timing tracking
        st.session_state.conversation_total_tokens_in = 0
        st.session_state.conversation_total_tokens_out = 0
        st.session_state.conversation_total_duration = 0.0
        st.session_state.last_request_tokens_in = 0
        st.session_state.last_request_tokens_out = 0
        st.session_state.last_request_duration = 0.0
        logger.info("Chat history, objective, phase, and usage tracking cleared by user.")
        # No st.rerun() needed here, Streamlit handles it after callback

    if st.sidebar.button("Clear Chat History", on_click=clear_chat_callback):
        # Logic moved to the callback function
        pass
        # logger.info("Chat history and objective cleared by user.") # Logging moved to callback
        # st.rerun() # Not needed when using on_click

    # --- Copy Conversation Button --- 
    st.markdown("---") # Separator
    if st.sidebar.button("Prepare Full Conversation For Copy", key="copy_conv_btn"):
        conversation_text = format_conversation_for_copy(
            st.session_state.get("current_objective", ""),
            st.session_state.get("messages", [])
        )
        if conversation_text:
            st_copy_to_clipboard(conversation_text, key="copy_full_conversation_clipboard")
            st.sidebar.success("Click icon to copy full conversation to clipboard!")
        else:
            st.sidebar.warning("Nothing to copy.")
    # -------------------------------

# --- Main Chat Interface --- 
st.title("NiFi Chat UI")

# --- Objective Input Panel --- 
st.text_area(
    "Define the overall objective for this session:", 
    key="objective_input",
    value=st.session_state.current_objective, 
    height=100,
    on_change=lambda: st.session_state.update(current_objective=st.session_state.objective_input) 
)
st.markdown("---")

# --- Display Chat History --- 
for index, message in enumerate(st.session_state.messages):
    if message.get("role") == "tool":
        continue
    # Removed system message check for HISTORY_CLEAR_MARKER
    # elif message.get("role") == "system" and message.get("content") == HISTORY_CLEAR_MARKER:
    #     st.info(HISTORY_CLEAR_MARKER)
    #     continue
        
    with st.chat_message(message["role"]):
        # Display content or tool call placeholder
        if message["role"] == "assistant" and "tool_calls" in message:
            tool_names = [tc.get('function', {}).get('name', 'unknown') for tc in message.get("tool_calls", [])]
            if tool_names:
                st.markdown(f"⚙️ Calling tool(s): `{', '.join(tool_names)}`...")
            else:
                st.markdown("_(Tool call with no details)_")
        elif "content" in message:
            st.markdown(message["content"])
            # Replace st.code with st_copy_to_clipboard
            # Add a unique key based on message content/role/potentially ID if available
            # For simplicity now, using content hash might work, but let's try a simple key first
            # A better key might involve an action_id or index if reliably available
            # Using content as part of key for now, might need adjustment
            # content_key = hash(message['content']) # Simple hash for uniqueness
            # st_copy_to_clipboard(message["content"], key=f"hist_copy_{content_key}")
            # --- Generate Unique Key ---
            unique_part = ""
            if message["role"] == "user":
                unique_part = message.get("user_request_id", "")
            elif message["role"] == "assistant":
                 unique_part = message.get("action_id", "")
            # Use index and the ID (or just index if ID is missing)
            copy_key = f"hist_copy_{index}_{unique_part}"
            # ---------------------------

            st_copy_to_clipboard(message["content"], key=copy_key) # Use the new unique key
        elif message["role"] == "assistant" and "tool_calls" in message and "content" not in message:
            tool_names = [tc.get('function', {}).get('name', 'unknown') for tc in message.get("tool_calls", [])]
            if tool_names:
                st.markdown(f"⚙️ Calling tool(s): `{', '.join(tool_names)}`...")
            else:
                st.markdown("_(Tool call with no details)_")
                
        # Display captions (Tokens, IDs)
        caption_parts = []
        if message["role"] == "user":
            user_req_id = message.get("user_request_id")
            if user_req_id:
                caption_parts.append(f"Request ID: `{user_req_id}`")
        elif message["role"] == "assistant":
            token_count_in = message.get("token_count_in", 0)
            token_count_out = message.get("token_count_out", 0)
            action_id = message.get("action_id")
            if token_count_in or token_count_out:
                caption_parts.append(f"Tokens: In={token_count_in}, Out={token_count_out}")
            if action_id:
                caption_parts.append(f"Action ID: `{action_id}`")
                
        if caption_parts:
            st.caption(" | ".join(caption_parts))

# --- Status Report Function ---
def send_status_report(provider: str, model_name: str, system_prompt: str, 
                      context_messages: list, user_req_id: str, actions_taken: int, bound_logger):
    """Send a hidden prompt to get status report from LLM when action limit is reached."""
    
    # Create hidden status prompt
    status_prompt = f"""
I've reached the maximum action limit ({actions_taken} actions) for this request. 
Please provide a brief status report:

1. What have I accomplished so far?
2. What challenges or difficulties did I encounter?
3. What would be my next planned actions if I could continue?
4. Any important notes or recommendations for the user?

Keep this concise but informative.
"""
    
    # Create temporary message history for status report
    status_messages = context_messages + [
        {"role": "user", "content": status_prompt}
    ]
    
    try:
        bound_logger.info("Requesting automated status report from LLM")
        
        # Call LLM for status report (no tools needed)
        chat_manager = st.session_state.get("chat_manager")
        if chat_manager:
            status_response = chat_manager.get_llm_response(
                messages=status_messages,
                system_prompt=system_prompt,
                provider=provider,
                model_name=model_name,
                user_request_id=user_req_id,
                selected_nifi_server_id=st.session_state.get("selected_nifi_server_id")
            )
        else:
            status_response = {"error": "ChatManager not available"}
        
        if status_response and status_response.get("content"):
            # Display the status report
            with st.chat_message("assistant"):
                st.markdown("### 📋 Status Report")
                st.markdown(status_response["content"])
            
            # Add to history (mark as status report for potential removal)
            status_tokens_in = status_response.get("token_count_in", 0)
            status_tokens_out = status_response.get("token_count_out", 0)
            status_message = {
                "role": "assistant",
                "content": f"### 📋 Status Report\n\n{status_response['content']}",
                "is_status_report": True,
                "token_count_in": status_tokens_in,
                "token_count_out": status_tokens_out,
                "action_id": str(uuid.uuid4())
            }
            
            st.session_state.messages.append(status_message)
            
            # Update session totals with status report tokens
            st.session_state.conversation_total_tokens_in += status_tokens_in
            st.session_state.conversation_total_tokens_out += status_tokens_out
            
            bound_logger.info("Status report generated and displayed successfully")
            
            # Optional: Remove from history if configured to do so
            # Status reports are kept in history (hide_status_reports = False by default)
            # if st.session_state.get("hide_status_reports", False):
            #     # We'll remove it after a delay or on next interaction
            #     # For now, just mark it for potential removal
            #     pass
            
    except Exception as e:
        bound_logger.error(f"Failed to generate status report: {e}", exc_info=True)
        # Fail silently - status report is optional
        bound_logger.info("Status report failed, but continuing normally")

# --- Workflow Execution Function ---
def run_async_workflow_integrated(workflow_name: str, provider: str, model_name: str, base_sys_prompt: str, user_req_id: str):
    """Run an async workflow with integrated real-time UI updates in the main chat interface."""
    import asyncio
    import threading
    import time
    from nifi_mcp_server.workflows.core.event_system import get_event_emitter, EventTypes
    from nifi_mcp_server.workflows.registry import get_workflow_registry
    
    bound_logger = logger.bind(user_request_id=user_req_id)
    execution_start_time = time.time()
    
    try:
        # Display workflow start message
        with st.chat_message("assistant"):
            st.markdown(f"🚀 **Starting async workflow:** {workflow_name}")
            st.markdown("*Real-time updates will appear below...*")
        
        st.session_state.messages.append({
            "role": "assistant",
            "content": f"🚀 **Starting async workflow:** {workflow_name}\n\n*Real-time updates will appear below...*"
        })
        
        # Create real-time UI containers
        progress_container = st.empty()
        status_container = st.empty()
        
        # Prepare execution context (same as sync version)
        current_objective = st.session_state.get("current_objective", "")
        if current_objective and current_objective.strip():
            effective_system_prompt = f"{base_sys_prompt}\n\n## Current Objective\n{current_objective.strip()}"
        else:
            effective_system_prompt = base_sys_prompt
        
        # Filter messages (same as sync version)
        filtered_messages = []
        for msg in st.session_state.messages:
            if (msg.get("role") == "assistant" and 
                msg.get("content", "").startswith("🚀 **Starting async workflow:")):
                continue
            filtered_messages.append(msg)
        
        context = {
            "provider": provider,
            "model_name": model_name,
            "system_prompt": effective_system_prompt,
            "user_request_id": user_req_id,
            "messages": filtered_messages,
            "selected_nifi_server_id": st.session_state.get("selected_nifi_server_id"),
            "selected_phase": st.session_state.get("selected_phase", "All"),
            "max_loop_iterations": st.session_state.get("max_loop_iterations", 10),
            "max_tokens_limit": st.session_state.get("max_tokens_limit", 8000),
            "auto_prune_history": st.session_state.get("auto_prune_history", False),
            "current_objective": current_objective
        }
        
        # Create async executor
        registry = get_workflow_registry()
        async_executor = registry.create_async_executor(workflow_name)
        
        if not async_executor:
            st.error(f"Failed to create async executor for workflow: {workflow_name}")
            return
        
        # Set up event handling for real-time UI updates
        events_received = []
        event_emitter = get_event_emitter()
        
        def handle_workflow_event(event):
            """Handle workflow events - just collect them for main thread processing."""
            events_received.append(event)
        
        # Register event handler
        event_emitter.on(handle_workflow_event)
        
        # Execute workflow asynchronously in background thread
        result_container = {"result": None, "error": None, "completed": False}
        
        def run_async_workflow():
            """Run the async workflow in a separate thread."""
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(async_executor.execute_async(context))
                result_container["result"] = result
                result_container["completed"] = True
            except Exception as e:
                result_container["error"] = str(e)
                result_container["completed"] = True
            finally:
                loop.close()
        
        # Start async execution in background thread
        workflow_thread = threading.Thread(target=run_async_workflow)
        workflow_thread.start()
        
        # Poll for completion with progress updates
        progress_bar = progress_container.progress(0)
        
        start_time = time.time()
        max_wait_time = 300  # 5 minutes max
        
        while not result_container["completed"] and (time.time() - start_time) < max_wait_time:
            elapsed = time.time() - start_time
            progress = min(elapsed / max_wait_time, 0.95)  # Never show 100% until complete
            
            # Update progress bar with event count
            progress_bar.progress(progress, text=f"Executing workflow... ({len(events_received)} events)")
            
            # Show latest status based on most recent events
            if events_received:
                latest_event = events_received[-1]
                
                with status_container:
                    if latest_event.event_type == EventTypes.LLM_START:
                        provider_name = latest_event.data.get('provider', 'Unknown')
                        model_name = latest_event.data.get('model', 'Unknown')
                        st.info(f"🤖 **LLM Call Started** - {provider_name} {model_name}")
                        
                    elif latest_event.event_type == EventTypes.LLM_COMPLETE:
                        tokens_in = latest_event.data.get("tokens_in", 0)
                        tokens_out = latest_event.data.get("tokens_out", 0)
                        st.success(f"✅ **LLM Call Complete** - Tokens: {tokens_in:,}/{tokens_out:,}")
                        
                    elif latest_event.event_type == EventTypes.TOOL_START:
                        tool_name = latest_event.data.get("tool_name", "Unknown")
                        tool_index = latest_event.data.get("tool_index", 1)
                        total_tools = latest_event.data.get("total_tools", 1)
                        st.info(f"⚙️ **Tool Call Started:** `{tool_name}` ({tool_index}/{total_tools})")
                        
                    elif latest_event.event_type == EventTypes.TOOL_COMPLETE:
                        tool_name = latest_event.data.get("tool_name", "Unknown")
                        st.success(f"✅ **Tool Call Complete:** `{tool_name}`")
                        
                    elif latest_event.event_type == EventTypes.WORKFLOW_COMPLETE:
                        loop_count = latest_event.data.get("loop_count", 0)
                        tool_calls_executed = latest_event.data.get("tool_calls_executed", 0)
                        st.success(f"🎉 **Workflow Complete!** Iterations: {loop_count}, Tool calls: {tool_calls_executed}")
            
            time.sleep(0.5)  # Update every 500ms
        
        # Wait for thread to complete
        workflow_thread.join(timeout=10)
        
        # Clean up progress indicators
        progress_bar.progress(1.0, text="Workflow execution complete")
        time.sleep(1)  # Brief pause to show completion
        progress_container.empty()
        status_container.empty()
        
        # Remove event handler
        event_emitter.remove_callback(handle_workflow_event)
        
        # Process results (same logic as sync workflow)
        if result_container["error"]:
            st.error(f"Async workflow execution failed: {result_container['error']}")
            return
        
        result = result_container["result"]
        if result and result.get("status") == "success":
            bound_logger.info("Async workflow execution completed successfully")
            
            # Extract workflow results (same as sync version)
            shared_state = result.get("shared_state", {})
            workflow_messages = shared_state.get("final_messages", [])
            
            if workflow_messages:
                # Calculate new messages (same logic as sync version)
                original_message_count = len(filtered_messages)
                new_messages = workflow_messages[original_message_count:] if len(workflow_messages) > original_message_count else []
                
                bound_logger.info(f"Async workflow completed with {len(new_messages)} new messages")
                
                # Process and display new messages (same as sync version)
                for msg in new_messages:
                    if msg.get("role") == "assistant" and (msg.get("content") or msg.get("tool_calls")):
                        # Extract workflow context from action_id for display
                        action_id = msg.get("action_id", "")
                        workflow_context = ""
                        
                        if action_id.startswith("wf-") and "-" in action_id:
                            parts = action_id.split("-")
                            if len(parts) >= 3:
                                workflow_id = parts[1]
                                step_id = parts[2]
                                workflow_context = f" | **Workflow:** {workflow_id} | **Step:** {step_id}"
                        
                        # Display assistant message with workflow context
                        with st.chat_message("assistant"):
                            if msg.get("content"):
                                st.markdown(msg["content"])
                            
                            if msg.get("tool_calls"):
                                tool_names = [tc.get('function', {}).get('name', 'unknown') for tc in msg.get("tool_calls", [])]
                                if tool_names:
                                    st.markdown(f"⚙️ **Workflow Tool Call(s):** `{', '.join(tool_names)}`{workflow_context}")
                            
                            # Show token info if available
                            tokens_in = msg.get("token_count_in", 0)
                            tokens_out = msg.get("token_count_out", 0)
                            if tokens_in > 0 or tokens_out > 0:
                                st.caption(f"Tokens: {tokens_in:,} in, {tokens_out:,} out | Action ID: `{action_id}`")
                
                # Add new messages to session state
                for msg in new_messages:
                    st.session_state.messages.append(msg)
                
                # Calculate final statistics
                execution_duration = time.time() - execution_start_time
                total_tokens_in = sum(msg.get("token_count_in", 0) for msg in new_messages if msg.get("role") == "assistant")
                total_tokens_out = sum(msg.get("token_count_out", 0) for msg in new_messages if msg.get("role") == "assistant")
                
                # Update session totals
                st.session_state.conversation_total_tokens_in += total_tokens_in
                st.session_state.conversation_total_tokens_out += total_tokens_out
                st.session_state.conversation_total_duration += execution_duration
                
                # Display execution summary with real-time event info
                with st.chat_message("assistant"):
                    st.markdown(f"📊 **Async Workflow Summary:**")
                    st.markdown(f"- **Events Received:** {len(events_received)}")
                    st.markdown(f"- **New Messages:** {len(new_messages)}")
                    st.markdown(f"- **Total Tokens:** {total_tokens_in + total_tokens_out:,} ({total_tokens_in:,} in, {total_tokens_out:,} out)")
                    st.markdown(f"- **Execution Time:** {execution_duration:.1f}s")
                    
                    if events_received:
                        with st.expander("📋 Event Timeline", expanded=False):
                            for event in events_received[-10:]:  # Show last 10 events
                                timestamp = time.strftime("%H:%M:%S", time.localtime(event.timestamp))
                                event_desc = event.data.get('tool_name', event.data.get('action_id', 'N/A'))
                                st.text(f"[{timestamp}] {event.event_type}: {event_desc}")
                
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": f"📊 **Async Workflow Summary:**\n- **Events Received:** {len(events_received)}\n- **New Messages:** {len(new_messages)}\n- **Total Tokens:** {total_tokens_in + total_tokens_out:,} ({total_tokens_in:,} in, {total_tokens_out:,} out)\n- **Execution Time:** {execution_duration:.1f}s"
                })
        else:
            st.error("Async workflow execution failed or returned unexpected result")
            
    except Exception as e:
        bound_logger.error(f"Async workflow execution error: {e}", exc_info=True)
        st.error(f"Failed to execute async workflow: {str(e)}")


def run_workflow_execution(workflow_name: str, provider: str, model_name: str, base_sys_prompt: str, user_req_id: str):
    """Runs a guided workflow execution."""
    bound_logger = logger.bind(user_request_id=user_req_id)
    
    # Track execution timing like unguided mode
    execution_start_time = time.time()
    
    try:
        # Get workflow registry
        from nifi_mcp_server.workflows.registry import get_workflow_registry
        registry = get_workflow_registry()
        
        # Create workflow executor
        executor = registry.create_executor(workflow_name)
        if not executor:
            error_msg = f"Failed to create executor for workflow: {workflow_name}"
            bound_logger.error(error_msg)
            with st.chat_message("assistant"):
                st.markdown(f"❌ **Error:** {error_msg}")
            st.session_state.messages.append({
                "role": "assistant",
                "content": f"❌ **Error:** {error_msg}"
            })
            return
        
        bound_logger.info(f"Starting guided workflow execution: {workflow_name}")
        
        # Display workflow start message
        with st.chat_message("assistant"):
            st.markdown(f"🚀 **Starting guided workflow:** {workflow_name}")
            st.markdown("*The workflow will execute multiple LLM iterations with tool calls. Please wait for completion...*")
        
        st.session_state.messages.append({
            "role": "assistant",
            "content": f"🚀 **Starting guided workflow:** {workflow_name}\n\n*The workflow will execute multiple LLM iterations with tool calls. Please wait for completion...*"
        })
        
        # Prepare execution context - match the unguided mode context construction
        current_objective = st.session_state.get("current_objective", "")
        if current_objective and current_objective.strip():
            effective_system_prompt = f"{base_sys_prompt}\n\n## Current Objective\n{current_objective.strip()}"
            bound_logger.debug("Objective found, appending to base system prompt for workflow.")
        else:
            effective_system_prompt = base_sys_prompt
            bound_logger.debug("No objective set, using base system prompt for workflow.")
        
        # Filter out the workflow startup message to prevent duplication
        # The startup message we just added should not be passed to the workflow
        filtered_messages = []
        for msg in st.session_state.messages:
            # Skip the workflow startup message we just added
            if (msg.get("role") == "assistant" and 
                msg.get("content", "").startswith("🚀 **Starting guided workflow:")):
                continue
            filtered_messages.append(msg)
        
        context = {
            "provider": provider,
            "model_name": model_name,
            "system_prompt": effective_system_prompt,  # Use the effective system prompt with objective
            "user_request_id": user_req_id,
            "messages": filtered_messages,  # Use filtered messages instead of all messages
            "selected_nifi_server_id": st.session_state.get("selected_nifi_server_id"),
            "selected_phase": st.session_state.get("selected_phase", "All"),
            "max_loop_iterations": st.session_state.get("max_loop_iterations", MAX_LOOP_ITERATIONS),
            "max_tokens_limit": st.session_state.get("max_tokens_limit", 8000),  # Add token limit from UI
            "auto_prune_history": st.session_state.get("auto_prune_history", False),  # Add pruning setting
            "current_objective": current_objective  # Also pass the objective separately for workflow use
        }
        
        # Execute workflow with real-time LLM call display
        with st.spinner(f"🤔 Executing Workflow: {workflow_name}"):
            result = executor.execute(context)
        
        # Calculate execution duration
        execution_duration = time.time() - execution_start_time
        
        # Check for successful execution - executor returns {"status": "success"} not {"success": True}
        if result and result.get("status") == "success":
            bound_logger.info("Workflow execution completed successfully")
            
            # Extract the actual workflow results from shared state
            shared_state = result.get("shared_state", {})
            
            # --- FIX: Get messages from the correct key ---
            # The complete message history is now in "final_messages"
            workflow_messages = shared_state.get("final_messages", [])
            
            # DEBUG: Log what we actually received
            workflow_logger = bound_logger.bind(
                interface="workflow", 
                direction="ui_processing",
                data={
                    "message_count": len(workflow_messages),
                    "messages": [
                        {
                            "index": i,
                            "role": msg.get("role", "unknown"),
                            "has_content": bool(msg.get("content")),
                            "has_tool_calls": bool(msg.get("tool_calls"))
                        }
                        for i, msg in enumerate(workflow_messages)
                    ]
                }
            )
            workflow_logger.info("Received workflow messages in UI")
            
            # Get other details from the "unguided_mimic_result" for the summary
            workflow_result = shared_state.get("unguided_mimic_result", {})
            
            # Calculate token counts correctly by summing from all messages
            total_tokens_in = 0
            total_tokens_out = 0
            
            # Sum up tokens from all assistant messages in the workflow
            for msg in workflow_messages:
                if msg.get("role") == "assistant":
                    total_tokens_in += msg.get("token_count_in", 0)
                    total_tokens_out += msg.get("token_count_out", 0)
            
            total_tokens = total_tokens_in + total_tokens_out
            
            # Get workflow execution details for summary
            loop_count = workflow_result.get("loop_count", 0)
            tool_calls_executed = workflow_result.get("tool_calls_executed", 0)
            max_iterations_reached = workflow_result.get("max_iterations_reached", False)
            tool_results = workflow_result.get("tool_results", [])
            
            # Extract unique tool names that were executed
            executed_tools = []
            if tool_results:
                tool_names = [result.get("tool_name") for result in tool_results if result.get("tool_name")]
                # Get unique tool names while preserving order
                seen = set()
                executed_tools = [name for name in tool_names if name not in seen and not seen.add(name)]
            
            # Extract and display the actual LLM responses from the workflow first
            if workflow_messages:
                # CRITICAL FIX: Only process messages that are NEW from this workflow execution
                # The workflow_messages contains ALL messages (input + new), but we only want to display the new ones
                
                # Calculate how many messages we originally passed to the workflow
                original_message_count = len(filtered_messages)
                
                # The new messages are everything after the original message count
                # Note: workflow_messages includes system prompt + original messages + new messages
                # But the workflow removes the system prompt, so it's: original_messages + new_messages
                new_messages = workflow_messages[original_message_count:] if len(workflow_messages) > original_message_count else []
                
                bound_logger.info(f"Message calculation: Original={original_message_count}, Total={len(workflow_messages)}, New={len(new_messages)}")
                
                workflow_logger.bind(
                    direction="ui_processing", 
                    data={
                        "original_message_count": original_message_count,
                        "total_workflow_messages": len(workflow_messages),
                        "new_messages_count": len(new_messages),
                        "message_details": [
                            {
                                "role": msg.get("role"),
                                "has_content": bool(msg.get("content")),
                                "has_tool_calls": bool(msg.get("tool_calls"))
                            }
                            for msg in new_messages
                        ]
                    }
                ).info("Processing new workflow messages")
                
                for msg in new_messages:
                    if msg.get("role") == "assistant" and (msg.get("content") or msg.get("tool_calls")):
                        # Extract workflow and step info from action_id
                        action_id = msg.get("action_id", "")
                        workflow_id = "unknown"
                        step_id = "unknown"
                        
                        # Parse workflow context from action_id if it follows the pattern wf-{workflow}-{step}-tool-{uuid}
                        if action_id.startswith("wf-") and "-tool-" in action_id:
                            parts = action_id.split("-tool-")[0]  # Remove the tool UUID part
                            if parts.startswith("wf-"):
                                workflow_parts = parts[3:].split("-")  # Remove 'wf-' prefix
                                if len(workflow_parts) >= 2:
                                    # Last part is step_id, everything before is workflow_id
                                    step_id = workflow_parts[-1]
                                    workflow_id = "-".join(workflow_parts[:-1])
                        
                        # Log workflow context for debugging
                        bound_logger.debug(f"Processing message: Action: {action_id}, Parsed Workflow: {workflow_id}, Step: {step_id}, Content: {len(msg.get('content', ''))}, Tool calls: {len(msg.get('tool_calls', []))}")
                        msg_tokens_in = msg.get("token_count_in", 0)
                        msg_tokens_out = msg.get("token_count_out", 0)
                        msg_total_tokens = msg_tokens_in + msg_tokens_out
                        
                        # Display the LLM response with workflow context
                        with st.chat_message("assistant"):
                            # Show workflow context like unguided mode shows Req/Act IDs
                            st.markdown(f"**🤖 LLM Response** - Workflow: `{workflow_id}` | Step: `{step_id}` | Action: `{action_id[:8]}...`")
                            
                            # Show token information
                            if msg_total_tokens > 0:
                                st.markdown(f"*📊 {msg_total_tokens:,} tokens ({msg_tokens_in:,} in, {msg_tokens_out:,} out)*")
                            
                            # Show tool calls if any (like unguided mode)
                            tool_calls = msg.get("tool_calls", [])
                            if tool_calls:
                                # Extract workflow context from tool_calls if available
                                tool_call_contexts = []
                                tool_names = []
                                for tc in tool_calls:
                                    tool_name = tc.get('function', {}).get('name', 'unknown')
                                    tool_names.append(tool_name)
                                    
                                    # Try to extract workflow context from tool call id
                                    tool_call_id = tc.get('id', '')
                                    if tool_call_id.startswith("wf-") and "-tool-" in tool_call_id:
                                        context_parts = tool_call_id.split("-tool-")[0]  # Remove UUID
                                        if context_parts.startswith("wf-"):
                                            context_workflow_parts = context_parts[3:].split("-")
                                            if len(context_workflow_parts) >= 2:
                                                context_step = context_workflow_parts[-1]
                                                context_workflow = "-".join(context_workflow_parts[:-1])
                                                tool_call_contexts.append(f"{context_workflow}/{context_step}")
                                            else:
                                                tool_call_contexts.append("unknown/unknown")
                                        else:
                                            tool_call_contexts.append("unknown/unknown")
                                    else:
                                        tool_call_contexts.append("unknown/unknown")
                                
                                if tool_names:
                                    # Show tool names and their contexts
                                    contexts_str = ", ".join(set(tool_call_contexts))  # Remove duplicates
                                    st.markdown(f"⚙️ **Workflow Tool Call(s):** `{', '.join(tool_names)}` | Context: `{contexts_str}`")
                                else:
                                    st.markdown("_(Tool call with no details)_")
                            
                            # Show actual content if present
                            content = msg.get("content", "")
                            if content:
                                st.markdown(content)
                        
                        # Add to session state
                        session_message = {
                            "role": "assistant",
                            "content": content,
                            "token_count_in": msg_tokens_in,
                            "token_count_out": msg_tokens_out,
                            "action_id": action_id,
                            "workflow_id": workflow_id,
                            "step_id": step_id
                        }
                        
                        # Include tool_calls if present
                        if tool_calls:
                            session_message["tool_calls"] = tool_calls
                            
                        st.session_state.messages.append(session_message)
                        
                        bound_logger.info(f"Displayed LLM response from workflow: {len(content)} characters, {len(tool_calls)} tool calls, Workflow: {workflow_id}, Step: {step_id}")
                    elif msg.get("role") == "tool":
                        # Handle tool call results if needed
                        bound_logger.debug(f"Workflow included tool result: {msg.get('tool_call_id', 'unknown')}")
            
            # Display workflow completion summary with tokens and duration (like unguided mode)
            with st.chat_message("assistant"):
                st.markdown("✅ **Workflow completed successfully**")
                
                # Display workflow execution details
                execution_details = []
                if loop_count > 0:
                    execution_details.append(f"{loop_count} iteration{'s' if loop_count != 1 else ''}")
                if tool_calls_executed > 0:
                    execution_details.append(f"{tool_calls_executed} tool call{'s' if tool_calls_executed != 1 else ''}")
                if max_iterations_reached:
                    execution_details.append("⚠️ max iterations reached")
                
                if execution_details:
                    st.markdown(f"*Executed {', '.join(execution_details)}*")
                
                # Display tools that were used
                if executed_tools:
                    tools_text = ", ".join(executed_tools)
                    st.markdown(f"*Tools used: {tools_text}*")
                
                # Display workflow summary if available
                if result.get("message"):
                    st.markdown(f"*{result['message']}*")
                
                # Display token and duration summary like unguided mode
                if total_tokens > 0:
                    st.markdown(f"📊 **Workflow Summary:** {total_tokens:,} tokens ({total_tokens_in:,} in, {total_tokens_out:,} out) • {execution_duration:.1f}s")
            
            # Generate status report when max iterations reached (like unguided mode)
            if max_iterations_reached and st.session_state.get("enable_status_reports", True):
                bound_logger.info("Max iterations reached in workflow - generating status report")
                
                # Prepare context messages for status report (like unguided mode)
                llm_context_messages = [{"role": "system", "content": effective_system_prompt}]
                
                # Validate and clean session messages before adding to context
                clean_messages = []
                for msg in st.session_state.messages:
                    if msg.get("role") == "assistant" and msg.get("tool_calls"):
                        # For assistant messages with tool_calls, only include if we have corresponding tool responses
                        # For now, skip these to avoid validation errors in status reports
                        clean_msg = {
                            "role": "assistant",
                            "content": msg.get("content", "")
                        }
                        if clean_msg["content"]:  # Only add if there's actual content
                            clean_messages.append(clean_msg)
                    elif msg.get("role") in ["user", "assistant"] and msg.get("content"):
                        # Include user messages and assistant messages with content (no tool_calls)
                        clean_messages.append({
                            "role": msg["role"],
                            "content": msg["content"]
                        })
                    # Skip tool messages for status report
                
                llm_context_messages.extend(clean_messages)
                
                # Call the same status report function used in unguided mode
                send_status_report(provider, model_name, effective_system_prompt, 
                                 llm_context_messages, user_req_id, loop_count, bound_logger)
            elif loop_count > 1 and st.session_state.get("enable_status_reports", True):
                # Also generate status report for successful multi-iteration workflows
                bound_logger.info("Multi-iteration workflow completed - generating status report")
                
                # Prepare context messages for status report (like unguided mode)
                llm_context_messages = [{"role": "system", "content": effective_system_prompt}]
                
                # Validate and clean session messages before adding to context
                clean_messages = []
                for msg in st.session_state.messages:
                    if msg.get("role") == "assistant" and msg.get("tool_calls"):
                        # For assistant messages with tool_calls, only include if we have corresponding tool responses
                        # For now, skip these to avoid validation errors in status reports
                        clean_msg = {
                            "role": "assistant",
                            "content": msg.get("content", "")
                        }
                        if clean_msg["content"]:  # Only add if there's actual content
                            clean_messages.append(clean_msg)
                    elif msg.get("role") in ["user", "assistant"] and msg.get("content"):
                        # Include user messages and assistant messages with content (no tool_calls)
                        clean_messages.append({
                            "role": msg["role"],
                            "content": msg["content"]
                        })
                    # Skip tool messages for status report
                
                llm_context_messages.extend(clean_messages)
                
                # Call the same status report function used in unguided mode
                send_status_report(provider, model_name, effective_system_prompt, 
                                 llm_context_messages, user_req_id, loop_count, bound_logger)
            
            # Add the workflow completion message to session state
            completion_message = "✅ **Workflow completed successfully**"
            
            # Add execution details to completion message
            if execution_details:
                completion_message += f"\n\n*Executed {', '.join(execution_details)}*"
            
            # Add tools used to completion message
            if executed_tools:
                tools_text = ", ".join(executed_tools)
                completion_message += f"\n\n*Tools used: {tools_text}*"
            
            if result.get("message"):
                completion_message += f"\n\n*{result['message']}*"
            if total_tokens > 0:
                completion_message += f"\n\n📊 **Workflow Summary:** {total_tokens:,} tokens ({total_tokens_in:,} in, {total_tokens_out:,} out) • {execution_duration:.1f}s"
            
            st.session_state.messages.append({
                "role": "assistant",
                "content": completion_message
            })
            
            # Update session state tracking like unguided mode
            st.session_state.last_request_duration = execution_duration
            st.session_state.conversation_total_duration = st.session_state.get("conversation_total_duration", 0) + execution_duration
            
            bound_logger.info(f"Workflow completed: {total_tokens} tokens ({total_tokens_in} in, {total_tokens_out} out) in {execution_duration:.1f}s")
        else:
            # Handle error case - could be status="error" or no result
            if result:
                error_msg = result.get("message", f"Workflow failed with status: {result.get('status', 'unknown')}")
            else:
                error_msg = "Workflow execution failed - no result returned"
            bound_logger.error(f"Workflow execution failed: {error_msg}")
            with st.chat_message("assistant"):
                st.markdown(f"❌ **Workflow failed:** {error_msg}")
            
            st.session_state.messages.append({
                "role": "assistant",
                "content": f"❌ **Workflow failed:** {error_msg}"
            })
        
    except Exception as e:
        bound_logger.error(f"Error in workflow execution: {e}", exc_info=True)
        with st.chat_message("assistant"):
            st.markdown(f"❌ **Workflow execution error:** {e}")
        
        st.session_state.messages.append({
            "role": "assistant",
            "content": f"❌ **Workflow execution error:** {e}"
        })
        
    finally:
        # Always reset execution state after workflow completion
        st.session_state.llm_executing = False
        st.session_state.stop_requested = False
        # Force a rerun to update the UI back to input mode
        st.rerun()

def smart_prune_messages(messages: List[Dict], max_tokens: int, provider: str, model_name: str, tools, logger) -> List[Dict]:
    """
    Intelligently prune messages to fit within token limits while maintaining conversation structure.
    
    Args:
        messages: List of conversation messages
        max_tokens: Maximum allowed tokens
        provider: LLM provider (e.g., "openai")
        model_name: Model name for token calculation
        tools: Available tools for token calculation
        logger: Logger instance
        
    Returns:
        Pruned message list that maintains valid conversation structure
    """
    if len(messages) <= 2:  # System + 1 user message minimum
        return messages
    
    # Calculate current tokens
    try:
        token_counter = st.session_state.get("token_counter")
        if token_counter:
            current_tokens = token_counter.calculate_input_tokens(messages, provider, model_name, tools)
            logger.info(f"SMART_PRUNE_DEBUG: Initial tokens: {current_tokens}, Limit: {max_tokens}, Messages: {len(messages)}")
            if current_tokens <= max_tokens:
                logger.info("SMART_PRUNE_DEBUG: Already under limit, no pruning needed")
                return messages  # No pruning needed
        else:
            logger.error("TokenCounter not available")
            return messages
    except Exception as e:
        logger.error(f"Error calculating tokens during smart pruning: {e}")
        return messages  # Return original on error
    
    # Keep system message (index 0) and work with the rest
    system_message = messages[0]
    conversation_messages = messages[1:]
    
    # DEBUG: Log message structure
    logger.info(f"SMART_PRUNE_DEBUG: Message structure analysis:")
    for i, msg in enumerate(conversation_messages):
        role = msg.get("role", "unknown")
        has_tool_calls = "tool_calls" in msg and len(msg.get("tool_calls", [])) > 0
        tool_call_id = msg.get("tool_call_id", "")
        logger.info(f"  [{i}] {role}{' (has_tool_calls)' if has_tool_calls else ''}{' (tool_call_id: ' + tool_call_id + ')' if tool_call_id else ''}")
    
    # Find safe removal points (complete conversation turns)
    # We'll build a list of "removal groups" - sets of message indices that can be removed together
    removal_groups = []
    i = 0
    
    while i < len(conversation_messages):
        message = conversation_messages[i]
        role = message.get("role")
        
        if role == "user":
            logger.debug(f"SMART_PRUNE_DEBUG: Processing user message at index {i}")
            # Start of a potential removal group
            group_start = i
            group_end = i  # At least include the user message
            
            # Look ahead for the complete turn
            j = i + 1
            while j < len(conversation_messages):
                next_message = conversation_messages[j]
                next_role = next_message.get("role")
                
                if next_role == "assistant":
                    group_end = j
                    # Check if this assistant message has tool calls
                    if "tool_calls" in next_message:
                        # Must include corresponding tool responses
                        tool_call_ids = {tc.get("id") for tc in next_message.get("tool_calls", [])}
                        k = j + 1
                        while k < len(conversation_messages) and tool_call_ids:
                            tool_message = conversation_messages[k]
                            if (tool_message.get("role") == "tool" and 
                                tool_message.get("tool_call_id") in tool_call_ids):
                                tool_call_ids.remove(tool_message.get("tool_call_id"))
                                group_end = k
                            elif tool_message.get("role") != "tool":
                                break  # End of tool responses
                            k += 1
                    # Don't break here - continue looking for more assistant messages in this turn
                elif next_role == "tool":
                    # Orphaned tool message (shouldn't happen, but handle gracefully)
                    group_end = j
                elif next_role == "user":
                    # Found next user message - end of current turn
                    break
                else:
                    break
                j += 1
            
            logger.debug(f"SMART_PRUNE_DEBUG: Turn found at indices {group_start}-{group_end}")
            
            # More aggressive pruning: preserve only the most recent complete conversation turn
            # Count how many complete turns we have
            total_turns = 0
            temp_i = 0
            while temp_i < len(conversation_messages):
                if conversation_messages[temp_i].get("role") == "user":
                    total_turns += 1
                    # Skip to end of this turn
                    temp_j = temp_i + 1
                    while temp_j < len(conversation_messages) and conversation_messages[temp_j].get("role") != "user":
                        temp_j += 1
                    temp_i = temp_j
                else:
                    temp_i += 1
            
            # Count which turn this is (1-based)
            current_turn = 0
            temp_i = 0
            while temp_i <= i:
                if conversation_messages[temp_i].get("role") == "user":
                    current_turn += 1
                temp_i += 1
            
            logger.debug(f"SMART_PRUNE_DEBUG: Turn {current_turn}/{total_turns}, indices {group_start}-{group_end}")
            
            # More aggressive pruning when severely over limit
            turns_to_preserve = 2  # Default: preserve last 2 turns
            if current_tokens > max_tokens * 2:  # If more than 2x the limit
                turns_to_preserve = 1  # Only preserve the last 1 turn
                logger.debug(f"SMART_PRUNE_DEBUG: Severe token limit exceeded ({current_tokens} > {max_tokens * 2}), preserving only last {turns_to_preserve} turn(s)")
            else:
                logger.debug(f"SMART_PRUNE_DEBUG: Standard pruning, preserving last {turns_to_preserve} turn(s)")
            
            # Only preserve the specified number of recent complete turns
            if current_turn <= total_turns - turns_to_preserve:
                removal_groups.append((group_start, group_end))
                logger.debug(f"SMART_PRUNE_DEBUG: Added removal group {group_start}-{group_end} (turn {current_turn}/{total_turns})")
            else:
                logger.debug(f"SMART_PRUNE_DEBUG: Preserving recent turn {current_turn}/{total_turns} at {group_start}-{group_end}")
            
            i = group_end + 1
        else:
            # Skip orphaned assistant/tool messages (shouldn't happen in well-formed conversations)
            logger.debug(f"SMART_PRUNE_DEBUG: Skipping orphaned {role} message at index {i}")
            i += 1
    
    # Remove groups from oldest to newest until we're under the token limit
    # CRITICAL FIX: Remove one group at a time and recalculate
    logger.info(f"SMART_PRUNE_DEBUG: Found {len(removal_groups)} removal groups to consider")
    pruned_messages = [system_message] + conversation_messages.copy()
    
    # Process removals one at a time, recalculating groups after each removal
    while removal_groups:
        # Take the first (oldest) group
        group_start, group_end = removal_groups[0]
        
        # Calculate current indices (accounting for system message)
        adjusted_start = group_start + 1  # +1 for system message
        adjusted_end = group_end + 1      # +1 for system message
        
        # Validate indices are still valid
        if adjusted_start >= len(pruned_messages) or adjusted_end >= len(pruned_messages):
            logger.warning(f"Invalid indices after previous removals: {adjusted_start}-{adjusted_end}, message count: {len(pruned_messages)}")
            break
        
        # Remove the group
        removed_messages = pruned_messages[adjusted_start:adjusted_end + 1]
        pruned_messages = pruned_messages[:adjusted_start] + pruned_messages[adjusted_end + 1:]
        
        # Log what was removed
        removed_roles = [msg.get("role") for msg in removed_messages]
        logger.info(f"Smart pruning removed message group (roles: {removed_roles}) - indices {adjusted_start}-{adjusted_end}")
        
        # CRITICAL FIX: Validate message structure after removal
        if not validate_message_structure(pruned_messages, logger):
            logger.warning("Message structure validation failed after removal, reverting...")
            # Revert the removal
            pruned_messages = pruned_messages[:adjusted_start] + removed_messages + pruned_messages[adjusted_start:]
            break
        
        # Check if we're now under the limit
        try:
            token_counter = st.session_state.get("token_counter")
            if token_counter:
                new_tokens = token_counter.calculate_input_tokens(pruned_messages, provider, model_name, tools)
                logger.debug(f"After removing group: {new_tokens} tokens (limit: {max_tokens})")
                if new_tokens <= max_tokens:
                    break
            else:
                logger.error("TokenCounter not available")
                break
        except Exception as e:
            logger.error(f"Error calculating tokens after removal: {e}")
            break
            
        # Remove the processed group and recalculate remaining groups
        removal_groups.pop(0)
        
        # Recalculate removal groups for the updated message list
        # This is necessary because indices have shifted after removal
        conversation_messages = pruned_messages[1:]  # Exclude system message
        removal_groups = []
        i = 0
        
        while i < len(conversation_messages):
            message = conversation_messages[i]
            role = message.get("role")
            
            if role == "user":
                # Start of a potential removal group
                group_start = i
                group_end = i  # At least include the user message
                
                # Look ahead for the complete turn
                j = i + 1
                while j < len(conversation_messages):
                    next_message = conversation_messages[j]
                    next_role = next_message.get("role")
                    
                    if next_role == "assistant":
                        group_end = j
                        # Check if this assistant message has tool calls
                        if "tool_calls" in next_message:
                            # Must include corresponding tool responses
                            tool_call_ids = {tc.get("id") for tc in next_message.get("tool_calls", [])}
                            k = j + 1
                            while k < len(conversation_messages) and tool_call_ids:
                                tool_message = conversation_messages[k]
                                if (tool_message.get("role") == "tool" and 
                                    tool_message.get("tool_call_id") in tool_call_ids):
                                    tool_call_ids.remove(tool_message.get("tool_call_id"))
                                    group_end = k
                                elif tool_message.get("role") != "tool":
                                    break  # End of tool responses
                                k += 1
                            # Don't break here - continue looking for more assistant messages in this turn
                        # Don't break here - continue looking for more assistant messages in this turn
                    elif next_role == "tool":
                        # Orphaned tool message (shouldn't happen, but handle gracefully)
                        group_end = j
                    elif next_role == "user":
                        # Found next user message - end of current turn
                        break
                    else:
                        break
                    j += 1
                
                # More aggressive pruning: preserve only the most recent complete conversation turn
                # Count how many complete turns we have
                total_turns = 0
                temp_i = 0
                while temp_i < len(conversation_messages):
                    if conversation_messages[temp_i].get("role") == "user":
                        total_turns += 1
                        # Skip to end of this turn
                        temp_j = temp_i + 1
                        while temp_j < len(conversation_messages) and conversation_messages[temp_j].get("role") != "user":
                            temp_j += 1
                        temp_i = temp_j
                    else:
                        temp_i += 1
                
                # Count which turn this is (1-based)
                current_turn = 0
                temp_i = 0
                while temp_i <= i:
                    if conversation_messages[temp_i].get("role") == "user":
                        current_turn += 1
                    temp_i += 1
                
                # More aggressive pruning when severely over limit (same logic as initial pass)
                try:
                    token_counter = st.session_state.get("token_counter")
                    if token_counter:
                        recalc_tokens = token_counter.calculate_input_tokens(pruned_messages, provider, model_name, tools)
                    else:
                        recalc_tokens = 0
                except Exception:
                    recalc_tokens = max_tokens * 2  # Assume severe if we can't calculate
                
                turns_to_preserve = 2  # Default: preserve last 2 turns
                if recalc_tokens > max_tokens * 2:  # If more than 2x the limit
                    turns_to_preserve = 1  # Only preserve the last 1 turn
                
                # Only preserve the specified number of recent complete turns
                if current_turn <= total_turns - turns_to_preserve:
                    removal_groups.append((group_start, group_end))
                
                i = group_end + 1
            else:
                # Skip orphaned assistant/tool messages (shouldn't happen in well-formed conversations)
                logger.debug(f"SMART_PRUNE_DEBUG: Skipping orphaned {role} message at index {i}")
                i += 1
    
    # Final validation and token check
    if not validate_message_structure(pruned_messages, logger):
        logger.error("Final message structure validation failed, returning original messages")
        return messages
    
    try:
        token_counter = st.session_state.get("token_counter")
        if token_counter:
            final_tokens = token_counter.calculate_input_tokens(pruned_messages, provider, model_name, tools)
            logger.info(f"Smart pruning complete: {len(messages)} → {len(pruned_messages)} messages, {current_tokens} → {final_tokens} tokens")
        else:
            logger.warning("TokenCounter not available for final token calculation")
    except Exception:
        pass
    
    return pruned_messages


def validate_message_structure(messages: List[Dict], logger) -> bool:
    """
    Validate that the message structure is valid for OpenAI API.
    
    Rules:
    1. Every 'tool' message must be preceded by an 'assistant' message with 'tool_calls'
    2. No orphaned tool messages
    3. System message should be first (if present)
    4. Assistant messages with tool_calls must have all corresponding tool responses
    
    Args:
        messages: List of messages to validate
        logger: Logger instance
        
    Returns:
        True if structure is valid, False otherwise
    """
    if not messages:
        return True
    
    # Track tool calls that need responses
    pending_tool_calls = set()
    
    for i, message in enumerate(messages):
        role = message.get("role")
        
        if role == "system":
            # System message should be first
            if i != 0:
                logger.warning(f"System message found at position {i}, should be at position 0")
                return False
                
        elif role == "assistant":
            # Check if there are unresolved tool calls from previous assistant message
            if pending_tool_calls:
                logger.warning(f"Previous assistant message has unresolved tool calls: {pending_tool_calls}")
                return False
            
            # Clear pending tool calls and check for new ones
            pending_tool_calls.clear()
            
            # Check for new tool calls
            tool_calls = message.get("tool_calls", [])
            for tool_call in tool_calls:
                tool_call_id = tool_call.get("id")
                if tool_call_id:
                    pending_tool_calls.add(tool_call_id)
                    
        elif role == "tool":
            # Tool message must have a corresponding tool_call_id
            tool_call_id = message.get("tool_call_id")
            if not tool_call_id:
                logger.warning(f"Tool message at position {i} missing tool_call_id")
                return False
                
            if tool_call_id not in pending_tool_calls:
                logger.warning(f"Tool message at position {i} has orphaned tool_call_id: {tool_call_id}")
                return False
                
            # Remove this tool call from pending
            pending_tool_calls.remove(tool_call_id)
            
        elif role == "user":
            # User messages should not appear while tool calls are pending
            if pending_tool_calls:
                logger.warning(f"User message while tool calls pending: {pending_tool_calls}")
                return False
    
    # Check if there are any unresolved tool calls at the end
    if pending_tool_calls:
        logger.warning(f"Unresolved tool calls at end: {pending_tool_calls}")
        return False
    
    return True

# --- Refactored Execution Loop Function --- 
def run_execution_loop(provider: str, model_name: str, base_sys_prompt: str, user_req_id: str):
    """Runs the main LLM interaction loop."""
    bound_logger = logger.bind(user_request_id=user_req_id)
    loop_count = 0
    
    # Use configurable max iterations from session state
    max_iterations = st.session_state.get("max_loop_iterations", MAX_LOOP_ITERATIONS)
    
    # Initialize request-level tracking
    request_start_time = time.time()
    request_tokens_in = 0
    request_tokens_out = 0
    
    try:
        while loop_count < max_iterations:
            # Check for stop request at the beginning of each iteration
            if st.session_state.get("stop_requested", False):
                bound_logger.info("Stop requested by user. Ending execution loop.")
                with st.chat_message("assistant"):
                    st.markdown("🛑 **Execution stopped by user.**")
                st.session_state.messages.append({
                    "role": "assistant", 
                    "content": "🛑 **Execution stopped by user.**"
                })
                break
            
            loop_count += 1
            current_loop_logger = bound_logger.bind(loop=loop_count)
            current_loop_logger.info(f"Starting execution loop iteration {loop_count}")

            # --- Prepare context for LLM call (History and System Prompt) ---
            # 1. Determine the System Prompt
            current_objective = st.session_state.get("current_objective", "")
            if current_objective and current_objective.strip():
                effective_system_prompt = f"{base_sys_prompt}\n\n## Current Objective\n{current_objective.strip()}"
                current_loop_logger.debug("Objective found, appending to base system prompt.")
            else:
                effective_system_prompt = base_sys_prompt
                current_loop_logger.debug("No objective set, using base system prompt.")
                
            # --- Prepare Tools (BEFORE potential pruning) --- 
            # Get the selected phase and NiFi server ID
            current_phase = st.session_state.get("selected_phase", "All")
            current_nifi_server_id = st.session_state.get("selected_nifi_server_id")
            if not current_nifi_server_id:
                st.error("No NiFi server selected. Please select a server from the sidebar.")
                current_loop_logger.error("Aborting loop: No NiFi server ID found in session state.")
                break # Stop the loop if no server is selected
                
            current_loop_logger = current_loop_logger.bind(nifi_server_id=current_nifi_server_id)
            current_loop_logger.debug(f"Fetching tools for phase: {current_phase}, Server ID: {current_nifi_server_id}")
            
            filtered_raw_tools_list = get_available_tools(
                phase=current_phase, 
                user_request_id=user_req_id, 
                selected_nifi_server_id=current_nifi_server_id
            )
            
            formatted_tools = None
            current_loop_logger.debug(f"Formatting tools for provider: {provider}")
            if filtered_raw_tools_list:
                 try:
                      chat_manager = st.session_state.get("chat_manager")
                      if chat_manager:
                          formatted_tools = chat_manager.get_tools(
                              provider=provider, 
                              user_request_id=user_req_id,
                              selected_nifi_server_id=current_nifi_server_id
                          ) 
                          if formatted_tools:
                               current_loop_logger.debug(f"Tools successfully formatted for {provider} ({len(formatted_tools)} tools).")
                          else:
                               current_loop_logger.warning(f"ChatManager.get_tools returned None/empty for {provider}.")
                      else:
                          current_loop_logger.warning("ChatManager not available")
                          formatted_tools = None
                 except Exception as fmt_e:
                      st.error(f"Error formatting tools for {provider} in loop: {fmt_e}")
                      current_loop_logger.error(f"Error formatting tools for {provider}: {fmt_e}", exc_info=True)
                      formatted_tools = None
            # --------------------------------------------------
                
            # 2. Initialize LLM context messages (AFTER tool prep, BEFORE pruning)
            llm_context_messages = [{"role": "system", "content": effective_system_prompt}]
            
            # CRITICAL FIX: Validate and clean session messages before adding to context
            # This prevents sending incomplete tool call sequences to the LLM
            session_messages = st.session_state.messages.copy()
            
            # Validate message structure first
            if not validate_message_structure(session_messages, current_loop_logger):
                current_loop_logger.warning("Session messages contain invalid tool call sequences. Cleaning...")
                
                # Clean the messages by removing incomplete tool call sequences
                cleaned_messages = []
                pending_tool_calls = set()
                
                for msg in session_messages:
                    role = msg.get("role")
                    
                    if role == "assistant":
                        # Check if we have pending tool calls from previous assistant message
                        if pending_tool_calls:
                            current_loop_logger.debug(f"Skipping assistant message with unresolved tool calls: {pending_tool_calls}")
                            continue
                            
                        # Check for new tool calls
                        tool_calls = msg.get("tool_calls", [])
                        if tool_calls:
                            # Only include if we can expect tool results to follow
                            # For now, skip assistant messages with tool calls that don't have results
                            tool_call_ids = {tc.get("id") for tc in tool_calls if tc.get("id")}
                            
                            # Look ahead to see if there are corresponding tool results
                            has_all_results = True
                            remaining_messages = session_messages[session_messages.index(msg) + 1:]
                            found_tool_ids = set()
                            
                            for future_msg in remaining_messages:
                                if future_msg.get("role") == "tool":
                                    tool_call_id = future_msg.get("tool_call_id")
                                    if tool_call_id in tool_call_ids:
                                        found_tool_ids.add(tool_call_id)
                                elif future_msg.get("role") == "assistant":
                                    # Stop looking when we hit another assistant message
                                    break
                            
                            if found_tool_ids != tool_call_ids:
                                current_loop_logger.debug(f"Skipping assistant message with incomplete tool results. Expected: {tool_call_ids}, Found: {found_tool_ids}")
                                continue
                            else:
                                pending_tool_calls = tool_call_ids.copy()
                        
                        cleaned_messages.append(msg)
                        
                    elif role == "tool":
                        tool_call_id = msg.get("tool_call_id")
                        if tool_call_id in pending_tool_calls:
                            cleaned_messages.append(msg)
                            pending_tool_calls.remove(tool_call_id)
                        else:
                            current_loop_logger.debug(f"Skipping orphaned tool message with id: {tool_call_id}")
                            
                    elif role == "user":
                        # User messages are always safe to include
                        # But if there are pending tool calls, we need to clear them
                        if pending_tool_calls:
                            current_loop_logger.debug(f"Clearing pending tool calls due to user message: {pending_tool_calls}")
                            pending_tool_calls.clear()
                        cleaned_messages.append(msg)
                        
                    else:
                        # Other message types (shouldn't happen in our case)
                        cleaned_messages.append(msg)
                
                current_loop_logger.info(f"Message cleaning complete: {len(session_messages)} → {len(cleaned_messages)} messages")
                llm_context_messages.extend(cleaned_messages)
            else:
                # Messages are valid, use them as-is
                llm_context_messages.extend(session_messages)
                
            current_loop_logger.debug(f"Initial llm_context_messages length (incl system prompt): {len(llm_context_messages)}")

            # --- Apply Auto-Pruning (if enabled) --- 
            if st.session_state.get("auto_prune_history", False):
                current_loop_logger.info("Auto-pruning enabled. Checking token count...")
                max_tokens = st.session_state.get("max_tokens_limit", 8000)
                
                # ENHANCED DEBUG: Always log current state
                current_loop_logger.info(f"DEBUG: Message count before pruning check: {len(llm_context_messages)}")
                current_loop_logger.info(f"DEBUG: Max token limit: {max_tokens}")
                
                # CRITICAL FIX: Use smart pruning instead of naive pruning
                try:
                    token_counter = st.session_state.get("token_counter")
                    if token_counter:
                        current_tokens = token_counter.calculate_input_tokens(
                            llm_context_messages, 
                            provider, 
                            model_name, 
                            tools=formatted_tools
                        )
                        current_loop_logger.info(f"DEBUG: Current token count (incl. tools): {current_tokens}, Limit: {max_tokens}")
                    else:
                        current_loop_logger.warning("TokenCounter not available")
                        current_tokens = 0
                    
                    if current_tokens > max_tokens:
                        current_loop_logger.info(f"DEBUG: Token limit exceeded ({current_tokens} > {max_tokens}). Applying smart pruning...")
                        
                        original_count = len(llm_context_messages)
                        llm_context_messages = smart_prune_messages(
                            messages=llm_context_messages,
                            max_tokens=max_tokens,
                            provider=provider,
                            model_name=model_name,
                            tools=formatted_tools,
                            logger=current_loop_logger
                        )
                        
                        # Enhanced verification
                        final_count = len(llm_context_messages)
                        current_loop_logger.info(f"DEBUG: Smart pruning results: {original_count} → {final_count} messages")
                        
                        try:
                            token_counter = st.session_state.get("token_counter")
                            if token_counter:
                                final_tokens = token_counter.calculate_input_tokens(llm_context_messages, provider, model_name, tools=formatted_tools)
                                current_loop_logger.info(f"DEBUG: Smart pruning token results: {current_tokens} → {final_tokens} tokens")
                                
                                if final_tokens > max_tokens:
                                    current_loop_logger.warning(f"DEBUG: Smart pruning failed to get under limit! Still at {final_tokens} tokens")
                                else:
                                    current_loop_logger.info(f"DEBUG: Smart pruning successful! Now at {final_tokens} tokens")
                            else:
                                current_loop_logger.warning("TokenCounter not available for final token calculation")
                                
                        except Exception as verify_e:
                            current_loop_logger.warning(f"Could not verify final token count after smart pruning: {verify_e}")
                    else:
                        current_loop_logger.info(f"DEBUG: Token count is within limit ({current_tokens} <= {max_tokens}). No pruning needed.")
                        
                except Exception as token_calc_err:
                    current_loop_logger.error(f"Error calculating tokens during pruning: {token_calc_err}", exc_info=True)
                    st.warning("Error calculating tokens, cannot prune history accurately. Disabling auto-prune for this request.")
                
                current_loop_logger.info(f"DEBUG: Final message count after pruning check: {len(llm_context_messages)}")
            else:
                current_loop_logger.info("DEBUG: Auto-pruning disabled in UI settings.")
            # ------------------------------------------
            
            # --- LLM Action ID --- # Moved Action ID generation closer to the LLM call
            llm_action_id = str(uuid.uuid4())
            current_loop_logger = current_loop_logger.bind(action_id=llm_action_id)

            # --- Call LLM --- 
            # Note: formatted_tools was prepared BEFORE the pruning loop
            response_data = None
            with st.spinner(f"Thinking... (Step {loop_count}/{max_iterations}) / Tokens: ~{current_tokens if 'current_tokens' in locals() else 'N/A'}"):
                current_loop_logger.info(f"Calling LLM ({provider} - {model_name})...")
                chat_manager = st.session_state.get("chat_manager")
                if chat_manager:
                    response_data = chat_manager.get_llm_response(
                        messages=llm_context_messages, 
                        system_prompt=effective_system_prompt,
                        provider=provider,
                        model_name=model_name,
                        user_request_id=user_req_id,
                        selected_nifi_server_id=current_nifi_server_id
                    )
                else:
                    response_data = {"error": "ChatManager not available"}
                if response_data is None:
                     st.error("LLM response was empty.")
                     current_loop_logger.error("LLM response data was None.")
                     break
                else:
                    current_loop_logger.info(f"Received response from LLM ({provider}).")
            # ---------------
            
            # --- Process LLM Response --- 
            llm_content = response_data.get("content")
            tool_calls = response_data.get("tool_calls")
            error_message = response_data.get("error")
            token_count_in = response_data.get("token_count_in", 0)
            token_count_out = response_data.get("token_count_out", 0)
            
            # Accumulate tokens for this request
            request_tokens_in += token_count_in
            request_tokens_out += token_count_out
            
            if error_message:
                current_loop_logger.error(f"LLM returned an error: {error_message}")
                with st.chat_message("assistant"):
                    st.markdown(f"Sorry, I encountered an error: {error_message}")
                st.session_state.messages.append({"role": "assistant", "content": f"Sorry, I encountered an error: {error_message}"})
                break
                
            # Add assistant message to history (including tool calls if any)
            assistant_message_to_add = {
                "role": "assistant", 
                "token_count_in": token_count_in, 
                "token_count_out": token_count_out,
                "action_id": llm_action_id # Add action_id here
            }
            if llm_content: assistant_message_to_add["content"] = llm_content
            if tool_calls: assistant_message_to_add["tool_calls"] = tool_calls
            st.session_state.messages.append(assistant_message_to_add)
            current_loop_logger.debug(f"Appended assistant message: Content={'present' if llm_content else 'absent'}, ToolCalls={len(tool_calls) if tool_calls else 0}")

            # Display assistant message (or tool call placeholder) generated in this loop iteration
            with st.chat_message("assistant"):
                if tool_calls:
                    tool_names = [tc.get('function', {}).get('name', 'unknown') for tc in tool_calls]
                    if tool_names:
                         st.markdown(f"⚙️ Calling tool(s): `{', '.join(tool_names)}`...")
                    else:
                         st.markdown("_(Tool call with no details)_") # Should be rare
                elif llm_content:
                    st.markdown(llm_content)
                    # Replace st.code with st_copy_to_clipboard here as well
                    # Use llm_action_id if available, otherwise generate a unique one for the key
                    copy_key = f"loop_copy_{llm_action_id if 'llm_action_id' in locals() and llm_action_id else uuid.uuid4()}"
                    st_copy_to_clipboard(llm_content, key=copy_key)
                else:
                    st.markdown("_(Assistant provided no content or tool calls)_") # Should be rare

                # Add token counts and action ID caption for this message
                caption_parts = []
                if token_count_in or token_count_out:
                     caption_parts.append(f"Tokens: In={token_count_in}, Out={token_count_out}")
                if llm_action_id: # Use the ID from this loop
                     caption_parts.append(f"Action ID: `{llm_action_id}`")
                if caption_parts:
                    st.caption(" | ".join(caption_parts))

            # --- Handle Tool Calls (if any) --- 
            if not tool_calls:
                current_loop_logger.info("No tool calls requested by LLM. Checking for TASK COMPLETE.")
                # If no tool calls and LLM thinks task is done, break the loop
                if llm_content and "TASK COMPLETE" in llm_content:
                    current_loop_logger.info("TASK COMPLETE detected. Ending execution loop.")
                    break
                else:
                    # If no tool call and task not complete, this iteration might be done (e.g., clarifying question)
                    # Or it could be an unexpected state. For now, assume the loop should end.
                    current_loop_logger.info("No tool calls and no TASK COMPLETE. Ending loop for this user request.")
                    break # Exit loop if no tools called and not explicitly complete
            else:
                current_loop_logger.info(f"Processing {len(tool_calls)} tool call(s)...")
                # Process tool calls one by one
                for tool_call in tool_calls:
                    # Check for stop request before each tool call
                    if st.session_state.get("stop_requested", False):
                        current_loop_logger.info("Stop requested during tool execution. Breaking tool loop.")
                        break
                        
                     # Decision: Use the llm_action_id for subsequent tool calls in this loop iteration
                     # This simplifies tracing for a single LLM turn, sacrificing granularity
                     # between multiple tool calls within the same turn.
                     # We could generate a unique tool_action_id = str(uuid.uuid4()) here if needed.
                    # tool_action_id = str(uuid.uuid4())
                    # tool_loop_logger = current_loop_logger.bind(action_id=tool_action_id) # Bind for tool logging
                    tool_loop_logger = current_loop_logger # Use the logger already bound with llm_action_id
                    
                    tool_id = tool_call.get("id")
                    function_call = tool_call.get("function")
                    if not tool_id or not function_call or not isinstance(function_call, dict):
                        tool_loop_logger.error(f"Skipping invalid tool call structure: {tool_call}")
                        continue
                    
                    function_name = function_call.get("name")
                    function_args_str = function_call.get("arguments", "{}")
                    tool_loop_logger.info(f"Executing tool: {function_name} (ID: {tool_id})")
                    
                    try:
                        # Parse arguments
                        arguments = json.loads(function_args_str)
                        tool_loop_logger.debug(f"Parsed arguments for {function_name}: {arguments}")
                        
                        # Execute the tool using mcp_handler
                        # Pass user_request_id and the llm_action_id from this loop iteration
                        # Pass the selected NiFi server ID
                        tool_result = execute_mcp_tool(
                            tool_name=function_name, 
                            params=arguments,
                            selected_nifi_server_id=current_nifi_server_id, # Pass selected server ID
                            user_request_id=user_req_id, 
                            action_id=llm_action_id # Use llm_action_id here
                        )
                        
                        # Handle validation errors gracefully
                        if isinstance(tool_result, dict) and tool_result.get("validation_error"):
                            # Display friendly message to user
                            ui_message = tool_result.get("ui_message", "Adjusting parameters...")
                            with st.chat_message("assistant"):
                                st.info(f"⚙️ {ui_message}")
                            
                            # But send detailed error to LLM for learning
                            tool_result_content = json.dumps(tool_result)
                        else:
                            # Normal tool result - format for LLM
                            tool_result_content = json.dumps(tool_result) if tool_result is not None else "null"
                        
                        # Format result for the LLM
                        tool_loop_logger.debug(f"Tool {function_name} execution result: {tool_result_content[:200]}...") # Log snippet

                        # Add tool result message to history for the next LLM iteration
                        st.session_state.messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_id,
                                "content": tool_result_content, # Send result back as JSON string
                                # "name": function_name # OpenAI includes name here, Gemini doesn't use it like this
                            }
                        )
                    except json.JSONDecodeError as json_err:
                        error_content = f"Error parsing arguments for {function_name}: {json_err}"
                        tool_loop_logger.error(error_content)
                        st.error(error_content)
                        st.session_state.messages.append({"role": "tool", "tool_call_id": tool_id, "content": error_content})
                    except Exception as tool_err:
                        error_content = f"Error executing tool {function_name}: {tool_err}"
                        tool_loop_logger.error(error_content, exc_info=True)
                        st.error(error_content)
                        st.session_state.messages.append({"role": "tool", "tool_call_id": tool_id, "content": error_content})
                # Continue loop to send tool results back to LLM
                # No break here - loop continues automatically
        
        # --- Handle Loop End Conditions ---
        if loop_count >= max_iterations:
            bound_logger.warning(f"Execution loop reached maximum iterations ({max_iterations})")
            
            # Display user feedback
            with st.chat_message("assistant"):
                st.markdown(f"⚠️ **Reached maximum action limit ({max_iterations} actions)**")
                st.markdown("I've completed as much as possible within the action limit.")
            
            # Add visible message to history
            st.session_state.messages.append({
                "role": "assistant",
                "content": f"⚠️ **Reached maximum action limit ({max_iterations} actions)**\n\nI've completed as much as possible within the action limit."
            })
            
            # Optional: Send status report (always enabled by default)
            # if st.session_state.get("enable_status_reports", True):
            send_status_report(provider, model_name, effective_system_prompt, 
                             llm_context_messages, user_req_id, loop_count, bound_logger)
        
    except Exception as e:
        bound_logger.error(f"Unexpected error in execution loop: {e}", exc_info=True)
        st.error(f"An unexpected error occurred: {e}")
    finally:
        # Calculate final timing and update session totals
        request_end_time = time.time()
        request_duration = request_end_time - request_start_time
        
        # Update session state with request totals
        st.session_state.last_request_tokens_in = request_tokens_in
        st.session_state.last_request_tokens_out = request_tokens_out
        st.session_state.last_request_duration = request_duration
        
        # Update conversation totals
        st.session_state.conversation_total_tokens_in += request_tokens_in
        st.session_state.conversation_total_tokens_out += request_tokens_out
        st.session_state.conversation_total_duration += request_duration
        
        # Display request summary
        if request_tokens_in > 0 or request_tokens_out > 0:
            total_request_tokens = request_tokens_in + request_tokens_out
            with st.chat_message("assistant"):
                st.markdown(f"📊 **Request Summary:** {total_request_tokens:,} tokens ({request_tokens_in:,} in, {request_tokens_out:,} out) • {request_duration:.1f}s")
            
            # Log the summary
            bound_logger.info(f"Request completed: {total_request_tokens} tokens ({request_tokens_in} in, {request_tokens_out} out) in {request_duration:.1f}s")
        
        # Always reset execution state
        st.session_state.llm_executing = False
        st.session_state.stop_requested = False
        # Force a rerun to update the UI back to input mode
        st.rerun()

# --- User Input Handling --- 
# Custom input UI with stop button functionality
is_executing = st.session_state.get("llm_executing", False)
pending_execution = st.session_state.get("pending_execution", None)

# If we have pending execution or are executing, show the stop UI
if is_executing or pending_execution:
    # Show status and stop button when executing or about to execute
    col1, col2 = st.columns([0.85, 0.15])
    with col1:
        if pending_execution:
            st.info(f"🚀 Starting LLM execution... (Max {st.session_state.get('max_loop_iterations', MAX_LOOP_ITERATIONS)} actions)")
        else:
            st.info(f"🤔 LLM is processing... (Max {st.session_state.get('max_loop_iterations', MAX_LOOP_ITERATIONS)} actions)")
    with col2:
        if st.button("🛑 Stop", key="stop_btn", help="Stop LLM execution", use_container_width=True):
            st.session_state.stop_requested = True
            # Clear pending execution if stopping before it starts
            if pending_execution:
                st.session_state.pending_execution = None
            # Also reset execution state to return to input mode
            st.session_state.llm_executing = False
            st.rerun()
            
    # Process pending execution after showing the UI
    if pending_execution and not is_executing:
        # Start the execution
        st.session_state.llm_executing = True
        st.session_state.pending_execution = None
        
        # Extract execution parameters
        provider, model_name, base_system_prompt, user_req_id = pending_execution
        
        # Choose execution mode based on workflow configuration
        execution_mode = st.session_state.get("workflow_execution_mode", "unguided")
        
        if execution_mode == "guided":
            # Check if a workflow is selected
            selected_workflow = st.session_state.get("selected_workflow")
            bound_logger = logger.bind(user_request_id=user_req_id)
            bound_logger.info(f"Guided mode execution: selected_workflow='{selected_workflow}', execution_mode='{execution_mode}'")
            
            if selected_workflow:
                # Check if this is an async workflow
                if selected_workflow == "async_unguided_mimic":
                    # Run async workflow with integrated real-time UI
                    try:
                        run_async_workflow_integrated(selected_workflow, provider, model_name, base_system_prompt, user_req_id)
                    except Exception as e:
                        bound_logger.error(f"Failed to run async workflow: {e}", exc_info=True)
                        st.error(f"Failed to run async workflow: {str(e)}")
                        # Fall back to regular workflow execution
                        run_workflow_execution(selected_workflow, provider, model_name, base_system_prompt, user_req_id)
                else:
                    # Run regular guided workflow
                    run_workflow_execution(
                        workflow_name=selected_workflow,
                        provider=provider, 
                        model_name=model_name, 
                        base_sys_prompt=base_system_prompt, 
                        user_req_id=user_req_id
                    )
            else:
                # No workflow selected, show error and fall back to unguided
                bound_logger.warning(f"Guided mode selected but no workflow chosen (selected_workflow='{selected_workflow}'), falling back to unguided")
                with st.chat_message("assistant"):
                    st.markdown("⚠️ **No workflow selected.** Falling back to unguided mode.")
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": "⚠️ **No workflow selected.** Falling back to unguided mode."
                })
                
                # Call the traditional execution loop
                run_execution_loop(provider=provider, model_name=model_name, base_sys_prompt=base_system_prompt, user_req_id=user_req_id)
        else:
            # Call the traditional execution loop
            run_execution_loop(provider=provider, model_name=model_name, base_sys_prompt=base_system_prompt, user_req_id=user_req_id)

else:
    # Show normal input when not executing and no pending execution
    col1, col2 = st.columns([0.85, 0.15])
    with col1:
        user_input = st.text_input(
            "Message",
            placeholder="What can I help you with?",
            key=f"user_input_field_{st.session_state.input_counter}",
            label_visibility="collapsed"
        )
    with col2:
        send_button = st.button("▶️ Send", key="send_btn", help="Send message", use_container_width=True)
    
    # Display session statistics under the input box
    total_session_tokens = st.session_state.conversation_total_tokens_in + st.session_state.conversation_total_tokens_out
    last_total_tokens = st.session_state.last_request_tokens_in + st.session_state.last_request_tokens_out
    
    if total_session_tokens > 0 or last_total_tokens > 0:
        stats_parts = []
        if total_session_tokens > 0:
            stats_parts.append(f"Session: {total_session_tokens:,} tokens ({st.session_state.conversation_total_duration:.1f}s)")
        if last_total_tokens > 0:
            stats_parts.append(f"Last: {last_total_tokens:,} tokens ({st.session_state.last_request_duration:.1f}s)")
        
        if stats_parts:
            st.caption(" | ".join(stats_parts))
    
    # Process input when send button is clicked
    if send_button and user_input.strip():
        if not provider:
            st.error("Please configure an API key and select a provider.")
            logger.warning("User submitted prompt but no provider was configured/selected.")
        else:
            # Clear the input field by incrementing the counter
            st.session_state.input_counter += 1
            
            user_request_id = str(uuid.uuid4())
            bound_logger = logger.bind(user_request_id=user_request_id)
            bound_logger.info(f"Received new user prompt (Provider: {provider})")

            # Append user message with ID to history
            user_message = {"role": "user", "content": user_input, "user_request_id": user_request_id}
            st.session_state.messages.append(user_message)
            
            # Immediately display user message
            with st.chat_message("user"):
                st.markdown(user_input)
                st.caption(f"Request ID: `{user_request_id}`")

            # Set up pending execution (will be processed on next run)
            st.session_state.pending_execution = (provider, model_name, base_system_prompt, user_request_id)
            st.session_state.stop_requested = False
            
            # Force a rerun to show the executing state
            st.rerun()

# End of file - No changes needed below last removed section 