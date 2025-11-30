# Placeholder for configuration loading (API keys etc.)
import os
import yaml
from pathlib import Path
# Remove streamlit import
# import streamlit as st
# from dotenv import load_dotenv # Removed dotenv import

# Define project root assuming this file is in config/settings.py
PROJECT_ROOT = Path(__file__).parent.parent

# --- YAML Configuration Loading ---

DEFAULT_LOGGING_CONFIG = {
    'log_directory': 'logs',
    'interface_debug_enabled': False,
    'console': {'level': 'INFO'},
    'client_file': {'enabled': True, 'level': 'DEBUG'},
    'server_file': {'enabled': True, 'level': 'DEBUG'},
}

DEFAULT_APP_CONFIG = {
    'nifi': {
        'servers': [] # Default to empty list
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
        'execution_mode': 'unguided',  # unguided | guided
        'default_action_limit': 10,
        'retry_attempts': 3,
        'enabled_workflows': [
            'unguided',
            'flow_documentation'
        ],
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
                'include_unclassified': True,  # Report unknown types
                'property_extraction': {
                    'mode': 'balanced',  # 'minimal', 'balanced', 'comprehensive'
                    'max_properties_per_processor': 10,  # Limit to prevent token bloat
                    'truncate_large_values': True,
                    'max_value_length': 500,
                    'include_defaults': False  # Skip properties with default values
                }
            },
            'generation': {
                'max_mermaid_nodes': 50,      # Simplify large diagrams
                'summary_max_words': 500,     # Executive summary limit
                'include_all_io': True        # Always list IO processors
            },
            'output': {
                'format': 'markdown',         # Output format
                'include_raw_data': False,    # Include JSON appendix
                'validate_mermaid': True,     # Validate diagram syntax
                'save_shared_state': True     # Save generation shared state snapshot
            }
        }
    }
}

def _load_yaml_config(config_path: Path, default_config: dict) -> dict:
    """Loads configuration from a YAML file, falling back to defaults."""
    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                config_data = yaml.safe_load(f)
                print(f"Successfully loaded configuration from {config_path}")
                # Simple merge strategy: Update default with loaded data (won't handle deep merges)
                merged_config = default_config.copy()
                if config_data:
                     # Basic recursive update for nested dicts
                    def update_dict(d, u):
                        for k, v in u.items():
                            if isinstance(v, dict):
                                d[k] = update_dict(d.get(k, {}), v)
                            else:
                                d[k] = v
                        return d
                    merged_config = update_dict(merged_config, config_data)
                return merged_config
        except Exception as e:
            print(f"Warning: Error loading configuration from {config_path}: {e}. Using defaults.")
            return default_config
    else:
        print(f"Warning: Configuration file not found at {config_path}. Using defaults.")
        return default_config

# Load Logging Config
LOGGING_CONFIG = _load_yaml_config(PROJECT_ROOT / "logging_config.yaml", DEFAULT_LOGGING_CONFIG)

# Load Application Config
_APP_CONFIG = _load_yaml_config(PROJECT_ROOT / "config.yaml", DEFAULT_APP_CONFIG)

# --- Configuration Accessors ---

def get_logging_config() -> dict:
    """Returns the loaded logging configuration."""
    return LOGGING_CONFIG

def get_app_config() -> dict:
    """Returns the loaded application configuration."""
    return _APP_CONFIG

def get_nifi_servers() -> list[dict]:
    """Returns the list of configured NiFi servers."""
    return _APP_CONFIG.get('nifi', {}).get('servers', [])

def get_nifi_server_config(server_id: str) -> dict | None:
    """Finds and returns the configuration for a specific NiFi server by its ID."""
    for server in get_nifi_servers():
        if server.get('id') == server_id:
            return server
    print(f"Warning: NiFi server configuration not found for ID: {server_id}")
    return None

