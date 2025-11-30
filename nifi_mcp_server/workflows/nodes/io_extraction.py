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
    
    # Use cached details if available, otherwise fetch
    proc_details_map = {}
    if cached_proc_details:
        proc_details_map = {pid: cached_proc_details.get(pid, {}) for pid in proc_ids}
        # Check if we have all the data we need (must have component.config.properties)
        missing_ids = [
            pid for pid in proc_ids 
            if not proc_details_map.get(pid, {}).get("component", {}).get("config", {}).get("properties")
        ]
        if missing_ids:
            # Fetch missing ones
            try:
                result = await nifi_tool_caller(
                    "get_nifi_object_details",
                    {
                        "object_type": "processor",
                        "object_ids": missing_ids,
                        "output_format": "doc_optimized",
                        "include_properties": True
                    },
                    prep_res
                )
                if isinstance(result, str):
                    result = json.loads(result)
                if not isinstance(result, list):
                    result = [result] if isinstance(result, dict) else []
                for item in result:
                    if isinstance(item, dict) and item.get("status") == "success":
                        proc_details_map[item.get("id")] = item.get("data", {})
            except Exception as e:
                if logger:
                    logger.warning(f"Failed to fetch missing processor details: {e}")
    else:
        # No cache, fetch all
        try:
            result = await nifi_tool_caller(
                "get_nifi_object_details",
                {
                    "object_type": "processor",
                    "object_ids": proc_ids,
                    "output_format": "doc_optimized",  # Gets business_properties
                    "include_properties": True
                },
                prep_res
            )
            
            if isinstance(result, str):
                result = json.loads(result)
            
            # DEFENSIVE: Ensure result is iterable (list)
            if not isinstance(result, list):
                if logger:
                    logger.warning(f"IO tool result is not a list: {type(result)}")
                result = [result] if isinstance(result, dict) else []
            
            # Map results by ID
            for item in result:
                # DEFENSIVE: Ensure item is a dict before calling .get()
                if isinstance(item, dict) and item.get("status") == "success":
                    proc_details_map[item.get("id")] = item.get("data", {})
        
        except Exception as e:
            if logger:
                logger.warning(f"Failed to fetch detailed IO endpoints: {e}")
            # Fallback to basic extraction
            return extract_io_basic(processors, categorizer)
    
    # Build detailed endpoint info
    endpoints = []
    
    # Extract endpoint details for each IO processor
    for io_proc in io_processors:
        proc_id = io_proc["id"]
        details = proc_details_map.get(proc_id, {})
        # DEFENSIVE: Ensure details is a dict
        if not isinstance(details, dict):
            if logger:
                logger.warning(f"Details for {proc_id} is not a dict: {type(details)}")
            continue
        component = details.get("component", details)
        # DEFENSIVE: Ensure component is a dict
        if not isinstance(component, dict):
            if logger:
                logger.warning(f"Component for {proc_id} is not a dict: {type(component)}")
            component = {}
        config = component.get("config", {})
        # DEFENSIVE: Ensure config is a dict
        if not isinstance(config, dict):
            if logger:
                logger.warning(f"Config for {proc_id} is not a dict: {type(config)}")
            config = {}
        properties = config.get("properties", {})
        # DEFENSIVE: Ensure properties is a dict (critical - this is where the error likely occurs)
        if not isinstance(properties, dict):
            if logger:
                logger.error(f"Properties for {proc_id} is not a dict: {type(properties)}, value: {properties}")
            properties = {}
        business_props = details.get("business_properties", {})
        # DEFENSIVE: Ensure business_props is a dict
        if not isinstance(business_props, dict):
            if logger:
                logger.warning(f"Business properties for {proc_id} is not a dict: {type(business_props)}")
            business_props = {}
        
        # Format processor reference with name, type, ID
        # Ensure we have the right structure for format_processor_reference
        proc_for_formatting = {"id": proc_id}
        if "component" in details:
            proc_for_formatting["component"] = details["component"]
        elif component and component != details:
            # component is already extracted, wrap it
            proc_for_formatting["component"] = component
        else:
            # Fallback: use what we have from io_proc
            proc_for_formatting["component"] = {
                "name": io_proc["name"],
                "type": io_proc["type"],
                "id": proc_id
            }
        
        proc_formatted = format_processor_reference(proc_for_formatting)
        
        endpoint = {
            "processor": io_proc["name"],
            "processor_type": io_proc["type"].split(".")[-1],
            "processor_id": proc_id[:8] if proc_id else "N/A",
            "processor_reference": proc_formatted,  # Full formatted reference
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
                "directory": properties.get("Directory", business_props.get("directory", "")),
                "file_filter": properties.get("File Filter", business_props.get("file_filter", "")),
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
                "url": properties.get("Remote URL", business_props.get("url", "")),
                "method": properties.get("HTTP Method", business_props.get("method", "GET")),
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
                "topic": properties.get("topic", properties.get("Topic Name(s)", business_props.get("topic", ""))),
                "bootstrap_servers": properties.get("bootstrap.servers", business_props.get("bootstrap_servers", "")),
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
                "processor_id": proc_id[:8] if proc_id else "N/A",
                "processor_reference": proc_formatted,
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

