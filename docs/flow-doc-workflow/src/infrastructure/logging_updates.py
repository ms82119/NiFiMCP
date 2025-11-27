"""
Logging infrastructure updates for documentation workflow.

Apply these changes to: config/logging_setup.py

1. Update workflow_format to include phase tracking
2. Update context_patcher to handle phase field
3. Update interface_logger_middleware to extract phase/event data
4. Update logger.configure defaults
"""

# =============================================================================
# 1. ENHANCED WORKFLOW LOG FORMAT
# =============================================================================

WORKFLOW_FORMAT_UPDATE = """
# Replace existing workflow_format (around line 244) with:

workflow_format = '''
{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {extra[interface]} | Phase:{extra[phase]:<12} | Req:{extra[user_request_id]} | Wf:{extra[workflow_id]} | Step:{extra[step_id]}
Event: {extra[event_type]}
Progress: {extra[progress_message]}
{extra[json_data]}
--------
'''
"""


# =============================================================================
# 2. CONTEXT PATCHER UPDATES
# =============================================================================

CONTEXT_PATCHER_UPDATE = """
# Update context_patcher function (around line 235) to add phase tracking:

def context_patcher(record):
    \"\"\"Patches log records with context from ContextVar.\"\"\"
    ctx = request_context.get()
    
    # Existing context fields
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
    
    # NEW: Phase tracking for documentation workflow
    phase_from_ctx = ctx.get("phase", "-")
    if phase_from_ctx != "-":
        record["extra"]["phase"] = phase_from_ctx
    
    return record
"""


# =============================================================================
# 3. INTERFACE LOGGER MIDDLEWARE UPDATES
# =============================================================================

INTERFACE_MIDDLEWARE_UPDATE = """
# Update interface_logger_middleware function to handle workflow events:

def interface_logger_middleware(record):
    \"\"\"Middleware to pre-process the log record for interface logging.\"\"\"
    context_patcher(record)
    
    # Only process records with 'interface' in extra
    if record["extra"].get("interface") is not None:
        try:
            data = record["extra"].get("data", {})
            
            # Extract phase from data if present (for workflow logs)
            if record["extra"].get("interface") == "workflow":
                phase = data.get("phase", record["extra"].get("phase", "-"))
                record["extra"]["phase"] = phase
                
                # Extract event type if present
                event_type = data.get("event_type", "-")
                record["extra"]["event_type"] = event_type
                
                # Extract progress message if present
                progress_msg = data.get("message", data.get("progress_message", ""))
                record["extra"]["progress_message"] = progress_msg
            
            json_data = json.dumps(data, indent=2, cls=SafeJsonEncoder)
            record["extra"]["json_data"] = json_data
            
        except Exception as e:
            record["extra"]["json_data"] = json.dumps({"error": f"Failed to serialize: {e}"})
    
    return record
"""


# =============================================================================
# 4. LOGGER CONFIGURE DEFAULTS
# =============================================================================

LOGGER_DEFAULTS_UPDATE = """
# Update logger.configure() call to include new default fields:

logger.configure(
    extra={
        "user_request_id": "-", 
        "action_id": "-", 
        "workflow_id": "-", 
        "step_id": "-",
        "phase": "-",           # NEW
        "event_type": "-",      # NEW
        "progress_message": ""  # NEW
    },
    patcher=interface_logger_middleware
)
"""

