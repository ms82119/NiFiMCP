# Analysis Phase Enhancement Strategy

## Current Problem

The analysis phase is producing **generic, superficial summaries** instead of **deep, technical analysis** with quoted expressions and logic chain explanations.

### Current Prompt Issues

**What we're asking:**
```
Write a 2-3 sentence summary that explains:
1. What this process group does (its purpose)
2. What data it processes or transforms
3. Key business logic or decisions it implements
...
Keep it concise and non-technical where possible.
```

**What we're getting:**
- Generic statements: "routes messages starting with 'MT7'"
- No quoted expressions
- No logic chain explanations
- No technical details
- Brief, high-level summaries

**What we need:**
- Detailed analysis with quoted expressions: `"routes using expression: ${messageType:startsWith(\"MT7\")}"`
- Logic chain explanations: "First, EvaluateJsonPath extracts messageType, then RouteOnAttribute uses it to route..."
- Technical depth: Show how processors work together
- Port-to-port flow: Explain how data moves between PGs via ports

## Root Cause Analysis

### Issue 1: Prompt Instructions Are Too Brief

**Current:**
- "Write a 2-3 sentence summary"
- "Keep it concise and non-technical"

**Problem:**
- LLM follows instructions literally → produces brief, generic text
- No incentive to quote expressions or explain logic chains
- "Non-technical" discourages showing technical details

**Solution:**
- Change to "Write a comprehensive 5-8 paragraph analysis"
- Remove "non-technical" instruction
- Explicitly ask for quoted expressions and logic chains

### Issue 2: Missing Port-to-Port Flow Context

**Current State:**
- Ports are discovered and stored in `flow_graph.ports`
- Connections through ports are in `flow_graph.connections` (with `sourceType: "OUTPUT_PORT"`, `destinationType: "INPUT_PORT"`)
- **BUT**: Port connections are NOT being passed to LLM in analysis prompts

**What's Missing:**
- Which PGs connect via ports
- What data flows through which ports
- The sequence of flow between PGs
- Port names and their purposes

**Example from your data:**
```json
{
  "sourceType": "OUTPUT_PORT",
  "sourceName": "MT7xx Extract Narrative",
  "destinationType": "INPUT_PORT",
  "destinationName": "Non-MT7xx In"
}
```

This shows data flows from "Top Level Listener" → "Narrative Extraction API" via ports, but the LLM doesn't see this!

### Issue 3: Logic Chains Not Explained

**Current:**
- LLM sees individual processors and their properties
- LLM sees connections between processors
- **BUT**: LLM is asked to "summarize" not "analyze the logic chain"

**What's Missing:**
- How data flows through processor sequence
- How one processor's output affects the next
- How routing decisions cascade
- How attribute extractions are used downstream

**Example Logic Chain (not being explained):**
1. `HandleHttpRequest` receives POST request
2. `EvaluateJsonPath` extracts `messageType` using `$["MessageType"]`
3. `RouteOnAttribute` uses extracted `messageType` in condition `${messageType:startsWith("MT7")}`
4. Routes to different paths based on condition
5. Each path processes differently

**Current output:** "Routes messages based on messageType"

**Needed output:** "The flow begins with HandleHttpRequest receiving POST requests. EvaluateJsonPath extracts the messageType field using JSONPath expression `$["MessageType"]`. This extracted value is then used by RouteOnAttribute processor, which implements the routing condition `${messageType:startsWith("MT7")}`. Messages matching this condition (starting with 'MT7') are routed to the 'MT7xx' relationship, while others are routed to 'NonMT7xx'. This creates two distinct processing paths..."

### Issue 4: Business Logic Not Quoted

**Current:**
- Business logic is passed to LLM in `business_logic_json`
- LLM is asked to "document routing conditions"
- **BUT**: LLM paraphrases instead of quoting

**What's Missing:**
- Actual expressions quoted verbatim
- Technical precision
- Ability to verify LLM interpretation

**Example:**
- **Current:** "routes based on messageType field starting with 'MT7'"
- **Needed:** "routes using expression `${messageType:startsWith("MT7")}` which checks if the messageType attribute starts with the string 'MT7'"

## Proposed Solution

### 1. Transform Prompts from "Summary" to "Deep Analysis"

**Current Prompt Structure:**
```
Write a 2-3 sentence summary that explains:
1. What this process group does
2. What data it processes
3. Key business logic
Keep it concise and non-technical.
```

