"""The unified NAO Agent Contract (plan.md sections 2, 5 and 15).

Every adapter translates its native interface into an :class:`AgentRequest`
and out of an :class:`AgentResponse`. Agent-specific protocol details never
leak into the core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .enums import Capability, Permission, Role

#: Expected structured output schema per role (plan.md section 15 / F-03).
ROLE_OUTPUT_SCHEMAS: dict[Role, dict[str, Any]] = {
    #: A verifier verdict must carry enough structured evidence to justify a
    #: ``passed=true`` claim: an explicit per-criterion result, each backed by
    #: evidence. ``additionalProperties`` is closed so an undeclared shape is a
    #: failure rather than silently ignored.
    Role.FINAL_VERIFIER: {
        "type": "object",
        "required": ["passed", "criteria_results"],
        "additionalProperties": False,
        "properties": {
            "passed": {"type": "boolean"},
            "reason": {"type": "string"},
            "criteria_results": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["criterion", "passed", "evidence"],
                    "additionalProperties": False,
                    "properties": {
                        "criterion": {"type": "string"},
                        "passed": {"type": "boolean"},
                        # F-03: evidence is a *structured reference* to a kind of
                        # NAO artifact (a test result, a diff, an audit record,
                        # ...), never arbitrary prose.
                        "evidence": {
                            "type": "object",
                            "required": ["kind", "reference"],
                            "additionalProperties": False,
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": [
                                        "test",
                                        "diff",
                                        "artifact",
                                        "command",
                                        "audit",
                                        "file",
                                    ],
                                },
                                "reference": {"type": "string"},
                                "detail": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
    Role.REVIEWER: {
        "type": "object",
        "required": ["findings"],
        "additionalProperties": False,
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["title", "severity"],
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "severity": {
                            "type": "string",
                            "enum": ["info", "low", "medium", "high", "critical"],
                        },
                        "description": {"type": "string"},
                        "file": {"type": "string"},
                        "line": {"type": "integer"},
                        "recommendation": {"type": "string"},
                    },
                },
            }
        },
    },
    Role.SECURITY_REVIEWER: {
        "type": "object",
        "required": ["findings"],
        "additionalProperties": False,
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["title", "severity"],
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "severity": {
                            "type": "string",
                            "enum": ["info", "low", "medium", "high", "critical"],
                        },
                        "description": {"type": "string"},
                        "file": {"type": "string"},
                    },
                },
            }
        },
    },
}


@dataclass(frozen=True)
class AgentSpec:
    """A declarative description of what an agent is and can do.

    This is the registry's unit of knowledge: capabilities, interfaces and
    support flags. It is loaded from configuration, never hard-coded.
    """

    name: str
    enabled: bool = True
    capabilities: tuple[Capability, ...] = ()
    interfaces: tuple[str, ...] = ()
    supports: dict[str, bool] = field(default_factory=dict)
    #: Ordering hint for routing within a role (higher = preferred).
    priority: int = 0

    def has_capability(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def has_capabilities(self, required: "set[Capability] | list[Capability]") -> bool:
        return all(c in self.capabilities for c in required)

    def supports_feature(self, feature: str) -> bool:
        return bool(self.supports.get(feature, False))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "capabilities": [c.value for c in self.capabilities],
            "interfaces": list(self.interfaces),
            "supports": dict(self.supports),
            "priority": self.priority,
        }


@dataclass
class AgentRequest:
    """What NAO hands to an adapter for one job."""

    job_id: str
    role: Role
    agent: str
    task_id: str
    #: The fully rendered prompt the agent receives.
    prompt: str
    #: Structured context pack (already serialized to a string by the pack).
    context: str = ""
    #: The isolated workspace directory the agent is allowed to touch.
    workspace: Optional[str] = None
    #: Permissions the policy engine has authorized for this job.
    permissions: tuple[Permission, ...] = ()
    timeout_seconds: int = 300
    #: Model/provider selection, interpreted by the adapter.
    model: Optional[str] = None
    provider: Optional[str] = None
    #: Names of output fields NAO expects to find in structured output.
    expected_output_schema: dict[str, Any] = field(default_factory=dict)
    #: Session to continue, when the agent supports continuation.
    session_id: Optional[str] = None
    #: Extra adapter-specific options (never agent-specific shell strings).
    options: dict[str, Any] = field(default_factory=dict)
    #: Environment variables (already redacted for auditability).
    environment: dict[str, str] = field(default_factory=dict)
    #: F-01: the policy-issued launch grant this job must present to the
    #: ProcessRunner boundary. It is opaque to the adapter, which may only pass
    #: it through - never construct, widen, or replace it.
    launch_authorization: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "role": self.role.value,
            "agent": self.agent,
            "task_id": self.task_id,
            "prompt": self.prompt,
            "context": self.context,
            "workspace": self.workspace,
            "permissions": [p.value for p in self.permissions],
            "timeout_seconds": self.timeout_seconds,
            "model": self.model,
            "provider": self.provider,
            "expected_output_schema": dict(self.expected_output_schema),
            "session_id": self.session_id,
            "options": dict(self.options),
            "environment": dict(self.environment),
        }


@dataclass
class AgentResponse:
    """Normalized output from any agent, regardless of native interface."""

    job_id: str
    agent: str
    status: str = "succeeded"
    response: Optional[str] = None
    structured_output: Optional[dict[str, Any]] = None
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    files_changed: list[str] = field(default_factory=list)
    commands_executed: list[str] = field(default_factory=list)
    tests: list[dict[str, Any]] = field(default_factory=list)
    usage: Optional[dict[str, Any]] = None
    duration_seconds: Optional[float] = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    session_id: Optional[str] = None
    #: Raw native events, preserved for auditing (plan.md section 20).
    native_metadata: dict[str, Any] = field(default_factory=dict)
    #: The exact command line used, for the audit trail.
    command: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.status in ("succeeded", "success", "completed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "agent": self.agent,
            "status": self.status,
            "response": self.response,
            "structured_output": self.structured_output,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "files_changed": list(self.files_changed),
            "commands_executed": list(self.commands_executed),
            "tests": list(self.tests),
            "usage": self.usage,
            "duration_seconds": self.duration_seconds,
            "errors": list(self.errors),
            "session_id": self.session_id,
            "native_metadata": dict(self.native_metadata),
            "command": list(self.command),
        }
