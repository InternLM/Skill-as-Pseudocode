<!-- refactored skeleton for anthropic_anchor-browser-automation (3 of 10 units replaced by child invocations (cleanup applied)) -->

# Anchor Browser Automation via Rube MCP
Automate Anchor Browser operations through Composio's Anchor Browser toolkit via Rube MCP.

**Toolkit docs**: [composio.dev/toolkits/anchor_browser](https://composio.dev/toolkits/anchor_browser)

## Prerequisites
- Rube MCP must be connected (RUBE_SEARCH_TOOLS available)
- Active Anchor Browser connection via `RUBE_MANAGE_CONNECTIONS` with toolkit `anchor_browser`
- Always call `RUBE_SEARCH_TOOLS` first to get current tool schemas

## Setup
**Get Rube MCP**: Add `https://rube.app/mcp` as an MCP server in your client configuration. No API keys needed — just add the endpoint and it works.

1. Verify Rube MCP is available by confirming `RUBE_SEARCH_TOOLS` responds
2. Call `RUBE_MANAGE_CONNECTIONS` with toolkit `anchor_browser`
3. If connection is not ACTIVE, follow the returned auth link to complete setup
4. Confirm connection status shows ACTIVE before running any workflows

## Tool Discovery
invoke(tool-discovery-execution, {use_case="Anchor Browser operations", session_id="generate_id"})

## Core Workflow Pattern

### Step 1: Discover Available Tools
```
RUBE_SEARCH_TOOLS
queries: [{use_case: "your specific Anchor Browser task"}]
session: {id: "existing_session_id"}
```

### Step 2: Check Connection
```
RUBE_MANAGE_CONNECTIONS
toolkits: ["anchor_browser"]
session_id: "your_session_id"
```

### Step 3: Execute Tools
invoke(tool-discovery-execution, {use_case="Execute Tools", session_id="your_session_id"})

## Known Pitfalls
invoke(tool-discovery-execution, {use_case="Tool schemas change. Never hardcode tool slugs or arguments without calling `RUBE_SEARCH_TOOLS`", session_id="Reuse session IDs within a workflow."})

## Quick Reference
| Operation | Approach |
|-----------|----------|
| Find tools | `RUBE_SEARCH_TOOLS` with Anchor Browser-specific use case |
| Connect | `RUBE_MANAGE_CONNECTIONS` with toolkit `anchor_browser` |
| Execute | `RUBE_MULTI_EXECUTE_TOOL` with discovered tool slugs |
| Bulk ops | `RUBE_REMOTE_WORKBENCH` with `run_composio_tool()` |
| Full schema | `RUBE_GET_TOOL_SCHEMAS` for tools with `schemaRef` |

---
*Powered by [Composio](https://composio.dev)*