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
# =============================================================================

PG_SUMMARY_PROMPT = """Summarize this NiFi Process Group for documentation.

Process Group: {pg_name}
{pg_purpose}

Processors ({processor_count} total) by category:
{categories_json}

IO Endpoints (CRITICAL - external interactions):
{io_endpoints_json}

Child summaries (if any):
{child_summaries_json}

Write a 2-3 sentence summary that explains:
1. What this process group does (its purpose)
2. What data it processes or transforms (mention specific sources/destinations from IO endpoints)
3. Key business logic or decisions it implements

IMPORTANT: Explicitly mention external systems, file paths, URLs, databases, or message queues
that this process group interacts with. These are critical documentation details.

Keep it concise and non-technical where possible.
Do NOT include processor IDs or UUIDs.
Focus on business value and data flow purpose."""


# =============================================================================
# PROCESS GROUP WITH CHILDREN (for parent PGs using child summaries)
# =============================================================================

PG_WITH_CHILDREN_PROMPT = """Summarize this parent Process Group based on its children.

Process Group: {pg_name}
Contains {child_count} sub-components:

{children_digest}

Write a 2-3 sentence summary that:
1. Describes the overall purpose of this process group
2. Explains how the sub-components work together
3. Notes the data flow through the children

The summary should work as a high-level overview.
Don't repeat details from child summaries - synthesize them.
Do NOT include processor IDs or UUIDs."""


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
2. Create subgraphs for nested process groups
3. Use different node shapes:
   - [[ ]] for process groups (stadium shape)
   - [(Database)] for IO/storage
   - {{Decision}} for routing groups
   - [Process] for transformation groups
4. Show parent-child relationships
5. Mark virtual groups with dashed style
6. Use readable names, NOT UUIDs

Example format:
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

