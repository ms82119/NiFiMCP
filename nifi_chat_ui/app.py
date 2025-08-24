import streamlit as st
import json # Import json for formatting tool results
import uuid # Added for context IDs
import time # Added for timing tracking
from loguru import logger # Import logger directly
from st_copy_to_clipboard import st_copy_to_clipboard # Import the new component
from typing import List, Dict
# New modular imports are above

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

# Use new modular ChatManager directly
from llm.chat_manager import ChatManager
from llm.utils.token_counter import TokenCounter
from llm.mcp.client import MCPClient
from mcp_handler import get_available_tools, execute_mcp_tool, get_nifi_servers
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

# Initialize new modular ChatManager after page config
_chat_manager = None
_token_counter = None
_mcp_client = None

def get_chat_manager() -> ChatManager:
    """Get or create the ChatManager instance."""
    global _chat_manager
    
    if _chat_manager is None:
        logger.info("Initializing new modular ChatManager...")
        
        # Use the existing config system - the API keys are already loaded
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
            }
        }
        
        _chat_manager = ChatManager(config_dict)
        logger.info("Successfully initialized new modular ChatManager")
    
    return _chat_manager

def get_token_counter() -> TokenCounter:
    """Get or create the TokenCounter instance."""
    global _token_counter
    if _token_counter is None:
        _token_counter = TokenCounter()
    return _token_counter

def get_mcp_client() -> MCPClient:
    """Get or create the MCPClient instance."""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient()
    return _mcp_client

# Initialize on first use (lazy initialization)
logger.info("New modular LLM architecture initialized (lazy loading)")

