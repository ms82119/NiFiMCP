"""
Document generation helper functions for NiFi flow documentation.

This module provides utilities for generating the final documentation,
including executive summaries, diagrams, hierarchical sections, and tables.
"""

from typing import Dict, Any, List, Optional, Callable
import json
import re
import html

from .component_formatter import format_processor_reference, format_destination_reference


async def generate_executive_summary(
    root_summary: Dict,
    all_summaries: Dict[str, Dict],
    llm_caller: Callable,
    prep_res: Dict[str, Any],
    logger = None
) -> str:
    """Generate executive summary from hierarchical summaries."""
    from ..prompts.documentation import HIERARCHICAL_SUMMARY_PROMPT
    
    # Collect IO endpoints with PG context for deduplication
    # Use processor:direction:pg_name as key to distinguish similar processors in different PGs
    seen = set()
    unique_io = []
    for pg_id, summary in all_summaries.items():
        pg_name = summary.get("name", "unknown")
        for io in summary.get("io_endpoints", []):
            # Include PG name in key to preserve context and avoid losing important distinctions
            key = f"{io.get('processor')}:{io.get('direction')}:{pg_name}"
            if key not in seen:
                seen.add(key)
                # Add PG name to IO endpoint for context in the prompt
                io_with_context = {**io, "pg_name": pg_name}
                unique_io.append(io_with_context)
    
    hierarchy_overview = []
    for pg_id, summary in all_summaries.items():
        if not summary.get("virtual"):
            # Include full summary (no truncation) - important for generation quality
            hierarchy_overview.append({
                "name": summary.get("name"),
                "purpose": summary.get("summary", "")  # Full summary, not truncated
            })
    
    prompt = HIERARCHICAL_SUMMARY_PROMPT.format(
        max_words=prep_res.get("summary_max_words", 500),
        root_summary=root_summary.get("summary", "No summary available"),
        io_endpoints=json.dumps(unique_io, indent=2),
        hierarchy_overview=json.dumps(hierarchy_overview, indent=2)
    )
    
    response = await llm_caller(
        messages=[{"role": "user", "content": prompt}],
        tools=None,
        execution_state=prep_res,
        action_id="generation-exec-summary"
    )
    
    return response.get("content", "Summary generation failed.")


