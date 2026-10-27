"""Structured test evidence (plan.md section 15).

An agent claiming "tests passed" is not evidence. The Test Manager produces
:class:`TestEvidence` by actually running a command and recording the facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class TestEvidence:
    """The factual record of a test run."""

    command: list[str]
    working_directory: str
    exit_code: Optional[int] = None
    duration_seconds: float = 0.0
    stdout: str = ""
    stderr: str = ""
    passed: Optional[int] = None
    failed: Optional[int] = None
    errors: Optional[int] = None
    skipped: Optional[int] = None
    #: "passed" | "failed" | "error" | "timeout" | "no_tests" | "cancelled" |
    #: "unknown"
    status: str = "unknown"
    kind: str = "project"  # targeted | project | regression | final
    job_id: Optional[str] = None
    agent: Optional[str] = None
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    scope: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TestEvidence":
        return cls(
            command=list(data.get("command", [])),
            working_directory=data.get("working_directory", ""),
            exit_code=data.get("exit_code"),
            duration_seconds=data.get("duration_seconds", 0.0),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            passed=data.get("passed"),
            failed=data.get("failed"),
            errors=data.get("errors"),
            skipped=data.get("skipped"),
            status=data.get("status", "unknown"),
            kind=data.get("kind", "project"),
            job_id=data.get("job_id"),
            agent=data.get("agent"),
            scope=data.get("scope"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": list(self.command),
            "working_directory": self.working_directory,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
            "status": self.status,
            "kind": self.kind,
            "job_id": self.job_id,
            "agent": self.agent,
            "scope": self.scope,
            "recorded_at": self.recorded_at.isoformat(),
        }
