"""Test Manager (plan.md section 15).

Tests are evidence, not assurances. NAO runs the command itself and records the
facts: command, working directory, exit code, duration, stdout, stderr and
parsed pass/fail counts. An agent's claim that "tests passed" is never treated
as sufficient evidence.

F-04 hardening: a green exit code is necessary but never sufficient. Evidence is
only trusted when the *command identifies a supported test runner* (unittest or
pytest) whose specific summary grammar is present, the summary is internally
consistent, the run was not cancelled, and at least one test actually executed.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
import shlex
from typing import Any, Optional

from .artifacts import ArtifactManager
from .audit import AuditLogger
from .launch import LaunchAuthority
from .models.evidence import TestEvidence
from .models.events import EventType
from .process import InternalProcessService, ProcessRunner


def normalize_test_command(command: Any) -> list[str]:
    """Normalize test commands into an explicit structured argument array (plan.md section 15 / F-14)."""
    if isinstance(command, (list, tuple)):
        return [str(part) for part in command if str(part).strip()]
    elif isinstance(command, str):
        command_str = command.strip()
        if not command_str:
            return []
        try:
            return shlex.split(command_str, posix=False)
        except ValueError:
            return command_str.split()
    return []


@dataclass
class TestPlan:
    name: str
    command: list[str]
    cwd: str
    kind: str = "project"  # targeted | project | regression | final
    scope: Optional[str] = None
    timeout_seconds: float = 600.0
    #: When True (the default), a green exit code is only *evidence* if the
    #: output shows that at least one test actually executed (F-04).
    require_tests: bool = True
    runner: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "command": list(self.command),
            "cwd": self.cwd,
            "kind": self.kind,
            "scope": self.scope,
            "timeout_seconds": self.timeout_seconds,
            "require_tests": self.require_tests,
            "runner": self.runner,
        }


# -- runner identification --------------------------------------------------

_EXE_SUFFIXES = (".exe", ".cmd", ".bat", ".py")
_PYTHON_NAMES = ("python", "python3", "py", "pypy", "pypy3")


def detect_test_runner(command: Any) -> Optional[str]:
    """Identify the supported test runner a command invokes, or ``None``.

    The runner is verified strictly from the command invocation, not from loose
    argument matching. Only ``unittest`` and ``pytest`` are supported.
    """
    tokens = [str(token) for token in (command or [])]
    if not tokens:
        return None
    name = Path(tokens[0]).name.lower()
    for suffix in _EXE_SUFFIXES:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    if name == "pytest":
        return "pytest"
    if name == "unittest":
        return "unittest"
    if name in _PYTHON_NAMES and len(tokens) >= 2:
        if tokens[1] == "-m" and len(tokens) >= 3:
            module = tokens[2].strip().lower()
            if module in ("pytest", "unittest"):
                return module
        else:
            script_name = Path(tokens[1]).name.lower()
            for suffix in _EXE_SUFFIXES:
                if script_name.endswith(suffix):
                    script_name = script_name[:-len(suffix)]
                    break
            if script_name in ("pytest", "unittest"):
                return script_name
    return None


# -- output parsing ---------------------------------------------------------

_PYTEST_SUMMARY = re.compile(
    r"(?P<count>\d+)\s+(?P<label>passed|failed|error|errors|skipped|xfailed|xpassed)"
)
_PYTEST_NO_TESTS = re.compile(r"no tests ran", re.IGNORECASE)
_UNITTEST_RAN = re.compile(r"Ran\s+(?P<count>\d+)\s+tests?", re.IGNORECASE)
_UNITTEST_FAILURES = re.compile(r"failures=(?P<count>\d+)", re.IGNORECASE)
_UNITTEST_ERRORS = re.compile(r"errors=(?P<count>\d+)", re.IGNORECASE)
_UNITTEST_OK = re.compile(r"^OK\b", re.IGNORECASE | re.MULTILINE)


def parse_test_output(
    stdout: str,
    stderr: str,
    runner: Optional[str] = None,
) -> dict[str, Any]:
    """Extract pass/fail counts using the *runner-specific* grammar.

    Returns a counts dict keyed ``passed``/``failed``/``errors``/``skipped`` (each
    ``Optional[int]``) plus a ``contradictory`` flag. When ``runner`` is not a
    supported runner the counts stay ``None`` (untrusted output), and when the
    same label is declared with two different values the result is marked
    contradictory so it can never be reported as a clean pass.
    """
    combined = f"{stdout}\n{stderr}"
    counts: dict[str, Any] = {
        "passed": None,
        "failed": None,
        "errors": None,
        "skipped": None,
        "contradictory": False,
    }
    if runner == "pytest":
        _parse_pytest(combined, counts)
    elif runner == "unittest":
        _parse_unittest(combined, counts)
    return counts


def _apply_unique(counts: dict[str, Any], label: str, values: set[int]) -> None:
    if len(values) > 1:
        # Conflicting duplicates: never overwrite; mark as untrustworthy.
        counts["contradictory"] = True
        counts[label] = None
    else:
        counts[label] = next(iter(values))


def _parse_pytest(combined: str, counts: dict[str, Any]) -> None:
    grouped: dict[str, set[int]] = {}
    for match in _PYTEST_SUMMARY.finditer(combined):
        label = match.group("label")
        if label in ("error", "errors"):
            label = "errors"
        if label in ("xfailed", "xpassed"):
            continue
        grouped.setdefault(label, set()).add(int(match.group("count")))
    if not grouped:
        if _PYTEST_NO_TESTS.search(combined):
            counts["passed"] = 0
            counts["failed"] = 0
            counts["errors"] = 0
        return
    for label, values in grouped.items():
        _apply_unique(counts, label, values)


def _parse_unittest(combined: str, counts: dict[str, Any]) -> None:
    ran = [int(m.group("count")) for m in _UNITTEST_RAN.finditer(combined)]
    failures = [int(m.group("count")) for m in _UNITTEST_FAILURES.finditer(combined)]
    errors = [int(m.group("count")) for m in _UNITTEST_ERRORS.finditer(combined)]

    if not ran:
        if "no tests ran" in combined.lower():
            counts["passed"] = 0
            counts["failed"] = 0
            counts["errors"] = 0
        return

    if len(set(ran)) > 1:
        counts["contradictory"] = True
        return
    total = ran[0]

    if total == 0:
        counts["passed"] = 0
        counts["failed"] = 0
        counts["errors"] = 0
        return

    failure_value: Optional[int] = None
    if failures:
        if len(set(failures)) > 1:
            counts["contradictory"] = True
        else:
            failure_value = failures[0]

    error_value: Optional[int] = None
    if errors:
        if len(set(errors)) > 1:
            counts["contradictory"] = True
        else:
            error_value = errors[0]

    has_ok = bool(_UNITTEST_OK.search(combined))
    if has_ok:
        if (failure_value is not None and failure_value > 0) or (error_value is not None and error_value > 0):
            # Contradictory: OK summary with nonzero failures/errors
            counts["contradictory"] = True
            return
        if failure_value is None:
            failure_value = 0
        if error_value is None:
            error_value = 0
    else:
        if failure_value is None and error_value is None:
            counts["contradictory"] = True
            return

    if counts["contradictory"]:
        return

    failures_count = failure_value or 0
    errors_count = error_value or 0
    counts["failed"] = failures_count
    counts["errors"] = errors_count
    counts["passed"] = max(total - failures_count - errors_count, 0)


class TestManager:
    def __init__(
        self,
        artifacts: Optional[ArtifactManager] = None,
        audit: Optional[AuditLogger] = None,
        runner: Optional[ProcessRunner] = None,
        internal: Optional[InternalProcessService] = None,
    ) -> None:
        self.artifacts = artifacts
        self.audit = audit
        if internal is not None:
            self.internal = internal
            self.runner = runner or internal._runner
        else:
            authority = LaunchAuthority()
            self.runner = runner or ProcessRunner(authority.verifier)
            self.internal = InternalProcessService(authority, self.runner)

    # -- planning ----------------------------------------------------------
    def default_plan(
        self,
        repo_path: str,
        kind: str = "project",
        scope: Optional[str] = None,
    ) -> TestPlan:
        """Choose a reasonable test command for the repository."""
        root = Path(repo_path)
        if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists():
            command = [sys.executable, "-m", "pytest", "-q"]
            runner = "pytest"
        else:
            command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
            runner = "unittest"
        if scope:
            command.append(scope)
        return TestPlan(
            name=f"{kind}-tests",
            command=command,
            cwd=str(root),
            kind=kind,
            scope=scope,
            runner=runner,
        )

    def plan_from_task(
        self, repo_path: str, task_metadata: dict, kind: str, scope: Optional[str] = None
    ) -> TestPlan:
        configured = task_metadata.get("test_command")
        configured_runner = task_metadata.get("test_runner")
        if configured:
            command = normalize_test_command(configured)
            if not command:
                return self.default_plan(repo_path, kind=kind, scope=scope)
            runner = configured_runner or detect_test_runner(command)
            return TestPlan(
                name=f"{kind}-tests",
                command=command,
                cwd=task_metadata.get("test_cwd") or repo_path,
                kind=kind,
                scope=scope,
                runner=runner,
            )
        return self.default_plan(repo_path, kind=kind, scope=scope)

    # -- execution ---------------------------------------------------------
    def run(
        self,
        plan: TestPlan,
        task_id: str,
        job_id: Optional[str] = None,
        agent: Optional[str] = None,
        gate: bool = False,
    ) -> TestEvidence:
        """Run a test plan and record the evidence.

        ``gate=True`` marks a workflow verification gate: such a gate always
        requires meaningful test evidence, independently of the public
        ``TestPlan.require_tests`` default (F-04).
        """
        if self.audit:
            self.audit.record(
                EventType.TESTS_STARTED,
                task_id=task_id,
                job_id=job_id,
                agent=agent,
                status=plan.kind,
                message=" ".join(plan.command),
                data=plan.to_dict(),
            )

        result = self.internal.run(
            argv=plan.command,
            cwd=plan.cwd,
            timeout=plan.timeout_seconds,
            purpose="tests",
        )

        runner = plan.runner or detect_test_runner(plan.command)
        counts = parse_test_output(result.stdout, result.stderr, runner)
        require_meaningful = True if gate else plan.require_tests
        status = self._classify(result, counts, require_meaningful)

        evidence = TestEvidence(
            command=plan.command,
            working_directory=plan.cwd,
            exit_code=result.exit_code,
            duration_seconds=result.duration_seconds,
            stdout=result.stdout,
            stderr=result.stderr or (result.error or ""),
            passed=counts["passed"],
            failed=counts["failed"],
            errors=counts["errors"],
            skipped=counts["skipped"],
            status=status,
            kind=plan.kind,
            job_id=job_id,
            agent=agent,
            scope=plan.scope,
        )

        if self.artifacts:
            self.artifacts.save_test(task_id, evidence)
        if self.audit:
            self.audit.record(
                EventType.TESTS_COMPLETED,
                task_id=task_id,
                job_id=job_id,
                agent=agent,
                status=status,
                message=f"{evidence.passed} passed, {evidence.failed} failed",
                data={
                    "kind": plan.kind,
                    "runner": runner,
                    "contradictory": counts.get("contradictory", False),
                    "exit_code": evidence.exit_code,
                    "passed": evidence.passed,
                    "failed": evidence.failed,
                    "errors": evidence.errors,
                    "duration_seconds": evidence.duration_seconds,
                },
            )
        return evidence

    @staticmethod
    def _classify(
        result, counts: dict[str, Any], require_meaningful: bool
    ) -> str:
        """Decide the evidence status (F-04).

        A test gate may pass ONLY when the process completed successfully, was
        not cancelled, the runner reported a consistent summary showing that
        meaningful tests actually executed, and there are zero failures and
        zero errors. A zero exit code is necessary but never sufficient.

        Precedence: cancellation > timeout > launch error > contradictory/torn
        summary > non-zero exit > reported failures/errors > (no evidence).
        """
        if result.cancelled:
            # Cancellation always wins over a normal success (F-04).
            return "cancelled"
        if result.timed_out:
            return "timeout"
        if result.error is not None:
            return "error"
        if counts.get("contradictory"):
            # Conflicting duplicate declarations can never be a pass.
            return "unknown"

        failed = counts.get("failed")
        errors = counts.get("errors")
        # Reported failures/errors are a FAIL regardless of the exit code.
        if failed is not None and failed > 0:
            return "failed"
        if errors is not None and errors > 0:
            return "failed"

        counted = [
            value
            for value in (counts.get("passed"), failed, errors)
            if value is not None
        ]
        total = sum(counted) if counted else None
        if total == 0:
            # Explicitly zero executed tests (or skipped-only). unittest exits
            # non-zero ("NO TESTS RAN") for this case, which is not a failure.
            return "passed" if not require_meaningful else "no_tests"
        if result.exit_code != 0:
            return "failed"
        if total is None:
            # No recognizable runner-specific summary in the output.
            return "passed" if not require_meaningful else "unknown"
        return "passed"
