import sys
import json
import re
from pathlib import Path
from loguru import logger
from contextvars import ContextVar

# Use ContextVar for request-specific context
request_context = ContextVar('request_context', default={})

# Assuming settings.py is in the same directory or accessible
try:
    from .settings import LOGGING_CONFIG, PROJECT_ROOT, get_llm_enqueue_enabled, get_interface_debug_enabled
except ImportError:
    # Fallback for potential execution context issues, adjust as needed
    print("Could not import settings relative to logging_setup. Trying absolute.")
    try:
        from config.settings import LOGGING_CONFIG, PROJECT_ROOT, get_llm_enqueue_enabled, get_interface_debug_enabled
    except ImportError:
        print("FATAL: Could not import LOGGING_CONFIG or PROJECT_ROOT from config.settings")
        # Provide minimal default config to prevent crashing if import fails completely
        PROJECT_ROOT = Path(".") 
        LOGGING_CONFIG = {
            'log_directory': 'logs',
            'interface_debug_enabled': False,
            'console': {'level': 'INFO', 'format': '{time} | {level} | {message}'},
            'client_file': {'enabled': False},
            'server_file': {'enabled': False},
            'llm_debug_file': {},
            'mcp_debug_file': {},
            'nifi_debug_file': {}
        }
        # Fallback functions for when settings can't be imported
        def get_llm_enqueue_enabled():
            return False  # Temporarily disabled to fix macOS semaphore issues
        def get_interface_debug_enabled():
            return False

# Define module patterns for client and server components
CLIENT_MODULES = [
    "nifi_chat_ui",
    "chat_manager",
    "mcp_handler",
    "app",
    "__main__",
]

SERVER_MODULES = [
    "nifi_mcp_server",
    "server",
    "nifi_client",
    "flow_documenter",
]

class SafeJsonEncoder(json.JSONEncoder):
    """Custom JSON encoder that safely handles non-serializable objects"""
    def default(self, obj):
        # Handle Schema objects and other special types from the generative AI library
        if hasattr(obj, '__class__') and 'Schema' in obj.__class__.__name__:
            return str(obj)  # Convert Schema objects to string representation
        if hasattr(obj, 'items') and callable(obj.items):
            try:
                # Try to convert dict-like objects (like MapComposite) to dict
                return dict(obj.items())
            except Exception:
                pass
        if hasattr(obj, '__dict__'):
            try:
                # Try to convert objects with __dict__ to their dictionary representation
                return obj.__dict__
            except Exception:
                pass
        # For all other non-serializable objects, convert to string
        try:
            return str(obj)
        except Exception:
            return f"<Unserializable object of type {type(obj).__name__}>"

# --- Patcher function to add context --- #
def context_patcher(record):
    """Patches log records with context from ContextVar.
       Only updates if the context var has non-default values.
    """
    ctx = request_context.get()
    # Only overwrite if the context var has a non-default value
    req_id_from_ctx = ctx.get("user_request_id", "-")
    if req_id_from_ctx != "-":
        record["extra"]["user_request_id"] = req_id_from_ctx
        
    act_id_from_ctx = ctx.get("action_id", "-")
    if act_id_from_ctx != "-":
        record["extra"]["action_id"] = act_id_from_ctx
        
    workflow_id_from_ctx = ctx.get("workflow_id", "-")
    if workflow_id_from_ctx != "-":
        record["extra"]["workflow_id"] = workflow_id_from_ctx
        
    step_id_from_ctx = ctx.get("step_id", "-") 
    if step_id_from_ctx != "-":
        record["extra"]["step_id"] = step_id_from_ctx
    # Return not strictly needed if called internally by another patcher
# ------------------------------------ #

# Define a middleware handler for interface logging to pre-process the data
def interface_logger_middleware(record):
    """Middleware to pre-process the log record for interface logging."""
    # --- Call context_patcher first --- #
    context_patcher(record)
    # ---------------------------------- #
    
    # Only process records with 'interface' in extra
    if record["extra"].get("interface") is not None:
        # Extract the data we want to log from the record
        try:
            data = record["extra"].get("data", {})
            
            # Serialize the data to JSON using our SafeJsonEncoder
            json_data = json.dumps(data, indent=2, cls=SafeJsonEncoder)
            
            # Store the serialized JSON string back in the record
            record["extra"]["json_data"] = json_data
            
            # Add a field for the formatted message that will be used in the log format string
            record["message"] = f"{record['extra']['interface']} {record['extra']['direction']}: {record['message']}"
        except Exception as e:
            # If anything fails during preprocessing, log it and continue
            record["extra"]["json_data"] = json.dumps({"error": f"Failed to serialize data: {str(e)}"})
    
    # Return the modified record
    return record

