# Property Extraction Strategy: Balancing Completeness vs. Token Efficiency

## The Challenge

We need to extract processor properties for LLM documentation, but face a trade-off:
- **Comprehensive**: Pass ALL properties → High token usage, potential model overload, noise
- **Curated**: Extract only "important" properties → Token-efficient but brittle, may miss critical details

## Proposed Multi-Tier Strategy

### Tier 1: Heuristic-Based Extraction (Primary - ~80% of cases)

Extract properties that are **likely to be business logic** using pattern matching:

```python
def extract_business_properties_heuristic(properties: Dict) -> Dict:
    """Extract properties using heuristics - works for any processor type."""
    result = {}
    
    for prop_name, prop_value in properties.items():
        if not prop_value or prop_value == "":
            continue
            
        # Skip common non-business properties
        if prop_name.lower() in ["routing strategy", "scheduling strategy", "run duration"]:
            continue
        
        # Pattern 1: Expression Language (${...})
        # Indicates dynamic values, attribute references, business logic
        if isinstance(prop_value, str) and "${" in prop_value:
            result[prop_name] = prop_value
            
        # Pattern 2: JSONPath ($[...] or $."...")
        # Indicates field extraction from JSON
        elif isinstance(prop_value, str) and prop_value.startswith("$"):
            result[prop_name] = prop_value
            
        # Pattern 3: URLs (http://, https://, file://, etc.)
        # Indicates external system interactions
        elif isinstance(prop_value, str) and any(
            prop_value.startswith(prefix) 
            for prefix in ["http://", "https://", "file://", "ftp://", "sftp://"]
        ):
            result[prop_name] = prop_value
            
        # Pattern 4: File paths (absolute or relative)
        # Indicates file system interactions
        elif isinstance(prop_value, str) and (
            prop_value.startswith("/") or 
            "\\" in prop_value or
            prop_value.count("/") > 2
        ):
            result[prop_name] = prop_value
            
        # Pattern 5: SQL-like queries (SELECT, INSERT, UPDATE, etc.)
        # Indicates database interactions
        elif isinstance(prop_value, str) and any(
            keyword in prop_value.upper() 
            for keyword in ["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"]
        ):
            result[prop_name] = prop_value
            
        # Pattern 6: Non-empty, non-default values for known business properties
        # Property names that suggest business logic
        elif any(keyword in prop_name.lower() for keyword in [
            "query", "sql", "script", "spec", "transform", "rule", 
            "condition", "filter", "expression", "path", "url", "endpoint",
            "topic", "queue", "table", "database", "schema"
        ]):
            result[prop_name] = prop_value
            
    return result
```

**Advantages:**
- Works for ANY processor type (no hardcoding needed)
- Catches most business logic automatically
- Token-efficient (only extracts meaningful properties)

**Limitations:**
- May miss processor-specific nuances
- Some false positives (e.g., file paths in comments)

### Tier 2: Processor-Specific Templates (Fallback - ~15% of cases)

For processors where heuristics might miss important details, use type-specific extraction:

```python
PROCESSOR_TEMPLATES = {
    "RouteOnAttribute": {
        "include_all": True,  # All properties except "Routing Strategy"
        "exclude": ["Routing Strategy"],
        "priority": ["MT7xx", "NonMT7xx", "unmatched"]  # Common routing property names
    },
    "InvokeHTTP": {
        "include": ["Remote URL", "HTTP Method", "Request Content-Type", "Authorization"],
        "extract_expressions": True  # Also extract any Expression Language
    },
    "ExecuteScript": {
        "include": ["Script Body"],
        "truncate": {"Script Body": 1000}  # Truncate long scripts
    },
    # ... more templates
}
```

**Advantages:**
- Captures processor-specific important properties
- Can handle truncation for large values (scripts, specs)
- Can prioritize certain properties

**Limitations:**
- Requires maintenance for each processor type
- May become outdated as NiFi adds new processors

### Tier 3: Full Property Fallback (Edge Cases - ~5% of cases)

