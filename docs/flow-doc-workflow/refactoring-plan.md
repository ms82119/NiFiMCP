# Refactoring Plan: Split `doc_nodes.py`

## Current State

The `doc_nodes.py` file has grown to **3,597 lines**, making it difficult to maintain and navigate. The file contains:
- 4 node classes (~136-1725 lines each)
- 28 helper methods for IO extraction, error handling, document generation, analysis, etc.
- Complex logic that would benefit from separation of concerns

### Current File Structure

| Class | Lines | Key Methods |
|-------|-------|-------------|
| `InitializeDocNode` | 136 | Basic prep/exec/post |
| `DiscoveryNode` | 639 | `_build_pg_tree`, `_calculate_depths` |
| `HierarchicalAnalysisNode` | 1,725 | `_extract_io_endpoints_detailed` (335), `_extract_error_handling` (247), `_analyze_pg_with_summaries` (155), `_analyze_pg_direct` (116), `_analyze_virtual_group` (127), `_categorize_processors`, `_format_connections_for_prompt`, `_build_connectivity_summary`, `_create_virtual_groups`, `_parse_virtual_groups`, `_get_pg_processors` |
| `DocumentationNode` | 1,065 | `_build_aggregated_io_table` (203), `_generate_hierarchical_diagram` (153), `_build_hierarchical_doc` (146), `_build_error_handling_table` (85), `_generate_executive_summary` (49), `_assemble_hierarchical_document` (54), `_validate_output` (27), `_emit_section_event` |

**Total**: 3,597 lines

---

## Proposed Structure

Split into the following modules:

```
nifi_mcp_server/workflows/nodes/
├── doc_nodes.py              # Main node classes (reduced to ~900 lines)
├── component_formatter.py    # Component formatting utilities ✅ (already created)
├── io_extraction.py          # IO endpoint extraction logic (~400 lines)
├── error_handling.py         # Error handling analysis logic (~300 lines)
├── analysis_helpers.py       # Analysis phase helper functions (~500 lines)
└── doc_generation_helpers.py # Document generation utilities (~600 lines)
```

---

## Module Breakdown

### 1. `component_formatter.py` ✅ (Already Created)

**Status**: Complete

**Contents**:
- `format_component_reference()` - Format component with name, type, ID
- `format_processor_reference()` - Convenience for processors
- `format_component_for_table()` - Format for table display
- `format_destination_reference()` - Format destination components

**Location**: `nifi_mcp_server/workflows/nodes/component_formatter.py`

---

### 2. `io_extraction.py` (NEW)

**Extract from**: `HierarchicalAnalysisNode` methods

**Contents**:
- `extract_io_endpoints_detailed()` - Main extraction logic (~335 lines)
  - Handles: GetFile, PutFile, SFTP, HTTP, Kafka, Database, JMS, S3, MongoDB, Elasticsearch, etc.
  - Processor-specific endpoint extraction
- `extract_io_basic()` - Fallback basic extraction (~30 lines)
- `extract_io_from_categorized()` - Extract from categorized processors (~10 lines)

**Dependencies**:
- `component_formatter` for formatting references
- `processor_categories` for IO_READ/IO_WRITE identification

**Interface**:
```python
async def extract_io_endpoints_detailed(
    processors: List[Dict],
    nifi_tool_caller: Callable,  # For calling get_nifi_object_details
    prep_res: Dict[str, Any],
    cached_proc_details: Optional[Dict[str, Dict]] = None,
    logger
) -> List[Dict]:
    """Extract detailed IO endpoint information."""
    ...

def extract_io_basic(
    processors: List[Dict],
    categorizer
) -> List[Dict]:
    """Fallback basic IO extraction if detailed fetch fails."""
    ...
```

**Move from**: 
- `HierarchicalAnalysisNode._extract_io_endpoints_detailed()` (lines ~1735-2070)
- `HierarchicalAnalysisNode._extract_io_basic()` (lines ~2071-2099)
- `HierarchicalAnalysisNode._extract_io_from_categorized()` (lines ~2100-2111)

**Estimated size**: ~400 lines

---

### 3. `error_handling.py` (NEW)

**Extract from**: `HierarchicalAnalysisNode` methods

**Contents**:
- `extract_error_handling()` - Main error analysis logic (~247 lines)
  - Builds connection maps
  - Analyzes error relationships
  - Identifies handled vs unhandled errors
