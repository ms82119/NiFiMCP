"""
Chat API Updates for Documentation Workflow

These updates add workflow selection to the chat API, allowing the
documentation workflow to be invoked via the same endpoint.

Apply these changes to: api/chat_api.py and api/models.py
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel


# ============================================================================
# Model Updates (api/models.py)
# ============================================================================

class ChatMessageUpdated(BaseModel):
    """
    Updated ChatMessage model with workflow selection.
    
    Replace the existing ChatMessage in api/models.py
    """
    content: str
    objective: Optional[str] = None
    provider: Optional[str] = "openai"
    model_name: Optional[str] = "gpt-4o-mini"
    selected_nifi_server_id: Optional[str] = None
    auto_prune_history: bool = True
    max_tokens_limit: int = 32000
    max_loop_iterations: int = 10
    
    # NEW: Workflow selection
    workflow_name: Optional[str] = None  # "unguided" | "flow_documentation" | None (defaults to unguided)
    
    # NEW: Documentation-specific options (only used with flow_documentation workflow)
    process_group_id: Optional[str] = None  # Target PG for documentation (defaults to "root")


# ============================================================================
# API Endpoint Updates (api/chat_api.py)
# ============================================================================

async def execute_workflow_updated(
    request_id: str,
    user_input: str,
    objective: Optional[str] = None,
    provider: str = "openai",
    model_name: str = "gpt-4o-mini",
    nifi_server_id: Optional[str] = None,
    auto_prune_history: bool = True,
    max_tokens_limit: int = 32000,
    max_loop_iterations: int = 10,
    # NEW parameters
    workflow_name: Optional[str] = None,
    process_group_id: Optional[str] = None
):
    """
    Execute workflow using existing PocketFlow workflow system.
    
    Updated version that supports workflow selection.
    """
    try:
        logger.info(f"=== WORKFLOW EXECUTION STARTED for request {request_id} ===")
        
        # Determine workflow to use
        selected_workflow = workflow_name or "unguided"
        logger.info(f"Selected workflow: {selected_workflow}")
        
        # Broadcast workflow start
        await manager.broadcast_json({
            "type": "workflow_status",
            "request_id": request_id,
            "status": "started",
            "message": f"Starting {selected_workflow} workflow...",
            "timestamp": str(uuid.uuid4())
        })
        
        # Get the workflow from registry
        workflow_def = workflow_registry.get_workflow(selected_workflow)
        if not workflow_def:
            raise Exception(f"Workflow '{selected_workflow}' not found in registry")
        
        logger.info(f"Using workflow: {workflow_def.name}")
        
        # Prepare shared state based on workflow type
        if selected_workflow == "flow_documentation":
            # Documentation workflow specific state
            shared_state = {
                "user_request_id": request_id,
                "process_group_id": process_group_id or "root",
                "provider": provider,
                "model_name": model_name,
                "selected_nifi_server_id": nifi_server_id,
                "workflow_name": "flow_documentation"
            }
            # Note: Documentation workflow doesn't use conversation history
            # It works with NiFi API data, not chat messages
            
        else:
            # Standard unguided workflow state (existing behavior)
            # Load system prompt
            system_prompt_path = Path(__file__).parent.parent / "nifi_chat_ui" / "system_prompt.md"
            try:
                with open(system_prompt_path, 'r', encoding='utf-8') as f:
                    system_prompt = f.read()
            except Exception as e:
                logger.warning(f"Could not load system prompt: {e}")
                system_prompt = "You are a NiFi assistant."
            
            # Get conversation history
            # ... (existing history loading code)
            messages_for_context = []  # Placeholder - use existing code
            
            shared_state = {
                "user_request_id": request_id,
                "user_prompt": user_input,
                "objective": objective,
                "provider": provider,
                "model_name": model_name,
                "selected_nifi_server_id": nifi_server_id,
                "system_prompt": system_prompt,
                "max_loop_iterations": max_loop_iterations,
                "max_tokens_limit": max_tokens_limit,
                "auto_prune_history": auto_prune_history,
                "messages": messages_for_context,
                "workflow_name": "unguided"
            }
        
        logger.info(f"Executing workflow with shared state keys: {list(shared_state.keys())}")
        
        # Create async executor for the workflow
        workflow_executor = workflow_registry.create_async_executor(selected_workflow)
        if not workflow_executor:
            raise Exception(f"Failed to create async executor for {selected_workflow} workflow")
        
        # Execute the workflow
        result = await workflow_executor.execute_async(
            initial_context=shared_state
        )
        
        # Update workflow status
        storage.save_workflow(
            request_id=request_id,
            workflow_name=selected_workflow,
            status="completed",
            result=result
        )
        
        logger.info(f"Completed workflow execution for request {request_id}")
        
    except Exception as e:
        logger.error(f"Workflow execution failed: {e}", exc_info=True)
        
        # Broadcast error
        await manager.broadcast_json({
            "type": "workflow_complete",
            "request_id": request_id,
            "result": {"error": str(e)},
            "timestamp": str(uuid.uuid4())
        })
        
        # Update workflow status
        storage.save_workflow(
            request_id=request_id,
            workflow_name=workflow_name or "unguided",
            status="error",
            result={"error": str(e)}
        )


# ============================================================================
# Submit Chat Update (api/chat_api.py)
# ============================================================================

"""
Update the submit_chat endpoint to pass workflow parameters:

