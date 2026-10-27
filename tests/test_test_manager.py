import sys
import unittest

from nao.artifacts import ArtifactManager
from nao.audit import AuditLogger
from nao.test_manager import (
    TestManager,
    TestPlan,
    detect_test_runner,
    normalize_test_command,
    parse_test_output,
)
from tests.support import init_repo, temp_dir


class ParseTests(unittest.TestCase):
    def test_pytest_summary(self):
        counts = parse_test_output(
            "5 passed, 1 failed, 2 skipped in 0.5s", "", runner="pytest"
        )
        self.assertEqual(counts["passed"], 5)
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(counts["skipped"], 2)

    def test_unittest_summary_ok(self):
        counts = parse_test_output("Ran 7 tests in 0.01s\n\nOK", "", runner="unittest")
        self.assertEqual(counts["passed"], 7)
        self.assertEqual(counts["failed"], 0)

    def test_unittest_summary_failures(self):
        counts = parse_test_output(
            "Ran 4 tests in 0.02s\n\nFAILED (failures=1, errors=1)",
            "",
            runner="unittest",
        )
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(counts["errors"], 1)
        self.assertEqual(counts["passed"], 2)

    def test_no_tests(self):
        counts = parse_test_output(
            "Ran 0 tests in 0.00s\n\nOK", "", runner="unittest"
        )
        self.assertEqual(counts["passed"], 0)

    def test_unknown_runner_is_not_parsed(self):
        # F-04: without a recognized runner the output is not trusted.
        counts = parse_test_output("Ran 7 tests in 0.01s\n\nOK", "")
        self.assertIsNone(counts["passed"])
        self.assertIsNone(counts["failed"])

    def test_contradictory_duplicate_declaration_is_flagged(self):
        # F-04: "3 passed, 1 failed, 0 failed" must not silently become failed=0.
        counts = parse_test_output("3 passed, 1 failed, 0 failed", "", runner="pytest")
        self.assertTrue(counts["contradictory"])
        self.assertIsNone(counts["failed"])

    def test_detect_runner(self):
        self.assertEqual(
            detect_test_runner(["python", "-m", "pytest", "-q"]), "pytest"
        )
        self.assertEqual(
            detect_test_runner(["python", "-m", "unittest", "discover"]), "unittest"
        )
        self.assertIsNone(detect_test_runner(["python", "-c", "print('x')"]))


class TestManagerTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir()
        self.artifacts = ArtifactManager(self.root / "runs")
        self.audit = AuditLogger(self.root / "audit")
        self.manager = TestManager(self.artifacts, self.audit)

    def test_real_test_run_produces_evidence(self):
        repo = init_repo(self.root / "repo")
        plan = self.manager.default_plan(str(repo), kind="targeted")
        evidence = self.manager.run(plan, task_id="task-t1", job_id="job-1")

        self.assertEqual(evidence.status, "passed")
        self.assertEqual(evidence.exit_code, 0)
        self.assertEqual(evidence.passed, 1)
        self.assertEqual(evidence.failed, 0)
        self.assertGreater(evidence.duration_seconds, 0)

    def test_evidence_is_persisted_and_audited(self):
        repo = init_repo(self.root / "repo")
        plan = self.manager.default_plan(str(repo))
        self.manager.run(plan, task_id="task-t2")
        self.assertEqual(len(self.artifacts.load_tests("task-t2")), 1)
        events = [e["event"] for e in self.audit.events("task-t2")]
        self.assertIn("tests_started", events)
        self.assertIn("tests_completed", events)

    def test_failing_command_is_evidence_of_failure(self):
        plan = TestPlan(
            name="boom",
            command=[sys.executable, "-c", "import sys; sys.exit(3)"],
            cwd=str(self.root),
            kind="regression",
        )
        evidence = self.manager.run(plan, task_id="task-t3")
        self.assertEqual(evidence.status, "failed")
        self.assertEqual(evidence.exit_code, 3)

    def test_missing_command_reports_error(self):
        plan = TestPlan(
            name="missing",
            command=["definitely-not-a-real-binary-xyz"],
            cwd=str(self.root),
        )
        evidence = self.manager.run(plan, task_id="task-t4")
        self.assertEqual(evidence.status, "error")
        self.assertFalse(evidence.ok)

    def test_plan_from_task_uses_configured_command(self):
        plan = self.manager.plan_from_task(
            str(self.root),
            {"test_command": [sys.executable, "-c", "print('hi')"]},
            kind="targeted",
        )
        self.assertEqual(plan.command[0], sys.executable)


