# oh-my-linearmcp

Unified MCP server for Linear on macOS.

- Fast reads from Linear local cache
- Automatic fallback to official Linear MCP for unsupported/degraded reads
- Write support via official Linear MCP (through the same auth flow)

## Name Change

This project name is now `oh-my-linearmcp`.

- New package/command: `oh-my-linearmcp`
- Legacy executable alias: `linear-mcp-fast` (compatibility only)

## Read/Write Policy

| Operation | Path |
|---|---|
| Read | local cache first, then official MCP fallback |
| Write | official MCP only |
| Post-write read window | temporary remote-first window (default 30s) |

## Requirements

- macOS (Linear.app local cache path is macOS-specific)
- Linear.app installed and opened at least once
- Node.js/npm (`npx`) for default official bridge (`mcp-remote`)
- Network access for official fallback/write calls

## Setup

Use only this server. It handles both local-fast reads and official MCP bridge.

### Claude Code

```bash
claude mcp add oh-my-linearmcp -- uvx oh-my-linearmcp
```

From local checkout:

```bash
claude mcp add oh-my-linearmcp -- uvx --from /path/to/oh-my-linearmcp oh-my-linearmcp
```

Module run:

```bash
python -m oh_my_linearmcp
```

### Claude Desktop / Cursor / VS Code / Windsurf

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

## Write Support (Official MCP)

Writes are supported by routing to official Linear MCP.

1. Use `list_official_tools` to discover official MCP tool names
2. Use `official_call_tool` to execute those official tools

Example flow:

```text
list_official_tools()
official_call_tool(name="create_issue", args={...})
official_call_tool(name="update_issue", args={...})
```

## Available Tools

### Local Read Tools (cache-first)

- `list_issues`
- `get_issue`
- `list_teams`
- `get_team`
- `list_projects`
- `get_project`
- `list_users`
- `get_user`
- `list_issue_statuses`
- `get_issue_status`
- `list_comments`
- `list_issue_labels`
- `list_initiatives`
- `get_initiative`
- `list_cycles`
- `list_documents`
- `get_document`
- `list_milestones`
- `get_milestone`
- `list_project_updates`
- `get_status_updates` (local supports only `type="project"`)

### Unified/Official Bridge Tools

- `official_call_tool` (call any official MCP tool)
- `list_official_tools`
- `refresh_cache`
- `get_cache_health`

## Environment Variables

- `LINEAR_OFFICIAL_MCP_TRANSPORT` (`stdio` default, or `http`)
- `LINEAR_OFFICIAL_MCP_COMMAND` (default: `npx`, used when transport=`stdio`)
- `LINEAR_OFFICIAL_MCP_ARGS` (default: `-y mcp-remote https://mcp.linear.app/mcp`, used when transport=`stdio`)
- `LINEAR_OFFICIAL_MCP_ENV` (JSON object, optional env for stdio child process)
- `LINEAR_OFFICIAL_MCP_CWD` (optional working directory for stdio child process)
- `LINEAR_OFFICIAL_MCP_URL` (default: `https://mcp.linear.app/mcp`)
- `LINEAR_OFFICIAL_MCP_HEADERS` (JSON headers, used when transport=`http`)
- `LINEAR_FAST_COHERENCE_WINDOW_SECONDS` (default: `30`)

## How It Works

```text
Linear.app (Electron)
  -> local IndexedDB cache
  -> oh-my-linearmcp
     -> read: local-first
     -> unsupported/degraded reads: official MCP fallback
     -> write: official MCP
```

## Notes

- Local reads can be stale; post-write remote-first window reduces mismatch risk.
- If local parsing is degraded, reads fall back to official MCP.
- Local `get_status_updates` supports only `type="project"`; others fall back to official MCP.

## Troubleshooting

**`npx: command not found`**
- Install Node.js/npm, or switch to `LINEAR_OFFICIAL_MCP_TRANSPORT=http`.

**Official calls unauthorized**
- Re-run official Linear MCP auth in your client (existing OAuth process).

**Local data seems stale**
- Open Linear.app to sync, or run `refresh_cache`.

**Linear local DB not found**
```bash
ls ~/Library/Application\ Support/Linear/IndexedDB/
```

## License

MIT
