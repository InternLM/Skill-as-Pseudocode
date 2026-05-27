<!-- refactored skeleton for anthropic_castingwords-automation (2 of 10 units replaced by child invocations (cleanup applied)) -->

# Castingwords Automation via Rube MCP
invoke(composio-automation, {toolkit_name="Castingwords"})

## Prerequisites
invoke(rube-mcp-connection, {toolkit="castingwords"})  (parent-specific: Always call `RUBE_SEARCH_TOOLS` first to get current tool schemas)

## Setup
**Get Rube MCP**: Add `https://rube.app/mcp` as an MCP server in your client configuration. No API keys needed — just add the endpoint and it works.

1. Verify Rube MCP is available by confirming `RUBE_SEARCH_TOOLS` responds
2. Call `RUBE_MANAGE_CONNECTIONS` with toolkit `castingwords`
3. If connection is not ACTIVE, follow the returned auth link to complete setup
4. Confirm connection status shows ACTIVE before running any workflows

## Tool Discovery
Always discover available tools before executing workflows:

```
invoke(rube-search-tools, {queries: [{use_case: "Castingwords operations", known_fields: ""}], session: {generate_id: true}})
```

This returns available tool slugs, input schemas, recommended execution plans, and known pitfalls.

## Core Workflow Pattern

### Step 1: Discover Available Tools
```
invoke(rube-search-tools, {queries: [{use_case: "your specific Castingwords task"}], session: {id: "existing_session_id"}})
```

### Step 2: Check Connection
```
invoke(rube-manage-connections, {toolkits: ["castingwords"], session_id: "your_session_id"})
```

### Step 3: Execute Tools
```
invoke(rube-multi-execute-tool, {tools: [{tool_slug: "TOOL_SLUG_FROM_SEARCH", arguments: {/* schema-compliant args from search results */}}], memory: {}, session_id: "your_session_id"})
```

## Known Pitfalls
- **Always search first**: Tool schemas change. Never hardcode tool slugs or arguments without calling `RUBE_SEARCH_TOOLS`
- **Check connection**: Verify `RUBE_MANAGE_CONNECTIONS` shows ACTIVE status before executing tools
- **Schema compliance**: Use exact field names and types from the search results
- **Memory parameter**: Always include `memory` in `RUBE_MULTI_EXECUTE_TOOL` calls, even if empty (`{}`)
- **Session reuse**: Reuse session IDs within a workflow. Generate new ones for new workflows
- **Pagination**: Check responses for pagination tokens and continue fetching until complete

## Quick Reference
| Operation | Approach |
|-----------|----------|
| Find tools | `invoke(rube-search-tools, {queries: [{use_case: "Castingwords operations", known_fields: ""}]})` |
| Connect | `invoke(rube-manage-connections, {toolkits: ["castingwords"]})` |
| Execute | `invoke(rube-multi-execute-tool, {tools: [{tool_slug: "TOOL_SLUG_FROM_SEARCH"}]})` |
| Bulk ops | `RUBE_REMOTE_WORKBENCH` with `run_composio_tool()` |
| Full schema | `RUBE_GET_TOOL_SCHEMAS` for tools with `schemaRef` |

---
*Powered by [Composio](https://composio.dev)*