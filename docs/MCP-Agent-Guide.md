# NiFi MCP — Agent and LLM orchestration

This guide is for **LLM agents** and **humans** using the NiFi MCP server from any MCP client (Cursor, Claude Code, Gemini IDE, etc.). It complements the [Usage Guide](./UsageGuide.md), which targets the **built-in browser chat bot** UI.

Models do not automatically read files in the repository. **Portable behavior** comes from MCP **server instructions** (injected by supporting clients) and **tool descriptions**. This document is the long-form reference you can paste into a session or keep for maintainers.

## Core playbook

### Full-flow documentation

- **Primary tool:** `document_nifi_flow` with `include_child_groups=True` and an appropriate `max_depth` (e.g. 5–10 for deep flows).
- **Output profile:** default `output_profile=doc_optimized` truncates long property values (scripts, queries) and drops status noise to save tokens. Use `full` only when debugging a specific processor; use `summary` for topology-only reads.
- **One heavy read per user goal:** avoid invoking multiple expensive readers in the **same turn** for the same sweep (e.g. do not parallelize `get_flow_outline`, `document_nifi_flow`, and `get_process_group_status` together). NiFi and the HTTP client may return transient errors or partial data under concurrent load.
- **Default path for “document the whole subtree”:** one `document_nifi_flow` call covering the target process group and descendants, rather than many small calls—unless output size or timeouts force chunking.

### Large or deep flows

1. **Optional map first:** `get_flow_outline` *or* `list_nifi_objects` with `object_type="process_groups"` and `search_scope="recursive"` (and `include_boundary_ports=True` if you need port names on shallow nodes) to see structure and counts cheaply.
2. **Then document:** `document_nifi_flow` on the chosen root with `include_child_groups=True`.
3. **If partial:** responses may include `continuation_token` or `completed=false`. **Resume with the same tool** using the token—do **not** spawn parallel “retry” calls for the same operation.

### Operational health vs structure

- **`get_process_group_status`** answers health, queues, bulletins, and validation. It is **heavy** when `include_child_groups=True`.
- Use it in a **separate** step when the user asks for health, queues, or bulletins—not bundled with full documentation unless they asked for both.

### Parameter contexts

- **`list_nifi_parameter_contexts`** — id/name inventory (no secret values).
- **`get_nifi_process_group_parameter_context`** — assigned context for a PG; sensitive values redacted.
- **`set_nifi_process_group_parameter_context`** — assign or clear a context on a PG (by id or unique name). Use after creating controller services that reference `#{PARAM}` so parameters resolve.

### Strategy: vertical before horizontal

When a flow is too large for one response or you are working incrementally:

- Prefer finishing **one subtree** (one child process group and its descendants) before moving to the **next sibling**—clearer context and easier continuation.
- Alternatively, use an **outline** pass (breadth, shallow) then **depth-first documentation** per branch. This is orchestration advice, not a separate API.

### Flow-as-code (export / import / diff)

- **Export to a git path:** `export_flow_to_path` with an explicit `path` (and optional `process_group_id`). Prefer this over `save_nifi_flow_export` when the JSON is source of truth in a repo.
- **Apply carefully:** `import_flow_definition` creates a **new** child PG; `replace_process_group_from_file` overwrites an existing PG. Always target a **sandbox** process group first—not production root—unless the user explicitly asks to replace a known PG.
- **Compare offline:** `diff_flow_json` on two files (baseline vs candidate) before replace. Ignore-layout is on by default; use the summary counts before diving into `changes`.
- **Do not** commit secrets from controller-service properties; redact or keep sensitive values out of git.

### Debugging a specific flowfile (provenance)

Use this when the user asks why a particular transaction, case, or task did or did not get the expected NiFi outcome—not for mapping an unknown flow.

1. **Find the flowfile** — `list_flowfiles` on a **processor** (`target_type=processor`) that recently handled similar work (e.g. a Log or terminal processor). Use `continuation_token` to page. Match by `event_time` if the user gives a timestamp.
2. **Inspect one case at a time** — `get_processor_event_diff` with `flowfile_uuid` and `processor_id` (the processor where logic ran, e.g. Match or Prepare). Set `max_attributes=100` and `max_content_bytes=65536` (or higher) so `intakeJson` and large attributes are not truncated.
3. **Read `updated_attributes`** in the response — often contains full `intakeJson` when the attributes list is truncated (`attributes_truncated: true`).
4. **Do not parallelize** provenance queries for the same PG — NiFi may return transient `ReadError`. Retry a single failed call once.
5. **Optional trace** — `trace_flowfile` for end-to-end timing and processor order when the failure point is unclear.

**Limits:** Provenance may not return every attribute; very large JSON may be written to a side file. If MCP returns 401, restart the MCP server or NiFi connection and retry.

**Project example:** TS Whitelisting case debug playbook — `/home/msteer/scripts/adhoc/TSWhitelisting/nifi/README.md` and `.cursor/skills/ts-whitelisting/SKILL.md`.

## Related documentation

- [Usage Guide](./UsageGuide.md) — built-in chat bot: session objective, tool phases, UI tips.
- [MCP stdio setup](./MCP-stdio-setup.md) — command, working directory, and `NIFI_SERVER_ID` for any MCP client.
- [Cursor MCP Setup](./Cursor-MCP-Setup.md) — Cursor-specific `.cursor/mcp.json` and reload steps (same server as above).

## Setup reminder

Configure NiFi in `config.yaml` and select the server (`set_nifi_server` / `NIFI_SERVER_ID`) as described in the project README and [MCP stdio setup](./MCP-stdio-setup.md).
