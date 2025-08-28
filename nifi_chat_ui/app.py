"""
Simplified main app for NiFi Chat UI using the new component architecture.
"""

import streamlit as st
import uuid
import time
from loguru import logger

# Import our new components
from event_handler import EventHandler
from chat_display import ChatDisplay
from workflow_executor import WorkflowExecutor
from sidebar import render_sidebar

# Import existing utilities
from llm.chat_manager import ChatManager
from llm.utils.token_counter import TokenCounter
from llm.mcp.client import MCPClient
from mcp_handler import get_nifi_servers

# Import config
try:
    import sys
    import os
    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    
    from config import settings as config
except ImportError:
    logger.error("Failed to import config.settings. Ensure config/__init__.py exists if needed, or check PYTHONPATH.")
    st.error("Configuration loading failed. Application cannot start.")
    st.stop()

# Set page config MUST be the first Streamlit call
st.set_page_config(page_title="NiFi Chat UI", layout="wide")

# --- Setup Logging --- 
try:
    if "logging_initialized" not in st.session_state:
        from config.logging_setup import setup_logging
        setup_logging(context='client')
        st.session_state.logging_initialized = True
        logger.info("Logging initialized for new session")
    else:
        logger.debug("Logging already initialized for this session")
except ImportError:
    print("Warning: Logging setup failed. Check config/logging_setup.py")

# --- Initialize Components ---
_chat_manager = None
_token_counter = None
_mcp_client = None

def get_chat_manager() -> ChatManager:
    """Get or create the ChatManager instance."""
    global _chat_manager
    
    if _chat_manager is None:
        logger.info("Initializing new modular ChatManager...")
        
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
def initialize_session_state():
    """Initialize all session state variables."""
    # Initialize chat history
    if "messages" not in st.session_state:
        st.session_state.messages = [] 

# Initialize objective
    if "current_objective" not in st.session_state:
        st.session_state.current_objective = ""
    
# Initialize auto-pruning settings
    if "auto_prune_history" not in st.session_state:
        st.session_state.auto_prune_history = False
    if "max_tokens_limit" not in st.session_state:
        st.session_state.max_tokens_limit = 16000

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
    # Flag to trigger UI reruns when new events arrive
    if "has_new_events" not in st.session_state:
        st.session_state.has_new_events = False

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

    # Initialize phase selection
    if "selected_phase" not in st.session_state:
        st.session_state.selected_phase = "All"

# --- Fetch NiFi Servers (once per session) ---
def initialize_nifi_servers():
    """Initialize NiFi server configuration."""
    if "nifi_servers" not in st.session_state:
        st.session_state.nifi_servers = get_nifi_servers()
        if not st.session_state.nifi_servers:
            logger.warning("Failed to retrieve NiFi server list from backend, or no servers configured.")
        else:
            logger.info(f"Retrieved {len(st.session_state.nifi_servers)} NiFi server configurations.")

    if "selected_nifi_server_id" not in st.session_state:
        st.session_state.selected_nifi_server_id = (
            st.session_state.nifi_servers[0]["id"] if st.session_state.nifi_servers else None
        )
        logger.info(f"Initial NiFi server selection set to: {st.session_state.selected_nifi_server_id}")

# --- Load System Prompt --- 
def load_system_prompt():
    """Load the system prompt from file."""
    try:
        with open(SYSTEM_PROMPT_FILE, "r") as f:
            return f.read()
    except FileNotFoundError:
        st.error(f"Error: System prompt file not found at {SYSTEM_PROMPT_FILE}")
        return "You are a helpful NiFi assistant."
    except Exception as e:
        st.error(f"Error reading system prompt file: {e}")
        return "You are a helpful NiFi assistant."

