"""Audit / Event Log (plan.md section 21).

Every significant orchestration event is appended to a per-task NDJSON file so
that months later NAO can answer "why did this file change?". Events are
machine-readable and structured; nothing is written as prose.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

from .config import redact_data
from .models.events import AuditEvent, EventType  # noqa: F401  (EventType re-exported)
from .paths import AUDIT_DIR


class AuditLogger:
    """Append-only structured event log, one NDJSON file per task (plan.md section 21 / F-15)."""

    def __init__(self, base_dir: Path = AUDIT_DIR) -> None:
        self.base_dir = Path(base_dir)
        self._lock = threading.Lock()
        self._memory: list[AuditEvent] = []
        self._memory_only = False
        self._is_degraded = False
        self._degraded_errors: list[str] = []

    @property
    def is_degraded(self) -> bool:
        return self._is_degraded

    @property
    def degraded_errors(self) -> list[str]:
        return list(self._degraded_errors)

    def _task_file(self, task_id: str) -> Path:
        return self.base_dir / task_id / "events.ndjson"

    def log(self, event: AuditEvent) -> AuditEvent:
        """Persist an event. Events without a task id are only kept in memory."""
        self._memory.append(event)
        if not event.task_id:
            return event

        path = self._task_file(event.task_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            redacted_payload = redact_data(event.to_dict())
            line = json.dumps(redacted_payload, ensure_ascii=False)
            with self._lock:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError as exc:
            # Auditing persistence failure marks audit state degraded (F-15)
            self._memory_only = True
            self._is_degraded = True
            self._degraded_errors.append(str(exc))
        return event

    def record(
        self,
        event_type: str,
        *,
        task_id: Optional[str] = None,
        job_id: Optional[str] = None,
        agent: Optional[str] = None,
        role: Optional[str] = None,
        status: Optional[str] = None,
        message: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
    ) -> AuditEvent:
        return self.log(
            AuditEvent(
                event=event_type,
                task_id=task_id,
                job_id=job_id,
                agent=agent,
                role=role,
                status=status,
                message=message,
                data=data or {},
            )
        )

    def events(self, task_id: str) -> list[dict[str, Any]]:
        path = self._task_file(task_id)
        if not path.exists():
            return [
                e.to_dict() for e in self._memory if e.task_id == task_id
            ]
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def memory_events(self) -> list[AuditEvent]:
        return list(self._memory)


class NullAuditLogger(AuditLogger):
    """Audit logger that only keeps events in memory (used by tests)."""

    def __init__(self) -> None:
        super().__init__(base_dir=Path("."))
        self._memory_only = True

    def log(self, event: AuditEvent) -> AuditEvent:
        self._memory.append(event)
        return event
