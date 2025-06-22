import pytest
import httpx
from typing import AsyncGenerator

# Unit test configuration - no server connectivity required
# This prevents integration test fixtures from causing warnings

# Override the fixtures from parent conftest.py to prevent warnings
@pytest.fixture(scope="module")
def async_client():
    """Mock async client for unit tests (not used but prevents warnings)."""
    return None

@pytest.fixture(scope="module", autouse=True)  # Override the parent fixture completely
def check_server_connectivity():
    """Mock server connectivity check for unit tests (not used but prevents warnings)."""
    # Do nothing - unit tests don't need server connectivity
    return True

@pytest.fixture(scope="session")
def global_logger():
    """Mock logger for unit tests."""
    import logging
    return logging.getLogger(__name__)

@pytest.fixture(scope="session") 
def base_url():
    """Mock base URL for unit tests."""
    return "http://localhost:8000"

@pytest.fixture(scope="session")
def target_nifi_server_id():
    """Mock NiFi server ID for unit tests."""
    return "test-server"

@pytest.fixture(scope="session")
def nifi_test_server_id():
    """Mock NiFi test server ID for unit tests."""
    return "test-server"

@pytest.fixture
def anyio_backend():
    """Configure anyio to only use asyncio backend for unit tests."""
    return 'asyncio' 