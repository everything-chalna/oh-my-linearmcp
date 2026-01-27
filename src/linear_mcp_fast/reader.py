"""
Linear Local Data Reader with TTL-based caching.

Reads Linear's local IndexedDB cache to provide fast access to issues, users,
teams, workflow states, and comments without API calls.
"""

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ccl_chromium_reader import ccl_chromium_indexeddb  # type: ignore

from .store_detector import DetectedStores, detect_stores

LINEAR_DB_PATH = os.path.expanduser(
    "~/Library/Application Support/Linear/IndexedDB/https_linear.app_0.indexeddb.leveldb"
)
LINEAR_BLOB_PATH = os.path.expanduser(
    "~/Library/Application Support/Linear/IndexedDB/https_linear.app_0.indexeddb.blob"
)

CACHE_TTL_SECONDS = 300  # 5 minutes


@dataclass
class CachedData:
    """Container for cached Linear data."""

    teams: dict[str, dict[str, Any]] = field(default_factory=dict)
    users: dict[str, dict[str, Any]] = field(default_factory=dict)
    states: dict[str, dict[str, Any]] = field(default_factory=dict)
    issues: dict[str, dict[str, Any]] = field(default_factory=dict)
    comments: dict[str, dict[str, Any]] = field(default_factory=dict)
    comments_by_issue: dict[str, list[str]] = field(default_factory=dict)
    projects: dict[str, dict[str, Any]] = field(default_factory=dict)
    issue_content: dict[str, str] = field(default_factory=dict)  # issueId -> description
    loaded_at: float = 0.0

    def is_expired(self) -> bool:
        """Check if the cache has expired."""
        return time.time() - self.loaded_at > CACHE_TTL_SECONDS


