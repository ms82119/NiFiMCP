"""
Persistent token storage for OIDC authentication.

This module provides file-based token storage that can be shared
across multiple processes (frontend API and MCP server).
"""

import json
import os
from pathlib import Path
from typing import Optional, Dict
from loguru import logger

# File locking for Unix systems (not available on Windows)
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False


class TokenStore:
    """File-based token storage with locking for multi-process access."""
    
    def __init__(self, store_path: str = "nifi_tokens.json"):
        """Initialize token store.
        
        Args:
            store_path: Path to the JSON file for storing tokens
        """
        self.store_path = Path(store_path)
        self.logger = logger.bind(component="TokenStore")
        self._ensure_store_file()
    
    def _ensure_store_file(self):
        """Ensure the token store file exists."""
        if not self.store_path.exists():
            self.store_path.write_text("{}")
            self.logger.debug(f"Created token store file: {self.store_path}")
    
    def _read_store(self) -> Dict[str, str]:
        """Read tokens from file with locking."""
        self._ensure_store_file()
        try:
            with open(self.store_path, 'r') as f:
                # Try to acquire shared lock (read) - Unix only
                if HAS_FCNTL:
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                    except (OSError, AttributeError):
                        pass
                try:
                    data = json.load(f)
                    return data if isinstance(data, dict) else {}
                finally:
                    if HAS_FCNTL:
                        try:
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                        except (OSError, AttributeError):
                            pass
        except json.JSONDecodeError:
            self.logger.warning(f"Token store file corrupted, resetting: {self.store_path}")
            return {}
        except Exception as e:
            self.logger.error(f"Error reading token store: {e}")
            return {}
    
    def _write_store(self, data: Dict[str, str]):
        """Write tokens to file with locking."""
        self._ensure_store_file()
        try:
            with open(self.store_path, 'w') as f:
                # Try to acquire exclusive lock (write) - Unix only
                if HAS_FCNTL:
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    except (OSError, AttributeError):
                        pass
                try:
                    json.dump(data, f, indent=2)
                finally:
                    if HAS_FCNTL:
                        try:
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                        except (OSError, AttributeError):
                            pass
        except Exception as e:
            self.logger.error(f"Error writing token store: {e}")
            raise
    
    def set_token(self, server_id: str, token: str):
        """Store a token for a server."""
        data = self._read_store()
        data[server_id] = token
        self._write_store(data)
        self.logger.debug(f"Stored token for server {server_id} (length: {len(token)})")
    
    def get_token(self, server_id: str) -> Optional[str]:
        """Retrieve a token for a server."""
        data = self._read_store()
        token = data.get(server_id)
        if token:
            self.logger.debug(f"Retrieved token for server {server_id} (length: {len(token)})")
        else:
            self.logger.debug(f"No token found for server {server_id}")
        return token
    
    def clear_token(self, server_id: str):
        """Remove a token for a server."""
        data = self._read_store()
        if server_id in data:
            del data[server_id]
            self._write_store(data)
            self.logger.debug(f"Cleared token for server {server_id}")
    
    def get_all_server_ids(self) -> list:
        """Get list of all server IDs with stored tokens."""
        data = self._read_store()
        return list(data.keys())


# Global token store instance
_token_store_instance = TokenStore()

def set_token(server_id: str, token: str):
    """Store a token for a server."""
    _token_store_instance.set_token(server_id, token)

def get_token(server_id: str) -> Optional[str]:
    """Retrieve a token for a server."""
    return _token_store_instance.get_token(server_id)

def clear_token(server_id: str):
    """Remove a token for a server."""
    _token_store_instance.clear_token(server_id)

def get_token_store_keys() -> list:
    """Get list of all server IDs with stored tokens."""
    return _token_store_instance.get_all_server_ids()