**New Prompt Structure:**
```
Write a comprehensive 5-8 paragraph technical analysis that:

1. **Purpose and Overview** (1 paragraph):
   - What this process group does and why it exists
   - Its role in the overall data flow

2. **Data Flow Sequence** (2-3 paragraphs):
   - Trace the exact path data takes through processors
   - Explain how processors are connected (use connections_json)
   - Show the sequence: Processor A → Processor B → Processor C
   - Explain what happens at each step

3. **Business Logic Analysis** (2-3 paragraphs):
   - For each routing rule: Quote the EXACT expression and explain what it does
     Example: "RouteOnAttribute implements condition `${messageType:startsWith("MT7")}` which routes messages where the messageType attribute begins with 'MT7'"
   - For each field extraction: Quote the EXACT JSONPath and explain what field is extracted
     Example: "EvaluateJsonPath extracts messageType using expression `$["MessageType"]` which reads the MessageType field from the JSON payload"
   - For each transformation: Explain what transformation occurs and why
   - Explain how these logic elements work together to create the overall behavior

4. **External Interactions** (1 paragraph):
   - List all external systems, URLs, file paths, databases
   - Explain what data is sent/received from each

5. **Error Handling** (1 paragraph):
   - Explain how errors are handled (or not handled)
   - Note any unhandled error relationships

CRITICAL REQUIREMENTS:
- Quote ALL expressions verbatim (routing conditions, JSONPath, Expression Language)
- Explain logic chains: Show how data flows from one processor to the next
- Be technical and precise - this is for developers
- Minimum 5 paragraphs, maximum 8 paragraphs
- Include specific processor names, expressions, and technical details
```

### 2. Add Port-to-Port Flow Information

**Enhance `_format_connections_for_prompt` to include port connections:**

```python
def _format_connections_for_prompt(
    self,
    connections: List[Dict],
    cached_proc_details: Optional[Dict[str, Dict]] = None,
    ports: Optional[Dict[str, Dict]] = None,  # NEW: Include ports
    pg_tree: Optional[Dict[str, Dict]] = None  # NEW: For PG names
) -> List[Dict]:
    """Format connections including port-to-port connections between PGs."""
    formatted = []
    
    for conn in connections:
        comp = conn.get("component", {})
        source = comp.get("source", {})
        dest = comp.get("destination", {})
        
        source_type = source.get("type", "")
        dest_type = dest.get("type", "")
        
        # Handle port-to-port connections (cross-PG flow)
        if source_type == "OUTPUT_PORT" or dest_type == "INPUT_PORT":
            source_pg_id = source.get("groupId")
            dest_pg_id = dest.get("groupId")
            
            source_pg_name = pg_tree.get(source_pg_id, {}).get("name", "Unknown PG") if pg_tree else "Unknown PG"
            dest_pg_name = pg_tree.get(dest_pg_id, {}).get("name", "Unknown PG") if pg_tree else "Unknown PG"
            
            formatted.append({
                "type": "cross_pg_port_connection",
                "from_pg": source_pg_name,
                "from_port": source.get("name", "?"),
                "to_pg": dest_pg_name,
                "to_port": dest.get("name", "?"),
                "relationship": comp.get("selectedRelationships", [])
            })
        else:
            # Regular processor-to-processor connection
            # ... existing logic ...
```

**Add to prompt:**
```
Port Connections (data flow between process groups):
{port_connections_json}

If this process group has input/output ports, explain:
- What data enters via input ports and from which parent/child PGs
- What data exits via output ports and to which parent/child PGs
- The purpose of each port in the overall flow
```

### 3. Enhance Connection Formatting to Show Logic Chains

**Current:**
```json
[
  {"from": "ProcessorA", "to": "ProcessorB", "relationships": ["success"]}
]
```

**Enhanced:**
```json
[
  {
    "from": "HandleHttpRequest",
    "to": "EvaluateJsonPath",
    "relationships": ["success"],
    "data_flow": "HTTP request body → JSON extraction",
    "purpose": "Extract messageType and transactionID from incoming JSON"
  },
  {
    "from": "EvaluateJsonPath",
    "to": "RouteOnAttribute",
    "relationships": ["success"],
    "data_flow": "Extracted attributes → Routing decision",
    "purpose": "Use extracted messageType to determine routing path"
  }
]
```

### 4. Store Business Logic BUT Enhance LLM Analysis First

