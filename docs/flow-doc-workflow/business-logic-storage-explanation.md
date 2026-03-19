# Business Logic Storage: Current State and Opportunities

## Point 3 Explanation: Storing Business Logic in pg_summaries

### Current Situation

**What We Have:**
- During analysis phase, we extract business logic (routing conditions, JSONPath expressions) from processors
- This data is passed to the LLM in prompts (e.g., `business_logic_json` in `PG_SUMMARY_PROMPT`)
- The LLM incorporates this into the summary text (e.g., "routes messages starting with 'MT7'")
- **BUT**: The actual expressions are NOT stored in `pg_summaries` - only the LLM's summary text is stored

**What's Stored in pg_summaries:**
```json
{
  "name": "Top Level Listener",
  "summary": "routes messages starting with 'MT7' differently...",  // LLM text
  "io_endpoints": [...],
  "error_handling": [...],
  "categories": {...}
  // ❌ NO business_logic field!
}
```

**What's Available During Analysis (but not stored):**
```json
{
  "business_logic": [
    {
      "processor": "RouteOnAttribute",
      "type": "RouteOnAttribute",
      "properties": {
        "MT7xx": "${http.method:equals(\"POST\"):and(${messageType:startsWith(\"MT7\")})}",
        "NonMT7xx": "${http.method:equals(\"POST\"):and(${messageType:startsWith(\"MT7\"):equals(\"false\")})}"
      }
    },
    {
      "processor": "EvaluateJsonPath",
      "type": "EvaluateJsonPath",
      "properties": {
        "messageType": "$[\"MessageType\"]",
        "transactionID": "$[\"Transaction ID\"]"
      }
    }
  ]
}
```

### The Problem

**Current hierarchy_doc output:**
```markdown
## Top Level Listener

The Top Level Listener routes messages starting with "MT7" differently from others...

### External Interactions
- Listen for incoming requests: `http://localhost:7008/api/transaction/screen`
```

**What's Missing:**
- The actual routing conditions: `${messageType:startsWith("MT7")}`
- The actual JSONPath expressions: `$["MessageType"]`, `$["Transaction ID"]`
- Which processor implements which logic
- The exact conditions for each route

**Why This Matters:**
1. **Precision**: LLM summary says "routes based on messageType" but doesn't show the exact condition
2. **Debugging**: Developers need the actual expressions to understand behavior
3. **Completeness**: Documentation should show both human-readable summary AND technical details
4. **Verification**: Users can't verify if the LLM correctly interpreted the logic

### Example: What We Could Show

**Enhanced hierarchy_doc (if we stored business_logic):**
```markdown
## Top Level Listener

The Top Level Listener routes messages starting with "MT7" differently from others...

### Business Logic

**Routing Rules (RouteOnAttribute):**
- **MT7xx route**: `${http.method:equals("POST"):and(${messageType:startsWith("MT7")})}`
  - Routes POST requests where messageType starts with "MT7"
- **NonMT7xx route**: `${http.method:equals("POST"):and(${messageType:startsWith("MT7"):equals("false")})}`
  - Routes POST requests where messageType does NOT start with "MT7"

**Field Extractions (EvaluateJsonPath):**
- **messageType**: Extracted from JSON path `$["MessageType"]`
- **transactionID**: Extracted from JSON path `$["Transaction ID"]`