class LinearLocalReader:
    """
    Reader for Linear's local IndexedDB cache.

    Provides fast, local-only access to Linear data without API calls.
    Data is cached in memory with a 5-minute TTL.
    """

    def __init__(
        self, db_path: str = LINEAR_DB_PATH, blob_path: str = LINEAR_BLOB_PATH
    ):
        self._db_path = db_path
        self._blob_path = blob_path
        self._cache = CachedData()
        self._stores: DetectedStores | None = None

    def _check_db_exists(self) -> None:
        """Verify the Linear database exists."""
        if not os.path.exists(self._db_path):
            raise FileNotFoundError(
                f"Linear database not found at {self._db_path}. "
                "Please ensure Linear.app is installed and has been opened at least once."
            )

    def _get_wrapper(self) -> ccl_chromium_indexeddb.WrappedIndexDB:
        """Get an IndexedDB wrapper instance."""
        self._check_db_exists()
        return ccl_chromium_indexeddb.WrappedIndexDB(self._db_path, self._blob_path)

    def _find_linear_db(
        self, wrapper: ccl_chromium_indexeddb.WrappedIndexDB
    ) -> ccl_chromium_indexeddb.WrappedDatabase:
        """Find the main Linear database with data."""
        for db_id in wrapper.database_ids:
            if "linear_" in db_id.name and db_id.name != "linear_databases":
                db = wrapper[db_id.name, db_id.origin]
                # Skip empty databases
                if list(db.object_store_names):
                    return db
        raise ValueError("Could not find Linear database in IndexedDB")

    def _to_str(self, val: Any) -> str:
        """Convert value to string, handling bytes."""
        if val is None:
            return ""
        if isinstance(val, bytes):
            return val.decode("utf-8", errors="replace")
        return str(val)

    def _extract_yjs_text(self, content_state: str | None) -> str:
        """Extract readable text from Y.js encoded contentState."""
        if not content_state:
            return ""

        try:
            decoded = base64.b64decode(content_state)
            text = decoded.decode("utf-8", errors="replace")

            # Extract readable text (Korean + ASCII printable)
            readable = re.findall(r"[\uac00-\ud7af\u0020-\u007e]+", text)

            # Structural markers to skip (ProseMirror/Y.js)
            skip_exact = {
                "prosemirror", "paragraph", "heading", "bullet_list", "list_item",
                "ordered_list", "level", "link", "null", "strong", "em", "code",
                "table", "table_row", "table_cell", "table_header", "colspan",
                "rowspan", "colwidth", "issuemention", "label", "href", "title",
                "order", "attrs", "content", "marks", "type", "text", "doc",
                "blockquote", "code_block", "hard_break", "horizontal_rule",
                "image", "suggestion_usermentions", "todo_item", "done", "language",
            }

            result = []
            for r in readable:
                r = r.strip()
                if len(r) < 2:
                    continue

                # Skip exact structural markers
                if r.lower() in skip_exact:
                    continue

                # Skip Y.js IDs and encoded strings
                if re.match(r"^w[\$\)\(A-Z]", r):
                    continue

                # Skip JSON objects and JSON-like patterns
                if r.startswith("{") or '{"' in r:
                    continue

                # Skip link markers with JSON
                if r.startswith("link") and "{" in r:
                    continue

                # Skip UUIDs
                if re.match(r"^[a-f0-9-]{36}$", r):
                    continue

                # Skip single characters or pure numbers
                if len(r) <= 2 and not re.search(r"[\uac00-\ud7af]", r):
                    continue

                # Skip strings that are mostly special characters
                if len(r) > 0:
                    special_ratio = sum(1 for c in r if c in "()[]{}$#@*&^%") / len(r)
                    if special_ratio > 0.3:
                        continue

                result.append(r)

            # Join and clean up
            text = " ".join(result)
            # Remove excessive whitespace and parentheses artifacts
            text = re.sub(r"\s*\(\s*$", "", text)
            text = re.sub(r"^\s*\)\s*", "", text)
            text = re.sub(r"\s+", " ", text)
            return text.strip()
        except Exception:
            return ""

    def _extract_comment_text(self, body_data: Any) -> str:
        """Extract plain text from ProseMirror bodyData format."""
        if body_data is None:
            return ""
        if isinstance(body_data, str):
            try:
                body_data = json.loads(body_data)
            except json.JSONDecodeError:
                return body_data

        def extract(node: Any) -> str:
            if isinstance(node, dict):
                node_type = node.get("type", "")
                if node_type == "text":
                    return node.get("text", "")
                if node_type == "suggestion_userMentions":
                    label = node.get("attrs", {}).get("label", "")
                    return f"@{label}" if label else ""
                if node_type == "hardBreak":
                    return "\n"
                content = node.get("content", [])
                return "".join(extract(c) for c in content)
            elif isinstance(node, list):
                return "".join(extract(c) for c in node)
            return ""

        return extract(body_data)

    def _load_from_store(
        self, db: ccl_chromium_indexeddb.WrappedDatabase, store_name: str
    ):
        """Load all records from a store, handling None values."""
        try:
            store = db[store_name]
            for record in store.iterate_records():
                if record.value:
                    yield record.value
        except Exception:
            pass

    def _reload_cache(self) -> None:
        """Reload all data from the IndexedDB."""
        wrapper = self._get_wrapper()
        db = self._find_linear_db(wrapper)

        # Detect stores if not already done
        if self._stores is None:
            self._stores = detect_stores(db)

        cache = CachedData(loaded_at=time.time())

        # Load teams
        if self._stores.teams:
            for val in self._load_from_store(db, self._stores.teams):
                cache.teams[val["id"]] = {
                    "id": val["id"],
                    "key": val.get("key"),
                    "name": val.get("name"),
                }

        # Load users from all detected user stores
        if self._stores.users:
            for store_name in self._stores.users:
                for val in self._load_from_store(db, store_name):
                    if val.get("id") not in cache.users:
                        cache.users[val["id"]] = {
                            "id": val["id"],
                            "name": val.get("name"),
                            "displayName": val.get("displayName"),
                            "email": val.get("email"),
                        }

        # Load workflow states from all detected state stores
        if self._stores.workflow_states:
            for store_name in self._stores.workflow_states:
                for val in self._load_from_store(db, store_name):
                    if val.get("id") not in cache.states:
                        cache.states[val["id"]] = {
                            "id": val["id"],
                            "name": val.get("name"),
                            "type": val.get("type"),
                            "color": val.get("color"),
                            "teamId": val.get("teamId"),
                            "position": val.get("position"),
                        }

        # Load issues
        if self._stores.issues:
            for val in self._load_from_store(db, self._stores.issues):
                team = cache.teams.get(val.get("teamId"), {})
                team_key = team.get("key", "???")
                identifier = f"{team_key}-{val.get('number')}"

                # Try descriptionData (ProseMirror format) first, fall back to description
                description = val.get("description")
                if not description and val.get("descriptionData"):
                    description = self._extract_comment_text(val.get("descriptionData"))

                cache.issues[val["id"]] = {
                    "id": val["id"],
                    "identifier": identifier,
                    "title": val.get("title"),
                    "description": description,
                    "number": val.get("number"),
                    "priority": val.get("priority"),
                    "estimate": val.get("estimate"),
                    "teamId": val.get("teamId"),
                    "stateId": val.get("stateId"),
                    "assigneeId": val.get("assigneeId"),
                    "projectId": val.get("projectId"),
                    "labelIds": val.get("labelIds", []),
                    "dueDate": val.get("dueDate"),
                    "createdAt": val.get("createdAt"),
                    "updatedAt": val.get("updatedAt"),
                }

        # Load comments
        if self._stores.comments:
            for val in self._load_from_store(db, self._stores.comments):
                comment_id = val.get("id")
                issue_id = val.get("issueId")
                if not comment_id or not issue_id:
                    continue

                cache.comments[comment_id] = {
                    "id": comment_id,
                    "issueId": issue_id,
                    "userId": val.get("userId"),
                    "body": self._extract_comment_text(val.get("bodyData")),
                    "createdAt": val.get("createdAt"),
                    "updatedAt": val.get("updatedAt"),
                }

                if issue_id not in cache.comments_by_issue:
                    cache.comments_by_issue[issue_id] = []
                cache.comments_by_issue[issue_id].append(comment_id)

        # Load projects
        if self._stores.projects:
            for val in self._load_from_store(db, self._stores.projects):
                cache.projects[val["id"]] = {
                    "id": val["id"],
                    "name": val.get("name"),
                    "description": val.get("description"),
                    "slugId": val.get("slugId"),
                    "icon": val.get("icon"),
                    "color": val.get("color"),
                    "state": val.get("state"),
                    "statusId": val.get("statusId"),
                    "priority": val.get("priority"),
                    "teamIds": val.get("teamIds", []),
                    "memberIds": val.get("memberIds", []),
                    "leadId": val.get("leadId"),
                    "startDate": val.get("startDate"),
                    "targetDate": val.get("targetDate"),
                    "createdAt": val.get("createdAt"),
                    "updatedAt": val.get("updatedAt"),
                }

        # Load issue content (Y.js encoded descriptions)
        if self._stores.issue_content:
            for val in self._load_from_store(db, self._stores.issue_content):
                issue_id = val.get("issueId")
                content_state = val.get("contentState")
                if issue_id and content_state:
                    extracted = self._extract_yjs_text(content_state)
                    if extracted:
                        cache.issue_content[issue_id] = extracted

        # Update issues with descriptions from issue_content
        for issue_id, desc in cache.issue_content.items():
            if issue_id in cache.issues and not cache.issues[issue_id].get("description"):
                cache.issues[issue_id]["description"] = desc

        self._cache = cache

    def _ensure_cache(self) -> CachedData:
        """Ensure the cache is loaded and not expired."""
        if self._cache.is_expired() or not self._cache.teams:
            self._reload_cache()
        return self._cache

    @property
    def teams(self) -> dict[str, dict[str, Any]]:
        """Get all teams."""
        return self._ensure_cache().teams

    @property
    def users(self) -> dict[str, dict[str, Any]]:
        """Get all users."""
        return self._ensure_cache().users

    @property
    def states(self) -> dict[str, dict[str, Any]]:
        """Get all workflow states."""
        return self._ensure_cache().states

    @property
    def issues(self) -> dict[str, dict[str, Any]]:
        """Get all issues."""
        return self._ensure_cache().issues

    @property
    def comments(self) -> dict[str, dict[str, Any]]:
        """Get all comments."""
        return self._ensure_cache().comments

    @property
    def projects(self) -> dict[str, dict[str, Any]]:
        """Get all projects."""
        return self._ensure_cache().projects

    def get_comments_for_issue(self, issue_id: str) -> list[dict[str, Any]]:
        """Get all comments for an issue, sorted by creation time."""
        cache = self._ensure_cache()
        comment_ids = cache.comments_by_issue.get(issue_id, [])
        comments = [cache.comments[cid] for cid in comment_ids if cid in cache.comments]
        return sorted(comments, key=lambda c: c.get("createdAt", ""))

    def find_user(self, search: str) -> dict[str, Any] | None:
        """Find a user by name or display name (case-insensitive partial match)."""
        search_lower = search.lower()
        candidates: list[tuple[int, dict[str, Any]]] = []

        for user in self.users.values():
            name = self._to_str(user.get("name", ""))
            display_name = self._to_str(user.get("displayName", ""))

            name_lower = name.lower()
            display_lower = display_name.lower()

            if search_lower in name_lower or search_lower in display_lower:
                score = 0
                if name_lower.startswith(search_lower):
                    score = 100
                elif f" {search_lower}" in f" {name_lower}":
                    score = 50
                elif display_lower.startswith(search_lower):
                    score = 40
                else:
                    score = 10

                candidates.append((score, user))

        if candidates:
            candidates.sort(key=lambda x: -x[0])
            return candidates[0][1]
        return None

    def find_team(self, search: str) -> dict[str, Any] | None:
        """Find a team by key or name (case-insensitive)."""
        search_lower = search.lower()
        search_upper = search.upper()

        for team in self.teams.values():
            key = team.get("key", "")
            name = self._to_str(team.get("name", ""))

            if key == search_upper or search_lower in name.lower():
                return team
        return None

    def get_issue_by_identifier(self, identifier: str) -> dict[str, Any] | None:
        """Get an issue by its identifier (e.g., 'UK-1234')."""
        identifier_upper = identifier.upper()
        for issue in self.issues.values():
            if issue.get("identifier", "").upper() == identifier_upper:
                return issue
        return None

    def find_project(self, search: str) -> dict[str, Any] | None:
        """Find a project by name or slugId (case-insensitive partial match)."""
        search_lower = search.lower()
        candidates: list[tuple[int, dict[str, Any]]] = []

        for project in self.projects.values():
            name = self._to_str(project.get("name", ""))
            slug_id = self._to_str(project.get("slugId", ""))

            name_lower = name.lower()
            slug_lower = slug_id.lower()

            if search_lower in name_lower or search_lower == slug_lower:
                score = 0
                if name_lower == search_lower:
                    score = 100
                elif name_lower.startswith(search_lower):
                    score = 80
                elif slug_lower == search_lower:
                    score = 70
                else:
                    score = 10

                candidates.append((score, project))

        if candidates:
            candidates.sort(key=lambda x: -x[0])
            return candidates[0][1]
        return None

    def get_issues_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """Get all issues assigned to a user."""
        return [
            issue
            for issue in self.issues.values()
            if issue.get("assigneeId") == user_id
        ]

    def get_state_name(self, state_id: str) -> str:
        """Get state name from state ID."""
        state = self.states.get(state_id, {})
        return state.get("name", "Unknown")

    def get_state_type(self, state_id: str) -> str:
        """Get state type from state ID."""
        state = self.states.get(state_id, {})
        return state.get("type", "unknown")

    def search_issues(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Search issues by title (case-insensitive)."""
        query_lower = query.lower()
        results = []

        for issue in self.issues.values():
            title = self._to_str(issue.get("title", ""))
            if query_lower in title.lower():
                results.append(issue)
                if len(results) >= limit:
                    break

        return results

    def get_summary(self) -> dict[str, int]:
        """Get a summary of loaded data counts."""
        cache = self._ensure_cache()
        return {
            "teams": len(cache.teams),
            "users": len(cache.users),
            "states": len(cache.states),
            "issues": len(cache.issues),
            "comments": len(cache.comments),
            "projects": len(cache.projects),
        }

    def get_user_name(self, user_id: str | None) -> str:
        """Get user name from user ID."""
        if not user_id:
            return "Unassigned"
        user = self.users.get(user_id, {})
        return user.get("name") or user.get("displayName") or "Unknown"

    def get_team_key(self, team_id: str | None) -> str:
        """Get team key from team ID."""
        if not team_id:
            return "???"
        team = self.teams.get(team_id, {})
        return team.get("key", "???")

    def get_project_name(self, project_id: str | None) -> str:
        """Get project name from project ID."""
        if not project_id:
            return ""
        project = self.projects.get(project_id, {})
        return project.get("name", "")
