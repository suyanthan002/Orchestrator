"""Artifact Manager (plan.md sections 5 and 21).

All run state is persisted in a predictable, structured layout::

    runs/<task_id>/task.json
    runs/<task_id>/jobs/<job_id>.json
    runs/<task_id>/diffs/<job_id>.diff
    runs/<task_id>/tests/<job_id>.json
    runs/<task_id>/contexts/<job_id>.txt
    runs/<task_id>/approvals.json

Raw agent output is preserved for auditing; nothing useful is discarded.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .config import redact_data, redact_text
from .models.evidence import TestEvidence
from .models.job import AgentJob
from .models.review import Finding
from .models.task import Task
from .paths import RUNS_DIR


class ArtifactManager:
    def __init__(self, runs_dir: Path = RUNS_DIR) -> None:
        self.runs_dir = Path(runs_dir)

    # -- paths -------------------------------------------------------------
    def task_dir(self, task_id: str) -> Path:
        return self.runs_dir / task_id

    def _write_json(self, path: Path, data: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        sanitized = redact_data(data)
        path.write_text(
            json.dumps(sanitized, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def _read_json(self, path: Path) -> Optional[Any]:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    # -- task --------------------------------------------------------------
    def save_task(self, task: Task) -> Path:
        return self._write_json(self.task_dir(task.task_id) / "task.json", task.to_dict())

    def load_task(self, task_id: str) -> Optional[Task]:
        data = self._read_json(self.task_dir(task_id) / "task.json")
        return Task.from_dict(data) if data else None

    def list_tasks(self) -> list[str]:
        if not self.runs_dir.exists():
            return []
        return sorted(
            p.name
            for p in self.runs_dir.iterdir()
            if p.is_dir() and (p / "task.json").exists()
        )

    # -- jobs --------------------------------------------------------------
    def save_job(
        self,
        task_id: str,
        job: AgentJob,
        response: Optional[dict[str, Any]] = None,
    ) -> Path:
        payload = {"job": job.to_dict()}
        if response is not None:
            payload["response"] = response
        return self._write_json(
            self.task_dir(task_id) / "jobs" / f"{job.job_id}.json", payload
        )

    def load_jobs(self, task_id: str) -> list[dict[str, Any]]:
        jobs_dir = self.task_dir(task_id) / "jobs"
        if not jobs_dir.exists():
            return []
        records = []
        for path in sorted(jobs_dir.glob("*.json")):
            data = self._read_json(path)
            if data:
                records.append(data)
        return records

    def save_context(self, task_id: str, job_id: str, text: str) -> Path:
        path = self.task_dir(task_id) / "contexts" / f"{job_id}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(redact_text(text), encoding="utf-8")
        return path

    # -- diffs -------------------------------------------------------------
    def save_diff(self, task_id: str, job_id: str, diff: str) -> Path:
        path = self.task_dir(task_id) / "diffs" / f"{job_id}.diff"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(redact_text(diff), encoding="utf-8")
        return path

    def load_diff(self, task_id: str, job_id: str) -> Optional[str]:
        path = self.task_dir(task_id) / "diffs" / f"{job_id}.diff"
        return path.read_text(encoding="utf-8") if path.exists() else None

    # -- tests -------------------------------------------------------------
    def save_test(self, task_id: str, evidence: TestEvidence) -> Path:
        name = f"{evidence.job_id or 'nao'}-{evidence.kind}-{int(evidence.recorded_at.timestamp())}.json"
        return self._write_json(
            self.task_dir(task_id) / "tests" / name, evidence.to_dict()
        )

    def load_tests(self, task_id: str) -> list[dict[str, Any]]:
        tests_dir = self.task_dir(task_id) / "tests"
        if not tests_dir.exists():
            return []
        return [
            data
            for data in (self._read_json(p) for p in sorted(tests_dir.glob("*.json")))
            if data
        ]

    # -- findings ----------------------------------------------------------
    def save_findings(self, task_id: str, findings: list[Finding]) -> Path:
        return self._write_json(
            self.task_dir(task_id) / "findings.json",
            [f.to_dict() for f in findings],
        )

    def load_findings(self, task_id: str) -> list[Finding]:
        data = self._read_json(self.task_dir(task_id) / "findings.json") or []
        return [Finding.from_dict(item) for item in data]

    # -- approvals ---------------------------------------------------------
    def save_approvals(self, task_id: str, approvals: list[dict[str, Any]]) -> Path:
        return self._write_json(self.task_dir(task_id) / "approvals.json", approvals)

    def load_approvals(self, task_id: str) -> list[dict[str, Any]]:
        return self._read_json(self.task_dir(task_id) / "approvals.json") or []

    # -- run status --------------------------------------------------------
    def run_status(self, task_id: str) -> dict[str, Any]:
        task = self.load_task(task_id)
        jobs = self.load_jobs(task_id)
        tests = self.load_tests(task_id)
        return {
            "task_id": task_id,
            "exists": task is not None,
            "state": task.state.value if task else None,
            "recipe": task.recipe if task else None,
            "title": task.title if task else None,
            "job_count": len(jobs),
            "jobs": [
                {
                    "job_id": j["job"]["job_id"],
                    "agent": j["job"]["agent"],
                    "role": j["job"]["role"],
                    "status": j["job"]["status"],
                    "attempt": j["job"]["attempt"],
                }
                for j in jobs
            ],
            "test_count": len(tests),
            "tests_passed": sum(1 for t in tests if t.get("status") == "passed"),
            "tests_failed": sum(1 for t in tests if t.get("status") == "failed"),
        }
