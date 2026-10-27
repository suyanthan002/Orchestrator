"""End-to-end workflow tests.

These drive the full orchestration pipeline (state machine, worktree, policy,
context, tests, review, fix loop, approval, merge, audit) against scripted
agents so nothing depends on a live cloud agent.
"""

import unittest
from pathlib import Path

from nao.adapters.base import Availability
from nao.audit import AuditLogger
from nao.config import load_config
from nao.errors import WorkflowError
from nao.models.enums import Role, TaskState
from nao.services import build_services
from nao.task_manager import TaskManager
from tests.support import (
    ScriptedAdapter,
    init_repo,
    make_response,
    scripted_services,
    temp_dir,
    write_in_workspace,
)


class UnavailableAdapter(ScriptedAdapter):
    def availability(self) -> Availability:
        return Availability(False, "not installed (test)")


#: The minimum valid passing verifier verdict: a passed boolean plus at least
#: one *structurally evidenced* criterion result (F-03).
PASSING_VERDICT = {
    "passed": True,
    "criteria_results": [
        {
            "criterion": "acceptance criteria satisfied",
            "passed": True,
            "evidence": {
                "kind": "test",
                "reference": "tests/test_app.py",
                "detail": "independent review found no blocking issues",
            },
        }
    ],
}


def make_handlers(
    state,
    implement_file="feature.py",
    resumed_finding=None,
    verdict=None,
):
    def architect(request):
        return make_response(request, text="PLAN: implement the feature.")

    def debugger(request):
        state["debugger_calls"] = state.get("debugger_calls", 0) + 1
        return make_response(request, text="DIAGNOSIS: root cause found.")

    def implementer(request):
        state["implement_calls"] = state.get("implement_calls", 0) + 1
        write_in_workspace(
            request,
            implement_file,
            f"# attempt {state['implement_calls']}\nVALUE = 1\n",
        )
        return make_response(request, structured={"edits": [implement_file]})

    def documentation(request):
        state["doc_calls"] = state.get("doc_calls", 0) + 1
        write_in_workspace(request, "README.md", "# Updated docs\n")
        return make_response(request, structured={"edits": ["README.md"]})

    def reviewer(request):
        state["review_calls"] = state.get("review_calls", 0) + 1
        script = state.get("review_script", [])
        index = state["review_calls"] - 1
        items = [script[index]] if index < len(script) else []
        return make_response(request, text="review complete", structured={"findings": items})

    def security_reviewer(request):
        state["security_calls"] = state.get("security_calls", 0) + 1
        return make_response(request, text="security review ok", structured={"findings": []})

    def tester(request):
        state["tester_calls"] = state.get("tester_calls", 0) + 1
        return make_response(request, text="tests analyzed")

    def verifier(request):
        state["verifier_calls"] = state.get("verifier_calls", 0) + 1
        if verdict is not None:
            return make_response(request, structured=verdict if isinstance(verdict, dict) else None, text=str(verdict) if isinstance(verdict, str) else None)
        custom_verdict = state.get("verdict", PASSING_VERDICT)
        if isinstance(custom_verdict, dict):
            return make_response(request, structured=custom_verdict)
        return make_response(request, text=str(custom_verdict))

    return {
        Role.ARCHITECT: architect,
        Role.DEBUGGER: debugger,
        Role.IMPLEMENTER: implementer,
        Role.DOCUMENTATION: documentation,
        Role.REVIEWER: reviewer,
        Role.SECURITY_REVIEWER: security_reviewer,
        Role.TESTER: tester,
        Role.FINAL_VERIFIER: verifier,
    }


class StateMachineTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir()
        self.repo = init_repo(self.root / "repo")
        self.services, _ = scripted_services(self.root, make_handlers({}))
        self.engine = TaskManager(self.services).engine
        self.manager = TaskManager(self.services)

    def test_invalid_transition_is_rejected(self):
        task = self.manager.create_task("x", repo_path=str(self.repo))
        with self.assertRaises(WorkflowError):
            self.engine.transition(task, TaskState.COMPLETED)

    def test_valid_transition_is_recorded(self):
        task = self.manager.create_task("x", repo_path=str(self.repo))
        self.engine.transition(task, TaskState.ANALYZING, "start")
        self.assertEqual(task.state, TaskState.ANALYZING)
        events = self.services.audit.events(task.task_id)
        self.assertTrue(
            any(e["event"] == "task_state_changed" for e in events)
        )

    def test_task_created_is_audited(self):
        task = self.manager.create_task("x", repo_path=str(self.repo))
        events = self.services.audit.events(task.task_id)
        self.assertEqual(events[0]["event"], "task_created")


class WorkflowRunTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir()
        self.repo = init_repo(self.root / "repo")

    def _services(self, state, **kwargs):
        services, requests = scripted_services(
            self.root, make_handlers(state, **kwargs)
        )
        return services, TaskManager(services), requests

    def test_approval_gate_pauses_execution(self):
        state = {}
        services, manager, _ = self._services(state)
        task = manager.create_task(
            "Add a feature", repo_path=str(self.repo), recipe="feature_implementation"
        )
        result = manager.run(task)

        self.assertEqual(result.state, TaskState.WAITING_FOR_APPROVAL)
        self.assertFalse(result.success)
        pending = manager.pending_approvals(task.task_id)
        self.assertEqual([p["subject"] for p in pending], ["approval:plan"])
        # No implementation may happen before approval.
        self.assertEqual(state.get("implement_calls", 0), 0)
        self.assertFalse((self.repo / "feature.py").exists())

    def test_full_implementation_flow_completes_and_merges(self):
        state = {}
        services, manager, requests = self._services(state)
        task = manager.create_task(
            "Add a feature", repo_path=str(self.repo), recipe="feature_implementation"
        )
        # Human approves the plan, commit, and merge up front.
        plan = services.approvals.request(task.task_id, "approval:plan", "review")
        services.approvals.grant(task.task_id, plan.approval_id, "human")
        commit = services.approvals.request(
            task.task_id, "permission:git_commit", "commit"
        )
        services.approvals.grant(task.task_id, commit.approval_id, "human")
        merge = services.approvals.request(
            task.task_id, "permission:git_merge", "merge"
        )
        services.approvals.grant(task.task_id, merge.approval_id, "human")

        result = manager.run(task)

        self.assertEqual(result.state, TaskState.COMPLETED, result.message)
        self.assertTrue(result.success, result.message)
        self.assertTrue((self.repo / "feature.py").exists())
        self.assertEqual(state["implement_calls"], 1)
        self.assertEqual(state["review_calls"], 1)

        events = [e["event"] for e in services.audit.events(task.task_id)]
        for expected in (
            "task_created",
            "git_baseline_created",
            "worktree_created",
            "agent_selected",
            "job_started",
            "job_completed",
            "files_changed",
            "tests_started",
            "tests_completed",
            "git_committed",
            "git_merged",
            "task_completed",
        ):
            self.assertIn(expected, events, expected)
        self.assertIn("worktree_removed", events)

    def test_fix_loop_resolves_finding_and_passes(self):
        state = {
            "review_script": [
                {
                    "title": "off-by-one",
                    "severity": "high",
                    "description": "value is wrong",
                    "file": "feature.py",
                }
            ]
        }
        services, manager, _ = self._services(state)
        task = manager.create_task(
            "Add a feature", repo_path=str(self.repo), recipe="feature_implementation"
        )
        plan = services.approvals.request(task.task_id, "approval:plan", "review")
        services.approvals.grant(task.task_id, plan.approval_id, "human")
        commit = services.approvals.request(task.task_id, "permission:git_commit", "commit")
        services.approvals.grant(task.task_id, commit.approval_id, "human")
        merge = services.approvals.request(task.task_id, "permission:git_merge", "merge")
        services.approvals.grant(task.task_id, merge.approval_id, "human")

        result = manager.run(task)

        self.assertEqual(result.state, TaskState.COMPLETED, result.message)
        self.assertEqual(state["implement_calls"], 2)  # initial + one fix
        self.assertEqual(state["review_calls"], 2)
        findings = manager.findings(task.task_id)
        self.assertTrue(any(f["status"] == "resolved" for f in findings))

    def test_fix_limit_escalates_to_human(self):
        state = {
            "review_script": [
                {"title": "issue 1", "severity": "high", "description": "a"},
                {"title": "issue 2", "severity": "high", "description": "b"},
                {"title": "issue 3", "severity": "high", "description": "c"},
            ]
        }
        services, manager, _ = self._services(state)
        task = manager.create_task(
            "Add a feature",
            repo_path=str(self.repo),
            recipe="feature_implementation",
            max_fix_attempts=1,
        )
        plan = services.approvals.request(task.task_id, "approval:plan", "review")
        services.approvals.grant(task.task_id, plan.approval_id, "human")

        result = manager.run(task)

        self.assertEqual(result.state, TaskState.NEEDS_HUMAN, result.message)
        self.assertIn("fix attempt limit", result.message)

    def test_read_only_recipe_creates_no_worktree(self):
        state = {
            "review_script": [],
        }
        services, manager, _ = self._services(state)
        task = manager.create_task(
            "Review the code", repo_path=str(self.repo), recipe="code_review"
        )
        result = manager.run(task)

        self.assertEqual(result.state, TaskState.COMPLETED, result.message)
        self.assertIsNone(task.workspace)
        events = [e["event"] for e in services.audit.events(task.task_id)]
        self.assertNotIn("worktree_created", events)

    def test_unavailable_agents_block_the_task(self):
        config = load_config()
        adapter_classes = {name: UnavailableAdapter for name in config.agents}
        services = build_services(
            config=config,
            runs_dir=self.root / "r2",
            audit_dir=self.root / "a2",
            audit=AuditLogger(self.root / "a2"),
            adapter_classes=adapter_classes,
        )
        manager = TaskManager(services)
        task = manager.create_task("Add a feature", repo_path=str(self.repo))
        result = manager.run(task)

        self.assertEqual(result.state, TaskState.BLOCKED, result.message)
        self.assertFalse(result.success)

    def test_dirty_target_repo_pauses_merge(self):
        state = {}
        services, manager, _ = self._services(state)
        task = manager.create_task(
            "Add a feature", repo_path=str(self.repo), recipe="feature_implementation"
        )
        plan = services.approvals.request(task.task_id, "approval:plan", "review")
        services.approvals.grant(task.task_id, plan.approval_id, "human")
        commit = services.approvals.request(task.task_id, "permission:git_commit", "commit")
        services.approvals.grant(task.task_id, commit.approval_id, "human")
        merge = services.approvals.request(task.task_id, "permission:git_merge", "merge")
        services.approvals.grant(task.task_id, merge.approval_id, "human")

        # Human makes an uncommitted change while NAO is working.
        (self.repo / "app.py").write_text("value = 999\n", encoding="utf-8")

        result = manager.run(task)

        self.assertEqual(result.state, TaskState.NEEDS_HUMAN, result.message)
        self.assertIn("dirty", result.message)
        # The uncommitted human change is untouched.
        self.assertEqual(
            (self.repo / "app.py").read_text(encoding="utf-8"), "value = 999\n"
        )

    def test_resume_after_approval_continues(self):
        state = {}
        services, manager, _ = self._services(state)
        task = manager.create_task(
            "Add a feature", repo_path=str(self.repo), recipe="feature_implementation"
        )
        first = manager.run(task)
        self.assertEqual(first.state, TaskState.WAITING_FOR_APPROVAL)

        plan = services.approvals.request(task.task_id, "approval:plan", "review")
        services.approvals.grant(task.task_id, plan.approval_id, "human")
        commit = services.approvals.request(task.task_id, "permission:git_commit", "commit")
        services.approvals.grant(task.task_id, commit.approval_id, "human")
        merge = services.approvals.request(task.task_id, "permission:git_merge", "merge")
        services.approvals.grant(task.task_id, merge.approval_id, "human")

        second = manager.run_task(task.task_id)
        self.assertEqual(second.state, TaskState.COMPLETED, second.message)

    def test_rejected_approval_fails_the_task(self):
        state = {}
        services, manager, _ = self._services(state)
        task = manager.create_task("Add a feature", repo_path=str(self.repo))
        manager.run(task)
        plan = services.approvals.all(task.task_id)[0]
        services.approvals.deny(task.task_id, plan.approval_id, "human", "no")

        result = manager.run_task(task.task_id)
        self.assertEqual(result.state, TaskState.FAILED)
        self.assertIn("denied", result.message)


