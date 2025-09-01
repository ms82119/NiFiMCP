"""
Chat API endpoints for message handling and workflow execution.

This module provides REST API endpoints for chat functionality,
integrating with the existing workflow executor and LLM components.
"""

import uuid
import asyncio
from typing import Dict, Any, Optional
from pathlib import Path
from fastapi import APIRouter, HTTPException, BackgroundTasks
from loguru import logger

from .models import ChatMessage, ChatResponse, ChatHistoryResponse, ErrorResponse
from .storage import storage
from .websocket_manager import manager

# Import existing components
from nifi_chat_ui.llm.chat_manager import ChatManager
from nifi_mcp_server.workflows.registry import get_workflow_registry
from nifi_mcp_server.workflows.core.executor import GuidedWorkflowExecutor
from config import settings

router = APIRouter(prefix="/api/chat", tags=["Chat"])

# Initialize components
chat_manager = ChatManager(settings.__dict__)
workflow_registry = get_workflow_registry()


@router.post("/", response_model=ChatResponse)
async def submit_chat(message: ChatMessage, background_tasks: BackgroundTasks):
    """Submit a chat message and start workflow execution."""
    try:
        # Generate unique request ID
        request_id = str(uuid.uuid4())
        
        # Broadcast workflow start notification
        await manager.broadcast_json({
            "type": "workflow_start",
            "request_id": request_id,
            "workflow_name": "unguided",
            "timestamp": str(uuid.uuid4())  # Placeholder for now
        })
        
        # Save workflow start to database
        storage.save_workflow(
            request_id=request_id,
            workflow_name="unguided",
            status="started"
        )
        
        # Start workflow execution in background
        try:
            # Temporarily force synchronous execution to debug
            logger.info(f"Executing workflow synchronously for debugging")
            await execute_workflow(
                request_id=request_id,
                user_input=message.content,
                objective=message.objective,
                provider=message.provider or "openai",
                model_name=message.model_name or "gpt-4o-mini",
                nifi_server_id=message.selected_nifi_server_id,
                auto_prune_history=message.auto_prune_history,
                max_tokens_limit=message.max_tokens_limit,
                max_loop_iterations=message.max_loop_iterations
            )
            logger.info(f"Background task added successfully for request {request_id}")
        except Exception as e:
            logger.error(f"Failed to add background task: {e}", exc_info=True)
            # Fallback: execute synchronously
            logger.info(f"Falling back to synchronous execution for request {request_id}")
            await execute_workflow(
                request_id=request_id,
                user_input=message.content,
                objective=message.objective,
                provider=message.provider or "openai",
                model_name=message.model_name or "gpt-4o-mini",
                nifi_server_id=message.selected_nifi_server_id,
                auto_prune_history=message.auto_prune_history,
                max_tokens_limit=message.max_tokens_limit,
                max_loop_iterations=message.max_loop_iterations
            )
        
        logger.info(f"Started workflow execution for request {request_id}")
        
        return ChatResponse(
            status="started",
            request_id=request_id,
            message="Workflow execution started"
        )
        
    except Exception as e:
        logger.error(f"Error submitting chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history", response_model=ChatHistoryResponse)
async def get_chat_history(limit: Optional[int] = 50, request_id: Optional[str] = None):
    """Get chat history from the database."""
    try:
        messages = storage.get_chat_history(limit=limit, request_id=request_id)
        
        return ChatHistoryResponse(
            messages=messages,
            total_count=len(messages)
        )
        
    except Exception as e:
        logger.error(f"Error getting chat history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/history")
async def clear_chat_history():
    """Clear all chat history from the database."""
    try:
        storage.clear_chat_history()
        logger.info("Chat history cleared from database")
        return {"status": "success", "message": "Chat history cleared"}
        
    except Exception as e:
        logger.error(f"Error clearing chat history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/save-complete-response")
async def save_complete_response(request: dict):
    """Save a complete response with all aggregated content."""
    try:
        request_id = request.get("request_id")
        content = request.get("content")
        metadata = request.get("metadata", {})
        
        if not request_id or not content:
            raise HTTPException(status_code=400, detail="Missing request_id or content")
        
        # Save the complete response as an assistant message with UI conversation format
        ui_metadata = metadata.copy() if metadata else {}
        ui_metadata["format"] = "ui_conversation"
        
        storage.save_message(
            request_id=request_id,
            role="assistant",
            content=content,
            provider=metadata.get("provider", "unknown"),
            model_name=metadata.get("model_name", "unknown"),
            nifi_server_id=metadata.get("nifi_server_id"),
            metadata=ui_metadata
        )
        
        logger.info(f"Saved complete response for request {request_id}")
        return {"status": "success"}
        
    except Exception as e:
        logger.error(f"Error saving complete response: {e}")
        raise HTTPException(status_code=500, detail="Failed to save complete response")


@router.get("/workflows/recent")
async def get_recent_workflows(limit: int = 10):
    """Get recent workflows."""
    try:
        workflows = storage.get_recent_workflows(limit=limit)
        return {"workflows": workflows}
        
    except Exception as e:
        logger.error(f"Error getting recent workflows: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workflows/{request_id}")
