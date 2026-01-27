"""
Auto-detect Linear IndexedDB object store hashes by sampling records.

Linear uses hash-based object store names that may change between versions.
This module detects stores by examining the structure of their records.
"""

from dataclasses import dataclass
from typing import Any

from ccl_chromium_reader import ccl_chromium_indexeddb  # type: ignore


@dataclass
class DetectedStores:
    """Container for detected object store names."""

    issues: str | None = None
    teams: str | None = None
    users: list[str] | None = None
    workflow_states: list[str] | None = None
    comments: str | None = None
    projects: str | None = None
    issue_content: str | None = None  # Y.js encoded issue descriptions


def _is_issue_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like an issue."""
    required = {"number", "teamId", "stateId", "title"}
    return required.issubset(record.keys())


def _is_user_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like a user."""
    required = {"name", "displayName", "email"}
    has_required = required.issubset(record.keys())
    has_avatar = "avatarUrl" in record or "avatar" in record
    return has_required and has_avatar


def _is_team_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like a team."""
    if not {"key", "name"}.issubset(record.keys()):
        return False
    key = record.get("key")
    if not isinstance(key, str):
        return False
    return key.isupper() and key.isalpha() and len(key) <= 10


def _is_workflow_state_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like a workflow state."""
    if not {"name", "type", "color"}.issubset(record.keys()):
        return False
    state_type = record.get("type")
    valid_types = {"started", "unstarted", "completed", "canceled", "backlog"}
    return state_type in valid_types


def _is_comment_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like a comment."""
    required = {"issueId", "userId", "bodyData", "createdAt"}
    return required.issubset(record.keys())


def _is_project_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like a project."""
    required = {"name", "description", "teamIds", "startDate", "targetDate", "statusId"}
    return required.issubset(record.keys())


def _is_issue_content_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like issue content (Y.js encoded description)."""
    required = {"issueId", "contentState"}
    return required.issubset(record.keys())


def detect_stores(db: ccl_chromium_indexeddb.WrappedDatabase) -> DetectedStores:
    """
    Detect object stores by sampling their first record.

    Args:
        db: The wrapped IndexedDB database to scan.

    Returns:
        DetectedStores with detected store names for each entity type.
    """
    result = DetectedStores(users=[], workflow_states=[])

    for store_name in db.object_store_names:
        if store_name is None or store_name.startswith("_") or "_partial" in store_name:
            continue

        try:
            store = db[store_name]
            for record in store.iterate_records():
                val = record.value
                if not isinstance(val, dict):
                    break

                if _is_issue_record(val) and result.issues is None:
                    result.issues = store_name
                elif _is_team_record(val) and result.teams is None:
                    result.teams = store_name
                elif _is_user_record(val) and store_name not in (result.users or []):
                    if result.users is None:
                        result.users = []
                    result.users.append(store_name)
                elif _is_workflow_state_record(val) and store_name not in (
                    result.workflow_states or []
                ):
                    if result.workflow_states is None:
                        result.workflow_states = []
                    result.workflow_states.append(store_name)
                elif _is_comment_record(val) and result.comments is None:
                    result.comments = store_name
                elif _is_project_record(val) and result.projects is None:
                    result.projects = store_name
                elif _is_issue_content_record(val) and result.issue_content is None:
                    result.issue_content = store_name

                break  # Only check first record
        except Exception:
            continue

    return result