# --- Constants ---
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
        # Use the first value from the dropdown list of models as the default
        default_selection = list(available_models.keys())[0]
        default_index = 0
        
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
    if "selected_workflow" not in st.session_state:
        st.session_state.selected_workflow = None
    if "available_workflows" not in st.session_state:
        st.session_state.available_workflows = []
    
    # === WORKFLOW SELECTION ===
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
            selected_workflow_display = st.selectbox(
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
                    st.caption(f"📋 {selected_workflow_def.description}")
                    
                    # Show workflow phases
                    if selected_workflow_def.phases and selected_workflow_def.phases != ["All"]:
                        phases_text = ", ".join(selected_workflow_def.phases)
                        st.caption(f"🔧 Phases: {phases_text}")
        else:
            st.warning("No workflows available or workflow system not configured")
            st.session_state.available_workflows = []
            st.session_state.selected_workflow = None
            
    except Exception as e:
        logger.error(f"Error loading workflows: {e}", exc_info=True)
        st.error(f"Error loading workflows: {e}")
        st.session_state.available_workflows = []
        st.session_state.selected_workflow = None

    # --- PHASE SELECTION ===
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
        value=10,
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
        logger.info("Chat history, golden context, objective, phase, and usage tracking cleared by user.")
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
                pass  # Skip placeholder when there are no tool details
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

            # Only show copy button if "Prepare Full Conversation For Copy" is active
            if st.session_state.get("show_copy_buttons", False):
                st_copy_to_clipboard(message["content"], key=copy_key) # Use the new unique key
        elif message["role"] == "assistant" and "tool_calls" in message and "content" not in message:
            tool_names = [tc.get('function', {}).get('name', 'unknown') for tc in message.get("tool_calls", [])]
            if tool_names:
                st.markdown(f"⚙️ Calling tool(s): `{', '.join(tool_names)}`...")
            else:
                pass  # Skip placeholder when there are no tool details
                
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

# Status reporting is now handled by workflows, not the UI

# --- Workflow Execution Function ---
def run_async_workflow_integrated(workflow_name: str, provider: str, model_name: str, base_sys_prompt: str, user_req_id: str, user_input: str):
    """Run an async workflow with integrated real-time UI updates in the main chat interface."""
    import asyncio
    import threading
    import time
    from nifi_mcp_server.workflows.core.event_system import get_event_emitter, EventTypes
    from nifi_mcp_server.workflows.registry import get_workflow_registry
    
    bound_logger = logger.bind(user_request_id=user_req_id)
    execution_start_time = time.time()
    
    # Check if we're already executing this specific workflow for this request
    current_execution = st.session_state.get("current_async_execution", {})
    
    # Check for stale execution state (older than 5 minutes)
    if current_execution.get("executing", False):
        start_time = current_execution.get("start_time", 0)
        if time.time() - start_time > 300:  # 5 minutes timeout
            bound_logger.warning(f"Clearing stale execution state for workflow '{current_execution.get('workflow_name')}'")
            st.session_state.current_async_execution = {}
            current_execution = {}
    
    if (current_execution.get("workflow_name") == workflow_name and 
        current_execution.get("user_request_id") == user_req_id and
        current_execution.get("executing", False)):
        bound_logger.warning(f"Async workflow '{workflow_name}' already executing for request {user_req_id}, skipping duplicate call")
        return
    
    # Set execution state for this specific workflow and request
    st.session_state.current_async_execution = {
        "workflow_name": workflow_name,
        "user_request_id": user_req_id,
        "executing": True,
        "start_time": execution_start_time
    }
    
    try:
        # Display workflow start message (only once)
        with st.chat_message("assistant"):
            st.markdown(f"🚀 **Starting async workflow:** {workflow_name}")
            st.markdown("*Real-time updates will appear below...*")
        
        st.session_state.messages.append({
            "role": "assistant",
            "content": f"🚀 **Starting async workflow:** {workflow_name}\n\n*Real-time updates will appear below...*",
            "workflow_event": True
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
        
        # Pass user input separately - workflow gets context from golden context
        context = {
            "provider": provider,
            "model_name": model_name,
            "system_prompt": effective_system_prompt,
            "user_request_id": user_req_id,
            "user_prompt": user_input,  # Pass user prompt separately
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
        
        # Pre-initialize components in main thread to avoid thread context warnings
        # This ensures all Streamlit-dependent initialization happens in the main thread
        try:
            chat_manager = get_chat_manager()
            token_counter = get_token_counter()
            mcp_client = get_mcp_client()
            
            # Get config from main thread to pass to workflow
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
                }
            }
            
            # Pass config to async executor to avoid background thread imports
            if hasattr(async_executor, 'set_config'):
                async_executor.set_config(config_dict)
                bound_logger.info("Passed config to async executor")
            

            
            bound_logger.info("Pre-initialized ChatManager, TokenCounter, and MCPClient in main thread")
        except Exception as e:
            bound_logger.warning(f"Failed to pre-initialize components: {e}")
            # Continue anyway - components will be initialized in background thread if needed
        
        # Set up event handling for real-time UI updates
        events_received = []
        async_stats = {}  # Closure variable to capture execution statistics from events
        event_emitter = get_event_emitter()
        
        def handle_workflow_event(event):
            """Handle workflow events - just collect them for main thread processing."""
            events_received.append(event)
            bound_logger.info(f"Received workflow event: {event.event_type} with data: {event.data}")
            
            # Capture execution statistics from WORKFLOW_COMPLETE events
            if event.event_type == EventTypes.WORKFLOW_COMPLETE and 'loop_count' in event.data:
                nonlocal async_stats
                async_stats.update(event.data)
                bound_logger.info(f"Captured async workflow stats: {async_stats}")
        
        # Register event handler
        event_emitter.on(handle_workflow_event)
        bound_logger.info("Event handler registered for async workflow")
        
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
                bound_logger.error(f"Async workflow execution failed: {e}", exc_info=True)
            finally:
                loop.close()
        
        # Start async execution in background thread
        workflow_thread = threading.Thread(target=run_async_workflow)
        workflow_thread.start()
        
        # Wait for completion with real-time updates
        start_time = time.time()
        max_wait_time = 300  # 5 minutes max
        last_event_count = 0
        
        while not result_container["completed"] and (time.time() - start_time) < max_wait_time:
            # Check for new events and display them in main thread
            if len(events_received) > last_event_count:
                bound_logger.info(f"Processing {len(events_received) - last_event_count} new events")
                
                # Process new events since last check
                workflow_completed = False
                for event in events_received[last_event_count:]:
                    bound_logger.info(f"Processing event: {event.event_type} with data: {event.data}")
                    
                    # Special handling for status report events to ensure they're processed
                    if event.event_type == "status_report_complete":
                        bound_logger.info("Found status_report_complete event - processing immediately")
                        # Process the event directly instead of recursive call
                        status_content = event.data.get("status_content", "")
                        bound_logger.info(f"Processing status report completion: {len(status_content)} chars")
                        
                        if status_content:
                            # Force immediate display
                            with st.chat_message("assistant"):
                                st.markdown("### 📋 Status Report")
                                st.markdown(status_content)
                            
                            # Add to session state
                            status_message = {
                                "role": "assistant",
                                "content": f"### 📋 Status Report\n\n{status_content}",
                                "is_status_report": True,
                                "token_count_in": event.data.get("token_count_in", 0),
                                "token_count_out": event.data.get("token_count_out", 0),
                                "action_id": str(uuid.uuid4())
                            }
                            st.session_state.messages.append(status_message)
                            bound_logger.info(f"Status report message added to session state. Total messages: {len(st.session_state.messages)}")
                            
                            # Remove placeholder
                            st.session_state.messages = [
                                m for m in st.session_state.messages 
                                if not m.get("status_report_placeholder")
                            ]
                            bound_logger.info("Status report placeholder removed")
                        else:
                            bound_logger.warning("Status report completion event received but no content")
                        continue
                    
                    if event.event_type == EventTypes.WORKFLOW_START:
                        # Skip workflow start events (already displayed)
                        bound_logger.info("Skipping WORKFLOW_START event (already displayed)")
                        continue
                    
                    elif event.event_type == EventTypes.LLM_START:
                        # Display LLM start with action ID
                        bound_logger.info(f"Processing LLM_START event: {event.data}")
                        action_id = event.data.get("action_id", "unknown")
                        message_count = event.data.get("message_count", 0)
                        tool_count = event.data.get("tool_count", 0)
                        provider = event.data.get("provider", "unknown")
                        model = event.data.get("model", "unknown")
                        
                        bound_logger.info(f"Displaying LLM start: {provider}/{model} with {message_count} messages, {tool_count} tools")
                        
                        with st.chat_message("assistant"):
                            st.markdown(f"🤔 **LLM Processing** | **Provider:** {provider} | **Model:** {model}")
                            st.markdown(f"📊 **Context:** {message_count} messages, {tool_count} tools available")
                            # st.caption(f"Action ID: `{action_id}`")  # Hide verbose Action ID in transient banner
                        
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": f"🤔 **LLM Processing** | **Provider:** {provider} | **Model:** {model}\n\n📊 **Context:** {message_count} messages, {tool_count} tools available",
                            "workflow_event": True,
                            "action_id": action_id
                            # Note: action_id is included in session state but not displayed in UI (as intended)
                        })
                        
                        bound_logger.info("LLM_START event processed and displayed")
                    
                    elif event.event_type == EventTypes.LLM_COMPLETE:
                        # Display LLM completion with token info
                        bound_logger.info(f"Processing LLM_COMPLETE event: {event.data}")
                        action_id = event.data.get("action_id", "unknown")
                        tokens_in = event.data.get("tokens_in", 0)
                        tokens_out = event.data.get("tokens_out", 0)
                        response_content = event.data.get("response_content", "")
                        tool_calls = event.data.get("tool_calls", [])
                        
                        # Handle tool_calls as either integer count or list of tool calls
                        tool_calls_count = 0
                        tool_calls_list = []
                        if isinstance(tool_calls, int):
                            tool_calls_count = tool_calls
                            tool_calls_list = []
                        elif isinstance(tool_calls, list):
                            tool_calls_count = len(tool_calls)
                            tool_calls_list = tool_calls
                        else:
                            bound_logger.warning(f"Unexpected tool_calls type: {type(tool_calls)}, value: {tool_calls}")
                            tool_calls_count = 0
                            tool_calls_list = []
                        
                        bound_logger.info(f"Displaying LLM completion: {tokens_in} in, {tokens_out} out, {tool_calls_count} tool calls")
                        
                        with st.chat_message("assistant"):
                            if response_content:
                                st.markdown(response_content)
                            
                            if tool_calls_list:
                                tool_names = [tc.get('function', {}).get('name', 'unknown') for tc in tool_calls_list]
                                st.markdown(f"⚙️ **Tool Call(s):** `{', '.join(tool_names)}`")
                            elif tool_calls_count > 0:
                                st.markdown(f"⚙️ **Tool Call(s):** {tool_calls_count} tool(s) called")
                            
                            st.caption(f"Tokens: {tokens_in:,} in, {tokens_out:,} out | Action ID: `{action_id}`")
                        
                        # Add to session state
                        assistant_entry = {
                            "role": "assistant",
                            "token_count_in": tokens_in,
                            "token_count_out": tokens_out,
                            "action_id": action_id
                        }
                        # Only include content and tool_calls if they exist (match UI display)
                        if response_content:
                            assistant_entry["content"] = response_content
                        if tool_calls_list:
                            assistant_entry["tool_calls"] = tool_calls_list
                        # Only mark as transient workflow_event if there is no content
                        if not response_content:
                            assistant_entry["workflow_event"] = True
                        st.session_state.messages.append(assistant_entry)
                        
                        bound_logger.info("LLM_COMPLETE event processed and displayed")
                    
                    elif event.event_type == EventTypes.TOOL_START:
                        # Display tool start
                        action_id = event.data.get("action_id", "unknown")
                        tool_name = event.data.get("tool_name", "unknown")
                        tool_args = event.data.get("tool_args", {})
                        
                        # Build content parts for both UI and session state
                        content_parts = [f"⚙️ **Executing Tool:** `{tool_name}`"]
                        if tool_args:
                            arg_str = str(tool_args)
                            content_parts.append(f"📋 **Arguments:** `{arg_str[:100]}{'...' if len(arg_str) > 100 else ''}`")

                        content = "\n\n".join(content_parts)

                        with st.chat_message("assistant"):
                            st.markdown(content)

                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": content,
                            "workflow_event": True,
                            "action_id": action_id
                        })
                    
                    elif event.event_type == EventTypes.TOOL_COMPLETE:
                        # Display tool completion
                        action_id = event.data.get("action_id", "unknown")
                        tool_name = event.data.get("tool_name", "unknown")
                        tool_result = event.data.get("tool_result", {})
                        execution_time = event.data.get("execution_time", 0)

                        # Compose the same content for both UI and session state
                        content_lines = [f"✅ **Tool Completed:** `{tool_name}`"]
                        if tool_result:
                            result_preview = str(tool_result)[:200]
                            content_lines.append(f"📄 **Result:** `{result_preview}{'...' if len(str(tool_result)) > 200 else ''}`")
                        content = "\n\n".join(content_lines)

                        with st.chat_message("assistant"):
                            for line in content_lines:
                                st.markdown(line)

                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": content,
                            "workflow_event": True,
                            "action_id": action_id
                        })
                    
                    elif event.event_type == EventTypes.WORKFLOW_COMPLETE:
                        # Handle workflow completion
                        bound_logger.info(f"Processing WORKFLOW_COMPLETE event: {event.data}")
                        
                        # Signal that the workflow is completed but continue processing remaining events
                        workflow_completed = True
                        result_container["completed"] = True
                        bound_logger.info("Workflow completion signaled - will continue processing remaining events")
                        # Don't break - continue processing any remaining events
                    
                    elif event.event_type == "status_report_start":
                        # Handle status report start
                        with st.chat_message("assistant"):
                            st.markdown("📝 Preparing status report…")
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": "📝 Preparing status report…",
                            "workflow_event": True,
                            "status_report_placeholder": True
                        })
                    
                    elif event.event_type == "status_report_complete":
                        # This event is already handled in the special handling section above
                        # Skip duplicate processing
                        bound_logger.info("Skipping duplicate status_report_complete event (already processed)")
                        continue
                    
                    elif event.event_type == "status_report_error":
                        # Handle status report error
                        error_msg = event.data.get("error", "Unknown error")
                        with st.chat_message("assistant"):
                            st.markdown(f"❌ **Status report failed:** {error_msg}")
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": f"❌ **Status report failed:** {error_msg}"
                        })
                        
                        # Remove placeholder
                        st.session_state.messages = [
                            m for m in st.session_state.messages 
                            if not m.get("status_report_placeholder")
                        ]
                    
                    elif event.event_type == EventTypes.MESSAGE_ADDED:
                        # Handle actual message content (LLM responses and tool results)
                        # These will be processed when the workflow completes and returns the final messages
                        bound_logger.info(f"Processing MESSAGE_ADDED event: {event.data}")
                        pass
                    else:
                        bound_logger.info(f"Unhandled event type: {event.event_type}")
                
                last_event_count = len(events_received)
                
                # Check if workflow completed during event processing
                if workflow_completed:
                    bound_logger.info("Workflow completion detected after processing all events, exiting main waiting loop")
                    break  # Exit the main while loop
            else:
                # Add periodic logging to show the loop is still running
                if int(time.time() - start_time) % 5 == 0 and int(time.time() - start_time) > 0:
                    bound_logger.debug(f"Waiting for events... (received: {len(events_received)}, completed: {result_container['completed']})")
            
            time.sleep(0.1)  # Check more frequently for real-time updates
        
        # Wait for thread to complete
        workflow_thread.join(timeout=10)
        
        # Remove event handler
        event_emitter.remove_callback(handle_workflow_event)
        
        # Process results (same logic as sync workflow)
        if result_container["error"]:
            st.error(f"Async workflow execution failed: {result_container['error']}")
        elif result_container["result"]:
            result = result_container["result"]
            
            # Defer cleanup of transient workflow_event messages until after we add final results
            
            # Process final messages from workflow (LLM responses and tool results)
            shared_state = result.get("shared_state", {})
            if not isinstance(shared_state, dict):
                shared_state = {}
            # Try multiple sources for final messages (async vs sync)
            new_messages = shared_state.get("final_messages") or result.get("final_messages") or []
            if not isinstance(new_messages, list):
                new_messages = []
            
            # NORMALIZE ASYNC WORKFLOW RESULT: Extract execution statistics from multiple sources
            # Prefer direct result keys first, then async_stats from events, then fallbacks
            loop_count = (result.get("loop_count") or 
                         async_stats.get("loop_count") or 
                         shared_state.get("unguided_mimic_result", {}).get("loop_count") or 
                         0)
            
            max_iterations_reached = (result.get("max_iterations_reached") or 
                                    async_stats.get("max_iterations_reached") or 
                                    shared_state.get("unguided_mimic_result", {}).get("max_iterations_reached") or 
                                    False)
            
            total_tokens_in = (result.get("total_tokens_in") or 
                             async_stats.get("total_tokens_in") or 
                             shared_state.get("unguided_mimic_result", {}).get("total_tokens_in") or 
                             0)
            
            total_tokens_out = (result.get("total_tokens_out") or 
                              async_stats.get("total_tokens_out") or 
                              shared_state.get("unguided_mimic_result", {}).get("total_tokens_out") or 
                              0)
            
            tool_calls_executed = (result.get("tool_calls_executed") or 
                                 async_stats.get("tool_calls_executed") or 
                                 shared_state.get("unguided_mimic_result", {}).get("tool_calls_executed") or 
                                 0)
            
            # Extract executed tools from multiple sources
            executed_tools = []
            # First try direct result
            if result.get("executed_tools"):
                executed_tools = result.get("executed_tools")
            # Then try async_stats from events
            elif async_stats.get("executed_tools"):
                executed_tools = async_stats.get("executed_tools")
            # Finally try tool_results from shared_state
            else:
                flow_result = shared_state.get("unguided_mimic_result") or {}
                tool_results = flow_result.get("tool_results", [])
                if tool_results:
                    tool_names = [result.get("tool_name") for result in tool_results if result.get("tool_name")]
                    # Get unique tool names while preserving order
                    seen = set()
                    executed_tools = [name for name in tool_names if name not in seen and not seen.add(name)]
            
            execution_duration = time.time() - start_time
            
            bound_logger.info(f"Normalized async workflow result: loop_count={loop_count}, max_iterations_reached={max_iterations_reached}, executed_tools={executed_tools}, tokens={total_tokens_in}+{total_tokens_out}")
            
            # Note: Messages are already displayed in real-time during workflow execution
            # No need to redisplay or reprocess them here - just keep them as they are
            
            # Note: No message filtering applied - keeping all messages as requested
            bound_logger.info(f"Keeping all messages in chat history (no filtering applied)")
            # Note: we no longer render the Async Workflow Summary; keep UI minimal
            
            # Use normalized async workflow result for status report decision
            # The normalized values are already calculated above from result + async_stats + fallbacks
            bound_logger.info(f"Status-report decision: loop_count={loop_count}, max_iterations_reached={max_iterations_reached}, executed_tools={len(executed_tools)}")
            bound_logger.info(f"Status report conditions: enable_status_reports={st.session_state.get('enable_status_reports', True)}, max_iterations_reached={max_iterations_reached}, loop_count >= 2={loop_count >= 2}")
            
            # Status reporting is now handled by workflows, not the UI
            
            # Simple completion summary (no redisplaying of messages)
            error_occurred = async_stats.get("error_occurred", False)
            total_tokens = total_tokens_in + total_tokens_out
            
            # Just log the completion - no UI redisplay

            
            if error_occurred:
                bound_logger.info("⚠️ Async workflow completed with errors")
            else:
                bound_logger.info("✅ Async workflow completed successfully")
            
            bound_logger.info(f"Workflow stats: {loop_count} iterations, {tool_calls_executed} tool calls, {total_tokens:,} tokens, {execution_duration:.1f}s")
            

            
            # Note: No completion message added to session state - keep messages as they are
            
            # Update session state tracking like unguided mode
            st.session_state.last_request_duration = execution_duration
            st.session_state.conversation_total_duration = st.session_state.get("conversation_total_duration", 0) + execution_duration
            
            bound_logger.info(f"Async workflow completed: {total_tokens} tokens ({total_tokens_in} in, {total_tokens_out} out) in {execution_duration:.1f}s")
        else:
            st.error("Async workflow execution failed or returned unexpected result")
            
    except Exception as e:
        bound_logger.error(f"Async workflow execution error: {e}", exc_info=True)
        st.error(f"Failed to execute async workflow: {str(e)}")
    finally:
        # Clear execution state for this specific workflow and request
        current_execution = st.session_state.get("current_async_execution", {})
        if (current_execution.get("workflow_name") == workflow_name and 
            current_execution.get("user_request_id") == user_req_id):
            st.session_state.current_async_execution = {
                "workflow_name": workflow_name,
                "user_request_id": user_req_id,
                "executing": False,
                "completed": True
            }
        
        st.session_state.stop_requested = False
        # Force a rerun to update the UI back to input mode
        st.rerun()