@router.post("/", response_model=ChatResponse)
async def submit_chat(message: ChatMessage, background_tasks: BackgroundTasks):
    try:
        request_id = str(uuid.uuid4())
        
        # Determine workflow
        workflow_name = message.workflow_name or "unguided"
        
        # Broadcast workflow start
        await manager.broadcast_json({
            "type": "workflow_start",
            "request_id": request_id,
            "workflow_name": workflow_name,
            "timestamp": str(uuid.uuid4())
        })
        
        # Save workflow start
        storage.save_workflow(
            request_id=request_id,
            workflow_name=workflow_name,
            status="started"
        )
        
        # Execute workflow with all parameters
        await execute_workflow(
            request_id=request_id,
            user_input=message.content,
            objective=message.objective,
            provider=message.provider or "openai",
            model_name=message.model_name or "gpt-4o-mini",
            nifi_server_id=message.selected_nifi_server_id,
            auto_prune_history=message.auto_prune_history,
            max_tokens_limit=message.max_tokens_limit,
            max_loop_iterations=message.max_loop_iterations,
            # NEW parameters
            workflow_name=message.workflow_name,
            process_group_id=message.process_group_id
        )
        
        return ChatResponse(
            status="started",
            request_id=request_id,
            message=f"{workflow_name} workflow execution started"
        )
        
    except Exception as e:
        logger.error(f"Error submitting chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
"""


# ============================================================================
# Alternative: Dedicated Documentation Endpoint
# ============================================================================

"""
If you prefer a separate endpoint for documentation, add this to chat_api.py:

@router.post("/document-flow", response_model=ChatResponse)
async def document_flow(
    process_group_id: str = "root",
    provider: Optional[str] = "openai",
    model_name: Optional[str] = "gpt-4",
    nifi_server_id: Optional[str] = None
):
    '''Generate documentation for a NiFi flow.'''
    try:
        request_id = str(uuid.uuid4())
        
        # Broadcast workflow start
        await manager.broadcast_json({
            "type": "workflow_start",
            "request_id": request_id,
            "workflow_name": "flow_documentation",
            "timestamp": str(uuid.uuid4())
        })
        
        # Save workflow start
        storage.save_workflow(
            request_id=request_id,
            workflow_name="flow_documentation",
            status="started"
        )
        
        # Prepare state
        shared_state = {
            "user_request_id": request_id,
            "process_group_id": process_group_id,
            "provider": provider,
            "model_name": model_name,
            "selected_nifi_server_id": nifi_server_id,
            "workflow_name": "flow_documentation"
        }
        
        # Create and execute workflow
        workflow_executor = workflow_registry.create_async_executor("flow_documentation")
        if not workflow_executor:
            raise Exception("Failed to create flow_documentation executor")
        
        result = await workflow_executor.execute_async(initial_context=shared_state)
        
        # Update status
        storage.save_workflow(
            request_id=request_id,
            workflow_name="flow_documentation",
            status="completed",
            result=result
        )
        
        return ChatResponse(
            status="started",
            request_id=request_id,
            message="Flow documentation workflow started"
        )
        
    except Exception as e:
        logger.error(f"Error starting flow documentation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
"""


# ============================================================================
# Integration Instructions
# ============================================================================
#
# Option A: Extend existing endpoint (recommended for consistency)
#
# 1. Update ChatMessage in api/models.py to add:
#    - workflow_name: Optional[str] = None
#    - process_group_id: Optional[str] = None
#
# 2. Update execute_workflow in api/chat_api.py to:
#    - Accept workflow_name and process_group_id parameters
#    - Use workflow selection logic shown above
#    - Prepare different shared_state based on workflow type
#
# 3. Update submit_chat in api/chat_api.py to:
#    - Pass workflow_name and process_group_id to execute_workflow
#
#
# Option B: Add dedicated endpoint
#
# 1. Add the document_flow endpoint shown above
# 2. This keeps the unguided endpoint unchanged
# 3. Provides clearer API separation
#
# ============================================================================