async def generate_hierarchical_diagram(
    pg_tree: Dict[str, Dict],
    pg_summaries: Dict[str, Dict],
    llm_caller: Callable,
    prep_res: Dict[str, Any],
    logger = None
) -> str:
    """Generate Mermaid diagram showing PG hierarchy."""
    from ..prompts.documentation import HIERARCHICAL_DIAGRAM_PROMPT
    
    hierarchy = []
    for pg_id, pg in pg_tree.items():
        summary = pg_summaries.get(pg_id, {})
        hierarchy.append({
            "id": pg_id[:8],
            "name": pg.get("name", pg_id[:8]),
            "parent": pg.get("parent", "")[:8] if pg.get("parent") else None,
            "purpose": summary.get("summary", "")[:200],  # Increased from 50 to 200 chars for better context
            "has_io": bool(summary.get("io_endpoints"))
        })
    
    for pg_id, virtual_groups in prep_res.get("virtual_groups", {}).items():
        for vg in virtual_groups:
            hierarchy.append({
                "id": f"vg-{vg.get('name', 'group')[:6]}",
                "name": vg.get("name"),
                "parent": pg_id[:8],
                "purpose": vg.get("purpose", "")[:200],  # Increased from 50 to 200 chars for better context
                "virtual": True
            })
    
    prompt = HIERARCHICAL_DIAGRAM_PROMPT.format(
        hierarchy_json=json.dumps(hierarchy, indent=2),
        max_nodes=prep_res.get("max_mermaid_nodes", 50)
    )
    
    response = await llm_caller(
        messages=[{"role": "user", "content": prompt}],
        tools=None,
        execution_state=prep_res,
        action_id="generation-diagram"
    )
    
    content = response.get("content", "")
    
    # Extract Mermaid code from markdown code block
    mermaid_match = re.search(r'```mermaid\s*([\s\S]*?)```', content)
    if mermaid_match:
        diagram_code = mermaid_match.group(1).strip()
    elif content.strip().startswith("graph") or content.strip().startswith("flowchart"):
        diagram_code = content.strip()
    else:
        if logger:
            logger.warning("LLM did not generate valid Mermaid diagram, using fallback")
        return "graph TD\n    A[Flow] --> B[Documentation Failed]"
    
    # Clean and validate the diagram code
    # Fix common issues: missing newlines after keywords
    diagram_code = re.sub(r'(flowchart\s+TD)(subgraph)', r'\1\n    \2', diagram_code)
    diagram_code = re.sub(r'(graph\s+TD)(subgraph)', r'\1\n    \2', diagram_code)
    # Ensure subgraph declarations have proper spacing
    diagram_code = re.sub(r'subgraph\s+(["\']?)([^"\']+)\1', r'subgraph \1\2\1', diagram_code)
    # Fix missing newlines before subgraph
    diagram_code = re.sub(r'}\s*(subgraph)', r'}\n    \1', diagram_code)
    # Fix missing newlines after subgraph declarations
    diagram_code = re.sub(r'(subgraph[^\n]+)\s*([A-Z])', r'\1\n        \2', diagram_code)

    # Validate basic structure
    if not (diagram_code.strip().startswith("graph") or diagram_code.strip().startswith("flowchart")):
        if logger:
            logger.warning("Mermaid diagram does not start with 'graph' or 'flowchart', using fallback")
        return "graph TD\n    A[Flow] --> B[Documentation Failed]"

    # Detect and fix HTML entity corruption
    # Handle corrupted patterns like: name="" value="" -> "name value"
    # This happens when HTML entities get mangled during processing
    original_code = diagram_code

    # Fix corrupted subgraph declarations like: subgraph name[" label=""]=""
    # Pattern: subgraph Name[" text=""]="" -> subgraph Name["text"]
    diagram_code = re.sub(r'subgraph\s+(\w+)\[\s*"([^"]*?)"\s*=\s*""\s*\]', r'subgraph \1["\2"]', diagram_code)
    
    # Fix more severe corruption: subgraph Name[" text=""]="" direction="" -> subgraph Name["text"]
    diagram_code = re.sub(r'subgraph\s+(\w+)\[\s*"\s*([^"]*?)\s*"\s*=\s*""\s*\]\s*=\s*""', r'subgraph \1["\2"]', diagram_code)
    
    # Fix patterns like: [" narrative="" swift="" screening"]=""
    # This happens when quotes get double-encoded: " becomes &quot; becomes ""
    diagram_code = re.sub(r'\[\s*"\s*([^"]*?)\s*""\s*([^"]*?)\s*"\s*\]\s*=\s*""', r'["\1 \2"]', diagram_code)
    
    # Fix corrupted node labels like: A[[label=""]]=""
    diagram_code = re.sub(r'\[\[([^\]]*?)"\s*=\s*""\s*\]\]', r'[[\1]]', diagram_code)
    
    # Fix corrupted connections like: A --&gt; B  (should be A --> B)
    diagram_code = re.sub(r'--&gt;', '-->', diagram_code)
    
    # More aggressive cleanup for heavily corrupted diagrams
    # Remove trailing ="" patterns that are artifacts of corruption
    diagram_code = re.sub(r'=""(\s*(?:subgraph|end|direction|tb|[a-z]\[\[))', r'\1', diagram_code)
    
    # Fix subgraph declarations that got mangled: subgraph Name["text"] -> subgraph Name["text"]
    diagram_code = re.sub(r'subgraph\s+(\w+)\[\s*"([^"]*?)"\s*\]', r'subgraph \1["\2"]', diagram_code)
    
    # Clean up any remaining corruption artifacts: remove ="" patterns
    diagram_code = re.sub(r'\s*=""\s*', ' ', diagram_code)
    
    # Fix patterns where quotes got split: " text="" -> "text"
    diagram_code = re.sub(r'"\s+([^"]+?)\s*""', r'"\1"', diagram_code)
    
    # Fix patterns where subgraph names got corrupted: Narrative_SWIFT_Screening[" text=""]=""
    # Extract the actual name and fix the label
    diagram_code = re.sub(r'subgraph\s+(\w+)\[\s*"\s*([^"]*?)\s*"\s*=\s*""\s*\]\s*=\s*""', r'subgraph \1["\2"]', diagram_code)

    # If the code changed significantly, log the cleanup
    if original_code != diagram_code and logger:
        logger.info(f"Applied corruption cleanup to Mermaid diagram. Original length: {len(original_code)}, Cleaned length: {len(diagram_code)}")

    # Decode any remaining HTML entities FIRST (before validation)
    if '&gt;' in diagram_code or '&lt;' in diagram_code or '&amp;' in diagram_code or '&quot;' in diagram_code:
        diagram_code = html.unescape(diagram_code)
        if logger:
            logger.info("Decoded HTML entities in Mermaid diagram")
        # Re-apply cleaning after HTML entity decoding
        diagram_code = re.sub(r'=""(\s*(?:subgraph|end|direction|tb|[a-z]\[\[))', r'\1', diagram_code)
        diagram_code = re.sub(r'\s*=""\s*', ' ', diagram_code)

    # Final validation - reject if still contains corruption patterns
    # Look for patterns that suggest HTML entity corruption or malformed attributes:
    # - Multiple quotes together: "" or '' (not valid in Mermaid)
    # - Equals signs in subgraph/node labels (not valid syntax)
    # - Patterns like: name="" or ="value" which suggest corruption
    if re.search(r'["\']\s*["\']', diagram_code) or re.search(r'=\s*["\']', diagram_code):
        if logger:
            logger.warning("Mermaid diagram still contains malformed attributes after cleanup, using fallback")
            logger.debug(f"Corrupted diagram code (first 200 chars): {diagram_code[:200]}")
        return "graph TD\n    A[Flow] --> B[Documentation Failed]"

    # Check for malformed node definitions (multiple = signs in node labels)
    # Valid nodes: A[Label], A[[Label]], A{{Label}}, A[(Label)]
    # Invalid: A[Label="value="] or similar corruption
    if re.search(r'\[\[[^\]]*="[^"]*="[^\]]*\]\]', diagram_code):
        if logger:
            logger.warning("Mermaid diagram contains malformed node labels after cleanup, using fallback")
        return "graph TD\n    A[Flow] --> B[Documentation Failed]"

    # Check for patterns like: subgraph " name=" which suggests corruption
    if re.search(r'subgraph\s+["\'][^"\']*=\s*["\']', diagram_code):
        if logger:
            logger.warning("Mermaid diagram contains malformed subgraph labels after cleanup, using fallback")
        return "graph TD\n    A[Flow] --> B[Documentation Failed]"

    # Additional check: if diagram contains patterns like [" text=""]="", reject it
    if re.search(r'\[\s*["\'][^"\']*["\']\s*=\s*["\']\s*["\']\s*\]', diagram_code):
        if logger:
            logger.warning("Mermaid diagram contains severely corrupted label patterns, using fallback")
        return "graph TD\n    A[Flow] --> B[Documentation Failed]"

    # Log the final cleaned diagram for debugging
    if logger:
        logger.debug(f"Final cleaned Mermaid diagram (first 300 chars): {diagram_code[:300]}")

    return diagram_code


