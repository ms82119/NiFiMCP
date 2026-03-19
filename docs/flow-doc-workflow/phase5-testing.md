# Phase 5: Testing Strategy

## Overview

**Goal**: Build testing infrastructure incrementally alongside implementation, avoiding heavy mock maintenance while ensuring quality.

**Philosophy**: 
- Tests should run **without LLM costs** during development
- Build tests **alongside each phase**, not after
- Focus on **deterministic, repeatable** tests
- Accept that **LLM output quality** requires human review

**Duration Estimate**: 2 days (spread across phases)

---

## 1. Current Testing Infrastructure

### Existing Structure

```
tests/
├── conftest.py              # Main fixtures (NiFi integration)
├── unit/                    # Fast, no external deps
│   ├── conftest.py          # Isolated fixtures
│   ├── test_smart_pruning_logic.py
│   └── test_debugging_efficiency.py
├── core_operations/         # NiFi integration tests
│   ├── test_nifi_processor_operations.py
│   ├── test_nifi_flow_documentation_improved.py
│   └── ... (27+ test files)
├── workflows/               # Workflow tests (limited)
│   ├── test_workflow_registry.py
│   └── test_workflow_nodes.py
└── utils/
    └── nifi_test_utils.py   # call_tool helper
```

### Current Strengths

| Aspect | Status |
|--------|--------|
| API tool testing | ✅ Good coverage |
| Fixture cleanup | ✅ Robust (stop → purge → delete) |
| Test utilities | ✅ `call_tool` helper |
| Unit test isolation | ✅ Separate conftest |
| Workflow registry tests | ✅ Basic coverage |

### Current Gaps

| Gap | Impact | Priority |
|-----|--------|----------|
| No workflow execution tests | Can't test multi-node flows | High |
| No LLM mocking/caching | Tests incur LLM costs | High |
| No JSON file fixtures | Tests need live NiFi | Medium |
| NiFi V2 token auth | May break existing tests | Medium |

---

## 2. Testing Strategy by Phase

### Phase 1: Infrastructure (No tests needed)

**Reason**: Event types, logging changes, config additions are low-risk.

**Validation**: Manual verification during Phase 3/4 testing.

---

### Phase 2: Tool Enhancements

**New Tests to Add**:

```
tests/unit/
└── test_processor_categorization.py    # NEW
```

```python
# tests/unit/test_processor_categorization.py
"""Unit tests for processor categorization logic."""

import pytest
from nifi_mcp_server.processor_categories import (
    ProcessorCategory,
    ProcessorCategorizer,
    PROCESSOR_CATEGORY_PATTERNS
)

class TestProcessorCategorization:
    """Test heuristic categorization of processors."""
    
    def setup_method(self):
        self.categorizer = ProcessorCategorizer()
    
    @pytest.mark.parametrize("processor_type,expected_category", [
        # IO_READ
        ("org.apache.nifi.processors.standard.GetFile", ProcessorCategory.IO_READ),
        ("org.apache.nifi.processors.kafka.pubsub.ConsumeKafka", ProcessorCategory.IO_READ),
        ("org.apache.nifi.processors.standard.GetSFTP", ProcessorCategory.IO_READ),
        
        # IO_WRITE
        ("org.apache.nifi.processors.standard.PutFile", ProcessorCategory.IO_WRITE),
        ("org.apache.nifi.processors.kafka.pubsub.PublishKafka", ProcessorCategory.IO_WRITE),
        
        # LOGIC
        ("org.apache.nifi.processors.standard.RouteOnAttribute", ProcessorCategory.LOGIC),
        ("org.apache.nifi.processors.standard.RouteOnContent", ProcessorCategory.LOGIC),
        
        # TRANSFORM
        ("org.apache.nifi.processors.standard.JoltTransformJSON", ProcessorCategory.TRANSFORM),
        ("org.apache.nifi.processors.standard.ConvertRecord", ProcessorCategory.TRANSFORM),
        
        # OTHER (unknown)
        ("com.custom.SomeCustomProcessor", ProcessorCategory.OTHER),
    ])
    def test_categorize_processor(self, processor_type, expected_category):
        """Test processor type categorization."""
        result = self.categorizer.categorize(processor_type)
        assert result == expected_category
    
    def test_unclassified_tracking(self):
        """Test that unclassified processors are tracked."""
        self.categorizer.categorize("com.unknown.NewProcessor")
        unclassified = self.categorizer.get_unclassified()
        assert "com.unknown.NewProcessor" in unclassified
    
    def test_category_expansion(self):
        """Test adding new patterns to categories."""
        custom_patterns = {
            ProcessorCategory.IO_READ: ["CustomGet*"]
        }
        categorizer = ProcessorCategorizer(additional_patterns=custom_patterns)
        result = categorizer.categorize("com.custom.CustomGetData")
        assert result == ProcessorCategory.IO_READ
```

