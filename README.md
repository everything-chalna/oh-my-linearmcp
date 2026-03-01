# oh-my-linear

Ask your AI agent to list Linear issues. Watch 37,000 tokens vanish into a single response full of avatar URLs, custom field schemas, and nested workspace metadata nobody asked for. Congratulations -- your context window is now half gone before the real work begins.

This is what happens when every `list_issues` call goes through the official Linear MCP. The responses are accurate. They're also enormous.

oh-my-linear reads directly from Linear.app's local IndexedDB cache instead. No API call. No network round-trip. Just the fields you actually care about.

```
"Give me the issues for team Frontend"

Official MCP:  ~37,500 tokens (nested GraphQL, full user objects, attachment metadata, ...)
oh-my-linear: ~2,500 tokens (identifier, title, priority, state, assignee, dueDate)
```

Same question. 15x less context.

## How It Works

Linear.app is an Electron app. Every time you open it, it syncs your workspace into a local IndexedDB (LevelDB on disk). oh-my-linear cracks open that database, parses the binary Chromium storage format, and serves the data as MCP tool responses.

```
Linear.app (Electron)
  -> syncs workspace to local IndexedDB
  -> oh-my-linear reads it directly
     -> reads:  local cache (no API call, <10ms)
     -> writes: proxied to official Linear MCP
     -> after a write: 30s remote-first window to avoid stale reads
```

Writes still go through the official MCP -- this isn't trying to replace it. It's a read accelerator that sits in front of it.

### The Store Detection Trick

Linear uses hash-based IndexedDB store names that change between app versions. Something like `fbaa32a232c2_issues` today might be `a1b2c3d4e5f6_issues` tomorrow. Instead of hardcoding these, oh-my-linear samples the first record from each store and matches the shape -- "has `teamId`, `stateId`, `title`? That's the issues store." Survives Linear updates without code changes.

## Setup

One server handles both reads and writes. Replace your existing Linear MCP with this.

### Claude Code

```bash
claude mcp add oh-my-linear -- uvx oh-my-linear
```

From local checkout:

```bash
claude mcp add oh-my-linear -- uvx --from /path/to/oh-my-linear oh-my-linear
```

### Claude Desktop / Cursor / VS Code / Windsurf

```json
{
  "mcpServers": {
    "oh-my-linear": {
      "command": "uvx",
      "args": ["oh-my-linear"]
    }
  }
}
```

## Requirements

- macOS (Linear.app cache path is macOS-specific)
- Linear.app installed and opened at least once
- Node.js/npm (`npx`) for official MCP bridge (`mcp-remote`)
- Network access for writes and fallback reads

## Read/Write Policy

| Operation | What happens |
|---|---|
| Read | Local cache first. If local data is missing or degraded, falls back to official MCP. |
| Write | Always goes to official MCP. |
| Read after write | 30-second remote-first window to avoid reading your own stale data. |

## Available Tools

### Local Read Tools (cache-first, fast)

`list_issues` / `get_issue` / `list_teams` / `get_team` / `list_projects` / `get_project` / `list_users` / `get_user` / `list_issue_statuses` / `get_issue_status` / `list_comments` / `list_issue_labels` / `list_initiatives` / `get_initiative` / `list_cycles` / `list_documents` / `get_document` / `list_milestones` / `get_milestone` / `list_project_updates` / `get_status_updates`

### Bridge Tools

- `official_call_tool` -- call any official Linear MCP tool (writes, unsupported reads, etc.)
- `list_official_tools` -- discover what the official MCP exposes
- `refresh_cache` -- force reload from local IndexedDB
- `get_cache_health` -- check local + official backend status
- `reauth_official` -- clear Linear OAuth tokens and force re-login
- `reauth_notion` -- clear Notion OAuth tokens and force re-login
- `reauth_all` -- clear both Linear and Notion OAuth tokens at once

### Write Example

```text
list_official_tools()
official_call_tool(name="create_issue", args={...})
official_call_tool(name="update_issue", args={...})
```

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `LINEAR_OFFICIAL_MCP_TRANSPORT` | `stdio` | `stdio` or `http` |
| `LINEAR_OFFICIAL_MCP_COMMAND` | `npx` | Command for stdio transport |
| `LINEAR_OFFICIAL_MCP_ARGS` | `-y mcp-remote https://mcp.linear.app/mcp` | Args for stdio transport |
| `LINEAR_OFFICIAL_MCP_ENV` | | JSON object, env for stdio child process |
| `LINEAR_OFFICIAL_MCP_CWD` | | Working directory for stdio child process |
| `LINEAR_OFFICIAL_MCP_URL` | `https://mcp.linear.app/mcp` | URL for http transport |
| `LINEAR_OFFICIAL_MCP_HEADERS` | | JSON headers for http transport |
| `LINEAR_FAST_COHERENCE_WINDOW_SECONDS` | `30` | Remote-first window after writes |
| `LINEAR_FAST_IDLE_REFRESH_SECONDS` | `60` | Idle gap (seconds) before auto-refreshing cache on next tool call |
| `NOTION_OFFICIAL_MCP_URL` | `https://mcp.notion.com/mcp` | Notion MCP server URL for reauth |
| `LINEAR_FAST_ACCOUNT_EMAILS` | | Comma-separated; filter cache to these orgs |
| `LINEAR_FAST_USER_ACCOUNT_IDS` | | Comma-separated; direct account-id scope |

## Auto-Refresh on Reconnect

When your MCP client reconnects after being idle (e.g., after sleep or switching apps), oh-my-linear detects the gap and automatically refreshes the local cache. If no tool call has been made for 60 seconds (configurable via `LINEAR_FAST_IDLE_REFRESH_SECONDS`), the next call triggers a cache reload so you always get fresh data without manually calling `refresh_cache`.

## Troubleshooting

`npx: command not found` -- Install Node.js, or set `LINEAR_OFFICIAL_MCP_TRANSPORT=http`.

`Official calls unauthorized` -- Re-run official Linear MCP OAuth in your client.

`Notion OAuth expired` -- Call `reauth_notion` to clear cached tokens. The next Notion MCP call will trigger a fresh login.

`Local data seems stale` -- Open Linear.app to trigger a sync, or call `refresh_cache`.

`Linear local DB not found` -- Check that it exists:

```bash
ls ~/Library/Application\ Support/Linear/IndexedDB/
```

## License

MIT
