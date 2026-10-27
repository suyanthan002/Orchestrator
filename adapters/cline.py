"""Legacy Cline adapter (preserved prototype).

This adapter keeps the original prototype's behaviour and public surface
(``ClineAdapter(runner).execute(task) -> AgentResult``) but no longer hard-codes
a user-specific installation path. It asks the NAO discovery layer for the
Cline executable and only falls back to an environment-derived location.

The new platform uses :class:`nao.adapters.cline.ClineAdapter`; both share the
same tested ``run_result`` parser.
"""

import os
import shutil
from pathlib import Path

from orchestrator.agent import AgentTask
from orchestrator.parser import parse_cline_output
from orchestrator.runner import AgentRunner

# NOTE (F-01): the legacy prototype previously injected `--auto-approve=true`,
# a blanket flag that granted write+shell outside the Policy Engine. The legacy
# demo task is a READ-ONLY verification, so it now uses `--plan` only, and
# `orchestrator.runner` validates the native argv against the read-only policy
# through the shared `nao.permissions` boundary before executing.
CLINE_ARGS = ["--json", "--plan"]


def _legacy_fallback_command() -> list[str]:
    """Environment-derived fallback for an npm-global Cline install."""
    node = shutil.which("node") or "node"
    appdata = os.environ.get("APPDATA")
    if appdata:
        entry = Path(appdata) / "npm" / "node_modules" / "cline" / "bin" / "cline"
        return [node, str(entry), *CLINE_ARGS]
    return ["cline", *CLINE_ARGS]


def build_cline_command() -> list[str]:
    """Resolve the Cline launcher, preferring NAO discovery."""
    try:
        from nao.config import load_config
        from nao.discovery import resolve_executable

        executable = resolve_executable(load_config().agent("cline"))
        if executable is not None:
            return [*executable.argv, *CLINE_ARGS]
    except Exception:
        # Discovery is best-effort in the legacy path.
        pass
    return _legacy_fallback_command()


class ClineAdapter:
    name = "cline"

    def __init__(self, runner: AgentRunner):
        self.runner = runner

    def execute(self, task: AgentTask):
        command = build_cline_command()
        result = self.runner.run(
            agent_name=self.name,
            command=command,
            task=task,
        )

        parsed = parse_cline_output(result.stdout)
        result.success = result.success and parsed.success
        result.final_response = parsed.final_response
        result.finish_reason = parsed.finish_reason
        result.iterations = parsed.iterations
        result.usage = parsed.usage
        return result
