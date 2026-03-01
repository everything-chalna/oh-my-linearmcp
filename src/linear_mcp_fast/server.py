"""
Unified MCP server for Linear local-fast reads + official MCP fallback/writes.
"""

from __future__ import annotations

import atexit
import logging
import signal
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .official_session import OfficialMcpSessionManager
from .reader import LinearLocalReader
from .router import ToolRouter

logger = logging.getLogger(__name__)

_RECONNECT_FLAG = Path(tempfile.gettempdir()) / "oh-my-linear-reconnect"


def _handle_sigterm(signum: int, frame: Any) -> None:
    """On SIGTERM (reconnect): clear tokens + write flag for eager reauth on next start."""
    if _official is not None:
        try:
            _official._clear_token_cache()
        except Exception:
            pass
    try:
        _RECONNECT_FLAG.touch()
    except Exception:
        pass
    _shutdown()
    raise SystemExit(0)


signal.signal(signal.SIGTERM, _handle_sigterm)


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Load local cache; connect official MCP based on reconnect flag / token state."""
    try:
        get_reader().refresh_cache(force=True)
    except Exception as exc:
        logger.warning("Cache init failed, starting degraded: %s", exc)

    reconnecting = _RECONNECT_FLAG.exists()
    if reconnecting:
        _RECONNECT_FLAG.unlink(missing_ok=True)

    if reconnecting or get_official()._has_cached_tokens():
        try:
            get_official()._ensure_connected()
        except Exception as exc:
            logger.warning("Official MCP connection failed: %s", exc)
    try:
        yield
    finally:
        _shutdown()


mcp = FastMCP(
    "OhMyLinear (Fast + Official)",
    instructions=(
        "OhMyLinear unified server. "
        "Read operations are served from local Linear.app cache first for speed, "
        "and automatically fall back to official Linear MCP when local cache is "
        "unsupported, degraded, or stale-sensitive. "
        "Write operations use official Linear MCP."
    ),
    lifespan=_lifespan,
)

_reader: LinearLocalReader | None = None
_official: OfficialMcpSessionManager | None = None
_router: ToolRouter | None = None


def get_reader() -> LinearLocalReader:
    global _reader
    if _reader is None:
        _reader = LinearLocalReader()
    return _reader


def get_official() -> OfficialMcpSessionManager:
    global _official
    if _official is None:
        _official = OfficialMcpSessionManager()
    return _official


def get_router() -> ToolRouter:
    global _router
    if _router is None:
        _router = ToolRouter(get_reader(), get_official())
    return _router


def _shutdown() -> None:
    if _official is not None:
        _official.close()


atexit.register(_shutdown)


def _read(tool_name: str, **kwargs: Any) -> Any:
    return get_router().call_read(tool_name, kwargs)


@mcp.tool()
def list_issues(
    assignee: str | None = None,
    team: str | None = None,
    state: str | None = None,
    priority: int | None = None,
    project: str | None = None,
    query: str | None = None,
    orderBy: str = "updatedAt",
    limit: int = 50,
) -> dict[str, Any]:
    """List issues with optional filtering and sorting.

    Args:
        assignee: Filter by user name or email.
        team: Filter by team key or name.
        state: Filter by state name or type (e.g., "Todo", "In Progress", "Done"). Case-insensitive.
        priority: Filter by exact numeric priority level.
        project: Filter by project name or ID.
        query: Case-insensitive substring search in issue titles.
        orderBy: Sort field - "updatedAt" (default) or "createdAt". Descending order.
        limit: Max issues to return (default 50). 0 or negative returns all.

    Returns:
        dict with "issues" (list of {identifier, title, priority, state, stateType,
        assignee, dueDate}) and "totalCount".
    """
    return _read(
        "list_issues",
        assignee=assignee,
        team=team,
        state=state,
        priority=priority,
        project=project,
        query=query,
        orderBy=orderBy,
        limit=limit,
    )


@mcp.tool()
def get_issue(id: str) -> dict[str, Any] | None:
    """Retrieve full details of a specific issue by identifier.

    Args:
        id: Issue identifier (e.g., "ENG-123").

    Returns:
        dict with {identifier, title, description, priority, estimate, state,
        stateType, assignee, project, dueDate, createdAt, updatedAt, comments
        (list of {author, body, createdAt}), url} or None if not found.
    """
    return _read("get_issue", id=id)


@mcp.tool()
def list_teams() -> list[dict[str, Any]]:
    """Retrieve all teams from the workspace.

    Returns:
        List of team dicts sorted by key, each with {key, name, issueCount}.
    """
    return _read("list_teams")


@mcp.tool()
def list_projects(team: str | None = None) -> list[dict[str, Any]]:
    """Retrieve projects, optionally filtered by team.

    Args:
        team: Team name or key to filter by. Returns empty list if not found.

    Returns:
        List of project dicts sorted by name, each with {name, state, issueCount,
        startDate, targetDate}.
    """
    return _read("list_projects", team=team)


@mcp.tool()
def get_team(query: str) -> dict[str, Any] | None:
    """Retrieve a team by name or key.

    Args:
        query: Team key (exact, case-sensitive uppercase) or name (substring, case-insensitive).

    Returns:
        dict with {id, key, name, description, issueCount, issuesByState} or None.
    """
    return _read("get_team", query=query)


@mcp.tool()
def get_project(query: str) -> dict[str, Any] | None:
    """Retrieve a project by name or slug ID.

    Args:
        query: Project name (substring, case-insensitive) or slug ID (exact, case-insensitive).

    Returns:
        dict with {id, name, description, state, startDate, targetDate, issueCount,
        issuesByState} or None.
    """
    return _read("get_project", query=query)


@mcp.tool()
def list_users() -> list[dict[str, Any]]:
    """List all workspace users with assigned issue counts.

    Returns:
        List of user dicts, each with {id, name, email, displayName, assignedIssueCount}.
    """
    return _read("list_users")


@mcp.tool()
def get_user(query: str) -> dict[str, Any] | None:
    """Retrieve a user by name or email.

    Args:
        query: User name or email to search for.

    Returns:
        dict with {id, name, email, displayName, assignedIssueCount, issuesByState} or None.
    """
    return _read("get_user", query=query)


@mcp.tool()
def list_issue_statuses(team: str) -> list[dict[str, Any]]:
    """List all issue statuses (workflow states) for a team.

    Args:
        team: Team key, name, or ID.

    Returns:
        List of status dicts, each with {id, name, type, color, position}.
    """
    return _read("list_issue_statuses", team=team)


@mcp.tool()
def get_issue_status(
    team: str,
    name: str | None = None,
    id: str | None = None,
) -> dict[str, Any] | None:
    """Get a single issue status by name or ID within a team.

    Args:
        team: Team key, name, or ID.
        name: Status name to search for. Optional if id is provided.
        id: Status ID to look up. Optional if name is provided.

    Returns:
        dict with {id, name, type, color, position, team} or None.
    """
    return _read("get_issue_status", team=team, name=name, id=id)


@mcp.tool()
def list_comments(issueId: str) -> list[dict[str, Any]]:
    """List all comments for a specific issue.

    Args:
        issueId: Issue identifier (e.g., "ENG-123").

    Returns:
        List of comment dicts, each with {id, author, body, createdAt, updatedAt}.
    """
    return _read("list_comments", issueId=issueId)


@mcp.tool()
def list_issue_labels(team: str | None = None) -> list[dict[str, Any]]:
    """List all issue labels, optionally filtered by team.

    Args:
        team: Team key or name to filter by. Returns all labels if None.

    Returns:
        List of label dicts sorted by name, each with {id, name, color, isGroup}.
    """
    return _read("list_issue_labels", team=team)


@mcp.tool()
def list_initiatives() -> list[dict[str, Any]]:
    """Retrieve all initiatives sorted alphabetically by name.

    Returns:
        List of initiative dicts, each with {id, name, slugId, color, status, owner}.
    """
    return _read("list_initiatives")


@mcp.tool()
def get_initiative(query: str) -> dict[str, Any] | None:
    """Retrieve a single initiative by name or identifier.

    Args:
        query: Initiative name or identifier to search for.

    Returns:
        dict with {id, name, slugId, color, status, owner, teamIds, createdAt,
        updatedAt} or None.
    """
    return _read("get_initiative", query=query)


@mcp.tool()
def list_cycles(teamId: str) -> list[dict[str, Any]]:
    """Retrieve cycles for a team.

    Args:
        teamId: Team ID or name.

    Returns:
        List of cycle dicts, each with {id, number, startsAt, endsAt, completedAt,
        progress ({completed, started, unstarted, total} or None)}.
    """
    return _read("list_cycles", teamId=teamId)


@mcp.tool()
def list_documents(project: str | None = None) -> list[dict[str, Any]]:
    """Retrieve documents, optionally filtered by project.

    Args:
        project: Project ID or name to filter by. Returns all documents if None.

    Returns:
        List of document dicts sorted by updatedAt desc, each with {id, title,
        slugId, project, createdAt, updatedAt}.
    """
    return _read("list_documents", project=project)


@mcp.tool()
def get_document(id: str) -> dict[str, Any] | None:
    """Retrieve a document by ID.

    Args:
        id: Document identifier.

    Returns:
        dict with {id, title, slugId, project, creator, createdAt, updatedAt, url} or None.
    """
    return _read("get_document", id=id)


@mcp.tool()
def list_milestones(project: str) -> list[dict[str, Any]]:
    """List all milestones for a project.

    Args:
        project: Project name or identifier.

    Returns:
        List of milestone dicts, each with {id, name, targetDate, progress
        ({completed, started, unstarted, total})}. Empty list if project not found.
    """
    return _read("list_milestones", project=project)


@mcp.tool()
def get_milestone(project: str, query: str) -> dict[str, Any] | None:
    """Retrieve a specific milestone in a project by name or ID.

    Args:
        project: Project name or identifier.
        query: Milestone name or query string to match.

    Returns:
        dict with {id, name, project, targetDate, sortOrder, progress
        ({completed, started, unstarted, total})} or None.
    """
    return _read("get_milestone", project=project, query=query)


@mcp.tool()
def get_status_updates(
    type: str,
    id: str | None = None,
    project: str | None = None,
    initiative: str | None = None,
    user: str | None = None,
    includeArchived: bool | None = None,
    orderBy: str = "createdAt",
    limit: int = 50,
    cursor: str | None = None,
    createdAt: str | None = None,
    updatedAt: str | None = None,
) -> dict[str, Any] | None:
    """Retrieve status updates with filtering. Local cache supports type='project' only.

    Args:
        type: Update type. Local cache only supports "project"; others fall back to official MCP.
        id: Specific status update ID.
        project: Project name or identifier to filter by.
        initiative: Initiative filter (not supported locally, triggers fallback).
        user: User name or identifier to filter by.
        includeArchived: Include archived (not supported locally).
        orderBy: Sort by "createdAt" (default) or "updatedAt". Descending.
        limit: Max results (default 50). 0 returns all.
        cursor: Pagination cursor (not supported locally).
        createdAt: Filter by creation date (not supported locally).
        updatedAt: Filter by update date (not supported locally).

    Returns:
        dict with "statusUpdates" (list of {id, body, health, author, project,
        createdAt, updatedAt}) and "totalCount", or None.
    """
    return _read(
        "get_status_updates",
        type=type,
        id=id,
        project=project,
        initiative=initiative,
        user=user,
        includeArchived=includeArchived,
        orderBy=orderBy,
        limit=limit,
        cursor=cursor,
        createdAt=createdAt,
        updatedAt=updatedAt,
    )


@mcp.tool()
def list_project_updates(project: str) -> list[dict[str, Any]]:
    """List all status updates for a project.

    Args:
        project: Project name or identifier.

    Returns:
        List of update dicts, each with {id, body, health, author, project,
        createdAt, updatedAt}. Empty list if project not found.
    """
    return _read("list_project_updates", project=project)


@mcp.tool()
def official_call_tool(name: str, args: dict[str, Any] | None = None) -> Any:
    """
    Call any official Linear MCP tool by name.

    Use this for write operations and any official-only tools.
    """
    return get_router().call_official(name, args or {})


@mcp.tool()
def list_official_tools() -> list[str]:
    """List tool names currently available from official Linear MCP."""
    return get_official().list_tools()


@mcp.tool()
def reauth_official() -> dict[str, Any]:
    """Force re-authentication of the official Linear MCP OAuth token.

    Clears cached OAuth tokens and disconnects the current session.
    The next tool call will trigger a fresh OAuth login flow via mcp-remote.
    Use this when:
    - OAuth token has expired or become invalid
    - You need to change the authenticated Linear account
    - Some teams/projects are not accessible (scope issues)

    Returns:
        dict with status, message, urlHash, deletedFiles, and searchedDirs.
    """
    return get_router().reauth_official()


@mcp.tool()
def reauth_notion() -> dict[str, Any]:
    """Clear Notion MCP OAuth token cache for re-authentication.

    Removes cached OAuth tokens for the official Notion MCP server.
    The next Notion MCP call will trigger a fresh OAuth login flow.
    Override URL via NOTION_OFFICIAL_MCP_URL env var.
    """
    return get_router().reauth_notion()


@mcp.tool()
def reauth_all() -> dict[str, Any]:
    """Clear OAuth tokens for both Linear and Notion MCP servers.

    Combines reauth_official (Linear) and reauth_notion into a single call.
    """
    return get_router().reauth_all()


@mcp.tool()
def refresh_cache() -> dict[str, Any]:
    """Force reload of local cache and return health state."""
    return get_router().refresh_local_cache()


@mcp.tool()
def get_cache_health() -> dict[str, Any]:
    """Return local+official health and coherence-window state."""
    return get_router().get_health()


def main() -> None:
    mcp.run()
