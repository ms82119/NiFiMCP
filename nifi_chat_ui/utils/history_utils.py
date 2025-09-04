"""
History utilities for converting and validating message formats used by the
NiFi Chat UI and workflows.
"""

from typing import List, Dict, Any
from loguru import logger as default_logger


def convert_db_messages_to_llm(database_messages: List[Dict[str, Any]], logger=default_logger) -> List[Dict[str, Any]]:
    """
    Convert database-format messages (with metadata) into LLM-format messages
    expected by providers and smart-pruning utilities.

    Database format examples:
      - assistant.tool_calls stored under metadata.tool_calls
      - tool.tool_call_id stored under metadata.tool_call_id

    LLM format examples:
      - assistant messages have top-level tool_calls
      - tool messages have top-level tool_call_id
    """
    if not database_messages:
        return []

    llm_format_messages: List[Dict[str, Any]] = []

    for i, message in enumerate(database_messages):
        role = message.get("role")
        metadata = message.get("metadata") or {}

        if role == "system":
            llm_format_messages.append({
                "role": "system",
                "content": message.get("content", "")
            })
            continue

        if role == "user":
            llm_format_messages.append({
                "role": "user",
                "content": message.get("content", "")
            })
            continue

        if role == "assistant":
            llm_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": message.get("content", "")
            }
            tool_calls = metadata.get("tool_calls") if isinstance(metadata, dict) else None
            if tool_calls:
                llm_msg["tool_calls"] = tool_calls
                logger.debug(f"DB→LLM convert: assistant[{i}] tool_calls={len(tool_calls)}")
            llm_format_messages.append(llm_msg)
            continue

        if role == "tool":
            llm_msg = {
                "role": "tool",
                "content": message.get("content", "")
            }
            tool_call_id = None
            if isinstance(metadata, dict):
                tool_call_id = metadata.get("tool_call_id")
            if not tool_call_id:
                tool_call_id = message.get("tool_call_id")
            if not tool_call_id:
                logger.warning(f"DB→LLM convert: tool[{i}] missing tool_call_id; skipping")
                continue
            llm_msg["tool_call_id"] = tool_call_id
            logger.debug(f"DB→LLM convert: tool[{i}] tool_call_id={tool_call_id}")
            llm_format_messages.append(llm_msg)
            continue

        logger.warning(f"DB→LLM convert: skipping unknown role at index {i}: {role}")

    logger.info(f"DB→LLM conversion: {len(database_messages)} -> {len(llm_format_messages)} messages")
    return llm_format_messages


def summarize_and_validate(messages: List[Dict[str, Any]], logger=default_logger, tag: str = "") -> bool:
    """
    Log a concise summary of the message structure and run a non-fatal validation.

    Returns True if structure looks valid, False otherwise. Does not mutate messages.
    """
    try:
        from nifi_chat_ui.utils.message_utils import validate_message_structure
    except Exception:
        # Fallback import path
        from .message_utils import validate_message_structure  # type: ignore

    prefix = f"STRUCTURE[{tag}] " if tag else "STRUCTURE "
    logger.info(f"{prefix}messages={len(messages)}")
    for i, msg in enumerate(messages):
        role = msg.get("role")
        has_tc = bool(msg.get("tool_calls"))
        tc_id = msg.get("tool_call_id")
        content_len = len(msg.get("content", "") or "")
        logger.info(f"{prefix}[{i}] role={role}, content={content_len}, has_tool_calls={has_tc}, tool_call_id={tc_id}")

    valid = validate_message_structure(messages, logger)
    logger.info(f"{prefix}valid={valid}")
    return valid


