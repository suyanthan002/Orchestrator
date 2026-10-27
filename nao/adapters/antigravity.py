"""Antigravity adapter.

Antigravity's headless CLI supports JSON/NDJSON streaming output, structured
output schemas and conversation IDs. This adapter consumes the streaming JSON
and keeps the conversation id for session continuation. It deliberately never
uses ``--dangerously-skip-permissions``: NAO grants permissions only when the
policy engine has authorized the corresponding NAO permission.
"""

from __future__ import annotations

import json

from ..models.agent_contract import AgentRequest, AgentResponse
from ..models.enums import Permission
from ..process import ProcessResult
from .base import BaseCliAdapter


class AntigravityAdapter(BaseCliAdapter):
    name = "antigravity"
    result_event_types = ("result", "final", "run_result")

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
        # Antigravity's streaming JSON is a sequence of typed events; the
        # generic parser already handles the terminal one, but we also mine the
        # stream for the conversation id and any structured output.
        response = super().parse(request, result)

        events = []
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        for event in events:
            for key in ("conversationId", "conversation_id", "sessionId"):
                if isinstance(event.get(key), str):
                    response.session_id = event[key]
            structured = event.get("structured_output") or event.get(
                "structuredOutput"
            )
            if isinstance(structured, dict):
                response.structured_output = structured
        response.native_metadata["stream_event_count"] = len(events)
        return response
