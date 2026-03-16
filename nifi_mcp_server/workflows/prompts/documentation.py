"""
LLM prompt templates for hierarchical flow documentation workflow.

These prompts are designed for:
1. Token efficiency - minimal but sufficient context
2. Structured output - JSON where possible for parsing
3. Bottom-up hierarchical analysis
"""

# =============================================================================
# VIRTUAL SUB-FLOW IDENTIFICATION
# Used when a PG has >threshold processors to create logical groupings
# =============================================================================

VIRTUAL_SUBFLOW_PROMPT = """This NiFi Process Group has {proc_count} processors but needs logical grouping for documentation.

Processors (with categories):
{processor_summary}

Connections between processors:
{connection_summary}

Identify {min_groups}-{max_groups} logical sub-flows that group related processors.

Common patterns:
- Data Ingestion: Processors that fetch/receive external data
- Validation: Schema validation, data quality checks  
- Transformation: Format conversion, field mapping, enrichment
- Routing/Decision: Conditional branching, filtering
- Output/Delivery: Writing to external destinations
- Error Handling: Failure routes, retry logic, dead letter

Requirements:
- Every processor must belong to exactly ONE group
- Use the processor "id" field (8-char) in processor_ids
- Groups should represent logical stages in data processing

Return a JSON array:
```json
[
  {{
    "name": "Data Ingestion",
    "purpose": "Fetches records from SFTP and parses CSV format",
    "processor_ids": ["abc12345", "def67890"]
  }}
]
```

Return ONLY the JSON array, no other text."""


# =============================================================================
# PROCESS GROUP SUMMARY (for leaf PGs or virtual groups)
# Temporarily Removed: {pg_purpose}, {categories_json}, {io_endpoints_json},
# {business_logic_json}
# =============================================================================

PG_SUMMARY_PROMPT = """Write a technical analysis of this NiFi Process Group for documentation purposes.

Process Group: {pg_name}

Processors ({processor_count} total):
{processors_json}

Data Flow (connections between processors):
{connections_json}

Child summaries (if any):
{children_digest}

Write a technical analysis of this part of a NiFi flow that is used in an Anti Money Laundering (AML) system.  Accuracy and extreme brevity are top priority.

1. **Purpose and Overview**:
   - What this process group does
   - The type of data it processes

2. **Data Flow Sequence**:
   - Use the connections and processors information supplied
   - Find what looks to be the starting points (there could be multiple), and trace each through to their ends
   - Note their current state (RUNNING, STOPPED, DISABLED, etc.) as that could indicate if this flow is currently active or not
   - Trace the EXACT path data takes through processors in sequence and describe what it is doing
   - It's not necessary to list every processor, and it's OK to combine steps/processors into a single description, but do make a reference to important business or technical details that will be covered in more detail in the Business Logic Analysis section below

3. **Business Logic Analysis**:
   - For EACH routing rule: Quote the EXACT expression verbatim and explain what it does
     Example: "RouteOnAttribute processor implements routing condition `${{messageType:startsWith("MT7")}}` which routes messages where the messageType attribute begins with the string 'MT7'"
   - For EACH field extraction: Quote the EXACT JSONPath or Expression Language and explain what field is extracted
     Example: "EvaluateJsonPath extracts the messageType field using JSONPath expression `$["MessageType"]` which reads the MessageType field from the JSON payload root"
   - For EACH transformation: Explain what transformation occurs, how it works, and why it's needed
   - For EACH attribute update: Quote the EXACT expression and explain what attribute is set/modified
   - Explain how these logic elements work together to create the overall business behavior
   - Show how data flows through the logic chain: extraction → routing → transformation


CRITICAL REQUIREMENTS:
- Quote ALL expressions verbatim: routing conditions, JSONPath expressions, Expression Language, attribute names
- Explain logic chains: Show how data flows from one processor to the next and how each step builds on the previous
- Be technical and precise: This documentation is for developers who need exact details
- Include specific processor names, expressions, and technical details
- Do NOT include processor IDs or UUIDs in the text
- Accuracy and extreme brevity are top priority.

"""