# --- MCP Feature Flags --- Accessors ---
def get_feature_auto_stop_enabled(headers: dict | None = None) -> bool:
    """Returns whether the Auto-Stop feature is enabled, checking header override first."""
    if headers:
        # Convert header keys to lowercase for case-insensitive comparison
        headers = {k.lower(): v for k, v in headers.items()}
        header_value = headers.get("x-mcp-auto-stop-enabled") # Headers are case-insensitive
        if header_value is not None:
            return str(header_value).lower() == "true"
    return _APP_CONFIG.get('mcp_features', {}).get('auto_stop_enabled', DEFAULT_APP_CONFIG['mcp_features']['auto_stop_enabled'])

def get_feature_auto_delete_enabled(headers: dict | None = None) -> bool:
    """Returns whether the Auto-Delete feature is enabled, checking header override first."""
    if headers:
        # Convert header keys to lowercase for case-insensitive comparison
        headers = {k.lower(): v for k, v in headers.items()}
        header_value = headers.get("x-mcp-auto-delete-enabled")
        if header_value is not None:
            return str(header_value).lower() == "true"
    return _APP_CONFIG.get('mcp_features', {}).get('auto_delete_enabled', DEFAULT_APP_CONFIG['mcp_features']['auto_delete_enabled'])

def get_feature_auto_purge_enabled(headers: dict | None = None) -> bool:
    """Returns whether the Auto-Purge feature is enabled, checking header override first."""
    if headers:
        # Convert header keys to lowercase for case-insensitive comparison
        headers = {k.lower(): v for k, v in headers.items()}
        header_value = headers.get("x-mcp-auto-purge-enabled")
        if header_value is not None:
            return str(header_value).lower() == "true"
    return _APP_CONFIG.get('mcp_features', {}).get('auto_purge_enabled', DEFAULT_APP_CONFIG['mcp_features']['auto_purge_enabled'])

# --- Logging Configuration Accessors ---

def get_llm_enqueue_enabled() -> bool:
    """Returns whether LLM logging should use enqueue for thread safety."""
    return _APP_CONFIG.get('logging', {}).get('llm_enqueue_enabled', DEFAULT_APP_CONFIG['logging']['llm_enqueue_enabled'])

def get_interface_debug_enabled() -> bool:
    """Returns whether detailed interface debug logging is enabled."""
    # Check the logging config (logging_config.yaml) where this setting belongs
    return LOGGING_CONFIG.get('interface_debug_enabled', False)

# --- Specific Config Values ---

# Load API keys using nested gets for safety
GOOGLE_API_KEY = _APP_CONFIG.get('llm', {}).get('google', {}).get('api_key')
OPENAI_API_KEY = _APP_CONFIG.get('llm', {}).get('openai', {}).get('api_key')
PERPLEXITY_API_KEY = _APP_CONFIG.get('llm', {}).get('perplexity', {}).get('api_key')
ANTHROPIC_API_KEY = _APP_CONFIG.get('llm', {}).get('anthropic', {}).get('api_key')

# Load model configurations with defaults from DEFAULT_APP_CONFIG if necessary
OPENAI_MODELS = _APP_CONFIG.get('llm', {}).get('openai', {}).get('models', DEFAULT_APP_CONFIG['llm']['openai']['models'])
GEMINI_MODELS = _APP_CONFIG.get('llm', {}).get('google', {}).get('models', DEFAULT_APP_CONFIG['llm']['google']['models'])
PERPLEXITY_MODELS = _APP_CONFIG.get('llm', {}).get('perplexity', {}).get('models', DEFAULT_APP_CONFIG['llm']['perplexity']['models'])
ANTHROPIC_MODELS = _APP_CONFIG.get('llm', {}).get('anthropic', {}).get('models', DEFAULT_APP_CONFIG['llm']['anthropic']['models'])

# Expert Help Model Configuration
EXPERT_HELP_PROVIDER = _APP_CONFIG.get('llm', {}).get('expert_help_model', {}).get('provider')
EXPERT_HELP_MODEL = _APP_CONFIG.get('llm', {}).get('expert_help_model', {}).get('model')

