# Chat Manager Refactoring Plan

## Executive Summary

The current `chat_manager.py` has grown into a monolithic, complex file that handles multiple LLM providers with inconsistent patterns, manual schema conversions, and overly complex error handling. This refactoring plan addresses the root causes and provides a path to a cleaner, more maintainable architecture.

## Current Problems Analysis

### 1. **Architectural Issues**
- **Monolithic Design**: Single file handling 4+ LLM providers with 2000+ lines
- **Inconsistent Patterns**: Each provider has evolved its own error handling and response parsing logic
- **Manual Schema Conversion**: Complex, error-prone conversion between OpenAI JSON Schema and Gemini Protocol Buffer formats
- **Fighting the Framework**: Custom solutions instead of using official libraries and best practices

### 2. **Provider-Specific Complexity**
- **Gemini**: MapComposite handling, complex schema cleaning, malformed function call detection
- **Anthropic**: Complex message format conversion, tool format conversion, nested error parsing
- **OpenAI/Perplexity**: Relatively simple but still has custom error handling
- **Inconsistent Error Handling**: Each provider has different error parsing strategies

### 3. **Integration Problems**
- **MCP Schema Inconsistencies**: MCP server providing inconsistent types (e.g., `timeout_seconds: string` vs `timeout_seconds: number`)
- **Manual Tool Conversion**: Instead of using proper MCP integration libraries
- **Complex Response Parsing**: Each provider requires custom response parsing logic

## Root Cause Analysis

### 1. **Missing Official Integration**
The complexity stems from not using official solutions:
- **Google ADK**: Has built-in MCP support for Gemini
- **Pydantic AI**: Clean MCP integration with automatic tool conversion
- **LangChain MCP Adapters**: MultiServerMCPClient with automatic schema handling

### 2. **Inconsistent MCP Server Output**
The MCP server is providing inconsistent schema types, forcing manual correction in the client.

### 3. **Lack of Abstraction**
No common interface for LLM providers, leading to provider-specific code scattered throughout.

## Refactoring Strategy

### Phase 1: Architecture Redesign
NOTE: the project uses venv, and uses uv sync and myproject.toml to manage the packages

#### 1.1 **Modular Provider Architecture**
```
nifi_chat_ui/
├── llm/
│   ├── __init__.py
│   ├── base.py              # Abstract base classes and interfaces
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── openai_client.py
│   │   ├── anthropic_client.py
│   │   ├── gemini_client.py
│   │   ├── perplexity_client.py
│   │   └── factory.py       # Provider factory
│   ├── mcp/
│   │   ├── __init__.py
│   │   ├── client.py        # MCP client wrapper
│   │   ├── tool_formatter.py # Tool formatting utilities
│   │   └── schema_validator.py # Schema validation and correction
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── token_counter.py
│   │   ├── error_handler.py
│   │   └── message_converter.py
│   └── config.py            # LLM configuration management
```

#### 1.2 **Standardized Interfaces**
```python
# llm/base.py
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class LLMResponse:
    content: Optional[str]
    tool_calls: Optional[List[Dict[str, Any]]]
    token_count_in: int
    token_count_out: int
    error: Optional[str] = None

class LLMProvider(ABC):
    @abstractmethod
    def send_message(
        self, 
        messages: List[Dict[str, Any]], 
        system_prompt: str, 
        tools: Optional[List[Any]] = None,
        user_request_id: Optional[str] = None,
        action_id: Optional[str] = None
    ) -> LLMResponse:
        pass
    
    @abstractmethod
    def format_tools(self, tools: List[Dict[str, Any]]) -> Any:
        pass
    
    @abstractmethod
    def is_configured(self) -> bool:
        pass
    
    @abstractmethod
    def get_available_models(self) -> List[str]:
        pass
```

### Phase 2: MCP Integration Improvement

#### 2.1 **Use Official MCP Integration Libraries**
Replace manual schema conversion with official solutions:

