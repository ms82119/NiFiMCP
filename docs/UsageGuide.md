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
1. **Flow summary** – Use `document_nifi_flow` with `include_flow_summary=True` (default) to get entry points, controller services, decision branches, flow paths, and boundary ports in one call. This gives the LLM enough structure to describe the flow or suggest changes.
2. **Health verdict** – Use `get_process_group_status` to get a single `health` field (`healthy` / `errors` / `degraded`) and an optional `bulletin_summary` (counts by level, last errors) so the agent can decide whether to change the flow or investigate first.
3. **Outline-first exploration** – For large, deep flows, use `get_flow_outline` to get a lightweight process group tree with counts and boundary ports. Then call `document_nifi_flow` or `get_process_group_status` on the PGs you care about.
4. **PG-level error analysis** – Use `analyze_nifi_processor_errors` with `process_group_id` to analyze errors across all processors in a group (or use `processor_id` for a single processor).

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
