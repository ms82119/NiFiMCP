#!/usr/bin/env python3
"""
Automated test script for documentation workflow.

This script tests the documentation workflow by:
1. Making API calls to the chat endpoint
2. Monitoring workflow status
3. Checking logs for errors
4. Validating the final output

Usage:
    python test_doc_workflow_auto.py [process_group_id] [nifi_server_id] [--token TOKEN]

Options:
    --token TOKEN          OIDC access token (if not provided, checks nifi_tokens.json)
    --check-token-only    Only check if token exists and is valid, don't run test
    --submit-token TOKEN   Submit token to API and exit

Token Management:
    Tokens are stored in nifi_tokens.json (shared with the application).
    If token expires, you can:
    1. Use --token to provide a new token
    2. Use --submit-token to store it via API
    3. Or manually edit nifi_tokens.json

Example:
    # Check if token exists
    python test_doc_workflow_auto.py --check-token-only proserve-f
    
    # Submit a new token
    python test_doc_workflow_auto.py --submit-token YOUR_TOKEN proserve-f
    
    # Run test with token from file
    python test_doc_workflow_auto.py de659067-d65e-3e66-836b-f73e841862af proserve-f
    
    # Run test with explicit token
    python test_doc_workflow_auto.py de659067-d65e-3e66-836b-f73e841862af proserve-f --token YOUR_TOKEN
"""

import sys
import os
import time
import json
import httpx
import asyncio
import argparse
from pathlib import Path
from typing import Optional, Dict, Any

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Configuration
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:3000")
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8000")
TIMEOUT_SECONDS = 300  # 5 minutes max for workflow
POLL_INTERVAL = 2  # Check status every 2 seconds
TOKEN_STORE_PATH = project_root / "nifi_tokens.json"


def get_stored_token(server_id: str) -> Optional[str]:
    """Get stored token from nifi_tokens.json file."""
    if not TOKEN_STORE_PATH.exists():
        return None
    
    try:
        with open(TOKEN_STORE_PATH, 'r') as f:
            tokens = json.load(f)
            return tokens.get(server_id)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Could not read token store: {e}")
        return None


def store_token_locally(server_id: str, token: str):
    """Store token in nifi_tokens.json file."""
    tokens = {}
    if TOKEN_STORE_PATH.exists():
        try:
            with open(TOKEN_STORE_PATH, 'r') as f:
                tokens = json.load(f)
        except (json.JSONDecodeError, IOError):
            tokens = {}
    
    tokens[server_id] = token
    
    try:
        with open(TOKEN_STORE_PATH, 'w') as f:
            json.dump(tokens, f, indent=2)
        print(f"✅ Token stored locally in {TOKEN_STORE_PATH}")
    except IOError as e:
        print(f"Warning: Could not write token store: {e}")


