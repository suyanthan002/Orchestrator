"""Targeted independent-audit remediation tests (F-01, F-03, F-04, F-06).

These tests exercise the four architectural fixes directly and at the
subprocess boundary they are meant to protect.
"""

import os
import sys
import unittest

from nao.config import AgentConfig
from nao.discovery import Executable
from nao.models.agent_contract import AgentRequest, ROLE_OUTPUT_SCHEMAS
from nao.models.enums import Permission, Role
from nao.permissions import (
    analyze_native_arguments,
    contract_from_agent_config,
    default_contract,
)
from nao.launch import LaunchAuthority
from nao.process import (
    AgentLaunchAuthorization,
    InternalProcessService,
    ProcessRunner,
)


#: A structured evidence reference (F-03).
def evidence_ref(kind="test", reference="tests/test_app.py::T::test_value"):
    return {"kind": kind, "reference": reference}
from nao.schema import validate
from nao.adapters import ADAPTER_CLASSES
from nao.adapters.base import BaseCliAdapter
from nao.config import load_config
from tests.support import dummy_agent_config


def make_request(prompt="do the thing", permissions=(), workspace="."):
    return AgentRequest(
        job_id="job-1",
        role=Role.IMPLEMENTER,
        agent="x",
        task_id="task-1",
        prompt=prompt,
        workspace=workspace,
        permissions=tuple(permissions),
    )


class NativeArgumentContractTests(unittest.TestCase):
    """F-01: unknown native flags fail closed; declared flags are honoured."""

    def test_approve_all_tools_with_read_is_rejected(self):
        reason = analyze_native_arguments(
            ["--approve-all-tools"], {Permission.READ}, default_contract()
        )
        self.assertIsNotNone(reason)
        self.assertIn("failing closed", reason)
        self.assertNotIn("is a blanket", reason)

    def test_arbitrary_unknown_permission_like_flag_is_rejected(self):
        for flag in (
            "--grant-everything",
            "--permission-mode=bypass",
            "--allow-tool=all",
            "--no-sandbox",
        ):
            reason = analyze_native_arguments(
                [flag], {Permission.READ}, default_contract()
            )
            self.assertIsNotNone(reason, flag)

    def test_unknown_argument_declared_harmless_is_allowed(self):
        config = dummy_agent_config(
            "declared",
            base_args=["--verify-only"],
            read_only_args=[],
            harmless_args=["--verify-only"],
        )
        contract = contract_from_agent_config("declared", config)
        self.assertIsNone(
            analyze_native_arguments(["--verify-only"], {Permission.READ}, contract)
        )

    def test_unknown_argument_not_declared_harmless_is_rejected(self):
        config = dummy_agent_config("undeclared", base_args=["--verify-only"], read_only_args=[])
        contract = contract_from_agent_config("undeclared", config)
        self.assertIsNotNone(
            analyze_native_arguments(["--verify-only"], {Permission.READ}, contract)
        )

    def test_commit_enabling_flag_cannot_be_smuggled_as_harmless(self):
        # Aider's --auto-commits grants GIT_COMMIT; it must never pass as a
        # harmless/deny token for a write+shell job.
        reason = analyze_native_arguments(
            ["--auto-commits"],
            {Permission.WRITE, Permission.SHELL},
            default_contract(),
        )
        self.assertIsNotNone(reason)
        self.assertIn("git_commit", reason)

    def test_forbidden_argument_is_rejected(self):
        config = dummy_agent_config(
            "forbidding", read_only_args=[], forbidden_args=["--danger"]
        )
        contract = contract_from_agent_config("forbidding", config)
        reason = analyze_native_arguments(["--danger"], {Permission.READ}, contract)
        self.assertIsNotNone(reason)
        self.assertIn("forbidden", reason)

    def test_aider_yes_with_write_only_is_rejected(self):
        adapter = ADAPTER_CLASSES["aider"](
            load_config().agent("aider"), ProcessRunner()
        )
        valid, reason = adapter.validate_permissions((Permission.WRITE,))
        self.assertFalse(valid)
        self.assertIn("--yes", reason)

    def test_cline_blanket_with_write_only_is_rejected(self):
        adapter = ADAPTER_CLASSES["cline"](
            load_config().agent("cline"), ProcessRunner()
        )
        valid, reason = adapter.validate_permissions((Permission.WRITE,))
        self.assertFalse(valid)
        self.assertIn("--auto-approve=true", reason)
        self.assertIn("shell", reason)

    def test_blanket_allowed_only_with_exact_authorized_capability(self):
        adapter = ADAPTER_CLASSES["cline"](
            load_config().agent("cline"), ProcessRunner()
        )
        # Exactly the capability cline's blanket flag is declared to grant.
        valid, reason = adapter.validate_permissions(
            (Permission.WRITE, Permission.SHELL)
        )
        self.assertTrue(valid, reason)
        # A superset is also fine: every granted capability is authorized.
        valid_super, _ = adapter.validate_permissions(
            (Permission.READ, Permission.WRITE, Permission.SHELL)
        )
        self.assertTrue(valid_super)

    def test_normal_read_only_cline_plan_invocation_is_allowed(self):
        exe = Executable(("test-agent",), "unit-test")
        adapter = ADAPTER_CLASSES["cline"](
            load_config().agent("cline"), ProcessRunner()
        )
        req = make_request(permissions=[Permission.READ, Permission.SEARCH, Permission.GIT_READ])
        argv = adapter.build_command(req, exe)
        self.assertIn("--plan", argv)
        self.assertIsNone(
            analyze_native_arguments(argv, req.permissions, adapter.command_contract())
        )