def build_hierarchical_doc(
    root_pg_id: str,
    pg_tree: Dict[str, Dict],
    pg_summaries: Dict[str, Dict],
    virtual_groups: Dict[str, List[Dict]],
    depth: int = 0
) -> str:
    """Build nested documentation sections from hierarchy with enhanced details."""
    lines = []
    
    pg = pg_tree.get(root_pg_id, {})
    summary = pg_summaries.get(root_pg_id, {})
    
    header_level = min(depth + 2, 4)
    header = "#" * header_level
    
    pg_name = pg.get("name", root_pg_id[:8])
    lines.append(f"{header} {pg_name}")
    lines.append("")
    
    # Summary text (from LLM analysis)
    if summary.get("summary"):
        lines.append(summary["summary"])
        lines.append("")
    
    # Enhanced IO Endpoints section with detailed information
    io_endpoints = summary.get("io_endpoints", [])
    if io_endpoints:
        lines.append("### External Interactions")
        lines.append("")
        
        inputs = [e for e in io_endpoints if e.get("direction") == "INPUT"]
        outputs = [e for e in io_endpoints if e.get("direction") == "OUTPUT"]
        
        if inputs:
            lines.append("**Input Sources:**")
            for io in inputs:
                endpoint_details = io.get("endpoint_details", {})
                endpoint_type = endpoint_details.get("type", "unknown")
                
                if endpoint_type == "http_server":
                    url = endpoint_details.get("url", "")
                    methods = endpoint_details.get("allowed_methods", [])
                    lines.append(f"- **{io.get('processor')}**: `{url}` (Methods: {', '.join(methods)})")
                elif endpoint_type == "file_system":
                    directory = endpoint_details.get("directory", "")
                    file_filter = endpoint_details.get("file_filter", "")
                    filter_str = f" (Filter: `{file_filter}`)" if file_filter else ""
                    lines.append(f"- **{io.get('processor')}**: Directory `{directory}`{filter_str}")
                elif endpoint_type == "kafka":
                    topic = endpoint_details.get("topic", "")
                    bootstrap = endpoint_details.get("bootstrap_servers", "")
                    lines.append(f"- **{io.get('processor')}**: Topic `{topic}` (Broker: `{bootstrap}`)")
                elif endpoint_type == "database":
                    table = endpoint_details.get("table", "")
                    connection_url = endpoint_details.get("connection_url", "")
                    lines.append(f"- **{io.get('processor')}**: Table `{table}` (Connection: `{connection_url}`)")
                else:
                    lines.append(f"- **{io.get('processor')}**: {endpoint_type}")
            lines.append("")
        
        if outputs:
            lines.append("**Output Destinations:**")
            for io in outputs:
                endpoint_details = io.get("endpoint_details", {})
                endpoint_type = endpoint_details.get("type", "unknown")
                
                if endpoint_type == "http":
                    url = endpoint_details.get("url", "")
                    method = endpoint_details.get("method", "GET")
                    if url:
                        lines.append(f"- **{io.get('processor')}**: `{method} {url}`")
                    else:
                        lines.append(f"- **{io.get('processor')}**: {method} (URL not configured)")
                elif endpoint_type == "http_response":
                    status_code = endpoint_details.get("status_code", "")
                    lines.append(f"- **{io.get('processor')}**: HTTP Response (Status: {status_code})")
                elif endpoint_type == "file_system":
                    directory = endpoint_details.get("directory", "")
                    file_filter = endpoint_details.get("file_filter", "")
                    filter_str = f" (Filter: `{file_filter}`)" if file_filter else ""
                    lines.append(f"- **{io.get('processor')}**: Directory `{directory}`{filter_str}")
                elif endpoint_type == "kafka":
                    topic = endpoint_details.get("topic", "")
                    bootstrap = endpoint_details.get("bootstrap_servers", "")
                    lines.append(f"- **{io.get('processor')}**: Topic `{topic}` (Broker: `{bootstrap}`)")
                elif endpoint_type == "database":
                    table = endpoint_details.get("table", "")
                    connection_url = endpoint_details.get("connection_url", "")
                    lines.append(f"- **{io.get('processor')}**: Table `{table}` (Connection: `{connection_url}`)")
                else:
                    lines.append(f"- **{io.get('processor')}**: {endpoint_type}")
            lines.append("")
    
    # Processor States section (if any are not RUNNING)
    categories = summary.get("categories", {})
    stopped_processors = []
    for category, proc_list in categories.items():
        for proc in proc_list:
            if isinstance(proc, dict):
                state = proc.get("state", "UNKNOWN")
                if state not in ["RUNNING", "UNKNOWN"]:
                    stopped_processors.append(f"{proc.get('name')} ({state})")
    
    if stopped_processors:
        lines.append("### Processor Status")
        lines.append("")
        lines.append(f"**Note:** The following processors are not running: {', '.join(stopped_processors)}")
        lines.append("")
    
    # Error Handling summary
    error_handling = summary.get("error_handling", [])
    if error_handling:
        unhandled = [e for e in error_handling if not e.get("handled") and not e.get("auto_terminated")]
        if unhandled:
            lines.append("### Error Handling")
            lines.append("")
            lines.append(f"**Warning:** {len(unhandled)} error relationship(s) are not handled:")
            for err in unhandled[:5]:  # Limit to first 5
                proc_ref = err.get("processor_reference", err.get("processor", "?"))
                rel = err.get("error_relationship", "?")
                lines.append(f"- `{proc_ref}`: `{rel}` relationship → NOT HANDLED")
            if len(unhandled) > 5:
                lines.append(f"- ... and {len(unhandled) - 5} more unhandled errors")
            lines.append("")
    
    # Virtual Groups (if any)
    vg_list = virtual_groups.get(root_pg_id, [])
    if vg_list:
        lines.append("### Logical Components")
        lines.append("")
        for vg in vg_list:
            lines.append(f"- **{vg.get('name')}**: {vg.get('purpose', '')}")
        lines.append("")
    
    # Recursively process children
    children = pg.get("children", [])
    if children:
        for child_id in children:
            child_doc = build_hierarchical_doc(
                child_id, pg_tree, pg_summaries, virtual_groups, depth + 1
            )
            lines.append(child_doc)
    
    return "\n".join(lines)


