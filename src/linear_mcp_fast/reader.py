"""
Linear Local Data Reader with TTL-based caching.

Reads Linear's local IndexedDB cache to provide fast access to issues, users,
teams, workflow states, and comments without API calls.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ccl_chromium_reader import ccl_chromium_indexeddb  # type: ignore

from .store_detector import DetectedStores, detect_stores

logger = logging.getLogger(__name__)

LINEAR_DB_PATH = os.path.expanduser(
    "~/Library/Application Support/Linear/IndexedDB/https_linear.app_0.indexeddb.leveldb"
)
LINEAR_BLOB_PATH = os.path.expanduser(
    "~/Library/Application Support/Linear/IndexedDB/https_linear.app_0.indexeddb.blob"
)

CACHE_TTL_SECONDS = 300  # 5 minutes
IDLE_REFRESH_THRESHOLD_SECONDS = int(
    os.getenv("LINEAR_FAST_IDLE_REFRESH_SECONDS", "60")
)
LOAD_DOCUMENT_CONTENT = os.getenv("LINEAR_FAST_LOAD_DOCUMENT_CONTENT", "0") == "1"

REQUIRED_STORE_KEYS = {"issues", "teams", "users", "workflow_states", "projects"}


def _parse_csv_env(var_name: str) -> set[str]:
    raw = os.getenv(var_name, "")
    values = [item.strip() for item in raw.split(",")]
    return {item for item in values if item}


@dataclass
class LocalHealth:
    """Health state for local cache reads."""

    degraded: bool = False
    reason: str | None = None
    failure_count: int = 0
    last_error: str | None = None
    last_error_at: float | None = None
    last_success_at: float | None = None


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
    labels: dict[str, dict[str, Any]] = field(default_factory=dict)
    initiatives: dict[str, dict[str, Any]] = field(default_factory=dict)
    cycles: dict[str, dict[str, Any]] = field(default_factory=dict)
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    document_content: dict[str, dict[str, Any]] = field(default_factory=dict)
    milestones: dict[str, dict[str, Any]] = field(default_factory=dict)
    project_updates: dict[str, dict[str, Any]] = field(default_factory=dict)
    project_statuses: dict[str, dict[str, Any]] = field(default_factory=dict)

    issue_counts_by_team: dict[str, int] = field(default_factory=dict)
    issue_counts_by_project: dict[str, int] = field(default_factory=dict)
    issue_counts_by_user: dict[str, int] = field(default_factory=dict)

    issue_state_counts_by_team: dict[str, dict[str, int]] = field(default_factory=dict)
    issue_state_counts_by_project: dict[str, dict[str, int]] = field(default_factory=dict)
    issue_state_counts_by_user: dict[str, dict[str, int]] = field(default_factory=dict)

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
        self._reload_lock = threading.Lock()
        self._health = LocalHealth()
        self._force_next_refresh = False
        self._last_tool_call_at: float = 0.0
        self._scope_account_emails = (
            _parse_csv_env("LINEAR_FAST_ACCOUNT_EMAILS")
            | _parse_csv_env("LINEAR_FAST_ACCOUNT_EMAIL")
        )
        self._scope_user_account_ids = (
            _parse_csv_env("LINEAR_FAST_USER_ACCOUNT_IDS")
            | _parse_csv_env("LINEAR_FAST_USER_ACCOUNT_ID")
        )

    def _set_degraded(self, reason: str) -> None:
        self._health.degraded = True
        self._health.reason = reason
        self._health.failure_count += 1
        self._health.last_error = reason
        self._health.last_error_at = time.time()

    def _set_healthy(self) -> None:
        self._health.degraded = False
        self._health.reason = None
        self._health.failure_count = 0
        self._health.last_success_at = time.time()

    def get_health(self) -> dict[str, Any]:
        return {
            "degraded": self._health.degraded,
            "reason": self._health.reason,
            "failureCount": self._health.failure_count,
            "lastError": self._health.last_error,
            "lastErrorAt": self._health.last_error_at,
            "lastSuccessAt": self._health.last_success_at,
            "loadedAt": self._cache.loaded_at,
            "ttlSeconds": CACHE_TTL_SECONDS,
            "lastToolCallAt": self._last_tool_call_at,
            "idleRefreshThresholdSeconds": IDLE_REFRESH_THRESHOLD_SECONDS,
            "scopeAccountEmails": sorted(self._scope_account_emails),
            "scopeUserAccountIds": sorted(self._scope_user_account_ids),
        }

    def is_degraded(self) -> bool:
        return self._health.degraded

    def refresh_cache(self, force: bool = True) -> None:
        if force:
            self._reload_cache()
            return
        self._ensure_cache()

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

    def _find_all_linear_dbs(
        self, wrapper: ccl_chromium_indexeddb.WrappedIndexDB
    ) -> list[ccl_chromium_indexeddb.WrappedDatabase]:
        """Find all Linear databases with data."""
        databases = []
        for db_id in wrapper.database_ids:
            if "linear_" in db_id.name and db_id.name != "linear_databases":
                db = wrapper[db_id.name, db_id.origin]
                if list(db.object_store_names):
                    databases.append(db)
        if not databases:
            raise ValueError("Could not find Linear database in IndexedDB")
        return databases

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
            readable = re.findall(r"[\uac00-\ud7af\u0020-\u007e]+", text)

            skip_exact = {
                "prosemirror",
                "paragraph",
                "heading",
                "bullet_list",
                "list_item",
                "ordered_list",
                "level",
                "link",
                "null",
                "strong",
                "em",
                "code",
                "table",
                "table_row",
                "table_cell",
                "table_header",
                "colspan",
                "rowspan",
                "colwidth",
                "issuemention",
                "label",
                "href",
                "title",
                "order",
                "attrs",
                "content",
                "marks",
                "type",
                "text",
                "doc",
                "blockquote",
                "code_block",
                "hard_break",
                "horizontal_rule",
                "image",
                "suggestion_usermentions",
                "todo_item",
                "done",
                "language",
            }

            result = []
            for r in readable:
                r = r.strip()
                if len(r) < 2:
                    continue

                r_lower = r.lower()
                if r_lower in skip_exact:
                    continue

                skip_prefixes = {"suggestion_usermentions", "issuemention", "prosemirror"}
                if any(r_lower.startswith(p) for p in skip_prefixes):
                    continue

                if re.match(r"^w[\$\)\(A-Z]", r):
                    continue
                if r.startswith("{") or '{"' in r:
                    continue
                if r.startswith("link") and "{" in r:
                    continue
                if re.match(r"^[a-f0-9-]{36}$", r):
                    continue
                if len(r) <= 2 and not re.search(r"[\uac00-\ud7af]", r):
                    continue

                special_ratio = sum(1 for c in r if c in "()[]{}$#@*&^%") / len(r)
                if special_ratio > 0.3:
                    continue

                result.append(r)

            text = " ".join(result)
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
            if isinstance(node, list):
                return "".join(extract(c) for c in node)
            return ""

        return extract(body_data)

    def _load_from_store(
        self,
        db: ccl_chromium_indexeddb.WrappedDatabase,
        store_name: str,
        load_errors: list[str] | None = None,
    ):
        """Load all records from a store, collecting errors for degraded health."""
        try:
            store = db[store_name]
            for record in store.iterate_records():
                if record.value:
                    yield record.value
        except Exception as exc:
            if load_errors is not None:
                load_errors.append(f"{store_name}: {exc}")

    @staticmethod
    def _detected_store_keys(stores: DetectedStores) -> set[str]:
        keys: set[str] = set()
        if stores.issues:
            keys.add("issues")
        if stores.teams:
            keys.add("teams")
        if stores.users:
            keys.add("users")
        if stores.workflow_states:
            keys.add("workflow_states")
        if stores.projects:
            keys.add("projects")
        return keys

    @staticmethod
    def _bump(counter: dict[str, int], key: str | None) -> None:
        if not key:
            return
        counter[key] = counter.get(key, 0) + 1

    @staticmethod
    def _bump_nested(counter: dict[str, dict[str, int]], key: str | None, state: str) -> None:
        if not key:
            return
        if key not in counter:
            counter[key] = {}
        state_counter = counter[key]
        state_counter[state] = state_counter.get(state, 0) + 1

    def _build_issue_indexes(self, cache: CachedData) -> None:
        """Build lightweight per-entity issue count indexes for fast handlers."""
        cache.issue_counts_by_team.clear()
        cache.issue_counts_by_project.clear()
        cache.issue_counts_by_user.clear()
        cache.issue_state_counts_by_team.clear()
        cache.issue_state_counts_by_project.clear()
        cache.issue_state_counts_by_user.clear()

        for issue in cache.issues.values():
            team_id = issue.get("teamId")
            project_id = issue.get("projectId")
            assignee_id = issue.get("assigneeId")

            state_id = issue.get("stateId")
            state_type = cache.states.get(state_id, {}).get("type", "unknown")

            self._bump(cache.issue_counts_by_team, team_id)
            self._bump(cache.issue_counts_by_project, project_id)
            self._bump(cache.issue_counts_by_user, assignee_id)

            self._bump_nested(cache.issue_state_counts_by_team, team_id, state_type)
            self._bump_nested(cache.issue_state_counts_by_project, project_id, state_type)
            self._bump_nested(cache.issue_state_counts_by_user, assignee_id, state_type)

    def _is_account_scope_enabled(self) -> bool:
        return bool(self._scope_account_emails or self._scope_user_account_ids)

    def _apply_account_scope(self, cache: CachedData) -> None:
        """
        Restrict cached data to organizations belonging to allowed account(s).

        Scope inputs:
        - LINEAR_FAST_ACCOUNT_EMAILS / LINEAR_FAST_ACCOUNT_EMAIL
        - LINEAR_FAST_USER_ACCOUNT_IDS / LINEAR_FAST_USER_ACCOUNT_ID
        """
        if not self._is_account_scope_enabled():
            return

        allowed_account_ids = set(self._scope_user_account_ids)
        if self._scope_account_emails:
            for user in cache.users.values():
                email = self._to_str(user.get("email")).strip().lower()
                account_id = self._to_str(user.get("userAccountId")).strip()
                if email in self._scope_account_emails and account_id:
                    allowed_account_ids.add(account_id)

        if not allowed_account_ids:
            raise ValueError(
                "account scope configured but no matching userAccountId found"
            )

        allowed_org_ids = {
            self._to_str(user.get("organizationId")).strip()
            for user in cache.users.values()
            if self._to_str(user.get("userAccountId")).strip() in allowed_account_ids
            and self._to_str(user.get("organizationId")).strip()
        }
        if not allowed_org_ids:
            raise ValueError(
                "account scope configured but no matching organizationId found"
            )

        cache.users = {
            user_id: user
            for user_id, user in cache.users.items()
            if self._to_str(user.get("organizationId")).strip() in allowed_org_ids
        }
        allowed_user_ids = set(cache.users.keys())

        cache.teams = {
            team_id: team
            for team_id, team in cache.teams.items()
            if self._to_str(team.get("organizationId")).strip() in allowed_org_ids
        }
        allowed_team_ids = set(cache.teams.keys())

        cache.states = {
            state_id: state
            for state_id, state in cache.states.items()
            if state.get("teamId") in allowed_team_ids
        }

        cache.issues = {
            issue_id: issue
            for issue_id, issue in cache.issues.items()
            if issue.get("teamId") in allowed_team_ids
        }
        allowed_issue_ids = set(cache.issues.keys())
        cache.issue_content = {
            issue_id: body
            for issue_id, body in cache.issue_content.items()
            if issue_id in allowed_issue_ids
        }

        cache.comments = {
            comment_id: comment
            for comment_id, comment in cache.comments.items()
            if comment.get("issueId") in allowed_issue_ids
        }
        cache.comments_by_issue = {}
        for comment_id, comment in cache.comments.items():
            issue_id = comment.get("issueId")
            if not issue_id:
                continue
            cache.comments_by_issue.setdefault(issue_id, []).append(comment_id)

        def _project_allowed(project: dict[str, Any]) -> bool:
            team_ids = [tid for tid in project.get("teamIds", []) if tid]
            if team_ids:
                return any(tid in allowed_team_ids for tid in team_ids)

            lead_id = project.get("leadId")
            if lead_id and lead_id in allowed_user_ids:
                return True

            member_ids = [uid for uid in project.get("memberIds", []) if uid]
            return any(uid in allowed_user_ids for uid in member_ids)

        cache.projects = {
            project_id: project
            for project_id, project in cache.projects.items()
            if _project_allowed(project)
        }
        allowed_project_ids = set(cache.projects.keys())

        cache.labels = {
            label_id: label
            for label_id, label in cache.labels.items()
            if not label.get("teamId") or label.get("teamId") in allowed_team_ids
        }

        def _initiative_allowed(initiative: dict[str, Any]) -> bool:
            team_ids = [tid for tid in initiative.get("teamIds", []) if tid]
            if team_ids:
                return any(tid in allowed_team_ids for tid in team_ids)
            owner_id = initiative.get("ownerId")
            return bool(owner_id and owner_id in allowed_user_ids)

        cache.initiatives = {
            initiative_id: initiative
            for initiative_id, initiative in cache.initiatives.items()
            if _initiative_allowed(initiative)
        }

        cache.cycles = {
            cycle_id: cycle
            for cycle_id, cycle in cache.cycles.items()
            if cycle.get("teamId") in allowed_team_ids
        }

        cache.documents = {
            document_id: document
            for document_id, document in cache.documents.items()
            if (
                document.get("projectId") in allowed_project_ids
                or (
                    not document.get("projectId")
                    and document.get("creatorId") in allowed_user_ids
                )
            )
        }

        cache.milestones = {
            milestone_id: milestone
            for milestone_id, milestone in cache.milestones.items()
            if milestone.get("projectId") in allowed_project_ids
        }

        cache.project_updates = {
            update_id: update
            for update_id, update in cache.project_updates.items()
            if update.get("projectId") in allowed_project_ids
        }

        allowed_project_status_ids = {
            project.get("statusId")
            for project in cache.projects.values()
            if project.get("statusId")
        }
        cache.project_statuses = {
            status_id: status
            for status_id, status in cache.project_statuses.items()
            if status_id in allowed_project_status_ids
        }

    def _reload_cache(self) -> None:
        """Reload all data from all Linear IndexedDB databases."""
        with self._reload_lock:
            try:
                wrapper = self._get_wrapper()
                databases = self._find_all_linear_dbs(wrapper)

                cache = CachedData(loaded_at=time.time())
                load_errors: list[str] = []
                soft_errors: list[str] = []
                detected_keys: set[str] = set()

                for db in databases:
                    stores = detect_stores(db)
                    detected_keys.update(self._detected_store_keys(stores))
                    self._load_from_db(db, stores, cache, load_errors, soft_errors)

                self._apply_account_scope(cache)

                for project in cache.projects.values():
                    status_id = project.get("statusId")
                    if status_id and status_id in cache.project_statuses:
                        project["state"] = cache.project_statuses[status_id].get("name")

                self._build_issue_indexes(cache)
                self._cache = cache

                missing_required = sorted(REQUIRED_STORE_KEYS - detected_keys)
                if missing_required:
                    self._set_degraded(
                        f"missing required stores: {', '.join(missing_required)}"
                    )
                elif not cache.issues or not cache.teams or not cache.users:
                    self._set_degraded("required entities are missing from cache")
                elif load_errors:
                    self._set_degraded(f"store read errors: {len(load_errors)}")
                else:
                    if soft_errors:
                        logger.warning(
                            "non-critical store read errors (ignored): %s",
                            "; ".join(soft_errors),
                        )
                    self._set_healthy()
            except Exception as exc:
                self._set_degraded(str(exc))
                raise

    def _load_from_db(
        self,
        db: ccl_chromium_indexeddb.WrappedDatabase,
        stores: DetectedStores,
        cache: CachedData,
        load_errors: list[str],
        soft_errors: list[str] | None = None,
    ) -> None:
        """Load data from a single database into the cache."""

        if stores.teams:
            for val in self._load_from_store(db, stores.teams, load_errors):
                cache.teams[val["id"]] = {
                    "id": val["id"],
                    "key": val.get("key"),
                    "name": val.get("name"),
                    "organizationId": val.get("organizationId"),
                }

        if stores.users:
            for store_name in stores.users:
                for val in self._load_from_store(db, store_name, load_errors):
                    if val.get("id") not in cache.users:
                        cache.users[val["id"]] = {
                            "id": val["id"],
                            "name": val.get("name"),
                            "displayName": val.get("displayName"),
                            "email": val.get("email"),
                            "organizationId": val.get("organizationId"),
                            "userAccountId": val.get("userAccountId"),
                            "active": val.get("active"),
                        }

        if stores.workflow_states:
            for store_name in stores.workflow_states:
                for val in self._load_from_store(db, store_name, load_errors):
                    if val.get("id") not in cache.states:
                        cache.states[val["id"]] = {
                            "id": val["id"],
                            "name": val.get("name"),
                            "type": val.get("type"),
                            "color": val.get("color"),
                            "teamId": val.get("teamId"),
                            "position": val.get("position"),
                        }

        if stores.issues:
            for val in self._load_from_store(db, stores.issues, load_errors):
                team = cache.teams.get(val.get("teamId"), {})
                team_key = team.get("key", "???")
                identifier = f"{team_key}-{val.get('number')}"

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

        if stores.comments:
            for val in self._load_from_store(db, stores.comments, load_errors):
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

        if stores.projects:
            for val in self._load_from_store(db, stores.projects, load_errors):
                cache.projects[val["id"]] = {
                    "id": val["id"],
                    "name": val.get("name"),
                    "description": val.get("description"),
                    "slugId": val.get("slugId"),
                    "icon": val.get("icon"),
                    "color": val.get("color"),
                    "state": None,
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

        if stores.issue_content:
            _ic_errors = soft_errors if soft_errors is not None else load_errors
            for val in self._load_from_store(db, stores.issue_content, _ic_errors):
                issue_id = val.get("issueId")
                content_state = val.get("contentState")
                if issue_id and content_state:
                    extracted = self._extract_yjs_text(content_state)
                    if extracted:
                        cache.issue_content[issue_id] = extracted

        for issue_id, desc in cache.issue_content.items():
            if issue_id in cache.issues and not cache.issues[issue_id].get("description"):
                cache.issues[issue_id]["description"] = desc

        if stores.labels:
            for store_name in stores.labels:
                for val in self._load_from_store(db, store_name, load_errors):
                    if val.get("id") not in cache.labels:
                        cache.labels[val["id"]] = {
                            "id": val["id"],
                            "name": val.get("name"),
                            "color": val.get("color"),
                            "isGroup": val.get("isGroup"),
                            "parentId": val.get("parentId"),
                            "teamId": val.get("teamId"),
                        }

        if stores.initiatives:
            for val in self._load_from_store(db, stores.initiatives, load_errors):
                cache.initiatives[val["id"]] = {
                    "id": val["id"],
                    "name": val.get("name"),
                    "slugId": val.get("slugId"),
                    "color": val.get("color"),
                    "status": val.get("status"),
                    "ownerId": val.get("ownerId"),
                    "teamIds": val.get("teamIds", []),
                    "createdAt": val.get("createdAt"),
                    "updatedAt": val.get("updatedAt"),
                }

        if stores.cycles:
            for val in self._load_from_store(db, stores.cycles, load_errors):
                cache.cycles[val["id"]] = {
                    "id": val["id"],
                    "number": val.get("number"),
                    "teamId": val.get("teamId"),
                    "startsAt": val.get("startsAt"),
                    "endsAt": val.get("endsAt"),
                    "completedAt": val.get("completedAt"),
                    "currentProgress": val.get("currentProgress"),
                }

        if stores.documents:
            for val in self._load_from_store(db, stores.documents, load_errors):
                doc_id = val.get("id")
                existing = cache.documents.get(doc_id)
                if existing and existing.get("updatedAt", "") >= val.get("updatedAt", ""):
                    continue
                cache.documents[doc_id] = {
                    "id": doc_id,
                    "title": val.get("title"),
                    "slugId": val.get("slugId"),
                    "projectId": val.get("projectId"),
                    "creatorId": val.get("creatorId"),
                    "createdAt": val.get("createdAt"),
                    "updatedAt": val.get("updatedAt"),
                }

        if LOAD_DOCUMENT_CONTENT and stores.document_content:
            for val in self._load_from_store(db, stores.document_content, load_errors):
                content_id = val.get("documentContentId")
                if content_id:
                    cache.document_content[content_id] = {
                        "id": val.get("id"),
                        "documentContentId": content_id,
                        "contentData": val.get("contentData"),
                    }

        if stores.milestones:
            for val in self._load_from_store(db, stores.milestones, load_errors):
                cache.milestones[val["id"]] = {
                    "id": val["id"],
                    "name": val.get("name"),
                    "projectId": val.get("projectId"),
                    "targetDate": val.get("targetDate"),
                    "sortOrder": val.get("sortOrder"),
                    "currentProgress": val.get("currentProgress"),
                }

        if stores.project_statuses:
            for val in self._load_from_store(db, stores.project_statuses, load_errors):
                status_id = val.get("id")
                if status_id and status_id not in cache.project_statuses:
                    cache.project_statuses[status_id] = {
                        "id": status_id,
                        "name": val.get("name"),
                        "color": val.get("color"),
                        "type": val.get("type"),
                    }

        if stores.project_updates:
            for val in self._load_from_store(db, stores.project_updates, load_errors):
                cache.project_updates[val["id"]] = {
                    "id": val["id"],
                    "body": val.get("body"),
                    "health": val.get("health"),
                    "projectId": val.get("projectId"),
                    "userId": val.get("userId"),
                    "createdAt": val.get("createdAt"),
                    "updatedAt": val.get("updatedAt"),
                }

    def mark_stale(self) -> None:
        """Force next cache access to trigger a full reload."""
        self._force_next_refresh = True

    def ensure_fresh(self) -> None:
        """Mark cache stale if idle gap exceeds threshold (reconnect heuristic)."""
        now = time.time()
        last = self._last_tool_call_at
        self._last_tool_call_at = now
        if last == 0.0:
            return  # first call — lifespan already loaded cache
        if now - last >= IDLE_REFRESH_THRESHOLD_SECONDS:
            logger.info(
                "Idle gap %.1fs >= %ds, forcing cache refresh",
                now - last,
                IDLE_REFRESH_THRESHOLD_SECONDS,
            )
            self._force_next_refresh = True

    def _ensure_cache(self) -> CachedData:
        """Ensure the cache is loaded and not expired."""
        if self._force_next_refresh or self._cache.is_expired() or not self._cache.teams:
            self._force_next_refresh = False
            self._reload_cache()
        return self._cache

    @property
    def teams(self) -> dict[str, dict[str, Any]]:
        return self._ensure_cache().teams

    @property
    def users(self) -> dict[str, dict[str, Any]]:
        return self._ensure_cache().users

    @property
    def states(self) -> dict[str, dict[str, Any]]:
        return self._ensure_cache().states

    @property
    def issues(self) -> dict[str, dict[str, Any]]:
        return self._ensure_cache().issues

    @property
    def comments(self) -> dict[str, dict[str, Any]]:
        return self._ensure_cache().comments

    @property
    def projects(self) -> dict[str, dict[str, Any]]:
        return self._ensure_cache().projects

    @property
    def labels(self) -> dict[str, dict[str, Any]]:
        return self._ensure_cache().labels

    @property
    def initiatives(self) -> dict[str, dict[str, Any]]:
        return self._ensure_cache().initiatives

    @property
    def cycles(self) -> dict[str, dict[str, Any]]:
        return self._ensure_cache().cycles

    @property
    def documents(self) -> dict[str, dict[str, Any]]:
        return self._ensure_cache().documents

    @property
    def milestones(self) -> dict[str, dict[str, Any]]:
        return self._ensure_cache().milestones

    @property
    def project_updates(self) -> dict[str, dict[str, Any]]:
        return self._ensure_cache().project_updates

    def get_issue_count_for_team(self, team_id: str | None) -> int:
        cache = self._ensure_cache()
        return cache.issue_counts_by_team.get(team_id or "", 0)

    def get_issue_count_for_project(self, project_id: str | None) -> int:
        cache = self._ensure_cache()
        return cache.issue_counts_by_project.get(project_id or "", 0)

    def get_issue_count_for_user(self, user_id: str | None) -> int:
        cache = self._ensure_cache()
        return cache.issue_counts_by_user.get(user_id or "", 0)

    def get_issue_state_counts_for_team(self, team_id: str | None) -> dict[str, int]:
        cache = self._ensure_cache()
        return dict(cache.issue_state_counts_by_team.get(team_id or "", {}))

    def get_issue_state_counts_for_project(self, project_id: str | None) -> dict[str, int]:
        cache = self._ensure_cache()
        return dict(cache.issue_state_counts_by_project.get(project_id or "", {}))

    def get_issue_state_counts_for_user(self, user_id: str | None) -> dict[str, int]:
        cache = self._ensure_cache()
        return dict(cache.issue_state_counts_by_user.get(user_id or "", {}))

    def get_comments_for_issue(self, issue_id: str) -> list[dict[str, Any]]:
        cache = self._ensure_cache()
        comment_ids = cache.comments_by_issue.get(issue_id, [])
        comments = [cache.comments[cid] for cid in comment_ids if cid in cache.comments]
        return sorted(comments, key=lambda c: c.get("createdAt", ""))

    def find_user(self, search: str) -> dict[str, Any] | None:
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
        search_lower = search.lower()
        search_upper = search.upper()

        for team in self.teams.values():
            key = team.get("key", "")
            name = self._to_str(team.get("name", ""))
            if key == search_upper or search_lower in name.lower():
                return team
        return None

    def find_issue_status(self, team_id: str, query: str) -> dict[str, Any] | None:
        query_lower = query.lower()
        candidates: list[tuple[int, dict[str, Any]]] = []

        for state in self.states.values():
            if state.get("teamId") != team_id:
                continue

            state_id = self._to_str(state.get("id", ""))
            name = self._to_str(state.get("name", ""))
            state_id_lower = state_id.lower()
            name_lower = name.lower()

            if state_id_lower == query_lower:
                score = 100
            elif name_lower == query_lower:
                score = 90
            elif name_lower.startswith(query_lower):
                score = 70
            elif query_lower in name_lower:
                score = 10
            else:
                continue

            candidates.append((score, state))

        if candidates:
            candidates.sort(key=lambda x: -x[0])
            return candidates[0][1]
        return None

    def get_issue_by_identifier(self, identifier: str) -> dict[str, Any] | None:
        identifier_upper = identifier.upper()
        for issue in self.issues.values():
            if issue.get("identifier", "").upper() == identifier_upper:
                return issue
        return None

    def find_project(self, search: str) -> dict[str, Any] | None:
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

    def find_milestone(self, project_id: str, query: str) -> dict[str, Any] | None:
        query_lower = query.lower()
        candidates: list[tuple[int, dict[str, Any]]] = []

        for milestone in self.milestones.values():
            if milestone.get("projectId") != project_id:
                continue

            milestone_id = self._to_str(milestone.get("id", ""))
            name = self._to_str(milestone.get("name", ""))
            milestone_id_lower = milestone_id.lower()
            name_lower = name.lower()

            if milestone_id_lower == query_lower:
                score = 100
            elif name_lower == query_lower:
                score = 90
            elif name_lower.startswith(query_lower):
                score = 70
            elif query_lower in name_lower:
                score = 10
            else:
                continue

            candidates.append((score, milestone))

        if candidates:
            candidates.sort(key=lambda x: -x[0])
            return candidates[0][1]
        return None

    def get_issues_for_user(self, user_id: str) -> list[dict[str, Any]]:
        return [issue for issue in self.issues.values() if issue.get("assigneeId") == user_id]

    def get_state_name(self, state_id: str) -> str:
        state = self.states.get(state_id, {})
        return state.get("name", "Unknown")

    def get_state_type(self, state_id: str) -> str:
        state = self.states.get(state_id, {})
        return state.get("type", "unknown")

    def search_issues(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
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
        if not user_id:
            return "Unassigned"
        user = self.users.get(user_id, {})
        return user.get("name") or user.get("displayName") or "Unknown"

    def get_team_key(self, team_id: str | None) -> str:
        if not team_id:
            return "???"
        team = self.teams.get(team_id, {})
        return team.get("key", "???")

    def get_project_name(self, project_id: str | None) -> str:
        if not project_id:
            return ""
        project = self.projects.get(project_id, {})
        return project.get("name", "")

    def get_label_name(self, label_id: str | None) -> str:
        if not label_id:
            return ""
        label = self.labels.get(label_id, {})
        return label.get("name", "")

    def get_cycles_for_team(self, team_id: str) -> list[dict[str, Any]]:
        cycles = [c for c in self.cycles.values() if c.get("teamId") == team_id]
        return sorted(cycles, key=lambda c: c.get("number", 0), reverse=True)

    def get_documents_for_project(self, project_id: str) -> list[dict[str, Any]]:
        return [d for d in self.documents.values() if d.get("projectId") == project_id]

    def get_milestones_for_project(self, project_id: str) -> list[dict[str, Any]]:
        milestones = [m for m in self.milestones.values() if m.get("projectId") == project_id]
        return sorted(milestones, key=lambda m: m.get("sortOrder", 0))

    def get_updates_for_project(self, project_id: str) -> list[dict[str, Any]]:
        updates = [u for u in self.project_updates.values() if u.get("projectId") == project_id]
        return sorted(updates, key=lambda u: u.get("createdAt", ""), reverse=True)

    def find_initiative(self, search: str) -> dict[str, Any] | None:
        search_lower = search.lower()
        for initiative in self.initiatives.values():
            name = self._to_str(initiative.get("name", ""))
            slug_id = self._to_str(initiative.get("slugId", ""))
            if search_lower in name.lower() or search_lower == slug_id.lower():
                return initiative
        return None

    def find_document(self, search: str) -> dict[str, Any] | None:
        search_lower = search.lower()
        for doc in self.documents.values():
            title = self._to_str(doc.get("title", ""))
            slug_id = self._to_str(doc.get("slugId", ""))
            if search_lower in title.lower() or search_lower == slug_id.lower():
                return doc
        return None