class PermissionBoundaryTests(unittest.TestCase):
    """F-01: the final subprocess boundary cannot be bypassed."""

    def setUp(self):
        self.authority = LaunchAuthority()
        self.runner = ProcessRunner(self.authority)
        self.internal = InternalProcessService(self.authority, self.runner)

    def test_direct_launch_without_authorization_is_denied(self):
        result = self.runner.run(
            ["cline", "--dangerously-skip-permissions"],
            cwd=".",
            timeout=5,
        )
        self.assertEqual(result.exit_code, None)
        self.assertIn("LAUNCH_DENIED", result.error or "")

    def test_agent_authorization_cannot_be_fabricated(self):
        # Direct construction is impossible: only the authority mints grants.
        with self.assertRaises(TypeError):
            AgentLaunchAuthorization(
                agent="custom",
                permissions=frozenset({Permission.READ}),
                contract=default_contract("custom"),
            )

    def test_internal_authorization_cannot_be_fabricated(self):
        from nao.process import InternalLaunchAuthorization

        with self.assertRaises(TypeError):
            InternalLaunchAuthorization(purpose="tests")

    def test_self_minted_grant_is_rejected_by_the_boundary(self):
        # A caller that fabricates its *own* authority still cannot pass the
        # real runner: the grant's authority is not the runner's authority.
        rogue = LaunchAuthority()
        forged = rogue.issue_agent_grant(
            agent="cline",
            permissions=frozenset({Permission.WRITE, Permission.SHELL}),
            contract=default_contract("cline"),
        )
        result = self.runner.run(
            ["cline", "--auto-approve=true"],
            cwd=".",
            timeout=5,
            authorization=forged,
        )
        self.assertEqual(result.exit_code, None)
        self.assertIn("LAUNCH_DENIED", result.error or "")

    def test_agent_launch_is_revalidated_at_the_boundary(self):
        authorization = self.authority.issue_agent_grant(
            agent="custom",
            permissions=frozenset({Permission.READ}),
            contract=default_contract("custom"),
        )
        result = self.runner.run(
            ["cline", "--dangerously-skip-permissions"],
            cwd=".",
            timeout=5,
            authorization=authorization,
        )
        self.assertEqual(result.exit_code, None)
        self.assertIn("LAUNCH_DENIED", result.error or "")

    def test_custom_adapter_cannot_bypass_the_boundary(self):
        adapter = BaseCliAdapter(dummy_agent_config("rogue"), self.runner)
        # A custom adapter that reaches for ProcessRunner directly, handing it a
        # dangerous argv without a valid authorization, must not launch.
        result = adapter.runner.run(
            ["rogue-agent", "--auto-approve=true"],
            cwd=".",
            timeout=5,
        )
        self.assertEqual(result.exit_code, None)
        self.assertIn("LAUNCH_DENIED", result.error or "")

    def test_harmless_declared_agent_launch_passes_the_boundary(self):
        authorization = self.authority.issue_agent_grant(
            agent="cline",
            permissions=frozenset({Permission.READ}),
            contract=contract_from_agent_config(
                "cline", load_config().agent("cline")
            ),
            job_id="job-1",
            task_id="task-1",
        )
        result = self.runner.run(
            ["nao-not-a-real-agent-xyz", "--json", "--plan"],
            cwd=".",
            timeout=5,
            authorization=authorization,
            agent="cline",
            job_id="job-1",
            task_id="task-1",
        )
        # It is allowed through the boundary (failure is "not found", not a denial).
        self.assertNotIn("LAUNCH_DENIED", result.error or "")

    def test_internal_launch_executes(self):
        result = self.internal.run(
            [sys.executable, "-c", "print('ok')"],
            cwd=".",
            timeout=30,
            purpose="test",
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), "ok")

    def test_adapter_cannot_access_launch_grant_minting_authority(self):
        adapter = BaseCliAdapter(dummy_agent_config("rogue"), self.runner)
        self.assertFalse(hasattr(adapter.runner, "_authority"))
        self.assertFalse(hasattr(adapter.runner, "issue_agent_grant"))
        self.assertFalse(hasattr(adapter.runner, "issue_internal"))
        self.assertTrue(hasattr(adapter.runner, "_verifier"))
        self.assertFalse(hasattr(adapter.runner._verifier, "_authority"))
        self.assertFalse(hasattr(adapter.runner._verifier, "issue_agent_grant"))
        self.assertFalse(hasattr(adapter.runner._verifier, "issue_internal"))

    def test_forged_grant_via_dataclass_replace_is_denied(self):
        import dataclasses
        valid_grant = self.authority.issue_agent_grant(
            agent="cline",
            permissions=frozenset({Permission.READ}),
            contract=default_contract("cline"),
            job_id="job-1",
            task_id="task-1",
        )
        forged_grant = dataclasses.replace(
            valid_grant,
            permissions=frozenset({Permission.READ, Permission.WRITE, Permission.SHELL}),
        )
        result = self.runner.run(
            ["cline", "--auto-approve=true"],
            cwd=".",
            timeout=5,
            authorization=forged_grant,
            agent="cline",
            job_id="job-1",
            task_id="task-1",
        )
        self.assertEqual(result.exit_code, None)
        self.assertIn("LAUNCH_DENIED", result.error or "")

    def test_forged_contract_via_dataclass_replace_is_denied(self):
        import dataclasses
        from nao.permissions import NativeArgumentContract
        valid_grant = self.authority.issue_agent_grant(
            agent="cline",
            permissions=frozenset({Permission.READ}),
            contract=default_contract("cline"),
            job_id="job-1",
            task_id="task-1",
        )
        forged_contract = NativeArgumentContract(
            agent="cline",
            harmless=frozenset({"--allow-tool=write"}),
        )
        forged_grant = dataclasses.replace(
            valid_grant,
            contract=forged_contract,
        )
        result = self.runner.run(
            ["cline", "--allow-tool=write"],
            cwd=".",
            timeout=5,
            authorization=forged_grant,
            agent="cline",
            job_id="job-1",
            task_id="task-1",
        )
        self.assertEqual(result.exit_code, None)
        self.assertIn("LAUNCH_DENIED", result.error or "")

    def test_internal_authorization_cannot_launch_agent(self):
        internal_auth = self.internal._authority.issue_internal("test")
        result = self.runner.run(
            ["cline", "--auto-approve=true"],
            cwd=".",
            timeout=5,
            authorization=internal_auth,
        )
        self.assertEqual(result.exit_code, None)
        self.assertIn("LAUNCH_DENIED: internal authorization cannot authorize agent execution", result.error or "")

        result2 = self.runner.run(
            [sys.executable, "-c", "print(1)"],
            cwd=".",
            timeout=5,
            authorization=internal_auth,
            agent="cline",
        )
        self.assertEqual(result2.exit_code, None)
        self.assertIn("LAUNCH_DENIED: internal authorization cannot authorize agent execution", result2.error or "")

    def test_internal_authorization_cannot_launch_unknown_executable(self):
        internal_auth = self.internal._authority.issue_internal("test")
        result = self.runner.run(
            ["untrusted-custom-binary-xyz", "--danger"],
            cwd=".",
            timeout=5,
            authorization=internal_auth,
        )
        self.assertEqual(result.exit_code, None)
        self.assertIn("LAUNCH_DENIED: internal authorization cannot launch unknown or custom executable", result.error or "")

    def test_omitted_identity_is_rejected(self):
        grant = self.authority.issue_agent_grant(
            agent="cline",
            permissions=frozenset({Permission.READ}),
            contract=default_contract("cline"),
            job_id="job-1",
            task_id="task-1",
        )
        # Omitted agent identity
        result1 = self.runner.run(
            ["cline", "--json"],
            cwd=".",
            timeout=5,
            authorization=grant,
            agent=None,
            job_id="job-1",
            task_id="task-1",
        )
        self.assertEqual(result1.exit_code, None)
        self.assertIn("LAUNCH_DENIED: agent launches require explicit agent binding", result1.error or "")

        # Omitted job_id
        result2 = self.runner.run(
            ["cline", "--json"],
            cwd=".",
            timeout=5,
            authorization=grant,
            agent="cline",
            job_id=None,
            task_id="task-1",
        )
        self.assertEqual(result2.exit_code, None)
        self.assertIn("LAUNCH_DENIED: agent launches require explicit job_id binding", result2.error or "")

        # Omitted task_id
        result3 = self.runner.run(
            ["cline", "--json"],
            cwd=".",
            timeout=5,
            authorization=grant,
            agent="cline",
            job_id="job-1",
            task_id=None,
        )
        self.assertEqual(result3.exit_code, None)
        self.assertIn("LAUNCH_DENIED: agent launches require explicit task_id binding", result3.error or "")

    def test_final_argv_mutation_is_rejected(self):
        grant = self.authority.issue_agent_grant(
            agent="cline",
            permissions=frozenset({Permission.READ}),
            contract=default_contract("cline"),
            job_id="job-1",
            task_id="task-1",
        )
        result = self.runner.run(
            ["cline", "--auto-approve=true"],
            cwd=".",
            timeout=5,
            authorization=grant,
            agent="cline",
            job_id="job-1",
            task_id="task-1",
        )
        self.assertEqual(result.exit_code, None)
        self.assertIn("LAUNCH_DENIED", result.error or "")

    def test_cross_job_reuse_is_rejected(self):
        grant = self.authority.issue_agent_grant(
            agent="cline",
            permissions=frozenset({Permission.READ}),
            contract=default_contract("cline"),
            job_id="job-A",
            task_id="task-1",
        )
        result = self.runner.run(
            ["cline", "--json"],
            cwd=".",
            timeout=5,
            authorization=grant,
            agent="cline",
            job_id="job-B",
            task_id="task-1",
        )
        self.assertEqual(result.exit_code, None)
        self.assertIn("LAUNCH_DENIED: grant job mismatch", result.error or "")

    def test_cross_agent_reuse_is_rejected(self):
        grant = self.authority.issue_agent_grant(
            agent="cline",
            permissions=frozenset({Permission.READ}),
            contract=default_contract("cline"),
            job_id="job-1",
            task_id="task-1",
        )
        result = self.runner.run(
            ["kilo", "--json"],
            cwd=".",
            timeout=5,
            authorization=grant,
            agent="kilo",
            job_id="job-1",
            task_id="task-1",
        )
        self.assertEqual(result.exit_code, None)
        self.assertIn("LAUNCH_DENIED: grant agent mismatch", result.error or "")

    def test_unknown_permission_bearing_flag_fails_closed(self):
        grant = self.authority.issue_agent_grant(
            agent="cline",
            permissions=frozenset({Permission.READ}),
            contract=default_contract("cline"),
            job_id="job-1",
            task_id="task-1",
        )
        result = self.runner.run(
            ["cline", "--secret-unmapped-dangerous-flag"],
            cwd=".",
            timeout=5,
            authorization=grant,
            agent="cline",
            job_id="job-1",
            task_id="task-1",
        )
        self.assertEqual(result.exit_code, None)
        self.assertIn("LAUNCH_DENIED", result.error or "")

    def test_goose_write_shell_remains_rejected(self):
        from nao.adapters.goose import GooseAdapter

        adapter = GooseAdapter(dummy_agent_config("goose"), self.runner)
        ok, reason = adapter.validate_permissions((Permission.WRITE,))
        self.assertFalse(ok)
        self.assertIn("UNSUPPORTED_PERMISSION", reason)

        ok2, reason2 = adapter.validate_permissions((Permission.SHELL,))
        self.assertFalse(ok2)
        self.assertIn("UNSUPPORTED_PERMISSION", reason2)


