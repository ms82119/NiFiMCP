"""Offline unit tests for clear-token-on-401 handling in NiFiClient.

These are fully mocked: no NiFi instance, no network, no token store access.
They cover the regression where an expired OIDC token stayed cached in
``NiFiClient._token`` forever, so a freshly-written ``nifi_tokens.json`` was
ignored until the MCP server process was restarted.
"""

import asyncio

import httpx
import pytest

from nifi_mcp_server.nifi_client import NiFiClient, NiFiAuthenticationError


class _StubHttpClient:
    """Stands in for the httpx.AsyncClient returned by NiFiClient._get_client."""

    def __init__(self, response: httpx.Response):
        self._response = response
        self.closed = False

    async def get(self, endpoint, **kwargs):
        raise httpx.HTTPStatusError(
            "error", request=self._response.request, response=self._response
        )

    async def aclose(self):
        self.closed = True


def _make_client(status_code: int, body: str) -> tuple[NiFiClient, _StubHttpClient]:
    request = httpx.Request("GET", "https://nifi.example/nifi-api/flow/process-groups/root")
    response = httpx.Response(status_code, text=body, request=request)
    stub = _StubHttpClient(response)

    client = NiFiClient(base_url="https://nifi.example/nifi-api", server_id="test-server")
    client._token = "cached.jwt.token"
    client._token_from_oidc = True
    client._client = stub

    async def _get_client():
        return stub

    client._get_client = _get_client
    return client, stub


def test_401_session_expired_clears_token_and_raises_auth_error():
    client, stub = _make_client(401, "Session Expired")

    with pytest.raises(NiFiAuthenticationError) as excinfo:
        asyncio.run(client.get_root_process_group_id())

    assert "Authentication token has expired" in str(excinfo.value)
    assert client._token is None, "cached token must be dropped so the store is re-read"
    assert client._client is None
    assert stub.closed is True


def test_401_unauthorized_body_also_clears_token():
    client, _ = _make_client(401, "Unauthorized")

    with pytest.raises(NiFiAuthenticationError):
        asyncio.run(client.get_root_process_group_id())

    assert client._token is None


def test_403_does_not_clear_token():
    """403 is a permissions problem, not an expiry - behaviour must be unchanged."""
    client, _ = _make_client(403, "Untrusted proxy / unauthorized")

    with pytest.raises(ConnectionError):
        asyncio.run(client.get_root_process_group_id())

    assert client._token == "cached.jwt.token"


def test_non_auth_status_error_keeps_existing_connection_error():
    client, _ = _make_client(500, "Internal Server Error")

    with pytest.raises(ConnectionError) as excinfo:
        asyncio.run(client.get_root_process_group_id())

    assert "Failed to get root process group ID: 500" in str(excinfo.value)
    assert client._token == "cached.jwt.token"


def test_401_without_expiry_wording_is_left_alone():
    """Conservative match: only expiry/unauthorized-looking 401 bodies clear the token."""
    client, _ = _make_client(401, "no body")

    with pytest.raises(ConnectionError):
        asyncio.run(client.get_root_process_group_id())

    assert client._token == "cached.jwt.token"


def test_cleared_token_makes_next_call_reauthenticate():
    """After a 401 clear, the next call must go back through _ensure_authenticated."""
    client, _ = _make_client(401, "Session Expired")

    with pytest.raises(NiFiAuthenticationError):
        asyncio.run(client.get_root_process_group_id())

    calls = []

    async def _fake_authenticate(server_id=None):
        calls.append(server_id)
        client._token = "fresh.jwt.token"

    client.authenticate = _fake_authenticate
    asyncio.run(client._ensure_authenticated())

    assert calls == ["test-server"]
    assert client._token == "fresh.jwt.token"
