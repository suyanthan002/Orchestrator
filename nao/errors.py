"""Exception hierarchy and structured failure classification.

NAO never collapses every non-zero exit code into one bucket. Failures are
classified so that retry policy can reason about them (plan.md section 22).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class FailureClass(str, Enum):
    """Structured classification of a failed job."""

    NONE = "none"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID_CONFIG = "invalid_configuration"
    TOOL_FAILURE = "tool_failure"
    MALFORMED_OUTPUT = "malformed_output"
    PROCESS_FAILURE = "process_failure"
    TEST_FAILURE = "test_failure"
    IMPLEMENTATION_FAILURE = "implementation_failure"
    GIT_CONFLICT = "git_conflict"
    PERMISSION_DENIED = "permission_denial"
    UNSUPPORTED_PERMISSION = "unsupported_permission"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    HUMAN_BLOCKED = "human_blocked"


#: Failure classes that are safe/meaningful to retry automatically.
RETRYABLE_FAILURES = frozenset(
    {
        FailureClass.TOOL_FAILURE,
        FailureClass.PROCESS_FAILURE,
        FailureClass.MALFORMED_OUTPUT,
        FailureClass.IMPLEMENTATION_FAILURE,
        FailureClass.RATE_LIMIT,  # retry, but preferably via a different agent
    }
)

#: Failure classes where retrying the *same* agent is pointless and a human
#: (or the Chief Architect) must intervene.
NON_RETRYABLE_FAILURES = frozenset(
    {
        FailureClass.AUTHENTICATION,
        FailureClass.AUTHORIZATION,
        FailureClass.INVALID_CONFIG,
        FailureClass.PERMISSION_DENIED,
        FailureClass.UNSUPPORTED_PERMISSION,
        FailureClass.GIT_CONFLICT,
        FailureClass.HUMAN_BLOCKED,
        FailureClass.UNAVAILABLE,
    }
)


def is_retryable(failure: FailureClass) -> bool:
    """Return True when automatic retry is appropriate for this failure."""
    return failure in RETRYABLE_FAILURES


class NaoError(Exception):
    """Base class for all NAO errors."""


class ConfigError(NaoError):
    """Raised when configuration is missing or invalid."""


class PolicyError(NaoError):
    """Raised when the policy engine rejects an operation."""


class UnsupportedPermissionError(PolicyError):
    """Raised when an agent adapter cannot safely enforce a requested permission."""

    def __init__(self, agent: str, permission: str, reason: str = ""):
        self.agent = agent
        self.permission = permission
        self.reason = reason
        super().__init__(
            f"Agent '{agent}' cannot safely enforce permission '{permission}': "
            f"{reason or 'unsupported by adapter'}"
        )


class ApprovalRequired(PolicyError):
    """Raised when an operation needs an explicit human approval record."""

    def __init__(self, permission: str, message: Optional[str] = None):
        self.permission = permission
        super().__init__(
            message or f"Human approval required for permission: {permission}"
        )


class WorkflowError(NaoError):
    """Raised for invalid workflow state transitions or recipe problems."""


class GitError(NaoError):
    """Raised when a Git operation fails."""


class AgentUnavailable(NaoError):
    """Raised when a requested agent cannot be discovered or authenticated."""

    def __init__(self, agent: str, reason: str = "not available"):
        self.agent = agent
        self.reason = reason
        super().__init__(f"Agent '{agent}' is unavailable: {reason}")


@dataclass
class StructuredError:
    """A machine-readable error record attached to a job result."""

    failure_class: FailureClass
    message: str
    detail: Optional[str] = None
    retryable: bool = field(default=False)

    def __post_init__(self) -> None:
        if not self.retryable:
            self.retryable = is_retryable(self.failure_class)

    def to_dict(self) -> dict:
        return {
            "failure_class": self.failure_class.value,
            "message": self.message,
            "detail": self.detail,
            "retryable": self.retryable,
        }


#: Ordered keyword heuristics used to classify raw agent output when the
#: adapter cannot do better. Lower index = higher precedence.
_CLASSIFICATION_PATTERNS: tuple[tuple[FailureClass, tuple[str, ...]], ...] = (
    (
        FailureClass.RATE_LIMIT,
        ("rate limit", "429", "too many requests", "quota exceeded"),
    ),
    (
        FailureClass.AUTHENTICATION,
        (
            "not authenticated",
            "unauthorized",
            "401",
            "invalid api key",
            "no api key",
            "login required",
            "please log in",
            "authentication",
            "not logged in",
        ),
    ),
    (
        FailureClass.AUTHORIZATION,
        ("403", "forbidden", "permission denied by provider"),
    ),
    (
        FailureClass.TIMEOUT,
        ("timed out", "timeout", "deadline exceeded"),
    ),
    (
        FailureClass.PERMISSION_DENIED,
        ("nao: permission denied", "policy denied", "not permitted by policy"),
    ),
    (
        FailureClass.UNAVAILABLE,
        (
            "command not found",
            "no such file or directory",
            "is not recognized as an internal or external command",
            "executable not found",
        ),
    ),
    (
        FailureClass.GIT_CONFLICT,
        ("merge conflict", "conflict (content)", "automatic merge failed"),
    ),
    (
        FailureClass.MALFORMED_OUTPUT,
        ("jsondecodeerror", "failed to parse", "malformed json"),
    ),
    (
        FailureClass.RESOURCE_EXHAUSTION,
        ("out of memory", "memoryerror", "cannot allocate memory"),
    ),
)


def classify_text(text: str) -> FailureClass:
    """Best-effort classification of free-form output.

    Returns :class:`FailureClass.NONE` if nothing matches, letting the caller
    decide whether the operation actually succeeded.
    """
    if not text:
        return FailureClass.NONE
    lowered = text.lower()
    for failure, needles in _CLASSIFICATION_PATTERNS:
        if any(needle in lowered for needle in needles):
            return failure
    return FailureClass.NONE
