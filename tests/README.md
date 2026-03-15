# Testing Guide

This directory contains tests for the NiFi MCP Server project.

## Test Structure

- `core_operations/` - Core functionality tests
- `auto_features/` - Auto-feature tests (auto-stop, auto-delete, etc.)
- `unit/` - Unit tests
- `utils/` - Test utilities and helpers

## Running Tests

```bash
# Run all tests
python -m pytest

# Run specific test file
python -m pytest tests/core_operations/test_create_nifi_flow_from_definition.py

# Run tests with verbose output
python -m pytest -v

# Run tests with logging output
python -m pytest -s
```

## Test Process Group Cleanup

The test suite creates temporary NiFi process groups for testing. These should be automatically cleaned up after tests complete.

### Automatic Cleanup

1. **Fixture-based cleanup** - Each test fixture properly cleans up its process groups
2. **Auto-cleanup hook** - Runs after all tests to clean up any remaining test process groups
3. **Enhanced error logging** - Critical cleanup failures are logged for manual intervention

### Manual Cleanup

If you see leftover test process groups in NiFi (typically named like `mcp-test-pg-nifi-local-example`), you can clean them up manually:

```bash
# Run the cleanup script
python tests/cleanup_test_pgs.py

# Or make it executable and run directly
chmod +x tests/cleanup_test_pgs.py
./tests/cleanup_test_pgs.py
```

### Test Process Group Naming Patterns

The test suite looks for these patterns when cleaning up:
- `mcp-test-pg-nifi-local-example` - Main test process groups
- `mcp-test-doc-flow-nifi-local-example` - Documentation test flows
- `mcp-test-merge-flow-nifi-local-example` - Merge flow test cases
- `CompleteFlowTestPG` - Complete flow creation tests

### Common Cleanup Issues

If cleanup fails, it's usually due to:
1. **Test interruption** - Tests stopped with Ctrl+C before cleanup could run
2. **NiFi state issues** - Process groups in an inconsistent state
3. **Network timeouts** - Communication issues with NiFi
4. **Permission issues** - NiFi API access problems

### Troubleshooting

1. Check test logs for `CLEANUP FAILURE` messages
2. Verify NiFi is running and accessible
3. Check NiFi UI for process group state (running/stopped)
4. Run manual cleanup script if needed
5. Restart NiFi if process groups are stuck in inconsistent state

## Configuration

