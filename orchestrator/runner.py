import subprocess
from pathlib import Path

from .agent import AgentResult, AgentTask


class AgentRunner:

    def run(
        self,
        agent_name: str,
        command: list[str],
        task: AgentTask,
    ) -> AgentResult:

        working_directory = Path(
            task.working_directory
        ).resolve()

        process_command = command + [task.prompt]

        try:
            completed = subprocess.run(
                process_command,
                cwd=working_directory,
                capture_output=True,
                text=True,
                timeout=task.timeout_seconds,
                shell=False,
            )

            return AgentResult(
                task_id=task.task_id,
                agent=agent_name,
                success=completed.returncode == 0,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                command=process_command,
            )

        except subprocess.TimeoutExpired as exc:

            stdout = exc.stdout or ""
            stderr = exc.stderr or ""

            if isinstance(stdout, bytes):
                stdout = stdout.decode(
                    "utf-8",
                    errors="replace",
                )

            if isinstance(stderr, bytes):
                stderr = stderr.decode(
                    "utf-8",
                    errors="replace",
                )

            return AgentResult(
                task_id=task.task_id,
                agent=agent_name,
                success=False,
                exit_code=None,
                stdout=stdout,
                stderr=stderr
                + "\nNAO: Agent execution timed out.",
                command=process_command,
            )

        except Exception as exc:

            return AgentResult(
                task_id=task.task_id,
                agent=agent_name,
                success=False,
                exit_code=None,
                stdout="",
                stderr=f"NAO runner error: {exc}",
                command=process_command,
            )