class ContextAndEvidenceTests(unittest.TestCase):
    def test_context_pack_is_persisted_and_reviewer_is_independent(self):
        root = temp_dir()
        repo = init_repo(root / "repo")
        state = {}
        services, requests = scripted_services(root, make_handlers(state))
        manager = TaskManager(services)
        task = manager.create_task(
            "Add a feature",
            repo_path=str(repo),
            recipe="feature_implementation",
            metadata={"relevant_files": ["app.py"]},
        )
        plan = services.approvals.request(task.task_id, "approval:plan", "review")
        services.approvals.grant(task.task_id, plan.approval_id, "human")
        commit = services.approvals.request(task.task_id, "permission:git_commit", "commit")
        services.approvals.grant(task.task_id, commit.approval_id, "human")
        merge = services.approvals.request(task.task_id, "permission:git_merge", "merge")
        services.approvals.grant(task.task_id, merge.approval_id, "human")
        result = manager.run(task)
        self.assertTrue(result.success, result.message)

        reviewer_requests = [r for r in requests if r.role is Role.REVIEWER]
        self.assertTrue(reviewer_requests)
        reviewer_request = reviewer_requests[0]
        # The reviewer must be a different agent than the implementer.
        implementer_agents = {
            j.agent for j in result.jobs if j.role is Role.IMPLEMENTER
        }
        self.assertNotIn(reviewer_request.agent, implementer_agents)
        # The reviewer must receive the acceptance criteria and the diff.
        self.assertIn("Acceptance", reviewer_request.context)

        # The context pack for a job is persisted for auditing.
        for job in result.jobs:
            context_file = (
                services.artifacts.task_dir(task.task_id)
                / "contexts"
                / f"{job.job_id}.txt"
            )
            self.assertTrue(context_file.exists())


