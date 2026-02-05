# linear-mcp-fast

Fast, read-only MCP server for Linear that reads from Linear.app's local cache on macOS.

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

## Setup

Use `linear-fast` for reads and the official Linear MCP for writes.

### Claude Code

```bash
# Fast reads (this package)
claude mcp add linear-fast -- uvx linear-mcp-fast

# Writes via official Linear MCP
claude mcp add --transport http linear https://mcp.linear.app/mcp
```

Run `/mcp` to authenticate with Linear.

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "linear-fast": {
      "command": "uvx",
      "args": ["linear-mcp-fast"]
    },
    "linear": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://mcp.linear.app/mcp"]
    }
  }
}
```

### Cursor

Install Linear MCP from [Cursor's MCP tools page](https://cursor.com/mcp), then add linear-fast:

```json
{
  "mcpServers": {
    "linear-fast": {
      "command": "uvx",
      "args": ["linear-mcp-fast"]
    }
  }
}
```

### VS Code / Windsurf / Others

```json
{
  "mcpServers": {
    "linear-fast": {
      "command": "uvx",
      "args": ["linear-mcp-fast"]
    },
    "linear": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://mcp.linear.app/mcp"]
    }
  }
}
```

See [Linear MCP docs](https://developers.linear.app/docs/ai/mcp-server) for Zed, Codex, v0, and other clients.

## Available Tools

Tools mirror the official Linear MCP for easy switching:

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
| `list_comments` | List comments for an issue |
| `list_issue_labels` | List available issue labels |
| `list_initiatives` | List all initiatives |
| `get_initiative` | Get initiative details |
| `list_cycles` | List cycles for a team |
| `list_documents` | List documents (optionally by project) |
| `get_document` | Get document details |
| `list_milestones` | List milestones for a project |
| `list_project_updates` | List updates for a project |

For writes (create issue, add comment, update status), use the official Linear MCP.

## How It Works

```
Linear.app (Electron)
    ↓ syncs data to local cache
IndexedDB (LevelDB)
~/Library/Application Support/Linear/IndexedDB/...
    ↓ read by
linear-mcp-fast
    ↓ decodes Y.js CRDT content
Fast, offline access to issues, teams, users, projects
```

### Issue Descriptions

Linear stores issue descriptions in a separate `contentState` field using Y.js CRDT encoding. This package decodes the binary format to extract readable text, so `get_issue` returns the description without an API call.

Note: The extraction is text-based (not full Y.js parsing), so some formatting may be lost. For rich markdown content, use the official Linear MCP.

## Troubleshooting

**"Linear database not found"**

Linear.app must be installed and opened at least once:
```bash
ls ~/Library/Application\ Support/Linear/IndexedDB/
```

**Data seems stale**

The local cache updates when Linear.app syncs. Open Linear.app to refresh.

## License

MIT