**For Gemini: Google ADK (Recommended)**
```python
# llm/providers/gemini_client.py
from google.agent_dev_kit import Agent

class GeminiClient(LLMProvider):
    def __init__(self, api_key: str, mcp_server_config: Dict):
        # Use Google ADK for built-in MCP support with Gemini
        self.agent = Agent(
            model="gemini-2.5-flash",
            mcp_servers=[mcp_server_config]  # ADK handles MCP integration automatically
        )
    
    def send_message(self, messages, system_prompt, tools):
        # ADK handles all the complex Protocol Buffer conversion internally
        return self.agent.run(messages, system_prompt, tools)
```

**For Other Providers: Keep Current SDK Approach**
```python
# llm/providers/openai_client.py
from openai import OpenAI

class OpenAIClient(LLMProvider):
    def __init__(self, api_key: str):
        # Use standard OpenAI SDK (current approach works well)
        self.client = OpenAI(api_key=api_key)
    
    def send_message(self, messages, system_prompt, tools):
        # Standard OpenAI SDK approach (current implementation)
        return self._send_with_openai_sdk(messages, system_prompt, tools)
```

**Key Differences:**
- **Google ADK**: Specifically designed for Gemini + MCP, handles Protocol Buffer conversion automatically
- **Standard SDKs**: Current approach for OpenAI, Anthropic, Perplexity (works well, no changes needed)

#### 2.2 **MCP Schema Validation and Correction**
```python
# llm/mcp/schema_validator.py
class MCPSchemaValidator:
    """Validates and corrects MCP server schema inconsistencies"""
    
    @staticmethod
    def validate_and_correct_schema(schema: Dict[str, Any], provider: str) -> Dict[str, Any]:
        """Fix common MCP schema issues before sending to LLM providers"""
        corrected = schema.copy()
        
        # Provider-specific corrections
        if provider.lower() == "gemini":
            # Gemini-specific corrections (Protocol Buffer format)
            corrected = MCPSchemaValidator._correct_for_gemini(corrected)
        else:
            # Standard JSON Schema corrections for OpenAI, Anthropic, Perplexity
            corrected = MCPSchemaValidator._correct_for_json_schema(corrected)
        
        return corrected
    
    @staticmethod
    def _correct_for_gemini(schema: Dict[str, Any]) -> Dict[str, Any]:
        """Apply Gemini-specific schema corrections"""
        # Convert JSON Schema types to Gemini Protocol Buffer types
        type_mapping = {
            "string": "STRING",
            "number": "NUMBER", 
            "integer": "INTEGER",
            "boolean": "BOOLEAN",
            "object": "OBJECT",
            "array": "ARRAY"
        }
        
        # Fix common MCP server inconsistencies for Gemini
        field_corrections = {
            "timeout_seconds": "NUMBER",
            "port": "INTEGER",
            "enabled": "BOOLEAN",
            "properties": "OBJECT",
            "relationships": "ARRAY",
            "object_id": "STRING",
            "process_group_id": "STRING"
        }
        
        return MCPSchemaValidator._apply_corrections(schema, field_corrections, type_mapping)
    
    @staticmethod
    def _correct_for_json_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
        """Apply standard JSON Schema corrections"""
        # Fix common MCP server inconsistencies for JSON Schema providers
        field_corrections = {
            "timeout_seconds": "number",
            "port": "integer",
            "enabled": "boolean",
            "properties": "object",
            "relationships": "array"
        }
        
        return MCPSchemaValidator._apply_corrections(schema, field_corrections, {})
    
    @staticmethod
    def _apply_corrections(schema: Dict[str, Any], field_corrections: Dict[str, str], type_mapping: Dict[str, str]) -> Dict[str, Any]:
        """Apply field-specific corrections to schema"""
        if "properties" in schema:
            for field, expected_type in field_corrections.items():
                if field in schema["properties"]:
                    prop = schema["properties"][field]
                    current_type = prop.get("type", "").lower()
                    expected_type_lower = expected_type.lower()
                    
                    if current_type != expected_type_lower:
                        # Apply type mapping if provided (for Gemini)
                        corrected_type = type_mapping.get(expected_type_lower, expected_type_lower)
                        prop["type"] = corrected_type
                        logger.info(f"Corrected {field} type: {current_type} -> {corrected_type}")
        
        return schema
```