# --- Main UI Components ---
def render_main_interface():
    """Render the main chat interface."""
    st.title("NiFi Chat UI")

    # Objective input panel
    st.text_area(
        "Define the overall objective for this session:", 
        key="objective_input",
        value=st.session_state.current_objective, 
        height=100,
        on_change=lambda: st.session_state.update(current_objective=st.session_state.objective_input) 
    )
    st.markdown("---")

    # Display chat history using our new component
    chat_display.display_chat_history()
    
    # Debug: Log that polling is running
    logger.debug("render_main_interface: polling for completion signals")
    
    # Check for new events and refresh UI (polling mechanism)
    has_new_events = st.session_state.get("has_new_events", False)
    completion_time = st.session_state.get("workflow_completion_time", 0)
    current_async = st.session_state.get("current_async_execution", {})
    
    # Check for file-based completion signals (workaround for thread isolation)
    file_signal_detected = False
    import os
    import tempfile
    import glob
    
    # Look for ANY completion signal files (don't depend on current_async state)
    pattern = os.path.join(tempfile.gettempdir(), "nifi_workflow_complete_*.signal")
    signal_files = glob.glob(pattern)
    
    if signal_files:
        try:
            # Read and cleanup signal file(s)
            for signal_file in signal_files:
                with open(signal_file, 'r') as f:
                    file_completion_time = float(f.read().strip())
                os.remove(signal_file)
                file_signal_detected = True
                logger.info(f"Detected workflow completion via file signal: {signal_file}")
        except Exception as e:
            logger.error(f"Error processing completion signal file: {e}")
    
    logger.debug(f"Polling: has_new_events={has_new_events}, completion_time={completion_time}, file_signal={file_signal_detected}, async.completed={current_async.get('completed', False)}")
    
    if has_new_events or file_signal_detected:
        logger.info("Detected new events or completion signal; triggering UI refresh")
        st.session_state.has_new_events = False
        # Clear completion time once processed
        if completion_time:
            st.session_state.workflow_completion_time = 0
        st.rerun()

    # Show execution status during workflow and ensure periodic polling
    if st.session_state.get("llm_executing", False):
        st.info("🔄 Workflow is executing... Please wait for completion.")
        
        # Force periodic reruns during execution to ensure completion signal detection
        import time
        current_time = time.time()
        last_poll_time = st.session_state.get("last_poll_time", 0)
        
        # Rerun every 2 seconds during execution to check for completion signals
        if current_time - last_poll_time > 2.0:
            st.session_state.last_poll_time = current_time
            logger.debug("Forcing periodic rerun during execution for completion signal detection")
            st.rerun()

def render_user_input():
    """Render user input handling."""
    # No speculative completion handling; rely on executor's current_async_execution

    # --- Compute unified running state and process pending immediately ---
    pending_execution = st.session_state.get("pending_execution", None)
    current_async = st.session_state.get("current_async_execution", {})
    async_executing_flag = bool(current_async.get("executing", False))
    logger.debug(
        f"Render input (pre): pending={'yes' if pending_execution else 'no'}, "
        f"async.executing={async_executing_flag}, async.completed={current_async.get('completed', False)}"
    )

    # Unified UI running flag: only pending or executor's executing
    is_running_ui = bool(pending_execution or async_executing_flag)
    logger.debug(f"UI running flag={is_running_ui}")

    # If we have pending execution or are executing, show the stop UI
    if is_running_ui:
        # Show status and stop button when executing or about to execute
        col1, col2 = st.columns([0.85, 0.15])
        with col1:
            if pending_execution:
                st.info(f"🚀 Starting LLM execution... (Max {st.session_state.get('max_loop_iterations', 10)} actions)")
            else:
                st.info(f"🤔 LLM is processing... (Max {st.session_state.get('max_loop_iterations', 10)} actions)")
        with col2:
            if st.button("🛑 Stop", key="stop_btn", help="Stop LLM execution", use_container_width=True):
                logger.info("Stop clicked: clearing pending and llm_executing; rerunning UI")
                st.session_state.stop_requested = True
                # Clear pending execution if stopping before it starts
                if pending_execution:
                    st.session_state.pending_execution = None
                # Also reset execution state to return to input mode
                st.session_state.llm_executing = False
                st.rerun()
                
        # Start pending execution after rendering stop UI (so the stop button is visible first)
        if pending_execution and not async_executing_flag:
            logger.debug("Starting pending execution after rendering stop UI")
            _process_pending_execution(pending_execution)

    else:
        # Show normal input when not executing and no pending execution
        col1, col2 = st.columns([0.85, 0.15])
        with col1:
            user_input = st.text_area(
                "Message",
                placeholder="What can I help you with?",
                key=f"user_input_field_{st.session_state.input_counter}",
                label_visibility="collapsed",
                height=100,
                max_chars=None
            )
        with col2:
            send_button = st.button("▶️ Send", key="send_btn", help="Send message", use_container_width=True)
        
        # Display session statistics under the input box
        _display_session_statistics()
        
        # Process input when send button is clicked
        if send_button and user_input.strip():
            logger.debug("Send clicked: processing user input")
            _process_user_input(user_input)

