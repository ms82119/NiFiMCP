"""
IO endpoint extraction logic for NiFi flow documentation.

This module extracts detailed IO endpoint information from processors,
including file paths, URLs, database connections, message queues, etc.
"""

from typing import Dict, Any, List, Optional, Callable
import json
import re

from .component_formatter import format_processor_reference


async def extract_io_endpoints_detailed(
    processors: List[Dict],
    nifi_tool_caller: Callable,
    prep_res: Dict[str, Any],
    categorizer,
    cached_proc_details: Optional[Dict[str, Dict]] = None,
    logger = None
) -> List[Dict]:
    """
    Extract detailed IO endpoint information including file paths, URLs, topics, etc.
    
    This is a critical function - it captures all external interaction points
    that must be documented (files, URLs, databases, message queues, etc.).
    
    Args:
        processors: List of processor dicts
        nifi_tool_caller: Async callable for calling get_nifi_object_details
        prep_res: Preparation context
        categorizer: ProcessorCategorizer instance
        cached_proc_details: Optional cached processor details to avoid duplicate API calls
        logger: Logger instance for logging
    
    Returns:
        List of endpoint dicts with processor info and endpoint details
    """
    io_categories = ["IO_READ", "IO_WRITE"]
    io_processors = []
    
    # Identify IO processors
    for proc in processors:
        proc_type = proc.get("component", {}).get("type", proc.get("type", ""))
        category = categorizer.categorize(proc_type).value
        
        if category in io_categories:
            io_processors.append({
                "id": proc.get("id"),
                "name": proc.get("component", {}).get("name", proc.get("name", "?")),
                "type": proc_type,
                "category": category
            })
    
    if not io_processors:
        return []
    
    # Fetch detailed info for IO processors (using batch mode from Phase 2)
    proc_ids = [p["id"] for p in io_processors]
    
    # Skip API call if no IO processors found
    if not proc_ids:
        return []
    
    # No longer need to fetch from API - flow_graph.processors has all properties
    # proc_details_map is only used for structure, but we get properties from flow_graph
    proc_details_map = {}
    
    # Build detailed endpoint info
    endpoints = []
    
    # Build a map of processors from flow_graph (which have all properties from discovery)
    flow_graph_processors = {}
    for proc in processors:
        proc_id = proc.get("id")
        if proc_id:
            flow_graph_processors[proc_id] = proc
    
    # Extract endpoint details for each IO processor
    for io_proc in io_processors:
        proc_id = io_proc["id"]
        
        # Get properties from flow_graph.processors (discovery data - has all properties)
        flow_graph_proc = flow_graph_processors.get(proc_id, {})
        properties = flow_graph_proc.get("properties", {})
        
        # business_properties not needed - we have full properties from flow_graph
        business_props = {}
        # DEFENSIVE: Ensure business_props is a dict
        if not isinstance(business_props, dict):
            if logger:
                logger.warning(f"Business properties for {proc_id} is not a dict: {type(business_props)}")
            business_props = {}
        
        # Format processor reference with name, type, ID
        # Use flow_graph processor data directly
        flow_graph_proc = flow_graph_processors.get(proc_id, {})
        proc_for_formatting = {
            "id": proc_id,
            "component": {
                "name": flow_graph_proc.get("name", io_proc["name"]),
                "type": flow_graph_proc.get("type", io_proc["type"]),
                "id": proc_id
            }
        }
        
        proc_formatted = format_processor_reference(proc_for_formatting)
        
        endpoint = {
            "processor": io_proc["name"],
            "processor_type": io_proc["type"].split(".")[-1],
            "processor_id": proc_id if proc_id else "N/A",  # Full UUID
            "processor_reference": proc_formatted,  # Full formatted reference (uses short ID in format)
            "type": io_proc["type"].split(".")[-1],  # Keep for backward compat
            "direction": "INPUT" if io_proc["category"] == "IO_READ" else "OUTPUT",
            "endpoint_details": {}
        }
        
        # Extract endpoint-specific details based on processor type
        proc_type_simple = io_proc["type"].split(".")[-1]
        
        # File-based processors
        if proc_type_simple in ["GetFile", "PutFile", "ListFile"]:
            endpoint["endpoint_details"] = {
                "type": "file_system",
                "directory": properties.get("Directory", ""),
                "file_filter": properties.get("File Filter", ""),
                "path": properties.get("Directory", "")
            }
        
        # SFTP processors
        elif proc_type_simple in ["GetSFTP", "PutSFTP", "ListSFTP"]:
            endpoint["endpoint_details"] = {
                "type": "sftp",
                "hostname": properties.get("Hostname", ""),
                "port": properties.get("Port", ""),
                "directory": properties.get("Remote Directory", ""),
                "username": properties.get("Username", "")
            }
        
        # HTTP client processors (outbound)
        elif proc_type_simple in ["InvokeHTTP", "GetHTTP", "PostHTTP"]:
            endpoint["endpoint_details"] = {
                "type": "http",
                "url": properties.get("Remote URL", ""),
                "method": properties.get("HTTP Method", "GET"),
                "ssl_context": properties.get("SSL Context Service", "")
            }
        
        # HTTP server processors (inbound listener)
        elif proc_type_simple == "HandleHttpRequest":
            # Build allowed methods list
            allowed_methods = []
            if properties.get("Allow GET", "").lower() == "true":
                allowed_methods.append("GET")
            if properties.get("Allow POST", "").lower() == "true":
                allowed_methods.append("POST")
            if properties.get("Allow PUT", "").lower() == "true":
                allowed_methods.append("PUT")
            if properties.get("Allow DELETE", "").lower() == "true":
                allowed_methods.append("DELETE")
            if properties.get("Allow HEAD", "").lower() == "true":
                allowed_methods.append("HEAD")
            if properties.get("Allow OPTIONS", "").lower() == "true":
                allowed_methods.append("OPTIONS")
            # Check for additional methods
            additional_methods = properties.get("Additional HTTP Methods")
            if additional_methods:
                allowed_methods.extend([m.strip() for m in additional_methods.split(",") if m.strip()])
            
            # Build URL from port and path
            port = properties.get("Listening Port", "")
            hostname = properties.get("Hostname", "")
            path = properties.get("Allowed Paths", "")
            protocol = "https" if properties.get("SSL Context Service") else "http"
            
            # Construct full URL
            if hostname:
                base_url = f"{protocol}://{hostname}:{port}" if port else f"{protocol}://{hostname}"
            else:
                base_url = f"{protocol}://localhost:{port}" if port else f"{protocol}://localhost"
            
            full_url = f"{base_url}{path}" if path else base_url
            
            endpoint["endpoint_details"] = {
                "type": "http_server",
                "url": full_url,
                "port": port,
                "hostname": hostname or "localhost",
                "path": path,
                "protocol": protocol,
                "allowed_methods": allowed_methods,
                "ssl_context": properties.get("SSL Context Service"),
                "client_authentication": properties.get("Client Authentication", "No Authentication")
            }
        
        # HTTP server processors (outbound response)
        elif proc_type_simple == "HandleHttpResponse":
            status_code = properties.get("HTTP Status Code", "")
            # Status code might be an expression, try to extract numeric value
            status_code_value = status_code
            if isinstance(status_code, str) and not status_code.isdigit():
                # Try to extract number from expression like "${code:defaultValue('200')}"
                match = re.search(r'\d+', status_code)
                if match:
                    status_code_value = match.group()
            
            endpoint["endpoint_details"] = {
                "type": "http_response",
                "status_code": status_code_value,
                "status_code_expression": status_code if status_code != status_code_value else None,
                "http_context_map": properties.get("HTTP Context Map"),
                "response_attributes_regex": properties.get("Attributes to add to the HTTP Response (Regex)")
            }
        
        # Kafka processors
        elif "Kafka" in proc_type_simple or proc_type_simple in ["ConsumeKafka", "PublishKafka"]:
            endpoint["endpoint_details"] = {
                "type": "kafka",
                "topic": properties.get("topic", properties.get("Topic Name(s)", "")),
                "bootstrap_servers": properties.get("bootstrap.servers", ""),
                "consumer_group": properties.get("Group ID", "")
            }
        
        # Database processors
        elif proc_type_simple in ["QueryDatabaseTable", "PutDatabaseRecord", "ExecuteSQL"]:
            endpoint["endpoint_details"] = {
                "type": "database",
                "connection_url": properties.get("Database Connection URL", ""),
                "table": properties.get("Table Name", ""),
                "connection_pool": properties.get("Database Connection Pooling Service", "")
            }
        
        # JMS processors
        elif proc_type_simple in ["ConsumeJMS", "PublishJMS", "GetJMSQueue", "PutJMS"]:
            endpoint["endpoint_details"] = {
                "type": "jms",
                "destination": properties.get("Destination Name", ""),
                "connection_factory": properties.get("Connection Factory Service", "")
            }
        
        # S3 processors
        elif "S3" in proc_type_simple:
            endpoint["endpoint_details"] = {
                "type": "s3",
                "bucket": properties.get("Bucket", ""),
                "object_key": properties.get("Object Key", ""),
                "region": properties.get("Region", "")
            }
        
        # MongoDB processors
        elif proc_type_simple in ["GetMongo", "PutMongo"]:
            endpoint["endpoint_details"] = {
                "type": "mongodb",
                "uri": properties.get("Mongo URI", ""),
                "database": properties.get("Mongo Database Name", ""),
                "collection": properties.get("Mongo Collection Name", "")
            }
        
        # Elasticsearch processors
        elif proc_type_simple in ["GetElasticsearch", "PutElasticsearchRecord"]:
            endpoint["endpoint_details"] = {
                "type": "elasticsearch",
                "url": properties.get("Elasticsearch URL", ""),
                "index": properties.get("Index", ""),
                "type": properties.get("Type", "")
            }
        
        # Generic fallback - try to extract any URL/path-like properties
        else:
            endpoint["endpoint_details"] = {
                "type": "generic",
                "properties": {}
            }
            # Look for common endpoint property names
            for key in ["Remote URL", "URL", "Hostname", "Directory", "Path", 
                       "Connection URL", "Endpoint", "Address"]:
                if key in properties and properties[key]:
                    endpoint["endpoint_details"]["properties"][key] = properties[key]
        
        endpoints.append(endpoint)
    
    return endpoints