**Usage in the Architecture:**
```python
# llm/mcp/client.py
class MCPClient:
    def __init__(self):
        self.schema_validator = MCPSchemaValidator()
    
    def get_tools_for_provider(self, provider: str) -> List[Dict[str, Any]]:
        """Get MCP tools with provider-specific schema validation"""
        raw_tools = self._get_raw_tools_from_mcp_server()
        
        # Apply provider-specific schema corrections
        corrected_tools = []
        for tool in raw_tools:
            if "function" in tool and "parameters" in tool["function"]:
                corrected_params = self.schema_validator.validate_and_correct_schema(
                    tool["function"]["parameters"], 
                    provider
                )
                tool["function"]["parameters"] = corrected_params
            corrected_tools.append(tool)
        
        return corrected_tools
```

### Phase 3: Simplified Error Handling

#### 3.1 **Unified Error Handler**
```python
# llm/utils/error_handler.py
class LLMErrorHandler:
    """Centralized error handling for all LLM providers"""
    
    @staticmethod
    def handle_error(exception: Exception, provider: str) -> str:
        """Extract user-friendly error messages from LLM API exceptions"""
        error_str = str(exception).lower()
        
        # Common error patterns across providers
        if 'rate limit' in error_str:
            return "Rate limit exceeded. Please try again later."
        elif 'authentication' in error_str or 'api key' in error_str:
            return "Authentication failed. Please check your API key."
        elif 'model not found' in error_str:
            return "Model not found. Please check the model name."
        elif 'quota' in error_str:
            return "Quota exceeded. Please check your account billing."
        else:
            return f"{provider.title()} API error: {str(exception)}"
```

#### 3.2 **Provider-Specific Error Handling**
```python
# llm/providers/gemini_client.py
class GeminiClient(LLMProvider):
    def handle_error(self, exception: Exception) -> str:
        # Use Google ADK's built-in error handling when possible
        if hasattr(exception, 'finish_reason'):
            if 'MALFORMED_FUNCTION_CALL' in str(exception.finish_reason):
                return "Function call schema error. Please check tool parameter schemas."
            elif 'SAFETY' in str(exception.finish_reason):
                return "Safety filter triggered. Please rephrase your request."
        
        return LLMErrorHandler.handle_error(exception, "Gemini")
```

### Phase 4: Configuration-Driven Approach

#### 4.1 **Provider Configuration**
```python
# llm/config.py
PROVIDER_CONFIG = {
    'openai': {
        'client_class': 'OpenAIClient',
        'requires_api_key': True,
        'supports_tools': True,
        'error_handler': 'OpenAIErrorHandler',
    },
    'gemini': {
        'client_class': 'GeminiClient', 
        'requires_api_key': True,
        'supports_tools': True,
        'error_handler': 'GeminiErrorHandler',
        'mcp_integration': 'google_adk',  # Use Google ADK
    },
    'anthropic': {
        'client_class': 'AnthropicClient',
        'requires_api_key': True, 
        'supports_tools': True,
        'error_handler': 'AnthropicErrorHandler',
    },
    'perplexity': {
        'client_class': 'PerplexityClient',
        'requires_api_key': True,
        'supports_tools': True,
        'error_handler': 'OpenAIErrorHandler',  # Compatible format
    }
}
```

#### 4.2 **Factory Pattern**
```python
# llm/providers/factory.py
class LLMProviderFactory:
    @staticmethod
    def create_provider(provider_name: str, config: Dict[str, Any]) -> LLMProvider:
        """Create LLM provider instance based on configuration"""
        provider_config = PROVIDER_CONFIG.get(provider_name.lower())
        if not provider_config:
            raise ValueError(f"Unsupported provider: {provider_name}")
        
        client_class = getattr(importlib.import_module(f"llm.providers.{provider_name.lower()}_client"), 
                              provider_config['client_class'])
        
        return client_class(config)
```