def get_expert_help_config() -> tuple[str | None, str | None]:
    """Returns the expert help model configuration as (provider, model)."""
    return EXPERT_HELP_PROVIDER, EXPERT_HELP_MODEL

def is_expert_help_available() -> bool:
    """Returns True if expert help is properly configured and the API key is available."""
    provider, model = get_expert_help_config()
    if not provider or not model:
        return False
    
    # Check if API key is available for the specified provider
    if provider == 'openai':
        return OPENAI_API_KEY is not None
    elif provider == 'google':
        return GOOGLE_API_KEY is not None
    elif provider == 'perplexity':
        return PERPLEXITY_API_KEY is not None
    elif provider == 'anthropic':
        return ANTHROPIC_API_KEY is not None
    else:
        return False

# Print loaded configuration (excluding sensitive values like full NiFi server details)
print("\nLoaded application configuration:")
print(f"OPENAI_MODELS: {OPENAI_MODELS}")
print(f"GEMINI_MODELS: {GEMINI_MODELS}")
print(f"PERPLEXITY_MODELS: {PERPLEXITY_MODELS}")
print(f"ANTHROPIC_MODELS: {ANTHROPIC_MODELS}")
print(f"GOOGLE_API_KEY configured: {'Yes' if GOOGLE_API_KEY else 'No'}")
print(f"OPENAI_API_KEY configured: {'Yes' if OPENAI_API_KEY else 'No'}")
print(f"PERPLEXITY_API_KEY configured: {'Yes' if PERPLEXITY_API_KEY else 'No'}")
print(f"ANTHROPIC_API_KEY configured: {'Yes' if ANTHROPIC_API_KEY else 'No'}")
expert_provider, expert_model = get_expert_help_config()
print(f"Expert Help Model: {expert_provider}:{expert_model if expert_provider and expert_model else 'Not configured'}")
print(f"Expert Help Available: {'Yes' if is_expert_help_available() else 'No'}")
nifi_server_summary = [(s.get('id', 'N/A'), s.get('name', 'N/A')) for s in get_nifi_servers()]
print(f"NiFi Servers configured: {len(nifi_server_summary)} {nifi_server_summary if nifi_server_summary else '(None)'}")
print(f"Logging config loaded: {'Yes' if LOGGING_CONFIG != DEFAULT_LOGGING_CONFIG else 'No (Using Defaults)'}")

# Print MCP Feature Flags status
print("\nMCP Feature Flags:")
print(f"  Auto-Stop Enabled: {get_feature_auto_stop_enabled()}")
print(f"  Auto-Delete Enabled: {get_feature_auto_delete_enabled()}")
print(f"  Auto-Purge Enabled: {get_feature_auto_purge_enabled()}")

# Print Logging Configuration status
print("\nLogging Configuration:")
print(f"  LLM Enqueue Enabled: {get_llm_enqueue_enabled()}")
print(f"  Interface Debug Enabled: {get_interface_debug_enabled()}")

# --- Workflow Configuration Accessors ---

def get_workflow_config() -> dict:
    """Returns the loaded workflow configuration."""
    return _APP_CONFIG.get('workflows', {})

def get_workflow_execution_mode() -> str:
    """Returns the current workflow execution mode (unguided | guided)."""
    return _APP_CONFIG.get('workflows', {}).get('execution_mode', DEFAULT_APP_CONFIG['workflows']['execution_mode'])

def get_workflow_action_limit() -> int:
    """Returns the default action limit for workflow steps."""
    return _APP_CONFIG.get('workflows', {}).get('default_action_limit', DEFAULT_APP_CONFIG['workflows']['default_action_limit'])

def get_workflow_retry_attempts() -> int:
    """Returns the number of retry attempts for workflow steps."""
    return _APP_CONFIG.get('workflows', {}).get('retry_attempts', DEFAULT_APP_CONFIG['workflows']['retry_attempts'])

def get_enabled_workflows() -> list[str]:
    """Returns the list of enabled workflows."""
    return _APP_CONFIG.get('workflows', {}).get('enabled_workflows', DEFAULT_APP_CONFIG['workflows']['enabled_workflows'])