- `_build_connection_map()` - Helper to build connection mapping (internal)
- `_normalize_relationships()` - Normalize relationship structures (internal)

**Dependencies**:
- `component_formatter` for formatting references

**Interface**:
```python
async def extract_error_handling(
    processors: List[Dict],
    connections: List[Dict],
    nifi_tool_caller: Callable,  # For calling get_nifi_object_details
    prep_res: Dict[str, Any],
    cached_proc_details: Optional[Dict[str, Dict]] = None,
    logger
) -> List[Dict]:
    """Extract error handling information from processors and connections."""
    ...
```

**Move from**: 
- `HierarchicalAnalysisNode._extract_error_handling()` (lines ~2112-2359)

**Estimated size**: ~300 lines

---

### 4. `analysis_helpers.py` (NEW)

**Extract from**: `HierarchicalAnalysisNode` methods

**Contents**:
- `analyze_pg_direct()` - Analyze leaf PG directly (~116 lines)
  - Fetches processor details
  - Categorizes processors
  - Extracts IO endpoints and business logic
  - Calls LLM for summary
- `analyze_pg_with_summaries()` - Analyze parent PG with children (~155 lines)
  - Combines child summaries with parent's own processors
  - Handles both children-only and children+processors cases
- `analyze_virtual_group()` - Analyze virtual sub-flow group (~127 lines)
- `create_virtual_groups()` - Use LLM to identify logical groupings (~50 lines)
- `parse_virtual_groups()` - Parse LLM response into virtual groups (~28 lines)
- `categorize_processors()` - Categorize processors by type (~43 lines)
- `format_connections_for_prompt()` - Format connections for LLM prompt (~51 lines)
- `build_connectivity_summary()` - Build simplified connectivity for LLM (~36 lines)
- `get_pg_processors()` - Get processors belonging to a PG (~12 lines)

**Dependencies**:
- `io_extraction` for IO endpoint extraction
- `error_handling` for error handling extraction
- `component_formatter` for formatting references
- `processor_categories` for categorization

**Interface**:
```python
async def analyze_pg_direct(
    pg: Dict,
    processors: List[Dict],
    nifi_tool_caller: Callable,
    llm_caller: Callable,
    prep_res: Dict[str, Any],
    io_extractor: Callable,
    error_extractor: Callable,
    categorizer,
    logger
) -> Dict:
    """Analyze a small leaf PG directly."""
    ...

async def analyze_pg_with_summaries(
    pg: Dict,
    child_summaries: List[Dict],
    direct_processors: List[Dict],
    nifi_tool_caller: Callable,
    llm_caller: Callable,
    prep_res: Dict[str, Any],
    io_extractor: Callable,
    error_extractor: Callable,
    categorizer,
    logger
) -> Dict:
    """Analyze a PG using child summaries AND parent's own processors."""
    ...

async def analyze_virtual_group(
    virtual_group: Dict,
    all_processors: List[Dict],
    nifi_tool_caller: Callable,
    llm_caller: Callable,
    prep_res: Dict[str, Any],
    parent_pg_id: str,
    io_extractor: Callable,
    error_extractor: Callable,
    categorizer,
    logger
) -> Dict:
    """Analyze a virtual sub-flow group."""
    ...

async def create_virtual_groups(
    pg_id: str,
    processors: List[Dict],
    connections: List[Dict],
    llm_caller: Callable,
    prep_res: Dict[str, Any],
    categorizer,
    logger
) -> List[Dict]:
    """Use LLM to identify logical groupings in a large flat PG."""
    ...

def categorize_processors(
    processors: List[Dict],
    categorizer,
    include_states: bool = True,
    cached_proc_details: Optional[Dict[str, Dict]] = None
) -> Dict[str, List[Dict]]:
    """Categorize processors and return category -> processor info mapping."""
    ...

def format_connections_for_prompt(
    connections: List[Dict],
    cached_proc_details: Optional[Dict[str, Dict]] = None
) -> List[Dict]:
    """Format connections for LLM prompt."""
    ...

def build_connectivity_summary(
    processors: List[Dict],
    connections: List[Dict]
) -> List[Dict]:
    """Build simplified connectivity for LLM."""
    ...
```