class VerifierEvidenceTests(unittest.TestCase):
    """F-03: the verifier schema and evidence contract."""

    def setUp(self):
        self.schema = ROLE_OUTPUT_SCHEMAS[Role.FINAL_VERIFIER]

    def _valid(self, instance):
        return validate(instance, self.schema) == []

    def test_minimal_valid_verifier_output(self):
        self.assertTrue(
            self._valid(
                {
                    "passed": False,
                    "criteria_results": [
                        {
                            "criterion": "x",
                            "passed": False,
                            "evidence": evidence_ref(),
                        }
                    ],
                }
            )
        )

    def test_evidence_must_be_a_structured_reference(self):
        # F-03: arbitrary prose is not evidence.
        self.assertFalse(
            self._valid(
                {
                    "passed": False,
                    "criteria_results": [
                        {"criterion": "x", "passed": False, "evidence": "looks fine"}
                    ],
                }
            )
        )
        self.assertFalse(
            self._valid(
                {
                    "passed": False,
                    "criteria_results": [
                        {
                            "criterion": "x",
                            "passed": False,
                            "evidence": {"kind": "vibes", "reference": "trust me"},
                        }
                    ],
                }
            )
        )

    def test_passing_verifier_requires_criteria_results(self):
        self.assertFalse(self._valid({"passed": True}))

    def test_missing_required_field_fails(self):
        self.assertFalse(
            self._valid({"criteria_results": []})
        )

    def test_wrong_type_fails(self):
        self.assertFalse(self._valid({"passed": "yes", "criteria_results": []}))

    def test_unexpected_property_fails(self):
        self.assertFalse(
            self._valid(
                {
                    "passed": False,
                    "criteria_results": [
                        {
                            "criterion": "x",
                            "passed": False,
                            "evidence": evidence_ref(),
                        }
                    ],
                    "unexpected": "x",
                }
            )
        )

    def test_criteria_missing_evidence_fails(self):
        self.assertFalse(
            self._valid(
                {
                    "passed": True,
                    "criteria_results": [{"criterion": "x", "passed": True}],
                }
            )
        )

    def test_empty_criteria_results_fails(self):
        self.assertFalse(self._valid({"passed": True, "criteria_results": []}))

    def test_criteria_mismatch_fails(self):
        from types import SimpleNamespace

        from nao.workflow.engine import WorkflowEngine

        task = SimpleNamespace(
            acceptance_criteria=["Reject illegal moves", "Keep existing model"]
        )
        reason = WorkflowEngine._verify_evidence(
            [
                {
                    "criterion": "Reject illegal moves",
                    "passed": True,
                    "evidence": evidence_ref(),
                }
            ],
            task,
        )
        self.assertTrue(reason)
        self.assertIn("Keep existing model", reason)

    def test_criteria_covered_and_backed_passes(self):
        from types import SimpleNamespace

        from nao.workflow.engine import WorkflowEngine

        task = SimpleNamespace(acceptance_criteria=["Reject illegal moves"])
        reason = WorkflowEngine._verify_evidence(
            [
                {
                    "criterion": "Reject illegal moves",
                    "passed": True,
                    "evidence": evidence_ref(),
                }
            ],
            task,
        )
        self.assertEqual(reason, "")

    def test_two_distinct_exact_criteria_passes(self):
        from types import SimpleNamespace
        from nao.workflow.engine import WorkflowEngine

        task = SimpleNamespace(
            acceptance_criteria=["Reject illegal moves", "Keep existing model"]
        )
        reason = WorkflowEngine._verify_evidence(
            [
                {
                    "criterion": "Reject illegal moves",
                    "passed": True,
                    "evidence": evidence_ref(reference="tests/test_moves.py"),
                },
                {
                    "criterion": "Keep existing model",
                    "passed": True,
                    "evidence": evidence_ref(reference="tests/test_model.py"),
                },
            ],
            task,
        )
        self.assertEqual(reason, "")

    def test_extra_verifier_criterion_fails(self):
        from types import SimpleNamespace
        from nao.workflow.engine import WorkflowEngine

        task = SimpleNamespace(acceptance_criteria=["Reject illegal moves"])
        reason = WorkflowEngine._verify_evidence(
            [
                {
                    "criterion": "Reject illegal moves",
                    "passed": True,
                    "evidence": evidence_ref(),
                },
                {
                    "criterion": "Extra unrequested criterion",
                    "passed": True,
                    "evidence": evidence_ref(),
                },
            ],
            task,
        )
        self.assertTrue(reason)
        self.assertIn("not task acceptance criteria", reason)

    def test_duplicate_verifier_criterion_fails(self):
        from types import SimpleNamespace
        from nao.workflow.engine import WorkflowEngine

        task = SimpleNamespace(acceptance_criteria=["Reject illegal moves"])
        reason = WorkflowEngine._verify_evidence(
            [
                {
                    "criterion": "Reject illegal moves",
                    "passed": True,
                    "evidence": evidence_ref(),
                },
                {
                    "criterion": "Reject illegal moves",
                    "passed": True,
                    "evidence": evidence_ref(),
                },
            ],
            task,
        )
        self.assertTrue(reason)
        self.assertIn("duplicate criterion", reason)

    def test_substring_criterion_fails(self):
        from types import SimpleNamespace
        from nao.workflow.engine import WorkflowEngine

        task = SimpleNamespace(acceptance_criteria=["Reject illegal moves"])
        reason = WorkflowEngine._verify_evidence(
            [
                {
                    "criterion": "Reject illegal",
                    "passed": True,
                    "evidence": evidence_ref(),
                }
            ],
            task,
        )
        self.assertTrue(reason)
        self.assertIn("did not report a result for acceptance criterion", reason)

    def test_blank_evidence_fails(self):
        from types import SimpleNamespace
        from nao.workflow.engine import WorkflowEngine

        task = SimpleNamespace(acceptance_criteria=["Reject illegal moves"])
        reason = WorkflowEngine._verify_evidence(
            [
                {
                    "criterion": "Reject illegal moves",
                    "passed": True,
                    "evidence": {"kind": "test", "reference": "   "},
                }
            ],
            task,
        )
        self.assertTrue(reason)
        self.assertIn("invalid evidence", reason)

    def test_nonexistent_artifact_reference_fails(self):
        from types import SimpleNamespace
        from nao.artifacts import ArtifactManager
        from nao.workflow.engine import WorkflowEngine
        from tests.support import temp_dir

        tmp = temp_dir()
        artifacts = ArtifactManager(tmp / "runs")
        task = SimpleNamespace(
            task_id="task-123",
            acceptance_criteria=["Reject illegal moves"],
            workspace=str(tmp / "ws"),
            repo_path=str(tmp / "ws"),
        )
        reason = WorkflowEngine._verify_evidence(
            [
                {
                    "criterion": "Reject illegal moves",
                    "passed": True,
                    "evidence": {"kind": "test", "reference": "tests/nonexistent_test.json"},
                }
            ],
            task,
            artifacts=artifacts,
            workspace=str(tmp / "ws"),
        )
        self.assertTrue(reason)
        self.assertIn("nonexistent test artifact reference", reason)

    def test_foreign_job_artifact_reference_fails(self):
        from types import SimpleNamespace
        from nao.artifacts import ArtifactManager
        from nao.workflow.engine import WorkflowEngine
        from tests.support import temp_dir

        tmp = temp_dir()
        artifacts = ArtifactManager(tmp / "runs")
        task = SimpleNamespace(
            task_id="task-current",
            acceptance_criteria=["Reject illegal moves"],
            workspace=str(tmp / "ws"),
            repo_path=str(tmp / "ws"),
        )
        reason = WorkflowEngine._verify_evidence(
            [
                {
                    "criterion": "Reject illegal moves",
                    "passed": True,
                    "evidence": {"kind": "test", "reference": "runs/task-foreign/tests/result.json"},
                }
            ],
            task,
            artifacts=artifacts,
        )
        self.assertTrue(reason)
        self.assertIn("foreign-task", reason)

    def test_duplicate_task_criteria_rejects(self):
        from nao.models.task import Task

        task = Task(
            task_id="t-1",
            acceptance_criteria=["Reject illegal moves", "Reject illegal moves"],
        )
        with self.assertRaises(ValueError) as ctx:
            task.validate()
        self.assertIn("duplicate acceptance criterion", str(ctx.exception))

    def test_contradictory_task_criteria_rejects(self):
        from nao.models.task import Task

        task = Task(
            task_id="t-1",
            acceptance_criteria=["Reject illegal moves", "Allow illegal moves"],
        )
        with self.assertRaises(ValueError) as ctx:
            task.validate()
        self.assertIn("contradictory acceptance criteria", str(ctx.exception))

    def test_reviewer_unexpected_property_fails(self):
        from nao.models.agent_contract import AgentResponse
        from nao.review_manager import ReviewManager

        manager = ReviewManager()
        response = AgentResponse(
            job_id="j",
            agent="reviewer",
            structured_output={"findings": [], "unexpected": "x"},
        )
        parsed = manager.parse(response, role=Role.REVIEWER)
        self.assertFalse(parsed.valid)

    def test_reviewer_invalid_severity_fails(self):
        from nao.models.agent_contract import AgentResponse
        from nao.review_manager import ReviewManager

        manager = ReviewManager()
        response = AgentResponse(
            job_id="j",
            agent="reviewer",
            structured_output={
                "findings": [{"title": "x", "severity": "apocalyptic"}]
            },
        )
        parsed = manager.parse(response, role=Role.REVIEWER)
        self.assertFalse(parsed.valid)