class StructuredTestCommandTests(unittest.TestCase):
    """F-14 regression tests: test commands are explicit argv arrays."""

    def setUp(self):
        self.manager = TestManager()

    def test_executable_only(self):
        self.assertEqual(normalize_test_command("pytest"), ["pytest"])

    def test_executable_and_args(self):
        # The critical case: "pytest -q" must NOT become one executable.
        self.assertEqual(
            normalize_test_command("pytest -q"), ["pytest", "-q"]
        )

    def test_structured_list_is_preserved(self):
        self.assertEqual(
            normalize_test_command(["pytest", "-q", "tests/"]),
            ["pytest", "-q", "tests/"],
        )

    def test_quoted_argument_stays_one_token(self):
        parts = normalize_test_command('python -c "print(1)"')
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0], "python")
        self.assertIn("print(1)", parts[2])

    def test_windows_path_argument(self):
        parts = normalize_test_command(
            r"C:\Python312\python.exe -m pytest"
        )
        self.assertEqual(parts[0], r"C:\Python312\python.exe")
        self.assertEqual(parts[1:], ["-m", "pytest"])

    def test_invalid_and_empty_commands(self):
        self.assertEqual(normalize_test_command(None), [])
        self.assertEqual(normalize_test_command(""), [])
        self.assertEqual(normalize_test_command([]), [])

    def test_plan_from_string_command_is_split(self):
        plan = self.manager.plan_from_task(
            str(temp_dir()),
            {"test_command": "pytest -q"},
            kind="targeted",
        )
        self.assertEqual(plan.command, ["pytest", "-q"])


class TestGateEvidenceTests(unittest.TestCase):
    """F-04: a green exit is only evidence when tests actually executed."""

    def setUp(self):
        self.root = temp_dir()
        self.manager = TestManager()

    def _run(self, code, require_tests=True):
        plan = TestPlan(
            name="gate",
            command=[sys.executable, "-c", code],
            cwd=str(self.root),
            require_tests=require_tests,
        )
        return self.manager.run(plan, task_id="gate-task")

    def test_fake_printed_unittest_summary_is_not_trusted(self):
        # F-04: an arbitrary process printing a test-looking summary is not
        # evidence that a test runner executed.
        evidence = self._run("print('Ran 3 tests in 0.1s\\n\\nOK')")
        self.assertEqual(evidence.status, "unknown")
        self.assertFalse(evidence.ok)

    def test_fake_printed_pytest_summary_is_not_trusted(self):
        evidence = self._run("print('5 passed, 0 failed')")
        self.assertEqual(evidence.status, "unknown")
        self.assertFalse(evidence.ok)

    def test_real_supported_runner_output_passes(self):
        repo = init_repo(self.root / "repo")
        plan = self.manager.default_plan(str(repo), kind="targeted")
        evidence = self.manager.run(plan, task_id="gate-real")
        self.assertEqual(evidence.status, "passed")
        self.assertTrue(evidence.ok)

    def test_exit_zero_zero_tests_fails(self):
        empty = self.root / "empty_tests"
        empty.mkdir()
        plan = TestPlan(
            name="empty",
            command=[sys.executable, "-m", "unittest", "discover", "-s", str(empty)],
            cwd=str(self.root),
        )
        evidence = self.manager.run(plan, task_id="gate-empty")
        self.assertEqual(evidence.status, "no_tests")
        self.assertFalse(evidence.ok)

    def test_exit_zero_indeterminate_evidence_fails(self):
        evidence = self._run("print('everything is fine')")
        self.assertEqual(evidence.status, "unknown")
        self.assertFalse(evidence.ok)

    def test_nonzero_exit_fails(self):
        evidence = self._run("import sys; sys.exit(1)")
        self.assertEqual(evidence.status, "failed")
        self.assertFalse(evidence.ok)

    def test_require_tests_false_allows_indeterminate(self):
        evidence = self._run("print('ok')", require_tests=False)
        self.assertEqual(evidence.status, "passed")

    def test_gate_flag_overrides_require_tests_false(self):
        # A workflow verification gate must require meaningful evidence
        # independently of the public TestPlan default.
        plan = TestPlan(
            name="gate-forced",
            command=[sys.executable, "-c", "print('ok')"],
            cwd=str(self.root),
            require_tests=False,
        )
        evidence = self.manager.run(plan, task_id="gate-forced", gate=True)
        self.assertEqual(evidence.status, "unknown")
        self.assertFalse(evidence.ok)

    def test_cancellation_with_exit_zero_never_passes(self):
        from nao.process import ProcessResult

        result = ProcessResult(command=["x"], exit_code=0, cancelled=True)
        status = TestManager._classify(
            result,
            {"passed": 5, "failed": 0, "errors": 0, "contradictory": False},
            True,
        )
        self.assertEqual(status, "cancelled")

    def test_unavailable_test_command_is_error(self):
        plan = TestPlan(
            name="missing", command=["definitely-not-a-real-binary-xyz"], cwd=str(self.root)
        )
        evidence = self.manager.run(plan, task_id="gate-task-2")
        self.assertEqual(evidence.status, "error")
        self.assertFalse(evidence.ok)


