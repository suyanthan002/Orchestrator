"""Goose adapter.

Goose is primarily an ACP agent but also has a headless ``run`` CLI. NAO uses
the CLI for now; ACP is declared as a supported interface in configuration so a
future ACP adapter can be introduced without touching the core.
"""

from __future__ import annotations

from ..models.agent_contract import AgentRequest, AgentResponse
from ..models.enums import Permission
from ..process import ProcessResult
from .base import BaseCliAdapter


class GooseAdapter(BaseCliAdapter):
    name = "goose"
    # Goose CLI text output is not typed; rely on the plain-text fallback.
    result_event_types = ("result", "final")
    supports_structured_output = False

    def enforceable_permissions(self) -> frozenset[Permission]:
        """Goose is read-only here (F-01).

        The headless ``goose run --no-session`` CLI exposes no native control
        that restricts file writes or shell execution, so NAO cannot faithfully
        represent WRITE or SHELL policy for Goose. Rather than claim an
        enforceability it does not have, those permissions are declared
        *unsupported*: a WRITE/SHELL job is denied for Goose and routed to an
        agent that can actually restrict the capability. Goose "usability" is
        never bought by weakening policy.
        """
        return frozenset(
            {
                Permission.READ,
                Permission.SEARCH,
                Permission.GIT_READ,
            }
        )

    def parse(
        self, request: AgentRequest, result: ProcessResult
    ) -> AgentResponse:
        response = super().parse(request, result)
        # Goose prints session lines like "Session: <id>"; remember them.
        for line in result.stdout.splitlines():
            lower = line.lower()
            if lower.startswith("session:") or lower.startswith("conversation id"):
                _, _, value = line.partition(":")
                value = value.strip()
                if value:
                    response.session_id = value
                    break
        return response
