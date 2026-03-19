"""
Token counter extensions for documentation workflow.

Add these methods to: nifi_chat_ui/llm/utils/token_counter.py

Extends TokenCounter class with JSON data counting and batching capabilities.
"""

from typing import Any, List, Dict
import json
from loguru import logger


# =============================================================================
# NEW METHODS FOR TOKENCOUNTER CLASS
# =============================================================================

TOKEN_COUNTER_EXTENSIONS = """
# Add these methods to TokenCounter class in token_counter.py:

def count_tokens_json(
    self, 
    data: Any, 
    provider: str = "openai", 
    model_name: str = "gpt-4"
) -> int:
    \"\"\"
    Count tokens for arbitrary JSON-serializable data.
    
    Useful for processor batching in documentation workflow.
    
    Args:
        data: Dict, list, or any JSON-serializable object
        provider: LLM provider for accurate counting
        model_name: Model name for provider-specific counting
        
    Returns:
        Token count for serialized data
    \"\"\"
    try:
        json_str = json.dumps(data, default=str)
    except (TypeError, ValueError) as e:
        logger.warning(f"Failed to serialize data for token counting: {e}")
        json_str = str(data)
    
    provider_lower = provider.lower()
    if provider_lower == "openai":
        return self.count_tokens_openai(json_str, model_name)
    elif provider_lower == "perplexity":
        return self.count_tokens_perplexity(json_str, model_name)
    elif provider_lower == "anthropic":
        return self.count_tokens_anthropic(json_str)
    else:  # gemini or others
        return self.count_tokens_gemini(json_str)


def batch_by_token_limit(
    self,
    items: List[Dict],
    max_tokens: int,
    provider: str = "openai",
    model_name: str = "gpt-4",
    reserved_tokens: int = 500  # Buffer for prompt overhead
) -> List[List[Dict]]:
    \"\"\"
    Split items into batches that fit within token limits.
    
    Args:
        items: List of dictionaries to batch
        max_tokens: Maximum tokens per batch
        provider: LLM provider for accurate counting
        model_name: Model name for counting
        reserved_tokens: Buffer for prompt text around data
        
    Returns:
        List of batches, each fitting within token limit
    \"\"\"
    available_tokens = max_tokens - reserved_tokens
    batches = []
    current_batch = []
    current_tokens = 0
    
    for item in items:
        item_tokens = self.count_tokens_json(item, provider, model_name)
        
        if current_tokens + item_tokens > available_tokens and current_batch:
            # Start new batch
            batches.append(current_batch)
            current_batch = [item]
            current_tokens = item_tokens
        else:
            current_batch.append(item)
            current_tokens += item_tokens
    
    if current_batch:
        batches.append(current_batch)
    
    logger.debug(f"Batched {len(items)} items into {len(batches)} batches "
                f"(max {max_tokens} tokens, provider={provider})")
    
    return batches
"""

