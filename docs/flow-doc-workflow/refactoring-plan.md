# Refactoring Plan: Split `doc_nodes.py`

## Problem

The `doc_nodes.py` file has grown to **~2000 lines**, making it difficult to maintain and navigate. The file contains:
- 4 node classes (~150-620 lines each)
- Multiple helper methods for IO extraction, error handling, document generation
- Complex logic that could benefit from separation of concerns

## Proposed Structure

Split into the following modules:

```
nifi_mcp_server/workflows/nodes/
├── doc_nodes.py              # Main node classes (reduced to ~800 lines)
├── component_formatter.py    # Component formatting utilities (NEW - already created)
├── io_extraction.py          # IO endpoint extraction logic (~300 lines)
├── error_handling.py         # Error handling analysis logic (~200 lines)
└── doc_generation_helpers.py # Document generation utilities (~400 lines)
```

## Module Breakdown

### 1. `component_formatter.py` ✅ (Already Created)

**Status**: Complete

**Contents**:
- `format_component_reference()` - Format component with name, type, ID
- `format_processor_reference()` - Convenience for processors
- `format_component_for_table()` - Format for table display
- `format_destination_reference()` - Format destination components

**Location**: `docs/flow-doc-workflow/src/nodes/component_formatter.py`

---

### 2. `io_extraction.py` (NEW)

**Extract from**: `HierarchicalAnalysisNode` methods

**Contents**:
- `extract_io_endpoints_detailed()` - Main extraction logic (~150 lines)
- `extract_io_basic()` - Fallback basic extraction (~30 lines)
- `_extract_endpoint_details_for_processor()` - Processor-specific extraction (~100 lines)
  - Handles: GetFile, PutFile, SFTP, HTTP, Kafka, Database, JMS, S3, MongoDB, Elasticsearch, etc.

**Dependencies**:
- `component_formatter` for formatting references
- `processor_categories` for IO_READ/IO_WRITE identification

**Interface**:
```python
async def extract_io_endpoints_detailed(
    processors: List[Dict],
    nifi_tool_caller: Callable,  # For calling get_nifi_object_details
    prep_res: Dict[str, Any],
    logger
) -> List[Dict]:
    """Extract detailed IO endpoint information."""
    ...
```

**Move from**: `HierarchicalAnalysisNode._extract_io_endpoints_detailed()` (lines ~926-1103)

---

### 3. `error_handling.py` (NEW)

**Extract from**: `HierarchicalAnalysisNode` methods

**Contents**:
- `extract_error_handling()` - Main error analysis logic (~150 lines)
- `_build_connection_map()` - Helper to build connection mapping (~30 lines)
- `_analyze_error_relationship()` - Analyze single error relationship (~50 lines)

**Dependencies**:
- `component_formatter` for formatting references

**Interface**:
```python
async def extract_error_handling(
    processors: List[Dict],
    connections: List[Dict],
    nifi_tool_caller: Callable,  # For calling get_nifi_object_details
    prep_res: Dict[str, Any],
    logger
) -> List[Dict]:
    """Extract error handling information."""
    ...
```

**Move from**: `HierarchicalAnalysisNode._extract_error_handling()` (lines ~1140-1285)

---

### 4. `doc_generation_helpers.py` (NEW)

**Extract from**: `DocumentationNode` methods

**Contents**:
- `build_aggregated_io_table()` - Build IO table with formatting (~150 lines)
- `build_error_handling_table()` - Build error handling table (~80 lines)
- `build_hierarchical_doc()` - Build nested documentation sections (~50 lines)
- `assemble_hierarchical_document()` - Assemble final document (~40 lines)
- `validate_output()` - Output validation (~30 lines)

**Dependencies**:
- `component_formatter` for formatting references

**Interface**:
```python
def build_aggregated_io_table(
    pg_summaries: Dict[str, Dict]
) -> str:
    """Build detailed aggregated IO table."""
    ...

def build_error_handling_table(
    pg_summaries: Dict[str, Dict]
) -> str:
    """Build error handling table."""
    ...

def build_hierarchical_doc(
    root_pg_id: str,
    pg_tree: Dict[str, Dict],
    pg_summaries: Dict[str, Dict],
    virtual_groups: Dict[str, List[Dict]],
    depth: int = 0
) -> str:
    """Build nested documentation sections."""
    ...
```

**Move from**: `DocumentationNode` methods (lines ~1633-1936)

---

### 5. `doc_nodes.py` (REFACTORED)

**Remaining contents**:
- `InitializeDocNode` class (~150 lines)
- `DiscoveryNode` class (~325 lines)
- `HierarchicalAnalysisNode` class (~600 lines, reduced from ~890)
  - Core node logic (prep, exec, post)
  - Virtual group creation/analysis
  - Calls to extracted modules
