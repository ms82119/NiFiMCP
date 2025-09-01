"""
Message utility functions for NiFi Chat UI.

This module contains functions for managing message history, including
smart pruning and message structure validation.
"""

from typing import List, Dict
from loguru import logger


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


def _truncate_message_content(message: Dict, max_content_length: int = 200, max_tool_id_length: int = 50) -> Dict:
    """
    Intelligently truncate message content while preserving structure and important information.
    
    Args:
        message: Message dictionary to truncate
        max_content_length: Maximum length for content field
        max_tool_id_length: Maximum length for tool_call_id field
        
    Returns:
        Copy of message with truncated content
    """
    truncated_message = message.copy()
    
    # Truncate content if it exists and is too long
    if "content" in truncated_message and truncated_message["content"]:
        original_content = truncated_message["content"]
        if len(original_content) > max_content_length:
            # Try to truncate at a word boundary
            truncated_content = original_content[:max_content_length]
            last_space = truncated_content.rfind(' ')
            if last_space > max_content_length * 0.8:  # Only break at word if we're not losing too much
                truncated_content = truncated_content[:last_space]
            truncated_content += "... [truncated]"
            truncated_message["content"] = truncated_content
    
    # Truncate tool_call_id if it's very long
    if truncated_message.get("role") == "tool" and "tool_call_id" in truncated_message:
        tool_call_id = truncated_message["tool_call_id"]
        if len(tool_call_id) > max_tool_id_length:
            truncated_message["tool_call_id"] = tool_call_id[:max_tool_id_length] + "..."
    
    # For assistant messages with tool calls, ensure tool_calls structure is preserved
    if truncated_message.get("role") == "assistant" and "tool_calls" in truncated_message:
        # Don't truncate tool_calls - they're essential for the LLM to understand what tools were called
        pass
    
    return truncated_message


def _log_message_structure(messages: List[Dict], logger, prefix: str = ""):
    """Helper function to log message structure details for debugging."""
    logger.info(f"{prefix}Message structure ({len(messages)} messages):")
    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content_length = len(msg.get("content", "")) if msg.get("content") else 0
        has_tool_calls = "tool_calls" in msg and len(msg.get("tool_calls", [])) > 0
        tool_call_id = msg.get("tool_call_id", "")
        logger.info(f"  [{i}] {role} (content: {content_length} chars){' (has_tool_calls)' if has_tool_calls else ''}{' (tool_call_id: ' + tool_call_id + ')' if tool_call_id else ''}")