# =============================================================================
# PROCESS GROUP WITH CHILDREN (for parent PGs using child summaries)
# =============================================================================

PG_WITH_CHILDREN_PROMPT = """Write a technical analysis of this NiFi Process Group for documentation purposes.

Process Group: {pg_name}
Contains {child_count} sub-components:

{children_digest}

Write a comprehensive technical analysis that covers all of the following. Use as many paragraphs as needed to be clear and complete, but ensure you include all important details. Be clear and concise, but no simpler - include all technical details that developers need.

1. **Overall Purpose** (1-2 paragraphs):
   - What this process group does overall and why it exists
   - Its role in the larger data flow architecture

2. **Sub-Component Integration** (2-4 paragraphs):
   - Explain how each sub-component works (reference their purposes from children_digest)
   - Explain how the sub-components work together
   - Show the sequence of data flow through the children
   - Explain how data moves from one child to the next
   - Describe any coordination or orchestration between children

3. **Data Flow Through Children** (1-3 paragraphs):
   - Trace the path data takes through the child process groups
   - Explain what happens in each child and how results flow to the next
   - Note any branching, merging, or parallel processing paths

4. **Error Handling and Resilience** (1 paragraph):
   - Summarize error handling strategies from child components
   - Explain how errors propagate between children
   - Note any error recovery or retry mechanisms

CRITICAL REQUIREMENTS:
- Synthesize information from child summaries - don't just repeat them verbatim
- Explain the relationships and data flow between children
- Be technical and precise - this is for developers
- Use as many paragraphs as needed to be complete - quality and completeness are more important than brevity
- Do NOT include processor IDs or UUIDs

The analysis should be clear and concise, but no simpler - include all important technical details that developers need to understand how the sub-components collaborate."""


# =============================================================================
# PROCESS GROUP WITH CHILDREN AND OWN PROCESSORS (for parent PGs with both)
# =============================================================================

PG_WITH_CHILDREN_AND_PROCESSORS_PROMPT = """Write a comprehensive technical analysis of this parent Process Group that has both sub-components AND its own processors.

Process Group: {pg_name}

This process group contains:
- {child_count} sub-components (child process groups)
- {processor_count} processors directly in this group

Sub-components:
{children_digest}

Own processors ({processor_count} total):
{processors_json}

Data Flow (connections between own processors):
{connections_json}

Write a technical analysis of this part of a NiFi flow that is used in an Anti Money Laundering (AML) system.  Accuracy and extreme brevity are top priority.

1. **Purpose and Overview**:
   - What this process group does
   - The type of data it processes

2. **Data Flow Sequence**:
   - Use the connections and processors information supplied, as well as the sub-components information supplied
   - Find what looks to be the starting points (there could be multiple), and trace each through to their ends
   - Note their current state (RUNNING, STOPPED, DISABLED, etc.) as that could indicate if this flow is currently active or not
   - Trace the EXACT path data takes through processors in sequence and describe what it is doing
   - It's not necessary to list every processor, and it's OK to combine steps/processors into a single description, but do make a reference to important business or technical details that will be covered in more detail in the Business Logic Analysis section below

3. **Business Logic Analysis**:
   - For EACH routing rule: Quote the EXACT expression verbatim and explain what it does
     Example: "RouteOnAttribute processor implements routing condition `${{messageType:startsWith("MT7")}}` which routes messages where the messageType attribute begins with the string 'MT7'"
   - For EACH field extraction: Quote the EXACT JSONPath or Expression Language and explain what field is extracted
     Example: "EvaluateJsonPath extracts the messageType field using JSONPath expression `$["MessageType"]` which reads the MessageType field from the JSON payload root"
   - For EACH transformation: Explain what transformation occurs, how it works, and why it's needed
   - For EACH attribute update: Quote the EXACT expression and explain what attribute is set/modified
   - Explain how these logic elements work together to create the overall business behavior
   - Show how data flows through the logic chain: extraction → routing → transformation


CRITICAL REQUIREMENTS:
- Quote ALL expressions verbatim: routing conditions, JSONPath expressions, Expression Language, attribute names
- Explain logic chains: Show how data flows from one processor to the next and how each step builds on the previous
- Be technical and precise: This documentation is for developers who need exact details
- Include specific processor names, expressions, and technical details
- Do NOT include processor IDs or UUIDs in the text
- Accuracy and extreme brevity are top priority.

"""