For unknown processors or when heuristics fail, provide a fallback:

```python
def extract_with_fallback(processor_type: str, properties: Dict, config: Dict) -> Dict:
    """Multi-tier extraction with fallback."""
    
    # Try heuristic first (fast, works for most)
    heuristic_result = extract_business_properties_heuristic(properties)
    
    # If heuristic found nothing, try processor template
    if not heuristic_result and processor_type in PROCESSOR_TEMPLATES:
        template_result = extract_using_template(processor_type, properties)
        if template_result:
            return template_result
    
    # If still nothing and config allows, return all non-empty properties
    if not heuristic_result and config.get("extraction_mode") == "comprehensive":
        return {k: v for k, v in properties.items() if v and v != ""}
    
    return heuristic_result
```

**Advantages:**
- Ensures we never miss everything
- Configurable verbosity

**Limitations:**
- Can be token-heavy if used too often

## Configuration Options

Add configuration to control extraction behavior:

```yaml
# config.yaml
documentation_workflow:
  analysis:
    property_extraction:
      mode: "balanced"  # "minimal", "balanced", "comprehensive"
      max_properties_per_processor: 10  # Limit to prevent token bloat
      truncate_large_values: true
      max_value_length: 500
      include_defaults: false  # Skip properties with default values
  output:
    save_shared_state: true  # Save generation shared state snapshot
```

**Modes:**
- **minimal**: Only heuristics, very token-efficient
- **balanced**: Heuristics + templates (default, recommended)
- **comprehensive**: Heuristics + templates + fallback to all properties

**User Control:**
- Users can configure `mode` in `config.yaml` or via API
- Default is `"balanced"` for optimal token efficiency vs. completeness
- See `docs/flow-doc-workflow/token-tracking-and-metrics.md` for details

## Smart Filtering by Property Value

Even when extracting properties, filter out noise:

```python
def is_meaningful_property(prop_name: str, prop_value: Any) -> bool:
    """Determine if a property is worth including."""
    
    # Skip empty/null
    if not prop_value or prop_value == "":
        return False
    
    # Skip default values (common NiFi defaults)
    defaults = ["0 sec", "1", "false", "true", "UTF-8", "ALL"]
    if str(prop_value).strip() in defaults:
        return False
    
    # Skip very long values (likely binary or encoded data)
    if isinstance(prop_value, str) and len(prop_value) > 2000:
        return False
    
    # Skip properties that are clearly internal/config
    internal_keywords = ["version", "revision", "bundle", "validation"]
    if any(kw in prop_name.lower() for kw in internal_keywords):
        return False
    
    return True
```

## Token Budget Management

Implement per-processor-group token limits:

```python
def extract_with_token_budget(
    processors: List[Dict], 
    max_tokens: int = 4000
) -> Dict[str, Dict]:
    """Extract properties with token budget management."""
    
    results = {}
    tokens_used = 0
    
    # Sort processors by importance (IO processors first, then LOGIC, etc.)
    sorted_procs = sorted(processors, key=lambda p: _processor_importance(p))
    
    for proc in sorted_procs:
        # Estimate tokens for this processor's properties
        proc_props = extract_business_properties(proc)
        estimated_tokens = estimate_tokens(json.dumps(proc_props))
        
        if tokens_used + estimated_tokens > max_tokens:
            # Truncate or skip less important processors
            if _processor_importance(proc) < 0.5:
                continue
            # Or truncate properties
            proc_props = _truncate_properties(proc_props, max_tokens - tokens_used)
        
        results[proc["id"]] = proc_props
        tokens_used += estimate_tokens(json.dumps(proc_props))
    
    return results
```

## Discovery Phase Property Storage

**Current Issue:** Properties appear empty in `flow_graph.processors`

**Root Cause:** Discovery uses `list_nifi_objects` which returns minimal processor info (name, type, state), not full properties.

**Solution Options:**