class FailClosedVerificationTests(unittest.TestCase):
    """F-03: Fail-Closed Verification Tests."""

    def setUp(self):
        self.root = temp_dir()
        self.repo = init_repo(self.root / "repo")

    def _run_with_verdict(self, verdict):
        state = {}
        services, requests = scripted_services(
            self.root, make_handlers(state, verdict=verdict)
        )
        manager = TaskManager(services)
        task = manager.create_task(
            "Verify task", repo_path=str(self.repo), recipe="feature_implementation"
        )
        plan = services.approvals.request(task.task_id, "approval:plan", "review")
        services.approvals.grant(task.task_id, plan.approval_id, "human")
        commit = services.approvals.request(task.task_id, "permission:git_commit", "commit")
        services.approvals.grant(task.task_id, commit.approval_id, "human")
        merge = services.approvals.request(task.task_id, "permission:git_merge", "merge")
        services.approvals.grant(task.task_id, merge.approval_id, "human")
        return manager.run(task), services

    def test_missing_structured_output_fails(self):
        result, _ = self._run_with_verdict("")
        self.assertEqual(result.state, TaskState.NEEDS_HUMAN)
        self.assertIn("validation failed", result.message.lower())

    def test_malformed_json_fails(self):
        result, _ = self._run_with_verdict("This is not json {bad")
        self.assertEqual(result.state, TaskState.NEEDS_HUMAN)
        self.assertIn("validation failed", result.message.lower())

    def test_ambiguous_verdict_fails(self):
        result, _ = self._run_with_verdict({"passed": "maybe", "reason": "unsure"})
        self.assertEqual(result.state, TaskState.NEEDS_HUMAN)
        self.assertIn("validation failed", result.message.lower())

    def test_explicit_failure_fails(self):
        result, _ = self._run_with_verdict({"passed": False, "reason": "unresolved bug"})
        self.assertEqual(result.state, TaskState.NEEDS_HUMAN)
        self.assertIn("validation failed", result.message.lower())

    def test_explicit_pass_succeeds(self):
        result, _ = self._run_with_verdict(dict(PASSING_VERDICT))
        self.assertEqual(result.state, TaskState.COMPLETED, result.message)
        self.assertTrue(result.success)

    def test_pass_without_criteria_evidence_fails(self):
        # F-03: a bare passed=true is not enough evidence.
        result, _ = self._run_with_verdict({"passed": True, "reason": "trust me"})
        self.assertEqual(result.state, TaskState.NEEDS_HUMAN)
        self.assertIn("validation failed", result.message.lower())

    def test_pass_with_unbacked_criterion_fails(self):
        # F-03: a criterion result with no evidence cannot justify passed=true.
        result, _ = self._run_with_verdict(
            {
                "passed": True,
                "criteria_results": [
                    {"criterion": "x", "passed": True, "evidence": ""}
                ],
            }
        )
        self.assertEqual(result.state, TaskState.NEEDS_HUMAN)
        self.assertIn("validation failed", result.message.lower())

    def test_missing_passed_field_fails(self):
        result, _ = self._run_with_verdict({"reason": "no verdict given"})
        self.assertEqual(result.state, TaskState.NEEDS_HUMAN)
        self.assertIn("validation failed", result.message.lower())

    def test_passed_as_string_fails(self):
        result, _ = self._run_with_verdict({"passed": "yes"})
        self.assertEqual(result.state, TaskState.NEEDS_HUMAN)
        self.assertIn("validation failed", result.message.lower())

    def test_unknown_verdict_shape_fails(self):
        result, _ = self._run_with_verdict({"verdict": "ok"})
        self.assertEqual(result.state, TaskState.NEEDS_HUMAN)
        self.assertIn("validation failed", result.message.lower())