def smart_prune_messages(messages: List[Dict], max_tokens: int, provider: str, model_name: str, tools, logger) -> List[Dict]:
    """
    Intelligently prune messages to fit within token limits while maintaining conversation structure.
    
    This function uses a hybrid approach:
    1. Identifies conversation turns (user→assistant→tool or assistant→tool chains)
    2. For messages marked for removal, truncates content instead of deleting entirely
    3. Preserves conversation structure and tool call relationships
    
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
        # Import token counter here to avoid circular imports
        import sys
        import os
        current_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(os.path.dirname(current_dir))
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        
        from nifi_chat_ui.llm.utils.token_counter import TokenCounter
        token_counter = TokenCounter()
        
        current_tokens = token_counter.calculate_input_tokens(messages, provider, model_name, tools)
        logger.info(f"SMART_PRUNE_DEBUG: Initial tokens: {current_tokens}, Limit: {max_tokens}, Messages: {len(messages)}")
        if current_tokens <= max_tokens:
            logger.info("SMART_PRUNE_DEBUG: Already under limit, no pruning needed")
            return messages  # No pruning needed
    except Exception as e:
        logger.error(f"Error calculating tokens during smart pruning: {e}")
        return messages  # Return original on error
    
    # Keep system message (index 0) and work with the rest
    system_message = messages[0]
    conversation_messages = messages[1:]
    
    # DEBUG: Log message structure
    _log_message_structure(messages, logger, "SMART_PRUNE_DEBUG: Initial ")
    
    # NEW: Identify conversation turns more intelligently
    # A turn can be:
    # 1. user → assistant (with optional tool calls and responses)
    # 2. assistant → tool responses (when no user message precedes)
    # 3. standalone assistant messages
    turns = []
    i = 0
    
    while i < len(conversation_messages):
        message = conversation_messages[i]
        role = message.get("role")
        
        if role == "user":
            # User-initiated turn
            turn_start = i
            turn_end = i
            
            # Look ahead for complete turn (assistant + tool responses)
            j = i + 1
            while j < len(conversation_messages):
                next_message = conversation_messages[j]
                next_role = next_message.get("role")
                
                if next_role == "assistant":
                    turn_end = j
                    # Include tool responses if this assistant has tool calls
                    if "tool_calls" in next_message:
                        tool_call_ids = {tc.get("id") for tc in next_message.get("tool_calls", [])}
                        k = j + 1
                        while k < len(conversation_messages) and tool_call_ids:
                            tool_message = conversation_messages[k]
                            if (tool_message.get("role") == "tool" and 
                                tool_message.get("tool_call_id") in tool_call_ids):
                                tool_call_ids.remove(tool_message.get("tool_call_id"))
                                turn_end = k
                            elif tool_message.get("role") != "tool":
                                break
                            k += 1
                elif next_role == "tool":
                    # Orphaned tool message (shouldn't happen, but handle gracefully)
                    turn_end = j
                elif next_role == "user":
                    # Found next user message - end of current turn
                    break
                else:
                    break
                j += 1
            
            turns.append((turn_start, turn_end, "user_initiated"))
            i = turn_end + 1
            
        elif role == "assistant":
            # Assistant-initiated turn (no preceding user message)
            turn_start = i
            turn_end = i
            
            # Include tool responses if this assistant has tool calls
            if "tool_calls" in message:
                tool_call_ids = {tc.get("id") for tc in message.get("tool_calls", [])}
                j = i + 1
                while j < len(conversation_messages) and tool_call_ids:
                    tool_message = conversation_messages[j]
                    if (tool_message.get("role") == "tool" and 
                        tool_message.get("tool_call_id") in tool_call_ids):
                        tool_call_ids.remove(tool_message.get("tool_call_id"))
                        turn_end = j
                    elif tool_message.get("role") != "tool":
                        break
                    j += 1
            
            turns.append((turn_start, turn_end, "assistant_initiated"))
            i = turn_end + 1
            
        elif role == "tool":
            # Orphaned tool message - group with previous turn if possible
            if turns:
                # Extend the last turn to include this tool message
                last_turn_start, last_turn_end, last_turn_type = turns[-1]
                turns[-1] = (last_turn_start, i, last_turn_type)
            else:
                # Create a standalone turn for this tool message
                turns.append((i, i, "tool_only"))
            i += 1
            
        else:
            # Unknown role - skip
            i += 1
    
    logger.info(f"SMART_PRUNE_DEBUG: Identified {len(turns)} conversation turns")
    for idx, (start, end, turn_type) in enumerate(turns):
        logger.debug(f"  Turn {idx}: {turn_type} at indices {start}-{end}")
    
    # Determine how many turns to preserve
    turns_to_preserve = 2  # Default: preserve last 2 turns
    if current_tokens > max_tokens * 2:  # If more than 2x the limit
        turns_to_preserve = 1  # Only preserve the last 1 turn
        logger.debug(f"SMART_PRUNE_DEBUG: Severe token limit exceeded ({current_tokens} > {max_tokens * 2}), preserving only last {turns_to_preserve} turn(s)")
    
    # Mark turns for removal (keep the most recent ones)
    turns_to_remove = turns[:-turns_to_preserve] if len(turns) > turns_to_preserve else []
    turns_to_keep = turns[-turns_to_preserve:] if len(turns) > turns_to_preserve else turns
    
    logger.info(f"SMART_PRUNE_DEBUG: Will remove {len(turns_to_remove)} turns, preserve {len(turns_to_keep)} turns")
    
    # NEW: Instead of removing messages, truncate their content
    pruned_messages = [system_message] + conversation_messages.copy()
    
    # Apply content truncation to messages in turns marked for removal
    for turn_start, turn_end, turn_type in turns_to_remove:
        logger.debug(f"SMART_PRUNE_DEBUG: Truncating content for turn {turn_start}-{turn_end} ({turn_type})")
        
        # Adjust indices for system message offset
        adjusted_start = turn_start + 1
        adjusted_end = turn_end + 1
        
        # Truncate content for each message in this turn
        for idx in range(adjusted_start, adjusted_end + 1):
            if idx < len(pruned_messages):
                message = pruned_messages[idx]
                # Use the helper function for intelligent truncation
                pruned_messages[idx] = _truncate_message_content(message)
                logger.debug(f"SMART_PRUNE_DEBUG: Applied content truncation to message {idx}")
    
    # Log the result to verify no messages were removed
    logger.info(f"SMART_PRUNE_DEBUG: After truncation: {len(pruned_messages)} messages (should equal {len(messages)})")
    if len(pruned_messages) != len(messages):
        logger.error(f"SMART_PRUNE_DEBUG: CRITICAL ERROR - Message count changed from {len(messages)} to {len(pruned_messages)}!")
        logger.error("SMART_PRUNE_DEBUG: This indicates messages were removed instead of truncated - returning original messages")
        return messages
    
    # Log the final message structure for debugging
    _log_message_structure(pruned_messages, logger, "SMART_PRUNE_DEBUG: Final ")
    
    # Validate the final structure
    if not validate_message_structure(pruned_messages, logger):
        logger.error("Message structure validation failed after truncation, returning original messages")
        return messages
    
    # Check final token count
    try:
        final_tokens = token_counter.calculate_input_tokens(pruned_messages, provider, model_name, tools)
        logger.info(f"Smart pruning complete: {len(messages)} → {len(pruned_messages)} messages, {current_tokens} → {final_tokens} tokens")
        
        # If we're still over the limit after truncation, fall back to aggressive pruning
        if final_tokens > max_tokens:
            logger.warning(f"SMART_PRUNE_DEBUG: Still over limit after truncation ({final_tokens} > {max_tokens}), applying aggressive pruning")
            return _apply_aggressive_pruning(messages, max_tokens, provider, model_name, tools, logger)
            
    except Exception as e:
        logger.error(f"Error calculating final tokens: {e}")
    
    return pruned_messages


def _apply_aggressive_pruning(messages: List[Dict], max_tokens: int, provider: str, model_name: str, tools, logger) -> List[Dict]:
    """
    Fallback aggressive pruning when content truncation isn't sufficient.
    This preserves only the most recent complete conversation turn.
    """
    logger.info("SMART_PRUNE_DEBUG: Applying aggressive pruning fallback")
    
    if len(messages) <= 2:
        return messages
    
    # Keep system message
    system_message = messages[0]
    conversation_messages = messages[1:]
    
    # Find the last complete turn
    last_turn_start = len(conversation_messages) - 1
    
    # Look backwards for the start of the last turn
    i = len(conversation_messages) - 1
    while i >= 0:
        message = conversation_messages[i]
        role = message.get("role")
        
        if role == "user":
            # Found start of last user-initiated turn
            last_turn_start = i
            break
        elif role == "assistant" and i > 0:
            # Check if this is part of a tool call sequence
            prev_message = conversation_messages[i-1]
            if prev_message.get("role") == "user":
                # This assistant is part of a user-initiated turn
                last_turn_start = i - 1
                break
            else:
                # This assistant starts a new turn
                last_turn_start = i
                break
        i -= 1
    
    # Instead of removing messages, apply aggressive truncation to older messages
    # but preserve the full chain of events
    pruned_messages = [system_message] + conversation_messages.copy()
    
    # Apply very aggressive truncation to older messages
    for i in range(1, last_turn_start + 1):  # +1 because we have system message at index 0
        if i < len(pruned_messages):
            message = pruned_messages[i]
            # Apply very aggressive truncation to older messages
            pruned_messages[i] = _truncate_message_content(message, max_content_length=100, max_tool_id_length=30)
            logger.debug(f"SMART_PRUNE_DEBUG: Applied aggressive truncation to message {i}")
    
    # Apply moderate truncation to preserved messages (except the very last one)
    for i in range(last_turn_start + 1, len(pruned_messages) - 1):
        if i < len(pruned_messages):
            message = pruned_messages[i]
            # Apply moderate truncation to preserved messages
            pruned_messages[i] = _truncate_message_content(message, max_content_length=300, max_tool_id_length=50)
            logger.debug(f"SMART_PRUNE_DEBUG: Applied moderate truncation to preserved message {i}")
    
    logger.info(f"SMART_PRUNE_DEBUG: Aggressive pruning preserved full chain with aggressive truncation of older messages")
    
    # Validate structure
    if not validate_message_structure(pruned_messages, logger):
        logger.error("Message structure validation failed after aggressive pruning, returning original messages")
        return messages
    
    return pruned_messages
