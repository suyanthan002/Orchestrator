"""Execution Manager (plan.md sections 5, 12 and 14).

Executes one job end to end:

1. acquire a resource slot (max 1 concurrent agent)
2. authorize the job's permissions through the policy engine
3. build the context pack
4. resolve the adapter (or fail as unavailable)
5. optionally continue a session
6. run the agent
7. capture the Git diff and changed files
8. classify the outcome and audit it
9. persist the job + response

The manager never invokes a shell directly for an agent; it delegates command
construction to the adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from .errors import FailureClass
from .models.agent_contract import (
    ROLE_OUTPUT_SCHEMAS,
    AgentRequest,
    AgentResponse,
)
from .models.enums import Capability, JobStatus, Permission, Role
from .models.evidence import TestEvidence
from .models.events import EventType
from .models.job import AgentJob
from .models.review import Finding
from .models.task import Task
from .policy import PolicyDecision
from .resource import ResourceUnavailable
from .safety import WorkspaceGuard

if TYPE_CHECKING:  # pragma: no cover
    from .services import NaoServices

#: Permissions a role needs by default. NAO itself runs the tests, so even the
#: tester role stays read-only.
ROLE_PERMISSIONS: dict[Role, tuple[Permission, ...]] = {
    Role.ARCHITECT: (Permission.READ, Permission.SEARCH, Permission.GIT_READ),
    Role.IMPLEMENTER: (
        Permission.READ,
        Permission.SEARCH,
        Permission.WRITE,
        Permission.GIT_READ,
    ),
    Role.REVIEWER: (Permission.READ, Permission.SEARCH, Permission.GIT_READ),
    Role.TESTER: (Permission.READ, Permission.SEARCH, Permission.GIT_READ),
    Role.DEBUGGER: (
        Permission.READ,
        Permission.SEARCH,
        Permission.WRITE,
        Permission.GIT_READ,
    ),
    Role.SECURITY_REVIEWER: (Permission.READ, Permission.SEARCH, Permission.GIT_READ),
    Role.DOCUMENTATION: (
        Permission.READ,
        Permission.SEARCH,
        Permission.WRITE,
        Permission.GIT_READ,
    ),
    Role.FINAL_VERIFIER: (Permission.READ, Permission.SEARCH, Permission.GIT_READ),
}

#: Capabilities each role requires of an agent.
ROLE_CAPABILITIES: dict[Role, tuple[Capability, ...]] = {
    Role.ARCHITECT: (Capability.PLANNING,),
    Role.IMPLEMENTER: (Capability.IMPLEMENTATION, Capability.FILE_EDIT),
    Role.REVIEWER: (Capability.CODE_REVIEW, Capability.READ_ONLY_ANALYSIS),
    Role.TESTER: (Capability.TESTING,),
    Role.DEBUGGER: (Capability.DEBUGGING,),
    Role.SECURITY_REVIEWER: (Capability.SECURITY_REVIEW,),
    Role.DOCUMENTATION: (Capability.DOCUMENTATION,),
    Role.FINAL_VERIFIER: (Capability.CODE_REVIEW,),
}


@dataclass
class JobExecution:
    """Everything NAO learned from running one job."""

    job: AgentJob
    response: Optional[AgentResponse] = None
    evidence: list[TestEvidence] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    diff: Optional[str] = None
    files_changed: list[str] = field(default_factory=list)
    blocked: bool = False
    decisions: list[PolicyDecision] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return bool(self.response and self.response.succeeded and not self.blocked)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": self.job.to_dict(),
            "response": self.response.to_dict() if self.response else None,
            "evidence": [e.to_dict() for e in self.evidence],
            "findings": [f.to_dict() for f in self.findings],
            "files_changed": list(self.files_changed),
            "blocked": self.blocked,
            "decisions": [d.to_dict() for d in self.decisions],
        }


class ExecutionManager:
    def __init__(self, services: "NaoServices", workspace_guard: Optional[WorkspaceGuard] = None) -> None:
        self.services = services
        self.workspace_guard = workspace_guard or WorkspaceGuard()

    # -- permission helpers ------------------------------------------------
    @staticmethod
    def required_permissions(role: Role, task: Task) -> tuple[Permission, ...]:
        return ROLE_PERMISSIONS.get(role, (Permission.READ,))

    def authorize(
        self, job: AgentJob, task: Task
    ) -> tuple[list[Permission], list[PolicyDecision]]:
        return self.services.policy.authorize_all(
            permissions=job.permissions,
            granted=task.granted_permissions,
            task_id=task.task_id,
            job_id=job.job_id,
        )

    # -- main entry point --------------------------------------------------
    def execute_job(
        self,
        task: Task,
        job: AgentJob,
        workspace: Optional[str] = None,
        base_ref: Optional[str] = None,
        findings: Optional[list[Finding]] = None,
        test_evidence: Optional[list[TestEvidence]] = None,
        diff: Optional[str] = None,
        previous_failures: Optional[list[dict[str, Any]]] = None,
    ) -> JobExecution:
        services = self.services
        workspace = workspace or task.workspace or task.repo_path

        execution = JobExecution(job=job)

        # 1. authorize
        effective, blocking = self.authorize(job, task)
        execution.decisions = blocking
        if blocking:
            approval_needed = [
                d for d in blocking if d.requires_approval
            ]
            for decision in approval_needed:
                services.approvals.request(
                    task_id=task.task_id,
                    subject=f"permission:{decision.permission.value}",
                    reason=decision.reason,
                    job_id=job.job_id,
                )
            execution.blocked = True
            job.status = JobStatus.BLOCKED
            services.audit.record(
                EventType.JOB_FAILED,
                task_id=task.task_id,
                job_id=job.job_id,
                agent=job.agent,
                role=job.role.value,
                status="blocked",
                message="permission policy blocked the job",
                data={"blocking": [d.to_dict() for d in blocking]},
            )
            services.artifacts.save_job(task.task_id, job, execution.to_dict())
            return execution

        # 2. adapter availability
        adapter = services.registry.adapter(job.agent)
        availability = adapter.availability()
        if not availability.available:
            job.status = JobStatus.UNAVAILABLE
            execution.response = AgentResponse(
                job_id=job.job_id,
                agent=job.agent,
                status=JobStatus.UNAVAILABLE.value,
                errors=[
                    {
                        "failure_class": FailureClass.UNAVAILABLE.value,
                        "message": availability.reason,
                        "retryable": False,
                    }
                ],
            )
            services.audit.record(
                EventType.AGENT_UNAVAILABLE,
                task_id=task.task_id,
                job_id=job.job_id,
                agent=job.agent,
                role=job.role.value,
                status="unavailable",
                message=availability.reason,
            )
            services.artifacts.save_job(task.task_id, job, execution.to_dict())
            return execution

        # 2.5. adapter permission validation (fail closed)
        perm_ok, perm_reason = adapter.validate_permissions(tuple(effective))
        if not perm_ok:
            job.status = JobStatus.FAILED
            execution.blocked = True
            execution.response = AgentResponse(
                job_id=job.job_id,
                agent=job.agent,
                status=JobStatus.FAILED.value,
                errors=[
                    {
                        "failure_class": FailureClass.UNSUPPORTED_PERMISSION.value,
                        "message": perm_reason,
                        "retryable": False,
                    }
                ],
                native_metadata={"unsupported_permission": perm_reason},
            )
            services.audit.record(
                EventType.PERMISSION_DENIED,
                task_id=task.task_id,
                job_id=job.job_id,
                agent=job.agent,
                role=job.role.value,
                status="unsupported_permission",
                message=perm_reason,
                data={"requested_permissions": [p.value for p in effective]},
            )
            services.artifacts.save_job(task.task_id, job, execution.to_dict())
            return execution

        # 2.6. policy-issued launch grant (F-01). Binding the *authorized*
        # permissions together with NAO's trusted native-argument contract here
        # means the adapter can only forward this grant; the subprocess boundary
        # re-validates the final argv against it and nothing the adapter says.
        launch_authorization = services.policy.issue_agent_grant(
            agent=job.agent,
            permissions=effective,
            task_id=task.task_id,
            job_id=job.job_id,
            role=job.role,
        )

        # 3. build the context pack
        pack = services.context.build(
            task=task,
            job=job,
            git_state=self._git_state(task, workspace),
            findings=findings,
            test_evidence=test_evidence,
            diff=diff,
            previous_failures=previous_failures,
        )
        services.audit.record(
            EventType.CONTEXT_BUILT,
            task_id=task.task_id,
            job_id=job.job_id,
            role=job.role.value,
            message=f"{len(pack.sections)} context sections",
        )

        # 4. session continuation
        session_id = None
        if adapter.spec().supports_feature("session_continuation"):
            session_id = services.sessions.get(job.agent, task.task_id)

        # 5. workspace guard
        self.workspace_guard.validate(workspace, task.repo_path)

        request = AgentRequest(
            job_id=job.job_id,
            role=job.role,
            agent=job.agent,
            task_id=task.task_id,
            prompt=job.prompt,
            context=pack.render(),
            workspace=workspace,
            permissions=tuple(effective),
            timeout_seconds=job.timeout_seconds,
            model=job.model,
            provider=job.provider,
            expected_output_schema=dict(ROLE_OUTPUT_SCHEMAS.get(job.role, {})),
            environment=job.environment,
            session_id=session_id,
            launch_authorization=launch_authorization,
        )

        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        services.audit.record(
            EventType.JOB_STARTED,
            task_id=task.task_id,
            job_id=job.job_id,
            agent=job.agent,
            role=job.role.value,
            status="running",
            message=f"attempt {job.attempt}",
            data={"effective_permissions": [p.value for p in effective]},
        )

        # 6. run under the resource gate
        try:
            with services.resources.acquire(job_id=job.job_id):
                response = adapter.execute(request)
        except ResourceUnavailable as exc:
            job.status = JobStatus.BLOCKED
            execution.blocked = True
            response = AgentResponse(
                job_id=job.job_id,
                agent=job.agent,
                status=JobStatus.BLOCKED.value,
                errors=[exc.error.to_dict()],
            )

        execution.response = response
        job.finished_at = datetime.now(timezone.utc)
        if response.succeeded:
            job.status = JobStatus.SUCCEEDED
        elif response.status == JobStatus.BLOCKED.value:
            job.status = JobStatus.BLOCKED
        elif response.status == JobStatus.CANCELLED.value:
            job.status = JobStatus.CANCELLED
        else:
            job.status = JobStatus.FAILED

        # 7. session continuity
        if response.session_id:
            services.sessions.set(
                job.agent, response.session_id, task.task_id, job.job_id
            )

        # 8. capture Git evidence for mutating roles
        if job.role in (Role.IMPLEMENTER, Role.DEBUGGER, Role.DOCUMENTATION):
            execution.diff, execution.files_changed = self._capture_git(
                task, job, workspace, base_ref
            )
        elif response.files_changed:
            execution.files_changed = response.files_changed

        # 9. audit + persist
        event = EventType.JOB_COMPLETED if job.status is JobStatus.SUCCEEDED else EventType.JOB_FAILED
        services.audit.record(
            event,
            task_id=task.task_id,
            job_id=job.job_id,
            agent=job.agent,
            role=job.role.value,
            status=job.status.value,
            message=job.status.value,
            data={
                "error_classes": [
                    e.get("failure_class") for e in response.errors
                ],
                "files_changed": execution.files_changed,
                "duration_seconds": response.duration_seconds,
            },
        )
        services.artifacts.save_job(task.task_id, job, execution.to_dict())
        if execution.diff:
            services.artifacts.save_diff(task.task_id, job.job_id, execution.diff)
        return execution

    # -- internals ---------------------------------------------------------
    def _git_state(
        self, task: Task, workspace: Optional[str]
    ) -> Optional[dict[str, Any]]:
        """Snapshot Git state for the context pack. Never fails a job."""
        if not task.repo_path:
            return None
        try:
            git = self.services.git_factory(task.repo_path)
            state = git.state()
        except Exception:  # pragma: no cover - defensive
            return None
        data = state.to_dict()
        data["worktree"] = workspace
        return data

    def _capture_git(
        self,
        task: Task,
        job: AgentJob,
        workspace: Optional[str],
        base_ref: Optional[str],
    ) -> tuple[Optional[str], list[str]]:
        if not task.repo_path or not workspace:
            return None, []
        try:
            git = self.services.git_factory(task.repo_path)
            files = git.changed_files(base_ref, cwd=workspace)
            diff = git.diff(base_ref, cwd=workspace) if files else ""
        except Exception:  # pragma: no cover - defensive
            return None, []
        if files:
            self.services.audit.record(
                EventType.FILES_CHANGED,
                task_id=task.task_id,
                job_id=job.job_id,
                agent=job.agent,
                status="changed",
                message=f"{len(files)} file(s) changed",
                data={"files": files[:100]},
            )
        return diff or None, files

    def cancel(self, job_id: str) -> bool:
        return self.services.runner.cancel(job_id)
