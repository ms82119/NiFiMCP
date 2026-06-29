# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Model Context Protocol (MCP) server exposing ~30 tools for interacting with Apache NiFi (tested on 1.23/1.28, expected to work on 2.x while the REST API stays consistent). It runs in two modes that share the same tool implementations:

1. **Stdio MCP server** — for IDE/agent clients (Cursor, Claude Code, etc.). Entry: `nifi_mcp_server/stdio_server.py`. No REST server or UI needed.
2. **REST server + browser chat bot** — the FastMCP tools are wrapped in a FastAPI app (`nifi_mcp_server/server.py`) that a separate FastAPI chat UI (`api/main.py`) calls over HTTP.

## Common commands

```bash
uv sync                      # install deps from pyproject.toml + uv.lock (run after pulling, deps change often)

# Stdio MCP server (any MCP client)
uv run python -m nifi_mcp_server.stdio_server        # NIFI_SERVER_ID env selects server; defaults to first in config.yaml

# Built-in chat bot — TWO processes in two terminals:
uvicorn nifi_mcp_server.server:app --reload --port 8000   # 1. MCP/NiFi backend
uvicorn api.main:app --reload --port 3000                 # 2. chat UI, then open http://localhost:3000
# If backend is not on 8000, set MCP_SERVER_PORT to its port for BOTH the UI process and tests.

# Tests
python -m pytest                                  # all
python -m pytest -m unit                          # unit only (no NiFi server needed, ~0.4s)
python -m pytest -m "not slow"                    # skip slow tests
python -m pytest tests/core_operations/test_nifi_processor_operations.py   # single file
python -m pytest tests/path::test_name -v -s      # single test, verbose, with logs
python tests/cleanup_test_pgs.py                  # clean leftover test process groups in NiFi
```

There is no separate lint/format step configured.

## Configuration

- `config.yaml` (gitignored; copy from `config.example.yaml`) holds NiFi server list and LLM API keys. The chat bot needs LLM keys; pure MCP usage does not.
- Each NiFi server entry has an `id` (used everywhere to select a server). Auth options: basic auth (`username`/`password`), OIDC bearer token, or mTLS client cert (`client_cert`/`client_key`, plus `host_header` when reaching NiFi via localhost/port-forward). `tls_verify: false` for self-signed dev certs.
- `config/settings.py` loads YAML, merges defaults, and is the single source for config access (`get_nifi_server_config`, `get_nifi_servers`, workflow/timeout settings). Many settings have env overrides (e.g. `NIFI_SERVER_ID`, `MCP_SERVER_PORT`, `NIFI_AUTO_STOP_VERIFY_SECONDS`).

### Secrets — do not read or commit
`config.yaml`, `nifi_tokens.json`, and `certs/` contain live credentials. Never read these files, print their contents, or commit them. OIDC tokens live in `nifi_tokens.json` (file-based store shared across processes, ~5 min expiry) — see `TOKEN_MANAGEMENT.md` for the token submit/check workflow via `test_doc_workflow_auto.py`.

## Architecture

### Tool layer (`nifi_mcp_server/`)
- `core.py` — creates the shared `FastMCP` instance (`mcp`), the NiFi client factory (`get_nifi_client` authenticates eagerly; `create_nifi_client` defers auth — used by stdio so startup never blocks on NiFi), and the `handle_nifi_errors` decorator that implements **Auto-Stop remediation** (on a "component is running" error it stops the parent process group and retries once).
- `nifi_client.py` (~2900 lines) — `NiFiClient`, the async httpx wrapper over the NiFi REST API. All HTTP, auth (token/basic/mTLS), and entity CRUD live here.
- `api_tools/` — the actual MCP tools, grouped by operational phase: `review.py` (list/get/document — read-only), `creation.py`, `modification.py`, `operation.py` (start/stop/purge), `helpers.py`, and `control.py` (MCP-only session/server/phase tools, hidden from the web UI). `utils.py` holds shared formatting/filtering helpers and the phase machinery.
- `server.py` and `stdio_server.py` are thin entrypoints. **Both import every `api_tools` module for its registration side effects** — adding a tool module means adding the import to both files.

### Tool phases (important)
Tools are tagged with `@tool_phases([...])` listing which of `Review | Build | Modify | Operate` they belong to. The current phase is set per-session via the `set_nifi_phase` control tool; when a phase is active, only tools tagged for it are runnable. `CONTROL_TOOL_NAMES` (`get_nifi_session_info`, `set_nifi_server`, `set_nifi_phase`) are always allowed. This narrows the tool surface the LLM sees so it doesn't, e.g., delete things during a review. The decorator also accumulates per-session token counts.

### Request context (`request_context.py`)
Per-request state (selected NiFi client, server id, logger, user/action ids, session token counters) flows through `contextvars` rather than function args. Tools read the active client via `current_nifi_client.get()`. This is why tools don't take a client parameter.

### Chat UI (`api/` + `nifi_chat_ui/`)
- `api/main.py` — FastAPI app serving the static frontend (`static/`) and WebSocket (`/ws`) for live workflow updates; routers in `api/chat_api.py`, `api/settings_api.py`, `api/diagram_api.py`.
- `nifi_chat_ui/llm/` — modular multi-provider LLM layer. `providers/` (anthropic, openai, gemini, perplexity) behind `factory.py`; `chat_manager.py` orchestrates LLM calls + tool execution; `mcp/client.py` is the HTTP client that calls the REST tool endpoints. `mcp_handler.py` (older interface) targets the backend via `MCP_SERVER_URL`/`MCP_SERVER_PORT`.

### Workflows (`nifi_mcp_server/workflows/`)
A guided/unguided workflow engine (built on `pocketflow`) for multi-step LLM-driven tasks like flow documentation. `registry.py` registers `WorkflowDefinition`s; `nodes/` are workflow steps, `prompts/` the prompt sequences, `definitions/` the concrete workflows. Enabled workflows are listed under `workflows.enabled_workflows` in config. The flagship is `flow_documentation`; see `flow_documenter.py` / `flow_documenter_improved.py` and `docs/flow-doc-workflow/`.

## Conventions

- Async throughout — tools and the NiFi client are `async`; the stdio server runs via `anyio.run`.
- Logging is `loguru`, configured by `config/logging_setup.py` (writes to `logs/`). Prefer the context-bound logger (`current_request_logger.get()`) inside tools.
- MCP server-instruction text (orchestration guidance injected into clients) lives in `nifi_mcp_server/mcp_instructions.txt`. Longer-form agent guidance is `docs/MCP-Agent-Guide.md`.
- Test placement: pure-logic tests (mocked, no server) go in `tests/unit/` and are marked `unit`; tests hitting a real NiFi go in `tests/core_operations/` etc. and are marked `integration`. Integration tests use fixtures (`test_pg`, ...) that auto-clean the NiFi objects they create — always use them rather than creating PGs by hand. See `tests/README.md`.
