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
    labels: list[str] | None = None  # Issue labels (team + workspace)
    initiatives: str | None = None
    project_statuses: str | None = None
    cycles: str | None = None
    documents: str | None = None
    document_content: str | None = None
    milestones: str | None = None
    project_updates: str | None = None


def _is_issue_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like an issue."""
    required = {"number", "teamId", "stateId", "title"}
    return required.issubset(record.keys())


def _is_user_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like a user."""
    required = {"name", "displayName", "email"}
    return required.issubset(record.keys())


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
    if not {"name", "type", "color", "teamId"}.issubset(record.keys()):
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
    required = {"name", "teamIds", "slugId", "statusId", "memberIds"}
    return required.issubset(record.keys())


def _is_issue_content_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like issue content (Y.js encoded description)."""
    required = {"issueId", "contentState"}
    return required.issubset(record.keys())


def _is_label_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like a label."""
    required = {"name", "color", "isGroup"}
    return required.issubset(record.keys())


def _is_initiative_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like an initiative."""
    required = {"name", "ownerId", "slugId", "frequencyResolution"}
    return required.issubset(record.keys())


def _is_project_status_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like a project status."""
    if not {"name", "color", "position", "type", "indefinite"}.issubset(record.keys()):
        return False
    # Must not have teamId (that's workflow state)
    return "teamId" not in record


def _is_cycle_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like a cycle."""
    required = {"number", "teamId", "startsAt", "endsAt"}
    return required.issubset(record.keys())


def _is_document_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like a document."""
    required = {"title", "slugId", "projectId", "sortOrder"}
    has_required = required.issubset(record.keys())
    # Must not be an issue
    not_issue = "number" not in record and "stateId" not in record
    return has_required and not_issue


def _is_document_content_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like document content."""
    required = {"documentContentId", "contentData"}
    return required.issubset(record.keys())


def _is_milestone_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like a project milestone."""
    required = {"name", "projectId", "sortOrder"}
    has_required = required.issubset(record.keys())
    # May have targetDate, currentProgress
    has_progress = "currentProgress" in record or "targetDate" in record
    return has_required and has_progress


def _is_project_update_record(record: dict[str, Any]) -> bool:
    """Check if a record looks like a project update."""
    # Has body and either projectId or health field
    has_body = "body" in record
    has_project = "projectId" in record or "health" in record
    # Must not be a comment
    not_comment = "issueId" not in record
    return has_body and has_project and not_comment


def detect_stores(db: ccl_chromium_indexeddb.WrappedDatabase) -> DetectedStores:
    """
    Detect object stores by sampling their first record.

    Args:
        db: The wrapped IndexedDB database to scan.

    Returns:
        DetectedStores with detected store names for each entity type.
    """
    result = DetectedStores(users=[], workflow_states=[], labels=[])

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
                elif _is_label_record(val) and store_name not in (result.labels or []):
                    if result.labels is None:
                        result.labels = []
                    result.labels.append(store_name)
                elif _is_initiative_record(val) and result.initiatives is None:
                    result.initiatives = store_name
                elif _is_project_status_record(val) and result.project_statuses is None:
                    result.project_statuses = store_name
                elif _is_cycle_record(val) and result.cycles is None:
                    result.cycles = store_name
                elif _is_document_record(val) and result.documents is None:
                    result.documents = store_name
                elif _is_document_content_record(val) and result.document_content is None:
                    result.document_content = store_name
                elif _is_milestone_record(val) and result.milestones is None:
                    result.milestones = store_name
                elif _is_project_update_record(val) and result.project_updates is None:
                    result.project_updates = store_name

                break  # Only check first record
        except Exception:
            continue

    return result