def is_client_module(record):
    """Filter function that checks if a log record is from a client module."""
    module_name = record["name"]
    
    # Special case for testing - if we see 'test_client' in the name, count it as a client module
    if 'test_client' in module_name:
        return True
        
    return any(module_name.startswith(client_mod) for client_mod in CLIENT_MODULES)

def is_server_module(record):
    """Filter function that checks if a log record is from a server module."""
    module_name = record["name"]
    
    # Special case for testing - if we see 'test_server' in the name, count it as a server module
    if 'test_server' in module_name:
        return True
        
    return any(module_name.startswith(server_mod) for server_mod in SERVER_MODULES)

def setup_logging(context: str | None = None):
    """Configures Loguru based on LOGGING_CONFIG and execution context."""
    logger.remove() # Remove default handler

    # Configure logger to add default context IDs and patchers
    logger.configure(
        # Keep default extra values for logs outside request context
        extra={"user_request_id": "-", "action_id": "-", "workflow_id": "-", "step_id": "-"},
        # Chain the patchers: context_patcher runs, then interface_logger_middleware
        # Ensure context_patcher runs first by applying it to the record before passing to the next
        # patcher=lambda record: interface_logger_middleware(context_patcher(record))
        # Note: context_patcher always returns the record, so 'or record' isn't strictly needed

        # Apply the combined patcher
        patcher=interface_logger_middleware
    )

    config = LOGGING_CONFIG
    log_dir = PROJECT_ROOT / config.get('log_directory', 'logs')
    log_dir.mkdir(parents=True, exist_ok=True)

    # --- Console Sink (Always Active) ---
    console_config = config.get('console', {})
    console_level = console_config.get('level', 'INFO')
    console_format = console_config.get('format', "{time} | {level} | {message}") # Default format if missing
    logger.add(
        sys.stderr,
        level=console_level.upper(),
        format=console_format,
        colorize=True,
    )

    # --- General File Sinks (Client/Server) ---
    # Define filter functions for client and server logs
    client_filter = is_client_module
    server_filter = is_server_module

    # Shared log format - timestamp | level | message | module:function:line | request_id | action_id | workflow_id | step_id
    file_format = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message} | {name}:{function}:{line} | Req:{extra[user_request_id]} | Act:{extra[action_id]} | Wf:{extra[workflow_id]} | Step:{extra[step_id]}"

    # Client file sink (Only if context is 'client')
    if context == 'client':
        client_config = config.get('client_file', {})
        if client_config.get('enabled', False):
            client_level = client_config.get('level', 'DEBUG')
            client_path_tmpl = client_config.get('path', "{log_directory}/client.log")
            client_path = log_dir / Path(client_path_tmpl.format(log_directory=log_dir.name)).name
            client_format = client_config.get('format', file_format)

            logger.add(
                client_path,
                level=client_level.upper(),
                format=client_format,
                filter=client_filter,  # Only include client module logs
                mode="w",  # Changed back to write/overwrite
                encoding='utf8',
                enqueue=False,  # Disabled to fix macOS semaphore issues
                backtrace=False,  # Disable backtrace for cleaner logs
                diagnose=False,   # Disable diagnosis info for cleaner logs
            )

    # Server file sink (Only if context is 'server')
    if context == 'server':
        server_config = config.get('server_file', {})
        if server_config.get('enabled', False):
            server_level = server_config.get('level', 'DEBUG')
            server_path_tmpl = server_config.get('path', "{log_directory}/server.log")
            server_path = log_dir / Path(server_path_tmpl.format(log_directory=log_dir.name)).name
            server_format = server_config.get('format', file_format)

            logger.add(
                server_path,
                level=server_level.upper(),
                format=server_format,
                filter=server_filter,  # Only include server module logs
                mode="w",  # Changed back to write/overwrite
                encoding='utf8',
                enqueue=False,  # Disabled to fix macOS semaphore issues
                backtrace=False,  # Disable backtrace for cleaner logs
                diagnose=False,   # Disable diagnosis info for cleaner logs
            )

    # --- Interface Debug File Sinks (Conditional) ---
    if get_interface_debug_enabled():
        # Common format for interface logs (LLM/MCP)
        interface_format = """
{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {extra[interface]}-{extra[direction]} | Req:{extra[user_request_id]} | Act:{extra[action_id]}
{extra[json_data]}
--------
"""
        
        # Workflow-specific format (no direction field needed)
        workflow_format = """
{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {extra[interface]} | Req:{extra[user_request_id]} | Wf:{extra[workflow_id]} | Step:{extra[step_id]}
{extra[json_data]}
--------
"""
        
        # Configure LLM and MCP logs if enabled, regardless of context
        for interface_key, interface_name in [
            ('llm_debug_file', 'llm'),
            ('mcp_debug_file', 'mcp'),
        ]:
            debug_config = config.get(interface_key, {})
            if debug_config: # Check if config block exists
                debug_level = debug_config.get('level', 'DEBUG')
                debug_path_tmpl = debug_config.get('path', f"{{log_directory}}/{interface_name}_debug.log")
                debug_path = log_dir / Path(debug_path_tmpl.format(log_directory=log_dir.name)).name

                # Filter to only log messages from this specific interface
                sink_filter = lambda record, name=interface_name: record["extra"].get("interface") == name

                # Use configurable enqueue setting for LLM logs to avoid pickle errors
                use_enqueue = False  # Disabled to fix macOS semaphore issues
                if interface_name == 'llm':
                    use_enqueue = get_llm_enqueue_enabled()

                logger.add(
                    debug_path,
                    level=debug_level.upper(),
                    filter=sink_filter,
                    format=interface_format,  # Use the interface format with direction
                    mode="w", # Changed back to write/overwrite
                    encoding='utf8',
                    enqueue=use_enqueue, # Configurable for LLM logs to resolve pickle issues
                    backtrace=False,  # Disable backtrace for cleaner logs
                    diagnose=False # Disable traceback to avoid recursion issues
                )
        
        # Configure workflow logs separately with workflow-specific format
        workflow_config = config.get('workflow_debug_file', {})
        if workflow_config: # Check if config block exists
            workflow_level = workflow_config.get('level', 'DEBUG')
            workflow_path_tmpl = workflow_config.get('path', "{log_directory}/workflow.log")
            workflow_path = log_dir / Path(workflow_path_tmpl.format(log_directory=log_dir.name)).name

            # Filter to only log messages from workflow interface
            workflow_filter = lambda record: record["extra"].get("interface") == "workflow"

            logger.add(
                workflow_path,
                level=workflow_level.upper(),
                filter=workflow_filter,
                format=workflow_format,  # Use the workflow format without direction
                mode="w", # Changed back to write/overwrite
                encoding='utf8',
                enqueue=False, # Disabled to fix macOS semaphore issues
                backtrace=False,  # Disable backtrace for cleaner logs
                diagnose=False # Disable traceback to avoid recursion issues
            )

        # Configure NiFi log ONLY in server context
        if context == 'server':
            interface_key, interface_name = ('nifi_debug_file', 'nifi')
            debug_config = config.get(interface_key, {})
            if debug_config: # Check if config block exists
                debug_level = debug_config.get('level', 'DEBUG')
                debug_path_tmpl = debug_config.get('path', f"{{log_directory}}/{interface_name}_debug.log")
                debug_path = log_dir / Path(debug_path_tmpl.format(log_directory=log_dir.name)).name

                # Filter to only log messages from this specific interface
                sink_filter = lambda record, name=interface_name: record["extra"].get("interface") == name

                logger.add(
                    debug_path,
                    level=debug_level.upper(),
                    filter=sink_filter,
                    format=interface_format,  # Use the interface format with direction
                    mode="w", # Changed back to write/overwrite
                    encoding='utf8',
                    enqueue=False, # Disabled to fix macOS semaphore issues
                    backtrace=False,  # Disable backtrace for cleaner logs
                    diagnose=False # Disable traceback to avoid recursion issues
                )

    # Log configuration completion only if context was provided
    if context:
        logger.info(f"Logging configured for '{context}' context.")
    else:
         logger.warning("Logging setup called without specific context. Defaulting to console only.")

# Helper function to safely serialize tool declarations for logging
def _serialize_tool(tool_decl):
    if not tool_decl: return None
    # Access attributes directly, handle potential missing parameters attribute
    params_schema = getattr(tool_decl, 'parameters', None) # Safely get parameters
    # Convert schema object to string representation for logging if it exists
    params_repr = str(params_schema) if params_schema else None
    return {
        'name': getattr(tool_decl, 'name', None), 
        'description': getattr(tool_decl, 'description', None), 
        'parameters': params_repr, # Use string representation
    } 