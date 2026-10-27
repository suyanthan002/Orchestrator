"""Cline adapter.

Reuses the prototype's tested ``run_result`` parser
(:func:`orchestrator.parser.parse_cline_output`) so the previously verified
Cline behaviour (JSON output, final response, usage, iterations, plan mode) is
preserved rather than reimplemented (plan.md section 26 / task section 26).

Cline speaks NDJSON on ``--json`` and supports ``--plan`` for read-only runs.
"""

from __future__ import annotations

from typing import Any

# The canonical Cline run_result parser lives with the preserved prototype.
from orchestrator.parser import parse_cline_output

from ..errors import FailureClass
from ..models.agent_contract import AgentRequest, AgentResponse
from ..models.enums import JobStatus, Permission
from ..process import ProcessResult
from .base import BaseCliAdapter


class ClineAdapter(BaseCliAdapter):
    name = "cline"
    result_event_types = ("run_result",)

    def enforceable_permissions(self) -> frozenset[Permission]:
        return frozenset(
            {
                Permission.READ,
                Permission.SEARCH,
                Permission.GIT_READ,
                Permission.WRITE,
                Permission.SHELL,
                Permission.PACKAGE_INSTALL,
            }
        )

    def parse(
        self, request: AgentRequest, result: ProcessResult
    ) -> AgentResponse:
        response = super().parse(request, result)

        parsed = parse_cline_output(result.stdout)
        if parsed.finish_reason is not None:
            response.response = parsed.final_response or response.response
            response.usage = parsed.usage or response.usage
            response.native_metadata["iterations"] = parsed.iterations
            response.native_metadata["finish_reason"] = parsed.finish_reason

            # Preserve the prototype's strict success rule.
            if not parsed.success and response.status == JobStatus.SUCCEEDED.value:
                response.status = JobStatus.FAILED.value
                response.errors.append(
                    {
                        "failure_class": (
                            FailureClass.IMPLEMENTATION_FAILURE.value
                        ),
                        "message": (
                            "Cline did not report a completed finish reason "
                            f"(got {parsed.finish_reason!r})"
                        ),
                        "retryable": True,
                    }
                )

        self._extract_cline_extras(response, result.stdout)
        return response

    @staticmethod
    def _extract_cline_extras(response: AgentResponse, stdout: str) -> None:
        """Pull session/conversation identifiers out of Cline's NDJSON."""
        import json

        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                continue
            for key in ("sessionId", "session_id", "conversationId"):
                if isinstance(event.get(key), str):
                    response.session_id = event[key]
                    return