**Component Formatting Tests**:

```python
# tests/unit/test_component_formatting.py
"""Unit tests for component reference formatting."""

import pytest
from nifi_mcp_server.workflows.nodes.component_formatter import (
    format_component_reference,
    format_processor_reference,
    format_destination_reference
)

class TestComponentFormatting:
    """Test unambiguous component reference formatting."""
    
    def test_format_processor_reference(self):
        """Test processor reference formatting."""
        processor = {
            "id": "12345678-1234-1234-1234-123456789012",
            "component": {
                "name": "GetFile",
                "type": "org.apache.nifi.processors.standard.GetFile"
            }
        }
        result = format_processor_reference(processor)
        assert "GetFile" in result
        assert "GetFile)" in result  # Type
        assert "[id:12345678]" in result  # Short ID
    
    def test_format_destination_reference(self):
        """Test destination reference formatting."""
        result = format_destination_reference(
            "abcd1234-5678-9012-3456-789012345678",
            "LogErrors",
            "org.apache.nifi.processors.standard.LogAttribute"
        )
        assert "LogErrors" in result
        assert "LogAttribute)" in result
        assert "[id:abcd1234]" in result
```

---

### Phase 3: Node Implementation

**New Tests to Add**:

```
tests/workflows/
├── test_doc_nodes.py              # NEW - Node unit tests
├── test_pg_tree_building.py       # NEW - Tree structure tests
├── test_io_extraction.py          # NEW - IO extraction tests
└── test_error_handling_analysis.py # NEW - Error analysis tests
```

#### Key: LLM Mocking/Caching Strategy

**Create LLM Response Cache**:

```python
# tests/fixtures/llm_responses/
├── pg_summary_responses.json      # Cached PG summary responses
├── virtual_group_responses.json   # Cached virtual grouping responses
└── executive_summary_responses.json
```

**LLM Mock Fixture**:

```python
# tests/workflows/conftest.py
"""Workflow test fixtures with LLM mocking."""

import pytest
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "llm_responses"

@pytest.fixture
def mock_llm_responses():
    """Load cached LLM responses for testing."""
    responses = {}
    for file in FIXTURES_DIR.glob("*.json"):
        with open(file) as f:
            responses[file.stem] = json.load(f)
    return responses

@pytest.fixture
def mock_call_llm_async(mock_llm_responses):
    """
    Mock LLM calls to return cached responses.
    
    This prevents LLM API costs during testing.
    Responses are keyed by action_id pattern.
    """
    async def _mock_call_llm(messages, tools, execution_state, action_id):
        # Match action_id pattern to cached response
        if "pg-summary" in action_id:
            return {"content": mock_llm_responses.get("pg_summary_default", "")}
        elif "virtual-groups" in action_id:
            return {"content": mock_llm_responses.get("virtual_groups_default", "[]")}
        elif "generation-exec-summary" in action_id:
            return {"content": mock_llm_responses.get("executive_summary_default", "")}
        else:
            return {"content": "Mock LLM response"}
    
    return _mock_call_llm

@pytest.fixture
def doc_node_with_mock_llm(mock_call_llm_async):
    """Create doc nodes with mocked LLM calls."""
    from nifi_mcp_server.workflows.nodes.doc_nodes import HierarchicalAnalysisNode
    
    node = HierarchicalAnalysisNode()
    node.call_llm_async = mock_call_llm_async
    return node
```

**Sample Cached Response**:

```json
// tests/fixtures/llm_responses/pg_summary_responses.json
{
  "pg_summary_default": "This process group handles file ingestion from SFTP servers, validates incoming records, and routes them based on data quality rules.",
  
  "pg_summary_io_heavy": "This process group reads transaction data from Kafka topics, enriches records with customer information from a database, and publishes results to downstream topics.",
  
  "virtual_groups_default": "[{\"name\": \"Input Processing\", \"purpose\": \"Handle incoming data\", \"processor_ids\": [\"abc\", \"def\"]}, {\"name\": \"Validation\", \"purpose\": \"Validate data quality\", \"processor_ids\": [\"ghi\", \"jkl\"]}]"
}
```

