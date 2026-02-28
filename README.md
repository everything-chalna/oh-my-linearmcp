# OhMyLinearMCP

Fast, unified MCP server for Linear on macOS:
- local-cache-first reads for speed
- official MCP fallback for unsupported/degraded reads
- official MCP passthrough for writes

## Why I Built This

While using the official Linear MCP with Claude Code, I noticed that **read operations consumed too much context**. Every issue query returned verbose responses with metadata I didn't need, eating into the AI's context window.

The problem:
- Official Linear MCP makes API calls for every read
- Issue descriptions require separate API calls
- Responses include excessive metadata (full user objects, workflow states, etc.)
- Context window fills up quickly when exploring issues
- Slower response times due to network latency

My solution: **Read directly from Linear.app's local cache.**

Linear.app stores issue descriptions in Y.js CRDT format. This package decodes them locally, so you get descriptions without API calls.

Linear.app (Electron) syncs all your data to a local IndexedDB. This MCP server reads from that cache, giving you:

- **Zero API calls** - Instant reads from disk
- **Smaller responses** - Only the fields you need
- **Offline access** - Works without internet
- **Faster iteration** - No rate limits, no latency
- **Issue descriptions** - Extracts text from Y.js encoded content (v0.3.0+)
- **All workspaces** - Reads from all Linear workspaces on your machine (v0.4.0+)

## Requirements

- **macOS only** - Linear.app stores its cache at `~/Library/Application Support/Linear/`
- **Linear.app** installed and opened at least once (to populate the cache)
- **Node.js/npm (`npx`)** for default official MCP bridge (`mcp-remote`)

## Setup

Add only this server. It handles both local-fast reads and official MCP access.

### Claude Code

```bash
claude mcp add oh-my-linearmcp -- uvx oh-my-linearmcp
```

Legacy CLI alias `linear-mcp-fast` remains available for compatibility.
Note: this alias is the executable name only; package resolution should use `oh-my-linearmcp`.

Official MCP bridge defaults to `npx -y mcp-remote https://mcp.linear.app/mcp`,
so it follows the same OAuth/auth cache flow as the official Linear MCP setup.

Optional env vars:
- `LINEAR_OFFICIAL_MCP_TRANSPORT` (`stdio` default, or `http`)
- `LINEAR_OFFICIAL_MCP_COMMAND` (default: `npx`, used when transport=`stdio`)
- `LINEAR_OFFICIAL_MCP_ARGS` (default: `-y mcp-remote https://mcp.linear.app/mcp`, used when transport=`stdio`)
- `LINEAR_OFFICIAL_MCP_ENV` (JSON object, optional extra env for stdio child process)
- `LINEAR_OFFICIAL_MCP_CWD` (optional working directory for stdio child process)
- `LINEAR_OFFICIAL_MCP_URL` (default: `https://mcp.linear.app/mcp`, used for default stdio args and http transport)
- `LINEAR_OFFICIAL_MCP_HEADERS` (JSON object of headers, used when transport=`http`)
- `LINEAR_FAST_COHERENCE_WINDOW_SECONDS` (default: `30`)

If you're developing from a local checkout, use:

```bash
claude mcp add oh-my-linearmcp -- uvx --from /path/to/oh-my-linearmcp oh-my-linearmcp
```

Module execution is also available:

```bash
python -m oh_my_linearmcp
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "oh-my-linearmcp": {
      "command": "uvx",
      "args": ["oh-my-linearmcp"]
    }
  }
}
```

### Cursor

Add OhMyLinearMCP:

```json
{
  "mcpServers": {
    "oh-my-linearmcp": {
      "command": "uvx",
      "args": ["oh-my-linearmcp"]
    }
  }
}
```

### VS Code / Windsurf / Others

```json
{
  "mcpServers": {
    "oh-my-linearmcp": {
      "command": "uvx",
      "args": ["oh-my-linearmcp"]
    }
  }
}
```

## Available Tools

Local read tools (cache-first with automatic official fallback):

| Tool | Description |
|------|-------------|
| `list_issues` | List issues with filters (team, state, assignee, priority) |
| `get_issue` | Get issue details by identifier (e.g., `DEV-123`) |
| `list_teams` | List all teams |
| `get_team` | Get team details |
| `list_projects` | List all projects |
| `get_project` | Get project details |
| `list_users` | List all users |
| `get_user` | Get user details |
| `list_issue_statuses` | List workflow states for a team |
| `get_issue_status` | Get workflow state details by team + id/name |
| `list_comments` | List comments for an issue |
| `list_issue_labels` | List available issue labels |
| `list_initiatives` | List all initiatives |
| `get_initiative` | Get initiative details |
| `list_cycles` | List cycles for a team |
| `list_documents` | List documents (optionally by project) |
| `get_document` | Get document details |
| `list_milestones` | List milestones for a project |
| `get_milestone` | Get milestone details by project + id/name |
| `list_project_updates` | List updates for a project |
| `get_status_updates` | List/get status updates (`type="project"` only) |

Unified/official bridge tools:

| Tool | Description |
|------|-------------|
| `official_call_tool` | Call any official Linear MCP tool by name with args |
| `list_official_tools` | List available official MCP tools |
| `refresh_cache` | Force local cache reload |
| `get_cache_health` | Show local/official health and coherence window state |

### Notes and limitations

- Local `get_status_updates` supports only `type="project"`; unsupported filters/types auto-fallback to official MCP.
- Local reads may be stale relative to recent writes. The server applies a short post-write remote-first coherence window.
- If local cache parsing degrades, reads fallback to official MCP automatically.

## How It Works

```
Linear.app (Electron)
    ↓ syncs data to local cache
IndexedDB (LevelDB)
~/Library/Application Support/Linear/IndexedDB/...
    ↓ local-fast read path (primary)
OhMyLinearMCP
    ↓ unsupported/degraded/write
official Linear MCP
```

### Issue Descriptions

Linear stores issue descriptions in a separate `contentState` field using Y.js CRDT encoding. This package decodes the binary format to extract readable text, so `get_issue` returns the description without an API call.

Note: The extraction is text-based (not full Y.js parsing), so some formatting may be lost. If local extraction is insufficient, the server can fallback to official MCP.

## Troubleshooting

**"Linear database not found"**

Linear.app must be installed and opened at least once:
```bash
ls ~/Library/Application\ Support/Linear/IndexedDB/
```

**Data seems stale**

The local cache updates when Linear.app syncs. Open Linear.app to refresh, or run `refresh_cache`.

## License

MIT