### External Interactions
- Listen for incoming requests: `http://localhost:7008/api/transaction/screen`
```

### Implementation Options

#### Option 1: Store business_logic in pg_summaries (Recommended)

**Modify `_analyze_pg_direct` and `_analyze_pg_with_summaries` to include:**
```python
return {
    "name": pg.get("name"),
    "id": pg.get("id"),
    "virtual": False,
    "summary": response.get("content", ""),
    "processor_count": len(processors),
    "categories": categorized,
    "io_endpoints": io_endpoints,
    "error_handling": error_handling,
    "business_logic": business_logic  # ✅ ADD THIS
}
```

**Then in `_build_hierarchical_doc`, add:**
```python
# Business Logic section
business_logic = summary.get("business_logic", [])
if business_logic:
    lines.append("### Business Logic")
    lines.append("")
    
    # Group by processor type
    routing_rules = [bl for bl in business_logic if bl.get("type") == "RouteOnAttribute"]
    jsonpath_extractions = [bl for bl in business_logic if bl.get("type") == "EvaluateJsonPath"]
    attribute_updates = [bl for bl in business_logic if bl.get("type") == "UpdateAttribute"]
    
    if routing_rules:
        lines.append("**Routing Rules:**")
        for rule in routing_rules:
            lines.append(f"- **{rule.get('processor')}**:")
            for prop_name, prop_value in rule.get("properties", {}).items():
                lines.append(f"  - `{prop_name}`: `{prop_value}`")
        lines.append("")
    
    if jsonpath_extractions:
        lines.append("**Field Extractions:**")
        for extraction in jsonpath_extractions:
            lines.append(f"- **{extraction.get('processor')}**:")
            for prop_name, prop_value in extraction.get("properties", {}).items():
                lines.append(f"  - `{prop_name}`: `{prop_value}`")
        lines.append("")
```

**Pros:**
- ✅ Complete technical details available
- ✅ Can show exact expressions
- ✅ Better for debugging and verification
- ✅ Documentation is more comprehensive

**Cons:**
- ⚠️ Increases `pg_summaries` size (but business_logic is relatively small)
- ⚠️ Need to update both `_analyze_pg_direct` and `_analyze_pg_with_summaries`

#### Option 2: Re-extract from flow_graph during generation

**In `_build_hierarchical_doc`, fetch processor details again:**
```python
# Get processor IDs for this PG from pg_tree
pg = pg_tree.get(root_pg_id, {})
processor_ids = [...]  # Extract from flow_graph

# Fetch processor details
proc_details = await self.call_nifi_tool(...)
business_props = extract_business_properties(proc_details)
```

**Pros:**
- ✅ No need to store in pg_summaries
- ✅ Always fresh data

**Cons:**
- ❌ Duplicate API calls (already fetched during analysis)
- ❌ Slower generation phase
- ❌ More complex code

#### Option 3: Hybrid - Store in pg_summaries but make it optional

**Add config option:**
```yaml
documentation_workflow:
  analysis:
    store_business_logic: true  # Store detailed business logic in summaries
```

**Pros:**
- ✅ User control
- ✅ Can disable if token/storage is a concern

**Cons:**
- ⚠️ More configuration complexity

### Recommendation

**Implement Option 1** - Store `business_logic` in `pg_summaries` because:

1. **Data is already extracted** - We're already building `business_logic` for the LLM prompt
2. **Small storage cost** - Business logic is typically small (a few KB per PG)
3. **High value** - Technical details are critical for documentation quality
4. **No performance impact** - We're not re-fetching data, just storing what we already have

### Implementation Steps

1. **Update `_analyze_pg_direct`** to include `business_logic` in return dict
2. **Update `_analyze_pg_with_summaries`** to include `business_logic` (for parent PGs with processors)
3. **Update `_analyze_virtual_group`** to include `business_logic` (for virtual groups)
4. **Update `_build_hierarchical_doc`** to display business logic in structured format
5. **Test** with a real flow to verify business logic appears correctly

### Expected Impact

**Before:**
- Documentation says "routes based on messageType" (vague)
- No way to see exact conditions
- Can't verify LLM interpretation

**After:**
- Documentation shows exact routing expressions
- JSONPath expressions visible
- Technical details alongside human-readable summary
- Better for developers and debugging

### Trade-offs

**Storage:**
- Each PG summary: ~1-5 KB additional (business_logic typically 10-50 properties)
- For 10 PGs: ~10-50 KB total
- Negligible compared to full shared state (~5 MB)

**Token Usage:**
- Business logic is NOT sent to LLM in generation phase (only in analysis phase)
- No impact on generation token costs
- Only stored for documentation formatting

**Maintenance:**
- Need to ensure business_logic is included in all analysis methods
- Need to handle cases where business_logic is empty