**Node Unit Tests (with mocked LLM)**:

```python
# tests/workflows/test_doc_nodes.py
"""Unit tests for documentation workflow nodes."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from nifi_mcp_server.workflows.nodes.doc_nodes import (
    InitializeDocNode,
    DiscoveryNode,
    HierarchicalAnalysisNode,
    DocumentationNode
)

class TestInitializeDocNode:
    """Test initialization node."""
    
    @pytest.fixture
    def init_node(self):
        return InitializeDocNode()
    
    @pytest.mark.asyncio
    async def test_initialization_success(self, init_node):
        """Test successful initialization."""
        shared = {
            "process_group_id": "root",
            "user_request_id": "test-123",
            "provider": "openai",
            "model_name": "gpt-4",
            "selected_nifi_server_id": "nifi-local"
        }
        
        # Mock parent prep_async
        with patch.object(init_node, 'prep_async', new_callable=AsyncMock) as mock_prep:
            mock_prep.return_value = shared
            prep_res = await init_node.prep_async(shared)
        
        exec_res = await init_node.exec_async(prep_res)
        
        assert exec_res["status"] == "success"
        assert "initialized_state" in exec_res
        assert exec_res["initialized_state"]["process_group_id"] == "root"
    
    @pytest.mark.asyncio
    async def test_initialization_missing_server(self, init_node):
        """Test initialization fails without NiFi server."""
        prep_res = {
            "process_group_id": "root",
            "user_request_id": "test-123",
            "nifi_server_id": None  # Missing
        }
        
        exec_res = await init_node.exec_async(prep_res)
        
        assert exec_res["status"] == "error"
        assert "No NiFi server" in exec_res["error"]


class TestHierarchicalAnalysisNode:
    """Test hierarchical analysis node with mocked LLM."""
    
    @pytest.fixture
    def analysis_node(self, mock_call_llm_async):
        """Analysis node with mocked LLM."""
        node = HierarchicalAnalysisNode()
        node.call_llm_async = mock_call_llm_async
        # Mock the NiFi tool call too
        node.call_nifi_tool = AsyncMock(return_value=[])
        return node
    
    @pytest.mark.asyncio
    async def test_categorize_processors(self, analysis_node):
        """Test processor categorization."""
        processors = [
            {"id": "1", "component": {"name": "GetFile", "type": "org.apache.nifi.processors.standard.GetFile"}},
            {"id": "2", "component": {"name": "RouteData", "type": "org.apache.nifi.processors.standard.RouteOnAttribute"}},
        ]
        
        # Call the categorization method directly
        categorized = analysis_node._categorize_processors(processors)
        
        assert "IO_READ" in categorized
        assert "LOGIC" in categorized
        assert "GetFile" in categorized["IO_READ"]
        assert "RouteData" in categorized["LOGIC"]
```

---

### Phase 4: Integration

**New Tests to Add**:

```
tests/workflows/
└── test_workflow_wiring.py        # NEW - Test node transitions
```

```python
# tests/workflows/test_workflow_wiring.py
"""Test workflow definition and node wiring."""

import pytest
from nifi_mcp_server.workflows.definitions.flow_documentation import (
    create_flow_documentation_workflow
)
from nifi_mcp_server.workflows.registry import get_workflow_registry

class TestFlowDocumentationWorkflowWiring:
    """Test multi-node workflow wiring."""
    
    def test_workflow_registration(self):
        """Test workflow is registered correctly."""
        registry = get_workflow_registry()
        workflow = registry.get_workflow("flow_documentation")
        
        assert workflow is not None
        assert workflow.name == "flow_documentation"
        assert workflow.is_async is True
        assert "Review" in workflow.phases
    
    def test_workflow_creation(self):
        """Test workflow creates without error."""
        flow = create_flow_documentation_workflow()
        
        assert flow is not None
        assert flow.start_node is not None
        assert flow.start_node.name == "initialize_doc"
    
    def test_node_transitions(self):
        """Test all node transitions are wired correctly."""
        flow = create_flow_documentation_workflow()
        
        # Get all nodes by traversing
        visited = set()
        nodes = []
        
        def traverse(node):
            if node is None or id(node) in visited:
                return
            visited.add(id(node))
            nodes.append(node)
            if hasattr(node, 'successors'):
                for successor in node.successors.values():
                    traverse(successor)
        
        traverse(flow.start_node)
        
        # Should have 4 nodes
        assert len(nodes) == 4
        
        # Verify node names
        node_names = {n.name for n in nodes}
        assert "initialize_doc" in node_names
        assert "discovery_node" in node_names
        assert "hierarchical_analysis_node" in node_names
        assert "documentation_node" in node_names
    
    def test_discovery_self_loop(self):
        """Test discovery node has self-loop for pagination."""
        flow = create_flow_documentation_workflow()
        discovery = flow.start_node.successors.get("default")
        
        assert discovery is not None
        assert "continue" in discovery.successors
        # Self-loop: discovery -> discovery
        assert discovery.successors["continue"] is discovery
    
    def test_analysis_self_loop(self):
        """Test analysis node has self-loop for depth iteration."""
        flow = create_flow_documentation_workflow()
        discovery = flow.start_node.successors.get("default")
        analysis = discovery.successors.get("complete")
        
        assert analysis is not None
        assert "next_level" in analysis.successors
        # Self-loop: analysis -> analysis
        assert analysis.successors["next_level"] is analysis
```

