"""Aider adapter.

Aider is best driven through scripted one-shot execution: ``--message`` plus
``--yes`` and dry-run controls. It has no structured JSON output, so NAO treats
its stdout as the response and relies on Git diffs for change detection.
"""

from __future__ import annotations

from ..models.agent_contract import AgentRequest, AgentResponse
from ..models.enums import Permission
from ..process import ProcessResult
from .base import BaseCliAdapter


class AiderAdapter(BaseCliAdapter):
    name = "aider"
    result_event_types = ()  # Aider does not emit terminal JSON events.

    def enforceable_permissions(self) -> frozenset[Permission]:
        return frozenset(
            {
                Permission.READ,
                Permission.SEARCH,
                Permission.GIT_READ,
                Permission.WRITE,
                Permission.SHELL,
                Permission.GIT_COMMIT,
            }
        )

    def parse(
        self, request: AgentRequest, result: ProcessResult
    ) -> AgentResponse:
        response = super().parse(request, result)
        # Aider echoes the conversation; keep the tail as the response.
        text = (result.stdout or "").strip()
        if text:
            response.response = text
            response.native_metadata["output_format"] = "text"
        return response