Tests use the following configuration:
- **NiFi Server**: `nifi-local-example` (http://localhost:8080)
- **MCP Server**: Set `MCP_SERVER_URL` (full URL) or `MCP_SERVER_PORT` (e.g. `8001`); default port is 8000. Example: `MCP_SERVER_PORT=8001 pytest tests/...` or `MCP_SERVER_URL=http://localhost:8001 pytest tests/...`.
- **Test naming**: Uses server ID to avoid conflicts

## Best Practices

1. Always use the provided test fixtures (`test_pg`, `test_pg_with_processors`, etc.)
2. Don't create process groups manually in tests without proper cleanup
3. Run cleanup script after test development sessions
4. Check NiFi UI periodically for leftover test components

## Directory Structure

```
tests/
├── README.md                     # This file - test organization guide
├── conftest.py                   # Main conftest with async fixtures for integration tests
├── unit/                         # Unit tests (no external dependencies)
│   ├── conftest.py              # Unit test fixtures (overrides parent to prevent warnings)
│   ├── test_debugging_efficiency.py  # Debugging efficiency logic tests (25 tests)
│   └── test_smart_pruning_logic.py   # Smart pruning algorithm tests (1 test)
├── core_operations/              # Integration tests (require NiFi server)
│   ├── test_nifi_processor_operations.py
│   ├── test_create_nifi_flow_from_definition.py
│   ├── test_nifi_controller_service_operations.py
│   └── ... (other integration tests)
├── auto_features/               # Tests for auto features (stop, delete, purge)
└── utils/                       # Utility and helper tests
```

## Test Placement Guidelines

### When to use `tests/unit/`

✅ **Place tests here if they:**
- **No External Dependencies**: Don't require a running NiFi server, database, or external services
- **Pure Logic Testing**: Test algorithms, data structures, validation logic, or business rules
- **Mock/Stub Heavy**: Use mocks, stubs, or simulated data instead of real API calls
- **Fast Execution**: Run in milliseconds, suitable for frequent execution during development
- **Isolated**: Can run independently without setup/teardown of external resources

**Examples:**
```python
# Unit test indicators:
from unittest.mock import Mock, patch
import pytest

def test_validation_logic():
    # Tests pure validation functions
    
def test_algorithm_behavior():
    # Tests smart algorithms with simulated data
    
def test_data_transformation():
    # Tests data parsing/transformation logic
```

### When to use `tests/core_operations/`

✅ **Place tests here if they:**
- **External Dependencies**: Require a running NiFi server or other external services
- **API Integration**: Make actual HTTP calls to test real API endpoints
- **End-to-End Workflows**: Test complete workflows involving multiple system components
- **Resource Management**: Test creation, modification, or deletion of actual NiFi objects
- **Network/Timing Sensitive**: Test timeouts, network failures, or async operations

**Examples:**
```python
# Integration test indicators:
import httpx
from tests.utils.nifi_test_utils import call_tool

async def test_create_processor_in_nifi(async_client, test_pg):
    # Tests actual processor creation on NiFi server
    
async def test_connection_operations(base_url, mcp_headers):
    # Tests real connection operations
```

## Code Generator Guidelines

When automatically generating tests, use these patterns to determine placement:

### Indicators for `tests/unit/`:
- Uses `unittest.mock`, `patch`, `Mock`
- No `async` fixtures or HTTP clients
- Tests pure functions with deterministic inputs/outputs
- Contains phrases like "validation", "parsing", "logic", "algorithm"
- Function names like `test_*_validation`, `test_*_logic`, `test_*_algorithm`

### Indicators for `tests/core_operations/`:
- Uses `async_client`, `httpx`, real HTTP calls
- Has setup/teardown of NiFi objects (uses fixtures like `test_pg`)
- Tests with actual server IDs or endpoints
- Contains phrases like "create_processor", "nifi_", "server", "endpoint"
- Function names like `test_*_operations`, `test_create_*`, `test_nifi_*`

## Test Environment Setup

### For Unit Tests:
- No special setup required
- Use the isolated `tests/unit/conftest.py` fixtures
- Run without server connectivity

### For Integration Tests:
- Requires running NiFi MCP Server
- Set `NIFI_TEST_SERVER_ID` environment variable
- Uses main `tests/conftest.py` fixtures with server connectivity checks

## Test Statistics

- **Total Tests**: ~170
- **Unit Tests**: 26 (in `tests/unit/`)
- **Integration Tests**: ~144 (in `tests/core_operations/` and other dirs)
- **Unit Test Execution**: ~0.4 seconds
- **Integration Test Execution**: ~7+ seconds (varies by server response)

## Fixture Inheritance

### Unit Tests (`tests/unit/`)
- Use simplified fixtures from `tests/unit/conftest.py`
- Override parent fixtures to prevent async warnings
- No server connectivity checks
- Mock implementations for required dependencies

### Integration Tests (all other directories)
- Use full fixtures from main `tests/conftest.py`
- Include server connectivity checks
- Real HTTP clients and NiFi server interactions
- Automatic test resource cleanup

## Adding New Tests

When adding new tests, consider:

1. **What am I testing?** - Pure logic or system integration?
2. **What does it depend on?** - Local code only or external services?
3. **How fast should it be?** - Milliseconds (unit) or seconds (integration)?
4. **Can it run in isolation?** - No external setup or requires NiFi server?

Use the decision tree above to place your test in the correct directory. 