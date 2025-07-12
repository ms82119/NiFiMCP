"""
MCP Client wrapper for LLM integration.

This module provides a wrapper around the existing MCP handler to integrate
with the new LLM architecture.
"""

from typing import List, Dict, Any, Optional
from loguru import logger

# Import the existing MCP handler
try:
    from ...mcp_handler import get_available_tools, execute_mcp_tool
except ImportError:
    # Fallback if run as script or structure changes
    try:
        from mcp_handler import get_available_tools, execute_mcp_tool
    except ImportError:
        logger.error("Could not import mcp_handler. MCP functionality will be limited.")
        get_available_tools = None
        execute_mcp_tool = None

from .schema_validator import MCPSchemaValidator


class MCPClient:
    """Wrapper for MCP server communication with schema validation."""
    
    def __init__(self):
        self.schema_validator = MCPSchemaValidator()
        self.logger = logger.bind(component="MCPClient")
    
    def get_tools_for_provider(self, provider: str, user_request_id: Optional[str] = None, selected_nifi_server_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get MCP tools with provider-specific schema validation.
        
        Args:
            provider: LLM provider name for schema validation
            user_request_id: Optional user request ID for logging
            selected_nifi_server_id: Optional NiFi server ID
            
        Returns:
            List of corrected tool definitions
        """
        if get_available_tools is None:
            self.logger.error("MCP handler not available")
            return []
        
        try:
            # Get raw tools from MCP server
            raw_tools = get_available_tools(
                selected_nifi_server_id=selected_nifi_server_id,
                user_request_id=user_request_id
            )
            
            if not raw_tools:
                self.logger.warning("No tools received from MCP server")
                return []
            
            # Apply provider-specific schema corrections
            corrected_tools = self.schema_validator.validate_tools_list(raw_tools, provider)
            
            self.logger.info(f"Retrieved and validated {len(corrected_tools)} tools for {provider}")
            return corrected_tools
            
        except Exception as e:
            self.logger.error(f"Error getting tools from MCP server: {e}")
            return []
    
    def get_tools(self, user_request_id: Optional[str] = None, selected_nifi_server_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get MCP tools without provider-specific validation (for backward compatibility).
        
        Args:
            user_request_id: Optional user request ID for logging
            selected_nifi_server_id: Optional NiFi server ID
            
        Returns:
            List of tool definitions
        """
        if get_available_tools is None:
            self.logger.error("MCP handler not available")
            return []
        
        try:
            tools = get_available_tools(
                user_request_id=user_request_id,
                selected_nifi_server_id=selected_nifi_server_id
            )
            self.logger.info(f"Retrieved {len(tools)} tools from MCP server")
            return tools
        except Exception as e:
            self.logger.error(f"Error getting tools from MCP server: {e}")
            return []
    
    def execute_tool(self, tool_name: str, arguments: Dict[str, Any], user_request_id: Optional[str] = None) -> Any:
        """
        Execute an MCP tool.
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            user_request_id: Optional user request ID for logging
            
        Returns:
            Tool execution result
        """
        if execute_mcp_tool is None:
            self.logger.error("MCP handler not available")
            return {"error": "MCP handler not available"}
        
        try:
            result = execute_mcp_tool(tool_name, arguments, user_request_id=user_request_id)
            self.logger.info(f"Executed tool '{tool_name}' successfully")
            return result
        except Exception as e:
            self.logger.error(f"Error executing tool '{tool_name}': {e}")
            return {"error": str(e)}
    
    def validate_tool_schema(self, tool: Dict[str, Any], provider: str) -> Dict[str, Any]:
        """
        Validate and correct a single tool's schema.
        
        Args:
            tool: Tool definition from MCP server
            provider: LLM provider name
            
        Returns:
            Corrected tool definition
        """
        return self.schema_validator.validate_tool_schema(tool, provider)
    
    def validate_tools_list(self, tools: List[Dict[str, Any]], provider: str) -> List[Dict[str, Any]]:
        """
        Validate and correct a list of tools.
        
        Args:
            tools: List of tool definitions from MCP server
            provider: LLM provider name
            
        Returns:
            List of corrected tool definitions
        """
        return self.schema_validator.validate_tools_list(tools, provider) 