class ExitZeroFailureSemanticsTests(unittest.TestCase):
    """F-04: exit 0 with reported failures/errors must NOT pass."""

    def setUp(self):
        self.root = temp_dir()
        self.manager = TestManager()

    def _plan(self, output, exit_code=0):
        # A command that *is* a recognized pytest invocation (its basename is
        # "pytest"), so the pytest-specific grammar is applied to its output.
        script = self.root / "pytest"
        script.write_text(
            f"print({output!r})\nimport sys; sys.exit({exit_code})\n",
            encoding="utf-8",
        )
        return TestPlan(
            name="semantics", command=[sys.executable, str(script)], cwd=str(self.root)
        )

    def _run(self, output, exit_code=0, kind="targeted"):
        plan = self._plan(output, exit_code)
        plan.kind = kind
        return self.manager.run(plan, task_id="semantics")

    def test_exit_zero_two_passed_one_failed_is_failed(self):
        evidence = self._run("2 passed, 1 failed in 0.1s")
        self.assertEqual(evidence.exit_code, 0)
        self.assertEqual(evidence.passed, 2)
        self.assertEqual(evidence.failed, 1)
        self.assertEqual(evidence.status, "failed")
        self.assertFalse(evidence.ok)

    def test_exit_zero_two_passed_one_error_is_failed(self):
        evidence = self._run("2 passed, 1 error in 0.1s")
        self.assertEqual(evidence.exit_code, 0)
        self.assertEqual(evidence.errors, 1)
        self.assertEqual(evidence.status, "failed")
        self.assertFalse(evidence.ok)

    def test_exit_zero_clean_run_passes(self):
        evidence = self._run("2 passed, 0 failed, 0 errors in 0.1s")
        self.assertEqual(evidence.status, "passed")
        self.assertTrue(evidence.ok)

    def test_exit_zero_zero_tests_is_no_tests(self):
        evidence = self._run("no tests ran in 0.0s")
        self.assertEqual(evidence.status, "no_tests")
        self.assertFalse(evidence.ok)

    def test_exit_zero_unknown_evidence_is_unknown(self):
        evidence = self._run("everything looks fine to me")
        self.assertEqual(evidence.status, "unknown")
        self.assertFalse(evidence.ok)

    def test_contradictory_summary_never_passes(self):
        # F-04: conflicting duplicate declarations are never a pass.
        evidence = self._run("3 passed, 1 failed, 0 failed")
        self.assertEqual(evidence.status, "unknown")
        self.assertFalse(evidence.ok)

    def test_reverification_uses_the_same_semantics(self):
        # The same classification applies to regression/re-verification runs.
        evidence = self._run("2 passed, 1 failed", kind="regression")
        self.assertEqual(evidence.kind, "regression")
        self.assertEqual(evidence.status, "failed")
        self.assertFalse(evidence.ok)


if __name__ == "__main__":
    unittest.main()