**Move from**: 
- `HierarchicalAnalysisNode._analyze_pg_direct()` (lines ~1523-1639)
- `HierarchicalAnalysisNode._analyze_pg_with_summaries()` (lines ~1367-1522)
- `HierarchicalAnalysisNode._analyze_virtual_group()` (lines ~1239-1366)
- `HierarchicalAnalysisNode._create_virtual_groups()` (lines ~1161-1210)
- `HierarchicalAnalysisNode._parse_virtual_groups()` (lines ~1211-1238)
- `HierarchicalAnalysisNode._categorize_processors()` (lines ~1640-1683)
- `HierarchicalAnalysisNode._format_connections_for_prompt()` (lines ~1684-1734)
- `HierarchicalAnalysisNode._build_connectivity_summary()` (lines ~2360-2396)
- `HierarchicalAnalysisNode._get_pg_processors()` (lines ~1148-1160)

**Estimated size**: ~500 lines

---

### 5. `doc_generation_helpers.py` (NEW)

**Extract from**: `DocumentationNode` methods

**Contents**:
- `build_aggregated_io_table()` - Build IO table with formatting (~203 lines)
  - Aggregates IO endpoints from all PGs
  - Formats with processor references
  - Groups by type and direction
- `build_error_handling_table()` - Build error handling table (~85 lines)
  - Aggregates error handling from all PGs
  - Shows handled vs unhandled errors
- `build_hierarchical_doc()` - Build nested documentation sections (~146 lines)
  - Recursively builds documentation from hierarchy
  - Includes IO endpoints, processor states, error handling
- `generate_executive_summary()` - Generate executive summary via LLM (~49 lines)
- `generate_hierarchical_diagram()` - Generate Mermaid diagram via LLM (~153 lines)
  - Includes diagram cleaning and validation
- `assemble_hierarchical_document()` - Assemble final document (~54 lines)
- `validate_output()` - Output validation (~27 lines)
- `emit_section_event()` - Emit section generation event (~10 lines)

**Dependencies**:
- `component_formatter` for formatting references

**Interface**:
```python
def build_aggregated_io_table(
    pg_summaries: Dict[str, Dict],
    formatter
) -> str:
    """Build detailed aggregated IO table."""
    ...

def build_error_handling_table(
    pg_summaries: Dict[str, Dict],
    formatter
) -> str:
    """Build error handling table."""
    ...

def build_hierarchical_doc(
    root_pg_id: str,
    pg_tree: Dict[str, Dict],
    pg_summaries: Dict[str, Dict],
    virtual_groups: Dict[str, List[Dict]],
    formatter,
    depth: int = 0
) -> str:
    """Build nested documentation sections from hierarchy."""
    ...

async def generate_executive_summary(
    root_summary: Dict,
    all_summaries: Dict[str, Dict],
    llm_caller: Callable,
    prep_res: Dict[str, Any],
    logger
) -> str:
    """Generate executive summary from hierarchical summaries."""
    ...

async def generate_hierarchical_diagram(
    pg_tree: Dict[str, Dict],
    pg_summaries: Dict[str, Dict],
    virtual_groups: Dict[str, List[Dict]],
    llm_caller: Callable,
    prep_res: Dict[str, Any],
    logger
) -> str:
    """Generate Mermaid diagram showing PG hierarchy."""
    ...

def assemble_hierarchical_document(
    title: str,
    executive_summary: str,
    mermaid_diagram: str,
    hierarchical_doc: str,
    io_table: str,
    error_table: str
) -> str:
    """Assemble final document from sections."""
    ...

def validate_output(
    final_document: str,
    pg_summaries: Dict[str, Dict]
) -> List[str]:
    """Validate output and return list of issues."""
    ...
```

**Move from**: 
- `DocumentationNode._build_aggregated_io_table()` (lines ~3101-3304)
- `DocumentationNode._build_error_handling_table()` (lines ~3305-3390)
- `DocumentationNode._build_hierarchical_doc()` (lines ~2954-3100)
- `DocumentationNode._generate_executive_summary()` (lines ~2751-2799)
- `DocumentationNode._generate_hierarchical_diagram()` (lines ~2800-2953)
- `DocumentationNode._assemble_hierarchical_document()` (lines ~3391-3445)
- `DocumentationNode._validate_output()` (lines ~3446-3472)
- `DocumentationNode._emit_section_event()` (lines ~3473-3482)

**Estimated size**: ~600 lines

