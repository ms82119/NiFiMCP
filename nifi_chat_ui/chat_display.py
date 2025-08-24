"""
Chat display component for aggregated workflow responses.
"""

import streamlit as st
from typing import Dict, Any, List
from loguru import logger

from event_handler import EventHandler


class ChatDisplay:
    """Display aggregated workflow responses in chat interface."""
    
    def __init__(self, event_handler: EventHandler):
        self.session_state = st.session_state
        self.event_handler = event_handler
    
    def display_chat_history(self) -> None:
        """Display all chat history with aggregated workflow responses."""
        logger.info("ChatDisplay: display_chat_history() called - UI is refreshing!")
        
        # Debug: Log what's in session state
        logger.info(f"ChatDisplay: Found {len(self.session_state.messages)} messages in session state")
        if len(self.session_state.messages) == 0:
            logger.info("ChatDisplay: No messages found, displaying empty state")
            st.info("No messages yet. Start a conversation!")
            return
            
        for i, msg in enumerate(self.session_state.messages):
            logger.info(f"ChatDisplay: Message {i}: role={msg.get('role')}, user_request_id={msg.get('user_request_id')}, content_length={len(str(msg.get('content', '')))}")
        
        # Group messages by user_request_id
        workflow_groups = self.group_messages_by_workflow()
        
        logger.info(f"ChatDisplay: Grouped into {len(workflow_groups)} workflow groups")
        for user_request_id, messages in workflow_groups.items():
            logger.info(f"ChatDisplay: Group {user_request_id}: {len(messages)} messages")
            self.display_workflow_response(user_request_id, messages)
    
    def group_messages_by_workflow(self) -> Dict[str, List[Dict[str, Any]]]:
        """Group messages by user_request_id."""
        groups = {}
        for message in self.session_state.messages:
            user_request_id = message.get("user_request_id")
            if user_request_id:
                if user_request_id not in groups:
                    groups[user_request_id] = []
                groups[user_request_id].append(message)
        return groups
    
    def display_workflow_response(self, user_request_id: str, messages: List[Dict[str, Any]]) -> None:
        """Display a single workflow response bubble."""
        # Find user message
        user_message = next((m for m in messages if m["role"] == "user"), None)
        if not user_message:
            logger.warning(f"No user message found for request {user_request_id}")
            return
        
        # Debug: Log the user message details
        logger.info(f"ChatDisplay: Displaying user message - role={user_message.get('role')}, content_length={len(str(user_message.get('content', '')))}")
        
        # Get workflow summary
        summary = self.event_handler.get_workflow_summary(user_request_id)
        
        # Display user message
        with st.chat_message("user"):
            st.markdown(user_message["content"])
            st.caption(f"Request ID: `{user_request_id}`")
        
        # Determine if we have anything meaningful to show in assistant bubble
        aggregated_content = self.session_state.get("aggregated_content", {}).get(user_request_id, [])
        aggregated_content = [p for p in aggregated_content if isinstance(p, str) and p.strip()]
        assistant_messages = [m for m in messages if m["role"] == "assistant"]
        fallback_display_messages = [
            msg for msg in assistant_messages 
            if not msg.get("status_report_placeholder") and 
               not msg.get("workflow_event") and 
               msg.get("content") and str(msg.get("content")).strip()
        ]
        has_running_status = bool(summary.get("is_running") and summary.get("current_event"))
        has_stats = any([
            summary.get("total_tokens", 0) > 0,
            summary.get("tools_executed", 0) > 0,
            summary.get("messages_in_context", 0) > 0,
            summary.get("duration", 0) > 0,
            bool(summary.get("model"))
        ])
        should_render_assistant = bool(aggregated_content or fallback_display_messages or has_running_status or has_stats)
        
        if not should_render_assistant:
            logger.info(f"ChatDisplay: Skipping empty assistant bubble for {user_request_id}")
            return
        
        # Display aggregated assistant response only when we have content or status
        with st.chat_message("assistant"):
            # Display aggregated content or fallback
            self.display_aggregated_response(messages)
            
            # Display execution summary
            self.display_execution_summary(summary)
    
    def display_aggregated_response(self, messages: List[Dict[str, Any]]) -> None:
        """Display aggregated LLM responses and tool calls."""
        user_request_id = messages[0].get("user_request_id") if messages else None
        if not user_request_id:
            return
        
        # Get workflow summary to check if it's still running
        summary = self.event_handler.get_workflow_summary(user_request_id)
        is_running = summary.get("is_running", False)
        
        # Get aggregated content for this workflow
        aggregated_content = self.session_state.get("aggregated_content", {}).get(user_request_id, [])
        
        if aggregated_content:
            # Display aggregated content as a single response (transient banners excluded upstream)
            filtered = [p for p in aggregated_content if isinstance(p, str) and p.strip()]
            full_content = "\n\n---\n\n".join(filtered)
            if full_content.strip():
                st.markdown(full_content)
                logger.info(f"ChatDisplay: Displayed aggregated content for {user_request_id}: {len(filtered)} parts")
        else:
            # Fallback to individual messages if no aggregated content
            assistant_messages = [m for m in messages if m["role"] == "assistant"]
            display_messages = [
                msg for msg in assistant_messages 
                if not msg.get("status_report_placeholder") and 
                   not msg.get("workflow_event") and 
                   msg.get("content") and msg.get("content").strip()  # Only show messages with actual content
            ]
            
            # Only show messages with actual content
            for msg in display_messages:
                if "content" in msg and msg["content"] and msg["content"].strip():
                    st.markdown(msg["content"])
                
                if "tool_calls" in msg and msg["tool_calls"]:
                    tool_names = [tc.get('function', {}).get('name', 'unknown') 
                                for tc in msg.get("tool_calls", [])]
                    st.markdown(f"⚙️ **Tool Call(s):** `{', '.join(tool_names)}`")
        
        # Show execution summary if we have statistics
        if not is_running:
            self._display_execution_statistics(user_request_id)
    
    def display_real_time_message(self, message_data) -> None:
        """Display a message in real-time during workflow execution."""
        # Display the message immediately in the UI
        if message_data.role == "assistant":
            with st.chat_message("assistant"):
                if message_data.content:
                    st.markdown(message_data.content)
                if message_data.tool_calls:
                    tool_names = [tc.get('function', {}).get('name', 'unknown') 
                                for tc in message_data.tool_calls]
                    st.markdown(f"⚙️ **Tool Call(s):** `{', '.join(tool_names)}`")
        elif message_data.role == "tool":
            with st.chat_message("assistant"):
                st.markdown(f"🔧 **Tool Result:** {len(str(message_data.content or ''))} characters")
        
        logger.info(f"ChatDisplay: Real-time message displayed: {message_data.role} - {len(str(message_data.content or ''))} chars")
    
    def _display_execution_statistics(self, user_request_id: str) -> None:
        """Display execution statistics for the completed workflow."""
        # Get the last request statistics from session state
        last_tokens_in = st.session_state.get("last_request_tokens_in", 0)
        last_tokens_out = st.session_state.get("last_request_tokens_out", 0)
        last_duration = st.session_state.get("last_request_duration", 0)
        
        if last_tokens_in > 0 or last_tokens_out > 0:
            st.markdown("---")
            st.markdown("**📊 Execution Summary:**")
            
            stats_parts = []
            if last_tokens_in > 0:
                stats_parts.append(f"📥 {last_tokens_in:,} tokens in")
            if last_tokens_out > 0:
                stats_parts.append(f"📤 {last_tokens_out:,} tokens out")
            if last_duration > 0:
                stats_parts.append(f"⏱️ {last_duration:.1f}s")
            
            if stats_parts:
                st.markdown(" | ".join(stats_parts))
    
    def display_execution_summary(self, summary: Dict[str, Any]) -> None:
        """Display execution summary at bottom of response."""
        if not summary:
            return
        
        # Show current status if running
        if summary.get("is_running") and summary.get("current_event"):
            st.markdown("---")
            st.markdown(f"🔄 **Currently:** {summary['current_event']}")
        
        # Show summary stats
        summary_parts = []
        
        # Model info
        if summary.get("model"):
            provider = summary.get("provider", "unknown")
            summary_parts.append(f"🤖 {provider}/{summary['model']}")
        
        # Token counts
        if summary.get("total_tokens", 0) > 0:
            summary_parts.append(f"📊 {summary['total_tokens']:,} tokens")
        
        # Tool execution
        if summary.get("tools_executed", 0) > 0:
            summary_parts.append(f"🔧 {summary['tools_executed']} tools")
        
        # Context info
        if summary.get("messages_in_context", 0) > 0:
            summary_parts.append(f"💬 {summary['messages_in_context']} messages")
        
        # Duration
        if summary.get("duration", 0) > 0:
            summary_parts.append(f"⏱️ {summary['duration']:.1f}s")
        
        # Workflow name
        if summary.get("workflow_name"):
            summary_parts.append(f"📋 {summary['workflow_name']}")
        
        # Display summary
        if summary_parts:
            st.caption(" | ".join(summary_parts))
        
        # Show error if any
        if summary.get("is_error"):
            st.error(f"❌ Workflow failed: {summary.get('current_event', 'Unknown error')}")
    
    def display_real_time_message(self, message_data) -> None:
        """Display a message in real-time during workflow execution."""
        # This is called during workflow execution for immediate display
        # We'll implement this when we integrate with the workflow executor
        pass
    
    def format_conversation_for_copy(self, objective: str, messages: List[Dict[str, Any]]) -> str:
        """Format the objective and chat messages into a single string for copying."""
        lines = []
        if objective and objective.strip():
            lines.append("## Overall Objective:")
            lines.append(objective.strip())
            lines.append("\n---")

        lines.append("## Conversation History:")
        
        # Group messages by workflow for better formatting
        workflow_groups = self.group_messages_by_workflow()
        
        for user_request_id, workflow_messages in workflow_groups.items():
            # Find user message
            user_message = next((m for m in workflow_messages if m["role"] == "user"), None)
            if user_message:
                lines.append(f"\n**User:**\n{user_message['content']}")
                
                # Get workflow summary
                summary = self.event_handler.get_workflow_summary(user_request_id)
                
                # Format assistant response
                assistant_messages = [m for m in workflow_messages if m["role"] == "assistant"]
                display_messages = [
                    msg for msg in assistant_messages 
                    if not msg.get("workflow_event") and not msg.get("status_report_placeholder")
                ]
                
                if display_messages:
                    lines.append(f"\n**Assistant:**")
                    for msg in display_messages:
                        if "content" in msg and msg["content"]:
                            lines.append(msg["content"])
                        
                        if "tool_calls" in msg and msg["tool_calls"]:
                            tool_names = [tc.get('function', {}).get('name', 'unknown') 
                                        for tc in msg.get("tool_calls", [])]
                            lines.append(f"⚙️ Calling tool(s): `{', '.join(tool_names)}`...")
                    
                    # Add summary if available
                    if summary:
                        summary_parts = []
                        if summary.get("model"):
                            summary_parts.append(f"Model: {summary['provider']}/{summary['model']}")
                        if summary.get("total_tokens", 0) > 0:
                            summary_parts.append(f"Tokens: {summary['total_tokens']:,}")
                        if summary.get("tools_executed", 0) > 0:
                            summary_parts.append(f"Tools: {summary['tools_executed']}")
                        if summary.get("duration", 0) > 0:
                            summary_parts.append(f"Duration: {summary['duration']:.1f}s")
                        
                        if summary_parts:
                            lines.append(f"\n*Summary: {' | '.join(summary_parts)}*")
        
        return "\n".join(lines)
