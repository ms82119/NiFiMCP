# NiFi MCP server over stdio (any MCP client)

The NiFi MCP server runs over **stdio**. Any host that supports MCP (Cursor, Claude Desktop, VS Code, JetBrains, Windsurf, etc.) can start it as a subprocess and expose the same tools as the built-in chat UI, without running the REST API or browser client.

**Agent / LLM usage:** Which tools to prefer, avoiding parallel heavy reads, and continuation tokens are documented in **[MCP Agent Guide](./MCP-Agent-Guide.md)**. That guide applies in every MCP host.

## Prerequisites

- Dependencies installed from the repo root (`uv sync` or `pip install -e .`).
- At least one NiFi server defined in `config.yaml` (same file as for the chat UI).

## Working directory

The process **must** run with the **repository root** as the current working directory so `config.yaml`, `config/`, and package imports resolve. How you set that depends on the client (workspace folder, `cwd` in config, etc.).

## Command and arguments

**Recommended** (uses the locked environment from `uv.lock`):

```bash
uv run python -m nifi_mcp_server.stdio_server
```

**Without `uv`**, after activating your venv:

```bash
python -m nifi_mcp_server.stdio_server
```

## Example MCP server definition (JSON)

Shape matches common hosts (field names may vary slightly; see your client’s MCP documentation):

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

If you use `python` instead of `uv`:

```json
{
  "mcpServers": {
    "nifi": {
      "command": "python",
      "args": ["-m", "nifi_mcp_server.stdio_server"],
      "env": {}
    }
  }
}
```

Point your client’s “add MCP server” UI at the same `command` / `args`, or merge this block into its config file if it uses JSON.

## Choosing the NiFi server

The stdio server uses **one** NiFi instance for all tool calls.

1. **Environment variable** — set `NIFI_SERVER_ID` to the `id` of a server in `config.yaml`:

   ```json
   "nifi": {
     "command": "uv",
     "args": ["run", "python", "-m", "nifi_mcp_server.stdio_server"],
     "env": {
       "NIFI_SERVER_ID": "nifi-local-example"
     }
   }
   ```

2. **Default** — if `NIFI_SERVER_ID` is unset, the **first** NiFi server in `config.yaml` is used.

## After changing `config.yaml` or MCP config

Restart the MCP server or use your client’s “reload MCP” action so changes are picked up.

## Cursor IDE

If you use Cursor, see **[Cursor MCP Setup](./Cursor-MCP-Setup.md)** for the project-level `.cursor/mcp.json` location and reload steps. The command and environment variables are the same as above.