def build_aggregated_io_table(
    pg_summaries: Dict[str, Dict]
) -> str:
    """
    Build detailed aggregated IO table with endpoint specifics.
    
    CRITICAL: This section must include all endpoint details (paths, URLs, topics, etc.)
    as these are essential for understanding external interactions.
    """
    all_inputs = []
    all_outputs = []
    
    for pg_id, summary in pg_summaries.items():
        pg_name = summary.get("name", pg_id[:8])
        for io in summary.get("io_endpoints", []):
            io_with_pg = {**io, "pg": pg_name}
            if io.get("direction") == "INPUT":
                all_inputs.append(io_with_pg)
            else:
                all_outputs.append(io_with_pg)
    
    if not all_inputs and not all_outputs:
        return "*No external IO endpoints identified.*"
    
    lines = []
    
    if all_inputs:
        lines.append("### Data Inputs")
        lines.append("")
        for io in all_inputs:
            endpoint_details = io.get("endpoint_details", {})
            endpoint_type = endpoint_details.get("type", "unknown")
            
            # Use formatted reference (name, type, ID)
            proc_ref = io.get("processor_reference", 
                f"{io.get('processor')} ({io.get('processor_type')}) [id:{io.get('processor_id')}]")
            
            lines.append(f"#### {proc_ref}")
            lines.append(f"**Process Group:** {io.get('pg')}")
            
            # Format endpoint details based on type
            if endpoint_type == "file_system":
                lines.append(f"- **Directory:** `{endpoint_details.get('directory', 'N/A')}`")
                if endpoint_details.get("file_filter"):
                    lines.append(f"- **File Filter:** `{endpoint_details.get('file_filter')}`")
            
            elif endpoint_type == "sftp":
                lines.append(f"- **Host:** `{endpoint_details.get('hostname', 'N/A')}:{endpoint_details.get('port', 'N/A')}`")
                lines.append(f"- **Directory:** `{endpoint_details.get('directory', 'N/A')}`")
                if endpoint_details.get("username"):
                    lines.append(f"- **Username:** `{endpoint_details.get('username')}`")
            
            elif endpoint_type == "http":
                lines.append(f"- **URL:** `{endpoint_details.get('url', 'N/A')}`")
                lines.append(f"- **Method:** `{endpoint_details.get('method', 'N/A')}`")
                if endpoint_details.get("ssl_context"):
                    lines.append(f"- **SSL Context:** `{endpoint_details.get('ssl_context')}`")
            
            elif endpoint_type == "http_server":
                lines.append(f"- **URL:** `{endpoint_details.get('url', 'N/A')}`")
                lines.append(f"- **Port:** `{endpoint_details.get('port', 'N/A')}`")
                lines.append(f"- **Path:** `{endpoint_details.get('path', '/')}`")
                if endpoint_details.get("allowed_methods"):
                    methods = ", ".join(endpoint_details.get("allowed_methods", []))
                    lines.append(f"- **Allowed Methods:** `{methods}`")
                if endpoint_details.get("ssl_context"):
                    lines.append(f"- **SSL Context:** `{endpoint_details.get('ssl_context')}`")
                if endpoint_details.get("client_authentication") and endpoint_details.get("client_authentication") != "No Authentication":
                    lines.append(f"- **Client Authentication:** `{endpoint_details.get('client_authentication')}`")
            
            elif endpoint_type == "http_response":
                lines.append(f"- **Status Code:** `{endpoint_details.get('status_code', 'N/A')}`")
                if endpoint_details.get("status_code_expression"):
                    lines.append(f"- **Status Code Expression:** `{endpoint_details.get('status_code_expression')}`")
                if endpoint_details.get("http_context_map"):
                    lines.append(f"- **HTTP Context Map:** `{endpoint_details.get('http_context_map')}`")
            
            elif endpoint_type == "kafka":
                lines.append(f"- **Topic:** `{endpoint_details.get('topic', 'N/A')}`")
                lines.append(f"- **Bootstrap Servers:** `{endpoint_details.get('bootstrap_servers', 'N/A')}`")
                if endpoint_details.get("consumer_group"):
                    lines.append(f"- **Consumer Group:** `{endpoint_details.get('consumer_group')}`")
            
            elif endpoint_type == "database":
                lines.append(f"- **Connection:** `{endpoint_details.get('connection_url', 'N/A')}`")
                if endpoint_details.get("table"):
                    lines.append(f"- **Table:** `{endpoint_details.get('table')}`")
            
            elif endpoint_type == "jms":
                lines.append(f"- **Destination:** `{endpoint_details.get('destination', 'N/A')}`")
            
            elif endpoint_type == "s3":
                lines.append(f"- **Bucket:** `{endpoint_details.get('bucket', 'N/A')}`")
                if endpoint_details.get("object_key"):
                    lines.append(f"- **Object Key:** `{endpoint_details.get('object_key')}`")
            
            elif endpoint_type == "mongodb":
                lines.append(f"- **Database:** `{endpoint_details.get('database', 'N/A')}`")
                lines.append(f"- **Collection:** `{endpoint_details.get('collection', 'N/A')}`")
            
            elif endpoint_type == "elasticsearch":
                lines.append(f"- **URL:** `{endpoint_details.get('url', 'N/A')}`")
                lines.append(f"- **Index:** `{endpoint_details.get('index', 'N/A')}`")
            
            else:
                # Generic fallback - show all properties
                props = endpoint_details.get("properties", {})
                if props:
                    for key, value in props.items():
                        if value:
                            lines.append(f"- **{key}:** `{value}`")
                else:
                    lines.append("- *Endpoint details not available*")
            
            lines.append("")
    
    if all_outputs:
        lines.append("### Data Outputs")
        lines.append("")
        for io in all_outputs:
            endpoint_details = io.get("endpoint_details", {})
            endpoint_type = endpoint_details.get("type", "unknown")
            
            # Use formatted reference (name, type, ID)
            proc_ref = io.get("processor_reference",
                f"{io.get('processor')} ({io.get('processor_type')}) [id:{io.get('processor_id')}]")
            
            lines.append(f"#### {proc_ref}")
            lines.append(f"**Process Group:** {io.get('pg')}")
            
            # Same formatting logic as inputs
            if endpoint_type == "file_system":
                lines.append(f"- **Directory:** `{endpoint_details.get('directory', 'N/A')}`")
                if endpoint_details.get("file_filter"):
                    lines.append(f"- **File Filter:** `{endpoint_details.get('file_filter')}`")
            
            elif endpoint_type == "sftp":
                lines.append(f"- **Host:** `{endpoint_details.get('hostname', 'N/A')}:{endpoint_details.get('port', 'N/A')}`")
                lines.append(f"- **Directory:** `{endpoint_details.get('directory', 'N/A')}`")
            
            elif endpoint_type == "http":
                lines.append(f"- **URL:** `{endpoint_details.get('url', 'N/A')}`")
                lines.append(f"- **Method:** `{endpoint_details.get('method', 'N/A')}`")
                if endpoint_details.get("ssl_context"):
                    lines.append(f"- **SSL Context:** `{endpoint_details.get('ssl_context')}`")
            
            elif endpoint_type == "http_server":
                lines.append(f"- **URL:** `{endpoint_details.get('url', 'N/A')}`")
                lines.append(f"- **Port:** `{endpoint_details.get('port', 'N/A')}`")
                lines.append(f"- **Path:** `{endpoint_details.get('path', '/')}`")
                if endpoint_details.get("allowed_methods"):
                    methods = ", ".join(endpoint_details.get("allowed_methods", []))
                    lines.append(f"- **Allowed Methods:** `{methods}`")
                if endpoint_details.get("ssl_context"):
                    lines.append(f"- **SSL Context:** `{endpoint_details.get('ssl_context')}`")
                if endpoint_details.get("client_authentication") and endpoint_details.get("client_authentication") != "No Authentication":
                    lines.append(f"- **Client Authentication:** `{endpoint_details.get('client_authentication')}`")
            
            elif endpoint_type == "http_response":
                lines.append(f"- **Status Code:** `{endpoint_details.get('status_code', 'N/A')}`")
                if endpoint_details.get("status_code_expression"):
                    lines.append(f"- **Status Code Expression:** `{endpoint_details.get('status_code_expression')}`")
                if endpoint_details.get("http_context_map"):
                    lines.append(f"- **HTTP Context Map:** `{endpoint_details.get('http_context_map')}`")
            
            elif endpoint_type == "kafka":
                lines.append(f"- **Topic:** `{endpoint_details.get('topic', 'N/A')}`")
                lines.append(f"- **Bootstrap Servers:** `{endpoint_details.get('bootstrap_servers', 'N/A')}`")
            
            elif endpoint_type == "database":
                lines.append(f"- **Connection:** `{endpoint_details.get('connection_url', 'N/A')}`")
                if endpoint_details.get("table"):
                    lines.append(f"- **Table:** `{endpoint_details.get('table')}`")
            
            elif endpoint_type == "jms":
                lines.append(f"- **Destination:** `{endpoint_details.get('destination', 'N/A')}`")
            
            elif endpoint_type == "s3":
                lines.append(f"- **Bucket:** `{endpoint_details.get('bucket', 'N/A')}`")
                if endpoint_details.get("object_key"):
                    lines.append(f"- **Object Key:** `{endpoint_details.get('object_key')}`")
            
            elif endpoint_type == "mongodb":
                lines.append(f"- **Database:** `{endpoint_details.get('database', 'N/A')}`")
                lines.append(f"- **Collection:** `{endpoint_details.get('collection', 'N/A')}`")
            
            elif endpoint_type == "elasticsearch":
                lines.append(f"- **URL:** `{endpoint_details.get('url', 'N/A')}`")
                lines.append(f"- **Index:** `{endpoint_details.get('index', 'N/A')}`")
            
            else:
                props = endpoint_details.get("properties", {})
                if props:
                    for key, value in props.items():
                        if value:
                            lines.append(f"- **{key}:** `{value}`")
                else:
                    lines.append("- *Endpoint details not available*")
            
            lines.append("")
    
    return "\n".join(lines)


