"""Audit event model (plan.md section 21).

Every significant orchestration event is a structured, machine-readable
record. Events carry enough information to reconstruct *why* a change
happened months later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4


class EventType:
    """Canonical event names. Plain constants keep the ndjson readable."""

    TASK_CREATED = "task_created"
    TASK_STATE_CHANGED = "task_state_changed"
    AGENT_SELECTED = "agent_selected"
    AGENT_UNAVAILABLE = "agent_unavailable"
    JOB_STARTED = "job_started"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    PERMISSION_REQUESTED = "permission_requested"
    PERMISSION_GRANTED = "permission_granted"
    PERMISSION_DENIED = "permission_denied"
    GIT_BASELINE_CREATED = "git_baseline_created"
    WORKTREE_CREATED = "worktree_created"
    WORKTREE_REMOVED = "worktree_removed"
    FILES_CHANGED = "files_changed"
    GIT_COMMITTED = "git_committed"
    GIT_MERGED = "git_merged"
    GIT_CONFLICT = "git_conflict"
    TESTS_STARTED = "tests_started"
    TESTS_COMPLETED = "tests_completed"
    REVIEW_STARTED = "review_started"
    REVIEW_COMPLETED = "review_completed"
    FINDING_CREATED = "finding_created"
    FINDING_RESOLVED = "finding_resolved"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"
    MERGE_COMPLETED = "merge_completed"
    CONTEXT_BUILT = "context_built"
    RESOURCE_WAIT = "resource_wait"
    JOB_CANCELLED = "job_cancelled"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    ERROR = "error"


@dataclass
class AuditEvent:
    """One immutable audit record."""

    event: str
    task_id: Optional[str] = None
    job_id: Optional[str] = None
    agent: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None
    message: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: f"evt-{uuid4().hex[:12]}")
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "event": self.event,
        }
        for key in ("task_id", "job_id", "agent", "role", "status", "message"):
            value = getattr(self, key)
            if value is not None:
                record[key] = value
        if self.data:
            record["data"] = self.data
        return record
