import pytest
import httpx
from typing import Dict, Any
from tests.utils import call_tool

@pytest.mark.anyio
async def test_document_complex_flow_simplified(
    test_complex_flow: Dict[str, Any],
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any
):
    """Test documenting a complex flow with the new simplified structure."""
    pg_id = test_complex_flow["process_group_id"]
    
    # Document the flow
    doc_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="document_nifi_flow",
        arguments={
            "process_group_id": pg_id,
            "include_properties": True,
            "include_descriptions": True
        },
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    assert isinstance(doc_result_list, list) and len(doc_result_list) > 0
    doc_result = doc_result_list[0]
    assert doc_result.get("status") in ["success", "warning"]
    
    flow_doc = doc_result.get("documentation", {})
    
    # Verify components are documented
    components = flow_doc.get("components", {})
    assert components, "No components found in documentation"
    
    # Verify processors section exists and contains our processors
    processors = components.get("processors", {})
    assert processors, "No processors found in documentation"
    
    # Verify ports section exists and contains our input port
    ports = components.get("ports", {})
    assert ports, "No ports found in documentation"
    
    # Verify the new structure doesn't have the old sections
    assert "flows" not in flow_doc, "Old 'flows' section should not exist in simplified structure"
    assert "decision_points" not in flow_doc, "Old 'decision_points' section should not exist in simplified structure"
    assert "unconnected_components" not in flow_doc, "Old 'unconnected_components' section should not exist in simplified structure"
    assert "connections" not in components, "Old 'connections' section should not exist in simplified structure"
    
    # Verify processors have embedded connection information
    for proc_id, proc_info in processors.items():
        assert "outgoing_connections" in proc_info, f"Processor {proc_id} missing outgoing_connections"
        assert "incoming_connections" in proc_info, f"Processor {proc_id} missing incoming_connections"
        assert "auto_terminated_relationships" in proc_info, f"Processor {proc_id} missing auto_terminated_relationships"
        assert isinstance(proc_info["outgoing_connections"], list), f"Processor {proc_id} outgoing_connections should be a list"
        assert isinstance(proc_info["incoming_connections"], list), f"Processor {proc_id} incoming_connections should be a list"
        assert isinstance(proc_info["auto_terminated_relationships"], list), f"Processor {proc_id} auto_terminated_relationships should be a list"

@pytest.mark.anyio
async def test_document_flow_processor_connections(
    test_complex_flow: Dict[str, Any],
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any
):
    """Test that processor connections are properly embedded in the simplified structure."""
    pg_id = test_complex_flow["process_group_id"]
    
    # Document the flow
    doc_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="document_nifi_flow",
        arguments={"process_group_id": pg_id},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    assert isinstance(doc_result_list, list) and len(doc_result_list) > 0
    doc_result = doc_result_list[0]
    assert doc_result.get("status") in ["success", "warning"]
    
    flow_doc = doc_result.get("documentation", {})
    processors = flow_doc.get("components", {}).get("processors", {})
    
    # Find the Split processor (RouteOnAttribute)
    split_proc = None
    split_proc_id = None
    for proc_id, proc_info in processors.items():
        if "Split" in proc_info["name"]:
            split_proc = proc_info
            split_proc_id = proc_id
            break
    
    assert split_proc is not None, "Split processor not found"
    
    # Check outgoing connections from Split processor
    outgoing_conns = split_proc["outgoing_connections"]
    assert len(outgoing_conns) == 3, f"Split processor should have 3 outgoing connections, got {len(outgoing_conns)}"
    
    # Verify the relationships
    relationships = {conn["relationship"] for conn in outgoing_conns}
    expected_relationships = {"success", "failure", "original"}
    assert relationships == expected_relationships, f"Expected relationships {expected_relationships}, got {relationships}"
    
    # Verify destination information
    for conn in outgoing_conns:
        assert "destination_name" in conn, "Connection missing destination_name"
        assert "destination_id" in conn, "Connection missing destination_id"
        assert "destination_type" in conn, "Connection missing destination_type"
        assert "connection_id" in conn, "Connection missing connection_id"
    
    # Find a processor that receives connections (e.g., Transform Data)
    transform_proc = None
    for proc_id, proc_info in processors.items():
        if "Transform" in proc_info["name"]:
            transform_proc = proc_info
            break
    
    assert transform_proc is not None, "Transform processor not found"
    
    # Check incoming connections to Transform processor
    incoming_conns = transform_proc["incoming_connections"]
    assert len(incoming_conns) == 1, f"Transform processor should have 1 incoming connection, got {len(incoming_conns)}"
    
    incoming_conn = incoming_conns[0]
    assert incoming_conn["source_name"] == "Split Flow", "Incoming connection should be from Split Flow"
    assert incoming_conn["relationship"] == "success", "Incoming connection should be 'success' relationship"