---

### Phase 5: End-to-End Testing

**Integration Tests with Real NiFi** (Run manually, not in CI):

```python
# tests/workflows/test_doc_workflow_integration.py
"""Integration tests for documentation workflow with real NiFi."""

import pytest
from nifi_mcp_server.workflows.registry import get_workflow_registry

@pytest.mark.integration
@pytest.mark.slow
class TestDocWorkflowIntegration:
    """
    Integration tests that run against real NiFi.
    
    Run manually with: pytest -m integration tests/workflows/test_doc_workflow_integration.py
    """
    
    @pytest.mark.asyncio
    async def test_simple_flow_documentation(
        self, 
        test_pg_with_processors,  # Existing fixture
        async_client,
        base_url,
        mcp_headers
    ):
        """Test documentation of simple flow."""
        pg_id = test_pg_with_processors["pg_id"]
        
        # Create workflow executor
        registry = get_workflow_registry()
        executor = registry.create_async_executor("flow_documentation")
        
        # Execute workflow
        result = await executor.execute_async({
            "user_request_id": "test-integration-1",
            "process_group_id": pg_id,
            "provider": "openai",  # Will be mocked in actual test
            "model_name": "gpt-4",
            "selected_nifi_server_id": mcp_headers["X-Nifi-Server-Id"]
        })
        
        # Verify result structure
        assert result["status"] == "success"
        shared = result.get("shared_state", {})
        assert "final_document" in shared
        assert "pg_summaries" in shared
    
    @pytest.mark.asyncio
    async def test_hierarchical_flow_documentation(
        self,
        test_complex_flow,  # Existing fixture - creates nested structure
        async_client,
        base_url,
        mcp_headers
    ):
        """Test documentation of hierarchical flow."""
        pg_id = test_complex_flow["process_group_id"]
        
        # Similar test with complex flow fixture
        ...
```

---

## 3. NiFi V2 Token Authentication

### Impact Assessment

The existing fixtures use NiFi V1 API patterns. If tests run against NiFi V2:

```python
# Current fixture header setup (conftest.py line 62-67)
@pytest.fixture(scope="module")
def mcp_headers(base_headers: dict, nifi_test_server_id: str) -> dict:
    return {
        **base_headers,
        "X-Nifi-Server-Id": nifi_test_server_id,
    }
```

### Proposed Update

```python
# tests/conftest.py - Updated header fixture
@pytest.fixture(scope="module")
def mcp_headers(base_headers: dict, nifi_test_server_id: str) -> dict:
    """
    Headers for MCP NiFi operations.
    
    Note: Authentication is handled by the MCP server using config.yaml.
    The X-Nifi-Server-Id header tells the MCP server which NiFi to target.
    Token-based auth (NiFi V2) is handled internally by nifi_client.py.
    """
    return {
        **base_headers,
        "X-Nifi-Server-Id": nifi_test_server_id,
    }
```

**Key Insight**: Authentication happens at the `nifi_client.py` level, not in tests. Tests just need to specify which server to use. The MCP server handles token auth based on `config.yaml`.

### Test for Token Auth

