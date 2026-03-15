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