def build_error_handling_table(
    pg_summaries: Dict[str, Dict]
) -> str:
    """
    Build error handling table showing where errors are handled or ignored.
    
    Lightweight initial implementation - can be expanded later with:
    - Retry policies
    - Error handling patterns
    - Dead letter queue analysis
    """
    all_errors = []
    
    for pg_id, summary in pg_summaries.items():
        pg_name = summary.get("name", pg_id[:8])
        for error in summary.get("error_handling", []):
            error_with_pg = {**error, "pg": pg_name}
            all_errors.append(error_with_pg)
    
    if not all_errors:
        return "*No error handling relationships identified.*"
    
    # Group by handled/ignored status
    handled_errors = [e for e in all_errors if e.get("handled")]
    ignored_errors = [e for e in all_errors if e.get("auto_terminated")]
    unhandled_errors = [e for e in all_errors if not e.get("handled") and not e.get("auto_terminated")]
    
    lines = []
    
    if handled_errors:
        lines.append("### Handled Errors")
        lines.append("")
        lines.append("| Process Group | Processor | Error Relationship | Destination |")
        lines.append("|---------------|-----------|---------------------|-------------|")
        for error in handled_errors:
            # Use formatted references (name, type, ID)
            proc_ref = error.get("processor_reference", 
                f"{error.get('processor')} ({error.get('processor_type')}) [id:{error.get('processor_id')}]")
            dest_ref = error.get("destination", "Unknown")
            
            lines.append(
                f"| {error.get('pg')} | `{proc_ref}` | "
                f"`{error.get('error_relationship')}` | `{dest_ref}` |"
            )
        lines.append("")
    
    if ignored_errors:
        lines.append("### Ignored Errors (Auto-Terminated)")
        lines.append("")
        lines.append("| Process Group | Processor | Error Relationship |")
        lines.append("|---------------|-----------|---------------------|")
        for error in ignored_errors:
            # Use formatted reference (name, type, ID)
            proc_ref = error.get("processor_reference",
                f"{error.get('processor')} ({error.get('processor_type')}) [id:{error.get('processor_id')}]")
            
            lines.append(
                f"| {error.get('pg')} | `{proc_ref}` | "
                f"`{error.get('error_relationship')}` |"
            )
        lines.append("")
        lines.append("*Note: These errors are automatically terminated and not routed anywhere.*")
        lines.append("")
    
    if unhandled_errors:
        lines.append("### ⚠️ Unhandled Errors")
        lines.append("")
        lines.append("| Process Group | Processor | Error Relationship |")
        lines.append("|---------------|-----------|---------------------|")
        for error in unhandled_errors:
            # Use formatted reference (name, type, ID)
            proc_ref = error.get("processor_reference",
                f"{error.get('processor')} ({error.get('processor_type')}) [id:{error.get('processor_id')}]")
            
            lines.append(
                f"| {error.get('pg')} | `{proc_ref}` | "
                f"`{error.get('error_relationship')}` |"
            )
        lines.append("")
        lines.append("*Warning: These error relationships exist but are not connected or auto-terminated.*")
        lines.append("*This may indicate missing error handling logic.*")
        lines.append("")
    
    return "\n".join(lines)