### Phase 5: Simplified Chat Manager

#### 5.1 **Refactored Chat Manager**
```python
# nifi_chat_ui/chat_manager.py (simplified)
from llm.providers.factory import LLMProviderFactory
from llm.mcp.client import MCPClient
from llm.utils.token_counter import TokenCounter

class ChatManager:
    def __init__(self):
        self.providers = {}
        self.mcp_client = MCPClient()
        self.token_counter = TokenCounter()
    
    def get_llm_response(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        provider: str,
        model_name: str,
        user_request_id: Optional[str] = None,
        action_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get response from specified LLM provider"""
        
        # Get or create provider instance
        if provider not in self.providers:
            self.providers[provider] = LLMProviderFactory.create_provider(provider, config)
        
        # Get tools from MCP with provider-specific schema validation
        tools = self.mcp_client.get_tools_for_provider(provider)
        
        # Send message to provider
        response = self.providers[provider].send_message(
            messages, system_prompt, tools, user_request_id, action_id
        )
        
        return response.to_dict()
```

**What Changes for Each Provider:**

1. **OpenAI/Perplexity/Anthropic**: 
   - Keep current SDK approach (no functional changes)
   - Move code to new modular structure
   - Get schema-validated tools from MCP client
   - Use unified error handling

2. **Gemini**: 
   - Replace manual schema conversion with Google ADK
   - Eliminate MapComposite handling code
   - Get schema-validated tools from MCP client
   - Use unified error handling

## Provider-Specific Migration Details

### **What Stays the Same vs What Changes**

#### **OpenAI, Perplexity, Anthropic (Minimal Changes)**
- **Current Approach**: These providers work well with standard JSON Schema from MCP
- **What Stays**: 
  - SDK usage (OpenAI SDK, Anthropic SDK)
  - Message format handling
  - Tool call processing
  - Error handling patterns
- **What Changes**:
  - Move code to modular structure
  - Use unified error handler
  - Get schema-validated tools from MCP client
  - Remove provider-specific error parsing complexity

#### **Gemini (Major Improvement)**
- **Current Problem**: Manual Protocol Buffer conversion, MapComposite handling, malformed function calls
- **What Changes**:
  - Replace manual schema conversion with Google ADK
  - Eliminate MapComposite handling code
  - Remove complex response parsing logic
  - Use ADK's built-in MCP integration
- **What Stays**: 
  - API key configuration
  - Model selection
  - Basic message handling

### **MCP Schema Validation Usage**

The schema validation acts as a **pre-processing layer** that:
1. **Fixes MCP Server Inconsistencies**: Corrects type mismatches (e.g., `timeout_seconds: string` → `timeout_seconds: number`)
2. **Provider-Specific Formatting**: 
   - **Gemini**: Converts JSON Schema types to Protocol Buffer types (`string` → `STRING`)
   - **Others**: Ensures consistent JSON Schema format
3. **Applied Before LLM Call**: Tools are validated/corrected before being sent to any provider

### **Google ADK vs Standard SDKs**

#### **Google ADK (Gemini Only)**
- **Purpose**: Solve Gemini's Protocol Buffer requirements
- **Benefits**: 
  - Built-in MCP support for Gemini
  - Automatic Protocol Buffer conversion
  - Official Google solution
- **When to Use**: Always for Gemini (replaces current manual conversion)

#### **Standard SDKs (Current Approach)**
- **Purpose**: Direct API integration for OpenAI, Anthropic, Perplexity
- **Benefits**:
  - Proven to work well
  - Official SDK support
  - No additional dependencies
- **When to Use**: Keep current approach for OpenAI/Anthropic/Perplexity (no changes needed)