# --- User Input Handling --- 
# Defensive reset: if a previous async workflow completed, ensure input UI returns
current_async = st.session_state.get("current_async_execution", {})
if st.session_state.get("llm_executing") and current_async.get("completed"):
    st.session_state.llm_executing = False
    st.session_state.pending_execution = None

# Custom input UI with stop button functionality
is_executing = st.session_state.get("llm_executing", False)
pending_execution = st.session_state.get("pending_execution", None)

# If we have pending execution or are executing, show the stop UI
if is_executing or pending_execution:
    # Show status and stop button when executing or about to execute
    col1, col2 = st.columns([0.85, 0.15])
    with col1:
        if pending_execution:
            st.info(f"🚀 Starting LLM execution... (Max {st.session_state.get('max_loop_iterations', 10)} actions)")
        else:
            st.info(f"🤔 LLM is processing... (Max {st.session_state.get('max_loop_iterations', 10)} actions)")
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
        provider, model_name, base_system_prompt, user_req_id, user_input = pending_execution
        
        # Simplified execution - only async workflow supported
        selected_workflow = st.session_state.get("selected_workflow")
        bound_logger = logger.bind(user_request_id=user_req_id)
        bound_logger.info(f"Async workflow execution: selected_workflow='{selected_workflow}'")
        
        if selected_workflow == "unguided":
            # Run async workflow with integrated real-time UI
            try:
                run_async_workflow_integrated(selected_workflow, provider, model_name, base_system_prompt, user_req_id, user_input)
            except Exception as e:
                bound_logger.error(f"Failed to run async workflow: {e}", exc_info=True)
                st.error(f"Failed to run async workflow: {str(e)}")
        else:
            # No supported workflow selected
            bound_logger.warning(f"No supported workflow selected: '{selected_workflow}'")
            with st.chat_message("assistant"):
                st.markdown("⚠️ **No supported workflow selected.** Only 'unguided' is currently supported.")
            st.session_state.messages.append({
                "role": "assistant",
                "content": "⚠️ **No supported workflow selected.** Only 'unguided' is currently supported."
            })

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
            st.session_state.pending_execution = (provider, model_name, base_system_prompt, user_request_id, user_input)
            st.session_state.stop_requested = False
            
            # Force a rerun to show the executing state
            st.rerun()

# End of file - No changes needed below last removed section 