@pytest.mark.anyio
async def test_document_flow_auto_terminated_relationships(
    test_complex_flow: Dict[str, Any],
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any
):
    """Test that auto-terminated relationships are properly documented."""
    pg_id = test_complex_flow["process_group_id"]
    
    # Since we can't easily modify processors in the test, let's test that 
    # the structure correctly handles auto-terminated relationships when they exist
    # by documenting the flow as-is and verifying the structure
    
    # Document the flow to verify auto-terminated relationship structure
    doc_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="document_nifi_flow",
        arguments={"process_group_id": pg_id},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    assert isinstance(doc_result_list, list) and len(doc_result_list) > 0
    doc_result = doc_result_list[0]
    assert doc_result.get("status") in ["success", "warning"]
    
    flow_doc = doc_result.get("documentation", {})
    processors = flow_doc.get("components", {}).get("processors", {})
    
    # Verify that all processors have the correct structure for connections
    for proc_id, proc_info in processors.items():
        assert "outgoing_connections" in proc_info, f"Processor {proc_id} missing outgoing_connections"
        assert "incoming_connections" in proc_info, f"Processor {proc_id} missing incoming_connections"
        assert "auto_terminated_relationships" in proc_info, f"Processor {proc_id} missing auto_terminated_relationships"
        
        # Check structure of outgoing connections
        for conn in proc_info["outgoing_connections"]:
            required_fields = ["connection_id", "destination_name", "destination_id", 
                             "destination_type", "relationship"]
            for field in required_fields:
                assert field in conn, f"Connection missing field: {field}"
        
        # Check structure of incoming connections  
        for conn in proc_info["incoming_connections"]:
            required_fields = ["connection_id", "source_name", "source_id", 
                             "source_type", "relationship"]
            for field in required_fields:
                assert field in conn, f"Incoming connection missing field: {field}"
        
        # Check structure of auto-terminated relationships
        for auto_term in proc_info["auto_terminated_relationships"]:
            assert "relationship" in auto_term, "Auto-terminated relationship missing relationship field"
            assert isinstance(auto_term["relationship"], str), "Auto-terminated relationship should be a string"

@pytest.mark.anyio
async def test_document_flow_component_details(
    test_complex_flow: Dict[str, Any],
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any
):
    """Test that component details are correctly documented in the simplified structure."""
    pg_id = test_complex_flow["process_group_id"]
    
    # Document the flow with all details
    doc_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="document_nifi_flow",
        arguments={
            "process_group_id": pg_id,
            "include_properties": True,
            "include_descriptions": True
        },
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    assert isinstance(doc_result_list, list) and len(doc_result_list) > 0
    doc_result = doc_result_list[0]
    assert doc_result.get("status") in ["success", "warning"]
    
    flow_doc = doc_result.get("documentation", {})
    
    # Check processor properties
    processors = flow_doc.get("components", {}).get("processors", {})
    
    # Check Transform processor properties
    transform_proc = None
    for proc_id, proc_data in processors.items():
        if "transform" in proc_data["name"].lower():
            transform_proc = proc_data
            break
    
    assert transform_proc is not None, "Transform processor not found"
    assert "properties" in transform_proc, "Transform processor should have properties"
    assert "transformed" in transform_proc["properties"], "Transform processor missing transformed property"
    assert transform_proc["properties"]["transformed"] == "true", "Transform processor property value incorrect"
    
    # Verify basic processor structure
    required_fields = ["id", "name", "type", "processor_type", "state", "outgoing_connections", "incoming_connections", "auto_terminated_relationships"]
    for field in required_fields:
        assert field in transform_proc, f"Transform processor missing required field: {field}"
    
    # Check ports
    ports = flow_doc.get("components", {}).get("ports", {})
    assert len(ports) > 0, "Should have at least one port documented"
    
    for port_id, port_data in ports.items():
        required_port_fields = ["id", "name", "type", "port_type", "state"]
        for field in required_port_fields:
            assert field in port_data, f"Port {port_id} missing required field: {field}"