```python
# tests/core_operations/test_nifi_v2_auth.py
"""Test NiFi V2 token authentication compatibility."""

import pytest
import os

@pytest.mark.skipif(
    os.environ.get("NIFI_VERSION") != "2",
    reason="Only run for NiFi V2 instances"
)
class TestNiFiV2Auth:
    """Test NiFi V2 token authentication."""
    
    @pytest.mark.asyncio
    async def test_list_processors_with_token_auth(
        self,
        async_client,
        base_url,
        mcp_headers,
        root_process_group_id
    ):
        """Test that listing processors works with token auth."""
        from tests.utils.nifi_test_utils import call_tool
        
        result = await call_tool(
            client=async_client,
            base_url=base_url,
            tool_name="list_nifi_objects",
            arguments={
                "object_type": "processors",
                "process_group_id": root_process_group_id
            },
            headers=mcp_headers
        )
        
        # Should succeed with token auth
        assert result is not None
        # Should not contain auth errors
        assert "401" not in str(result)
        assert "403" not in str(result)
```

---

## 4. JSON File Mode (Future Enhancement)

### Vision

```
┌─────────────────────────────────────────────────────────────────────┐
│  Future: Tests use NiFi JSON exports instead of live API calls     │
│                                                                     │
│  Benefits:                                                          │
│  • No NiFi instance needed                                         │
│  • Deterministic, repeatable tests                                 │
│  • Can run in CI/CD                                                │
│  • Same fixtures for testing AND git-based flow analysis          │
└─────────────────────────────────────────────────────────────────────┘
```

### Proposed Architecture

```python
# Future: nifi_mcp_server/nifi_client_file.py
"""File-based NiFi client for testing and git analysis."""

class NiFiFileClient:
    """
    NiFi client that reads from exported flow JSON files.
    
    Compatible with NiFi's "Download flow definition" export format.
    """
    
    def __init__(self, flow_json_path: str):
        with open(flow_json_path) as f:
            self.flow = json.load(f)
    
    def get_process_group(self, pg_id: str) -> Dict:
        """Get process group from loaded flow."""
        ...
    
    def get_processors(self, pg_id: str) -> List[Dict]:
        """Get processors in a process group."""
        ...
    
    def get_connections(self, pg_id: str) -> List[Dict]:
        """Get connections in a process group."""
        ...
```

### Test Fixture Evolution

```python
# Future: tests/conftest.py addition

@pytest.fixture
def nifi_flow_from_file():
    """Load NiFi flow from JSON export for testing."""
    def _load(fixture_name: str):
        path = Path(__file__).parent / "fixtures" / "nifi_flows" / f"{fixture_name}.json"
        return NiFiFileClient(path)
    return _load

# Usage in tests:
async def test_with_file_fixture(nifi_flow_from_file):
    client = nifi_flow_from_file("hierarchical_flow")
    processors = client.get_processors("root")
    assert len(processors) == 10
```

### Export Test Fixtures

Create fixtures by exporting real flows:

1. In NiFi UI: Right-click PG → "Download flow definition"
2. Save as `tests/fixtures/nifi_flows/<name>.json`
3. Use in tests without NiFi connectivity

---

## 5. Test Evolution Timeline

```
┌─────────────────────────────────────────────────────────────────────┐
│ Phase 1: Infrastructure                                             │
│   - No tests needed                                                 │
│   - Manual verification during later phases                         │
├─────────────────────────────────────────────────────────────────────┤
│ Phase 2: Tool Enhancements                                          │
│   + tests/unit/test_processor_categorization.py                     │
│   + tests/unit/test_component_formatting.py                         │
│   = ~10 new unit tests, ~1 second runtime                          │
├─────────────────────────────────────────────────────────────────────┤
│ Phase 3: Node Implementation                                        │
│   + tests/workflows/conftest.py (LLM mock fixtures)                 │
│   + tests/fixtures/llm_responses/*.json (cached responses)          │
│   + tests/workflows/test_doc_nodes.py                               │
│   + tests/workflows/test_pg_tree_building.py                        │
│   + tests/workflows/test_io_extraction.py                           │
│   = ~20 new tests with mocked LLM, ~3 seconds runtime              │
├─────────────────────────────────────────────────────────────────────┤
│ Phase 4: Integration                                                │
│   + tests/workflows/test_workflow_wiring.py                         │
│   = ~5 tests for workflow structure, <1 second runtime             │
├─────────────────────────────────────────────────────────────────────┤
│ Phase 5: End-to-End                                                 │
│   + tests/workflows/test_doc_workflow_integration.py                │
│   = ~3 integration tests, requires NiFi, manual run                │
│   Σ Total: ~38 new tests                                           │
├─────────────────────────────────────────────────────────────────────┤
│ Future: JSON File Mode                                              │
│   + nifi_mcp_server/nifi_client_file.py                             │
│   + tests/fixtures/nifi_flows/*.json                                │
│   + Update fixtures to use file mode optionally                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. LLM Testing Best Practices

### Do NOT Test

- Exact LLM output wording
- Specific sentence structure
- Token counts (varies by model version)

### DO Test

- Output structure (has summary, has diagram, has IO table)
- No raw UUIDs in user-facing sections
- Mermaid diagram is parseable (starts with `graph` or `flowchart`)
- All PGs have summaries
- IO endpoints are captured

### Sample Structure Validation

```python
def test_documentation_structure(self, final_document):
    """Test documentation has required sections."""
    assert "# NiFi Flow Documentation" in final_document
    assert "## Executive Summary" in final_document
    assert "```mermaid" in final_document
    assert "## External Interactions" in final_document
    assert "## Error Handling" in final_document
    
