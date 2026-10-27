"""Human Approval Manager (plan.md sections 9, 20 and 24).

Approval is *state*, not a printed message. A request is created, persisted,
and can only become GRANTED or DENIED by an explicit human action recorded with
an approver identity. NAO never pretends approval happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from .artifacts import ArtifactManager
from .audit import AuditLogger
from .models.enums import ApprovalState
from .models.events import EventType


@dataclass
class ApprovalRequest:
    approval_id: str
    task_id: str
    #: What the approval is for, e.g. a permission name or "merge".
    subject: str
    reason: str = ""
    job_id: Optional[str] = None
    state: ApprovalState = ApprovalState.PENDING
    requester: str = "nao"
    approver: Optional[str] = None
    note: Optional[str] = None
    requested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "task_id": self.task_id,
            "subject": self.subject,
            "reason": self.reason,
            "job_id": self.job_id,
            "state": self.state.value,
            "requester": self.requester,
            "approver": self.approver,
            "note": self.note,
            "requested_at": self.requested_at.isoformat(),
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalRequest":
        return cls(
            approval_id=data["approval_id"],
            task_id=data["task_id"],
            subject=data["subject"],
            reason=data.get("reason", ""),
            job_id=data.get("job_id"),
            state=ApprovalState(data.get("state", ApprovalState.PENDING.value)),
            requester=data.get("requester", "nao"),
            approver=data.get("approver"),
            note=data.get("note"),
            requested_at=_parse(data.get("requested_at")),
            decided_at=_parse(data.get("decided_at")),
        )


def _parse(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)


class ApprovalManager:
    """File-backed approval state."""

    def __init__(self, artifacts: ArtifactManager, audit: AuditLogger) -> None:
        self.artifacts = artifacts
        self.audit = audit

    def _load(self, task_id: str) -> list[ApprovalRequest]:
        return [
            ApprovalRequest.from_dict(item)
            for item in self.artifacts.load_approvals(task_id)
        ]

    def _save(self, task_id: str, approvals: list[ApprovalRequest]) -> None:
        self.artifacts.save_approvals(task_id, [a.to_dict() for a in approvals])

    def request(
        self,
        task_id: str,
        subject: str,
        reason: str = "",
        job_id: Optional[str] = None,
        requester: str = "nao",
    ) -> ApprovalRequest:
        approvals = self._load(task_id)
        # Re-use an existing pending/past request for the same subject+job.
        for existing in approvals:
            if existing.subject == subject and existing.job_id == job_id:
                return existing
        request = ApprovalRequest(
            approval_id=f"appr-{uuid4().hex[:8]}",
            task_id=task_id,
            subject=subject,
            reason=reason,
            job_id=job_id,
            requester=requester,
        )
        approvals.append(request)
        self._save(task_id, approvals)
        self.audit.record(
            EventType.APPROVAL_REQUESTED,
            task_id=task_id,
            job_id=job_id,
            status=ApprovalState.PENDING.value,
            message=reason or subject,
            data={"approval_id": request.approval_id, "subject": subject},
        )
        return request

    def pending(self, task_id: str) -> list[ApprovalRequest]:
        return [
            a for a in self._load(task_id) if a.state is ApprovalState.PENDING
        ]

    def all(self, task_id: str) -> list[ApprovalRequest]:
        return self._load(task_id)

    def _decide(
        self,
        task_id: str,
        approval_id: str,
        state: ApprovalState,
        approver: str,
        note: Optional[str],
    ) -> ApprovalRequest:
        approvals = self._load(task_id)
        for request in approvals:
            if request.approval_id == approval_id:
                if request.state is not ApprovalState.PENDING:
                    raise ValueError(
                        f"approval {approval_id} already {request.state.value}"
                    )
                request.state = state
                request.approver = approver
                request.note = note
                request.decided_at = datetime.now(timezone.utc)
                self._save(task_id, approvals)
                self.audit.record(
                    EventType.APPROVAL_GRANTED
                    if state is ApprovalState.GRANTED
                    else EventType.APPROVAL_DENIED,
                    task_id=task_id,
                    job_id=request.job_id,
                    status=state.value,
                    message=request.subject,
                    data={"approval_id": approval_id, "approver": approver},
                )
                return request
        raise KeyError(f"unknown approval {approval_id} for task {task_id}")

    def grant(
        self, task_id: str, approval_id: str, approver: str, note: Optional[str] = None
    ) -> ApprovalRequest:
        return self._decide(
            task_id, approval_id, ApprovalState.GRANTED, approver, note
        )

    def deny(
        self, task_id: str, approval_id: str, approver: str, note: Optional[str] = None
    ) -> ApprovalRequest:
        return self._decide(
            task_id, approval_id, ApprovalState.DENIED, approver, note
        )

    def is_granted(
        self, task_id: str, subject: str, job_id: Optional[str] = None
    ) -> bool:
        for request in self._load(task_id):
            if request.subject != subject:
                continue
            if job_id is not None and request.job_id not in (None, job_id):
                continue
            if request.state is ApprovalState.GRANTED:
                return True
        return False

    def is_denied(self, task_id: str, subject: str) -> bool:
        return any(
            a.subject == subject and a.state is ApprovalState.DENIED
            for a in self._load(task_id)
        )