---

### 6. `doc_nodes.py` (REFACTORED)

**Remaining contents**:
- `InitializeDocNode` class (~136 lines) - No changes needed
- `DiscoveryNode` class (~639 lines)
  - Keep: `prep_async`, `exec_async`, `post_async`
  - Keep: `_build_pg_tree`, `_calculate_depths` (small helpers, can stay)
- `HierarchicalAnalysisNode` class (~400 lines, reduced from 1,725)
  - Core node logic: `prep_async`, `exec_async`, `post_async`
  - Calls to extracted modules
  - State management
- `DocumentationNode` class (~200 lines, reduced from 1,065)
  - Core node logic: `prep_async`, `exec_async`, `post_async`
  - Calls to extracted helpers
  - State management

**Imports**:
```python
from .component_formatter import (
    format_processor_reference,
    format_destination_reference,
    format_component_for_table
)
from .io_extraction import (
    extract_io_endpoints_detailed,
    extract_io_basic
)
from .error_handling import extract_error_handling
from .analysis_helpers import (
    analyze_pg_direct,
    analyze_pg_with_summaries,
    analyze_virtual_group,
    create_virtual_groups,
    categorize_processors,
    format_connections_for_prompt,
    build_connectivity_summary,
    get_pg_processors
)
from .doc_generation_helpers import (
    build_aggregated_io_table,
    build_error_handling_table,
    build_hierarchical_doc,
    generate_executive_summary,
    generate_hierarchical_diagram,
    assemble_hierarchical_document,
    validate_output
)
```

**Estimated size**: ~900 lines (reduced from 3,597)

---

## Refactoring Steps

### Step 1: Create Helper Modules

1. ✅ Create `component_formatter.py` (already done)
2. Create `io_extraction.py` - Extract IO logic (~400 lines)
3. Create `error_handling.py` - Extract error handling logic (~300 lines)
4. Create `analysis_helpers.py` - Extract analysis helper functions (~500 lines)
5. Create `doc_generation_helpers.py` - Extract document generation (~600 lines)

### Step 2: Update `HierarchicalAnalysisNode`

1. Replace `_extract_io_endpoints_detailed()` calls with `extract_io_endpoints_detailed()`
2. Replace `_extract_error_handling()` calls with `extract_error_handling()`
3. Replace `_analyze_pg_direct()` calls with `analyze_pg_direct()`
4. Replace `_analyze_pg_with_summaries()` calls with `analyze_pg_with_summaries()`
5. Replace `_analyze_virtual_group()` calls with `analyze_virtual_group()`
6. Replace `_create_virtual_groups()` calls with `create_virtual_groups()`
7. Replace `_categorize_processors()` calls with `categorize_processors()`
8. Replace `_format_connections_for_prompt()` calls with `format_connections_for_prompt()`
9. Replace `_build_connectivity_summary()` calls with `build_connectivity_summary()`
10. Replace `_get_pg_processors()` calls with `get_pg_processors()`
11. Remove all extracted methods
12. Update imports

### Step 3: Update `DocumentationNode`

1. Replace `_build_aggregated_io_table()` calls with `build_aggregated_io_table()`
2. Replace `_build_error_handling_table()` calls with `build_error_handling_table()`
3. Replace `_build_hierarchical_doc()` calls with `build_hierarchical_doc()`
4. Replace `_generate_executive_summary()` calls with `generate_executive_summary()`
5. Replace `_generate_hierarchical_diagram()` calls with `generate_hierarchical_diagram()`
6. Replace `_assemble_hierarchical_document()` calls with `assemble_hierarchical_document()`
7. Replace `_validate_output()` calls with `validate_output()`
8. Replace `_emit_section_event()` calls with `emit_section_event()` (or keep as small helper)
9. Remove extracted methods
10. Update imports

### Step 4: Testing

1. Ensure all imports resolve correctly
2. Test that extracted functions work with node context (logger, tool caller, LLM caller)
3. Verify documentation output is unchanged
4. Run existing tests
5. Test with real workflow execution

---

## Implementation Notes

### Passing Context to Extracted Functions

Since extracted functions need access to:
- `self.call_nifi_tool()` - For API calls
- `self.call_llm_async()` - For LLM calls
- `self.bound_logger` - For logging
- `self.categorizer` - For processor categorization
- `prep_res` - For configuration

