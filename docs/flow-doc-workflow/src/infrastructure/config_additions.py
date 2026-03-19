"""
Configuration additions for documentation workflow.

Add to: config/settings.py

1. Add documentation_workflow section to DEFAULT_APP_CONFIG
2. Add accessor functions
3. Add validation function
"""

# =============================================================================
# 1. CONFIG SECTION (add to DEFAULT_APP_CONFIG)
# =============================================================================

DOC_WORKFLOW_CONFIG = """
# Add to DEFAULT_APP_CONFIG dict:

'documentation_workflow': {
    'discovery': {
        'timeout_seconds': 120,       # Max time for discovery phase
        'max_depth': 10,              # Max PG nesting depth
        'batch_size': 50,             # Components per API call
        'max_retries': 3              # Retries on API failure
    },
    'analysis': {
        'large_pg_threshold': 25,     # Trigger virtual grouping above this
        'max_processors_per_llm_call': 30,  # Batch size for LLM
        'max_tokens_per_analysis': 8000,     # Token budget per call
        'min_virtual_groups': 3,      # Minimum virtual groups
        'max_virtual_groups': 7,      # Maximum virtual groups
        'include_unclassified': True  # Report unknown types
    },
    'generation': {
        'max_mermaid_nodes': 50,      # Simplify large diagrams
        'summary_max_words': 500,     # Executive summary limit
        'include_all_io': True        # Always list IO processors
    },
    'output': {
        'format': 'markdown',         # Output format
        'include_raw_data': False,    # Include JSON appendix
        'validate_mermaid': True      # Validate diagram syntax
    }
}
"""


# =============================================================================
# 2. ACCESSOR FUNCTIONS (add to settings.py)
# =============================================================================

CONFIG_ACCESSORS = """
# Add these functions to settings.py:

def get_documentation_workflow_config() -> dict:
    \"\"\"Returns the documentation workflow configuration.\"\"\"
    return _APP_CONFIG.get('documentation_workflow', 
                           DEFAULT_APP_CONFIG.get('documentation_workflow', {}))

def get_doc_discovery_config() -> dict:
    \"\"\"Returns discovery phase configuration.\"\"\"
    return get_documentation_workflow_config().get('discovery', {})

def get_doc_analysis_config() -> dict:
    \"\"\"Returns analysis phase configuration.\"\"\"
    return get_documentation_workflow_config().get('analysis', {})

def get_doc_generation_config() -> dict:
    \"\"\"Returns generation phase configuration.\"\"\"
    return get_documentation_workflow_config().get('generation', {})

def get_doc_output_config() -> dict:
    \"\"\"Returns output configuration.\"\"\"
    return get_documentation_workflow_config().get('output', {})

# Convenience accessors for common settings
def get_doc_discovery_timeout() -> int:
    \"\"\"Returns discovery timeout in seconds.\"\"\"
    return get_doc_discovery_config().get('timeout_seconds', 120)

def get_doc_max_processors_per_llm() -> int:
    \"\"\"Returns max processors per LLM analysis call.\"\"\"
    return get_doc_analysis_config().get('max_processors_per_llm_call', 30)

def get_doc_max_mermaid_nodes() -> int:
    \"\"\"Returns max nodes in Mermaid diagram.\"\"\"
    return get_doc_generation_config().get('max_mermaid_nodes', 50)

def get_doc_large_pg_threshold() -> int:
    \"\"\"Returns processor count threshold for virtual grouping.\"\"\"
    return get_doc_analysis_config().get('large_pg_threshold', 25)
"""


# =============================================================================
# 3. VALIDATION FUNCTION (add to settings.py)
# =============================================================================

CONFIG_VALIDATION = """
# Add this function to settings.py:

def validate_documentation_workflow_config() -> List[str]:
    \"\"\"Validate documentation workflow configuration, return list of warnings.\"\"\"
    warnings = []
    config = get_documentation_workflow_config()
    
    # Check discovery settings
    discovery = config.get('discovery', {})
    if discovery.get('timeout_seconds', 120) < 30:
        warnings.append("Discovery timeout < 30s may cause incomplete results")
    if discovery.get('max_depth', 10) > 20:
        warnings.append("Max depth > 20 may cause performance issues")
    
    # Check analysis settings
    analysis = config.get('analysis', {})
    if analysis.get('max_processors_per_llm_call', 30) > 50:
        warnings.append("More than 50 processors per LLM call may exceed token limits")
    if analysis.get('large_pg_threshold', 25) < 10:
        warnings.append("Large PG threshold < 10 may create too many virtual groups")
    
    # Check generation settings
    generation = config.get('generation', {})
    if generation.get('max_mermaid_nodes', 50) > 100:
        warnings.append("Mermaid diagrams with >100 nodes may not render well")
    
    return warnings
"""

