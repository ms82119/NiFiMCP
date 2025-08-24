"""
Async Workflow UI Integration for Real-Time Updates

This module provides UI components that integrate with the async workflow
event system to show real-time progress updates.
"""

import asyncio
import time
import threading
from typing import Dict, Any, List, Optional
import streamlit as st
from loguru import logger

from nifi_mcp_server.workflows.core.event_system import get_event_emitter, EventTypes
from nifi_mcp_server.workflows.registry import get_workflow_registry


class AsyncWorkflowUI:
    """
    UI component for real-time async workflow execution.
    """
    
    def __init__(self):
        self.event_emitter = get_event_emitter()
        self.workflow_container = None
        self.progress_container = None
        self.status_container = None
        self.messages_container = None
        
    def setup_ui_containers(self):
        """Set up UI containers for real-time updates."""
        # Create containers for different types of updates
        self.workflow_container = st.container()
        self.progress_container = st.empty()
        self.status_container = st.empty()
        self.messages_container = st.container()
        
    def display_workflow_start(self, workflow_name: str):
        """Display workflow start message."""
        with self.workflow_container:
            st.markdown(f"🚀 **Starting async workflow:** {workflow_name}")
            st.markdown("*Real-time updates will appear below...*")
    
    def update_progress(self, event_data: Dict[str, Any]):
        """Update progress display based on event."""
        event_type = event_data.get("event_type")
        data = event_data.get("data", {})
        
        if event_type == EventTypes.LLM_START:
            with self.status_container:
                st.info(f"🤖 LLM Call Started - {data.get('provider', 'Unknown')} {data.get('model', 'Unknown')}")
                
        elif event_type == EventTypes.LLM_COMPLETE:
            with self.status_container:
                tokens_in = data.get("tokens_in", 0)
                tokens_out = data.get("tokens_out", 0)
                st.success(f"✅ LLM Call Complete - Tokens: {tokens_in}/{tokens_out}")
                
        elif event_type == EventTypes.TOOL_START:
            with self.status_container:
                tool_name = data.get("tool_name", "Unknown")
                tool_index = data.get("tool_index", 1)
                total_tools = data.get("total_tools", 1)
                st.info(f"⚙️ Tool Call Started: {tool_name} ({tool_index}/{total_tools})")
                
        elif event_type == EventTypes.TOOL_COMPLETE:
            with self.status_container:
                tool_name = data.get("tool_name", "Unknown")
                st.success(f"✅ Tool Call Complete: {tool_name}")
                
        elif event_type == EventTypes.MESSAGE_ADDED:
            # Add new message to the UI
            message_role = data.get("message_role")
            if message_role == "assistant":
                # This would be an assistant response
                with self.messages_container:
                    with st.chat_message("assistant"):
                        content_length = data.get("content_length", 0)
                        tool_calls = data.get("tool_calls", 0)
                        if content_length > 0:
                            st.markdown("*Assistant response received*")
                        if tool_calls > 0:
                            st.markdown(f"*Tool calls: {tool_calls}*")
                            
        elif event_type == EventTypes.WORKFLOW_COMPLETE:
            with self.status_container:
                loop_count = data.get("loop_count", 0)
                tool_calls_executed = data.get("tool_calls_executed", 0)
                st.success(f"🎉 Workflow Complete! Iterations: {loop_count}, Tool calls: {tool_calls_executed}")


def run_async_workflow_with_ui(workflow_name: str, provider: str, model_name: str, 
                              base_sys_prompt: str, user_req_id: str):
    """
    Run an async workflow with real-time UI updates.
    
    This is a demonstration of how async workflows can provide real-time feedback.
    """
    bound_logger = logger.bind(user_request_id=user_req_id)
    
    # Create UI handler
    ui = AsyncWorkflowUI()
    ui.setup_ui_containers()
    ui.display_workflow_start(workflow_name)
    
    # Prepare context (same as sync version)
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
    try:
        registry = get_workflow_registry()
        async_executor = registry.create_async_executor(workflow_name)
        
        if not async_executor:
            st.error(f"Failed to create async executor for workflow: {workflow_name}")
            return
        
        # Set up event handling for UI updates
        events_received = []
        
        def handle_event(event):
            """Handle workflow events for UI updates."""
            events_received.append(event)
            # Convert event to dict for UI processing
            event_data = {
                "event_type": event.event_type,
                "workflow_id": event.workflow_id,
                "step_id": event.step_id,
                "data": event.data,
                "timestamp": event.timestamp
            }
            ui.update_progress(event_data)
        
        # Register event handler
        ui.event_emitter.on(handle_event)
        
        # Execute workflow asynchronously
        # Note: Streamlit doesn't directly support async execution in the main thread,
        # so we'll run it in a thread and poll for results
        
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
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        start_time = time.time()
        max_wait_time = 300  # 5 minutes max
        
        while not result_container["completed"] and (time.time() - start_time) < max_wait_time:
            elapsed = time.time() - start_time
            progress = min(elapsed / max_wait_time, 0.95)  # Never show 100% until complete
            progress_bar.progress(progress)
            
            status_text.text(f"Executing workflow... ({len(events_received)} events received)")
            time.sleep(0.5)  # Update every 500ms
        
        # Wait for thread to complete
        workflow_thread.join(timeout=10)
        
        # Clean up progress indicators
        progress_bar.progress(1.0)
        status_text.empty()
        
        # Process results
        if result_container["error"]:
            st.error(f"Workflow execution failed: {result_container['error']}")
            return
        
        result = result_container["result"]
        if result and result.get("status") == "success":
            st.success("✅ Async workflow execution completed successfully!")
            
            # Process and display workflow messages (similar to sync version)
            shared_state = result.get("shared_state", {})
            workflow_messages = shared_state.get("final_messages", [])
            
            if workflow_messages:
                # Calculate new messages (same logic as sync version)
                original_message_count = len(filtered_messages)
                new_messages = workflow_messages[original_message_count:] if len(workflow_messages) > original_message_count else []
                
                bound_logger.info(f"Async workflow completed with {len(new_messages)} new messages")
                
                # Add new messages to session state
                for msg in new_messages:
                    st.session_state.messages.append(msg)
                
                # Display summary
                with st.expander("📊 Workflow Execution Summary", expanded=True):
                    st.write(f"**Events Received:** {len(events_received)}")
                    st.write(f"**New Messages:** {len(new_messages)}")
                    st.write(f"**Execution Time:** {time.time() - start_time:.2f} seconds")
                    
                    if events_received:
                        st.write("**Event Timeline:**")
                        for i, event in enumerate(events_received[-10:]):  # Show last 10 events
                            timestamp = time.strftime("%H:%M:%S", time.localtime(event.timestamp))
                            st.text(f"[{timestamp}] {event.event_type}: {event.data.get('tool_name', event.data.get('action_id', 'N/A'))}")
        else:
            st.error("Workflow execution failed or returned unexpected result")
        
        # Remove event handler
        ui.event_emitter.remove_callback(handle_event)
        
    except Exception as e:
        bound_logger.error(f"Async workflow execution error: {e}", exc_info=True)
        st.error(f"Failed to execute async workflow: {str(e)}")


# Test function for development
def test_async_workflow_ui():
    """Test function for async workflow UI (for development/testing)."""
    st.title("🚀 Async Workflow Test")
    
    if st.button("Test Async Unguided Mimic"):
        # Mock test execution
        run_async_workflow_with_ui(
            workflow_name="unguided",
            provider="openai", 
            model_name="gpt-4o-mini",
            base_sys_prompt="You are a helpful NiFi assistant.",
            user_req_id="test-async-workflow"
        ) 