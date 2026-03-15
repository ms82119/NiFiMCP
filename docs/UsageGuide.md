# NiFi MCP Usage Guide

This guide provides detailed information about using the NiFi MCP system effectively.

## UI Features and Tips

### Session Objective
The top text box "Define the overall objective for this session" is optional. If you add something here, it will be appended to the system prompt for every LLM request. It's useful to include:
- The process group ID that you want the LLM to work within
- Specific objectives to keep front and center for the LLM

### Token Management
The token count after each LLM response is worth keeping an eye on. The history of prior conversations will be included and will always grow in size. You may want to reset the conversation history (button in side panel) from time to time to manage token usage.

### Copying and Exporting
- LLM responses can be copied to the clipboard with the white icons under the responses
- In the side panel, there is a button to copy the complete conversation

### Tool Phase Filter
The Tool Phase filter dropdown allows you to expose different tools to the LLM at different phases of your work:
- **Review**: Only gives read tools (no write operations)
- **Build**: Tools for creating new components
- **Modify**: Tools for updating existing components
- **Operate**: Tools for starting/stopping and managing flow execution

For example, if you just want to document flows, you can set the dropdown to "Review" which only gives it read tools and not write ones.

## Automatic Features

NiFi MCP includes several automatic features that simplify component management:

### 1. Auto-Stop
Automatically stops running processors before deleting them. This prevents errors when trying to delete a running processor.

### 2. Auto-Delete
Automatically deletes connections to/from a processor being deleted. This eliminates the need to manually delete connections before removing a processor.

### 3. Auto-Purge
Automatically purges data from connections before deleting them. This prevents errors when trying to delete connections with data in the queue.

### Configuration
These features can be configured in the `config.yaml` file:

```yaml
features:
  auto_stop_enabled: true    # Controls the Auto-Stop feature
  auto_delete_enabled: true  # Controls the Auto-Delete feature
  auto_purge_enabled: true   # Controls the Auto-Purge feature
```

You can enable or disable these features globally by changing these settings. The server will use these default settings for all operations unless overridden specifically.

## Best Practices

### Flow Development
1. **Start with a clear objective** - Use the session objective to guide the LLM
2. **Use appropriate tool phases** - Switch between Review, Build, Modify, and Operate as needed
3. **Monitor token usage** - Use Auto-prune History when it gets too large.  Use Clear Chat History when you want a new conversation 
4. **Test incrementally** - Build and test your flow in stages
5. **Processor-type defaults** - Some NiFi processors have defaults that may need overriding when creating flows via `create_nifi_processors`. For example, **DuplicateFlowFile** defaults to 100 copies; set the "Number of Copies" property (e.g. to 1 for "original + one duplicate") in the same call or via `update_nifi_processors_properties` to avoid creating many flowfiles per request.
6. **Canvas layout** – To avoid processors and ports stacking on top of each other:
   - **At create time**: Omit `position_x`/`position_y` (and `position`) in `create_nifi_processors` or `create_nifi_ports`; the tools will assign positions automatically in a horizontal row (or vertical column if you set `layout_direction="vertical"`). You can set `layout_origin_x`, `layout_origin_y`, `layout_spacing_x` (default 280), and `layout_spacing_y` (default 120) to control where the row/column starts and spacing. When specifying positions manually, use roughly 280px horizontal and 120px vertical spacing so boxes do not overlap.
   - **After building**: Use `layout_nifi_process_group` with the process group ID to reposition processors, ports, and **child process groups** in that group using a **zig-zag** layout: components alternate between two rows (horizontal) or two columns (vertical), giving more space and less overlap than a single row/column. Order: input ports, then child PGs, then processors, then output ports (each sorted by name). Set `origin_x`, `origin_y`, `spacing_x`, `spacing_y` and `layout_direction` (default `"vertical"`; or `"horizontal"`). Use `include_ports=False` to skip ports and `include_process_groups=False` to skip child PGs. Stop the group before layout (NiFi does not allow moving running components); start it again after.

