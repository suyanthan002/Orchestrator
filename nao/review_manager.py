"""Review Manager (plan.md sections 10, 11 and 16).

Review is independent by construction: the reviewer is a different agent than
the implementer, and it receives the task, acceptance criteria, the actual diff
and the test evidence - not the implementer's conclusion.

Findings are structured and stateful. A review fails when any blocking finding
remains open.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from .artifacts import ArtifactManager
from .audit import AuditLogger
from .models.agent_contract import ROLE_OUTPUT_SCHEMAS, AgentResponse
from .models.enums import FindingStatus, Role
from .models.events import EventType
from .models.review import Finding
from .schema import validate

_VALID_SEVERITIES = {"info", "low", "medium", "high", "critical"}


@dataclass
class ReviewOutcome:
    passed: bool
    findings: list[Finding] = field(default_factory=list)
    blocking: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "findings": [f.to_dict() for f in self.findings],
            "blocking": [f.finding_id for f in self.blocking],
        }


@dataclass
class ReviewParse:
    """Result of strictly parsing a reviewer's output (F-03)."""

    valid: bool
    findings: list[Finding] = field(default_factory=list)
    reason: str = ""


class ReviewManager:
    def __init__(
        self,
        artifacts: Optional[ArtifactManager] = None,
        audit: Optional[AuditLogger] = None,
    ) -> None:
        self.artifacts = artifacts
        self.audit = audit

    # -- strict parsing (enforcement path) ---------------------------------
    def parse(
        self,
        response: AgentResponse,
        role: Role,
        task_id: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> ReviewParse:
        """Validate a reviewer's output against its declared schema (F-03).

        Returns ``valid=False`` for missing, malformed, wrong-typed or
        unexpected output. It never fabricates an empty (passing) finding set
        from unparseable output.
        """
        schema = ROLE_OUTPUT_SCHEMAS.get(role)
        if not schema:
            return ReviewParse(
                False,
                [],
                f"no reviewer output schema is declared for role '{role.value}'",
            )

        candidate: Optional[dict[str, Any]] = None
        if isinstance(response.structured_output, dict):
            candidate = response.structured_output
        else:
            candidate = self._parse_response_json(response)

        if not isinstance(candidate, dict):
            return ReviewParse(
                False, [], "reviewer returned no structured findings object"
            )

        errors = validate(candidate, schema)
        if errors:
            return ReviewParse(
                False,
                [],
                "reviewer output does not satisfy the required schema: "
                + "; ".join(errors),
            )

        findings = self._build_findings(
            candidate.get("findings") or [], task_id, job_id
        )
        return ReviewParse(True, findings)

    def _build_findings(
        self,
        raw_items: list[Any],
        task_id: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> list[Finding]:
        findings: list[Finding] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            title = (
                item.get("title")
                or item.get("issue")
                or item.get("message")
                or "review finding"
            )
            severity = str(item.get("severity", "medium")).lower()
            if severity not in _VALID_SEVERITIES:
                severity = "medium"
            finding = Finding.create(
                title=str(title),
                description=str(
                    item.get("description") or item.get("detail") or ""
                ),
                severity=severity,
                file=item.get("file"),
                line=item.get("line"),
                recommendation=item.get("recommendation"),
                raised_by_job=job_id,
            )
            status = str(item.get("status", "open")).lower()
            if status in {s.value for s in FindingStatus}:
                finding.status = FindingStatus(status)
            findings.append(finding)
            if self.audit:
                self.audit.record(
                    EventType.FINDING_CREATED,
                    task_id=task_id,
                    job_id=job_id,
                    status=finding.severity,
                    message=finding.title,
                    data={"finding_id": finding.finding_id, "file": finding.file},
                )
        return findings

    # -- lenient extraction (non-enforcing helper) -------------------------
    def collect_findings(
        self,
        response: AgentResponse,
        task_id: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> list[Finding]:
        """Best-effort extraction for callers that are not an enforcement gate.

        The workflow never uses this for gating review; it calls :meth:`parse`,
        which fails closed.
        """
        return self._build_findings(self._extract_items(response), task_id, job_id)

    def _extract_items(self, response: AgentResponse) -> list[Any]:
        for source in (response.structured_output, self._parse_response_json(response)):
            if isinstance(source, dict):
                items = source.get("findings")
                if isinstance(items, list):
                    return items
        return []

    @staticmethod
    def _parse_response_json(response: AgentResponse) -> Optional[dict[str, Any]]:
        text = response.response or response.stdout or ""
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    # -- state management --------------------------------------------------
    def resolve(
        self,
        findings: list[Finding],
        finding_ids: list[str],
        fix_job_id: str,
        status: FindingStatus = FindingStatus.RESOLVED,
        task_id: Optional[str] = None,
    ) -> list[Finding]:
        for finding in findings:
            if finding.finding_id in finding_ids:
                finding.status = status
                finding.resolved_by_job = fix_job_id
                if self.audit:
                    self.audit.record(
                        EventType.FINDING_RESOLVED,
                        task_id=task_id,
                        job_id=fix_job_id,
                        status=status.value,
                        message=finding.title,
                        data={"finding_id": finding.finding_id},
                    )
        return findings

    def evaluate(self, findings: list[Finding]) -> ReviewOutcome:
        blocking = [f for f in findings if f.blocks_completion]
        return ReviewOutcome(
            passed=not blocking,
            findings=findings,
            blocking=blocking,
        )

    def persist(self, task_id: str, findings: list[Finding]) -> None:
        if self.artifacts:
            self.artifacts.save_findings(task_id, findings)
