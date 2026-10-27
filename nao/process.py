import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .env import build_sanitized_environment
from .launch import (
    AgentLaunchAuthorization,
    InternalLaunchAuthorization,
    LaunchAuthority,
    LaunchVerifier,
)
from .permissions import (
    NativeArgumentContract,
    analyze_native_arguments,
)

__all__ = [
    "AgentLaunchAuthorization",
    "InternalLaunchAuthorization",
    "InternalProcessService",
    "LaunchAuthority",
    "LaunchVerifier",
    "ProcessResult",
    "ProcessRunner",
]

_KNOWN_AGENT_NAMES: frozenset[str] = frozenset(
    {"aider", "antigravity", "cline", "copilot", "cursor", "goose", "kilo"}
)

_INTERNAL_ALLOWED_EXECUTABLES: frozenset[str] = frozenset(
    {"git", "python", "python3", "py", "pypy", "pypy3", "pytest", "unittest", "taskkill"}
)


def _is_agent_invocation(argv: list[str]) -> bool:
    if not argv:
        return False
    exe_name = Path(argv[0]).name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".py"):
        if exe_name.endswith(suffix):
            exe_name = exe_name[:-len(suffix)]
            break
    if exe_name in _KNOWN_AGENT_NAMES or "agent" in exe_name:
        return True
    return False


def _is_trusted_internal_invocation(argv: list[str]) -> bool:
    if not argv:
        return False
    exe_name = Path(argv[0]).name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".py"):
        if exe_name.endswith(suffix):
            exe_name = exe_name[:-len(suffix)]
            break
    if exe_name in _INTERNAL_ALLOWED_EXECUTABLES:
        return True
    try:
        if Path(argv[0]).resolve() == Path(sys.executable).resolve():
            return True
    except Exception:
        pass
    return False


@dataclass
class ProcessResult:
    command: list[str]
    exit_code: Optional[int]
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    cancelled: bool = field(default=False)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return (
            self.exit_code == 0
            and not self.timed_out
            and not self.cancelled
            and self.error is None
        )


@dataclass
class _Running:
    process: subprocess.Popen
    task_id: Optional[str] = None
    cancelled: bool = field(default=False)