def assemble_hierarchical_document(
    title: str,
    executive_summary: str,
    mermaid_diagram: str,
    hierarchical_doc: str,
    io_table: str,
    error_table: str
) -> str:
    """Assemble final hierarchical Markdown document."""
    doc = f"""# NiFi Flow Documentation

## Process Group: {title}

---

## Executive Summary

{executive_summary}

---

## Flow Architecture

```mermaid
{mermaid_diagram}
```

---

## Detailed Breakdown

{hierarchical_doc}

---

## External Interactions

{io_table}

---

## Error Handling

{error_table}

---

*Generated by NiFi Flow Documentation Workflow (Hierarchical Analysis)*
"""
    return doc


def validate_output(
    final_document: str,
    pg_summaries: Dict[str, Dict]
) -> List[str]:
    """Validate generated output quality."""
    issues = []
    
    uuid_pattern = r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'
    if re.search(uuid_pattern, final_document):
        issues.append("Raw UUIDs found in document - consider using names instead")
    
    mermaid_match = re.search(r'```mermaid\s*([\s\S]*?)```', final_document)
    if mermaid_match:
        mermaid_content = mermaid_match.group(1)
        if not (mermaid_content.strip().startswith("graph") or 
               mermaid_content.strip().startswith("flowchart")):
            issues.append("Mermaid diagram may have invalid syntax")
    
    if len(pg_summaries) > 1:
        mentioned_pgs = sum(1 for pg in pg_summaries.values() 
                           if pg.get("name", "") in final_document)
        if mentioned_pgs < len(pg_summaries) * 0.5:
            issues.append("Less than half of PGs mentioned in documentation")
    
    return issues


def emit_section_event(
    section: str,
    prep_res: Dict[str, Any],
    event_emitter: Optional[Callable] = None
):
    """
    Emit event for section generation.
    
    Note: This is a placeholder. The actual event emission should be handled
    by the node class since it requires access to node-specific methods.
    """
    # This function is kept for compatibility but should be called from the node
    # where it has access to emit_doc_phase_event
    pass