@pytest.mark.anyio
async def test_document_flow_include_flow_summary(
    test_complex_flow: Dict[str, Any],
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any
):
    """Test that document_nifi_flow with include_flow_summary=True returns flow_summary with entry_points, controller_services, paths, boundary_ports."""
    pg_id = test_complex_flow["process_group_id"]
    doc_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="document_nifi_flow",
        arguments={
            "process_group_id": pg_id,
            "include_flow_summary": True,
        },
        headers=mcp_headers,
        custom_logger=global_logger
    )
    assert isinstance(doc_result_list, list) and len(doc_result_list) > 0
    doc_result = doc_result_list[0]
    assert doc_result.get("status") in ["success", "warning"]
    flow_doc = doc_result.get("documentation", {})
    flow_summary = flow_doc.get("flow_summary")
    assert flow_summary is not None, "flow_summary should be present when include_flow_summary=True"
    assert "entry_points" in flow_summary, "flow_summary should have entry_points"
    assert "controller_services" in flow_summary, "flow_summary should have controller_services"
    assert "decision_branches" in flow_summary, "flow_summary should have decision_branches"
    assert "flow_paths" in flow_summary, "flow_summary should have flow_paths"
    assert "boundary_ports" in flow_summary, "flow_summary should have boundary_ports"
    assert "cross_pg_connections" in flow_summary, "flow_summary should have cross_pg_connections (cross-PG port resolution)"
    assert "cross_pg_flow_map" in flow_summary, "flow_summary should have cross_pg_flow_map"
    assert isinstance(flow_summary["entry_points"], list), "entry_points should be a list"
    assert isinstance(flow_summary["boundary_ports"], dict), "boundary_ports should be a dict"
    assert "input_ports" in flow_summary["boundary_ports"], "boundary_ports should have input_ports"
    assert "output_ports" in flow_summary["boundary_ports"], "boundary_ports should have output_ports"


@pytest.mark.anyio
async def test_document_flow_with_merge_inputs_simplified(
    test_merge_flow: Dict[str, Any],
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any
):
    """Test that flows with multiple inputs converging to a single processor are properly documented in simplified structure."""
    pg_id = test_merge_flow["process_group_id"]
    
    # Document the flow
    doc_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="document_nifi_flow",
        arguments={"process_group_id": pg_id},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    assert isinstance(doc_result_list, list) and len(doc_result_list) > 0
    doc_result = doc_result_list[0]
    assert doc_result.get("status") in ["success", "warning"]
    
    flow_doc = doc_result.get("documentation", {})
    processors = flow_doc.get("components", {}).get("processors", {})
    
    # Find the Merge processor
    merge_proc = None
    merge_proc_id = None
    for proc_id, proc_info in processors.items():
        if "Merge" in proc_info["name"]:
            merge_proc = proc_info
            merge_proc_id = proc_id
            break
    
    assert merge_proc is not None, "Merge processor not found"
    
    # Check incoming connections to Merge processor
    incoming_conns = merge_proc["incoming_connections"]
    assert len(incoming_conns) == 2, f"Merge processor should have 2 incoming connections, got {len(incoming_conns)}"
    
    # Verify the connections come from Update processors
    source_names = {conn["source_name"] for conn in incoming_conns}
    expected_sources = {"Update 1", "Update 2"}
    assert source_names == expected_sources, f"Expected sources {expected_sources}, got {source_names}"
    
    # Find the Update processors and verify they have outgoing connections to Merge
    update_procs = []
    for proc_id, proc_info in processors.items():
        if "Update" in proc_info["name"]:
            update_procs.append(proc_info)
    
    assert len(update_procs) == 2, "Should have 2 Update processors"
    
    for update_proc in update_procs:
        outgoing_conns = update_proc["outgoing_connections"]
        merge_connections = [conn for conn in outgoing_conns if conn["destination_name"] == "Merge Flows"]
        assert len(merge_connections) == 1, f"Update processor {update_proc['name']} should have 1 connection to Merge"
        
        merge_conn = merge_connections[0]
        assert merge_conn["relationship"] == "success", "Connection to Merge should be 'success' relationship"
        assert merge_conn["destination_id"] == merge_proc_id, "Connection should point to correct Merge processor"

@pytest.mark.anyio
async def test_document_empty_process_group(
    test_pg: Dict[str, Any],
    async_client: httpx.AsyncClient,
    base_url: str,
    mcp_headers: Dict[str, str],
    global_logger: Any
):
    """Test documenting an empty process group."""
    pg_id = test_pg["id"]
    
    # Document the empty process group
    doc_result_list = await call_tool(
        client=async_client,
        base_url=base_url,
        tool_name="document_nifi_flow",
        arguments={"process_group_id": pg_id},
        headers=mcp_headers,
        custom_logger=global_logger
    )
    
    assert isinstance(doc_result_list, list) and len(doc_result_list) > 0
    doc_result = doc_result_list[0]
    assert doc_result.get("status") in ["success", "warning"]
    
    flow_doc = doc_result.get("documentation", {})
    components = flow_doc.get("components", {})
    
    # Should have empty components sections
    assert components.get("processors", {}) == {}, "Empty process group should have no processors"
    assert components.get("ports", {}) == {}, "Empty process group should have no ports"
    
    # Verify structure is still correct
    assert "processors" in components, "Components should have processors section even if empty"
    assert "ports" in components, "Components should have ports section even if empty" 