class ProcessRunner:
    """Runs processes with timeout, cancellation and task-scoped tracking.

    This is the single guarded execution path for every NAO subprocess yet it is
    deliberately NOT the security boundary against malicious in-process Python
    (see ``nao.launch``). It *is* the boundary that stops an adapter from
    forwarding a native capability the NAO policy did not authorize:

    * there is no launch without an authorization,
    * the authorization must have been verified by this runner's verifier, so an
      adapter cannot fabricate one or access grant-minting authority, and
      * agent launches are re-analysed here against the *policy-issued* contract
        and permission set carried by the grant, bound to agent/job/task identities.
    """

    def __init__(
        self,
        verifier: Optional[LaunchVerifier | LaunchAuthority] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._running: dict[str, _Running] = {}
        if isinstance(verifier, LaunchAuthority):
            self._verifier = verifier.verifier
        elif isinstance(verifier, LaunchVerifier):
            self._verifier = verifier
        elif verifier is not None and hasattr(verifier, "verify"):
            self._verifier = verifier
        else:
            self._verifier = LaunchAuthority().verifier

    def run(
        self,
        argv: list[str],
        cwd: str,
        timeout: float,
        env: Optional[dict[str, str]] = None,
        job_id: Optional[str] = None,
        task_id: Optional[str] = None,
        authorization: Optional[
            "InternalLaunchAuthorization | AgentLaunchAuthorization"
        ] = None,
        agent: Optional[str] = None,
    ) -> ProcessResult:
        start = time.monotonic()

        # F-01: the FINAL authorization boundary. No launch is permitted without
        # an authorization verified by this runner's verifier. A caller-constructed,
        # mutated (e.g. dataclasses.replace), or self-issued authorization is rejected here.
        if authorization is None or not self._verifier.verify(authorization):
            return ProcessResult(
                command=list(argv),
                exit_code=None,
                error=(
                    "LAUNCH_DENIED: subprocess launch was not authorized by the "
                    "NAO launch authority"
                ),
                duration_seconds=time.monotonic() - start,
            )

        # F-01: Internal authorization can never authorize agent execution or custom/unknown binaries.
        if isinstance(authorization, InternalLaunchAuthorization):
            if agent is not None or _is_agent_invocation(argv):
                return ProcessResult(
                    command=list(argv),
                    exit_code=None,
                    error="LAUNCH_DENIED: internal authorization cannot authorize agent execution",
                    duration_seconds=time.monotonic() - start,
                )
            if not _is_trusted_internal_invocation(argv):
                return ProcessResult(
                    command=list(argv),
                    exit_code=None,
                    error="LAUNCH_DENIED: internal authorization cannot launch unknown or custom executable",
                    duration_seconds=time.monotonic() - start,
                )
        elif isinstance(authorization, AgentLaunchAuthorization):
            # F-01: Mandatory explicit agent, job, and task binding for agent launches
            if not agent:
                return ProcessResult(
                    command=list(argv),
                    exit_code=None,
                    error="LAUNCH_DENIED: agent launches require explicit agent binding",
                    duration_seconds=time.monotonic() - start,
                )
            if not job_id:
                return ProcessResult(
                    command=list(argv),
                    exit_code=None,
                    error="LAUNCH_DENIED: agent launches require explicit job_id binding",
                    duration_seconds=time.monotonic() - start,
                )
            if not task_id:
                return ProcessResult(
                    command=list(argv),
                    exit_code=None,
                    error="LAUNCH_DENIED: agent launches require explicit task_id binding",
                    duration_seconds=time.monotonic() - start,
                )

            # F-01: Cross-agent binding check
            if authorization.agent != agent:
                return ProcessResult(
                    command=list(argv),
                    exit_code=None,
                    error=(
                        f"LAUNCH_DENIED: grant agent mismatch (grant is for '{authorization.agent}', "
                        f"caller requested '{agent}')"
                    ),
                    duration_seconds=time.monotonic() - start,
                )
            # F-01: Cross-job binding check
            if authorization.job_id is not None and authorization.job_id != job_id:
                return ProcessResult(
                    command=list(argv),
                    exit_code=None,
                    error=(
                        f"LAUNCH_DENIED: grant job mismatch (grant is for job '{authorization.job_id}', "
                        f"caller requested '{job_id}')"
                    ),
                    duration_seconds=time.monotonic() - start,
                )
            # F-01: Cross-task binding check
            if authorization.task_id is not None and authorization.task_id != task_id:
                return ProcessResult(
                    command=list(argv),
                    exit_code=None,
                    error=(
                        f"LAUNCH_DENIED: grant task mismatch (grant is for task '{authorization.task_id}', "
                        f"caller requested '{task_id}')"
                    ),
                    duration_seconds=time.monotonic() - start,
                )

            violation = analyze_native_arguments(
                argv,
                authorization.permissions,
                authorization.contract,
            )
            if violation:
                return ProcessResult(
                    command=list(argv),
                    exit_code=None,
                    error=f"LAUNCH_DENIED: {violation}",
                    duration_seconds=time.monotonic() - start,
                )
        else:
            return ProcessResult(
                command=list(argv),
                exit_code=None,
                error="LAUNCH_DENIED: invalid launch authorization type",
                duration_seconds=time.monotonic() - start,
            )

        # F-06: every NAO-controlled subprocess environment passes through the
        # single environment policy. A caller-supplied environment is *merged*
        # with the sanitized baseline; it can never be handed to the child verbatim.
        env = build_sanitized_environment(env)
        try:
            process = subprocess.Popen(
                argv,
                cwd=str(Path(cwd)),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                shell=False,
            )
        except FileNotFoundError as exc:
            return ProcessResult(
                command=argv,
                exit_code=None,
                error=f"executable not found: {exc}",
                duration_seconds=time.monotonic() - start,
            )
        except OSError as exc:
            return ProcessResult(
                command=argv,
                exit_code=None,
                error=f"failed to start process: {exc}",
                duration_seconds=time.monotonic() - start,
            )

        if job_id:
            with self._lock:
                self._running[job_id] = _Running(process=process, task_id=task_id)

        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate(process)
            stdout, stderr = process.communicate()
        finally:
            with self._lock:
                running = self._running.pop(job_id, None) if job_id else None

        cancelled = bool(running and running.cancelled)
        return ProcessResult(
            command=argv,
            exit_code=process.returncode,
            stdout=stdout or "",
            stderr=stderr or "",
            duration_seconds=time.monotonic() - start,
            timed_out=timed_out,
            cancelled=cancelled,
        )

    def cancel(self, job_id: str) -> bool:
        """Terminate a single running job. Returns True if it was running."""
        with self._lock:
            running = self._running.get(job_id)
            if not running:
                return False
            running.cancelled = True
        _terminate(running.process)
        return True

    def cancel_task(self, task_id: str) -> list[str]:
        """Terminate only running jobs belonging to the given task_id (F-13)."""
        cancelled_jobs: list[str] = []
        with self._lock:
            targets = [
                (jid, r) for jid, r in self._running.items() if r.task_id == task_id
            ]
            for jid, r in targets:
                r.cancelled = True
                cancelled_jobs.append(jid)
        for _, r in targets:
            _terminate(r.process)
        return cancelled_jobs

    def active_jobs(self, task_id: Optional[str] = None) -> list[str]:
        with self._lock:
            if task_id:
                return [
                    jid for jid, r in self._running.items() if r.task_id == task_id
                ]
            return list(self._running)


class InternalProcessService:
    """The only code allowed to launch NAO-internal (non-agent) subprocesses.

    It holds the trusted :class:`LaunchAuthority` and mints the internal
    authorization itself, so callers (Git, the Test Manager, discovery) never
    hold - and adapters are never given - the capability to create one.
    """

    def __init__(
        self, authority: LaunchAuthority, runner: ProcessRunner
    ) -> None:
        self._authority = authority
        self._runner = runner

    def run(
        self,
        argv: list[str],
        cwd: str,
        timeout: float,
        env: Optional[dict[str, str]] = None,
        job_id: Optional[str] = None,
        task_id: Optional[str] = None,
        purpose: str = "internal",
    ) -> ProcessResult:
        return self._runner.run(
            argv=argv,
            cwd=cwd,
            timeout=timeout,
            env=env,
            job_id=job_id,
            task_id=task_id,
            authorization=self._authority.issue_internal(purpose),
        )


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            try:
                # F-06: the taskkill helper must not open a second, unmanaged
                # environment path - it runs under the same sanitized policy.
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                    env=build_sanitized_environment(),
                )
                process.wait(timeout=2)
                return
            except Exception:
                pass

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    except OSError:
        pass