def test_no_raw_uuids(self, final_document):
    """Test no raw UUIDs in documentation."""
    import re
    uuid_pattern = r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'
    # Should only appear in [id:xxxx] format, not raw
    raw_uuids = re.findall(uuid_pattern, final_document)
    for uuid in raw_uuids:
        # Should be in short form like [id:12345678]
        assert f"[id:{uuid[:8]}]" in final_document or uuid not in final_document
```

---

## 7. Files to Create

### New Test Files

| File | Phase | Type |
|------|-------|------|
| `tests/unit/test_processor_categorization.py` | 2 | Unit |
| `tests/unit/test_component_formatting.py` | 2 | Unit |
| `tests/workflows/conftest.py` | 3 | Fixtures |
| `tests/workflows/test_doc_nodes.py` | 3 | Unit |
| `tests/workflows/test_pg_tree_building.py` | 3 | Unit |
| `tests/workflows/test_io_extraction.py` | 3 | Unit |
| `tests/workflows/test_error_handling_analysis.py` | 3 | Unit |
| `tests/workflows/test_workflow_wiring.py` | 4 | Unit |
| `tests/workflows/test_doc_workflow_integration.py` | 5 | Integration |

### New Fixture Files

| File | Purpose |
|------|---------|
| `tests/fixtures/llm_responses/pg_summary_responses.json` | Cached PG summaries |
| `tests/fixtures/llm_responses/virtual_group_responses.json` | Cached virtual groups |
| `tests/fixtures/llm_responses/executive_summary_responses.json` | Cached exec summaries |

### Future Files (JSON File Mode)

| File | Purpose |
|------|---------|
| `nifi_mcp_server/nifi_client_file.py` | File-based NiFi client |
| `tests/fixtures/nifi_flows/simple_linear.json` | 5-processor linear flow |
| `tests/fixtures/nifi_flows/hierarchical.json` | 3-level nested PGs |
| `tests/fixtures/nifi_flows/large_flat.json` | 30+ processors for virtual grouping |

---

## 8. Test Running Commands

```bash
# Run all unit tests (fast, no NiFi needed)
pytest tests/unit/ -v

# Run workflow unit tests (with mocked LLM)
pytest tests/workflows/ -v --ignore=tests/workflows/test_doc_workflow_integration.py

# Run integration tests (needs NiFi, slow)
pytest tests/workflows/test_doc_workflow_integration.py -v -m integration

# Run specific test file
pytest tests/unit/test_processor_categorization.py -v

# Run with coverage
pytest tests/ --cov=nifi_mcp_server --cov-report=html

# Clear LLM response cache and regenerate (manual)
python tests/utils/regenerate_llm_fixtures.py
```

---

## 9. Success Criteria

### MVP Testing Complete When

- [ ] Processor categorization has unit tests
- [ ] Component formatting has unit tests
- [ ] Doc nodes have unit tests with mocked LLM
- [ ] Workflow wiring has tests
- [ ] At least one integration test passes against real NiFi
- [ ] Tests run without incurring LLM costs (cached responses)
- [ ] Tests work with NiFi V2 token auth

### Future Milestones

- [ ] JSON file mode implemented
- [ ] All fixtures have file-based alternatives
- [ ] Tests can run in CI without NiFi instance

---

## 10. Notes for Implementation

1. **Create LLM cache first** - Before writing node tests, create the cached response fixtures
2. **Keep fixtures small** - Only cache the responses actually needed
3. **Document cache regeneration** - When prompts change, responses need updating
4. **Mark integration tests** - Use `@pytest.mark.integration` for tests needing NiFi
5. **NiFi V2 consideration** - Ensure `config.yaml` has correct token auth for test server