## Implementation Plan

### Phase 1: Foundation (Week 1)
1. Create new directory structure
2. Implement base classes and interfaces
3. Create configuration management
4. Set up factory pattern

### Phase 2: MCP Integration (Week 2)
1. Research and implement Google ADK for Gemini
2. Create MCP client wrapper with schema validation
3. Implement schema validation and correction for all providers
4. Test with existing MCP server

### Phase 3: Provider Migration (Week 3)
1. **OpenAI Client**: Migrate to new architecture, keep current SDK approach (no functional changes)
2. **Perplexity Client**: Migrate to new architecture, keep current SDK approach (OpenAI-compatible)
3. **Anthropic Client**: Migrate to new architecture, keep current SDK approach (no functional changes)
4. **Gemini Client**: Migrate to new architecture with Google ADK integration (major improvement)

### Phase 4: Error Handling & Utils (Week 4)
1. Implement unified error handler
2. Create token counting utilities
3. Create message conversion utilities
4. Add comprehensive logging

### Phase 5: Integration & Testing (Week 5)
1. Update chat_manager.py to use new architecture
2. Update app.py to use new ChatManager
3. Comprehensive testing with all providers
4. Performance testing and optimization



## Migration Strategy

### 1. **Backward Compatibility**
- Keep existing chat_manager.py during migration
- Gradual migration of providers (one at a time)
- Feature flags for new vs old implementation
- **OpenAI/Perplexity/Anthropic**: Minimal functional changes, mostly structural
- **Gemini**: Major improvement but with fallback to current implementation

### 2. **Testing Strategy**
- Unit tests for each provider
- Integration tests with MCP server
- Performance benchmarks
- Error scenario testing
- **Provider-specific testing**: Ensure each provider works with schema validation

### 3. **Rollback Plan**
- Keep old implementation as fallback
- Gradual rollout with monitoring
- Quick rollback capability if issues arise
- **Gemini-specific**: Can rollback to current manual conversion if ADK has issues

### 4. **Provider Migration Order**
1. **OpenAI** (lowest risk - mostly structural changes)
2. **Perplexity** (low risk - OpenAI-compatible)
3. **Anthropic** (medium risk - format conversion but current approach works)
4. **Gemini** (highest risk - new ADK integration, but highest benefit)

## Current Status and Implementation Findings (July 12 2025)

### What We Achieved
After implementing the chat manager refactor and working through protobuf integration challenges, we reached the following state:

#### ✅ **Successful Protobuf Conversion**
- Fixed Gemini's MapComposite object conversion in `message_converter.py`
- Added robust `_convert_protobuf_to_dict()` function that properly handles nested protobuf objects
- Protobuf objects from Gemini are now correctly converted to JSON-serializable dictionaries
- **Result**: The "outgoing" problem is solved - all LLMs can read and understand tool definitions

#### ✅ **Tool Schema Generation Working**
- All LLMs (OpenAI, Gemini, Anthropic, Perplexity) can successfully receive and understand tool schemas
- `create_complete_nifi_flow` tool is available to all providers
- Complex nested tools are properly formatted for each provider
- **Result**: LLMs can see and choose to use complex tools

### Critical Issue Discovered: FastMCP Validation Problem

#### ❌ **Server-Side Validation Failure**
During implementation, we discovered a fundamental issue with FastMCP's handling of complex TypedDict schemas:

**The Problem:**
- When we added explicit types to help LLMs understand tool definitions, FastMCP began treating `NifiObjectDefinition` as requiring **ALL possible fields for ALL object types**
- Instead of conditional validation (processor objects only need processor fields), FastMCP validates as "every object must have every field"
- This affects **ALL LLMs equally** - both OpenAI and Gemini hit the same 110+ validation errors

**Example Validation Error:**
```
processor objects (type: 'processor') incorrectly require:
- service_type (should only be required for controller_service)
- source_name, target_name, relationships (should only be required for connections)
- position_x, position_y, process_group_id (should be optional)
```

