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


def smart_prune_messages(messages: List[Dict], max_tokens: int, provider: str, model_name: str, tools, logger) -> List[Dict]:
    """
    Intelligently prune messages to fit within token limits while maintaining conversation structure.
    
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
    logger.info(f"SMART_PRUNE_DEBUG: Message structure analysis:")
    for i, msg in enumerate(conversation_messages):
        role = msg.get("role", "unknown")
        has_tool_calls = "tool_calls" in msg and len(msg.get("tool_calls", [])) > 0
        tool_call_id = msg.get("tool_call_id", "")
        logger.info(f"  [{i}] {role}{' (has_tool_calls)' if has_tool_calls else ''}{' (tool_call_id: ' + tool_call_id + ')' if tool_call_id else ''}")
    
    # Find safe removal points (complete conversation turns)
    # We'll build a list of "removal groups" - sets of message indices that can be removed together
    removal_groups = []
    i = 0
    
    while i < len(conversation_messages):
        message = conversation_messages[i]
        role = message.get("role")
        
        if role == "user":
            logger.debug(f"SMART_PRUNE_DEBUG: Processing user message at index {i}")
            # Start of a potential removal group
            group_start = i
            group_end = i  # At least include the user message
            
            # Look ahead for the complete turn
            j = i + 1
            while j < len(conversation_messages):
                next_message = conversation_messages[j]
                next_role = next_message.get("role")
                
                if next_role == "assistant":
                    group_end = j
                    # Check if this assistant message has tool calls
                    if "tool_calls" in next_message:
                        # Must include corresponding tool responses
                        tool_call_ids = {tc.get("id") for tc in next_message.get("tool_calls", [])}
                        k = j + 1
                        while k < len(conversation_messages) and tool_call_ids:
                            tool_message = conversation_messages[k]
                            if (tool_message.get("role") == "tool" and 
                                tool_message.get("tool_call_id") in tool_call_ids):
                                tool_call_ids.remove(tool_message.get("tool_call_id"))
                                group_end = k
                            elif tool_message.get("role") != "tool":
                                break  # End of tool responses
                            k += 1
                    # Don't break here - continue looking for more assistant messages in this turn
                elif next_role == "tool":
                    # Orphaned tool message (shouldn't happen, but handle gracefully)
                    group_end = j
                elif next_role == "user":
                    # Found next user message - end of current turn
                    break
                else:
                    break
                j += 1
            
            logger.debug(f"SMART_PRUNE_DEBUG: Turn found at indices {group_start}-{group_end}")
            
            # More aggressive pruning: preserve only the most recent complete conversation turn
            # Count how many complete turns we have
            total_turns = 0
            temp_i = 0
            while temp_i < len(conversation_messages):
                if conversation_messages[temp_i].get("role") == "user":
                    total_turns += 1
                    # Skip to end of this turn
                    temp_j = temp_i + 1
                    while temp_j < len(conversation_messages) and conversation_messages[temp_j].get("role") != "user":
                        temp_j += 1
                    temp_i = temp_j
                else:
                    temp_i += 1
            
            # Count which turn this is (1-based)
            current_turn = 0
            temp_i = 0
            while temp_i <= i:
                if conversation_messages[temp_i].get("role") == "user":
                    current_turn += 1
                temp_i += 1
            
            logger.debug(f"SMART_PRUNE_DEBUG: Turn {current_turn}/{total_turns}, indices {group_start}-{group_end}")
            
            # More aggressive pruning when severely over limit
            turns_to_preserve = 2  # Default: preserve last 2 turns
            if current_tokens > max_tokens * 2:  # If more than 2x the limit
                turns_to_preserve = 1  # Only preserve the last 1 turn
                logger.debug(f"SMART_PRUNE_DEBUG: Severe token limit exceeded ({current_tokens} > {max_tokens * 2}), preserving only last {turns_to_preserve} turn(s)")
            else:
                logger.debug(f"SMART_PRUNE_DEBUG: Standard pruning, preserving last {turns_to_preserve} turn(s)")
            
            # Only preserve the specified number of recent complete turns
            if current_turn <= total_turns - turns_to_preserve:
                removal_groups.append((group_start, group_end))
                logger.debug(f"SMART_PRUNE_DEBUG: Added removal group {group_start}-{group_end} (turn {current_turn}/{total_turns})")
            else:
                logger.debug(f"SMART_PRUNE_DEBUG: Preserving recent turn {current_turn}/{total_turns} at {group_start}-{group_end}")
            
            i = group_end + 1
        else:
            # Skip orphaned assistant/tool messages (shouldn't happen in well-formed conversations)
            logger.debug(f"SMART_PRUNE_DEBUG: Skipping orphaned {role} message at index {i}")
            i += 1
    
    # Remove groups from oldest to newest until we're under the token limit
    # CRITICAL FIX: Remove one group at a time and recalculate
    logger.info(f"SMART_PRUNE_DEBUG: Found {len(removal_groups)} removal groups to consider")
    pruned_messages = [system_message] + conversation_messages.copy()
    
    # Process removals one at a time, recalculating groups after each removal
    while removal_groups:
        # Take the first (oldest) group
        group_start, group_end = removal_groups[0]
        
        # Calculate current indices (accounting for system message)
        adjusted_start = group_start + 1  # +1 for system message
        adjusted_end = group_end + 1      # +1 for system message
        
        # Validate indices are still valid
        if adjusted_start >= len(pruned_messages) or adjusted_end >= len(pruned_messages):
            logger.warning(f"Invalid indices after previous removals: {adjusted_start}-{adjusted_end}, message count: {len(pruned_messages)}")
            break
        
        # Remove the group
        removed_messages = pruned_messages[adjusted_start:adjusted_end + 1]
        pruned_messages = pruned_messages[:adjusted_start] + pruned_messages[adjusted_end + 1:]
        
        # Log what was removed
        removed_roles = [msg.get("role") for msg in removed_messages]
        logger.info(f"Smart pruning removed message group (roles: {removed_roles}) - indices {adjusted_start}-{adjusted_end}")
        
        # CRITICAL FIX: Validate message structure after removal
        if not validate_message_structure(pruned_messages, logger):
            logger.warning("Message structure validation failed after removal, reverting...")
            # Revert the removal
            pruned_messages = pruned_messages[:adjusted_start] + removed_messages + pruned_messages[adjusted_start:]
            break
        
        # Check if we're now under the limit
        try:
            new_tokens = token_counter.calculate_input_tokens(pruned_messages, provider, model_name, tools)
            logger.debug(f"After removing group: {new_tokens} tokens (limit: {max_tokens})")
            if new_tokens <= max_tokens:
                break
        except Exception as e:
            logger.error(f"Error calculating tokens after removal: {e}")
            break
            
        # Remove the processed group and recalculate remaining groups
        removal_groups.pop(0)
        
        # Recalculate removal groups for the updated message list
        # This is necessary because indices have shifted after removal
        conversation_messages = pruned_messages[1:]  # Exclude system message
        removal_groups = []
        i = 0
        
        while i < len(conversation_messages):
            message = conversation_messages[i]
            role = message.get("role")
            
            if role == "user":
                # Start of a potential removal group
                group_start = i
                group_end = i  # At least include the user message
                
                # Look ahead for the complete turn
                j = i + 1
                while j < len(conversation_messages):
                    next_message = conversation_messages[j]
                    next_role = next_message.get("role")
                    
                    if next_role == "assistant":
                        group_end = j
                        # Check if this assistant message has tool calls
                        if "tool_calls" in next_message:
                            # Must include corresponding tool responses
                            tool_call_ids = {tc.get("id") for tc in next_message.get("tool_calls", [])}
                            k = j + 1
                            while k < len(conversation_messages) and tool_call_ids:
                                tool_message = conversation_messages[k]
                                if (tool_message.get("role") == "tool" and 
                                    tool_message.get("tool_call_id") in tool_call_ids):
                                    tool_call_ids.remove(tool_message.get("tool_call_id"))
                                    group_end = k
                                elif tool_message.get("role") != "tool":
                                    break  # End of tool responses
                                k += 1
                            # Don't break here - continue looking for more assistant messages in this turn
                        # Don't break here - continue looking for more assistant messages in this turn
                    elif next_role == "tool":
                        # Orphaned tool message (shouldn't happen, but handle gracefully)
                        group_end = j
                    elif next_role == "user":
                        # Found next user message - end of current turn
                        break
                    else:
                        break
                    j += 1
                
                # More aggressive pruning: preserve only the most recent complete conversation turn
                # Count how many complete turns we have
                total_turns = 0
                temp_i = 0
                while temp_i < len(conversation_messages):
                    if conversation_messages[temp_i].get("role") == "user":
                        total_turns += 1
                        # Skip to end of this turn
                        temp_j = temp_i + 1
                        while temp_j < len(conversation_messages) and conversation_messages[temp_j].get("role") != "user":
                            temp_j += 1
                        temp_i = temp_j
                    else:
                        temp_i += 1
                
                # Count which turn this is (1-based)
                current_turn = 0
                temp_i = 0
                while temp_i <= i:
                    if conversation_messages[temp_i].get("role") == "user":
                        current_turn += 1
                    temp_i += 1
                
                # More aggressive pruning when severely over limit (same logic as initial pass)
                try:
                    recalc_tokens = token_counter.calculate_input_tokens(pruned_messages, provider, model_name, tools)
                except Exception:
                    recalc_tokens = max_tokens * 2  # Assume severe if we can't calculate
                
                turns_to_preserve = 2  # Default: preserve last 2 turns
                if recalc_tokens > max_tokens * 2:  # If more than 2x the limit
                    turns_to_preserve = 1  # Only preserve the last 1 turn
                
                # Only preserve the specified number of recent complete turns
                if current_turn <= total_turns - turns_to_preserve:
                    removal_groups.append((group_start, group_end))
                
                i = group_end + 1
            else:
                # Skip orphaned assistant/tool messages (shouldn't happen in well-formed conversations)
                logger.debug(f"SMART_PRUNE_DEBUG: Skipping orphaned {role} message at index {i}")
                i += 1
    
    # Final validation and token check
    if not validate_message_structure(pruned_messages, logger):
        logger.error("Final message structure validation failed, returning original messages")
        return messages
    
    try:
        final_tokens = token_counter.calculate_input_tokens(pruned_messages, provider, model_name, tools)
        logger.info(f"Smart pruning complete: {len(messages)} → {len(pruned_messages)} messages, {current_tokens} → {final_tokens} tokens")
    except Exception:
        pass
    
    return pruned_messages
