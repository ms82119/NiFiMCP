#!/usr/bin/env python3
"""
Simple LLM integration debug script.

This script tests the LLM integration with minimal token usage to identify issues.
"""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from nifi_mcp_server.workflows.definitions.unguided_mimic import get_workflow_chat_manager
from nifi_chat_ui.llm.chat_manager import ChatManager
from config import settings

def test_simple_llm_call():
    """Test a simple LLM call without tools."""
    print("🔍 Testing simple LLM call without tools...")
    
    try:
        # Get chat manager
        chat_manager = get_workflow_chat_manager()
        print(f"✅ ChatManager initialized with providers: {list(chat_manager.providers.keys())}")
        
        # Test simple call
        result = chat_manager.get_llm_response(
            messages=[{"role": "user", "content": "Say 'Hello World'"}],
            system_prompt="You are a helpful assistant. Keep responses very short.",
            provider="openai",
            model_name="gpt-4o-mini",
            user_request_id="debug-test-123",
            action_id="debug-action-456",
            selected_nifi_server_id="nifi-local-example",
            tools=None  # No tools for this test
        )
        
        print(f"📊 LLM Response: {result}")
        
        if result.get("error"):
            print(f"❌ LLM call failed: {result['error']}")
            return False
        
        if result.get("content"):
            print(f"✅ LLM call successful! Response: {result['content']}")
            print(f"📈 Tokens used: {result.get('token_count_in', 0)} in, {result.get('token_count_out', 0)} out")
            return True
        else:
            print(f"⚠️  LLM call returned no content: {result}")
            return False
            
    except Exception as e:
        print(f"❌ Exception during LLM call: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_llm_with_tools():
    """Test LLM call with tools."""
    print("\n🔧 Testing LLM call with tools...")
    
    try:
        chat_manager = get_workflow_chat_manager()
        
        # Test with tools
        result = chat_manager.get_llm_response(
            messages=[{"role": "user", "content": "List available tools"}],
            system_prompt="You are a helpful assistant. If tools are available, list them.",
            provider="openai",
            model_name="gpt-4o-mini",
            user_request_id="debug-test-456",
            action_id="debug-action-789",
            selected_nifi_server_id="nifi-local-example",
            tools="auto"  # Fetch tools automatically
        )
        
        print(f"📊 LLM Response with tools: {result}")
        
        if result.get("error"):
            print(f"❌ LLM call with tools failed: {result['error']}")
            return False
        
        print(f"✅ LLM call with tools successful!")
        print(f"📈 Tokens used: {result.get('token_count_in', 0)} in, {result.get('token_count_out', 0)} out")
        return True
        
    except Exception as e:
        print(f"❌ Exception during LLM call with tools: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_workflow_node_llm_call():
    """Test LLM call through the workflow node."""
    print("\n🔄 Testing LLM call through workflow node...")
    
    try:
        from nifi_mcp_server.workflows.definitions.unguided_mimic import InitializeExecutionNode
        
        node = InitializeExecutionNode()
        
        # Test prep
        prep_res = {
            "provider": "openai",
            "model_name": "gpt-4o-mini",
            "system_prompt": "You are a helpful assistant. Keep responses very short.",
            "user_request_id": "debug-test-789",
            "messages": [{"role": "user", "content": "Say 'Hello from workflow'"}],
            "selected_nifi_server_id": "nifi-local-example"
        }
        
        print("📝 Testing node preparation...")
        prep_result = node.prep(prep_res)
        print(f"✅ Prep result keys: {list(prep_result.keys())}")
        
        print("🚀 Testing node execution...")
        exec_result = node.exec(prep_res)
        print(f"📊 Execution result: {exec_result}")
        
        if exec_result.get("status") == "success":
            print("✅ Workflow node execution successful!")
            return True
        else:
            print(f"❌ Workflow node execution failed: {exec_result}")
            return False
            
    except Exception as e:
        print(f"❌ Exception during workflow node test: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all debug tests."""
    print("🚀 Starting LLM Integration Debug Tests")
    print("=" * 50)
    
    # Check configuration
    print(f"🔧 OpenAI API Key configured: {'Yes' if settings.OPENAI_API_KEY else 'No'}")
    print(f"🔧 Available OpenAI models: {settings.OPENAI_MODELS}")
    print(f"🔧 Using model: gpt-4o-mini (cost-effective for testing)")
    
    if not settings.OPENAI_API_KEY:
        print("❌ No OpenAI API key configured. Please check config.yaml")
        return
    
    # Run tests
    tests = [
        ("Simple LLM Call", test_simple_llm_call),
        ("LLM with Tools", test_llm_with_tools),
        ("Workflow Node LLM", test_workflow_node_llm_call)
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n{'='*20} {test_name} {'='*20}")
        try:
            success = test_func()
            results.append((test_name, success))
        except Exception as e:
            print(f"❌ Test {test_name} crashed: {e}")
            results.append((test_name, False))
    
    # Summary
    print(f"\n{'='*50}")
    print("📋 TEST SUMMARY")
    print("=" * 50)
    
    for test_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} {test_name}")
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    print(f"\n🎯 Overall: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! LLM integration is working.")
    else:
        print("🔧 Some tests failed. Check the output above for details.")

if __name__ == "__main__":
    main() 