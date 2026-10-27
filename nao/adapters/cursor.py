"""Cursor adapter.

Cursor's CLI supports non-interactive automation, structured output and
worktrees, and also exposes ACP via ``agent acp``. NAO uses the CLI and
preserves the conversation id for session continuation.
"""

from __future__ import annotations

import json

from ..models.agent_contract import AgentRequest, AgentResponse
from ..models.enums import Permission
from ..process import ProcessResult
from .base import BaseCliAdapter


class CursorAdapter(BaseCliAdapter):
    name = "cursor"
    result_event_types = ("result", "run_result", "final")

    def enforceable_permissions(self) -> frozenset[Permission]:
        return frozenset(
            {
                Permission.READ,
                Permission.SEARCH,
                Permission.GIT_READ,
                Permission.WRITE,
                Permission.SHELL,
            }
        )

    def parse(
        self, request: AgentRequest, result: ProcessResult
    ) -> AgentResponse:
        response = super().parse(request, result)
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            for key in ("conversationId", "conversation_id", "sessionId"):
                if isinstance(event.get(key), str):
                    response.session_id = event[key]
                    break
        return response