- `DocumentationNode` class (~300 lines, reduced from ~620)
  - Core node logic
  - Calls to extracted helpers

**Imports**:
```python
from .component_formatter import format_processor_reference, ...
from .io_extraction import extract_io_endpoints_detailed
from .error_handling import extract_error_handling
from .doc_generation_helpers import (
    build_aggregated_io_table,
    build_error_handling_table,
    build_hierarchical_doc,
    assemble_hierarchical_document,
    validate_output
)
```

---

## Refactoring Steps

### Step 1: Create Helper Modules ✅ (Component Formatter Done)

1. ✅ Create `component_formatter.py`
2. Create `io_extraction.py` - Extract IO logic
3. Create `error_handling.py` - Extract error handling logic
4. Create `doc_generation_helpers.py` - Extract document generation

### Step 2: Update `HierarchicalAnalysisNode`

1. Replace `_extract_io_endpoints_detailed()` calls with `extract_io_endpoints_detailed()`
2. Replace `_extract_error_handling()` calls with `extract_error_handling()`
3. Remove extracted methods
4. Update imports

### Step 3: Update `DocumentationNode`

1. Replace `_build_aggregated_io_table()` calls with `build_aggregated_io_table()`
2. Replace `_build_error_handling_table()` calls with `build_error_handling_table()`
3. Replace `_build_hierarchical_doc()` calls with `build_hierarchical_doc()`
4. Replace `_assemble_hierarchical_document()` calls with `assemble_hierarchical_document()`
5. Replace `_validate_output()` calls with `validate_output()`
6. Remove extracted methods
7. Update imports

### Step 4: Testing

1. Ensure all imports resolve correctly
2. Test that extracted functions work with node context (logger, tool caller)
3. Verify documentation output is unchanged
4. Run existing tests

---

## Implementation Notes

### Passing Context to Extracted Functions

Since extracted functions need access to:
- `self.call_nifi_tool()` - For API calls
- `self.bound_logger` - For logging
- `prep_res` - For configuration

**Option A**: Pass as parameters (recommended)
```python
# In node
io_endpoints = await extract_io_endpoints_detailed(
    processors,
    self.call_nifi_tool,  # Pass method
    prep_res,
    self.bound_logger
)
```

**Option B**: Create helper class with context
```python
class IOExtractor:
    def __init__(self, nifi_tool_caller, logger, prep_res):
        self.call_nifi_tool = nifi_tool_caller
        self.logger = logger
        self.prep_res = prep_res
    
    async def extract_detailed(self, processors):
        ...
```

**Recommendation**: Use Option A (simpler, more functional)

### Backward Compatibility

- Keep function signatures as similar as possible
- Maintain same return value structures
- Ensure error handling is preserved

---

## File Size Targets

| File | Current | Target | Reduction |
|------|---------|--------|-----------|
| `doc_nodes.py` | ~2000 lines | ~800 lines | -60% |
| `io_extraction.py` | 0 | ~300 lines | NEW |
| `error_handling.py` | 0 | ~200 lines | NEW |
| `doc_generation_helpers.py` | 0 | ~400 lines | NEW |
| `component_formatter.py` | 0 | ~100 lines | NEW ✅ |

**Total**: ~2000 lines → ~1800 lines (slight increase due to imports/boilerplate, but better organization)

---

## Benefits

1. **Maintainability**: Each module has a single, clear responsibility
2. **Testability**: Helper functions can be unit tested independently
3. **Reusability**: IO extraction and error handling could be used by other workflows
4. **Readability**: Easier to find and understand specific functionality
5. **Collaboration**: Multiple developers can work on different modules simultaneously

---

## Migration Checklist

- [ ] Create `io_extraction.py` with extracted methods
- [ ] Create `error_handling.py` with extracted methods
- [ ] Create `doc_generation_helpers.py` with extracted methods
- [ ] Update `HierarchicalAnalysisNode` to use extracted modules
- [ ] Update `DocumentationNode` to use extracted modules
- [ ] Update imports in `doc_nodes.py`
- [ ] Test all node transitions work correctly
- [ ] Test documentation output is unchanged
- [ ] Update Phase 3 documentation to reflect new structure
- [ ] Update any tests that reference these methods

---

## Timeline Estimate

- **Step 1** (Create modules): 2-3 hours
- **Step 2** (Update HierarchicalAnalysisNode): 1 hour
- **Step 3** (Update DocumentationNode): 1 hour
- **Step 4** (Testing): 1-2 hours

**Total**: ~5-7 hours

---

## Notes

- This refactoring should be done **after** Phase 3 implementation is complete and tested
- Consider doing this as a separate task/PR to keep the main implementation clean
- The extracted modules can be created incrementally (one at a time) to reduce risk