async def check_api_server() -> bool:
    """Check if API server is reachable."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{API_BASE_URL}/health")
            if response.status_code == 200:
                return True
    except Exception:
        pass
    return False


async def submit_token_to_api(server_id: str, token: str) -> bool:
    """Submit token to API endpoint."""
    # First check if API server is reachable
    if not await check_api_server():
        print(f"\n❌ Cannot reach API server at {API_BASE_URL}")
        print(f"   Make sure the API server is running.")
        print(f"   You can start it with: python -m api.main")
        return False
    
    url = f"{API_BASE_URL}/api/settings/nifi-servers/{server_id}/credentials"
    
    payload = {"token": token}
    
    print(f"\n📤 Submitting token to API...")
    print(f"   Server: {server_id}")
    print(f"   Token length: {len(token)}")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
            
            if result.get("status") == "success":
                print(f"✅ Token submitted successfully")
                return True
            else:
                print(f"⚠️  Unexpected response: {result}")
                return False
    except httpx.HTTPStatusError as e:
        error_detail = f"HTTP {e.response.status_code}"
        try:
            error_body = e.response.json()
            error_detail += f": {error_body.get('detail', error_body)}"
        except:
            error_detail += f": {e.response.text[:200]}"
        print(f"❌ Error submitting token: {error_detail}")
        print(f"   URL: {url}")
        return False
    except httpx.ConnectError as e:
        print(f"❌ Cannot connect to API server at {API_BASE_URL}")
        print(f"   Error: {e}")
        print(f"   Make sure the API server is running on {API_BASE_URL}")
        return False
    except httpx.HTTPError as e:
        print(f"❌ Error submitting token: {type(e).__name__}: {e}")
        print(f"   URL: {url}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error submitting token: {type(e).__name__}: {e}")
        return False


async def check_token_validity(server_id: str, token: Optional[str] = None) -> Dict[str, Any]:
    """Check if token exists and optionally validate it."""
    result = {
        "has_token": False,
        "token_source": None,
        "token_length": 0,
        "is_valid": None,
        "message": ""
    }
    
    # Get token if not provided
    if not token:
        token = get_stored_token(server_id)
        if token:
            result["token_source"] = "nifi_tokens.json"
        else:
            result["message"] = f"No token found for server {server_id}"
            return result
    else:
        result["token_source"] = "command_line"
    
    result["has_token"] = True
    result["token_length"] = len(token)
    
    # Try to validate token by checking server health
    print(f"\n🔍 Checking token validity...")
    print(f"   Server: {server_id}")
    print(f"   Token source: {result['token_source']}")
    print(f"   Token length: {result['token_length']}")
    
    # Submit token if not already stored
    if result["token_source"] == "command_line":
        await submit_token_to_api(server_id, token)
        store_token_locally(server_id, token)
    
    # Try health check
    try:
        url = f"{API_BASE_URL}/api/settings/nifi-server-health/{server_id}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            if response.status_code == 200:
                health = response.json()
                if health.get("status") == "healthy":
                    result["is_valid"] = True
                    result["message"] = "Token is valid - server health check passed"
                else:
                    result["is_valid"] = False
                    result["message"] = f"Token may be invalid - {health.get('message', 'Unknown error')}"
            else:
                result["is_valid"] = False
                result["message"] = f"Health check failed with status {response.status_code}"
    except Exception as e:
        result["is_valid"] = None
        result["message"] = f"Could not validate token: {e}"
    
    return result


async def get_available_nifi_servers() -> list:
    """Get list of available NiFi servers."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Try to get servers from MCP server
            response = await client.get(f"{MCP_SERVER_URL}/tools")
            if response.status_code == 200:
                # Parse response to find server info
                # This is a simplified version - adjust based on actual API
                return ["proserve-f"]  # Default for now
    except Exception as e:
        print(f"Warning: Could not fetch NiFi servers: {e}")
    return ["proserve-f"]  # Fallback