def is_workflow_enabled(workflow_name: str) -> bool:
    """Returns True if the specified workflow is enabled."""
    return workflow_name in get_enabled_workflows()

# Print Workflow Configuration status
print("\nWorkflow Configuration:")
print(f"  Execution Mode: {get_workflow_execution_mode()}")
print(f"  Default Action Limit: {get_workflow_action_limit()}")
print(f"  Retry Attempts: {get_workflow_retry_attempts()}")
print(f"  Enabled Workflows: {get_enabled_workflows()}")

# --- Documentation Workflow Configuration Accessors ---

def get_documentation_workflow_config() -> dict:
    """Returns the documentation workflow configuration.
    
    Looks in workflows.documentation_workflow first (correct location),
    then falls back to root-level documentation_workflow for backward compatibility.
    """
    workflows_config = _APP_CONFIG.get('workflows', {})
    if 'documentation_workflow' in workflows_config:
        return workflows_config['documentation_workflow']
    
    # Fallback to root level for backward compatibility
    return _APP_CONFIG.get('documentation_workflow', 
                           DEFAULT_APP_CONFIG.get('workflows', {}).get('documentation_workflow', {}))

def get_doc_discovery_config() -> dict:
    """Returns discovery phase configuration."""
    return get_documentation_workflow_config().get('discovery', {})

def get_doc_analysis_config() -> dict:
    """Returns analysis phase configuration."""
    return get_documentation_workflow_config().get('analysis', {})

def get_doc_generation_config() -> dict:
    """Returns generation phase configuration."""
    return get_documentation_workflow_config().get('generation', {})

def get_doc_output_config() -> dict:
    """Returns output configuration."""
    return get_documentation_workflow_config().get('output', {})

def get_doc_property_extraction_config() -> dict:
    """Returns property extraction configuration."""
    return get_doc_analysis_config().get('property_extraction', {
        'mode': 'balanced',
        'max_properties_per_processor': 10,
        'truncate_large_values': True,
        'max_value_length': 500,
        'include_defaults': False
    })

def get_doc_property_extraction_mode() -> str:
    """Returns property extraction mode: 'minimal', 'balanced', or 'comprehensive'."""
    return get_doc_property_extraction_config().get('mode', 'balanced')

def should_save_generation_shared_state() -> bool:
    """Returns whether to save generation shared state snapshot."""
    return get_doc_output_config().get('save_shared_state', True)

# Convenience accessors for common settings
def get_doc_discovery_timeout() -> int:
    """Returns discovery timeout in seconds."""
    return get_doc_discovery_config().get('timeout_seconds', 120)

def get_doc_max_processors_per_llm() -> int:
    """Returns max processors per LLM analysis call."""
    return get_doc_analysis_config().get('max_processors_per_llm_call', 30)

def get_doc_max_mermaid_nodes() -> int:
    """Returns max nodes in Mermaid diagram."""
    return get_doc_generation_config().get('max_mermaid_nodes', 50)

def get_doc_large_pg_threshold() -> int:
    """Returns processor count threshold for virtual grouping."""
    return get_doc_analysis_config().get('large_pg_threshold', 25)

def validate_documentation_workflow_config() -> list[str]:
    """Validate documentation workflow configuration, return list of warnings."""
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

# --- Deprecated Functions (Keep temporarily for reference/smooth transition if needed, but remove eventually) ---

# def load_api_key(key_name: str) -> str | None:
#     """DEPRECATED: Loads an API key from environment variables."""
#     print(f"Warning: Deprecated function load_api_key called for {key_name}")
#     return os.getenv(key_name)

# def load_config_value(key_name: str, default_value: str) -> str:
#     """DEPRECATED: Loads a configuration value from environment variables with a default fallback."""
#     print(f"Warning: Deprecated function load_config_value called for {key_name}")
#     return os.getenv(key_name, default_value)

# --- End of File ---