### Backup and flow export
1. **Save flow as JSON** – Use `save_nifi_flow_export` to download the root process group flow (same as the NiFi UI “Download flow definition” without external services) and save it as a JSON file. Files are written to the directory set in config (`nifi.flow_export_directory`; default `flow_exports` relative to the project root). Filenames use the form `{server_id}_{YYYYMMDD-HHMMSS}.json` (server ID is sanitized for the filesystem).
2. **List saved snapshots** – `get_nifi_session_info` returns `flow_export_directory` (the resolved folder path) and `saved_flow_snapshots` (list of `filename`, `path`, `modified` for JSON files matching the current server). Use this to see existing backups before or after saving.

### Flow documentation and health tools
1. **Flow summary** – Use `document_nifi_flow` with `include_flow_summary=True` (default) to get entry points, controller services, decision branches, flow paths, boundary ports, **cross_pg_connections** (which parent/child PG each port connects to), and **cross_pg_flow_map** (PG-to-PG flow) in one call.
2. **Recursive documentation** – Use `document_nifi_flow` with `include_child_groups=True` and `max_depth` to document a process group and its descendants in one call; the response includes `child_groups` (each with the same structure) so the LLM can walk the tree without N separate calls.
3. **Health verdict** – Use `get_process_group_status` to get a single `health` field (`healthy` / `errors` / `degraded`) and an optional `bulletin_summary`. Use `include_child_groups=True` and `max_depth` to get status for descendant PGs in one call, with `child_groups` and `child_health_summary` (counts of healthy/errors/degraded).
4. **Outline-first exploration** – For large, deep flows, use `get_flow_outline` to get a lightweight process group tree with counts and boundary ports. Alternatively, use `list_nifi_objects` with `object_type="process_groups"`, `search_scope="recursive"`, and `include_boundary_ports=True` to get a hierarchy with input_ports/output_ports (and optionally counts) on each node up to depth 2.
5. **PG-level error analysis** – Use `analyze_nifi_processor_errors` with `process_group_id` to analyze errors across all processors in a group (or use `processor_id` for a single processor).

### Flowfile debugging and tracing
1. **List flowfiles** – Use `list_flowfiles` with `target_id` (connection or processor) and `target_type` (`connection` or `processor`). Results are paged: when `has_more` is true, pass the returned `continuation_token` on the next call (with the same `target_id` and `target_type`) to get the next page. Default `max_results` is 100 per call to limit token usage.
2. **Trace one flowfile** – Use `trace_flowfile` with a FlowFile `flowfile_uuid` to see all provenance steps (processor name, event time, duration, parent/child UUIDs). Response includes a `summary` (total events, time range, total duration, top bottlenecks) and a paged `events` list. Use `continuation_token` to fetch the next page (pass the same `flowfile_uuid` and optional `start_date`/`end_date`). Default `max_events` is 50 per call. Use `bottlenecks_only=True` to return only the slowest steps.
3. **Drill into an event** – Use `get_flowfile_event_details` with an `event_id` from a trace or from `list_flowfiles` (processor) to see attributes and content at that step. Content is capped by `max_content_bytes` (default 4096); attributes are capped (default 30) with `attributes_truncated` and `total_attributes` when truncated. When the event has previous/updated attributes, `attribute_changes` summarizes added, modified, and removed keys so you can see what changed at that processor.

### Automated Handling Of Flow Design Errors
1. **Check processor status** - The LLM will look for INVALID processors and fix configuration issues
2. **Review bulletins** - The LLM will check for error messages in the NiFi UI
3. **Use the debug tools** - Offer these tools to the LLM to help it focus on diagnosing flow issues

### Performance
1. **Manage conversation history** - Large histories can slow down responses
2. **Use specific process group IDs** - This helps the LLM focus on relevant components
3. **Leverage automatic features** - Let the system handle cleanup operations
4. **Use get_flow_outline first for big flows** - One outline call then targeted documentation keeps token usage down

## Troubleshooting

### Common Issues
1. **Processors showing as INVALID** - Check required properties and controller service references
2. **Connection failures** - Ensure source and target processors are valid
3. **Authentication errors** - Verify NiFi credentials in config.yaml
4. **Tool call failures** - Check that the MCP server is running and accessible

### Getting Help
- Review the example conversations for common patterns
- Check the testing guide for working examples
- Ask the LLM to use the "expert help" tool for complex questions
- Monitor server logs for detailed error information