async def find_test_process_group(nifi_server_id: str) -> Optional[str]:
    """Try to find a test process group ID."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Call list_nifi_objects to find a PG
            # This would require MCP tool execution - simplified for now
            print("Note: Auto-detection of test PG not implemented yet")
            return None
    except Exception as e:
        print(f"Warning: Could not find test PG: {e}")
    return None


async def submit_documentation_request(
    process_group_id: str,
    nifi_server_id: str,
    provider: str = "openai",
    model_name: str = "gpt-4o-mini"
) -> str:
    """Submit a documentation workflow request and return request_id."""
    url = f"{API_BASE_URL}/api/chat/"
    
    payload = {
        "content": f"Document process group {process_group_id}",
        "workflow_name": "flow_documentation",
        "process_group_id": process_group_id,
        "selected_nifi_server_id": nifi_server_id,
        "provider": provider,
        "model_name": model_name,
        "auto_prune_history": False,
        "max_tokens_limit": 32000,
        "max_loop_iterations": 10
    }
    
    print(f"\n📤 Submitting documentation request...")
    print(f"   Process Group: {process_group_id}")
    print(f"   NiFi Server: {nifi_server_id}")
    print(f"   Provider: {provider}, Model: {model_name}")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
        
        request_id = result.get("request_id")
        if not request_id:
            raise ValueError(f"No request_id in response: {result}")
        
        print(f"✅ Request submitted: {request_id}")
        return request_id


async def check_workflow_status(request_id: str) -> Dict[str, Any]:
    """Check the status of a workflow execution."""
    url = f"{API_BASE_URL}/api/chat/workflows/{request_id}"
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(url)
            if response.status_code == 404:
                return {"status": "not_found", "message": "Workflow not found in database"}
            response.raise_for_status()
            data = response.json()
            # Normalize status field
            if "status" not in data:
                # Try to infer from other fields
                if "end_time" in data and data["end_time"]:
                    data["status"] = "completed"
                elif "start_time" in data:
                    data["status"] = "running"
            return data
        except httpx.HTTPError as e:
            return {"status": "error", "error": str(e)}


async def get_chat_history(request_id: str) -> Dict[str, Any]:
    """Get chat history for a request."""
    url = f"{API_BASE_URL}/api/chat/history?request_id={request_id}"
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def check_logs_for_errors(request_id: str) -> list:
    """Check log files for errors related to this request."""
    errors = []
    log_dir = project_root / "logs"
    
    if not log_dir.exists():
        return errors
    
    # Check workflow.log for errors
    workflow_log = log_dir / "workflow.log"
    if workflow_log.exists():
        with open(workflow_log, 'r') as f:
            lines = f.readlines()
            for i, line in enumerate(lines[-100:], start=len(lines)-99):  # Last 100 lines
                if request_id in line and ("ERROR" in line or "error" in line.lower()):
                    errors.append(f"workflow.log:{i}: {line.strip()}")
    
    # Check server.log for errors
    server_log = log_dir / "server.log"
    if server_log.exists():
        with open(server_log, 'r') as f:
            lines = f.readlines()
            for i, line in enumerate(lines[-200:], start=len(lines)-199):  # Last 200 lines
                if request_id in line and ("ERROR" in line or "Exception" in line or "Traceback" in line):
                    errors.append(f"server.log:{i}: {line.strip()[:200]}")
    
    return errors


async def monitor_workflow(request_id: str) -> Dict[str, Any]:
    """Monitor workflow execution until completion or timeout."""
    print(f"\n⏳ Monitoring workflow execution...")
    print(f"   Request ID: {request_id}")
    print(f"   Timeout: {TIMEOUT_SECONDS}s, Poll interval: {POLL_INTERVAL}s")
    
    start_time = time.time()
    last_status = None
    error_count = 0
    
    while True:
        elapsed = time.time() - start_time
        
        if elapsed > TIMEOUT_SECONDS:
            print(f"\n⏱️  Timeout after {TIMEOUT_SECONDS}s")
            return {
                "status": "timeout",
                "elapsed_seconds": elapsed,
                "message": "Workflow did not complete within timeout"
            }
        
        # Check workflow status
        try:
            status = await check_workflow_status(request_id)
            current_status = status.get("status", "unknown")
            
            if current_status != last_status:
                print(f"   [{elapsed:.1f}s] Status: {current_status}")
                last_status = current_status
            
            # Check for completion
            if current_status in ["completed", "success", "error", "failed"]:
                print(f"\n✅ Workflow finished with status: {current_status}")
                return {
                    "status": current_status,
                    "elapsed_seconds": elapsed,
                    "workflow_data": status
                }
            
            # Check for errors in logs periodically
            if int(elapsed) % 10 == 0:  # Every 10 seconds
                log_errors = await check_logs_for_errors(request_id)
                if log_errors:
                    error_count += len(log_errors)
                    print(f"   ⚠️  Found {len(log_errors)} error(s) in logs")
                    for err in log_errors[:3]:  # Show first 3
                        print(f"      {err}")
        
        except Exception as e:
            print(f"   ⚠️  Error checking status: {e}")
        
        await asyncio.sleep(POLL_INTERVAL)


async def validate_output(request_id: str) -> Dict[str, Any]:
    """Validate the workflow output."""
    print(f"\n🔍 Validating workflow output...")
    
    validation_results = {
        "request_id": request_id,
        "has_final_document": False,
        "document_size": 0,
        "has_summary": False,
        "has_diagram": False,
        "has_io_table": False,
        "has_error_handling": False,
        "pg_summaries_count": 0,
        "errors": []
    }
    
    try:
        # Get chat history to find final response
        history = await get_chat_history(request_id)
        messages = history.get("messages", [])
        
        # Find assistant messages with final document
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                
                # Check for documentation sections
                if "# NiFi Flow Documentation" in content:
                    validation_results["has_final_document"] = True
                    validation_results["document_size"] = len(content)
                    
                    # Check for required sections
                    if "## Executive Summary" in content or "Executive Summary" in content:
                        validation_results["has_summary"] = True
                    
                    if "```mermaid" in content or "flowchart" in content or "graph" in content:
                        validation_results["has_diagram"] = True
                    
                    if "## External Interactions" in content or "Data Inputs" in content:
                        validation_results["has_io_table"] = True
                    
                    if "## Error Handling" in content or "Handled Errors" in content:
                        validation_results["has_error_handling"] = True
                    
                    break
        
        # Check workflow status for additional info
        workflow_status = await check_workflow_status(request_id)
        
        # Try to extract result data if stored as JSON string
        result_str = workflow_status.get("result")
        if result_str:
            try:
                if isinstance(result_str, str):
                    workflow_data = json.loads(result_str)
                else:
                    workflow_data = result_str
                
                # Check for pg_summaries in result
                shared_state = workflow_data.get("shared_state", {})
                if "pg_summaries" in shared_state:
                    validation_results["pg_summaries_count"] = len(shared_state.get("pg_summaries", {}))
            except (json.JSONDecodeError, TypeError):
                pass
        
        # Check for errors
        if workflow_status.get("status") == "error":
            validation_results["errors"].append(workflow_status.get("error", "Unknown error"))
        
        # Check logs for errors
        log_errors = await check_logs_for_errors(request_id)
        if log_errors:
            validation_results["errors"].extend(log_errors)
    
    except Exception as e:
        validation_results["errors"].append(f"Validation error: {e}")
    
    return validation_results


async def debug_shared_state(request_id: str) -> None:
    """Print internal workflow shared state (for debugging)."""
    print(f"\n🔎 Debugging shared workflow state...")
    print(f"   Request ID: {request_id}")
    
    status = await check_workflow_status(request_id)
    result_field = status.get("result")
    
    workflow_data: Dict[str, Any] = {}
    
    if not result_field:
        print("   ⚠️ No 'result' field found in workflow status.")
    else:
        try:
            if isinstance(result_field, str):
                workflow_data = json.loads(result_field)
            elif isinstance(result_field, dict):
                workflow_data = result_field
            else:
                print(f"   ⚠️ Unsupported result type: {type(result_field)}")
        except Exception as e:
            print(f"   ⚠️ Failed to parse result field as JSON: {e}")
    
    shared = workflow_data.get("shared_state", {}) if isinstance(workflow_data, dict) else {}
    
    if not shared:
        print("   ⚠️ No shared_state found in workflow result.")
        return
    
    pg_summaries = shared.get("pg_summaries", {})
    doc_sections = shared.get("doc_sections", {})
    final_document = shared.get("final_document", "") or shared.get("final_doc", "")
    
    print("\n   📂 Shared State Overview:")
    print(f"   - Keys: {sorted(shared.keys())}")
    print(f"   - pg_summaries count: {len(pg_summaries) if isinstance(pg_summaries, dict) else 'N/A'}")
    print(f"   - doc_sections keys: {list(doc_sections.keys()) if isinstance(doc_sections, dict) else 'N/A'}")
    print(f"   - final_document length: {len(final_document) if isinstance(final_document, str) else 'N/A'}")
    
    # Show sample PG summaries
    if isinstance(pg_summaries, dict) and pg_summaries:
        sample_keys = list(pg_summaries.keys())[:3]
        print("\n   🧩 Sample pg_summaries entries:")
        for key in sample_keys:
            summary = pg_summaries.get(key, {})
            print(f"     - PG {key}:")
            if isinstance(summary, dict):
                print(f"         name: {summary.get('name')}")
                print(f"         virtual: {summary.get('virtual')}")
                print(f"         has_summary_text: {bool(summary.get('summary'))}")
                print(f"         io_endpoints: {len(summary.get('io_endpoints', []))}")
                print(f"         error_handling: {len(summary.get('error_handling', []))}")
            else:
                print(f"         (non-dict summary of type {type(summary)})")
    else:
        print("\n   ⚠️ No pg_summaries available.")
    
    # Show doc_sections overview
    if isinstance(doc_sections, dict) and doc_sections:
        print("\n   📑 doc_sections overview:")
        for name, section in doc_sections.items():
            if isinstance(section, str):
                length = len(section)
            elif isinstance(section, dict):
                length = len(json.dumps(section))
            else:
                length = 0
            print(f"     - {name}: type={type(section).__name__}, length={length}")
    else:
        print("\n   ⚠️ No doc_sections available.")
    
    # Print a small preview of the final document, if present
    if isinstance(final_document, str) and final_document:
        preview = final_document[:800]
        print("\n   📄 Final document preview (first 800 chars):")
        print("   " + "-" * 58)
        for line in preview.splitlines():
            print("   " + line)
        if len(final_document) > 800:
            print("   ... [truncated]")
        print("   " + "-" * 58)
    else:
        print("\n   ⚠️ No final_document content in shared_state.")


def print_validation_results(results: Dict[str, Any]):
    """Print validation results in a readable format."""
    print(f"\n{'='*60}")
    print(f"📊 Validation Results")
    print(f"{'='*60}")
    
    print(f"\n✅ Checks Passed:")
    checks = [
        ("Final Document Generated", results["has_final_document"]),
        ("Executive Summary", results["has_summary"]),
        ("Mermaid Diagram", results["has_diagram"]),
        ("IO Endpoints Table", results["has_io_table"]),
        ("Error Handling Table", results["has_error_handling"]),
    ]
    
    for check_name, passed in checks:
        status = "✅" if passed else "❌"
        print(f"   {status} {check_name}")
    
    print(f"\n📈 Metrics:")
    print(f"   Document Size: {results['document_size']:,} bytes")
    print(f"   PG Summaries: {results['pg_summaries_count']}")
    
    if results["errors"]:
        print(f"\n⚠️  Errors Found ({len(results['errors'])}):")
        for i, error in enumerate(results["errors"][:5], 1):  # Show first 5
            print(f"   {i}. {error[:150]}")
        if len(results["errors"]) > 5:
            print(f"   ... and {len(results['errors']) - 5} more")
    else:
        print(f"\n✅ No errors found!")
    
    print(f"\n{'='*60}")


async def main():
    """Main test execution."""
    parser = argparse.ArgumentParser(
        description="Automated test script for documentation workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Token Management:
  Tokens are stored in nifi_tokens.json (shared with the application).
  If your token expires (~5 minutes), you can:
  1. Use --token to provide a new token for this run
  2. Use --submit-token to store it permanently via API
  3. Or manually edit nifi_tokens.json

Examples:
  # Check if token exists and is valid
  python test_doc_workflow_auto.py --check-token-only proserve-f
  
  # Submit a new token
  python test_doc_workflow_auto.py --submit-token YOUR_TOKEN proserve-f
  
  # Run test (uses token from nifi_tokens.json)
  python test_doc_workflow_auto.py PG_ID proserve-f
  
  # Run test with explicit token
  python test_doc_workflow_auto.py PG_ID proserve-f --token YOUR_TOKEN
        """
    )
    
    parser.add_argument("process_group_id", nargs="?", help="Process group ID to document")
    parser.add_argument("nifi_server_id", nargs="?", help="NiFi server ID (e.g., proserve-f)")
    parser.add_argument("--token", help="OIDC access token (overrides stored token)")
    parser.add_argument("--check-token-only", action="store_true", 
                       help="Only check if token exists and is valid, don't run test")
    parser.add_argument("--submit-token", metavar="TOKEN",
                       help="Submit token to API and exit (requires nifi_server_id)")
    parser.add_argument("--debug-shared", action="store_true",
                       help="After run, print internal shared workflow state (pg_summaries, doc_sections, final_document)")
    
    args = parser.parse_args()
    
    print("="*60)
    print("🧪 Automated Documentation Workflow Test")
    print("="*60)
    
    # Handle token submission mode
    if args.submit_token:
        if not args.nifi_server_id:
            print("❌ Error: nifi_server_id is required when using --submit-token")
            print("   Usage: python test_doc_workflow_auto.py --submit-token TOKEN nifi_server_id")
            return 1
        
        success = await submit_token_to_api(args.nifi_server_id, args.submit_token)
        if success:
            store_token_locally(args.nifi_server_id, args.submit_token)
        return 0 if success else 1
    
    # Handle token check mode
    if args.check_token_only:
        if not args.nifi_server_id:
            print("❌ Error: nifi_server_id is required when using --check-token-only")
            return 1
        
        result = await check_token_validity(args.nifi_server_id, args.token)
        
        print(f"\n{'='*60}")
        print(f"📋 Token Status")
        print(f"{'='*60}")
        print(f"   Has Token: {'✅ Yes' if result['has_token'] else '❌ No'}")
        if result['has_token']:
            print(f"   Source: {result['token_source']}")
            print(f"   Length: {result['token_length']} characters")
            if result['is_valid'] is True:
                print(f"   Validity: ✅ Valid")
            elif result['is_valid'] is False:
                print(f"   Validity: ❌ Invalid")
            else:
                print(f"   Validity: ⚠️  Unknown")
        print(f"   Message: {result['message']}")
        print(f"{'='*60}\n")
        
        return 0 if result['has_token'] and result['is_valid'] else 1
    
    # Normal test execution
    process_group_id = args.process_group_id
    nifi_server_id = args.nifi_server_id
    
    # Get NiFi server if not provided
    if not nifi_server_id:
        servers = await get_available_nifi_servers()
        if servers:
            nifi_server_id = servers[0]
            print(f"📡 Using NiFi server: {nifi_server_id}")
        else:
            print("❌ Error: No NiFi server available")
            return 1
    
    # Check token before proceeding
    print(f"\n🔐 Checking authentication token...")
    token = args.token or get_stored_token(nifi_server_id)
    
    if not token:
        print(f"❌ Error: No token found for server {nifi_server_id}")
        print(f"\n   To provide a token, use one of these methods:")
        print(f"   1. --token TOKEN (for this run only)")
        print(f"   2. --submit-token TOKEN {nifi_server_id} (to store permanently)")
        print(f"   3. Manually edit {TOKEN_STORE_PATH}")
        print(f"\n   Example:")
        print(f"   python test_doc_workflow_auto.py --submit-token YOUR_TOKEN {nifi_server_id}")
        return 1
    
    # Submit token if provided via command line
    if args.token:
        print(f"   Using token from command line")
        success = await submit_token_to_api(nifi_server_id, token)
        if not success:
            print(f"\n❌ Failed to submit token. Cannot proceed without valid token.")
            return 1
        store_token_locally(nifi_server_id, token)
    else:
        print(f"   Using stored token from {TOKEN_STORE_PATH}")
        # Ensure it's also in API (in case API was restarted)
        success = await submit_token_to_api(nifi_server_id, token)
        if not success:
            print(f"\n⚠️  Warning: Failed to submit token to API, but continuing with stored token.")
            print(f"   The workflow may fail if the API server doesn't have the token.")
            print(f"   Consider using --submit-token to refresh the token.")
    
    # Get process group ID if not provided
    if not process_group_id:
        # Try to find a test PG
        pg_id = await find_test_process_group(nifi_server_id)
        if not pg_id:
            print("❌ Error: process_group_id is required")
            print("   Usage: python test_doc_workflow_auto.py <process_group_id> [nifi_server_id]")
            print("\n   Example:")
            print("   python test_doc_workflow_auto.py de659067-d65e-3e66-836b-f73e841862af proserve-f")
            return 1
        process_group_id = pg_id
    
    # Validate token right before submitting workflow (tokens expire quickly)
    print(f"\n🔍 Validating token before workflow submission...")
    validation = await check_token_validity(nifi_server_id, token)
    if validation.get("is_valid") is False:
        print(f"⚠️  Warning: Token validation failed: {validation.get('message')}")
        print(f"   The token may have expired. Consider refreshing it with --submit-token")
        response = input("   Continue anyway? (y/N): ").strip().lower()
        if response != 'y':
            print("   Aborting test.")
            return 1
    elif validation.get("is_valid") is True:
        print(f"✅ Token validated successfully")
    
    try:
        # Step 1: Submit request
        request_id = await submit_documentation_request(
            process_group_id,
            nifi_server_id
        )
        
        # Step 2: Monitor execution
        result = await monitor_workflow(request_id)
        
        # Optional: Debug internal shared state before validation
        if args.debug_shared:
            await debug_shared_state(request_id)
        
        # Step 3: Validate output (externalized form)
        validation = await validate_output(request_id)
        validation["execution_result"] = result
        
        # Step 4: Print results
        print_validation_results(validation)
        
        # Determine exit code
        if validation["has_final_document"] and not validation["errors"]:
            print("\n✅ Test PASSED: Documentation generated successfully!")
            return 0
        elif validation["errors"]:
            print("\n❌ Test FAILED: Errors found during execution")
            return 1
        else:
            print("\n⚠️  Test INCOMPLETE: Documentation may be missing sections")
            return 2
    
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        return 130
    except Exception as e:
        print(f"\n❌ Test FAILED with exception: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

