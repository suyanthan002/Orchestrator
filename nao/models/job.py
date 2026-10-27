"""The AgentJob model (plan.md section 5).

A job is one execution attempt by one agent for one role. Tasks produce jobs;
jobs produce results. Keeping these separate is what makes the system
auditable and retryable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .enums import JobStatus, Permission, Role


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


@dataclass
class AgentJob:
    """A single, auditable agent execution attempt."""

    job_id: str
    task_id: str
    agent: str
    role: Role
    #: Isolated workspace the agent must stay inside.
    workspace: Optional[str] = None
    prompt: str = ""
    context: str = ""
    permissions: tuple[Permission, ...] = ()
    timeout_seconds: int = 300
    model: Optional[str] = None
    provider: Optional[str] = None
    environment: dict[str, str] = field(default_factory=dict)
    expected_output_schema: dict[str, Any] = field(default_factory=dict)
    input_artifacts: list[str] = field(default_factory=list)
    #: 1-based attempt counter for this role.
    attempt: int = 1
    #: The job this one retries/fixes, if any.
    parent_job: Optional[str] = None
    session_id: Optional[str] = None
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "task_id": self.task_id,
            "agent": self.agent,
            "role": self.role.value,
            "workspace": self.workspace,
            "prompt": self.prompt,
            "context": self.context,
            "permissions": [p.value for p in self.permissions],
            "timeout_seconds": self.timeout_seconds,
            "model": self.model,
            "provider": self.provider,
            "environment": dict(self.environment),
            "expected_output_schema": dict(self.expected_output_schema),
            "input_artifacts": list(self.input_artifacts),
            "attempt": self.attempt,
            "parent_job": self.parent_job,
            "session_id": self.session_id,
            "status": self.status.value,
            "created_at": _iso(self.created_at),
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
        }