class TestGatesTests(unittest.TestCase):
    """F-04: Test Gates Tests."""

    def setUp(self):
        self.root = temp_dir()
        self.repo = init_repo(self.root / "repo")

    def test_failing_regression_test_escalates_to_needs_human(self):
        state = {}
        services, requests = scripted_services(self.root, make_handlers(state))
        manager = TaskManager(services)
        task = manager.create_task(
            "Add feature",
            repo_path=str(self.repo),
            recipe="feature_implementation",
            metadata={"test_command": ["python", "-c", "import sys; sys.exit(1)"]},
            max_fix_attempts=1,
        )
        plan = services.approvals.request(task.task_id, "approval:plan", "review")
        services.approvals.grant(task.task_id, plan.approval_id, "human")
        commit = services.approvals.request(task.task_id, "permission:git_commit", "commit")
        services.approvals.grant(task.task_id, commit.approval_id, "human")
        merge = services.approvals.request(task.task_id, "permission:git_merge", "merge")
        services.approvals.grant(task.task_id, merge.approval_id, "human")

        result = manager.run(task)
        self.assertEqual(result.state, TaskState.NEEDS_HUMAN)
        self.assertIn("fix attempt limit reached", result.message.lower())

    def test_testing_recipe_reports_failure_on_failing_tests(self):
        state = {}
        services, requests = scripted_services(self.root, make_handlers(state))
        manager = TaskManager(services)
        task = manager.create_task(
            "Run tests",
            repo_path=str(self.repo),
            recipe="testing",
            metadata={"test_command": ["python", "-c", "import sys; sys.exit(2)"]},
        )
        result = manager.run(task)
        self.assertEqual(result.state, TaskState.FAILED)
        self.assertFalse(result.success)

    def test_bug_fix_regression_failure_cannot_complete(self):
        # F-04: bug_fix must require a passing regression test before merge.
        import sys

        state = {}
        services, _ = scripted_services(self.root, make_handlers(state))
        manager = TaskManager(services)
        task = manager.create_task(
            "Fix a bug",
            repo_path=str(self.repo),
            recipe="bug_fix",
            metadata={"test_command": [sys.executable, "-c", "import sys; sys.exit(1)"]},
            max_fix_attempts=1,
        )
        plan = services.approvals.request(task.task_id, "approval:plan", "review")
        services.approvals.grant(task.task_id, plan.approval_id, "human")
        commit = services.approvals.request(task.task_id, "permission:git_commit", "commit")
        services.approvals.grant(task.task_id, commit.approval_id, "human")
        merge = services.approvals.request(task.task_id, "permission:git_merge", "merge")
        services.approvals.grant(task.task_id, merge.approval_id, "human")

        result = manager.run(task)
        self.assertIn(result.state, (TaskState.NEEDS_HUMAN, TaskState.FAILED))
        self.assertFalse(result.success)
        self.assertFalse((self.repo / "feature.py").exists())

    def test_exit_zero_with_reported_failure_cannot_pass(self):
        # F-04: an exit code of 0 with a reported failing test is NOT a pass.
        import sys

        state = {}
        services, _ = scripted_services(self.root, make_handlers(state))
        manager = TaskManager(services)
        task = manager.create_task(
            "Run tests",
            repo_path=str(self.repo),
            recipe="testing",
            metadata={
                "test_command": [
                    sys.executable,
                    "-c",
                    "print('2 passed, 1 failed in 0.1s')",
                ]
            },
        )
        result = manager.run(task)
        self.assertEqual(result.state, TaskState.FAILED)
        self.assertFalse(result.success)

    def test_successful_regression_completes(self):
        # F-04: a bug_fix whose regression tests genuinely pass may complete.
        # The command must invoke a *supported test runner* - a bare process
        # printing a summary line is no longer accepted as evidence.
        import sys

        state = {}
        services, _ = scripted_services(self.root, make_handlers(state))
        manager = TaskManager(services)
        task = manager.create_task(
            "Fix a bug",
            repo_path=str(self.repo),
            recipe="bug_fix",
            metadata={
                "test_command": [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                ]
            },
        )
        plan = services.approvals.request(task.task_id, "approval:plan", "review")
        services.approvals.grant(task.task_id, plan.approval_id, "human")
        commit = services.approvals.request(task.task_id, "permission:git_commit", "commit")
        services.approvals.grant(task.task_id, commit.approval_id, "human")
        merge = services.approvals.request(task.task_id, "permission:git_merge", "merge")
        services.approvals.grant(task.task_id, merge.approval_id, "human")
        result = manager.run(task)
        self.assertEqual(result.state, TaskState.COMPLETED, result.message)
        self.assertTrue(result.success)

    def test_testing_recipe_passes_on_success(self):
        # F-04: the gate requires evidence that tests actually executed, so the
        # command must run real tests (the temp repo has tests/test_app.py).
        import sys

        state = {}
        services, requests = scripted_services(self.root, make_handlers(state))
        manager = TaskManager(services)
        task = manager.create_task(
            "Run tests",
            repo_path=str(self.repo),
            recipe="testing",
            metadata={
                "test_command": [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                ]
            },
        )
        result = manager.run(task)
        self.assertEqual(result.state, TaskState.COMPLETED, result.message)
        self.assertTrue(result.success)


