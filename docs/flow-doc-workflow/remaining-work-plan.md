# Flow Documentation Workflow - Remaining Work Plan

## Status Summary

### ✅ Completed Phases

1. **Discovery Phase** - ✅ **OPTIMAL**
   - Port discovery implemented
   - Processor count fallback for invalid processors
   - Error handling improved
   - All data structures populated correctly

2. **Analysis Phase** - ✅ **OPTIMAL**
   - HandleHttpRequest/HandleHttpResponse endpoint extraction working
   - Processor reference formatting fixed
   - Duplicate tool calls eliminated via caching
   - LLM receiving rich, accurate data

3. **Infrastructure** - ✅ **COMPLETE**
   - Logging architecture implemented and documented
   - UI event display fixed (token/chars counts, LLM calls)
   - Phase tracking in logs working
   - Event system fully functional

4. **Testing Infrastructure** - ✅ **COMPLETE**
   - Phase 2-4 tests passing
   - Phase 5 integration tests created (ready for NiFi execution)
   - LLM mocking in place

---

## 🔴 Remaining Work

### 1. Documentation Generation Output (DocumentationNode)

**Goal**: Ensure the DocumentationNode produces high-quality, structured documentation from the rich analysis data.

**Current Status**: Analysis phase produces excellent data, but final documentation quality needs verification and improvement.

**Tasks**:

#### 1.1 Verify DocumentationNode Inputs
- [ ] Add debug mode to `test_doc_workflow_auto.py` (`--debug-shared`):
  - Fetch `/api/chat/workflows/{request_id}` after workflow completes
  - Print: `len(pg_summaries)`, keys, and 1-2 sample summaries
  - Print presence/keys of `doc_sections` and size of `final_document`
  - Verify `DocumentationNode` sees the same `pg_summaries` that analysis produced

#### 1.2 Verify doc_sections Population
- [ ] Check that each section is being created and stored:
  - Executive summary section
  - Mermaid diagram section
  - IO endpoints table section
  - Error handling table section
- [ ] Verify sections are stored in `shared["doc_sections"]` correctly

#### 1.3 Verify final_document Assembly
- [ ] Confirm `assemble_hierarchical_document()` is called
- [ ] Verify result is written to `shared["final_document"]`
- [ ] Ensure API layer reads from correct field (`final_document`)
- [ ] Verify test script can retrieve and display the document

#### 1.4 Improve Prompts (After Data Verification)
- [ ] Review and tune LLM prompts in `DocumentationNode`:
  - Ensure prompts explicitly reference PG/processor names
  - Add instructions to use IO endpoint details from analysis
  - Emphasize using error handling paths in documentation
  - Improve instructions for business logic description
- [ ] Test prompt changes with real workflow runs
- [ ] Compare before/after documentation quality

**Priority**: **HIGH** - This is the final output that users see

---

### 2. Enhanced Testing & Debugging

**Goal**: Make it easier to test and debug individual phases and verify data quality.

**Tasks**:

#### 2.1 Extend test_doc_workflow_auto.py
- [ ] Add `--debug-shared` flag:
  - Fetch workflow result from API
  - Print internal state (pg_summaries, doc_sections, final_document)
  - Show data structure and sample content
  
- [ ] Add `--mode` flags for phase-specific testing:
  - `--mode discovery`: Validate `flow_graph`, `pg_tree`, `max_depth`, component counts
  - `--mode analysis`: Validate presence & quality of `pg_summaries`, IO endpoints, error handling
  - `--mode generation`: Validate `doc_sections` and `final_document` content
  
- [ ] Add selective assertions:
  - Each mode only asserts what's relevant for that phase
  - Allows quick iteration on specific concerns
  
- [ ] Improve debugging output:
  - Always print `request_id``
  - Optionally dump JSON snapshot to `logs/debug-workflow-{request_id}.json`
  - Include timestamps and phase durations

**Priority**: **MEDIUM** - Improves development velocity

---

### 3. Error Handling Improvements

**Goal**: Ensure critical failures are properly detected and reported.

**Tasks**:

#### 3.1 Critical PG Failure Detection
- [ ] Add logic to detect when critical PGs fail:
  - Root PG (depth 0) fails processor/connection listing → mark workflow as error
  - Top-level child PGs (depth 1) fail → consider marking as error or warning
  - Document failures in "Incomplete Coverage" / "Limitations" section
  
- [ ] Ensure error status propagates correctly:
  - DiscoveryNode detects critical failure → returns "error"
  - Workflow routes to DocumentationNode to create error document
  - API saves status as "error" (already implemented)

#### 3.2 Error Documentation
- [ ] Add "Limitations" section to final document when:
  - Critical PGs failed discovery
  - Partial data was recovered
  - Invalid processors were encountered
- [ ] Include specific error messages and affected PGs

**Priority**: **MEDIUM** - Improves reliability and user experience

---

### 4. Polish & Regression Guard

**Goal**: Make the workflow stable, maintainable, and regression-resistant.

**Tasks**:

#### 4.1 Golden Flow Test Suite
- [ ] Create small test PGs with known behavior:
  - Simple linear flow (3-5 processors)
  - Branching flow (RouteOnAttribute with multiple paths)
  - Nested PG flow (parent with 2-3 children)
  - HTTP server flow (HandleHttpRequest → processing → HandleHttpResponse)
  
- [ ] Add automated tests that validate:
  - Discovery captures all components correctly
  - Analysis produces accurate summaries
  - Documentation includes expected sections and content

#### 4.2 Regression Tests
- [ ] Add pytest tests around `test_doc_workflow_auto.py`:
  - Test script execution with mocked API responses
  - Verify debug mode output format
  - Test phase-specific modes
  - Validate error handling paths

#### 4.3 Documentation Updates
- [ ] Update this plan as items are completed
- [ ] Document any new patterns or best practices discovered
- [ ] Keep architecture documentation current

**Priority**: **LOW** - Important for long-term maintainability

---

## Implementation Order

1. **First**: Documentation Generation Verification (1.1-1.3)
   - Verify data flow from analysis to documentation
   - Ensure all sections are created
   - Fix any wiring issues

2. **Second**: Prompt Improvements (1.4)
   - After verifying data is correct, improve prompts
   - Test and iterate on documentation quality

3. **Third**: Enhanced Testing (2.1)
   - Add debug modes and phase-specific testing
   - Makes future debugging easier

4. **Fourth**: Error Handling (3.1-3.2)
   - Improve failure detection and reporting

5. **Fifth**: Polish (4.1-4.3)
   - Add regression tests and golden flows
   - Long-term stability

---

## Success Criteria

- [ ] DocumentationNode produces high-quality, structured output
- [ ] All doc_sections are populated correctly
- [ ] final_document is assembled and accessible via API
- [ ] Test script can debug individual phases
- [ ] Critical failures are detected and reported
- [ ] Regression tests prevent future breakage

---

## Notes

- **Analysis phase is optimal** - focus should be on documentation generation
- **Discovery phase is optimal** - no changes needed
- **Infrastructure is complete** - logging, events, UI all working
- **Testing infrastructure exists** - just needs enhancement for debugging

---

## Related Documentation

- `analysis-phase-final-verification.md` - Analysis phase status (✅ OPTIMAL)
- `discovery-gaps-fixes.md` - Discovery fixes applied
- `logging-architecture.md` - Logging system documentation
- `ui-event-fixes.md` - UI display fixes

