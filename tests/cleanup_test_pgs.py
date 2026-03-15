#!/usr/bin/env python3
"""
Cleanup script to remove leftover test process groups from NiFi.
Run this after test runs to clean up any orphaned test process groups.
"""

import asyncio
import os
import httpx
import logging
from typing import List, Dict, Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Use MCP_SERVER_URL if set, else MCP_SERVER_PORT (default 8000)
BASE_URL = os.environ.get("MCP_SERVER_URL") or f"http://localhost:{os.environ.get('MCP_SERVER_PORT', '8000')}"
HEADERS = {'X-Nifi-Server-Id': 'nifi-local-example'}

# Test process group name patterns to identify and clean up
TEST_PG_PATTERNS = [
    "mcp-test-pg-nifi-local-example",
    "mcp-test-doc-flow-nifi-local-example", 
    "mcp-test-merge-flow-nifi-local-example",
    "CompleteFlowTestPG"
]

async def call_tool(client: httpx.AsyncClient, tool_name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Call MCP tool and return results."""
    try:
        response = await client.post(
            f"{BASE_URL}/call_tool",
            json={"name": tool_name, "arguments": arguments},
            headers=HEADERS
        )
        if response.status_code == 200:
            result = response.json()
            return result if isinstance(result, list) else [result]
        else:
            logger.error(f"HTTP {response.status_code}: {response.text}")
            return []
    except Exception as e:
        logger.error(f"Error calling {tool_name}: {e}")
        return []

async def get_process_groups() -> List[Dict[str, Any]]:
    """Get all process groups in root."""
    async with httpx.AsyncClient() as client:
        result = await call_tool(client, "list_nifi_objects", {
            "object_type": "process_group",
            "parent_process_group_id": "root"
        })
        
        if result and len(result) > 0:
            return result[0].get("process_groups", [])
        return []

async def stop_process_group(client: httpx.AsyncClient, pg_id: str) -> bool:
    """Stop a process group."""
    result = await call_tool(client, "operate_nifi_objects", {
        "operations": [{
            "object_type": "process_group",
            "object_id": pg_id,
            "operation_type": "stop"
        }]
    })
    return result and result[0].get("status") == "success"

async def purge_flowfiles(client: httpx.AsyncClient, pg_id: str) -> bool:
    """Purge flowfiles from a process group."""
    result = await call_tool(client, "purge_flowfiles", {
        "target_id": pg_id,
        "target_type": "process_group",
        "timeout_seconds": 30
    })
    return result and result[0].get("status") == "success"

async def delete_process_group(client: httpx.AsyncClient, pg_id: str, pg_name: str) -> bool:
    """Delete a process group."""
    result = await call_tool(client, "delete_nifi_objects", {
        "objects": [{
            "object_type": "process_group",
            "object_id": pg_id,
            "name": pg_name
        }]
    })
    return result and result[0].get("status") == "success"

async def cleanup_test_process_groups():
    """Main cleanup function."""
    logger.info("🧹 Starting cleanup of test process groups...")
    
    # Get all process groups
    pgs = await get_process_groups()
    if not pgs:
        logger.info("No process groups found or unable to connect to NiFi.")
        return
    
    # Find test process groups
    test_pgs = []
    for pg in pgs:
        pg_name = pg.get("name", "")
        for pattern in TEST_PG_PATTERNS:
            if pattern in pg_name:
                test_pgs.append(pg)
                break
    
    if not test_pgs:
        logger.info("✅ No test process groups found to clean up.")
        return
    
    logger.info(f"🔍 Found {len(test_pgs)} test process groups to clean up:")
    for pg in test_pgs:
        logger.info(f"  - {pg.get('name')} (ID: {pg.get('id')})")
    
    # Clean up each test process group
    async with httpx.AsyncClient() as client:
        for pg in test_pgs:
            pg_id = pg.get("id")
            pg_name = pg.get("name")
            
            logger.info(f"🛑 Cleaning up process group: {pg_name}")
            
            try:
                # Step 1: Stop the process group
                logger.info(f"  Stopping process group...")
                await stop_process_group(client, pg_id)
                
                # Step 2: Purge flowfiles
                logger.info(f"  Purging flowfiles...")
                await purge_flowfiles(client, pg_id)
                
                # Step 3: Delete the process group
                logger.info(f"  Deleting process group...")
                success = await delete_process_group(client, pg_id, pg_name)
                
                if success:
                    logger.info(f"  ✅ Successfully deleted {pg_name}")
                else:
                    logger.error(f"  ❌ Failed to delete {pg_name}")
                    
            except Exception as e:
                logger.error(f"  ❌ Error cleaning up {pg_name}: {e}")
    
    logger.info("🧹 Cleanup completed!")

if __name__ == "__main__":
    asyncio.run(cleanup_test_process_groups()) 