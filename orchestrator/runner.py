"""Legacy prototype agent runner (F-01).

This is the preserved prototype execution path. It is not used by the modern
platform (``nao.execution`` / ``nao.adapters`` do that), but it must obey the
same trust boundary:

* the **complete** final argv is assembled first and only then analyzed, so no
  prompt text is ever appended after authorization, and
* subprocesses are launched through the trusted internal process service rather
  than a bare ``subprocess.run`` call.
"""

from pathlib import Path

from nao.launch import LaunchAuthority
from nao.models.enums import Permission
from nao.permissions import analyze_native_flags
from nao.process import InternalProcessService, ProcessRunner

from .agent import AgentResult, AgentTask

#: The legacy prototype only issues read-only verification tasks. Native flags
#: are validated against exactly these permissions so a blanket switch can
#: never slip through the legacy path (F-01).
_LEGACY_AUTHORIZED_PERMISSIONS = frozenset(
    {Permission.READ, Permission.SEARCH, Permission.GIT_READ}
)


class AgentRunner:

    def __init__(self) -> None:
        authority = LaunchAuthority()
        self._internal = InternalProcessService(authority, ProcessRunner(authority))

    def run(
        self,
        agent_name: str,
        command: list[str],
        task: AgentTask,
    ) -> AgentResult:

        working_directory = Path(
            task.working_directory
        ).resolve()

        # F-01: build the COMPLETE final argv (native command + prompt) and
        # analyze *that*. The previous order analyzed `command` and then
        # appended the prompt, leaving prompt-derived option-looking argv
        # outside the analysis.
        process_command = [*command, task.prompt]

        violation = analyze_native_flags(
            process_command,
            _LEGACY_AUTHORIZED_PERMISSIONS,
        )
        if violation:
            return AgentResult(
                task_id=task.task_id,
                agent=agent_name,
                success=False,
                exit_code=None,
                stdout="",
                stderr=f"NAO: {violation}",
                command=list(process_command),
            )

        result = self._internal.run(
            argv=process_command,
            cwd=str(working_directory),
            timeout=task.timeout_seconds,
            purpose="legacy-agent",
        )

        if result.timed_out:
            return AgentResult(
                task_id=task.task_id,
                agent=agent_name,
                success=False,
                exit_code=None,
                stdout=result.stdout,
                stderr=(result.stderr or "")
                + "\nNAO: Agent execution timed out.",
                command=process_command,
            )

        if result.error:
            return AgentResult(
                task_id=task.task_id,
                agent=agent_name,
                success=False,
                exit_code=None,
                stdout=result.stdout,
                stderr=f"NAO runner error: {result.error}",
                command=process_command,
            )

        return AgentResult(
            task_id=task.task_id,
            agent=agent_name,
            success=result.exit_code == 0,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            command=process_command,
        )