**Option A**: Pass as parameters (recommended)
```python
# In node
io_endpoints = await extract_io_endpoints_detailed(
    processors,
    self.call_nifi_tool,  # Pass method
    prep_res,
    cached_proc_details,
    self.bound_logger
)

summary = await analyze_pg_direct(
    pg,
    direct_processors,
    self.call_nifi_tool,
    self.call_llm_async,
    prep_res,
    extract_io_endpoints_detailed,  # Pass function
    extract_error_handling,  # Pass function
    self.categorizer,
    self.bound_logger
)
```

**Option B**: Create helper class with context
```python
class AnalysisHelper:
    def __init__(self, nifi_tool_caller, llm_caller, logger, categorizer, prep_res):
        self.call_nifi_tool = nifi_tool_caller
        self.call_llm = llm_caller
        self.logger = logger
        self.categorizer = categorizer
        self.prep_res = prep_res
    
    async def analyze_pg_direct(self, pg, processors):
        ...
```

**Recommendation**: Use Option A (simpler, more functional, easier to test)

### Handling Async Functions

Some extracted functions are async (need `await`), some are sync. Ensure:
- Async functions are properly awaited in nodes
- Sync functions can be called directly
- All function signatures match their usage

### Backward Compatibility

- Keep function signatures as similar as possible
- Maintain same return value structures
- Ensure error handling is preserved
- Keep all defensive checks and type validation

---

## File Size Targets

| File | Current | Target | Reduction |
|------|---------|--------|-----------|
| `doc_nodes.py` | 3,597 lines | ~900 lines | -75% |
| `io_extraction.py` | 0 | ~400 lines | NEW |
| `error_handling.py` | 0 | ~300 lines | NEW |
| `analysis_helpers.py` | 0 | ~500 lines | NEW |
| `doc_generation_helpers.py` | 0 | ~600 lines | NEW |
| `component_formatter.py` | ~100 lines | ~100 lines | ✅ |

**Total**: 3,597 lines → ~2,800 lines (slight reduction due to imports/boilerplate, but much better organization)

---

## Benefits

1. **Maintainability**: Each module has a single, clear responsibility
2. **Testability**: Helper functions can be unit tested independently
3. **Reusability**: IO extraction, error handling, and analysis helpers could be used by other workflows
4. **Readability**: Easier to find and understand specific functionality
5. **Collaboration**: Multiple developers can work on different modules simultaneously
6. **Debugging**: Easier to isolate issues to specific modules

---

## Migration Checklist

- [ ] Create `io_extraction.py` with extracted methods
- [ ] Create `error_handling.py` with extracted methods
- [ ] Create `analysis_helpers.py` with extracted methods
- [ ] Create `doc_generation_helpers.py` with extracted methods
- [ ] Update `HierarchicalAnalysisNode` to use extracted modules
- [ ] Update `DocumentationNode` to use extracted modules
- [ ] Update imports in `doc_nodes.py`
- [ ] Test all node transitions work correctly
- [ ] Test documentation output is unchanged
- [ ] Test with real workflow execution
- [ ] Update Phase 3 documentation to reflect new structure
- [ ] Update any tests that reference these methods

---

## Timeline Estimate

- **Step 1** (Create modules): 4-5 hours
  - `io_extraction.py`: 1.5 hours
  - `error_handling.py`: 1 hour
  - `analysis_helpers.py`: 1.5 hours
  - `doc_generation_helpers.py`: 1 hour
- **Step 2** (Update HierarchicalAnalysisNode): 2 hours
- **Step 3** (Update DocumentationNode): 1.5 hours
- **Step 4** (Testing): 2-3 hours

**Total**: ~10-12 hours

---

## Notes

- This refactoring should be done **after** the current workflow is stable and tested
- Consider doing this as a separate task/PR to keep the main implementation clean
- The extracted modules can be created incrementally (one at a time) to reduce risk
- Test after each module extraction to ensure nothing breaks
- Keep the original `doc_nodes.py` in version control until all tests pass

---

## Risk Mitigation

1. **Incremental approach**: Extract one module at a time, test, then move to next
2. **Comprehensive testing**: Test with real workflows after each extraction
3. **Version control**: Keep original code until fully verified
4. **Documentation**: Update docstrings and comments as you extract
5. **Type hints**: Ensure all function signatures have proper type hints for IDE support