class EnvironmentPolicyTests(unittest.TestCase):
    """F-06: no caller can hand a subprocess an unsanitized environment or resolve secrets."""

    _PROBE = (
        "import os, json; "
        "print(json.dumps({k: os.environ.get(k) for k in "
        "['NAO_INJECTED_API_KEY', 'NAO_INJECTED_TOKEN', 'NAO_SAFE_VAR', "
        "'PYTHONPATH', 'NAO_REF_SECRET', 'TARGET_VAR', 'MY_PATH', 'MY_PP', 'MY_NP']}))"
    )

    def setUp(self):
        authority = LaunchAuthority()
        self.runner = ProcessRunner(authority.verifier)
        self.internal = InternalProcessService(authority, self.runner)

    def _run_with_env(self, env):
        import json

        result = self.internal.run(
            [sys.executable, "-c", self._PROBE],
            cwd=".",
            timeout=30,
            env=env,
            purpose="test",
        )
        self.assertEqual(result.exit_code, 0, result.stderr)
        return json.loads(result.stdout.strip())

    def test_caller_cannot_inject_raw_secret(self):
        values = self._run_with_env(
            {
                "NAO_INJECTED_API_KEY": "sk-live-abcdefghijklmnop",
                "NAO_INJECTED_TOKEN": "ghp_abcdefghijklmnopqrstuvwxyz",
                "NAO_SAFE_VAR": "hello",
            }
        )
        self.assertIsNone(values["NAO_INJECTED_API_KEY"])
        self.assertIsNone(values["NAO_INJECTED_TOKEN"])
        self.assertEqual(values["NAO_SAFE_VAR"], "hello")

    def test_caller_cannot_override_pythonpath(self):
        values = self._run_with_env({"PYTHONPATH": "/tmp/hijack"})
        self.assertNotEqual(values.get("PYTHONPATH"), "/tmp/hijack")

    def test_env_secret_name_reference_rejected(self):
        os.environ["SECRET_NAME"] = "super-secret-123"
        try:
            values = self._run_with_env({"TARGET_VAR": "${ENV:SECRET_NAME}"})
            self.assertIsNone(values.get("TARGET_VAR"))
        finally:
            os.environ.pop("SECRET_NAME", None)

    def test_env_aws_secret_access_key_reference_rejected(self):
        os.environ["AWS_SECRET_ACCESS_KEY"] = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        try:
            values = self._run_with_env({"TARGET_VAR": "${ENV:AWS_SECRET_ACCESS_KEY}"})
            self.assertIsNone(values.get("TARGET_VAR"))
        finally:
            os.environ.pop("AWS_SECRET_ACCESS_KEY", None)

    def test_env_path_reference_protected(self):
        values = self._run_with_env({"MY_PATH": "${ENV:PATH}"})
        self.assertIsNone(values.get("MY_PATH"))

    def test_env_pythonpath_reference_protected(self):
        os.environ["PYTHONPATH"] = "/custom/pythonpath"
        try:
            values = self._run_with_env({"MY_PP": "${ENV:PythonPath}"})
            self.assertIsNone(values.get("MY_PP"))
        finally:
            os.environ.pop("PYTHONPATH", None)

    def test_env_node_path_reference_protected(self):
        os.environ["NODE_PATH"] = "/custom/nodepath"
        try:
            values = self._run_with_env({"MY_NP": "${ENV:Node_Path}"})
            self.assertIsNone(values.get("MY_NP"))
        finally:
            os.environ.pop("NODE_PATH", None)

    def test_safe_env_reference_resolved(self):
        os.environ["NAO_SAFE_SOURCE_VAR"] = "safe_value_456"
        try:
            values = self._run_with_env({"TARGET_VAR": "${ENV:NAO_SAFE_SOURCE_VAR}"})
            self.assertEqual(values.get("TARGET_VAR"), "safe_value_456")
        finally:
            os.environ.pop("NAO_SAFE_SOURCE_VAR", None)

    def test_casing_variants_of_protected_overrides_rejected(self):
        for path_var in ("path", "PATH", "Path", "pAtH"):
            from nao.env import is_protected_env_name
            self.assertTrue(is_protected_env_name(path_var))

        for py_var in ("PythonPath", "PYTHONPATH", "pythonpath", "Pythonpath"):
            from nao.env import is_protected_env_name
            self.assertTrue(is_protected_env_name(py_var))

        for node_var in ("Node_Path", "NODE_PATH", "node_path", "Node_path"):
            from nao.env import is_protected_env_name
            self.assertTrue(is_protected_env_name(node_var))

    def test_host_secret_not_inherited_when_caller_supplies_env(self):
        os.environ["NAO_HOST_LEAK_SECRET"] = "leak"
        try:
            env = {"NAO_SAFE_VAR": "ok"}
            values = self._run_with_env(env)
            self.assertIsNone(values.get("NAO_HOST_LEAK_SECRET"))
        finally:
            os.environ.pop("NAO_HOST_LEAK_SECRET", None)


if __name__ == "__main__":
    unittest.main()
