import os
import logging
from loguru import logger
import httpx
import uuid
from typing import Optional, Dict, Any, Union, List, Literal, Tuple, Callable, Awaitable
from mcp.server.fastmcp.exceptions import ToolError
import asyncio
import time
import base64
import json

# Define exceptions locally instead of importing them
class NiFiAuthenticationError(Exception):
    """Raised when there is an error authenticating with NiFi."""
    pass

class NiFiOperationError(Exception):
    """Raised when there is an error performing an operation in NiFi."""
    pass

# Define needed models locally
class AuthParams:
    """Authentication parameters for NiFi."""
    def __init__(self, username: Optional[str] = None, password: Optional[str] = None):
        self.username = username
        self.password = password

class NiFiClient:
    """A simple asynchronous client for the NiFi REST API."""

    def __init__(self, base_url: str, username: Optional[str] = None, password: Optional[str] = None, 
                 tls_verify: bool = True, credential_callback: Optional[Callable[[str], Awaitable[Tuple[str, str]]]] = None):
        """Initializes the NiFiClient.

        Args:
            base_url: The base URL of the NiFi API (e.g., "https://localhost:8443/nifi-api"). Required.
            username: The username for NiFi authentication. Optional - will prompt if not provided.
            password: The password for NiFi authentication. Optional - will prompt if not provided.
            tls_verify: Whether to verify the server's TLS certificate. Defaults to True.
            credential_callback: Optional async callback function that takes a server_id and returns (username, password).
                                If provided, will be called when credentials are needed.
        """
        if not base_url:
            raise ValueError("base_url is required for NiFiClient")
        self.base_url = base_url
        self.username = username
        self.password = password
        self.tls_verify = tls_verify
        self.credential_callback = credential_callback
        self._client = None
        self._token = None
        self._auth_config = None  # Cache auth configuration
        # Generate a unique client ID for this instance, used for revisions
        self._client_id = str(uuid.uuid4())
        logger.info(f"NiFiClient initialized for {self.base_url} with client ID: {self._client_id}")

    @property
    def is_authenticated(self) -> bool:
        """Checks if the client currently holds an authentication token."""
        return self._token is not None

    async def _get_client(self):
        """Returns an httpx client instance, configuring auth if token exists."""
        # Always create a new client instance to ensure headers are fresh,
        # especially after authentication. If performance becomes an issue,
        # we could optimize, but this ensures correctness.
        if self._client:
             await self._client.aclose() # Ensure old connection is closed if recreating
             self._client = None

        headers = {}
        cookies = {}
        if self._token:
            # Try both Authorization header and cookie format
            # Some NiFi 2.x OIDC setups expect the token in a cookie
            headers["Authorization"] = f"Bearer {self._token}"
            # Also set as cookie (NiFi 2.x OIDC may require this)
            cookies["__Secure-Authorization-Bearer"] = self._token
            # NiFi often requires client ID for state changes, let's check if we need it here
            # Might need to parse initial response or call another endpoint if needed.

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            verify=self.tls_verify,
            headers=headers,
            cookies=cookies if cookies else None,
            timeout=30.0 # Keep timeout
        )
        return self._client

    async def get_authentication_config(self) -> Dict[str, Any]:
        """Gets the authentication configuration from NiFi."""
        if self._auth_config:
            return self._auth_config
            
        async with httpx.AsyncClient(base_url=self.base_url, verify=self.tls_verify) as client:
            endpoint = "/authentication/configuration"
            try:
                response = await client.get(endpoint)
                response.raise_for_status()
                self._auth_config = response.json()
                return self._auth_config
            except httpx.HTTPStatusError as e:
                logger.debug(f"Auth config endpoint returned {e.response.status_code} - {e.response.text}")
                # If endpoint doesn't exist (NiFi 1.x) or requires auth (401), assume username/password auth
                if e.response.status_code in (404, 401):
                    # 404 = endpoint doesn't exist (NiFi 1.x)
                    # 401 = endpoint requires authentication (NiFi 1.x or 2.x with auth required)
                    # In both cases, assume we can use username/password authentication
                    self._auth_config = {"authenticationConfiguration": {"externalLoginRequired": False, "loginSupported": True}}
                    return self._auth_config
                raise NiFiAuthenticationError(f"Failed to get auth config: {e.response.status_code}") from e
            except httpx.RequestError as e:
                logger.error(f"Error getting auth config: {e}")
                raise NiFiAuthenticationError(f"Error getting auth config: {e}") from e

    async def _get_credentials(self, server_id: Optional[str] = None) -> Tuple[str, str]:
        """Gets credentials, prompting if not available."""
        # Normalize empty strings to None
        username = self.username
        password = self.password
        
        # Treat empty strings and None as missing
        if username and isinstance(username, str):
            username = username.strip()
            if username == "":
                username = None
        if password and isinstance(password, str):
            if password == "":
                password = None
        
        # If we have a callback and credentials are missing, use it
        if (not username or not password) and self.credential_callback:
            logger.debug(f"Credentials missing for server {server_id}, using callback")
            try:
                username, password = await self.credential_callback(server_id or "unknown")
                if username and password:
                    logger.debug(f"Credentials retrieved from callback for server {server_id}")
                    return username, password
            except Exception as e:
                logger.error(f"Error getting credentials from callback: {e}")
                raise NiFiAuthenticationError(f"Failed to get credentials: {e}") from e
        
        # If still missing, raise error (in web context, callback should be provided)
        if not username or not password:
            raise NiFiAuthenticationError(
                "Credentials not provided and no credential callback available. "
                "Please provide username/password in config or implement credential_callback."
            )
        
        return username, password

    async def _authenticate_oidc(self, username: str, password: str) -> str:
        """Authenticates using OIDC/OAuth2 with username/password.
        
        Attempts multiple authentication methods:
        1. OAuth2 Resource Owner Password Credentials grant
        2. Basic Auth with username/password
        3. Direct token exchange endpoints
        
        Note: Many OIDC providers don't support username/password programmatically
        and require browser-based authentication flow.
        """
        auth_config = await self.get_authentication_config()
        auth_config_data = auth_config.get("authenticationConfiguration", {})
        login_uri = auth_config_data.get("loginUri", "")
        
        logger.info(f"Attempting OIDC authentication for {self.base_url}")
        
        import base64
        
        # Prepare Basic Auth header
        credentials_b64 = base64.b64encode(f"{username}:{password}".encode()).decode()
        basic_auth_header = f"Basic {credentials_b64}"
        
        async with httpx.AsyncClient(base_url=self.base_url, verify=self.tls_verify) as client:
            try:
                # Try multiple endpoints and methods
                token_endpoints = [
                    ("/access/oidc/exchange", "form"),  # NiFi-specific OIDC exchange
                    ("/oauth2/token", "form"),  # Standard OAuth2 token endpoint
                    ("/access/token", "form"),  # Fallback to standard endpoint
                    ("/access/oidc/exchange", "basic"),  # Try with Basic Auth
                    ("/oauth2/token", "basic"),  # Try with Basic Auth
                ]
                
                for endpoint, auth_method in token_endpoints:
                    try:
                        logger.debug(f"Trying OIDC authentication at {endpoint} with {auth_method} auth")
                        
                        headers = {"Content-Type": "application/x-www-form-urlencoded"}
                        data = {}
                        
                        if auth_method == "basic":
                            # Use Basic Auth header
                            headers["Authorization"] = basic_auth_header
                            # Some endpoints might still need grant_type in body
                            data = {"grant_type": "password"}
                        else:
                            # Use form data with username/password
                            data = {
                                "username": username,
                                "password": password,
                                "grant_type": "password"
                            }
                        
                        response = await client.post(
                            endpoint,
                            data=data,
                            headers=headers
                        )
                        
                        if response.status_code == 200:
                            # Try to parse as JSON first (OAuth2 standard)
                            try:
                                token_data = response.json()
                                token = token_data.get("access_token") or token_data.get("token") or token_data.get("id_token")
                                if token:
                                    logger.info(f"OIDC authentication successful via {endpoint} with {auth_method} auth")
                                    return token
                            except:
                                # If not JSON, treat as plain text token
                                token = response.text.strip()
                                if token and len(token) > 10:  # Basic sanity check
                                    logger.info(f"OIDC authentication successful via {endpoint} with {auth_method} auth")
                                    return token
                    except httpx.HTTPStatusError as e:
                        # Log but continue trying other methods
                        if e.response.status_code not in (404, 401, 409):
                            logger.debug(f"Endpoint {endpoint} ({auth_method}) returned {e.response.status_code}: {e.response.text[:200]}")
                        continue
                    except Exception as e:
                        logger.debug(f"Error trying {endpoint} ({auth_method}): {e}, trying next...")
                        continue
                
                # If all endpoints failed, provide detailed error message
                auth_config_str = f"Login URI: {login_uri}" if login_uri else "No login URI configured"
                raise NiFiAuthenticationError(
                    f"OIDC authentication failed. This NiFi 2.x server requires OIDC/OAuth2 authentication, "
                    f"but programmatic username/password authentication is not supported by the OIDC provider. "
                    f"{auth_config_str}. "
                    f"Please contact your administrator to obtain an API access token, or use browser-based authentication "
                    f"to obtain a token that can be used for API access."
                )
                
            except NiFiAuthenticationError:
                raise
            except httpx.RequestError as e:
                logger.error(f"Network error during OIDC authentication: {e}")
                raise NiFiAuthenticationError(f"Network error during OIDC authentication: {e}") from e

    async def authenticate(self, server_id: Optional[str] = None):
        """Authenticates with NiFi and stores the token.
        
        Args:
            server_id: Optional server ID for credential callback context.
        """
        # Get authentication configuration
        auth_config = await self.get_authentication_config()
        auth_config_data = auth_config.get("authenticationConfiguration", {})
        
        external_login_required = auth_config_data.get("externalLoginRequired", False)
        login_supported = auth_config_data.get("loginSupported", True)
        
        if external_login_required:
            # OIDC/OAuth2 authentication required
            logger.info(f"NiFi server requires OIDC/OAuth2 authentication (server_id: {server_id})")
            
            # First check if we have a token stored directly
            if server_id:
                from nifi_mcp_server.token_store import get_token, get_token_store_keys
                logger.debug(f"Checking for stored token for server_id: {server_id}")
                stored_token = get_token(server_id)
                if stored_token:
                    # Validate token format (should be a JWT with 3 parts separated by dots)
                    token_parts = stored_token.split('.')
                    if len(token_parts) == 3:
                        logger.info(f"Using stored access token for OIDC authentication (token length: {len(stored_token)}, JWT format valid)")
                    else:
                        logger.warning(f"Token format may be invalid (expected JWT with 3 parts, got {len(token_parts)} parts)")
                    self._token = stored_token
                    # Force recreation of the main client with the token
                    if self._client:
                        await self._client.aclose()
                    self._client = None
                    logger.debug(f"Token set, client recreated. is_authenticated={self.is_authenticated}")
                    return
                else:
                    available_servers = get_token_store_keys()
                    logger.warning(
                        f"No stored token found for OIDC server {server_id}. "
                        f"Available tokens for servers: {available_servers}. "
                        f"Token store keys: {available_servers}"
                    )
            else:
                logger.warning(f"OIDC authentication required but server_id is None - cannot retrieve stored token")
            
            # If no token found, raise error - OIDC requires token, not username/password
            raise NiFiAuthenticationError(
                f"OIDC authentication requires an access token. "
                f"No token found for server {server_id}. "
                f"Please provide an access token via the credential prompt or API."
            )
        
        # Fall back to username/password authentication (NiFi 1.x style)
        # Get credentials (prompt if needed)
        username, password = await self._get_credentials(server_id)
        
        # Fall back to username/password authentication (NiFi 1.x style)
        async with httpx.AsyncClient(base_url=self.base_url, verify=self.tls_verify) as auth_client:
            endpoint = "/access/token"
            try:
                logger.info(f"Authenticating with NiFi at {self.base_url}{endpoint}")
                response = await auth_client.post(
                    endpoint,
                    data={"username": username, "password": password},
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )
                response.raise_for_status()
                self._token = response.text
                logger.info("Authentication successful.")
                
                # Force recreation of the main client with the token
                if self._client:
                    await self._client.aclose()
                self._client = None

            except httpx.HTTPStatusError as e:
                error_text = e.response.text
                logger.error(f"Authentication failed: {e.response.status_code} - {error_text}")
                
                # Provide helpful error message for 409 (OIDC required)
                if e.response.status_code == 409:
                    raise NiFiAuthenticationError(
                        f"Authentication failed: This NiFi server requires OIDC/OAuth2 authentication. "
                        f"Please check the server configuration or contact your administrator."
                    ) from e
                
                raise NiFiAuthenticationError(f"Authentication failed: {e.response.status_code}") from e
            except httpx.RequestError as e:
                logger.error(f"An error occurred during authentication: {e}")
                raise NiFiAuthenticationError(f"An error occurred during authentication: {e}") from e
            except Exception as e:
                logger.error(f"An unexpected error occurred during authentication: {e}", exc_info=True)
                raise NiFiAuthenticationError(f"An unexpected error occurred during authentication: {e}") from e

    async def close(self):
        """Closes the underlying httpx client."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("NiFi client connection closed.")

    # --- Placeholder for other API methods ---
    async def get_root_process_group_id(self, user_request_id: str = "-", action_id: str = "-") -> str:
        """Gets the ID of the root process group."""
        local_logger = logger.bind(user_request_id=user_request_id, action_id=action_id)
        if not self._token:
            local_logger.error("Authentication required before getting root process group ID.")
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = "/flow/process-groups/root"
        try:
            local_logger.info(f"Fetching root process group ID from {self.base_url}{endpoint}")
            response = await client.get(endpoint)
            response.raise_for_status()
            data = response.json()
            # Check both top-level ID and nested ID within processGroupFlow
            root_id = data.get('id')
            if not root_id and 'processGroupFlow' in data and isinstance(data['processGroupFlow'], dict):
                root_id = data['processGroupFlow'].get('id')
                
            if not root_id:
                 local_logger.error(f"Root process group ID not found in response structure: {data}") # Log structure on error
                 raise ConnectionError("Could not extract root process group ID from response.")
            local_logger.info(f"Retrieved root process group ID: {root_id}")
            return root_id
        except httpx.HTTPStatusError as e:
            local_logger.error(f"Failed to get root process group ID: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to get root process group ID: {e.response.status_code}") from e
        except (httpx.RequestError, ValueError) as e:
            local_logger.error(f"Error getting root process group ID: {e}")
            raise ConnectionError(f"Error getting root process group ID: {e}") from e
        except Exception as e:
            local_logger.error(f"An unexpected error occurred getting root process group ID: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred getting root process group ID: {e}") from e

    async def list_processors(self, process_group_id: str, user_request_id: str = "-", action_id: str = "-") -> list[dict]:
        """Lists processors within a specified process group."""
        local_logger = logger.bind(user_request_id=user_request_id, action_id=action_id)
        
        if not self._token:
            local_logger.error("Authentication required before listing processors.")
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/process-groups/{process_group_id}/processors"
        try:
            local_logger.info(f"Fetching processors for group {process_group_id} from {self.base_url}{endpoint}")
            response = await client.get(endpoint)
            response.raise_for_status()
            data = response.json()
            # The response is typically a ProcessorsEntity which has a 'processors' key containing a list
            processors = data.get("processors", [])
            local_logger.info(f"Found {len(processors)} processors in group {process_group_id}.")
            return processors

        except httpx.HTTPStatusError as e:
            local_logger.error(f"Failed to list processors for group {process_group_id}: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to list processors: {e.response.status_code}") from e
        except (httpx.RequestError, ValueError) as e:
            local_logger.error(f"Error listing processors for group {process_group_id}: {e}")
            raise ConnectionError(f"Error listing processors: {e}") from e
        except Exception as e:
            local_logger.error(f"An unexpected error occurred listing processors: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred listing processors: {e}") from e

    async def create_processor(
        self,
        process_group_id: str,
        processor_type: str,
        name: str,
        position: Dict[str, float],
        config: Optional[Dict[str, Any]] = None
    ) -> dict:
        """Creates a new processor in the specified process group."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/process-groups/{process_group_id}/processors"

        # Construct the request body (ProcessorEntity)
        request_body = {
            "revision": {
                "clientId": self._client_id,
                "version": 0
            },
            "component": {
                "type": processor_type,
                "name": name,
                "position": position,
            }
        }
        # Add config if provided (simplified - real config might need more structure)
        if config:
            request_body["component"]["config"] = {"properties": config}

        try:
            logger.info(f"Creating processor '{name}' ({processor_type}) in group {process_group_id} at {position}")
            response = await client.post(endpoint, json=request_body)
            response.raise_for_status() # Checks for 4xx/5xx errors
            created_processor_data = response.json()
            logger.info(f"Successfully created processor '{name}' with ID: {created_processor_data.get('id')}")
            return created_processor_data # Return the full response body

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to create processor '{name}': {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to create processor: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error creating processor '{name}': {e}")
            raise ConnectionError(f"Error creating processor: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred creating processor '{name}': {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred creating processor: {e}") from e

    async def create_connection(
        self,
        process_group_id: str,
        source_id: str,
        target_id: str,
        relationships: list[str],
        source_type: str = "PROCESSOR", # Usually PROCESSOR
        target_type: str = "PROCESSOR", # Usually PROCESSOR
        name: Optional[str] = None
    ) -> dict:
        """Creates a connection between two components in a process group."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/process-groups/{process_group_id}/connections"

        # Construct the request body (ConnectionEntity)
        request_body = {
            "revision": {
                "clientId": self._client_id,
                "version": 0
            },
            "component": {
                "name": name or "", # Optional connection name
                "source": {
                    "id": source_id,
                    "groupId": process_group_id,
                    "type": source_type.upper()
                },
                "destination": {
                    "id": target_id,
                    "groupId": process_group_id,
                    "type": target_type.upper()
                },
                "selectedRelationships": relationships
                # Can add other config like flowfileExpiration, backPressureObjectThreshold etc. if needed
            }
        }

        try:
            logger.info(f"Creating connection from {source_id} ({relationships}) to {target_id} in group {process_group_id}")
            response = await client.post(endpoint, json=request_body)
            response.raise_for_status()
            created_connection_data = response.json()
            logger.info(f"Successfully created connection with ID: {created_connection_data.get('id')}")
            return created_connection_data

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to create connection: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to create connection: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error creating connection: {e}")
            raise ConnectionError(f"Error creating connection: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred creating connection: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred creating connection: {e}") from e

    async def get_processor_details(self, processor_id: str) -> dict:
        """Fetches the details and configuration of a specific processor."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/processors/{processor_id}"
        try:
            logger.info(f"Fetching details for processor {processor_id} from {self.base_url}{endpoint}")
            response = await client.get(endpoint)
            logger.debug(f"NiFiClient.get_processor_details: GET {endpoint} - Response Status: {response.status_code}, Response Body Raw: {response.text}") # Added log
            response.raise_for_status()
            processor_details = response.json()
            logger.info(f"Successfully fetched details for processor {processor_id}")
            return processor_details

        except httpx.HTTPStatusError as e:
            # Handle 404 Not Found specifically
            if e.response.status_code == 404:
                logger.warning(f"Processor with ID {processor_id} not found.")
                raise ValueError(f"Processor with ID {processor_id} not found.") from e # Raise ValueError for not found
            else:
                logger.error(f"Failed to get details for processor {processor_id}: {e.response.status_code} - {e.response.text}")
                raise ConnectionError(f"Failed to get processor details: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error getting details for processor {processor_id}: {e}")
            raise ConnectionError(f"Error getting processor details: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred getting processor details for {processor_id}: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred getting processor details: {e}") from e

    async def delete_processor(self, processor_id: str, version: int) -> bool:
        """Deletes a processor given its ID and current revision version."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        # The version must be passed as a query parameter, along with the client ID
        endpoint = f"/processors/{processor_id}?version={version}&clientId={self._client_id}"

        try:
            logger.info(f"Attempting to delete processor {processor_id} (version {version}) using {self.base_url}{endpoint}")
            response = await client.delete(endpoint)
            response.raise_for_status() # Raises HTTPStatusError for 4xx/5xx

            # Check if deletion was successful (usually returns 200 OK with the entity deleted)
            if response.status_code == 200:
                 logger.info(f"Successfully deleted processor {processor_id}.")
                 # We could return the response JSON, but a boolean might suffice
                 return True
            else:
                 # This case might not be reachable if raise_for_status is effective
                 logger.warning(f"Processor deletion for {processor_id} returned status {response.status_code}, but expected 200.")
                 return False

        except httpx.HTTPStatusError as e:
            # Handle specific errors like 404 (Not Found) or 409 (Conflict - likely wrong version)
            if e.response.status_code == 404:
                 logger.warning(f"Processor {processor_id} not found for deletion.")
                 # Consider if this should be True (it's already gone) or False/raise error
                 return False # Treat as failure to delete *now*
            elif e.response.status_code == 409:
                 logger.error(f"Conflict deleting processor {processor_id}. Check revision version ({version}). Response: {e.response.text}")
                 raise ValueError(f"Conflict deleting processor {processor_id}. Ensure correct version ({version}) is used.") from e
            else:
                 logger.error(f"Failed to delete processor {processor_id}: {e.response.status_code} - {e.response.text}")
                 raise ConnectionError(f"Failed to delete processor: {e.response.status_code}, {e.response.text}") from e
        except httpx.RequestError as e:
            logger.error(f"Error deleting processor {processor_id}: {e}")
            raise ConnectionError(f"Error deleting processor: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred deleting processor {processor_id}: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred deleting processor: {e}") from e

    async def get_connection(self, connection_id: str) -> dict:
        """Fetches the details of a specific connection."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/connections/{connection_id}"
        try:
            logger.info(f"Fetching details for connection {connection_id} from {self.base_url}{endpoint}")
            response = await client.get(endpoint)
            response.raise_for_status()
            connection_details = response.json()
            logger.info(f"Successfully fetched details for connection {connection_id}")
            return connection_details

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Connection with ID {connection_id} not found.")
                raise ValueError(f"Connection with ID {connection_id} not found.") from e
            else:
                logger.error(f"Failed to get details for connection {connection_id}: {e.response.status_code} - {e.response.text}")
                raise ConnectionError(f"Failed to get connection details: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error getting details for connection {connection_id}: {e}")
            raise ConnectionError(f"Error getting connection details: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred getting connection details for {connection_id}: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred getting connection details: {e}") from e

    async def list_connections(self, process_group_id: Optional[str] = None, user_request_id: str = "-", action_id: str = "-") -> list[dict]:
        """Lists all connections in a process group or in the entire flow if no process_group_id is provided."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        
        if process_group_id:
            # List connections in a specific process group
            endpoint = f"/process-groups/{process_group_id}/connections"
            log_message = f"Fetching connections in process group {process_group_id}"
        else:
            # List all connections in the flow
            endpoint = "/connections"
            log_message = "Fetching all connections in the flow"
            
        logger.info(f"{log_message} from {self.base_url}{endpoint}")
        
        try:
            response = await client.get(
                endpoint,
                headers={"User-Request-ID": user_request_id, "Action-ID": action_id}
            )
            response.raise_for_status()
            data = response.json()
            
            # Extract the connections from the response
            connections = data.get("connections", [])
            logger.info(f"Successfully fetched {len(connections)} connections")
            return connections
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Error listing connections: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to list connections: {e.response.status_code}, {e.response.text}") from e
        except httpx.RequestError as e:
            error_msg = str(e) or repr(e) or "Unknown request error"
            logger.error(f"Error listing connections: {error_msg}")
            raise ConnectionError(f"Error listing connections: {error_msg}") from e

    async def delete_connection(self, connection_id: str, version_number: Optional[int] = None) -> bool:
        """Deletes a connection given its ID and optionally a revision version. 
           If version is not provided, it will fetch the current version."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        
        # Get current version if not provided
        if version_number is None:
            try:
                connection_details = await self.get_connection(connection_id)
                version_number = connection_details.get("revision", {}).get("version")
                if version_number is None:
                    raise ValueError(f"Could not determine revision version for connection {connection_id}")
                logger.info(f"Using fetched revision version {version_number} for connection {connection_id}")
            except Exception as e:
                logger.error(f"Failed to fetch connection {connection_id} details to get version: {e}")
                raise

        endpoint = f"/connections/{connection_id}?version={version_number}&clientId={self._client_id}"

        try:
            logger.info(f"Deleting connection {connection_id} (version {version_number}) from {self.base_url}{endpoint}")
            response = await client.delete(endpoint)
            response.raise_for_status()

            if response.status_code == 200:
                logger.info(f"Successfully deleted connection {connection_id}.")
                return True
            else:
                logger.warning(f"Connection deletion for {connection_id} returned status {response.status_code}, expected 200.")
                return False

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Connection {connection_id} not found for deletion.")
                return False
            elif e.response.status_code == 409:
                logger.error(f"Conflict deleting connection {connection_id}. Check revision version ({version_number}) or ensure connection is empty. Response: {e.response.text}")
                raise ValueError(f"Conflict deleting connection {connection_id}. Ensure correct version ({version_number}) and emptiness.") from e
            else:
                logger.error(f"Failed to delete connection {connection_id}: {e.response.status_code} - {e.response.text}")
                raise ConnectionError(f"Failed to delete connection: {e.response.status_code}, {e.response.text}") from e
        except httpx.RequestError as e:
            logger.error(f"Error deleting connection {connection_id}: {e}")
            raise ConnectionError(f"Error deleting connection: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred deleting connection {connection_id}: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred deleting connection: {e}") from e

    async def update_connection(self, connection_id: str, update_payload: Dict[str, Any]) -> Dict:
        """Updates a specific connection using the provided payload (including revision and component)."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/connections/{connection_id}"
        revision = update_payload.get("revision", {})
        version = revision.get("version", "UNKNOWN")

        try:
            logger.info(f"Updating connection {connection_id} (Version: {version}).")
            # Log selected relationships being set
            selected_relationships = update_payload.get("component", {}).get("selectedRelationships")
            if selected_relationships is not None:
                logger.debug(f"Setting selectedRelationships to: {selected_relationships}")
                
            response = await client.put(endpoint, json=update_payload)
            response.raise_for_status()
            updated_entity = response.json()
            logger.info(f"Successfully updated connection {connection_id}. New revision: {updated_entity.get('revision', {}).get('version')}")
            return updated_entity

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Connection {connection_id} not found for update.")
                raise ValueError(f"Connection with ID {connection_id} not found.") from e
            elif e.response.status_code == 409:
                logger.error(f"Conflict updating connection {connection_id}. Revision ({version}) likely stale. Response: {e.response.text}")
                raise ValueError(f"Conflict updating connection {connection_id}. Revision mismatch.") from e
            else:
                logger.error(f"Failed to update connection {connection_id}: {e.response.status_code} - {e.response.text}")
                raise ConnectionError(f"Failed to update connection: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error updating connection {connection_id}: {e}")
            raise ConnectionError(f"Error updating connection: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred updating connection {connection_id}: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred updating connection: {e}") from e

    async def update_processor_config(
        self,
        processor_id: str,
        update_type: str,
        update_data: Union[Dict[str, Any], List[str]]
    ) -> dict:
        """Updates specific parts of a processor's component configuration (properties or auto-terminated relationships)."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        # Validate update_type
        valid_update_types = ["properties", "auto-terminatedrelationships"]
        if update_type not in valid_update_types:
            raise ValueError(f"Invalid update_type '{update_type}'. Must be one of {valid_update_types}")

        # 1. Get current processor entity to obtain the latest revision
        logger.info(f"Fetching current details for processor {processor_id} before update.")
        try:
            current_entity = await self.get_processor_details(processor_id)
            current_revision = current_entity["revision"]
            current_component = current_entity["component"]
        except (ValueError, ConnectionError) as e:
            logger.error(f"Failed to fetch processor {processor_id} for update: {e}")
            raise

        # 2. Prepare the update payload
        # Start with essential component details
        update_component = {
            "id": current_component["id"],
            # Keep existing name, position etc. unless explicitly changed later
            "name": current_component.get("name"),
            "position": current_component.get("position"),
            # Copy existing config first, then overwrite specific part
            "config": current_component.get("config", {}).copy(),
        }

        # Apply the specific update based on update_type
        log_message_part = "unknown configuration part"
        if update_type == "properties":
            if not isinstance(update_data, dict):
                raise TypeError("update_data must be a dictionary when update_type is 'properties'.")
            
            # Ensure config and properties sub-dict exist
            if "config" not in update_component:
                 update_component["config"] = {}
            if "properties" not in update_component["config"]:
                 update_component["config"]["properties"] = {} # Initialize/clear existing

            KNOWN_DIRECT_CONFIG_KEYS = {
                "schedulingStrategy", "schedulingPeriod", "executionNode", "comments",
                "penaltyDuration", "yieldDuration", "bulletinLevel", "runDurationMillis",
                "lossTolerant"
            }
            
            temp_properties_to_set = {}
            direct_config_to_set = {}

            for key, value in update_data.items():
                # Normalize key for matching (e.g. "Scheduling Strategy" vs "schedulingStrategy")
                # However, NiFi API is case-sensitive for these direct keys.
                # The keys in update_data likely come from user input or previous GETs.
                # For now, assume keys in update_data match NiFi's expected case for direct keys.
                if key in KNOWN_DIRECT_CONFIG_KEYS:
                    direct_config_to_set[key] = value
                else:
                    # Assume it's a true property meant for the 'properties' sub-dictionary
                    temp_properties_to_set[key] = value
            
            # Assign true properties
            update_component["config"]["properties"] = temp_properties_to_set
            
            # Assign direct config items
            for key, value in direct_config_to_set.items():
                update_component["config"][key] = value
                
            log_message_part = f"properties: {temp_properties_to_set}, direct_configs: {direct_config_to_set}"

        elif update_type == "auto-terminatedrelationships":
            # Expect a list of strings (relationship names)
            if not isinstance(update_data, list) or not all(isinstance(item, str) for item in update_data):
                 raise TypeError("update_data must be a list of strings (relationship names) when update_type is 'auto-terminatedrelationships'.")
            
            # Ensure config key exists
            if "config" not in update_component:
                 update_component["config"] = {}
                 
            # Assign to component.config.autoTerminatedRelationships based on UI capture
            update_component["config"]["autoTerminatedRelationships"] = update_data
            log_message_part = f"config.autoTerminatedRelationships: {update_data}"

        # Construct final payload
        update_payload = {
            "revision": current_revision,
            "component": update_component
        }

        # 3. Make the PUT request
        client = await self._get_client()
        endpoint = f"/processors/{processor_id}"
        try:
            logger.debug(f"NiFiClient.update_processor_config: Sending PUT request to {endpoint} with payload: {update_payload}") # Added log
            logger.info(f"Updating processor {processor_id} (Version: {current_revision.get('version')}). Updating {log_message_part}")
            response = await client.put(endpoint, json=update_payload)
            response.raise_for_status()
            updated_entity = response.json()
            logger.info(f"Successfully updated processor {processor_id}. New revision: {updated_entity.get('revision', {}).get('version')}")
            return updated_entity

        except httpx.HTTPStatusError as e:
            # Handle 409 Conflict (likely stale revision)
            if e.response.status_code == 409:
                logger.error(f"NiFiClient.update_processor_config: Conflict. Endpoint: {e.request.url}, Method: {e.request.method}, Sent Payload: {update_payload}, Response Status: {e.response.status_code}, Response Body: {e.response.text}") # Added log
                logger.error(f"Conflict updating processor {processor_id}. Revision ({current_revision.get('version')}) likely stale. Response: {e.response.text}")
                raise ValueError(f"Conflict updating processor {processor_id}. Revision mismatch.") from e
            else:
                logger.error(f"NiFiClient.update_processor_config: HTTP Error. Endpoint: {e.request.url}, Method: {e.request.method}, Sent Payload: {update_payload}, Response Status: {e.response.status_code}, Response Body: {e.response.text}") # Added log
                logger.error(f"Failed to update processor {processor_id}: {e.response.status_code} - {e.response.text}")
                raise ConnectionError(f"Failed to update processor: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error updating processor {processor_id}: {e}")
            raise ConnectionError(f"Error updating processor: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred updating processor {processor_id}: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred updating processor: {e}") from e

    async def update_processor_state(self, processor_id: str, state: str) -> dict:
        """Starts or stops a specific processor."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        normalized_state = state.upper()
        if normalized_state not in ["RUNNING", "STOPPED"]:
            raise ValueError("Invalid state specified. Must be 'RUNNING' or 'STOPPED'.")

        # 1. Get current processor entity to obtain the latest revision
        # We need the revision even just to change the state.
        logger.info(f"Fetching current revision for processor {processor_id} before changing state to {normalized_state}.")
        try:
            # Use get_processor_details as it already handles fetching the entity
            current_entity = await self.get_processor_details(processor_id)
            current_revision = current_entity["revision"]
        except (ValueError, ConnectionError) as e:
            logger.error(f"Failed to fetch processor {processor_id} to update state: {e}")
            raise

        # 2. Prepare the update payload for the run-status endpoint
        update_payload = {
            "revision": current_revision,
            "state": normalized_state,
            "disconnectedNodeAcknowledged": False # Usually required, defaults to false
        }

        # 3. Make the PUT request to the run-status endpoint
        client = await self._get_client()
        endpoint = f"/processors/{processor_id}/run-status"
        try:
            logger.info(f"Setting processor {processor_id} state to {normalized_state} (Version: {current_revision.get('version')}).")
            response = await client.put(endpoint, json=update_payload)
            response.raise_for_status()
            updated_entity = response.json() # The response contains the processor entity with updated status
            logger.info(f"Successfully set processor {processor_id} state to {updated_entity.get('component',{}).get('state', 'UNKNOWN')}. New revision: {updated_entity.get('revision', {}).get('version')}")
            return updated_entity

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                logger.error(f"Conflict changing state for processor {processor_id}. Revision ({current_revision.get('version')}) likely stale. Response: {e.response.text}")
                raise ValueError(f"Conflict changing processor state for {processor_id}. Revision mismatch.") from e
            else:
                logger.error(f"Failed to change state for processor {processor_id}: {e.response.status_code} - {e.response.text}")
                raise ConnectionError(f"Failed to change processor state: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error changing state for processor {processor_id}: {e}")
            raise ConnectionError(f"Error changing processor state: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred changing state for processor {processor_id}: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred changing processor state: {e}") from e

    async def get_process_group_status_snapshot(self, process_group_id: str) -> dict:
        """Fetches the status snapshot for a specific process group, including component states and queue sizes.

        Args:
            process_group_id: The ID of the target process group.

        Returns:
            A dictionary containing the process group status snapshot, typically under the 'processGroupStatus' key.
        """
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/flow/process-groups/{process_group_id}/status"
        try:
            logger.info(f"Fetching status snapshot for process group {process_group_id} from {self.base_url}{endpoint}")
            response = await client.get(endpoint)
            response.raise_for_status()
            status_data = response.json()
            # The core data is usually within processGroupStatus
            logger.info(f"Successfully fetched status snapshot for process group {process_group_id}")
            return status_data.get("processGroupStatus", {}) # Return the main status part

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Process group {process_group_id} not found when fetching status snapshot.")
                raise ValueError(f"Process group with ID {process_group_id} not found.") from e
            else:
                logger.error(f"Failed to get status snapshot for process group {process_group_id}: {e.response.status_code} - {e.response.text}")
                raise ConnectionError(f"Failed to get process group status snapshot: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error getting status snapshot for process group {process_group_id}: {e}")
            raise ConnectionError(f"Error getting process group status snapshot: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred getting status snapshot for {process_group_id}: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred getting process group status snapshot: {e}") from e

    async def get_bulletin_board(self, group_id: Optional[str] = None, source_id: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Fetches bulletins from the NiFi bulletin board, optionally filtered.

        Args:
            group_id: The ID of the process group to filter bulletins by.
            source_id: The ID of the source component to filter bulletins by.
            limit: The maximum number of bulletins to return.

        Returns:
            A list of bulletin dictionaries.
        """
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = "/flow/bulletin-board"
        params = {"limit": limit}
        if group_id:
            params["groupId"] = group_id
        if source_id:
            params["sourceId"] = source_id

        try:
            logger.info(f"Fetching bulletins from {self.base_url}{endpoint} with params: {params}")
            response = await client.get(endpoint, params=params)
            response.raise_for_status()

            # Step 1: Get the raw JSON string from the response
            raw_json_string = response.text  
            cleaned_json_string = raw_json_string.replace('\n', '\\n')
            parsed_json = json.loads(cleaned_json_string)
            bulletins = parsed_json.get('bulletinBoard', {}).get('bulletins', [])
            logger.info(f"Successfully fetched {len(bulletins)} bulletins.")
            return bulletins

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to get bulletins: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to get bulletins: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error getting bulletins: {e}")
            raise ConnectionError(f"Error getting bulletins: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred getting bulletins: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred getting bulletins: {e}") from e

    async def get_parameter_context(self, process_group_id: str, user_request_id: str = "-", action_id: str = "-") -> list:
        """Retrieves the parameter context associated with a process group."""
        local_logger = logger.bind(user_request_id=user_request_id, action_id=action_id)
        local_logger.info(f"Fetching process group {process_group_id} to get parameter context")
        try:
            # First, get the process group details to find the parameter context ID
            pg_details = await self.get_process_group_details(process_group_id, user_request_id=user_request_id, action_id=action_id) # Pass IDs down
            param_context_id = pg_details.get("component", {}).get("parameterContext", {}).get("id")

            if not param_context_id:
                local_logger.info(f"No parameter context found for process group {process_group_id}")
                return []

            # Now fetch the parameter context details using its ID
            client = await self._get_client()
            endpoint = f"/parameter-contexts/{param_context_id}?includeInheritedParameters=true"
            local_logger.info(f"Fetching parameter context {param_context_id} from {self.base_url}{endpoint}")
            response = await client.get(endpoint)
            response.raise_for_status()
            data = response.json()
            parameters = data.get("component", {}).get("parameters", [])
            local_logger.info(f"Found {len(parameters)} parameters in context {param_context_id} for group {process_group_id}.")
            # Extract only parameter name and value if needed, or return full structure
            # simplified_params = [{p['parameter']['name']: p['parameter'].get('value')} for p in parameters]
            return parameters # Return full parameter entity list

        except NiFiAuthenticationError as e:
             local_logger.error(f"Authentication error fetching parameter context for PG {process_group_id}: {e}", exc_info=False)
             raise ToolError(f"Authentication error accessing parameter context for PG {process_group_id}.") from e
        except httpx.HTTPStatusError as e:
            local_logger.error(f"Failed to get parameter context for PG {process_group_id}: {e.response.status_code} - {e.response.text}")
            raise ToolError(f"Failed to get parameter context: {e.response.status_code}") from e
        except (httpx.RequestError, ValueError, ConnectionError) as e:
            local_logger.error(f"Error getting parameter context for PG {process_group_id}: {e}")
            raise ToolError(f"Error getting parameter context: {e}") from e
        except Exception as e:
            local_logger.error(f"An unexpected error occurred getting parameter context for PG {process_group_id}: {e}", exc_info=True)
            raise ToolError(f"An unexpected error occurred getting parameter context: {e}") from e

    async def get_input_ports(self, process_group_id: str) -> list[dict]:
        """Lists input ports within a specified process group."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/process-groups/{process_group_id}/input-ports"
        try:
            logger.info(f"Fetching input ports for group {process_group_id} from {self.base_url}{endpoint}")
            response = await client.get(endpoint)
            response.raise_for_status()
            data = response.json()
            # Response is InputPortsEntity with 'inputPorts' key
            ports = data.get("inputPorts", [])
            logger.info(f"Found {len(ports)} input ports in group {process_group_id}.")
            return ports
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to list input ports for group {process_group_id}: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to list input ports: {e.response.status_code}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error listing input ports for group {process_group_id}: {e}")
            raise ConnectionError(f"Error listing input ports: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred listing input ports: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred listing input ports: {e}") from e

    async def get_output_ports(self, process_group_id: str) -> list[dict]:
        """Lists output ports within a specified process group."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/process-groups/{process_group_id}/output-ports"
        try:
            logger.info(f"Fetching output ports for group {process_group_id} from {self.base_url}{endpoint}")
            response = await client.get(endpoint)
            response.raise_for_status()
            data = response.json()
            # Response is OutputPortsEntity with 'outputPorts' key
            ports = data.get("outputPorts", [])
            logger.info(f"Found {len(ports)} output ports in group {process_group_id}.")
            return ports
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to list output ports for group {process_group_id}: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to list output ports: {e.response.status_code}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error listing output ports for group {process_group_id}: {e}")
            raise ConnectionError(f"Error listing output ports: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred listing output ports: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred listing output ports: {e}") from e

    async def get_process_groups(self, process_group_id: str) -> list[dict]:
        """Lists immediate child process groups within a specified process group."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/process-groups/{process_group_id}/process-groups"
        try:
            logger.info(f"Fetching child process groups for group {process_group_id} from {self.base_url}{endpoint}")
            response = await client.get(endpoint)
            response.raise_for_status()
            data = response.json()
            # Response is ProcessGroupsEntity with 'processGroups' key
            groups = data.get("processGroups", [])
            logger.info(f"Found {len(groups)} child process groups in group {process_group_id}.")
            return groups
        except httpx.HTTPStatusError as e:
            error_text = e.response.text or ""
            logger.error(f"Failed to list process groups for group {process_group_id}: {e.response.status_code} - {error_text}")
            
            # Handle token expiration (401 with "Session Expired" or similar)
            if e.response.status_code == 401 and ("Session Expired" in error_text or "expired" in error_text.lower() or "unauthorized" in error_text.lower()):
                logger.warning(f"Token appears to be expired (401 Unauthorized). Clearing token for potential re-authentication.")
                self._token = None
                # Force client recreation on next use
                if self._client:
                    await self._client.aclose()
                    self._client = None
                raise NiFiAuthenticationError(
                    f"Authentication token has expired. Please provide a new access token. "
                    f"Original error: {e.response.status_code} - {error_text}"
                ) from e
            
            raise ConnectionError(f"Failed to list process groups: {e.response.status_code}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error listing process groups for group {process_group_id}: {e}")
            raise ConnectionError(f"Error listing process groups: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred listing process groups: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred listing process groups: {e}") from e

    async def get_process_group_details(self, process_group_id: str, user_request_id: str = "-", action_id: str = "-") -> dict:
        """Fetches the details of a specific process group."""
        local_logger = logger.bind(user_request_id=user_request_id, action_id=action_id)
        if not self._token:
            local_logger.error("Authentication required before getting process group details.")
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/process-groups/{process_group_id}"
        try:
            local_logger.info(f"Fetching details for process group {process_group_id} from {self.base_url}{endpoint}")
            response = await client.get(endpoint)
            response.raise_for_status()
            group_details = response.json()
            local_logger.info(f"Successfully fetched details for process group {process_group_id}")
            return group_details

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                local_logger.warning(f"Process group with ID {process_group_id} not found.")
                raise ValueError(f"Process group with ID {process_group_id} not found.") from e
            else:
                local_logger.error(f"Failed to get details for process group {process_group_id}: {e.response.status_code} - {e.response.text}")
                raise ConnectionError(f"Failed to get process group details: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            local_logger.error(f"Error getting details for process group {process_group_id}: {e}")
            raise ConnectionError(f"Error getting process group details: {e}") from e
        except Exception as e:
            local_logger.error(f"An unexpected error occurred getting process group details for {process_group_id}: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred getting process group details: {e}") from e

    async def get_process_group_flow(self, process_group_id: str, ui_only: bool = False) -> dict:
        """Fetches the flow details for a specific process group, often including counts.
        
        Args:
            process_group_id: The ID of the process group
            ui_only: If True, adds ?uiOnly=true parameter (useful when individual endpoints fail)
        """
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/flow/process-groups/{process_group_id}"
        if ui_only:
            endpoint += "?uiOnly=true"
        try:
            logger.info(f"Fetching flow details for process group {process_group_id} from {self.base_url}{endpoint}")
            response = await client.get(endpoint)
            response.raise_for_status()
            flow_details = response.json()
            logger.info(f"Successfully fetched flow details for process group {process_group_id}")
            return flow_details

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Process group flow with ID {process_group_id} not found.")
                raise ValueError(f"Process group flow with ID {process_group_id} not found.") from e
            else:
                logger.error(f"Failed to get flow details for process group {process_group_id}: {e.response.status_code} - {e.response.text}")
                raise ConnectionError(f"Failed to get process group flow details: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error getting flow details for process group {process_group_id}: {e}")
            raise ConnectionError(f"Error getting process group flow details: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred getting process group flow details for {process_group_id}: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred getting process group flow details: {e}") from e

    async def get_input_port_details(self, port_id: str) -> dict:
        """Fetches the details of a specific input port."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/input-ports/{port_id}"
        try:
            logger.info(f"Fetching details for input port {port_id} from {self.base_url}{endpoint}")
            response = await client.get(endpoint)
            response.raise_for_status()
            port_details = response.json()
            logger.info(f"Successfully fetched details for input port {port_id}")
            return port_details

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Input port with ID {port_id} not found.")
                raise ValueError(f"Input port with ID {port_id} not found.") from e
            else:
                logger.error(f"Failed to get details for input port {port_id}: {e.response.status_code} - {e.response.text}")
                raise ConnectionError(f"Failed to get input port details: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error getting details for input port {port_id}: {e}")
            raise ConnectionError(f"Error getting input port details: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred getting input port details for {port_id}: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred getting input port details: {e}") from e

    async def get_output_port_details(self, port_id: str) -> dict:
        """Fetches the details of a specific output port."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/output-ports/{port_id}"
        try:
            logger.info(f"Fetching details for output port {port_id} from {self.base_url}{endpoint}")
            response = await client.get(endpoint)
            response.raise_for_status()
            port_details = response.json()
            logger.info(f"Successfully fetched details for output port {port_id}")
            return port_details

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Output port with ID {port_id} not found.")
                raise ValueError(f"Output port with ID {port_id} not found.") from e
            else:
                logger.error(f"Failed to get details for output port {port_id}: {e.response.status_code} - {e.response.text}")
                raise ConnectionError(f"Failed to get output port details: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error getting details for output port {port_id}: {e}")
            raise ConnectionError(f"Error getting output port details: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred getting output port details for {port_id}: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred getting output port details: {e}") from e

    async def delete_input_port(self, port_id: str, version: int) -> bool:
        """Deletes an input port given its ID and current revision version."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/input-ports/{port_id}?version={version}&clientId={self._client_id}"

        try:
            logger.info(f"Attempting to delete input port {port_id} (version {version}) using {self.base_url}{endpoint}")
            response = await client.delete(endpoint)
            response.raise_for_status() # Raises HTTPStatusError for 4xx/5xx

            if response.status_code == 200:
                 logger.info(f"Successfully deleted input port {port_id}.")
                 return True
            else:
                 logger.warning(f"Input port deletion for {port_id} returned status {response.status_code}, expected 200.")
                 return False

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                 logger.warning(f"Input port {port_id} not found for deletion.")
                 return False
            elif e.response.status_code == 409:
                 logger.error(f"Conflict deleting input port {port_id}. Check revision version ({version}) or ensure port is stopped/disconnected. Response: {e.response.text}")
                 raise ValueError(f"Conflict deleting input port {port_id}. Ensure correct version ({version}) and state.") from e
            else:
                 logger.error(f"Failed to delete input port {port_id}: {e.response.status_code} - {e.response.text}")
                 raise ConnectionError(f"Failed to delete input port: {e.response.status_code}, {e.response.text}") from e
        except httpx.RequestError as e:
            logger.error(f"Error deleting input port {port_id}: {e}")
            raise ConnectionError(f"Error deleting input port: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred deleting input port {port_id}: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred deleting input port: {e}") from e

    async def delete_output_port(self, port_id: str, version: int) -> bool:
        """Deletes an output port given its ID and current revision version."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/output-ports/{port_id}?version={version}&clientId={self._client_id}"

        try:
            logger.info(f"Attempting to delete output port {port_id} (version {version}) using {self.base_url}{endpoint}")
            response = await client.delete(endpoint)
            response.raise_for_status()

            if response.status_code == 200:
                 logger.info(f"Successfully deleted output port {port_id}.")
                 return True
            else:
                 logger.warning(f"Output port deletion for {port_id} returned status {response.status_code}, expected 200.")
                 return False

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                 logger.warning(f"Output port {port_id} not found for deletion.")
                 return False
            elif e.response.status_code == 409:
                 logger.error(f"Conflict deleting output port {port_id}. Check revision version ({version}) or ensure port is stopped/disconnected. Response: {e.response.text}")
                 raise ValueError(f"Conflict deleting output port {port_id}. Ensure correct version ({version}) and state.") from e
            else:
                 logger.error(f"Failed to delete output port {port_id}: {e.response.status_code} - {e.response.text}")
                 raise ConnectionError(f"Failed to delete output port: {e.response.status_code}, {e.response.text}") from e
        except httpx.RequestError as e:
            logger.error(f"Error deleting output port {port_id}: {e}")
            raise ConnectionError(f"Error deleting output port: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred deleting output port {port_id}: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred deleting output port: {e}") from e

    async def delete_process_group(self, pg_id: str, version: int) -> bool:
        """Deletes a process group given its ID and current revision version. Fails if not empty."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        # Recursive deletion isn't standard; this deletes only if empty
        endpoint = f"/process-groups/{pg_id}?version={version}&clientId={self._client_id}"

        try:
            logger.info(f"Attempting to delete process group {pg_id} (version {version}) using {self.base_url}{endpoint}")
            response = await client.delete(endpoint)
            response.raise_for_status()

            if response.status_code == 200:
                 logger.info(f"Successfully deleted process group {pg_id}.")
                 return True
            else:
                 logger.warning(f"Process group deletion for {pg_id} returned status {response.status_code}, expected 200.")
                 return False

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                 logger.warning(f"Process group {pg_id} not found for deletion.")
                 return False
            elif e.response.status_code == 409:
                 # This is common if the group is not empty or stopped
                 logger.error(f"Conflict deleting process group {pg_id}. Check revision version ({version}) or ensure group is empty and stopped. Response: {e.response.text}")
                 raise ValueError(f"Conflict deleting process group {pg_id}. Ensure correct version ({version}) and that it is empty and stopped.") from e
            else:
                 logger.error(f"Failed to delete process group {pg_id}: {e.response.status_code} - {e.response.text}")
                 raise ConnectionError(f"Failed to delete process group: {e.response.status_code}, {e.response.text}") from e
        except httpx.RequestError as e:
            logger.error(f"Error deleting process group {pg_id}: {e}")
            raise ConnectionError(f"Error deleting process group: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred deleting process group {pg_id}: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred deleting process group: {e}") from e

    async def update_input_port_state(self, port_id: str, state: str) -> dict:
        """Starts or stops a specific input port."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        normalized_state = state.upper()
        if normalized_state not in ["RUNNING", "STOPPED", "DISABLED"]:
            raise ValueError("Invalid state specified. Must be 'RUNNING' or 'STOPPED' or 'DISABLED'.")

        # 1. Get current details for revision
        logger.info(f"Fetching current revision for input port {port_id} before changing state to {normalized_state}.")
        try:
            current_entity = await self.get_input_port_details(port_id)
            current_revision = current_entity["revision"]
        except (ValueError, ConnectionError) as e:
            logger.error(f"Failed to fetch input port {port_id} to update state: {e}")
            raise

        # 2. Prepare payload
        update_payload = {
            "revision": current_revision,
            "state": normalized_state,
            "disconnectedNodeAcknowledged": False
        }

        # 3. Make PUT request
        client = await self._get_client()
        endpoint = f"/input-ports/{port_id}/run-status"
        try:
            logger.info(f"Setting input port {port_id} state to {normalized_state} (Version: {current_revision.get('version')}).")
            response = await client.put(endpoint, json=update_payload)
            response.raise_for_status()
            updated_entity = response.json()
            logger.info(f"Successfully set input port {port_id} state to {updated_entity.get('component',{}).get('state', 'UNKNOWN')}.")
            return updated_entity
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                logger.error(f"Conflict changing state for input port {port_id}. Revision ({current_revision.get('version')}) likely stale or state invalid. Response: {e.response.text}")
                raise ValueError(f"Conflict changing input port state for {port_id}. Revision mismatch or invalid state.") from e
            else:
                logger.error(f"Failed to change state for input port {port_id}: {e.response.status_code} - {e.response.text}")
                raise ConnectionError(f"Failed to change input port state: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error changing state for input port {port_id}: {e}")
            raise ConnectionError(f"Error changing input port state: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred changing state for input port {port_id}: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred changing input port state: {e}") from e

    async def update_output_port_state(self, port_id: str, state: str) -> dict:
        """Starts or stops a specific output port."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        normalized_state = state.upper()
        if normalized_state not in ["RUNNING", "STOPPED", "DISABLED"]:
            raise ValueError("Invalid state specified. Must be 'RUNNING' or 'STOPPED' or 'DISABLED'.")

        # 1. Get current details for revision
        logger.info(f"Fetching current revision for output port {port_id} before changing state to {normalized_state}.")
        try:
            current_entity = await self.get_output_port_details(port_id)
            current_revision = current_entity["revision"]
        except (ValueError, ConnectionError) as e:
            logger.error(f"Failed to fetch output port {port_id} to update state: {e}")
            raise

        # 2. Prepare payload
        update_payload = {
            "revision": current_revision,
            "state": normalized_state,
            "disconnectedNodeAcknowledged": False
        }

        # 3. Make PUT request
        client = await self._get_client()
        endpoint = f"/output-ports/{port_id}/run-status"
        try:
            logger.info(f"Setting output port {port_id} state to {normalized_state} (Version: {current_revision.get('version')}).")
            response = await client.put(endpoint, json=update_payload)
            response.raise_for_status()
            updated_entity = response.json()
            logger.info(f"Successfully set output port {port_id} state to {updated_entity.get('component',{}).get('state', 'UNKNOWN')}.")
            return updated_entity
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                logger.error(f"Conflict changing state for output port {port_id}. Revision ({current_revision.get('version')}) likely stale or state invalid. Response: {e.response.text}")
                raise ValueError(f"Conflict changing output port state for {port_id}. Revision mismatch or invalid state.") from e
            else:
                logger.error(f"Failed to change state for output port {port_id}: {e.response.status_code} - {e.response.text}")
                raise ConnectionError(f"Failed to change output port state: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error changing state for output port {port_id}: {e}")
            raise ConnectionError(f"Error changing output port state: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred changing state for output port {port_id}: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred changing output port state: {e}") from e

    async def create_input_port(self, pg_id: str, name: str, position: Dict[str, float]) -> dict:
        """Creates a new input port in the specified process group."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/process-groups/{pg_id}/input-ports"

        request_body = {
            "revision": {"clientId": self._client_id, "version": 0},
            "component": {
                "name": name,
                "position": position,
                # Add other defaults if needed, e.g., comments: "", state: "STOPPED"
            }
        }

        try:
            logger.info(f"Creating input port '{name}' in group {pg_id} at {position}")
            response = await client.post(endpoint, json=request_body)
            response.raise_for_status()
            created_port_data = response.json()
            logger.info(f"Successfully created input port '{name}' with ID: {created_port_data.get('id')}")
            return created_port_data
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to create input port '{name}': {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to create input port: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error creating input port '{name}': {e}")
            raise ConnectionError(f"Error creating input port: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred creating input port '{name}': {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred creating input port: {e}") from e

    async def create_output_port(self, pg_id: str, name: str, position: Dict[str, float]) -> dict:
        """Creates a new output port in the specified process group."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/process-groups/{pg_id}/output-ports"

        request_body = {
            "revision": {"clientId": self._client_id, "version": 0},
            "component": {
                "name": name,
                "position": position,
            }
        }

        try:
            logger.info(f"Creating output port '{name}' in group {pg_id} at {position}")
            response = await client.post(endpoint, json=request_body)
            response.raise_for_status()
            created_port_data = response.json()
            logger.info(f"Successfully created output port '{name}' with ID: {created_port_data.get('id')}")
            return created_port_data
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to create output port '{name}': {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to create output port: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error creating output port '{name}': {e}")
            raise ConnectionError(f"Error creating output port: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred creating output port '{name}': {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred creating output port: {e}") from e

    async def create_process_group(self, parent_pg_id: str, name: str, position: Dict[str, float]) -> dict:
        """Creates a new process group within the specified parent process group."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/process-groups/{parent_pg_id}/process-groups"

        request_body = {
            "revision": {"clientId": self._client_id, "version": 0},
            "component": {
                "name": name,
                "position": position,
                # Add other defaults if needed, e.g., comments: ""
            }
        }

        try:
            logger.info(f"Creating process group '{name}' in parent group {parent_pg_id} at {position}")
            response = await client.post(endpoint, json=request_body)
            response.raise_for_status()
            created_pg_data = response.json()
            logger.info(f"Successfully created process group '{name}' with ID: {created_pg_data.get('id')}")
            return created_pg_data
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to create process group '{name}': {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to create process group: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error creating process group '{name}': {e}")
            raise ConnectionError(f"Error creating process group: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred creating process group '{name}': {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred creating process group: {e}") from e

    async def get_processor_types(self) -> List[Dict]:
        """Fetches the list of available processor types from the NiFi instance."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = "/flow/processor-types"

        try:
            logger.info(f"Fetching available processor types from {self.base_url}{endpoint}")
            response = await client.get(endpoint)
            response.raise_for_status()
            data = response.json()
            # The response is ProcessorTypesEntity, containing 'processorTypes' list
            processor_types = data.get("processorTypes", [])
            logger.info(f"Successfully fetched {len(processor_types)} available processor types.")
            return processor_types

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to get processor types: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to get processor types: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"Error getting processor types: {e}")
            raise ConnectionError(f"Error getting processor types: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred getting processor types: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred getting processor types: {e}") from e

    async def search_flow(self, query: str) -> Dict:
        """Performs a global search across the NiFi flow using the provided query string."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = "/flow/search-results"
        params = {"q": query}

        try:
            logger.info(f"Performing global flow search with query '{query}' using {self.base_url}{endpoint}")
            response = await client.get(endpoint, params=params)
            response.raise_for_status()
            search_results = response.json()
            logger.info(f"Successfully performed global flow search for query '{query}'.")
            # The response structure usually includes a top-level key like 'searchResultsDTO'
            # Example: { "searchResultsDTO": { "processorResults": [...], ... } }
            return search_results

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to perform flow search for query '{query}': {e.response.status_code} - {e.response.text}")
            # Don't raise ValueError for 404, search simply might not find anything or endpoint might differ
            raise ConnectionError(f"Failed to perform flow search: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e: # Include ValueError for potential JSON parsing issues
            logger.error(f"Error performing flow search for query '{query}': {e}")
            raise ConnectionError(f"Error performing flow search: {e}") from e
        except Exception as e:
            logger.error(f"An unexpected error occurred performing flow search for query '{query}': {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred performing flow search: {e}") from e

    async def update_process_group_state(self, pg_id: str, state: str) -> dict:
        """Starts or stops all eligible components within a specific process group."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        normalized_state = state.upper()
        if normalized_state not in ["RUNNING", "STOPPED"]:
            raise ValueError("Invalid state specified. Must be 'RUNNING' or 'STOPPED'.")

        client = await self._get_client()
        endpoint = f"/flow/process-groups/{pg_id}"

        # The payload is simple for the bulk operation
        update_payload = {
            "id": pg_id,
            "state": normalized_state,
            "disconnectedNodeAcknowledged": False
        }

        try:
            logger.info(f"Setting process group {pg_id} state to {normalized_state}")
            response = await client.put(endpoint, json=update_payload)
            response.raise_for_status()
            updated_entity = response.json()
            logger.info(f"Successfully set process group {pg_id} state to {normalized_state}")
            return updated_entity

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to set process group {pg_id} state to {normalized_state}: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to set process group state: {e.response.status_code}, {e.response.text}") from e
        except Exception as e:
            logger.error(f"Error setting process group {pg_id} state to {normalized_state}: {e}")
            raise ConnectionError(f"Error setting process group state: {e}") from e

    async def purge_process_group_flowfiles(self, process_group_id: str, timeout_seconds: int = 30) -> Dict[str, Any]:
        """
        Purges all flowfiles from all connections in a process group.
        
        Args:
            process_group_id: The ID of the process group containing connections to purge
            timeout_seconds: Maximum time in seconds to wait for each purge operation
            
        Returns:
            Dict containing purge results with success status, message, and detailed results
        """
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")
        
        # First, get all connections in the process group
        connections = await self.list_connections(process_group_id)
        
        if not connections:
            return {
                "success": True,
                "message": f"No connections found in process group {process_group_id}",
                "results": []
            }
        
        results = []
        success = True
        
        # Process each connection using the new handle_drop_request method
        for connection in connections:
            connection_id = connection.get("id")
            if not connection_id:
                continue
                
            result = await self.handle_drop_request(connection_id, timeout_seconds)
            results.append(result)
            if not result["success"]:
                success = False
        
        return {
            "success": success,
            "message": f"Purged {sum(1 for r in results if r.get('success', False))} of {len(results)} connections in process group {process_group_id}",
            "results": results
        }

    async def create_drop_request(self, connection_id: str) -> Dict[str, Any]:
        """Creates a new drop request for a connection.
        
        Args:
            connection_id: The ID of the connection to drop FlowFiles from
            
        Returns:
            Dict containing the drop request details with ID directly accessible
        """
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/flowfile-queues/{connection_id}/drop-requests"
        
        try:
            logger.info(f"Creating drop request for connection {connection_id}")
            response = await client.post(endpoint)
            response.raise_for_status()
            drop_request_data = response.json()
            
            # Extract the drop request details from the response
            drop_request = drop_request_data.get("dropRequest", {})
            request_id = drop_request.get("id")
            
            if not request_id:
                raise ValueError("No drop request ID returned from NiFi")
                
            logger.info(f"Successfully created drop request {request_id} for connection {connection_id}")
            
            # Return the drop request with ID directly accessible at the top level
            # and also preserve the original structure
            return {
                "id": request_id,
                "dropRequest": drop_request,
                "original": drop_request_data
            }
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to create drop request for connection {connection_id}: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to create drop request: {e.response.status_code}, {e.response.text}") from e
        except Exception as e:
            logger.error(f"Error creating drop request for connection {connection_id}: {e}")
            raise ConnectionError(f"Error creating drop request: {e}") from e

    async def get_drop_request(self, connection_id: str, request_id: str) -> Dict[str, Any]:
        """Gets the status of a drop request.
        
        Args:
            connection_id: The ID of the connection
            request_id: The ID of the drop request
            
        Returns:
            Dict containing the drop request status
        """
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/flowfile-queues/{connection_id}/drop-requests/{request_id}"
        
        try:
            response = await client.get(endpoint)
            response.raise_for_status()
            status_data = response.json()
            
            return status_data.get("dropRequest", {})
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to get drop request status for {request_id}: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to get drop request status: {e.response.status_code}, {e.response.text}") from e
        except Exception as e:
            logger.error(f"Error getting drop request status for {request_id}: {e}")
            raise ConnectionError(f"Error getting drop request status: {e}") from e

    async def delete_drop_request(self, connection_id: str, request_id: str) -> None:
        """Delete a drop request for a connection to clean up."""
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/flowfile-queues/{connection_id}/drop-requests/{request_id}"
        
        try:
            response = await client.delete(endpoint)
            response.raise_for_status()
            logger.info(f"Successfully deleted drop request {request_id} for connection {connection_id}")
            
        except httpx.HTTPStatusError as e:
            logger.warning(f"Failed to delete drop request {request_id}: {e.response.status_code} - {e.response.text}")
            # Don't raise an error here as this is cleanup
        except Exception as e:
            logger.warning(f"Error deleting drop request {request_id}: {e}")
            # Don't raise an error here as this is cleanup

    async def delete_connections_batch(self, connection_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Deletes multiple connections in a single operation, handling version retrieval.
        
        Args:
            connection_ids: List of connection IDs to delete
            
        Returns:
            Dictionary mapping connection IDs to their deletion results:
            {
                "connection_id": {
                    "success": True/False,
                    "message": "Success/error message",
                    "error": Exception object if failed
                }
            }
        """
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")
        
        # Return dictionary mapping connection ID -> result
        results = {}
        
        # Get the client just once and reuse it for all operations
        client = await self._get_client()
        
        # Process each connection
        for connection_id in connection_ids:
            results[connection_id] = {
                "success": False,
                "message": "",
                "error": None
            }
            
            try:
                # Get current version
                connection_details = None
                try:
                    # Use direct API call rather than self.get_connection to avoid potential client recreation
                    endpoint = f"/connections/{connection_id}"
                    response = await client.get(endpoint)
                    response.raise_for_status()
                    connection_details = response.json()
                    
                    version_number = connection_details.get("revision", {}).get("version")
                    if version_number is None:
                        raise ValueError(f"Could not determine revision version for connection {connection_id}")
                    logger.info(f"Using fetched revision version {version_number} for batch deletion of connection {connection_id}")
                except Exception as e:
                    logger.error(f"Failed to fetch connection {connection_id} details to get version: {e}")
                    results[connection_id]["message"] = f"Failed to get connection details: {e}"
                    results[connection_id]["error"] = e
                    continue

                # Attempt to delete
                endpoint = f"/connections/{connection_id}?version={version_number}&clientId={self._client_id}"
                logger.info(f"Batch deleting connection {connection_id} (version {version_number}) from {self.base_url}{endpoint}")
                response = await client.delete(endpoint)
                response.raise_for_status()
                
                # Record result
                if response.status_code == 200:
                    logger.info(f"Successfully batch deleted connection {connection_id}.")
                    results[connection_id]["success"] = True
                    results[connection_id]["message"] = "Successfully deleted"
                else:
                    logger.warning(f"Connection batch deletion for {connection_id} returned status {response.status_code}, expected 200.")
                    results[connection_id]["message"] = f"Unexpected response status: {response.status_code}"
                
            except httpx.HTTPStatusError as e:
                error_msg = ""
                # Classify common error conditions
                if e.response.status_code == 404:
                    error_msg = f"Connection {connection_id} not found for deletion."
                    logger.warning(error_msg)
                elif e.response.status_code == 409:
                    error_text = e.response.text
                    if "running" in error_text.lower() or "active" in error_text.lower():
                        error_msg = f"Cannot delete connection {connection_id} because upstream component is running"
                        logger.warning(error_msg)
                    elif "has data" in error_text.lower() or "queued" in error_text.lower():
                        error_msg = f"Cannot delete connection {connection_id} because it has queued data"
                        logger.warning(error_msg)
                    else:
                        error_msg = f"Conflict deleting connection {connection_id}. Check revision or state."
                        logger.error(f"{error_msg} Response: {e.response.text}")
                else:
                    error_msg = f"Failed to delete connection: {e.response.status_code}, {e.response.text}"
                    logger.error(f"Failed to delete connection {connection_id}: {e.response.status_code} - {e.response.text}")
                    
                results[connection_id]["message"] = error_msg
                results[connection_id]["error"] = e
                
            except Exception as e:
                error_msg = f"Error deleting connection: {str(e)}"
                logger.error(f"An unexpected error occurred deleting connection {connection_id}: {e}", exc_info=True)
                
                results[connection_id]["message"] = error_msg
                results[connection_id]["error"] = e
        
        # Return overall results
        return results

    async def handle_drop_request(self, connection_id: str, timeout_seconds: int = 30) -> Dict[str, Any]:
        """Handles a drop request for a connection's flowfiles from creation through completion/polling."""
        try:
            # Create the drop request
            drop_request_result = await self.create_drop_request(connection_id)
            request_id = drop_request_result.get("id")
            
            if not request_id:
                raise ValueError("No drop request ID found in response")
            
            # Poll until complete or timeout
            start_time = time.time()
            final_status = None
            
            while True:
                if time.time() - start_time > timeout_seconds:
                    raise TimeoutError(f"Drop request timed out after {timeout_seconds} seconds")

                status = await self.get_drop_request(connection_id, request_id)
                if status.get("finished"):
                    final_status = status
                    break
                await asyncio.sleep(0.5)  # Short delay between checks

            # Always try to cleanup the request
            await self.delete_drop_request(connection_id, request_id)

            return {
                "success": True,
                "connection_id": connection_id,
                "dropped_count": final_status.get("dropped", "0 / 0 bytes")
            }

        except Exception as e:
            logger.error(f"Error during drop request for connection {connection_id}: {e}")
            return {
                "success": False,
                "connection_id": connection_id,
                "error": str(e)
            }

    async def stop_processor(self, processor_id: str) -> dict:
        """Convenience method to stop a processor."""
        return await self.update_processor_state(processor_id, "STOPPED")

    async def start_processor(self, processor_id: str) -> dict:
        """Convenience method to start a processor."""
        return await self.update_processor_state(processor_id, "RUNNING")

    async def stop_process_group(self, pg_id: str) -> dict:
        """Convenience method to stop a process group."""
        return await self.update_process_group_state(pg_id, "STOPPED")

    async def start_process_group(self, pg_id: str) -> dict:
        """Convenience method to start a process group."""
        return await self.update_process_group_state(pg_id, "RUNNING")

    # --- FlowFile Listing Methods (Queue-based) ---
    
    async def create_flowfile_listing_request(self, connection_id: str) -> Dict[str, Any]:
        """Creates a flowfile listing request for a connection queue.
        
        Args:
            connection_id: The ID of the connection to list flowfiles from
            
        Returns:
            Dict containing the listing request details with ID
        """
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/flowfile-queues/{connection_id}/listing-requests"
        
        try:
            logger.info(f"Creating flowfile listing request for connection {connection_id}")
            response = await client.post(endpoint)
            response.raise_for_status()
            listing_request_data = response.json()
            
            # Extract the listing request details from the response
            listing_request = listing_request_data.get("listingRequest", {})
            request_id = listing_request.get("id")
            
            if not request_id:
                raise ValueError("No listing request ID returned from NiFi")
                
            logger.info(f"Successfully created flowfile listing request {request_id} for connection {connection_id}")
            
            # Return the listing request with ID directly accessible
            return {
                "id": request_id,
                "listingRequest": listing_request,
                "original": listing_request_data
            }
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to create flowfile listing request for connection {connection_id}: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to create flowfile listing request: {e.response.status_code}, {e.response.text}") from e
        except Exception as e:
            logger.error(f"Error creating flowfile listing request for connection {connection_id}: {e}")
            raise ConnectionError(f"Error creating flowfile listing request: {e}") from e

    async def get_flowfile_listing_request(self, connection_id: str, request_id: str) -> Dict[str, Any]:
        """Gets the status of a flowfile listing request.
        
        Args:
            connection_id: The ID of the connection
            request_id: The ID of the listing request
            
        Returns:
            Dict containing the listing request status and flowfile summaries when completed
        """
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/flowfile-queues/{connection_id}/listing-requests/{request_id}"
        
        try:
            response = await client.get(endpoint)
            response.raise_for_status()
            status_data = response.json()
            
            return status_data.get("listingRequest", {})
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to get flowfile listing request status for {request_id}: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to get flowfile listing request status: {e.response.status_code}, {e.response.text}") from e
        except Exception as e:
            logger.error(f"Error getting flowfile listing request status for {request_id}: {e}")
            raise ConnectionError(f"Error getting flowfile listing request status: {e}") from e

    async def delete_flowfile_listing_request(self, connection_id: str, request_id: str) -> None:
        """Delete a flowfile listing request to clean up.
        
        Args:
            connection_id: The ID of the connection
            request_id: The ID of the listing request
        """
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/flowfile-queues/{connection_id}/listing-requests/{request_id}"
        
        try:
            response = await client.delete(endpoint)
            response.raise_for_status()
            logger.info(f"Successfully deleted flowfile listing request {request_id} for connection {connection_id}")
            
        except httpx.HTTPStatusError as e:
            logger.warning(f"Failed to delete flowfile listing request {request_id}: {e.response.status_code} - {e.response.text}")
            # Don't raise an error here as this is cleanup
        except Exception as e:
            logger.warning(f"Error deleting flowfile listing request {request_id}: {e}")
            # Don't raise an error here as this is cleanup

    # --- Provenance Query Methods (Processor-based) ---
    
    async def submit_provenance_query(self, query_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Submits a provenance query to search for flowfiles.
        
        Args:
            query_payload: Should contain 'processor_id' and optional 'max_results'
            
        Returns:
            Dict containing the provenance query details with ID
        """
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        # Extract parameters from the query payload
        processor_id = query_payload.get('processor_id')
        max_results = query_payload.get('max_results', 1000)
        
        if not processor_id:
            raise ValueError("processor_id is required for provenance query")

        # Format the request according to NiFi API requirements
        nifi_payload = {
            "provenance": {
                "request": {
                    "maxResults": max_results,
                    "summarize": True,
                    "incrementalResults": False,
                    "searchTerms": {
                        "ProcessorID": {
                            "value": processor_id,
                            "inverse": False
                        }
                    }
                }
            }
        }

        client = await self._get_client()
        endpoint = "/provenance"
        
        try:
            logger.info(f"Submitting provenance query for processor {processor_id}")
            response = await client.post(endpoint, json=nifi_payload)
            response.raise_for_status()
            query_response_data = response.json()
            
            # Extract the query details from the response
            query = query_response_data.get("provenance", {})
            query_id = query.get("id")
            
            if not query_id:
                raise ValueError("No provenance query ID returned from NiFi")
                
            logger.info(f"Successfully submitted provenance query {query_id}")
            
            # Return the query with ID directly accessible
            return {
                "id": query_id,
                "provenance": query,
                "original": query_response_data
            }
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to submit provenance query: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to submit provenance query: {e.response.status_code}, {e.response.text}") from e
        except Exception as e:
            logger.error(f"Error submitting provenance query: {e}")
            raise ConnectionError(f"Error submitting provenance query: {e}") from e

    async def get_provenance_query(self, query_id: str) -> Dict[str, Any]:
        """Gets the status of a provenance query.
        
        Args:
            query_id: The ID of the provenance query
            
        Returns:
            Dict containing the query status
        """
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/provenance/{query_id}"
        
        try:
            response = await client.get(endpoint)
            response.raise_for_status()
            status_data = response.json()
            
            return status_data.get("provenance", {})
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to get provenance query status for {query_id}: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to get provenance query status: {e.response.status_code}, {e.response.text}") from e
        except Exception as e:
            logger.error(f"Error getting provenance query status for {query_id}: {e}")
            raise ConnectionError(f"Error getting provenance query status: {e}") from e

    async def get_provenance_results(self, query_id: str) -> List[Dict[str, Any]]:
        """Gets the results of a completed provenance query.
        
        Args:
            query_id: The ID of the provenance query
            
        Returns:
            List of provenance event dictionaries
        """
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/provenance/{query_id}/results"
        
        try:
            response = await client.get(endpoint)
            response.raise_for_status()
            results_data = response.json()
            
            # Extract the events from the response
            results = results_data.get("provenanceResults", {})
            events = results.get("provenanceEvents", [])
            
            logger.info(f"Retrieved {len(events)} provenance events for query {query_id}")
            return events
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to get provenance query results for {query_id}: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to get provenance query results: {e.response.status_code}, {e.response.text}") from e
        except Exception as e:
            logger.error(f"Error getting provenance query results for {query_id}: {e}")
            raise ConnectionError(f"Error getting provenance query results: {e}") from e

    async def delete_provenance_query(self, query_id: str) -> None:
        """Delete a provenance query to clean up.
        
        Args:
            query_id: The ID of the provenance query
        """
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/provenance/{query_id}"
        
        try:
            response = await client.delete(endpoint)
            response.raise_for_status()
            logger.info(f"Successfully deleted provenance query {query_id}")
            
        except httpx.HTTPStatusError as e:
            logger.warning(f"Failed to delete provenance query {query_id}: {e.response.status_code} - {e.response.text}")
            # Don't raise an error here as this is cleanup
        except Exception as e:
            logger.warning(f"Error deleting provenance query {query_id}: {e}")
            # Don't raise an error here as this is cleanup

    # --- Provenance Event Content Methods ---
    
    async def get_provenance_event(self, event_id: int) -> Dict[str, Any]:
        """Gets details for a specific provenance event.
        
        Args:
            event_id: The ID of the provenance event
            
        Returns:
            Dict containing the event details
        """
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/provenance-events/{event_id}"
        
        try:
            response = await client.get(endpoint)
            response.raise_for_status()
            event_data = response.json()
            
            return event_data.get("provenanceEvent", {})
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to get provenance event {event_id}: {e.response.status_code} - {e.response.text}")
            if e.response.status_code == 404:
                raise ValueError(f"Provenance event {event_id} not found") from e
            raise ConnectionError(f"Failed to get provenance event: {e.response.status_code}, {e.response.text}") from e
        except Exception as e:
            logger.error(f"Error getting provenance event {event_id}: {e}")
            raise ConnectionError(f"Error getting provenance event: {e}") from e

    async def get_provenance_event_content(self, event_id: int, direction: Literal["input", "output"]) -> httpx.Response:
        """Gets content for a specific provenance event.
        
        Args:
            event_id: The ID of the provenance event
            direction: Whether to get 'input' or 'output' content
            
        Returns:
            httpx.Response object for streaming content
        """
        if not self._token:
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/provenance-events/{event_id}/content/{direction}"
        
        try:
            response = await client.get(endpoint)
            response.raise_for_status()
            
            # Return the response for streaming - caller should handle aclose()
            return response
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to get {direction} content for provenance event {event_id}: {e.response.status_code} - {e.response.text}")
            if e.response.status_code == 404:
                raise ValueError(f"Content not available for provenance event {event_id} ({direction})") from e
            raise ConnectionError(f"Failed to get provenance event content: {e.response.status_code}, {e.response.text}") from e
        except Exception as e:
            logger.error(f"Error getting {direction} content for provenance event {event_id}: {e}")
            raise ConnectionError(f"Error getting provenance event content: {e}") from e

    # ==========================================
    # Controller Service Methods
    # ==========================================

    async def list_controller_services(self, process_group_id: str, user_request_id: str = "-", action_id: str = "-") -> List[Dict]:
        """Lists controller services within a specified process group."""
        local_logger = logger.bind(user_request_id=user_request_id, action_id=action_id)
        
        if not self._token:
            local_logger.error("Authentication required before listing controller services.")
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/flow/process-groups/{process_group_id}/controller-services"
        try:
            local_logger.info(f"Fetching controller services for group {process_group_id} from {self.base_url}{endpoint}")
            response = await client.get(endpoint)
            response.raise_for_status()
            data = response.json()
            # The response is typically a ControllerServicesEntity which has a 'controllerServices' key containing a list
            controller_services = data.get("controllerServices", [])
            local_logger.info(f"Found {len(controller_services)} controller services in group {process_group_id}.")
            return controller_services

        except httpx.HTTPStatusError as e:
            local_logger.error(f"Failed to list controller services for group {process_group_id}: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to list controller services: {e.response.status_code}") from e
        except (httpx.RequestError, ValueError) as e:
            local_logger.error(f"Error listing controller services for group {process_group_id}: {e}")
            raise ConnectionError(f"Error listing controller services: {e}") from e
        except Exception as e:
            local_logger.error(f"An unexpected error occurred listing controller services: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred listing controller services: {e}") from e

    async def get_controller_service_details(self, controller_service_id: str, user_request_id: str = "-", action_id: str = "-") -> Dict:
        """Gets detailed information about a specific controller service."""
        local_logger = logger.bind(user_request_id=user_request_id, action_id=action_id)
        
        if not self._token:
            local_logger.error("Authentication required before getting controller service details.")
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/controller-services/{controller_service_id}"
        try:
            local_logger.info(f"Fetching controller service details for {controller_service_id} from {self.base_url}{endpoint}")
            response = await client.get(endpoint)
            response.raise_for_status()
            controller_service_entity = response.json()
            local_logger.info(f"Successfully retrieved controller service details for {controller_service_id}.")
            return controller_service_entity

        except httpx.HTTPStatusError as e:
            local_logger.error(f"Failed to get controller service details for {controller_service_id}: {e.response.status_code} - {e.response.text}")
            if e.response.status_code == 404:
                raise ValueError(f"Controller service {controller_service_id} not found") from e
            raise ConnectionError(f"Failed to get controller service details: {e.response.status_code}") from e
        except (httpx.RequestError, ValueError) as e:
            local_logger.error(f"Error getting controller service details for {controller_service_id}: {e}")
            raise ConnectionError(f"Error getting controller service details: {e}") from e
        except Exception as e:
            local_logger.error(f"An unexpected error occurred getting controller service details: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred getting controller service details: {e}") from e

    async def create_controller_service(
        self,
        process_group_id: str,
        service_type: str,
        name: str,
        properties: Optional[Dict[str, Any]] = None,
        user_request_id: str = "-",
        action_id: str = "-"
    ) -> Dict:
        """Creates a new controller service in the specified process group."""
        local_logger = logger.bind(user_request_id=user_request_id, action_id=action_id)
        
        if not self._token:
            local_logger.error("Authentication required before creating controller service.")
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/process-groups/{process_group_id}/controller-services"

        # Construct the request body (ControllerServiceEntity)
        request_body = {
            "revision": {
                "clientId": self._client_id,
                "version": 0
            },
            "disconnectedNodeAcknowledged": False,
            "component": {
                "type": service_type,
                "name": name
            }
        }

        # Add properties if provided
        if properties:
            request_body["component"]["properties"] = properties

        try:
            local_logger.info(f"Creating controller service '{name}' ({service_type}) in group {process_group_id}")
            response = await client.post(endpoint, json=request_body)
            response.raise_for_status()
            controller_service_entity = response.json()
            
            # Extract the controller service ID for logging
            created_id = controller_service_entity.get("component", {}).get("id", "unknown")
            local_logger.info(f"Successfully created controller service '{name}' with ID {created_id}")
            return controller_service_entity

        except httpx.HTTPStatusError as e:
            local_logger.error(f"Failed to create controller service '{name}': {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to create controller service: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            local_logger.error(f"Error creating controller service '{name}': {e}")
            raise ConnectionError(f"Error creating controller service: {e}") from e
        except Exception as e:
            local_logger.error(f"An unexpected error occurred creating controller service '{name}': {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred creating controller service: {e}") from e

    async def update_controller_service_properties(
        self,
        controller_service_id: str,
        properties: Dict[str, Any],
        user_request_id: str = "-",
        action_id: str = "-"
    ) -> Dict:
        """Updates controller service properties."""
        local_logger = logger.bind(user_request_id=user_request_id, action_id=action_id)
        
        if not self._token:
            local_logger.error("Authentication required before updating controller service properties.")
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        # First, get current controller service to get revision info
        current_entity = await self.get_controller_service_details(controller_service_id, user_request_id, action_id)
        
        client = await self._get_client()
        endpoint = f"/controller-services/{controller_service_id}"

        # Construct the update payload
        request_body = {
            "revision": current_entity.get("revision", {}),
            "disconnectedNodeAcknowledged": False,
            "component": {
                "id": controller_service_id,
                "properties": properties
            }
        }

        try:
            local_logger.info(f"Updating properties for controller service {controller_service_id}")
            response = await client.put(endpoint, json=request_body)
            response.raise_for_status()
            updated_entity = response.json()
            local_logger.info(f"Successfully updated properties for controller service {controller_service_id}")
            return updated_entity

        except httpx.HTTPStatusError as e:
            local_logger.error(f"Failed to update controller service properties for {controller_service_id}: {e.response.status_code} - {e.response.text}")
            if e.response.status_code == 404:
                raise ValueError(f"Controller service {controller_service_id} not found") from e
            elif e.response.status_code == 409:
                raise ValueError(f"Conflict updating controller service {controller_service_id}. Revision mismatch: {e.response.text}") from e
            raise ConnectionError(f"Failed to update controller service properties: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            local_logger.error(f"Error updating controller service properties for {controller_service_id}: {e}")
            raise ConnectionError(f"Error updating controller service properties: {e}") from e
        except Exception as e:
            local_logger.error(f"An unexpected error occurred updating controller service properties: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred updating controller service properties: {e}") from e

    async def delete_controller_service(self, controller_service_id: str, version: int, user_request_id: str = "-", action_id: str = "-") -> bool:
        """Deletes a controller service."""
        local_logger = logger.bind(user_request_id=user_request_id, action_id=action_id)
        
        if not self._token:
            local_logger.error("Authentication required before deleting controller service.")
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = f"/controller-services/{controller_service_id}"
        params = {
            "version": version,
            "clientId": self._client_id,
            "disconnectedNodeAcknowledged": "false"
        }

        try:
            local_logger.info(f"Deleting controller service {controller_service_id} with version {version}")
            response = await client.delete(endpoint, params=params)
            response.raise_for_status()
            local_logger.info(f"Successfully deleted controller service {controller_service_id}")
            return True

        except httpx.HTTPStatusError as e:
            local_logger.error(f"Failed to delete controller service {controller_service_id}: {e.response.status_code} - {e.response.text}")
            if e.response.status_code == 404:
                local_logger.warning(f"Controller service {controller_service_id} not found, considering it already deleted")
                return True  # Consider it successful if already gone
            elif e.response.status_code == 409:
                raise ValueError(f"Conflict deleting controller service {controller_service_id}. Revision mismatch or service in use: {e.response.text}") from e
            raise ConnectionError(f"Failed to delete controller service: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            local_logger.error(f"Error deleting controller service {controller_service_id}: {e}")
            raise ConnectionError(f"Error deleting controller service: {e}") from e
        except Exception as e:
            local_logger.error(f"An unexpected error occurred deleting controller service: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred deleting controller service: {e}") from e

    async def enable_controller_service(self, controller_service_id: str, user_request_id: str = "-", action_id: str = "-") -> Dict:
        """Enables a controller service."""
        local_logger = logger.bind(user_request_id=user_request_id, action_id=action_id)
        
        if not self._token:
            local_logger.error("Authentication required before enabling controller service.")
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        # First, get current controller service to get revision info
        current_entity = await self.get_controller_service_details(controller_service_id, user_request_id, action_id)
        
        client = await self._get_client()
        endpoint = f"/controller-services/{controller_service_id}/run-status"

        # Construct the request body to enable the service
        request_body = {
            "revision": current_entity.get("revision", {}),
            "disconnectedNodeAcknowledged": False,
            "state": "ENABLED"
        }

        try:
            local_logger.info(f"Enabling controller service {controller_service_id}")
            response = await client.put(endpoint, json=request_body)
            response.raise_for_status()
            updated_entity = response.json()
            local_logger.info(f"Successfully enabled controller service {controller_service_id}")
            return updated_entity

        except httpx.HTTPStatusError as e:
            local_logger.error(f"Failed to enable controller service {controller_service_id}: {e.response.status_code} - {e.response.text}")
            if e.response.status_code == 404:
                raise ValueError(f"Controller service {controller_service_id} not found") from e
            elif e.response.status_code == 409:
                raise ValueError(f"Conflict enabling controller service {controller_service_id}: {e.response.text}") from e
            raise ConnectionError(f"Failed to enable controller service: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            local_logger.error(f"Error enabling controller service {controller_service_id}: {e}")
            raise ConnectionError(f"Error enabling controller service: {e}") from e
        except Exception as e:
            local_logger.error(f"An unexpected error occurred enabling controller service: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred enabling controller service: {e}") from e

    async def disable_controller_service(self, controller_service_id: str, user_request_id: str = "-", action_id: str = "-") -> Dict:
        """Disables a controller service."""
        local_logger = logger.bind(user_request_id=user_request_id, action_id=action_id)
        
        if not self._token:
            local_logger.error("Authentication required before disabling controller service.")
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        # First, get current controller service to get revision info
        current_entity = await self.get_controller_service_details(controller_service_id, user_request_id, action_id)
        
        client = await self._get_client()
        endpoint = f"/controller-services/{controller_service_id}/run-status"

        # Construct the request body to disable the service
        request_body = {
            "revision": current_entity.get("revision", {}),
            "disconnectedNodeAcknowledged": False,
            "state": "DISABLED"
        }

        try:
            local_logger.info(f"Disabling controller service {controller_service_id}")
            response = await client.put(endpoint, json=request_body)
            response.raise_for_status()
            updated_entity = response.json()
            local_logger.info(f"Successfully disabled controller service {controller_service_id}")
            return updated_entity

        except httpx.HTTPStatusError as e:
            local_logger.error(f"Failed to disable controller service {controller_service_id}: {e.response.status_code} - {e.response.text}")
            if e.response.status_code == 404:
                raise ValueError(f"Controller service {controller_service_id} not found") from e
            elif e.response.status_code == 409:
                raise ValueError(f"Conflict disabling controller service {controller_service_id}: {e.response.text}") from e
            raise ConnectionError(f"Failed to disable controller service: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            local_logger.error(f"Error disabling controller service {controller_service_id}: {e}")
            raise ConnectionError(f"Error disabling controller service: {e}") from e
        except Exception as e:
            local_logger.error(f"An unexpected error occurred disabling controller service: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred disabling controller service: {e}") from e

    async def get_controller_service_types(self, user_request_id: str = "-", action_id: str = "-") -> List[Dict]:
        """Fetches the list of available controller service types from the NiFi instance."""
        local_logger = logger.bind(user_request_id=user_request_id, action_id=action_id)
        
        if not self._token:
            local_logger.error("Authentication required before getting controller service types.")
            raise NiFiAuthenticationError("Client is not authenticated. Call authenticate() first.")

        client = await self._get_client()
        endpoint = "/flow/controller-service-types"

        try:
            local_logger.info(f"Fetching available controller service types from {self.base_url}{endpoint}")
            response = await client.get(endpoint)
            response.raise_for_status()
            data = response.json()
            # The response is ControllerServiceTypesEntity, containing 'controllerServiceTypes' list
            controller_service_types = data.get("controllerServiceTypes", [])
            local_logger.info(f"Successfully fetched {len(controller_service_types)} available controller service types.")
            return controller_service_types

        except httpx.HTTPStatusError as e:
            local_logger.error(f"Failed to get controller service types: {e.response.status_code} - {e.response.text}")
            raise ConnectionError(f"Failed to get controller service types: {e.response.status_code}, {e.response.text}") from e
        except (httpx.RequestError, ValueError) as e:
            local_logger.error(f"Error getting controller service types: {e}")
            raise ConnectionError(f"Error getting controller service types: {e}") from e
        except Exception as e:
            local_logger.error(f"An unexpected error occurred getting controller service types: {e}", exc_info=True)
            raise ConnectionError(f"An unexpected error occurred getting controller service types: {e}") from e
