"""Unit tests for mTLS client-certificate authentication in NiFiClient.

Verifies the cert-mode behaviour: when both client_cert and client_key are
configured, identity comes from the TLS client certificate, so no token/login
is required. All offline — no NiFi connectivity needed.
"""

import pytest
from unittest.mock import AsyncMock, patch

from nifi_mcp_server.nifi_client import NiFiClient

BASE_URL = "https://nifi.example.com/nifi-api"


def _make_client(**kwargs):
    return NiFiClient(base_url=BASE_URL, **kwargs)


def test_cert_mode_enabled_only_when_both_cert_and_key_present():
    assert _make_client(client_cert="c.crt", client_key="c.key")._cert_mode is True
    assert _make_client(client_cert="c.crt")._cert_mode is False
    assert _make_client(client_key="c.key")._cert_mode is False
    assert _make_client()._cert_mode is False


def test_is_authenticated_true_in_cert_mode_without_token():
    client = _make_client(client_cert="c.crt", client_key="c.key")
    assert client._token is None
    assert client.is_authenticated is True


def test_is_authenticated_false_without_cert_or_token():
    assert _make_client().is_authenticated is False


@pytest.mark.anyio
async def test_ensure_authenticated_skips_network_in_cert_mode():
    """Cert mode must not trigger a login/network call."""
    client = _make_client(client_cert="c.crt", client_key="c.key")
    client.authenticate = AsyncMock()
    await client._ensure_authenticated()
    client.authenticate.assert_not_called()


@pytest.mark.anyio
async def test_ensure_authenticated_authenticates_when_not_cert_mode():
    client = _make_client()
    client.authenticate = AsyncMock()
    await client._ensure_authenticated()
    client.authenticate.assert_awaited_once()


@pytest.mark.anyio
async def test_get_client_uses_client_cert_and_sends_no_auth_header_in_cert_mode():
    client = _make_client(client_cert="c.crt", client_key="c.key", tls_verify=False)
    with patch("nifi_mcp_server.nifi_client.httpx.AsyncClient") as mock_async_client:
        await client._get_client()
    kwargs = mock_async_client.call_args.kwargs
    assert kwargs["cert"] == ("c.crt", "c.key")
    assert kwargs["verify"] is False
    assert "Authorization" not in kwargs["headers"]


@pytest.mark.anyio
async def test_get_client_sets_host_header_when_configured():
    client = _make_client(
        client_cert="c.crt", client_key="c.key", host_header="nifi.internal.example.com"
    )
    with patch("nifi_mcp_server.nifi_client.httpx.AsyncClient") as mock_async_client:
        await client._get_client()
    kwargs = mock_async_client.call_args.kwargs
    assert kwargs["headers"].get("Host") == "nifi.internal.example.com"


@pytest.mark.anyio
async def test_get_client_omits_cert_and_uses_bearer_when_not_cert_mode():
    client = _make_client()
    client._token = "tok"
    with patch("nifi_mcp_server.nifi_client.httpx.AsyncClient") as mock_async_client:
        await client._get_client()
    kwargs = mock_async_client.call_args.kwargs
    assert "cert" not in kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer tok"


@pytest.mark.anyio
async def test_authenticate_uses_stored_token_when_no_userpass():
    """With no username/password, a stored token short-circuits before auth-config."""
    client = _make_client()  # no username/password
    with patch("nifi_mcp_server.token_store.get_token", return_value="a.b.c"), \
         patch.object(client, "get_authentication_config", new=AsyncMock()) as get_config:
        await client.authenticate(server_id="srv")
    assert client._token == "a.b.c"
    assert client._token_from_oidc is True
    get_config.assert_not_awaited()  # never reached the auth-config path


@pytest.mark.anyio
async def test_authenticate_does_not_override_configured_userpass():
    """An explicit username/password keeps precedence: the stored-token short-circuit
    is skipped and we fall through to the normal auth-config path."""
    client = _make_client(username="u", password="p")
    sentinel = RuntimeError("fell through to auth-config")
    with patch("nifi_mcp_server.token_store.get_token", return_value="a.b.c"), \
         patch.object(client, "get_authentication_config", new=AsyncMock(side_effect=sentinel)):
        with pytest.raises(RuntimeError, match="fell through"):
            await client.authenticate(server_id="srv")
    assert client._token is None  # stored token was NOT applied
