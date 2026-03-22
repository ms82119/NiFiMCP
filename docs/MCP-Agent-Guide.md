# NiFi MCP — Agent and LLM orchestration

This guide is for **LLM agents** and **humans** using the NiFi MCP server from any MCP client (Cursor, Claude Code, Gemini IDE, etc.). It complements the [Usage Guide](./UsageGuide.md), which targets the **built-in browser chat bot** UI.

Models do not automatically read files in the repository. **Portable behavior** comes from MCP **server instructions** (injected by supporting clients) and **tool descriptions**. This document is the long-form reference you can paste into a session or keep for maintainers.

## Core playbook

### Full-flow documentation

- **Primary tool:** `document_nifi_flow` with `include_child_groups=True` and an appropriate `max_depth` (e.g. 5–10 for deep flows).
- **One heavy read per user goal:** avoid invoking multiple expensive readers in the **same turn** for the same sweep (e.g. do not parallelize `get_flow_outline`, `document_nifi_flow`, and `get_process_group_status` together). NiFi and the HTTP client may return transient errors or partial data under concurrent load.
- **Default path for “document the whole subtree”:** one `document_nifi_flow` call covering the target process group and descendants, rather than many small calls—unless output size or timeouts force chunking.

### Large or deep flows

1. **Optional map first:** `get_flow_outline` *or* `list_nifi_objects` with `object_type="process_groups"` and `search_scope="recursive"` (and `include_boundary_ports=True` if you need port names on shallow nodes) to see structure and counts cheaply.
2. **Then document:** `document_nifi_flow` on the chosen root with `include_child_groups=True`.
3. **If partial:** responses may include `continuation_token` or `completed=false`. **Resume with the same tool** using the token—do **not** spawn parallel “retry” calls for the same operation.

### Operational health vs structure

- **`get_process_group_status`** answers health, queues, bulletins, and validation. It is **heavy** when `include_child_groups=True`.
- Use it in a **separate** step when the user asks for health, queues, or bulletins—not bundled with full documentation unless they asked for both.

### Strategy: vertical before horizontal

When a flow is too large for one response or you are working incrementally:

- Prefer finishing **one subtree** (one child process group and its descendants) before moving to the **next sibling**—clearer context and easier continuation.
- Alternatively, use an **outline** pass (breadth, shallow) then **depth-first documentation** per branch. This is orchestration advice, not a separate API.

## Related documentation

- [Usage Guide](./UsageGuide.md) — built-in chat bot: session objective, tool phases, UI tips.
- [MCP stdio setup](./MCP-stdio-setup.md) — command, working directory, and `NIFI_SERVER_ID` for any MCP client.
- [Cursor MCP Setup](./Cursor-MCP-Setup.md) — Cursor-specific `.cursor/mcp.json` and reload steps (same server as above).

## Setup reminder

Configure NiFi in `config.yaml` and select the server (`set_nifi_server` / `NIFI_SERVER_ID`) as described in the project README and [MCP stdio setup](./MCP-stdio-setup.md).