# =============================================================================
# HIERARCHICAL EXECUTIVE SUMMARY (for final documentation)
# =============================================================================

HIERARCHICAL_SUMMARY_PROMPT = """Write an executive summary for a NiFi data flow.

Maximum {max_words} words.

Root process group summary:
{root_summary}

All external IO endpoints (CRITICAL DETAILS):
{io_endpoints}

Process group hierarchy overview:
{hierarchy_overview}

Write a clear, non-technical executive summary that explains:
1. What data this flow processes - SPECIFICALLY mention:
   - File paths, directories, or file patterns (for file-based inputs)
   - URLs or endpoints (for HTTP/API inputs)
   - Database connections and tables (for database inputs)
   - Message queue topics and brokers (for queue inputs)
   - Any other external systems or data sources
2. The major processing stages and their purposes
3. Where data goes - SPECIFICALLY mention:
   - Output file paths or directories
   - Destination URLs or APIs
   - Target databases and tables
   - Output message queues and topics
   - Any other external systems or destinations
4. Key business rules or decisions applied

CRITICAL: The summary MUST include specific endpoint details (paths, URLs, topics, etc.)
from the IO endpoints list. These are essential for understanding the flow's external interactions.

Focus on business value but include technical endpoint details.
Do NOT include UUIDs or technical IDs.
Write for a non-technical stakeholder audience but include endpoint specifics."""


# =============================================================================
# HIERARCHICAL MERMAID DIAGRAM
# =============================================================================

HIERARCHICAL_DIAGRAM_PROMPT = """Generate a Mermaid flowchart showing the process group hierarchy.

Hierarchy data:
{hierarchy_json}

Maximum {max_nodes} nodes in the diagram.

Requirements:
1. Use `flowchart TD` (top-down)
2. Create subgraphs for each process group - the subgraph box itself represents the PG
3. DO NOT create duplicate nodes inside subgraphs that repeat the subgraph name
4. Only create nodes inside subgraphs if they represent:
   - Child process groups (as nested subgraphs, not nodes)
   - Virtual groups (as dashed subgraphs)
5. Show parent-child relationships between subgraphs using arrows
6. Use readable names, NOT UUIDs
7. Keep it clean - subgraphs represent containers, not individual nodes

Example format (correct):
```mermaid
flowchart TD
    subgraph Root["Main Pipeline"]
        subgraph Ingestion["Data Ingestion"]
            A[(GetSFTP)]
            B[ParseCSV]
        end
        subgraph Processing["Data Processing"]
            C{{Validation}}
            D[Transform]
        end
        Ingestion --> Processing
    end
```

Example format (WRONG - don't duplicate names):
```mermaid
flowchart TD
    subgraph Root["Main Pipeline"]
        R[(Main Pipeline)]  # DON'T DO THIS - redundant!
        subgraph Ingestion["Data Ingestion"]
            I[(Data Ingestion)]  # DON'T DO THIS - redundant!
        end
    end
```

Generate ONLY the Mermaid code, no explanation."""


# =============================================================================
# BUSINESS LOGIC EXTRACTION (for LOGIC category processors)
# =============================================================================

BUSINESS_LOGIC_EXTRACTION_PROMPT = """Extract business logic rules from these NiFi processors.

Processors ({processor_count} total):
{processor_json}

For each processor with meaningful logic, identify:
1. The condition or decision (e.g., RouteOnAttribute expressions)
2. What data transformation occurs (e.g., Jolt specs, scripts)
3. Any validation or filtering rules

Return a JSON array of rules:
```json
[
  {{
    "processor": "ProcessorName",
    "rule_type": "ROUTING|TRANSFORM|VALIDATION|FILTER",
    "description": "Human-readable description of the rule"
  }}
]
```

Only include processors with actual business logic.
Skip generic processors like LogAttribute.
Return ONLY the JSON array, no other text."""

