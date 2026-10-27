"""GitHub Copilot CLI adapter.

Copilot CLI supports programmatic prompts and explicit tool allow/deny
controls. NAO therefore maps its permission policy onto ``--allow-tool`` /
``--deny-tool`` flags via configuration, rather than granting blanket access.
"""

from __future__ import annotations

from ..models.agent_contract import AgentRequest, AgentResponse
from ..models.enums import Permission
from ..process import ProcessResult
from .base import BaseCliAdapter


class CopilotAdapter(BaseCliAdapter):
    name = "copilot"
    result_event_types = ("result", "final")

    def enforceable_permissions(self) -> frozenset[Permission]:
        return frozenset(
            {
                Permission.READ,
                Permission.SEARCH,
                Permission.GIT_READ,
                Permission.WRITE,
                Permission.SHELL,
                Permission.NETWORK,
            }
        )

    def parse(
        self, request: AgentRequest, result: ProcessResult
    ) -> AgentResponse:
        response = super().parse(request, result)
        response.native_metadata["tool_controls"] = [
            arg for arg in response.command if arg.startswith("--allow-tool")
            or arg.startswith("--deny-tool")
        ]
        return response