class ExplicitCommitApprovalTests(unittest.TestCase):
    """F-08: Explicit Commit and Merge Approval Separation Tests."""

    def setUp(self):
        self.root = temp_dir()
        self.repo = init_repo(self.root / "repo")

    def test_commit_denied_stops_workflow(self):
        state = {}
        services, requests = scripted_services(self.root, make_handlers(state))
        manager = TaskManager(services)
        task = manager.create_task(
            "Add feature", repo_path=str(self.repo), recipe="feature_implementation"
        )
        plan = services.approvals.request(task.task_id, "approval:plan", "review")
        services.approvals.grant(task.task_id, plan.approval_id, "human")
        
        # Run until waiting for commit approval
        result1 = manager.run(task)
        self.assertEqual(result1.state, TaskState.WAITING_FOR_APPROVAL)
        pending = manager.pending_approvals(task.task_id)
        self.assertEqual(pending[0]["subject"], "permission:git_commit")
        
        # Deny commit
        services.approvals.deny(task.task_id, pending[0]["approval_id"], "human", "no commit")
        result2 = manager.run_task(task.task_id)
        self.assertEqual(result2.state, TaskState.FAILED)
        self.assertIn("denied", result2.message)

    def test_commit_approved_merge_denied_stops_before_merge(self):
        state = {}
        services, requests = scripted_services(self.root, make_handlers(state))
        manager = TaskManager(services)
        task = manager.create_task(
            "Add feature", repo_path=str(self.repo), recipe="feature_implementation"
        )
        plan = services.approvals.request(task.task_id, "approval:plan", "review")
        services.approvals.grant(task.task_id, plan.approval_id, "human")
        commit = services.approvals.request(task.task_id, "permission:git_commit", "commit")
        services.approvals.grant(task.task_id, commit.approval_id, "human")

        # Run until waiting for merge approval
        result1 = manager.run(task)
        self.assertEqual(result1.state, TaskState.WAITING_FOR_APPROVAL)
        pending = manager.pending_approvals(task.task_id)
        self.assertEqual(pending[0]["subject"], "permission:git_merge")

        # Deny merge
        services.approvals.deny(task.task_id, pending[0]["approval_id"], "human", "no merge")
        result2 = manager.run_task(task.task_id)
        self.assertEqual(result2.state, TaskState.FAILED)
        self.assertIn("denied", result2.message)
        # Verify base repo was not merged
        self.assertFalse((self.repo / "feature.py").exists())


