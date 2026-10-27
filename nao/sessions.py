"""Session Manager (plan.md section 23).

Where an agent exposes a session/conversation id, NAO preserves it so a later
job can say "continue from your previous implementation session". Not every
agent supports this; the adapter reports whether it does and the session is
only reused when the agent declares ``session_continuation``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .config import redact_data
from .paths import RUNS_DIR


class SessionManager:
    def __init__(self, store_path: Optional[Path] = None) -> None:
        self.store_path = store_path or (RUNS_DIR / "sessions.json")
        self._sessions: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self.store_path.exists():
            try:
                self._sessions = json.loads(
                    self.store_path.read_text(encoding="utf-8")
                )
            except json.JSONDecodeError:
                self._sessions = {}

    def _persist(self) -> None:
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            # F-05: session state is a persisted artifact and passes through the
            # same redaction boundary as everything else.
            self.store_path.write_text(
                json.dumps(redact_data(self._sessions), indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def get(self, agent: str, task_id: Optional[str] = None) -> Optional[str]:
        """Retrieve the persisted session for a specific task and agent (plan.md section 23 / F-11).
        
        Strictly task-scoped: never falls back to an agent-wide or different task's session.
        """
        if not task_id:
            return None
        key = f"{agent}:{task_id}"
        record = self._sessions.get(key)
        return record.get("session_id") if record else None

    def set(
        self,
        agent: str,
        session_id: str,
        task_id: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> None:
        """Store a session identifier scoped strictly by agent and task_id."""
        if not task_id:
            return
        record = {"session_id": session_id, "task_id": task_id, "job_id": job_id}
        key = f"{agent}:{task_id}"
        self._sessions[key] = record
        self._persist()

    def clear(self, agent: Optional[str] = None, task_id: Optional[str] = None) -> None:
        if agent is None and task_id is None:
            self._sessions = {}
        elif task_id and agent:
            self._sessions.pop(f"{agent}:{task_id}", None)
        elif agent:
            self._sessions = {
                k: v for k, v in self._sessions.items() if not k.startswith(f"{agent}:")
            }
        elif task_id:
            self._sessions = {
                k: v for k, v in self._sessions.items() if not k.endswith(f":{task_id}")
            }
        self._persist()
