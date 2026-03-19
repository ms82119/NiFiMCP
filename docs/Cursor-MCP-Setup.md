# Using the NiFi MCP Server in Cursor IDE

The NiFi MCP server can run over **stdio** so Cursor (or other MCP clients) can use the same NiFi tools directly, without the chat UI or REST API.

## Prerequisites

- Project dependencies installed (`uv sync` or `pip install -e .`).
- At least one NiFi server configured in `config.yaml` (same as for the chat UI).
- Cursor IDE with MCP support.

## Cursor configuration

A project-level MCP config is in **`.cursor/mcp.json`**. It starts the NiFi MCP server as a subprocess over stdio.

Default contents:

```json
{
  "mcpServers": {
    "nifi": {
      "command": "uv",
      "args": ["run", "python", "-m", "nifi_mcp_server.stdio_server"],
      "env": {}
    }
  }
}
```

- **Without `uv`**: use `"command": "python"` and `"args": ["-m", "nifi_mcp_server.stdio_server"]` (run from the project root so `config.yaml` is found).
- **Working directory**: Cursor usually runs the server with the workspace folder as the current directory. Open the NiFiMCP project as the workspace so `config.yaml` and `config/` resolve correctly.

## Choosing the NiFi server

The stdio server uses a single NiFi instance for all tool calls:

1. **Environment variable**  
   Set `NIFI_SERVER_ID` in the MCP server config to the `id` of a server in `config.yaml`:

   ```json
   "nifi": {
     "command": "uv",
     "args": ["run", "python", "-m", "nifi_mcp_server.stdio_server"],
     "env": {
       "NIFI_SERVER_ID": "nifi-local-example"
     }
   }
   ```

2. **Default**  
   If `NIFI_SERVER_ID` is not set, the **first** NiFi server in `config.yaml` is used.

## After changing config

- Reload MCP in Cursor: **Settings → Features → MCP → Reload**, or restart Cursor.

## What you can do in Cursor

Once the NiFi MCP server is running in Cursor, you can:

- Ask the AI to list process groups, describe flows, create or modify processors, connections, etc., using the same tools as the chat UI.
- Keep using the REST server and chat UI as before; they are unchanged and can run alongside Cursor.
