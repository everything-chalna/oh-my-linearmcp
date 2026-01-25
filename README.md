# linear-mcp-fast

Fast, read-only MCP server for Linear that reads from Linear.app's local cache on macOS.

## Why I Built This

While using the official Linear MCP with Claude Code, I noticed that **read operations consumed too much context**. Every issue query returned verbose responses with metadata I didn't need, eating into the AI's context window.

The problem:
- Official Linear MCP makes API calls for every read
- Responses include excessive metadata (full user objects, workflow states, etc.)
- Context window fills up quickly when exploring issues
- Slower response times due to network latency

My solution: **Read directly from Linear.app's local cache.**

Linear.app (Electron) syncs all your data to a local IndexedDB. This MCP server reads from that cache, giving you:

- **Zero API calls** - Instant reads from disk
- **Smaller responses** - Only the fields you need
- **Offline access** - Works without internet
- **Faster iteration** - No rate limits, no latency

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

For writes (create issue, add comment, update status), use the official Linear MCP.

## How It Works

```
Linear.app (Electron)
    ↓ syncs data to local cache
IndexedDB (LevelDB)
~/Library/Application Support/Linear/IndexedDB/...
    ↓ read by
linear-mcp-fast
    ↓
Fast, offline access to issues, teams, users, projects
```

## Troubleshooting

**"Linear database not found"**

Linear.app must be installed and opened at least once:
```bash
ls ~/Library/Application\ Support/Linear/IndexedDB/
```

**Data seems stale**

The local cache updates when Linear.app syncs. Open Linear.app to refresh.

**Returns 0 issues**

Multiple IndexedDB databases may exist. Version 0.2.2+ automatically finds the correct one.

## License

MIT