**Strategy:**
1. **First**: Enhance prompts to demand deep analysis with quoted expressions
2. **Test**: See if LLM produces better output with enhanced prompts
3. **Then**: Store business_logic in pg_summaries as backup/reference
4. **Use**: Stored business_logic for:
   - Verification (check if LLM correctly interpreted)
   - Fallback (if LLM doesn't quote, we can add it)
   - Technical appendix (show raw expressions alongside LLM analysis)

**Why this order:**
- If we store business_logic first, LLM might rely on it being available later → less incentive to analyze deeply now
- If we enhance prompts first, LLM is forced to deeply analyze → better quality
- Then storing it becomes a safety net, not a crutch

## Expected Outcomes

### Before (Current):
```
The Top Level Listener routes messages starting with "MT7" differently from others, 
and extracts important fields like messageType and transactionID from incoming JSON data.
```

### After (Enhanced):
```
The Top Level Listener process group serves as the entry point for incoming HTTP 
requests at `http://localhost:7008/api/transaction/screen`, accepting both GET and 
POST methods.

**Data Flow Sequence:**
The flow begins with the HandleHttpRequest processor ("Listen for incoming requests") 
receiving HTTP POST requests. The request body, containing JSON transaction data, is 
then passed to the EvaluateJsonPath processor. This processor extracts two critical 
fields using JSONPath expressions: `$["MessageType"]` extracts the messageType field, 
and `$["Transaction ID"]` extracts the transactionID field. These extracted attributes 
are added to the FlowFile's attribute map.

The extracted messageType attribute is then used by the RouteOnAttribute processor, 
which implements two routing conditions. The first condition, `${messageType:startsWith("MT7")}`, 
routes messages where the messageType begins with the string "MT7" to the "MT7xx" 
relationship. The second condition, `${messageType:startsWith("MT7"):equals("false")}`, 
routes all other messages to the "NonMT7xx" relationship. This creates two distinct 
processing paths based on the message type.

**Port Connections:**
Data exits this process group via three output ports: "MT7xx Extract Narrative", 
"Non MT7xx", and "Failure". The "MT7xx Extract Narrative" port sends MT7xx messages 
to the "Narrative Extraction API" child process group for narrative extraction. 
The "Non MT7xx" port routes non-MT7xx messages to the "Standard TS" child process 
group for standard transaction processing.

**Business Logic Details:**
- RouteOnAttribute processor implements routing logic using Expression Language:
  - MT7xx route: `${http.method:equals("POST"):and(${messageType:startsWith("MT7")})}`
    This condition requires both: (1) HTTP method is POST, AND (2) messageType starts with "MT7"
  - NonMT7xx route: `${http.method:equals("POST"):and(${messageType:startsWith("MT7"):equals("false")})}`
    This routes POST requests where messageType does NOT start with "MT7"
- EvaluateJsonPath extracts fields:
  - messageType: `$["MessageType"]` - reads MessageType field from JSON root
  - transactionID: `$["Transaction ID"]` - reads Transaction ID field from JSON root
```

## Implementation Priority

### Phase 1: Prompt Enhancement (High Priority)
1. Rewrite `PG_SUMMARY_PROMPT` to demand deep analysis
2. Remove "concise" and "non-technical" instructions
3. Add explicit requirements to quote expressions
4. Add logic chain explanation requirements
5. Test with current data to see improvement

### Phase 2: Port Connection Integration (High Priority)
1. Enhance `_format_connections_for_prompt` to include port connections
2. Add port connection section to prompts
3. Ask LLM to explain port-to-port flow
4. Test to verify port flow is explained

### Phase 3: Connection Enhancement (Medium Priority)
1. Enhance connection formatting to include data flow purpose
2. Add logic chain context to connections
3. Help LLM understand processor relationships better

### Phase 4: Business Logic Storage (Medium Priority)
1. Store business_logic in pg_summaries (as backup/verification)
2. Use stored business_logic to verify LLM analysis
3. Add technical appendix if LLM doesn't quote expressions

## Key Insights

1. **Prompt instructions drive output quality** - "2-3 sentences" → brief output. "5-8 paragraphs with quoted expressions" → detailed output.

2. **Port-to-port flow is critical** - This is how PGs communicate, but it's not being explained.

3. **Logic chains need explicit explanation** - LLM sees connections but isn't asked to explain the sequence and purpose.

4. **Quoting expressions is essential** - Generic descriptions lose precision. Actual expressions are needed for developers.

5. **Technical depth is valuable** - "Non-technical" instruction is counterproductive. Developers need technical details.

6. **Store business_logic as safety net, not primary source** - Enhance LLM analysis first, then store for verification/fallback.

## Questions to Consider

1. **Token budget**: Deeper analysis = more tokens. Should we:
   - Increase `max_tokens_per_analysis`?
   - Make analysis depth configurable?
   - Use different models for analysis vs. generation?

2. **Analysis quality vs. speed**: Deeper analysis takes longer. Is this acceptable?

3. **Port connection complexity**: Some flows have many port connections. How do we present this clearly?

4. **Logic chain length**: Some flows have 20+ processors. How do we explain long chains without overwhelming?

5. **Parent PG analysis**: Should parent PGs analyze their own processors AND explain how children connect via ports?