1. **Store properties during discovery** (comprehensive but memory-heavy):
   ```python
   # In DiscoveryNode, fetch full details for processors
   proc_details = await nifi_client.get_processor_details(proc_id)
   processor["properties"] = proc_details.get("component", {}).get("config", {}).get("properties", {})
   ```

2. **Lazy fetch during analysis** (current approach, but needs fixing):
   - Analysis phase already fetches via `get_nifi_object_details`
   - But properties might not be included in `doc_optimized` format
   - Need to ensure `include_properties=True` is passed

3. **Hybrid approach** (recommended):
   - Discovery: Store processor metadata (name, type, relationships, parent_pg_id)
   - Analysis: Fetch full properties only when needed (via `get_nifi_object_details`)
   - Cache fetched properties to avoid re-fetching

## Implementation Plan

### Phase 1: Enhance Heuristic Extraction
1. Implement `extract_business_properties_heuristic()` function
2. Replace hardcoded processor types with heuristics as primary method
3. Keep processor templates as fallback

### Phase 2: Add Configuration
1. Add `property_extraction` config section
2. Implement mode selection (minimal/balanced/comprehensive)
3. Add token budget management

### Phase 3: Fix Discovery Property Storage
1. Investigate why properties are empty in discovery phase
2. Either store during discovery or ensure analysis phase fetches correctly
3. Add caching to avoid redundant fetches

### Phase 4: Smart Filtering
1. Implement `is_meaningful_property()` filtering
2. Add property value truncation for large values
3. Skip default/empty values

## Expected Outcomes

**Token Efficiency:**
- Heuristic extraction: ~50-70% reduction vs. all properties
- Smart filtering: Additional ~20-30% reduction
- Total: ~70-80% token savings while retaining business logic

**Completeness:**
- Heuristics catch ~80% of business logic automatically
- Templates catch processor-specific cases (~15%)
- Fallback ensures we never miss everything (~5%)

**Maintainability:**
- No need to hardcode every processor type
- Heuristics work for new/unknown processors
- Templates only needed for special cases

## Example: Before vs. After

**Before (All Properties - ~2000 tokens):**
```json
{
  "processor": "InvokeHTTP",
  "properties": {
    "Remote URL": "https://api.example.com/data",
    "HTTP Method": "POST",
    "Connection Timeout": "5 secs",
    "Socket Read Timeout": "15 secs",
    "Request Content-Type": "application/json",
    "Request Date Header Enabled": "True",
    "Response Body Attribute Name": null,
    "Response Body Attribute Size": "2048",
    "Response Cache Enabled": "false",
    "Response Cache Size": "10MB",
    "Response Cookie Strategy": "DISABLED",
    "Response Generation Required": "false",
    "Response FlowFile Naming Strategy": "RANDOM",
    "Response Header Request Attributes Enabled": "true",
    "Response Redirects Enabled": "True",
    "Authorization": "${authToken}",
    "HTTP/2 Disabled": "False",
    "SSL Context Service": null,
    "proxy-configuration-service": null,
    "Request OAuth2 Access Token Provider": null,
    "OAuth2 Access Token Refresh Strategy": "ON_TOKEN_EXPIRATION",
    "Request Username": null,
    "Request Password": null,
    "Request Digest Authentication Enabled": "false",
    "Request Failure Penalization Enabled": "false",
    "Request Body Enabled": "true",
    "Request Multipart Form-Data Name": null,
    "Request Multipart Form-Data Filename Enabled": "true",
    "Request Chunked Transfer-Encoding Enabled": "false",
    "Request Content-Encoding": "DISABLED",
    "Request Header Attributes Pattern": null,
    "Request User-Agent": null
  }
}
```

**After (Heuristic + Template - ~300 tokens):**
```json
{
  "processor": "InvokeHTTP",
  "properties": {
    "Remote URL": "https://api.example.com/data",
    "HTTP Method": "POST",
    "Request Content-Type": "application/json",
    "Authorization": "${authToken}"
  }
}
```

**Savings:** ~85% token reduction while retaining all business-critical information.