def _process_pending_execution(pending_execution):
    """Process pending execution."""
    # Start the execution
    logger.debug("_process_pending_execution: clearing pending_execution and starting executor")
    st.session_state.pending_execution = None

    # Extract execution parameters
    provider, model_name, base_system_prompt, user_req_id, user_input = pending_execution

    # Get selected workflow
    selected_workflow = st.session_state.get("selected_workflow")
    bound_logger = logger.bind(user_request_id=user_req_id)
    bound_logger.info(f"Async workflow execution: selected_workflow='{selected_workflow}'")

    if selected_workflow == "unguided":
        # Run async workflow with integrated real-time UI
        try:
            workflow_executor.run_async_workflow(
                selected_workflow, provider, model_name, base_system_prompt, user_req_id, user_input
            )
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
            "content": "⚠️ **No supported workflow selected.** Only 'unguided' is currently supported.",
        })

def _display_session_statistics():
    """Display session statistics under the input box."""
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
    
def _process_user_input(user_input: str):
    """Process user input and set up execution."""
    # Clear any pending workflow completion state to avoid conflicts
    st.session_state.has_new_events = False
    
    if not provider:
        st.error("Please configure an API key and select a provider.")
        logger.warning("User submitted prompt but no provider was configured/selected.")
        return

    # Clear the input field by incrementing the counter
    st.session_state.input_counter += 1

    user_request_id = str(uuid.uuid4())
    bound_logger = logger.bind(user_request_id=user_request_id)
    bound_logger.info(f"Received new user prompt (Provider: {provider})")

    # Add user message to history via event handler
    event_handler.add_user_message(user_input, user_request_id)

    # Set up pending execution (will be processed on next run)
    st.session_state.pending_execution = (provider, model_name, base_system_prompt, user_request_id, user_input)
    st.session_state.stop_requested = False
    # Clear any lingering execution state
    st.session_state.llm_executing = False

    logger.debug(f"_process_user_input: queued pending_execution for request_id={user_request_id}; triggering rerun")
    # Force a rerun to immediately render the stop UI and start pending on next pass
    st.rerun()

# --- Main Application ---
def main():
    """Main application entry point."""
    # Initialize session state
    initialize_session_state()
    initialize_nifi_servers()
    
    # Load system prompt
    global base_system_prompt
    base_system_prompt = load_system_prompt()
    
    # Initialize our new components
    global event_handler, chat_display, workflow_executor, provider, model_name
    event_handler = EventHandler()
    chat_display = ChatDisplay(event_handler)
    workflow_executor = WorkflowExecutor(event_handler, chat_display)
    
    # Render sidebar and get selected options
    with st.sidebar:
        provider, model_name, selected_workflow = render_sidebar(config)
    
    # Render main interface
    render_main_interface()
    
    # Render user input
    render_user_input()

if __name__ == "__main__":
    main()