async def get_workflow_status(request_id: str):
    """Get workflow status for a specific request."""
    try:
        workflow = storage.get_workflow_status(request_id)
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")
        
        return workflow
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting workflow status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def execute_workflow(
    request_id: str,
    user_input: str,
    objective: Optional[str] = None,
    provider: str = "openai",
    model_name: str = "gpt-4o-mini",
    nifi_server_id: Optional[str] = None,
    auto_prune_history: bool = True,
    max_tokens_limit: int = 16000,
    max_loop_iterations: int = 10
):
    """Execute workflow using existing PocketFlow workflow system."""
    try:
        logger.info(f"=== BACKGROUND TASK STARTED for request {request_id} ===")
        logger.info(f"Starting workflow execution for request {request_id}")
        logger.info(f"Input parameters: user_input='{user_input[:50]}...', objective='{objective}', provider='{provider}', model='{model_name}', nifi_server='{nifi_server_id}'")
        
        # Broadcast workflow start
        await manager.broadcast_json({
            "type": "workflow_status",
            "request_id": request_id,
            "status": "started",
            "message": "Workflow execution started...",
            "timestamp": str(uuid.uuid4())
        })
        
        # Get the unguided workflow from registry
        logger.info(f"Getting workflow from registry...")
        workflow_def = workflow_registry.get_workflow("unguided")
        if not workflow_def:
            raise Exception("Unguided workflow not found in registry")
        
        logger.info(f"Using workflow: {workflow_def.name}")
        logger.info(f"Workflow definition: {workflow_def.to_dict()}")
        
        # Load system prompt from file
        system_prompt_path = Path(__file__).parent.parent / "nifi_chat_ui" / "system_prompt.md"
        try:
            with open(system_prompt_path, 'r', encoding='utf-8') as f:
                base_system_prompt = f.read()
        except FileNotFoundError:
            logger.warning(f"System prompt file not found at {system_prompt_path}, using default")
            base_system_prompt = "You are a helpful NiFi assistant. Help the user with their request."
        
        # Add objective to system prompt if provided
        if objective:
            system_prompt = f"{base_system_prompt}\n\nObjective: {objective}"
        else:
            system_prompt = base_system_prompt
        
        # Load LLM conversation history from database
        logger.info("Loading LLM conversation history from database...")
        llm_history = storage.get_llm_conversation_history(limit=50)  # Get recent LLM conversation messages
        
        # Prepare messages for LLM context (format)
        messages_for_context = []
        for msg in llm_history:
            metadata = msg.get('metadata') or {}
            
            if msg['role'] in ['user', 'assistant', 'tool']:
                # Include assistant messages even if content is empty (they might have tool_calls)
                # Include user and tool messages only if they have content
                if msg['role'] == 'assistant' or msg.get('content'):
                    # Format message for LLM context
                    formatted_msg = {
                        'role': msg['role'],
                        'content': msg.get('content', '')  # Use empty string for assistant messages without content
                    }
                    # Add tool_calls for assistant messages if available
                    if msg['role'] == 'assistant' and metadata.get('tool_calls'):
                        formatted_msg['tool_calls'] = metadata['tool_calls']
                    # Add tool_call_id for tool messages if available (put in message, not metadata)
                    if msg['role'] == 'tool' and metadata.get('tool_call_id'):
                        formatted_msg['tool_call_id'] = metadata['tool_call_id']
                    messages_for_context.append(formatted_msg)
                    logger.debug(f"Included LLM message: role={msg['role']}, content_length={len(msg.get('content', ''))}, has_tool_calls={bool(metadata.get('tool_calls'))}")
        
        logger.info(f"Loaded {len(messages_for_context)} LLM conversation messages from database (total LLM messages: {len(llm_history)})")
        
        # Prepare shared state for workflow execution
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
            # Add conversation history for golden context
            "messages": messages_for_context,
            "golden_context": {
                "messages": messages_for_context,
                "objective": objective or "",
                "current_phase": "",
                "metadata": {}
            }
        }
        
        # Execute workflow using the existing PocketFlow system
        logger.info(f"Executing workflow with shared state: {list(shared_state.keys())}")
        logger.info(f"Conversation history: {len(messages_for_context)} messages")
        
        # Create async executor for the workflow
        workflow_executor = workflow_registry.create_async_executor("unguided")
        if not workflow_executor:
            raise Exception("Failed to create async executor for unguided workflow")
        
        # Execute the workflow
        result = await workflow_executor.execute_async(
            initial_context=shared_state
        )
        
        # Update workflow status
        storage.save_workflow(
            request_id=request_id,
            workflow_name="unguided",
            status="completed",
            result=result
        )
        
        logger.info(f"Completed workflow execution for request {request_id}")
        
    except Exception as e:
        logger.error(f"Error executing workflow for request {request_id}: {e}", exc_info=True)
        
        # Update workflow status to failed
        storage.save_workflow(
            request_id=request_id,
            workflow_name="unguided",
            status="failed",
            result={"error": str(e)}
        )
        
        # Broadcast error notification
        await manager.broadcast_json({
            "type": "workflow_complete",
            "request_id": request_id,
            "result": {"error": str(e)},
            "timestamp": str(uuid.uuid4())
        })
