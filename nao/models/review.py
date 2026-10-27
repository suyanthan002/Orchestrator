"""Review findings (plan.md sections 10, 11 and 16).

A finding is the structured output of an independent reviewer. Findings have
states, can be grouped into a review report, and drive the fix/re-audit loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from .enums import FindingStatus


@dataclass
class Finding:
    """A single review observation."""

    finding_id: str
    title: str
    description: str = ""
    #: info | low | medium | high | critical
    severity: str = "medium"
    status: FindingStatus = FindingStatus.OPEN
    file: Optional[str] = None
    line: Optional[int] = None
    recommendation: Optional[str] = None
    #: Job that raised the finding.
    raised_by_job: Optional[str] = None
    #: Job that fixed/resolved it.
    resolved_by_job: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @staticmethod
    def create(
        title: str,
        description: str = "",
        severity: str = "medium",
        **kwargs: Any,
    ) -> "Finding":
        return Finding(
            finding_id=f"find-{uuid4().hex[:8]}",
            title=title,
            description=description,
            severity=severity,
            **kwargs,
        )

    @property
    def blocks_completion(self) -> bool:
        return (
            self.status in (FindingStatus.OPEN, FindingStatus.REJECTED)
            and self.severity in ("high", "critical", "medium")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "status": self.status.value,
            "file": self.file,
            "line": self.line,
            "recommendation": self.recommendation,
            "raised_by_job": self.raised_by_job,
            "resolved_by_job": self.resolved_by_job,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Finding":
        return cls(
            finding_id=data["finding_id"],
            title=data.get("title", ""),
            description=data.get("description", ""),
            severity=data.get("severity", "medium"),
            status=FindingStatus(data.get("status", FindingStatus.OPEN.value)),
            file=data.get("file"),
            line=data.get("line"),
            recommendation=data.get("recommendation"),
            raised_by_job=data.get("raised_by_job"),
            resolved_by_job=data.get("resolved_by_job"),
        )