**Root Cause:**
FastMCP's automatic TypedDict → Pydantic conversion doesn't support discriminated unions properly. It treats optional fields as "required for all variants" instead of "required based on discriminator field."

### Field Name Mismatch Issue
Additional complication discovered:
- LLMs send connection objects with `source` and `target` fields
- Schema expects `source_name` and `target_name` fields
- This suggests schema definition inconsistencies between what LLMs generate and what the server expects

### Our Implementation Journey

#### **Phase 1: Simple Tools** ✅
- `create_processors`, `create_controller_services`, etc. worked perfectly
- All LLMs happy with simple JSON schemas
- **Downside**: Multiple tool calls needed for complete flows

#### **Phase 2: Complex Tool** ✅ (for OpenAI)
- Created `create_complete_flow` to reduce tool calls
- Worked beautifully for OpenAI
- **Problem**: Gemini hit MapComposite/protobuf issues with nested data

#### **Phase 3: "Fix" for Gemini** ✅❌
- Added explicit types to help Gemini understand tool definitions
- **Success**: All LLMs can now READ the tool definitions properly
- **Unintended consequence**: FastMCP now over-validates incoming tool calls

#### **Phase 4: Current State** ❌
- Tool definitions work perfectly (outgoing)
- Server validation fails for complex tools (incoming)
- ALL LLMs affected, not just Gemini
- Simple tools still work fine

### Available Options Moving Forward

#### **Option 1: Revert to Simple Tools** ⭐ (Immediate Fix)
**Approach**: Go back to the working simple tools approach
- Use `create_processors`, `create_controller_services`, `create_connections` separately
- Accept multiple tool calls for complex flows
- **Pros**: 
  - Immediate solution that works for all LLMs
  - Known to be reliable
  - No regression risk
- **Cons**: 
  - More orchestration needed
  - More tool calls required

#### **Option 2: Fix FastMCP Validation** (Long-term Solution)
**Approach**: Implement proper Pydantic discriminated unions on the server
- Replace TypedDict with proper Pydantic models with discriminators
- Ensure conditional validation works correctly
- **Pros**: 
  - Best of both worlds - complex tools that work
  - Maintains the efficiency gains
- **Cons**: 
  - More complex server-side changes
  - Risk of introducing new issues
  - Requires deep FastMCP understanding

#### **Option 3: Hybrid Approach** (Balanced)
**Approach**: Keep simple tools + add a simpler complex tool
- Maintain reliable simple tools
- Create a less complex version of `create_complete_flow` with simpler validation
- **Pros**: 
  - Flexibility for different use cases
  - Gradual complexity increase
- **Cons**: 
  - Maintenance overhead of multiple approaches

#### **Option 4: Schema Simplification** (Alternative)
**Approach**: Redesign complex tool to avoid validation complexity
- Use simpler, flatter schemas that don't trigger FastMCP validation issues
- Accept some limitations in exchange for reliability
- **Pros**: 
  - Avoids FastMCP complexity entirely
  - Maintains single complex tool
- **Cons**: 
  - May limit functionality
  - Still requires schema redesign work

### Lessons Learned

1. **FastMCP Limitations**: FastMCP has limitations with complex discriminated unions that aren't immediately obvious
2. **Validation vs Schema Generation**: Solutions that work for schema generation (outgoing) may not work for validation (incoming)
3. **Provider Differences**: The fundamental issue is server-side, not provider-specific
4. **Simple vs Complex Trade-offs**: There's a complexity threshold where simple, multiple tools may be more reliable than single complex tools

### Recommended Next Steps

Based on our analysis, the recommended approach is:

1. **Immediate**: Implement Option 1 (revert to simple tools) to restore functionality
2. **Short-term**: Use the working simple tools approach while evaluating options
3. **Long-term**: Investigate Option 2 (fix FastMCP validation) for future enhancement

This provides immediate relief while preserving the option to implement complex tools correctly in the future.



