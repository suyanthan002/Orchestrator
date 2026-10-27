"""Task Manager (plan.md sections 4 and 8).

Owns task creation, persistence and lifecycle. Running a task delegates to the
Workflow Engine; the Task Manager never executes an agent itself.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from .models.enums import Permission, TaskState
from .models.events import EventType
from .models.task import Task
from .services import NaoServices
from .workflow.engine import WorkflowEngine, WorkflowResult
from .workflow.recipes import RECIPES, get_recipe


class TaskManager:
    def __init__(self, services: NaoServices) -> None:
        self.services = services
        self.engine = WorkflowEngine(services)

    # -- creation ----------------------------------------------------------
    def create_task(
        self,
        description: str,
        title: str = "",
        recipe: str = "feature_implementation",
        acceptance_criteria: Optional[list[str]] = None,
        repo_path: Optional[str] = None,
        granted_permissions: Optional[list[str]] = None,
        routing_overrides: Optional[dict[str, str]] = None,
        max_fix_attempts: int = 2,
        max_jobs_per_role: int = 1,
        task_type: str = "general",
        metadata: Optional[dict[str, Any]] = None,
    ) -> Task:
        if recipe not in RECIPES:
            raise ValueError(f"unknown recipe '{recipe}'")
        definition = get_recipe(recipe)

        if granted_permissions is None:
            granted_permissions = list(definition.default_permissions)

        task = Task(
            task_id=f"task-{uuid4().hex[:8]}",
            title=title or description.splitlines()[0][:80] if description else recipe,
            description=description,
            acceptance_criteria=list(acceptance_criteria or []),
            task_type=task_type,
            repo_path=str(repo_path) if repo_path else self.services.config.default_repo_path,
            granted_permissions=[Permission(p) for p in granted_permissions],
            recipe=recipe,
            routing_overrides=dict(routing_overrides or {}),
            max_fix_attempts=max_fix_attempts,
            max_jobs_per_role=max_jobs_per_role,
            metadata=dict(metadata or {}),
        )
        task.validate()
        self.services.artifacts.save_task(task)
        self.services.audit.record(
            EventType.TASK_CREATED,
            task_id=task.task_id,
            status=task.state.value,
            message=task.title,
            data={
                "recipe": recipe,
                "repo_path": task.repo_path,
                "permissions": [p.value for p in task.granted_permissions],
                "acceptance_criteria": task.acceptance_criteria,
            },
        )
        return task

    # -- lifecycle ---------------------------------------------------------
    def get(self, task_id: str) -> Optional[Task]:
        return self.services.artifacts.load_task(task_id)

    def list_tasks(self) -> list[str]:
        return self.services.artifacts.list_tasks()

    def run(self, task: Task) -> WorkflowResult:
        reloaded = self.get(task.task_id)
        if reloaded is not None:
            # Resume from persisted state, preserving in-memory edits.
            reloaded.description = task.description
            reloaded.acceptance_criteria = task.acceptance_criteria
            reloaded.granted_permissions = task.granted_permissions
            reloaded.routing_overrides = task.routing_overrides
            reloaded.metadata = {**reloaded.metadata, **task.metadata}
            task = reloaded
        return self._run_and_flag_audit(task)

    def run_task(self, task_id: str) -> WorkflowResult:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"unknown task '{task_id}'")
        return self._run_and_flag_audit(task)

    def _run_and_flag_audit(self, task: Task) -> WorkflowResult:
        """Run the workflow and surface any audit-degradation (F-15)."""
        result = self.engine.run(task)
        # A completion can never be advertised as fully audited if durable
        # audit storage failed at any point during the run.
        result.audit_degraded = self.services.audit.is_degraded
        return result

    def status(self, task_id: str) -> dict[str, Any]:
        return self.services.artifacts.run_status(task_id)

    def cancel(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        # Only cancel jobs belonging to this task (plan.md section 18 / F-13)
        self.services.runner.cancel_task(task_id)
        if task.state not in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
            self.engine.transition(task, TaskState.CANCELLED, "cancelled by operator")
        return True

    # -- approvals ---------------------------------------------------------
    def approve(
        self, task_id: str, approval_id: str, approver: str, note: Optional[str] = None
    ) -> dict:
        request = self.services.approvals.grant(task_id, approval_id, approver, note)
        return request.to_dict()

    def reject(
        self, task_id: str, approval_id: str, approver: str, note: Optional[str] = None
    ) -> dict:
        request = self.services.approvals.deny(task_id, approval_id, approver, note)
        return request.to_dict()

    def pending_approvals(self, task_id: str) -> list[dict]:
        return [a.to_dict() for a in self.services.approvals.pending(task_id)]

    # -- reporting ---------------------------------------------------------
    def findings(self, task_id: str) -> list[dict]:
        return [f.to_dict() for f in self.services.artifacts.load_findings(task_id)]

    def evidence(self, task_id: str) -> list[dict]:
        return self.services.artifacts.load_tests(task_id)

    def audit_events(self, task_id: str) -> list[dict]:
        return self.services.audit.events(task_id)

    def jobs(self, task_id: str) -> list[dict]:
        return self.services.artifacts.load_jobs(task_id)
