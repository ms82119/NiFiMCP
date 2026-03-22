# Using the NiFi MCP server in Cursor IDE

Cursor is one MCP host among many. **stdio command, working directory, and `NIFI_SERVER_ID`** are documented in **[MCP stdio setup](./MCP-stdio-setup.md)**; this page only covers Cursor-specific paths.

## Project MCP config

A project-level MCP definition lives at **`.cursor/mcp.json`**. It starts the NiFi MCP server over stdio when Cursor connects to the project.

Default shape (same as the generic example in [MCP stdio setup](./MCP-stdio-setup.md)):

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

Open the **NiFiMCP** folder as the Cursor workspace so the server runs with the repo root as the current directory and finds `config.yaml`.

## Reloading after changes

After editing `.cursor/mcp.json` or `config.yaml`, reload MCP: **Settings → Features → MCP → Reload**, or restart Cursor.

## What you can do

Once the NiFi MCP server is connected in Cursor, you can ask the agent to list process groups, document flows, create or modify processors and connections, and use the same tools as the chat UI. The REST server and chat UI are unchanged and can run alongside Cursor.

**Orchestration** (tool choice, heavy reads, continuation tokens): **[MCP Agent Guide](./MCP-Agent-Guide.md)**.