class RecipeRoleSemanticsTests(unittest.TestCase):
    """F-16: Recipe Role Semantics & F-12: Independent Reviewer Tests."""

    def setUp(self):
        self.root = temp_dir()
        self.repo = init_repo(self.root / "repo")

    def test_bug_fix_recipe_routes_to_debugger(self):
        state = {}
        services, requests = scripted_services(self.root, make_handlers(state))
        manager = TaskManager(services)
        task = manager.create_task(
            "Fix a bug", repo_path=str(self.repo), recipe="bug_fix"
        )
        plan = services.approvals.request(task.task_id, "approval:plan", "review")
        services.approvals.grant(task.task_id, plan.approval_id, "human")
        commit = services.approvals.request(task.task_id, "permission:git_commit", "commit")
        services.approvals.grant(task.task_id, commit.approval_id, "human")
        merge = services.approvals.request(task.task_id, "permission:git_merge", "merge")
        services.approvals.grant(task.task_id, merge.approval_id, "human")

        result = manager.run(task)
        self.assertEqual(result.state, TaskState.COMPLETED)
        self.assertEqual(state.get("debugger_calls", 0), 1)

    def test_single_agent_escalates_to_needs_human_when_reviewing(self):
        config = load_config()
        # Restrict config to only 1 agent
        single_agent = list(config.agents.keys())[0]
        config.agents = {single_agent: config.agents[single_agent]}
        state = {}
        services, _ = scripted_services(self.root, make_handlers(state), config=config)
        manager = TaskManager(services)
        task = manager.create_task(
            "Single agent task", repo_path=str(self.repo), recipe="feature_implementation"
        )
        plan = services.approvals.request(task.task_id, "approval:plan", "review")
        services.approvals.grant(task.task_id, plan.approval_id, "human")

        result = manager.run(task)
        self.assertEqual(result.state, TaskState.NEEDS_HUMAN)
        self.assertIn("no eligible agent for role", result.message)

    def test_reverifying_is_actually_entered_after_a_fix(self):
        # F-16: REVERIFYING must be a reachable state, not a dead declaration.
        state = {
            "review_script": [
                {"title": "bug", "severity": "high", "description": "bad"}
            ]
        }
        services, _ = scripted_services(self.root, make_handlers(state))
        manager = TaskManager(services)
        task = manager.create_task(
            "Add a feature", repo_path=str(self.repo), recipe="feature_implementation"
        )
        plan = services.approvals.request(task.task_id, "approval:plan", "review")
        services.approvals.grant(task.task_id, plan.approval_id, "human")
        commit = services.approvals.request(task.task_id, "permission:git_commit", "commit")
        services.approvals.grant(task.task_id, commit.approval_id, "human")
        merge = services.approvals.request(task.task_id, "permission:git_merge", "merge")
        services.approvals.grant(task.task_id, merge.approval_id, "human")

        result = manager.run(task)
        self.assertTrue(result.success, result.message)
        states = [
            e.get("status")
            for e in services.audit.events(task.task_id)
            if e["event"] == "task_state_changed"
        ]
        self.assertIn("reverifying", states)

    def test_recipe_step_routes_to_its_declared_role(self):
        # F-16: roles come from the recipe, never hard-coded per step kind.
        from nao.workflow.recipes import get_recipe

        recipe = get_recipe("feature_implementation")
        roles = {s.kind: s.role for s in recipe.steps if s.role}
        self.assertEqual(roles["analyze"], "architect")
        self.assertEqual(roles["implement"], "implementer")
        self.assertEqual(roles["review"], "reviewer")
        self.assertEqual(roles["validate"], "final_verifier")
        self.assertEqual(get_recipe("bug_fix").steps[0].role, "debugger")


class MalformedReviewerTests(unittest.TestCase):
    """F-03: unparseable reviewer output can never become 'no findings'."""

    def setUp(self):
        self.root = temp_dir()
        self.repo = init_repo(self.root / "repo")

    def test_malformed_reviewer_output_is_not_no_findings(self):
        handlers = make_handlers({})
        handlers[Role.REVIEWER] = lambda request: make_response(
            request, text="I skimmed the diff; it looks fine to me."
        )
        services, _ = scripted_services(self.root, handlers)
        manager = TaskManager(services)
        task = manager.create_task(
            "Add a feature", repo_path=str(self.repo), recipe="feature_implementation"
        )
        plan = services.approvals.request(task.task_id, "approval:plan", "review")
        services.approvals.grant(task.task_id, plan.approval_id, "human")
        commit = services.approvals.request(task.task_id, "permission:git_commit", "commit")
        services.approvals.grant(task.task_id, commit.approval_id, "human")
        merge = services.approvals.request(task.task_id, "permission:git_merge", "merge")
        services.approvals.grant(task.task_id, merge.approval_id, "human")

        result = manager.run(task)
        self.assertEqual(result.state, TaskState.NEEDS_HUMAN, result.message)
        self.assertIn("review failed", result.message.lower())
        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
