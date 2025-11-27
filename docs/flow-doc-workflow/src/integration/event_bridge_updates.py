"""
EventBridge Updates for Documentation Workflow Events

These updates add handlers for documentation-specific event types
to the EventBridge class.

Apply these changes to: api/event_bridge.py
"""

from typing import Dict, Any, Optional
from nifi_mcp_server.workflows.core.event_system import EventTypes, WorkflowEvent


def _map_documentation_events(
    event: WorkflowEvent,
    user_request_id: str,
    event_data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Map documentation workflow events to WebSocket message format.
    
    Add this to the _map_event_to_websocket method in EventBridge class.
    
    Args:
        event: The workflow event
        user_request_id: User's request ID
        event_data: Event data dictionary
    
    Returns:
        WebSocket message dict or None if event should not be forwarded
    """
    
    # === Documentation Phase Events ===
    
    if event.event_type == EventTypes.DOC_PHASE_START:
        phase = event_data.get("phase", "Unknown")
        progress_message = event_data.get("progress_message", f"Starting {phase} phase...")
        return {
            "type": "workflow_status",
            "request_id": user_request_id,
            "status": "processing",
            "message": f"📄 {progress_message}",
            "data": {
                "phase": phase,
                "workflow": "flow_documentation",
                "metrics": event_data.get("metrics", {})
            },
            "timestamp": str(event.id)
        }
    
    elif event.event_type == EventTypes.DOC_PHASE_COMPLETE:
        phase = event_data.get("phase", "Unknown")
        metrics = event_data.get("metrics", {})
        progress_message = event_data.get("progress_message", f"{phase} complete")
        return {
            "type": "workflow_status",
            "request_id": user_request_id,
            "status": "processing",
            "message": f"✅ {progress_message}",
            "data": {
                "phase": phase,
                "metrics": metrics,
                "progress_message": progress_message
            },
            "timestamp": str(event.id)
        }
    
    elif event.event_type == EventTypes.DOC_PHASE_ERROR:
        phase = event_data.get("phase", "Unknown")
        error = event_data.get("error", "Unknown error")
        return {
            "type": "workflow_status",
            "request_id": user_request_id,
            "status": "error",
            "message": f"❌ Error in {phase}: {error}",
            "data": {
                "phase": phase,
                "error": error
            },
            "timestamp": str(event.id)
        }
    
    # === Discovery Phase Events ===
    
    elif event.event_type == EventTypes.DOC_DISCOVERY_CHUNK:
        metrics = event_data.get("metrics", {})
        progress_message = event_data.get("progress_message", "Discovering components...")
        processors_found = metrics.get("processors_found", 0)
        pgs_found = metrics.get("process_groups_found", 0)
        
        return {
            "type": "workflow_status",
            "request_id": user_request_id,
            "status": "processing",
            "message": f"🔍 {progress_message}",
            "data": {
                "phase": "DISCOVERY",
                "processors": processors_found,
                "process_groups": pgs_found,
                "chunk_elapsed_ms": metrics.get("chunk_elapsed_ms", 0)
            },
            "timestamp": str(event.id)
        }
    
    elif event.event_type == EventTypes.DOC_DISCOVERY_TIMEOUT:
        continuation_token = event_data.get("continuation_token")
        return {
            "type": "workflow_status",
            "request_id": user_request_id,
            "status": "processing",
            "message": "⏱️ Discovery timeout, will continue from checkpoint...",
            "data": {
                "phase": "DISCOVERY",
                "has_continuation": bool(continuation_token)
            },
            "timestamp": str(event.id)
        }
    
    elif event.event_type == EventTypes.DOC_DISCOVERY_RETRY:
        attempt = event_data.get("attempt", 1)
        return {
            "type": "workflow_status",
            "request_id": user_request_id,
            "status": "processing",
            "message": f"🔄 Retrying discovery (attempt {attempt})...",
            "data": {
                "phase": "DISCOVERY",
                "attempt": attempt
            },
            "timestamp": str(event.id)
        }
    
    # === Analysis Phase Events ===
    
    elif event.event_type == EventTypes.DOC_ANALYSIS_CATEGORIZE:
        metrics = event_data.get("metrics", {})
        categorized_count = metrics.get("categorized_count", 0)
        return {
            "type": "workflow_status",
            "request_id": user_request_id,
            "status": "processing",
            "message": f"📊 Categorized {categorized_count} processors",
            "data": {
                "phase": "ANALYSIS",
                "categorized": categorized_count,
                "categories": metrics.get("categories", {})
            },
            "timestamp": str(event.id)
        }
    
    elif event.event_type == EventTypes.DOC_ANALYSIS_BATCH:
        metrics = event_data.get("metrics", {})
        pg_name = metrics.get("pg_name", "Unknown")
        depth = metrics.get("depth", 0)
        progress_message = event_data.get(
            "progress_message", 
            f"Analyzing '{pg_name}' at depth {depth}"
        )
        
        return {
            "type": "workflow_status",
            "request_id": user_request_id,
            "status": "processing",
            "message": f"🧠 {progress_message}",
            "data": {
                "phase": "ANALYSIS",
                "pg_name": pg_name,
                "depth": depth
            },
            "timestamp": str(event.id)
        }
    
    elif event.event_type == EventTypes.DOC_ANALYSIS_SUBFLOW:
        pg_name = event_data.get("pg_name", "Unknown")
        subflow_count = event_data.get("subflow_count", 0)
        return {
            "type": "workflow_status",
            "request_id": user_request_id,
            "status": "processing",
            "message": f"🔗 Identified {subflow_count} virtual sub-flows in '{pg_name}'",
            "data": {
                "phase": "ANALYSIS",
                "pg_name": pg_name,
                "subflow_count": subflow_count
            },
            "timestamp": str(event.id)
        }
    
    # === Generation Phase Events ===
    
    elif event.event_type == EventTypes.DOC_GENERATION_SECTION:
        section = event_data.get("metrics", {}).get("section", "Unknown")
        section_labels = {
            "summary": "Executive Summary",
            "diagram": "Flow Diagram",
            "hierarchy_doc": "Detailed Breakdown",
            "io_table": "External Interactions",
            "error_handling_table": "Error Handling"
        }
        label = section_labels.get(section, section)
        
        return {
            "type": "workflow_status",
            "request_id": user_request_id,
            "status": "processing",
            "message": f"📝 Generating: {label}",
            "data": {
                "phase": "GENERATION",
                "section": section,
                "section_label": label
            },
            "timestamp": str(event.id)
        }
    
    elif event.event_type == EventTypes.DOC_GENERATION_DIAGRAM:
        node_count = event_data.get("node_count", 0)
        return {
            "type": "workflow_status",
            "request_id": user_request_id,
            "status": "processing",
            "message": f"📊 Generated Mermaid diagram with {node_count} nodes",
            "data": {
                "phase": "GENERATION",
                "node_count": node_count
            },
            "timestamp": str(event.id)
        }
    
    elif event.event_type == EventTypes.DOC_GENERATION_VALIDATE:
        issues = event_data.get("issues", [])
        issue_count = len(issues)
        if issue_count == 0:
            message = "✅ Documentation validated successfully"
        else:
            message = f"⚠️ Documentation has {issue_count} validation warning(s)"
        
        return {
            "type": "workflow_status",
            "request_id": user_request_id,
            "status": "processing",
            "message": message,
            "data": {
                "phase": "GENERATION",
                "issues": issues,
                "valid": issue_count == 0
            },
            "timestamp": str(event.id)
        }
    
    # === Progress Updates ===
    
    elif event.event_type == EventTypes.DOC_PROGRESS_UPDATE:
        progress_message = event_data.get("progress_message", "Processing...")
        phase = event_data.get("phase", "Unknown")
        return {
            "type": "workflow_status",
            "request_id": user_request_id,
            "status": "processing",
            "message": progress_message,
            "data": {
                "phase": phase,
                "metrics": event_data.get("metrics", {})
            },
            "timestamp": str(event.id)
        }
    
    # Not a documentation event
    return None


def _map_workflow_complete_updated(
    event: WorkflowEvent,
    user_request_id: str,
    event_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Updated WORKFLOW_COMPLETE handler that distinguishes between workflows.
    
    Replace the existing WORKFLOW_COMPLETE handling in _map_event_to_websocket.
    """
    workflow_name = event_data.get("workflow_name", "unguided")
    
    if workflow_name == "flow_documentation":
        # Documentation workflow completion
        final_document = event_data.get("final_document", "")
        metrics = event_data.get("metrics", {})
        validation_issues = event_data.get("validation_issues", [])
        
        return {
            "type": "workflow_complete",
            "request_id": user_request_id,
            "result": {
                "content": final_document,
                "content_type": "markdown",
                "metadata": {
                    "workflow": "flow_documentation",
                    "pgs_documented": metrics.get("generation", {}).get("pgs_documented", 0),
                    "total_duration_ms": metrics.get("total_duration_ms", 0),
                    "discovery_duration_ms": metrics.get("discovery", {}).get("total_duration_ms", 0),
                    "analysis_duration_ms": metrics.get("analysis", {}).get("total_duration_ms", 0),
                    "generation_duration_ms": metrics.get("generation", {}).get("duration_ms", 0),
                    "document_size_bytes": metrics.get("generation", {}).get("document_size_bytes", 0),
                    "validation_issues": validation_issues,
                    "virtual_groups_created": metrics.get("analysis", {}).get("virtual_groups_created", 0),
                    "error_handling_entries": metrics.get("generation", {}).get("error_handling_entries", 0)
                }
            },
            "timestamp": str(event.id)
        }
    
    else:
        # Standard unguided workflow completion
        # (keep existing logic here)
        final_content = event_data.get("final_content", None)
        final_tokens_in = event_data.get("final_tokens_in", 0)
        final_tokens_out = event_data.get("final_tokens_out", 0)
        is_status_report = final_content and "### 📋 Status Report" in final_content
        
        return {
            "type": "workflow_complete",
            "request_id": user_request_id,
            "result": {
                "content": final_content,
                "tokens_in": event_data.get("total_tokens_in", 0),
                "tokens_out": event_data.get("total_tokens_out", 0),
                "tool_calls": [],
                "metadata": {
                    "token_count_in": event_data.get("total_tokens_in", 0),
                    "token_count_out": event_data.get("total_tokens_out", 0),
                    "tool_calls_count": event_data.get("tool_calls_executed", 0),
                    "loop_count": event_data.get("loop_count", 0),
                    "is_status_report": is_status_report,
                    "model_name": event_data.get("model_name", "unknown"),
                    "provider": event_data.get("provider", "unknown"),
                    "messages_in_request": event_data.get("messages_in_request", 0),
                    "elapsed_seconds": event_data.get("elapsed_seconds", 0)
                }
            },
            "timestamp": str(event.id)
        }


# ============================================================================
# Integration Instructions
# ============================================================================
#
# In api/event_bridge.py, update _map_event_to_websocket method:
#
# 1. At the START of the method, check for documentation events:
#
#     doc_message = _map_documentation_events(event, user_request_id, event_data)
#     if doc_message is not None:
#         return doc_message
#
# 2. Replace the WORKFLOW_COMPLETE handling with _map_workflow_complete_updated
#
# 3. Import the new EventTypes at the top of the file (they should already
#    be imported via event_system.py after Phase 1 changes)
#
# ============================================================================