def extract_io_basic(
    processors: List[Dict],
    categorizer
) -> List[Dict]:
    """Fallback basic IO extraction if detailed fetch fails."""
    io_categories = ["IO_READ", "IO_WRITE"]
    endpoints = []
    
    for proc in processors:
        proc_type = proc.get("component", {}).get("type", proc.get("type", ""))
        category = categorizer.categorize(proc_type).value
        
        if category in io_categories:
            proc_name = proc.get("component", {}).get("name", proc.get("name", "?"))
            proc_id = proc.get("id", "")
            proc_formatted = format_processor_reference(proc)
            
            endpoints.append({
                "processor": proc_name,
                "processor_type": proc_type.split(".")[-1],
                "processor_id": proc_id if proc_id else "N/A",  # Full UUID
                "processor_reference": proc_formatted,  # Uses short ID in format
                "type": proc_type.split(".")[-1],  # Keep for backward compat
                "direction": "INPUT" if category == "IO_READ" else "OUTPUT",
                "endpoint_details": {"type": "unknown", "note": "Details not available"}
            })
    
    return endpoints


def extract_io_from_categorized(
    categorized: Dict[str, List[str]],
    processors: List[Dict],
    categorizer
) -> List[Dict]:
    """
    DEPRECATED: Use extract_io_endpoints_detailed() instead.
    
    Kept for backward compatibility but should be replaced.
    """
    return extract_io_basic(processors, categorizer)

