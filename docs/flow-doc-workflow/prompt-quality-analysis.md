# LLM Prompt and Data Quality Analysis

## Summary

After reviewing `logs/llm_debug.log`, I've identified several issues affecting the quality of generated documentation:

## Issues Found

### 1. **Child Summary Truncation in Parent PG Prompts** (CRITICAL)
**Location**: `doc_nodes.py:1190`
**Problem**: Child summaries are truncated to 200 characters: `cs.get("purpose", cs.get("summary", "")[:200])`
**Impact**: Parent PG summaries lose critical context about what child components do
**Example from log**: 
- "It transf" (truncated from "It transforms...")
- "It process" (truncated from "It processes...")

**Fix**: Remove truncation or increase to at least 500 chars

### 2. **Mermaid Diagram Purpose Truncation** (HIGH)
**Location**: `doc_nodes.py:2334`
**Problem**: Purpose fields truncated to only 50 characters: `summary.get("summary", "")[:50]`
**Impact**: Diagram generation loses context about what each PG does
**Example from log**: Purpose shows as "The \"Narrative SWIFT Screening\" process group is d" (cut off)

**Fix**: Remove truncation or increase to at least 200 chars

### 3. **IO Endpoint Deduplication Losses Context** (MEDIUM)
**Location**: `doc_nodes.py:2280-2290`
**Problem**: IO endpoints deduplicated by `processor:direction` key, losing which PG they belong to
**Impact**: Executive Summary can't distinguish between similar processors in different PGs
**Example**: Two "Response Handler" PGs both have "Respond with success" - deduplication loses which is which

**Fix**: Include PG name in deduplication key or keep PG context in the endpoint data

### 4. **Missing Error Handling Context** (MEDIUM)
**Problem**: Error handling details not included in Executive Summary or parent PG prompts
**Impact**: Generated summaries don't mention error handling strategies
**Fix**: Include error_handling data in prompts

### 5. **Generic IO Endpoint Details** (LOW - Already Fixed)
**Status**: Previously fixed - HTTP server endpoints now include full URL, port, path, methods
**Note**: The log shows good endpoint details (e.g., `http://localhost:7008/api/transaction/screen`)

## Prompt Quality Assessment

### ✅ **Good Prompts**

1. **PG_SUMMARY_PROMPT** (Leaf PGs):
   - Clear structure
   - Explicitly asks for external systems, URLs, paths
   - Good IO endpoint details included
   - **Response Quality**: Good - includes specific URLs and status codes

2. **HIERARCHICAL_SUMMARY_PROMPT** (Executive Summary):
   - Very clear requirements
   - Explicitly asks for specific endpoint details
   - Good structure with numbered requirements
   - **Response Quality**: Good - includes URL, methods, status codes

### ⚠️ **Prompts Needing Improvement**

1. **PG_WITH_CHILDREN_PROMPT** (Parent PGs):
   - Child summaries are truncated (200 chars)
   - Missing error handling context
   - **Response Quality**: Adequate but could be better with full child summaries

2. **HIERARCHICAL_DIAGRAM_PROMPT** (Mermaid):
   - Purpose fields too short (50 chars)
   - **Response Quality**: Adequate but diagram structure could be clearer

## Recommendations

### Immediate Fixes (High Priority)

1. **Remove/Increase Truncation**:
   - Child summaries: 200 → 500+ chars (or remove)
   - Mermaid purposes: 50 → 200+ chars (or remove)

2. **Improve IO Endpoint Context**:
   - Include PG name in deduplication or keep PG context
   - Add processor count per endpoint type

3. **Add Error Handling to Prompts**:
   - Include error_handling data in parent PG prompts
   - Mention error handling in Executive Summary prompt

### Medium Priority

4. **Enhance Parent PG Prompt**:
   - Add more context about data flow between children
   - Include processor counts per child
   - Add error handling summary

5. **Improve Mermaid Prompt**:
   - Include more context about PG purposes
   - Add IO endpoint indicators in diagram
   - Clarify subgraph vs node distinction (already improved)

## Data Quality Observations

### ✅ **Good Data**

- IO endpoints include detailed properties (URLs, status codes, methods)
- Processor categorization is working
- Processor references are properly formatted
- Full summaries are now included in Executive Summary (after recent fix)

### ⚠️ **Data Quality Issues**

- Child summaries truncated in parent prompts
- Purpose fields truncated in Mermaid prompt
- IO endpoint deduplication loses PG context
- Error handling not included in generation prompts

## Next Steps

1. Fix truncation issues (lines 1190, 2334, 2344)
2. Improve IO endpoint deduplication to preserve PG context
3. Add error handling to generation prompts
4. Test with full data to verify quality improvements

