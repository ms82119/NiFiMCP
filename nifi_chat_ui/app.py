import streamlit as st
import json # Import json for formatting tool results
import uuid # Added for context IDs
import time # Added for timing tracking
from loguru import logger # Import logger directly
from st_copy_to_clipboard import st_copy_to_clipboard # Import the new component

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
from chat_manager import get_llm_response, get_formatted_tool_definitions, calculate_input_tokens
from chat_manager import configure_llms, is_initialized
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

# Initialize LLM clients after page config
if not is_initialized:
    logger.info("Initializing LLM clients...")
    configure_llms()

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

# Initialize action limit feedback settings
if "enable_status_reports" not in st.session_state:
    st.session_state.enable_status_reports = True
if "hide_status_reports" not in st.session_state:
    st.session_state.hide_status_reports = False

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

    # --- Phase Selection --- 
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
         
    # --- Auto Prune Settings --- 
    st.markdown("---") # Separator
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
    # --------------------------

    # --- Execution Settings ---
    st.markdown("---") # Separator
    st.subheader("Execution Settings")
    st.checkbox(
        "Enable Status Reports",
        value=True,
        key="enable_status_reports",
        help="When LLM reaches the 10-action limit, automatically request a status report"
    )
    st.checkbox(
        "Hide Status Reports from History",
        value=False, 
        key="hide_status_reports",
        help="Remove status report messages from chat history after display"
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
        status_response = get_llm_response(
            messages=status_messages,
            system_prompt=system_prompt,
            tools=None,  # No tools for status report
            provider=provider,
            model_name=model_name,
            user_request_id=user_req_id
        )
        
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
            if st.session_state.get("hide_status_reports", False):
                # We'll remove it after a delay or on next interaction
                # For now, just mark it for potential removal
                pass
            
    except Exception as e:
        bound_logger.error(f"Failed to generate status report: {e}", exc_info=True)
        # Fail silently - status report is optional
        bound_logger.info("Status report failed, but continuing normally")

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
                      formatted_tools = get_formatted_tool_definitions(
                          provider=provider, 
                          raw_tools=filtered_raw_tools_list, 
                          user_request_id=user_req_id
                      ) 
                      if formatted_tools:
                           current_loop_logger.debug(f"Tools successfully formatted for {provider} ({len(formatted_tools)} tools).")
                      else:
                           current_loop_logger.warning(f"get_formatted_tool_definitions returned None/empty for {provider}.")
                 except Exception as fmt_e:
                      st.error(f"Error formatting tools for {provider} in loop: {fmt_e}")
                      current_loop_logger.error(f"Error formatting tools for {provider}: {fmt_e}", exc_info=True)
                      formatted_tools = None 
            # --------------------------------------------------
                
            # 2. Initialize LLM context messages (AFTER tool prep, BEFORE pruning)
            llm_context_messages = [{"role": "system", "content": effective_system_prompt}]
            llm_context_messages.extend(st.session_state.messages)
            current_loop_logger.debug(f"Initial llm_context_messages length (incl system prompt): {len(llm_context_messages)}")

            # --- Apply Auto-Pruning (if enabled) --- 
            if st.session_state.get("auto_prune_history", False):
                current_loop_logger.info("Auto-pruning enabled. Checking token count...")
                max_tokens = st.session_state.get("max_tokens_limit", 8000)
                
                pruning_iterations = 0
                while pruning_iterations < 200:
                    pruning_iterations += 1
                    try:
                        # PASS formatted_tools to the token calculation here
                        current_tokens = calculate_input_tokens(
                            llm_context_messages, 
                            provider, 
                            model_name, 
                            tools=formatted_tools # Pass the prepared tools
                        )
                        current_loop_logger.debug(f"Current token count (incl. tools): {current_tokens}, Limit: {max_tokens}")
                    except Exception as token_calc_err:
                        current_loop_logger.error(f"Error calculating tokens during pruning: {token_calc_err}", exc_info=True)
                        st.warning("Error calculating tokens, cannot prune history accurately.")
                        break

                    if current_tokens > max_tokens and len(llm_context_messages) > 2:
                        removed_message = llm_context_messages.pop(1)
                        # Recalculate tokens after removal for logging difference (optional but helpful)
                        # Note: Recalculating just for logging adds a bit of overhead
                        try:
                            new_token_count = calculate_input_tokens(llm_context_messages, provider, model_name, tools=formatted_tools)
                            tokens_removed = current_tokens - new_token_count
                        except:
                            tokens_removed = "N/A"
                        current_loop_logger.info(f"Pruning message at index 1 (Role: {removed_message.get('role')}, Approx Tokens Removed: {tokens_removed}) to fit token limit.")
                    else:
                        if current_tokens <= max_tokens:
                            current_loop_logger.debug("Token count is within limit. No more pruning needed.")
                        elif len(llm_context_messages) <= 2:
                            current_loop_logger.warning(f"Cannot prune further. Only system prompt and last user message remain, but token count ({current_tokens}) still exceeds limit ({max_tokens}).")
                        break
                else:
                     current_loop_logger.warning(f"Pruning loop safety break hit after {pruning_iterations} iterations.")

                current_loop_logger.debug(f"Final llm_context_messages length after pruning: {len(llm_context_messages)}")
            else:
                current_loop_logger.debug("Auto-pruning disabled.")
            # ------------------------------------------
            
            # --- LLM Action ID --- # Moved Action ID generation closer to the LLM call
            llm_action_id = str(uuid.uuid4())
            current_loop_logger = current_loop_logger.bind(action_id=llm_action_id)

            # --- Call LLM --- 
            # Note: formatted_tools was prepared BEFORE the pruning loop
            response_data = None
            with st.spinner(f"Thinking... (Step {loop_count}/{max_iterations}) / Tokens: ~{current_tokens if 'current_tokens' in locals() else 'N/A'}"):
                try:
                    current_loop_logger.info(f"Calling LLM ({provider} - {model_name})...")
                    response_data = get_llm_response(
                        messages=llm_context_messages, 
                        system_prompt=effective_system_prompt,
                        tools=formatted_tools,
                        provider=provider,
                        model_name=model_name,
                        user_request_id=user_req_id
                    )
                    if response_data is None:
                         st.error("LLM response was empty.")
                         current_loop_logger.error("LLM response data was None.")
                         break
                    else:
                        current_loop_logger.info(f"Received response from LLM ({provider}).")
                except Exception as e:
                    st.error(f"Error calling LLM API: {e}")
                    current_loop_logger.error(f"Error calling LLM API ({provider}): {e}", exc_info=True)
                    break
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
                        
                        # Format result for the LLM
                        tool_result_content = json.dumps(tool_result) if tool_result is not None else "null"
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
            
            # Optional: Send status report
            if st.session_state.get("enable_status_reports", True):
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
        
        # Call the execution loop
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