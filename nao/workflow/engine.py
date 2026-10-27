"""Workflow Engine (plan.md sections 4, 10, 11 and 17).

This is the heart of NAO. It moves a task through explicit states, executes the
steps of a recipe, and owns the fix/re-audit loop:

    IMPLEMENT -> TEST -> INDEPENDENT REVIEW -> FINDINGS -> FIX -> TEST AGAIN
    -> REVIEW AGAIN -> VALIDATE

Infinite loops are prevented by explicit attempt limits; hitting a limit
escalates to ``NEEDS_HUMAN`` rather than silently retrying forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from ..approval import ApprovalManager
from ..errors import WorkflowError
from ..execution import (
    ROLE_CAPABILITIES,
    ROLE_PERMISSIONS,
    ExecutionManager,
    JobExecution,
)
from ..models.enums import (
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    Capability,
    JobStatus,
    Permission,
    Role,
    TaskState,
)
from ..models.evidence import TestEvidence
from ..models.events import EventType
from ..models.job import AgentJob
from ..models.review import Finding
from ..models.task import Task
from ..router import RoutingError
from ..safety import WorkspaceGuard
from .recipes import Recipe, RecipeStep, get_recipe

#: The only evidence kinds a verifier may cite (F-03). References point at NAO
#: artifacts (test runs, diffs, audit records, command results), never prose.
_EVIDENCE_KINDS = frozenset(
    {"test", "diff", "artifact", "command", "audit", "file"}
)


@dataclass
class WorkflowResult:
    task_id: str
    state: TaskState
    success: bool
    message: str
    jobs: list[AgentJob] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    evidence: list[TestEvidence] = field(default_factory=list)
    worktree_branch: Optional[str] = None
    #: True when durable audit persistence failed (F-15). The completion is
    #: never advertised as fully audited while this is set.
    audit_degraded: bool = False

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "state": self.state.value,
            "success": self.success,
            "message": self.message,
            "jobs": [j.to_dict() for j in self.jobs],
            "findings": [f.to_dict() for f in self.findings],
            "evidence": [e.to_dict() for e in self.evidence],
            "worktree_branch": self.worktree_branch,
            "audit_degraded": self.audit_degraded,
        }


class WorkflowEngine:
    def __init__(
        self, services, workspace_guard: Optional[WorkspaceGuard] = None
    ) -> None:
        self.services = services
        self.workspace_guard = workspace_guard or WorkspaceGuard()
        self.execution = ExecutionManager(services, self.workspace_guard)

    # -- state machine -----------------------------------------------------
    def transition(self, task: Task, new_state: TaskState, reason: str = "") -> None:
        if new_state == task.state:
            return
        allowed = VALID_TRANSITIONS.get(task.state, frozenset())
        if new_state not in allowed:
            raise WorkflowError(
                f"invalid transition {task.state.value} -> {new_state.value}"
            )
        previous = task.state
        task.state = new_state
        task.updated_at = datetime.now(timezone.utc)
        self.services.artifacts.save_task(task)
        self.services.audit.record(
            EventType.TASK_STATE_CHANGED,
            task_id=task.task_id,
            status=new_state.value,
            message=reason or f"{previous.value} -> {new_state.value}",
            data={"from": previous.value, "to": new_state.value},
        )

    # -- entry point -------------------------------------------------------
    def run(self, task: Task) -> WorkflowResult:
        self.services.artifacts.save_task(task)
        try:
            recipe = get_recipe(task.recipe)
        except KeyError as exc:
            self.transition(task, TaskState.FAILED, str(exc))
            return WorkflowResult(task.task_id, task.state, False, str(exc))

        # Resume from persisted progress so an approval gate or a restart does
        # not repeat already-finished work.
        progress: dict = task.metadata.get("workflow_progress", {})
        result = WorkflowResult(task.task_id, task.state, False, "")
        steps = recipe.steps
        index = int(progress.get("index", 0))
        fix_attempts = int(progress.get("fix_attempts", 0))
        analysis = task.metadata.get("analysis", "")
        plan = task.metadata.get("plan", "")
        diff: Optional[str] = progress.get("diff")
        findings: list[Finding] = self.services.artifacts.load_findings(task.task_id)
        evidence: list[TestEvidence] = [
            TestEvidence.from_dict(item)
            for item in self.services.artifacts.load_tests(task.task_id)
        ]
        tests_ok = bool(progress.get("tests_ok", True))
        failures: list[dict] = list(progress.get("failures", []))
        last_implement_index = int(progress.get("last_implement_index", 0))
        worktree_path: Optional[str] = progress.get("worktree_path") or task.workspace
        worktree_branch: Optional[str] = progress.get("worktree_branch")
        base_ref: Optional[str] = progress.get("base_ref")
        implementation_done = bool(progress.get("implementation_done", False))
        if worktree_branch:
            result.worktree_branch = worktree_branch

        while index < len(steps):
            step = steps[index]
            self._save_progress(
                task,
                index=index,
                fix_attempts=fix_attempts,
                tests_ok=tests_ok,
                failures=failures,
                last_implement_index=last_implement_index,
                worktree_path=worktree_path,
                worktree_branch=worktree_branch,
                base_ref=base_ref,
                implementation_done=implementation_done,
                diff=diff,
            )

            # -- analyze ------------------------------------------------
            if step.kind == "analyze":
                role = self._role(step.role, Role.ARCHITECT)
                self.transition(task, TaskState.ANALYZING, step.description)
                outcome = self._run_role_job(
                    task, role, extras={"analysis": analysis},
                    findings=findings, evidence=evidence,
                )
                result.jobs.append(outcome.job)
                if not outcome.succeeded:
                    return self._fail(task, result, outcome, failures, "analysis")
                analysis = self._response_text(outcome)
                task.metadata["analysis"] = analysis
                index += 1
                continue

            # -- plan ---------------------------------------------------
            if step.kind == "plan":
                role = self._role(step.role, Role.ARCHITECT)
                outcome = self._run_role_job(
                    task, role, extras={"analysis": analysis},
                    findings=findings, evidence=evidence,
                )
                result.jobs.append(outcome.job)
                if not outcome.succeeded:
                    return self._fail(task, result, outcome, failures, "planning")
                plan = self._response_text(outcome)
                task.metadata["plan"] = plan
                self.transition(task, TaskState.PLANNED, "plan produced")
                index += 1
                continue

            # -- approval gate ------------------------------------------
            if step.kind == "approve":
                if step.approval == "merge":
                    subject = "permission:git_merge"
                elif step.approval == "commit":
                    subject = "permission:git_commit"
                else:
                    subject = f"approval:{step.approval}"
                if self.services.approvals.is_denied(task.task_id, subject):
                    self.transition(task, TaskState.FAILED, f"{subject} denied")
                    result.state = task.state
                    result.message = f"{subject} denied by human"
                    return result
                if not self.services.approvals.is_granted(task.task_id, subject):
                    self.services.approvals.request(
                        task_id=task.task_id,
                        subject=subject,
                        reason=f"Human approval required: {step.approval}",
                    )
                    if task.state is not TaskState.WAITING_FOR_APPROVAL:
                        self.transition(
                            task,
                            TaskState.WAITING_FOR_APPROVAL,
                            f"waiting for {subject}",
                        )
                    result.state = task.state
                    result.message = f"waiting for human approval ({subject})"
                    self.services.artifacts.save_task(task)
                    return result
                if task.state is TaskState.WAITING_FOR_APPROVAL:
                    self.transition(task, TaskState.READY, "approval granted")
                index += 1
                continue

            # -- worktree -----------------------------------------------
            if step.kind == "worktree":
                if not task.repo_path:
                    return self._block(
                        task, result, "no repository configured for the task"
                    )
                git = self.services.git_factory(task.repo_path)
                baseline = git.create_baseline(task.task_id)
                base_ref = baseline.head
                if baseline.dirty:
                    self.services.audit.record(
                        EventType.ERROR,
                        task_id=task.task_id,
                        status="dirty_baseline",
                        message=(
                            "repository has uncommitted changes; using an "
                            "isolated worktree from HEAD"
                        ),
                    )
                worktree = git.create_worktree(task.task_id, base_ref)
                worktree_path = worktree.path
                worktree_branch = worktree.branch
                task.workspace = worktree_path
                task.metadata["baseline"] = baseline.to_dict()
                result.worktree_branch = worktree_branch
                self.transition(task, TaskState.READY, "worktree created")
                index += 1
                continue

            # -- implement ----------------------------------------------
            if step.kind == "implement":
                last_implement_index = index
                self.transition(task, TaskState.IMPLEMENTING, step.description)
                role = self._role(step.role, Role.IMPLEMENTER)
                extras = {}
                if findings:
                    extras["findings"] = self._render_findings(findings)
                outcome = self._run_role_job(
                    task, role, workspace=worktree_path, base_ref=base_ref,
                    extras=extras, findings=findings, evidence=evidence,
                    diff=diff, previous_failures=failures,
                )
                result.jobs.append(outcome.job)
                if outcome.diff:
                    diff = outcome.diff
                if not outcome.succeeded:
                    return self._fail(task, result, outcome, failures, "implementation")
                if outcome.job and outcome.job.agent and outcome.job.agent != "none":
                    task.metadata["implementation_agent"] = outcome.job.agent

                # A fix job resolves the findings it was asked to address; the
                # following review re-evaluates the code and may raise new ones.
                if fix_attempts > 0 and findings:
                    open_ids = [
                        f.finding_id for f in findings if f.blocks_completion
                    ]
                    if open_ids:
                        self.services.review.resolve(
                            findings,
                            open_ids,
                            outcome.job.job_id,
                            task_id=task.task_id,
                        )
                        self.services.review.persist(task.task_id, findings)
                implementation_done = True
                self.transition(task, TaskState.IMPLEMENTED, "implementation complete")
                index += 1
                continue

            # -- test ---------------------------------------------------
            if step.kind == "test":
                # A test run that follows a fix is a re-verification, not the
                # initial test (F-16: REVERIFYING is actually entered).
                test_state = (
                    TaskState.REVERIFYING if fix_attempts > 0 else TaskState.TESTING
                )
                self.transition(task, test_state, step.description)
                git_cwd = worktree_path or task.repo_path
                plan_obj = self.services.tests.plan_from_task(
                    repo_path=git_cwd or ".",
                    task_metadata=task.metadata,
                    kind="targeted" if fix_attempts == 0 else "regression",
                )
                # F-04: a workflow test step is a verification *gate*; it always
                # requires trustworthy evidence of executed tests.
                evidence_item = self.services.tests.run(
                    plan_obj, task_id=task.task_id, gate=True
                )
                evidence.append(evidence_item)
                result.evidence.append(evidence_item)
                tests_ok = evidence_item.ok
                if not tests_ok:
                    failures.append(
                        {
                            "failure_class": "test_failure",
                            "message": f"tests {evidence_item.status} "
                            f"({evidence_item.failed} failed)",
                        }
                    )
                    # Test gate (F-04): If this is a mutating recipe and the next step is NOT review,
                    # e.g., regression testing in bug_fix, test failure MUST trigger fix loop or escalate.
                    next_kind = steps[index + 1].kind if index + 1 < len(steps) else None
                    if recipe.mutating and next_kind not in ("review", "security_review"):
                        if fix_attempts >= task.max_fix_attempts:
                            self.transition(
                                task, TaskState.NEEDS_HUMAN,
                                f"test failure gate: fix limit reached ({evidence_item.status})",
                            )
                            result.state = task.state
                            result.findings = findings
                            result.message = f"regression tests failed after {fix_attempts} fix attempt(s)"
                            return result
                        fix_attempts += 1
                        self.transition(
                            task, TaskState.FIXING,
                            f"test failed ({evidence_item.status}); fix attempt {fix_attempts}/{task.max_fix_attempts}",
                        )
                        index = last_implement_index
                        continue
                index += 1
                continue

            # -- review (independent) -----------------------------------
            if step.kind in ("review", "security_review"):
                role = self._role(
                    step.role,
                    Role.REVIEWER if step.kind == "review" else Role.SECURITY_REVIEWER,
                )
                self.transition(task, TaskState.REVIEWING, step.description)
                # Independent reviewer (F-12): Exclude the implementing agent
                implementer = task.metadata.get("implementation_agent")
                review_exclude = {implementer} if implementer else set()
                outcome = self._run_role_job(
                    task, role,
                    workspace=worktree_path,
                    base_ref=base_ref,
                    extras={"review_diff": (diff or "")[:20000]},
                    findings=findings,
                    evidence=evidence,
                    diff=diff,
                    initial_exclude=review_exclude,
                )
                result.jobs.append(outcome.job)
                if not outcome.succeeded:
                    # A reviewer that cannot run is not evidence of quality.
                    return self._fail(task, result, outcome, failures, "review")

                # F-03: enforce the reviewer's declared output schema. Missing,
                # malformed or wrong-typed output is a verification failure and
                # must NEVER be coerced into "no findings".
                parsed = self.services.review.parse(
                    outcome.response,
                    role=role,
                    task_id=task.task_id,
                    job_id=outcome.job.job_id,
                )
                if not parsed.valid:
                    self.services.audit.record(
                        EventType.ERROR,
                        task_id=task.task_id,
                        job_id=outcome.job.job_id,
                        role=role.value,
                        status="invalid_review_output",
                        message=parsed.reason,
                    )
                    self.transition(
                        task,
                        TaskState.NEEDS_HUMAN,
                        f"reviewer output invalid: {parsed.reason}",
                    )
                    result.state = task.state
                    result.findings = findings
                    result.message = f"review failed: {parsed.reason}"
                    return result

                findings.extend(parsed.findings)
                self.services.review.persist(task.task_id, findings)

                outcome_review = self.services.review.evaluate(findings)
                blocking = outcome_review.blocking
                needs_fix = bool(blocking) or not tests_ok

                if step.kind == "security_review":
                    # Security audits report rather than loop; the findings are
                    # returned to the human instead of triggering a fix job.
                    index += 1
                    continue

                if needs_fix:
                    if fix_attempts >= task.max_fix_attempts:
                        self.transition(
                            task, TaskState.NEEDS_HUMAN,
                            "fix attempt limit reached",
                        )
                        result.state = task.state
                        result.findings = findings
                        result.message = (
                            f"fix attempt limit reached: {len(blocking)} blocking "
                            f"finding(s) after {fix_attempts} fix attempt(s)"
                        )
                        return result
                    fix_attempts += 1
                    self.transition(
                        task, TaskState.FIXING,
                        f"fix attempt {fix_attempts}/{task.max_fix_attempts}",
                    )
                    # Rewind to the implement step for a fix job.
                    index = last_implement_index
                    continue

                self.transition(task, TaskState.VALIDATED, "review passed")
                index += 1
                continue

            # -- validate -----------------------------------------------
            if step.kind == "validate":
                role = self._role(step.role, Role.FINAL_VERIFIER)
                self.transition(task, TaskState.VALIDATED, step.description)
                outcome = self._run_role_job(
                    task, role,
                    workspace=worktree_path,
                    base_ref=base_ref,
                    extras={"review_diff": (diff or "")[:20000]},
                    findings=findings,
                    evidence=evidence,
                    diff=diff,
                )
                result.jobs.append(outcome.job)
                if not outcome.succeeded:
                    return self._fail(task, result, outcome, failures, "validation")
                verdict = self._final_verdict(outcome, task)
                # F-04: final validation may never advance on a green exit code
                # alone - it consumes the same trustworthy test evidence.
                if verdict.get("passed") is True and not tests_ok:
                    verdict = {
                        "passed": False,
                        "valid": False,
                        "reason": (
                            "final validation cannot pass without trustworthy "
                            "test evidence"
                        ),
                    }
                task.metadata["validation"] = verdict
                if verdict.get("passed") is not True:
                    reason = verdict.get("reason") or "criteria not satisfied"
                    self.services.audit.record(
                        EventType.ERROR,
                        task_id=task.task_id,
                        job_id=outcome.job.job_id,
                        status="validation_failed",
                        message=f"final validation failed: {reason}",
                        data=verdict,
                    )
                    if fix_attempts >= task.max_fix_attempts:
                        self.transition(
                            task, TaskState.NEEDS_HUMAN,
                            f"final validation failed ({reason}); fix limit reached",
                        )
                        result.state = task.state
                        result.message = f"final validation failed: {reason}"
                        return result
                    fix_attempts += 1
                    self.transition(task, TaskState.FIXING, f"validation failed: {reason}")
                    index = last_implement_index
                    continue
                index += 1
                continue

            # -- merge --------------------------------------------------
            if step.kind == "merge":
                paused = self._merge(
                    task, result, worktree_path, worktree_branch, base_ref
                )
                if paused is not None:
                    return paused
                index += 1
                continue

            # -- cleanup ------------------------------------------------
            if step.kind == "cleanup":
                if worktree_path and task.repo_path:
                    try:
                        git = self.services.git_factory(task.repo_path)
                        git.remove_worktree(
                            worktree_path, force=True, task_id=task.task_id
                        )
                    except Exception:  # pragma: no cover - cleanup is best effort
                        pass
                    task.workspace = None
                index += 1
                continue

            # -- report -------------------------------------------------
            if step.kind == "report":
                result.findings = findings
                result.evidence = evidence
                if not tests_ok and evidence:
                    if task.state not in TERMINAL_STATES:
                        self.transition(task, TaskState.FAILED, "tests failed, report produced")
                    result.state = task.state
                    result.success = False
                    result.message = f"testing completed with failures: {sum(e.failed or 0 for e in evidence)} failed"
                    self._record_completion(task, result)
                    return result
                if task.state not in TERMINAL_STATES:
                    self.transition(task, TaskState.COMPLETED, "report produced")
                result.state = task.state
                result.success = True
                result.message = "completed"
                self._record_completion(task, result)
                return result

            # Unknown step kind: refuse rather than guess.
            raise WorkflowError(f"unknown recipe step kind '{step.kind}'")

        # Recipe finished without an explicit completion step (mutating recipes
        # complete at merge).
        result.findings = findings
        result.evidence = evidence
        result.state = task.state
        result.success = task.state in (TaskState.COMPLETED, TaskState.MERGED)
        result.message = result.message or task.state.value
        if result.success:
            self._record_completion(task, result)
        return result

    # -- merge -------------------------------------------------------------
    def _merge(
        self,
        task: Task,
        result: WorkflowResult,
        worktree_path: Optional[str],
        worktree_branch: Optional[str],
        base_ref: Optional[str],
    ) -> Optional[WorkflowResult]:
        """Merge the worktree branch. Returns a result to stop on, else None."""
        services = self.services

        # 1. Evaluate GIT_COMMIT permission explicitly (F-08)
        commit_decision = services.policy.evaluate(
            Permission.GIT_COMMIT, task.granted_permissions, task.task_id
        )
        if commit_decision.requires_approval:
            services.approvals.request(
                task_id=task.task_id,
                subject="permission:git_commit",
                reason="Human approval required before committing changes",
            )
            if task.state is not TaskState.WAITING_FOR_APPROVAL:
                self.transition(
                    task, TaskState.WAITING_FOR_APPROVAL, "commit requires approval"
                )
            result.state = task.state
            result.message = "waiting for human approval (permission:git_commit)"
            services.artifacts.save_task(task)
            return result
        if not commit_decision.allowed:
            self.transition(task, TaskState.FAILED, commit_decision.reason)
            result.state = task.state
            result.message = f"commit blocked: {commit_decision.reason}"
            return result

        # 2. Evaluate GIT_MERGE permission explicitly (F-08)
        merge_decision = services.policy.evaluate(
            Permission.GIT_MERGE, task.granted_permissions, task.task_id
        )
        if merge_decision.requires_approval:
            services.approvals.request(
                task_id=task.task_id,
                subject="permission:git_merge",
                reason="Human approval required before merging",
            )
            if task.state is not TaskState.WAITING_FOR_APPROVAL:
                self.transition(
                    task, TaskState.WAITING_FOR_APPROVAL, "merge requires approval"
                )
            result.state = task.state
            result.message = "waiting for human approval (permission:git_merge)"
            services.artifacts.save_task(task)
            return result
        if not merge_decision.allowed:
            self.transition(task, TaskState.FAILED, merge_decision.reason)
            result.state = task.state
            result.message = f"merge blocked: {merge_decision.reason}"
            return result

        if not task.repo_path or not worktree_branch:
            return self._block(task, result, "no worktree branch to merge")
        _ = base_ref

        self.transition(task, TaskState.READY_FOR_MERGE, "preparing merge")
        git = services.git_factory(task.repo_path)
        state = git.state()
        if state.dirty:
            self.transition(
                task, TaskState.NEEDS_HUMAN,
                "target repository has uncommitted changes; refusing to merge",
            )
            result.state = task.state
            result.message = (
                "merge paused: the target working tree is dirty and NAO will not "
                "overwrite uncommitted work"
            )
            return result

        # Commit the worktree branch, then merge it into the baseline branch.
        if worktree_path:
            git.commit(
                message=f"NAO {task.task_id}: {task.title or task.task_type}",
                cwd=worktree_path,
                task_id=task.task_id,
            )
        try:
            git.merge(
                worktree_branch,
                task_id=task.task_id,
                message=f"NAO merge {task.task_id}",
            )
        except Exception as exc:
            self.transition(task, TaskState.NEEDS_HUMAN, f"merge failed: {exc}")
            result.state = task.state
            result.message = f"merge failed: {exc}"
            return result

        self.transition(task, TaskState.MERGED, "merged")
        self.transition(task, TaskState.COMPLETED, "task complete")
        result.state = task.state
        result.success = True
        result.message = "merged and completed"
        return None

    # -- helpers -----------------------------------------------------------
    def _save_progress(self, task: Task, **values) -> None:
        progress = dict(task.metadata.get("workflow_progress", {}))
        progress.update(values)
        task.metadata["workflow_progress"] = progress
        self.services.artifacts.save_task(task)

    def _record_completion(self, task: Task, result: WorkflowResult) -> None:
        self.services.audit.record(
            EventType.TASK_COMPLETED,
            task_id=task.task_id,
            status=task.state.value,
            message=result.message,
            data={
                "jobs": len(result.jobs),
                "findings": len(result.findings),
                "evidence": [e.status for e in result.evidence],
            },
        )

    def _render_findings(self, findings: list[Finding]) -> str:
        lines = []
        for finding in findings:
            if finding.status.value in ("open", "rejected"):
                lines.append(
                    f"- [{finding.severity}] {finding.title} "
                    f"({finding.file or 'n/a'}): {finding.description}"
                )
        return "\n".join(lines)

    def _response_text(self, outcome: JobExecution) -> str:
        if outcome.response and outcome.response.response:
            return outcome.response.response
        return ""

    @staticmethod
    def _parse_json_dict(text: Optional[str]) -> Optional[dict[str, Any]]:
        import json

        if not text or not text.strip():
            return None
        s = text.strip()
        if "```json" in s:
            start = s.find("```json") + 7
            end = s.find("```", start)
            if end != -1:
                s = s[start:end].strip()
        elif "```" in s:
            start = s.find("```") + 3
            end = s.find("```", start)
            if end != -1:
                s = s[start:end].strip()

        start = s.find("{")
        end = s.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            val = json.loads(s[start : end + 1])
            return val if isinstance(val, dict) else None
        except json.JSONDecodeError:
            return None

    def _final_verdict(self, outcome: JobExecution, task: Task) -> dict:
        """Fail-closed verifier output evaluation (plan.md section 15 / F-03).

        The *complete* declared verifier schema is enforced, not just the
        ``passed`` boolean: missing required fields, wrong types and unexpected
        properties are verification failures. A ``passed=true`` verdict must
        also be backed by per-criterion structured evidence, and where the task
        declares acceptance criteria those criteria must actually be covered.
        """
        from ..models.agent_contract import ROLE_OUTPUT_SCHEMAS
        from ..schema import validate

        schema = ROLE_OUTPUT_SCHEMAS.get(Role.FINAL_VERIFIER, {})
        if not outcome.response:
            return self._invalid_verdict("no response received from verifier")

        candidate = None
        structured = outcome.response.structured_output
        if isinstance(structured, dict):
            candidate = structured
        else:
            text = outcome.response.response or outcome.response.stdout or ""
            candidate = self._parse_json_dict(text)

        if not isinstance(candidate, dict):
            return self._invalid_verdict(
                "verifier returned no structured verdict (missing output)"
            )

        errors = validate(candidate, schema)
        if errors:
            return self._invalid_verdict(
                "verifier verdict does not satisfy the required schema: "
                + "; ".join(errors)
            )

        if candidate.get("passed") is True:
            evidence_reason = self._verify_evidence(
                candidate.get("criteria_results"),
                task,
                artifacts=self.services.artifacts if hasattr(self, "services") and self.services else None,
                workspace=getattr(task, "workspace", None) or getattr(task, "repo_path", None),
            )
            if evidence_reason:
                return self._invalid_verdict(evidence_reason)
        return candidate

    @staticmethod
    def _normalize_criterion(value: object) -> str:
        """Exact criterion identity: whitespace- and case-normalized, no fuzz."""
        return " ".join(str(value).strip().casefold().split())

    @classmethod
    def _verify_evidence(
        cls,
        criteria_results: object,
        task: Task,
        artifacts: Optional[Any] = None,
        workspace: Optional[str] = None,
    ) -> str:
        """Return a rejection reason when a passing verdict is not justified.

        F-03: the task criteria and the verifier's ``criteria_results`` must be
        an exact one-to-one set - every task criterion reported exactly once,
        no extras, no duplicates, exact (non-substring) identity, and each
        passed criterion backed by structured evidence referencing a real artifact.
        """
        if not isinstance(criteria_results, list) or not criteria_results:
            return (
                "verifier reported passed=true without any criteria_results "
                "evidence"
            )

        task_criteria = [
            str(criterion)
            for criterion in (getattr(task, "acceptance_criteria", []) or [])
            if str(criterion).strip()
        ]

        from ..models.task import detect_contradictory_criteria, detect_duplicate_criteria

        dup = detect_duplicate_criteria(task_criteria)
        if dup:
            return f"task has {dup}"
        contra = detect_contradictory_criteria(task_criteria)
        if contra:
            return f"contradictory task acceptance criteria: '{contra[0]}' vs '{contra[1]}'"

        supplied: dict[str, str] = {}
        failed: list[str] = []
        for item in criteria_results:
            if not isinstance(item, dict):
                return "verifier criteria_results entries must be objects"
            raw_criterion = item.get("criterion")
            if not isinstance(raw_criterion, str) or not raw_criterion.strip():
                return "verifier criterion_results entry has no criterion"
            key = cls._normalize_criterion(raw_criterion)
            if key in supplied:
                return (
                    "verifier reported duplicate criterion: "
                    f"{raw_criterion}"
                )
            supplied[key] = raw_criterion

            evidence = item.get("evidence")
            valid, err = cls._validate_evidence(evidence, task, artifacts, workspace)
            if not valid:
                return (
                    f"verifier criterion '{raw_criterion}' has invalid evidence: {err}"
                )
            if item.get("passed") is not True:
                failed.append(raw_criterion)

        if failed:
            return (
                "verifier reported passed=true but criteria failed: "
                + ", ".join(failed)
            )

        if not task_criteria:
            return ""

        wanted = {cls._normalize_criterion(c): c for c in task_criteria}
        missing = [wanted[k] for k in wanted if k not in supplied]
        if missing:
            return (
                "verifier did not report a result for acceptance criterion: "
                + ", ".join(missing)
            )
        extra = [supplied[k] for k in supplied if k not in wanted]
        if extra:
            return (
                "verifier reported criteria that are not task acceptance "
                "criteria: " + ", ".join(extra)
            )
        return ""

    @classmethod
    def _validate_evidence(
        cls,
        evidence: object,
        task: Task,
        artifacts: Optional[Any] = None,
        workspace: Optional[str] = None,
    ) -> tuple[bool, str]:
        """F-03: evidence must be a structured artifact reference to an existing artifact."""
        if not isinstance(evidence, dict):
            return False, "evidence must be a structured object (prose is not accepted)"
        kind = evidence.get("kind")
        reference = evidence.get("reference")
        if not isinstance(kind, str) or kind not in _EVIDENCE_KINDS:
            return False, f"evidence kind '{kind}' is invalid (must be one of {sorted(_EVIDENCE_KINDS)})"
        if not isinstance(reference, str) or not reference.strip():
            return False, "evidence reference is blank or missing"
        reference = reference.strip()

        task_id = getattr(task, "task_id", None)
        if task_id:
            normalized_ref = reference.replace("\\", "/")
            for part in normalized_ref.split("/"):
                if part.startswith("task-") and part != task_id:
                    return False, f"foreign-task artifact reference '{reference}'"

        if artifacts is not None and task_id:
            task_dir = artifacts.task_dir(task_id)
            if kind == "test":
                tests_dir = task_dir / "tests"
                test_records = artifacts.load_tests(task_id)
                found = False
                if tests_dir.exists() and any(p.name == reference or reference in p.name for p in tests_dir.glob("*.json")):
                    found = True
                elif any(reference in str(t) or t.get("job_id") == reference or t.get("scope") == reference for t in test_records):
                    found = True
                else:
                    ws = workspace or getattr(task, "workspace", None) or getattr(task, "repo_path", None)
                    if ws and (Path(ws) / reference.split("::")[0]).exists():
                        found = True
                if not found:
                    return False, f"nonexistent test artifact reference '{reference}'"
            elif kind == "diff":
                diffs_dir = task_dir / "diffs"
                found = (
                    (diffs_dir.exists() and (diffs_dir / reference).exists())
                    or (diffs_dir.exists() and (diffs_dir / f"{reference}.diff").exists())
                    or (artifacts.load_diff(task_id, reference) is not None)
                )
                if not found:
                    return False, f"nonexistent diff artifact reference '{reference}'"
            elif kind in ("artifact", "command"):
                jobs_dir = task_dir / "jobs"
                found = (
                    (task_dir / reference).exists()
                    or (jobs_dir.exists() and (jobs_dir / reference).exists())
                    or (jobs_dir.exists() and (jobs_dir / f"{reference}.json").exists())
                    or (task_dir / "task.json").exists()
                )
                if not found:
                    return False, f"nonexistent artifact reference '{reference}'"
            elif kind == "file":
                ws = workspace or getattr(task, "workspace", None) or getattr(task, "repo_path", None)
                if not ws or not (Path(ws) / reference).exists():
                    return False, f"nonexistent workspace file reference '{reference}'"

        return True, ""

    @classmethod
    def _evidence_is_valid(cls, evidence: object) -> bool:
        """Legacy helper for schema checks."""
        valid, _ = cls._validate_evidence(evidence, task=None)  # type: ignore
        return valid

    @staticmethod
    def _invalid_verdict(reason: str) -> dict:
        return {"passed": False, "valid": False, "reason": reason}

    def _role(self, name: Optional[str], default: Role) -> Role:
        if not name:
            return default
        try:
            return Role(name)
        except ValueError:
            return default

    def _select_agent(
        self, task: Task, role: Role, exclude: set[str]
    ) -> str:
        # Route on the permissions this *job* will actually exercise
        # (ROLE_PERMISSIONS), not the whole task grant. The task grant is
        # enforced later by the policy engine (plan.md sections 8 and 17), but
        # adapter permission compatibility must be checked against the real job.
        decision = self.services.router.select(
            role=role,
            required_capabilities=ROLE_CAPABILITIES.get(role, ()),
            granted_permissions=ROLE_PERMISSIONS.get(role, (Permission.READ,)),
            exclude=exclude,
            overrides=task.routing_overrides,
        )
        self.services.audit.record(
            EventType.AGENT_SELECTED,
            task_id=task.task_id,
            agent=decision.agent,
            role=role.value,
            status="selected",
            message=decision.reason,
            data=decision.to_dict(),
        )
        return decision.agent

    def _build_prompt(self, task: Task, role: Role, extras: dict) -> str:
        criteria = "\n".join(f"- {c}" for c in task.acceptance_criteria) or "- (none)"
        parts = [
            f"Role: {role.value}",
            f"Task: {task.title or task.task_type}",
            "",
            task.description,
            "",
            "Acceptance criteria:",
            criteria,
        ]
        if extras.get("analysis"):
            parts += ["", "Previous analysis:", extras["analysis"][:4000]]
        if extras.get("findings"):
            parts += ["", "Open review findings to fix:", extras["findings"]]
        if extras.get("review_diff"):
            parts += [
                "",
                "Diff under review:",
                "```",
                extras["review_diff"],
                "```",
            ]
        return "\n".join(parts)

    def _run_role_job(
        self,
        task: Task,
        role: Role,
        workspace: Optional[str] = None,
        base_ref: Optional[str] = None,
        extras: Optional[dict] = None,
        findings: Optional[list[Finding]] = None,
        evidence: Optional[list[TestEvidence]] = None,
        diff: Optional[str] = None,
        previous_failures: Optional[list[dict]] = None,
        initial_exclude: Optional[set[str]] = None,
    ) -> JobExecution:
        extras = extras or {}
        exclude: set[str] = set(initial_exclude or set())
        attempts = max(1, task.max_jobs_per_role)
        last: Optional[JobExecution] = None

        for attempt in range(1, attempts + 1):
            try:
                agent = self._select_agent(task, role, exclude)
            except RoutingError as exc:
                # Availability-only failures block the task (nothing a human can
                # decide); policy/capability/exclusion failures need a human.
                failure_class = (
                    "unavailable"
                    if exc.is_availability_only()
                    else "unsupported_permission"
                )
                self.services.audit.record(
                    EventType.AGENT_UNAVAILABLE,
                    task_id=task.task_id,
                    role=role.value,
                    status="routing_failed",
                    message=str(exc),
                    data={
                        "failure_class": failure_class,
                        "rejected": [
                            {"agent": n, "reason": r} for n, r in exc.rejected
                        ],
                    },
                )
                if last is not None:
                    return last
                from ..models.agent_contract import AgentResponse
                job = AgentJob(
                    job_id=f"job-{uuid4().hex[:8]}",
                    task_id=task.task_id,
                    agent="none",
                    role=role,
                    status=JobStatus.UNAVAILABLE,
                )
                outcome = JobExecution(job=job, blocked=False)
                outcome.response = AgentResponse(
                    job_id=job.job_id,
                    agent="none",
                    status="unavailable",
                    errors=[{"failure_class": failure_class, "message": str(exc)}],
                )
                return outcome

            job = AgentJob(
                job_id=f"job-{uuid4().hex[:8]}",
                task_id=task.task_id,
                agent=agent,
                role=role,
                workspace=workspace,
                prompt=self._build_prompt(task, role, extras),
                permissions=ROLE_PERMISSIONS.get(role, (Permission.READ,)),
                timeout_seconds=self.services.config.agent(agent).timeout_seconds,
                model=self.services.config.agent(agent).model,
                provider=self.services.config.agent(agent).provider,
                attempt=attempt,
                parent_job=last.job.job_id if last else None,
            )
            outcome = self.execution.execute_job(
                task,
                job,
                workspace=workspace,
                base_ref=base_ref,
                findings=findings,
                test_evidence=evidence,
                diff=diff,
                previous_failures=previous_failures,
            )
            last = outcome
            if outcome.succeeded:
                return outcome
            # Retry only when the failure is retryable and another agent exists.
            error_classes = [
                e.get("failure_class")
                for e in (outcome.response.errors if outcome.response else [])
            ]
            retryable = any(
                cls in ("tool_failure", "process_failure", "malformed_output",
                        "implementation_failure", "rate_limit")
                for cls in error_classes
            )
            if not retryable or outcome.blocked:
                return outcome
            exclude.add(agent)

        assert last is not None
        return last

    def _fail(
        self,
        task: Task,
        result: WorkflowResult,
        outcome: JobExecution,
        failures: list[dict],
        phase: str,
    ) -> WorkflowResult:
        error_class = "unknown"
        message = f"{phase} failed"
        if outcome.response and outcome.response.errors:
            error = outcome.response.errors[0]
            error_class = error.get("failure_class", "unknown")
            message = error.get("message", message)
        failures.append({"failure_class": error_class, "message": message, "phase": phase})

        if outcome.blocked:
            self.transition(task, TaskState.BLOCKED, message)
        elif error_class == "unavailable":
            # No usable agent exists to run the job (not installed / disabled).
            self.transition(task, TaskState.BLOCKED, message)
        elif error_class in ("unsupported_permission", "rate_limit",
                             "authentication", "authorization",
                             "invalid_configuration"):
            self.transition(task, TaskState.NEEDS_HUMAN, message)
        else:
            self.transition(task, TaskState.FAILED, message)

        self.services.audit.record(
            EventType.TASK_FAILED,
            task_id=task.task_id,
            status=task.state.value,
            message=message,
            data={"phase": phase, "failure_class": error_class},
        )
        result.state = task.state
        result.success = False
        result.message = message
        return result

    def _block(
        self, task: Task, result: WorkflowResult, message: str
    ) -> WorkflowResult:
        if task.state not in TERMINAL_STATES:
            self.transition(task, TaskState.BLOCKED, message)
        result.state = task.state
        result.success = False
        result.message = message
        return result
