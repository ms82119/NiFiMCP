"""
Configuration Updates for Documentation Workflow

These updates add documentation workflow configuration to the settings system.

Apply these changes to:
- config/settings.py (accessor functions)
- config.yaml (configuration values)
"""

from typing import Dict, Any


# ============================================================================
# Default Configuration (add to settings.py DEFAULT_APP_CONFIG)
# ============================================================================

DOCUMENTATION_WORKFLOW_DEFAULTS = {
    'documentation_workflow': {
        'discovery': {
            'timeout_seconds': 120,     # Max time per discovery batch
            'max_depth': 10,            # Max PG nesting depth to traverse
            'batch_size': 50            # Processors per batch
        },
        'analysis': {
            'large_pg_threshold': 25,   # Processors before virtual grouping
            'max_tokens_per_analysis': 8000,  # Token limit per LLM call
            'min_virtual_groups': 3,    # Min groups for large PGs
            'max_virtual_groups': 7     # Max groups for large PGs
        },
        'generation': {
            'max_mermaid_nodes': 50,    # Max nodes in Mermaid diagram
            'summary_max_words': 500,   # Max words in executive summary
            'include_all_io': True      # Include all IO endpoints in table
        }
    }
}


# ============================================================================
# Updated DEFAULT_APP_CONFIG (replace in settings.py)
# ============================================================================

"""
DEFAULT_APP_CONFIG = {
    'nifi': {
        'servers': []
    },
    'llm': {
        'google': {'api_key': None, 'models': ['gemini-1.5-pro-latest']},
        'openai': {'api_key': None, 'models': ['gpt-4-turbo-preview']},
        'perplexity': {'api_key': None, 'models': ['sonar-pro']},
        'anthropic': {'api_key': None, 'models': ['claude-sonnet-4-20250109']},
        'expert_help_model': {'provider': None, 'model': None}
    },
    'mcp_features': {
        'auto_stop_enabled': True,
        'auto_delete_enabled': True,
        'auto_purge_enabled': True
    },
    'logging': {
        'llm_enqueue_enabled': True
    },
    'workflows': {
        'execution_mode': 'unguided',
        'default_action_limit': 10,
        'retry_attempts': 3,
        'enabled_workflows': [
            'unguided',
            'flow_documentation'  # ADD THIS
        ],
        # ADD THIS SECTION
        'documentation_workflow': {
            'discovery': {
                'timeout_seconds': 120,
                'max_depth': 10,
                'batch_size': 50
            },
            'analysis': {
                'large_pg_threshold': 25,
                'max_tokens_per_analysis': 8000,
                'min_virtual_groups': 3,
                'max_virtual_groups': 7
            },
            'generation': {
                'max_mermaid_nodes': 50,
                'summary_max_words': 500,
                'include_all_io': True
            }
        }
    }
}
"""


# ============================================================================
# Accessor Functions (add to settings.py)
# ============================================================================

def get_documentation_workflow_config() -> Dict[str, Any]:
    """
    Get documentation workflow configuration.
    
    Returns:
        Dict with discovery, analysis, and generation settings
    
    Example:
        config = get_documentation_workflow_config()
        threshold = config.get('analysis', {}).get('large_pg_threshold', 25)
    """
    app_config = get_app_config()
    workflows_config = app_config.get('workflows', {})
    doc_config = workflows_config.get('documentation_workflow', {})
    
    # Merge with defaults to ensure all keys exist
    defaults = DOCUMENTATION_WORKFLOW_DEFAULTS['documentation_workflow']
    
    merged = {}
    for section in ['discovery', 'analysis', 'generation']:
        merged[section] = {
            **defaults.get(section, {}),
            **doc_config.get(section, {})
        }
    
    return merged


def get_discovery_config() -> Dict[str, Any]:
    """Get discovery phase configuration."""
    return get_documentation_workflow_config().get('discovery', {})


def get_analysis_config() -> Dict[str, Any]:
    """Get analysis phase configuration."""
    return get_documentation_workflow_config().get('analysis', {})


def get_generation_config() -> Dict[str, Any]:
    """Get generation phase configuration."""
    return get_documentation_workflow_config().get('generation', {})


def is_documentation_workflow_enabled() -> bool:
    """Check if flow_documentation workflow is enabled."""
    app_config = get_app_config()
    enabled_workflows = app_config.get('workflows', {}).get('enabled_workflows', [])
    return 'flow_documentation' in enabled_workflows


# ============================================================================
# config.yaml Updates
# ============================================================================

"""
Add the following to your config.yaml file:

workflows:
  execution_mode: 'unguided'
  default_action_limit: 10
  retry_attempts: 3
  enabled_workflows:
    - 'unguided'
    - 'flow_documentation'
  
  # Documentation workflow configuration
  documentation_workflow:
    discovery:
      # Maximum time (seconds) for each discovery batch
      timeout_seconds: 120
      # Maximum process group nesting depth to traverse
      max_depth: 10
      # Number of processors to fetch per batch
      batch_size: 50
    
    analysis:
      # Processor count threshold for virtual grouping
      # PGs with more processors than this will be split into virtual groups
      large_pg_threshold: 25
      # Maximum tokens per LLM analysis call
      max_tokens_per_analysis: 8000
      # Minimum number of virtual groups to create
      min_virtual_groups: 3
      # Maximum number of virtual groups to create
      max_virtual_groups: 7
    
    generation:
      # Maximum nodes to include in Mermaid diagram
      max_mermaid_nodes: 50
      # Maximum words for executive summary
      summary_max_words: 500
      # Whether to include all IO endpoints in documentation
      include_all_io: true
"""


# ============================================================================
# Integration Instructions
# ============================================================================
#
# 1. Add DOCUMENTATION_WORKFLOW_DEFAULTS to settings.py at the top with
#    the other default configs
#
# 2. Update DEFAULT_APP_CONFIG in settings.py:
#    - Add 'flow_documentation' to enabled_workflows list
#    - Add 'documentation_workflow' section
#
# 3. Add the accessor functions to settings.py:
#    - get_documentation_workflow_config()
#    - get_discovery_config()
#    - get_analysis_config()
#    - get_generation_config()
#    - is_documentation_workflow_enabled()
#
# 4. Update config.yaml with the documentation_workflow section
#
# 5. Nodes access config via:
#    from config.settings import get_documentation_workflow_config
#    config = get_documentation_workflow_config()
#    threshold = config['analysis']['large_pg_threshold']
#
# ============================================================================

