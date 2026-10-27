"""Domain enumerations shared across NAO.

These are the vocabulary of the platform: workflow states (plan.md section 4),
agent roles (section 3), capabilities (section 2), permissions (section 8),
approval levels (section 20) and job statuses.
"""

from __future__ import annotations

from enum import Enum


class TaskState(str, Enum):
    """Explicit workflow states. A task always knows where it is."""

    RECEIVED = "received"
    ANALYZING = "analyzing"
    PLANNED = "planned"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    READY = "ready"
    IMPLEMENTING = "implementing"
    IMPLEMENTED = "implemented"
    TESTING = "testing"
    REVIEWING = "reviewing"
    FIXING = "fixing"
    REVERIFYING = "reverifying"
    VALIDATED = "validated"
    READY_FOR_MERGE = "ready_for_merge"
    MERGED = "merged"
    COMPLETED = "completed"
    # Failure branches
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"
    CANCELLED = "cancelled"


#: Terminal states never transition anywhere else.
TERMINAL_STATES = frozenset(
    {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}
)

#: The canonical forward progression plus failure branches (plan.md section 4).
VALID_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    # Recipes are declarative, so the non-terminal states may enter any of the
    # work states a recipe step requires. Terminal states remain terminal and
    # RECEIVED may only begin analysis (or fail immediately).
    TaskState.RECEIVED: frozenset(
        {TaskState.ANALYZING, TaskState.BLOCKED, TaskState.FAILED,
         TaskState.NEEDS_HUMAN, TaskState.CANCELLED}
    ),
    TaskState.ANALYZING: frozenset(
        {TaskState.PLANNED, TaskState.READY, TaskState.WAITING_FOR_APPROVAL,
         TaskState.IMPLEMENTING, TaskState.TESTING, TaskState.REVIEWING,
         TaskState.NEEDS_HUMAN, TaskState.BLOCKED, TaskState.FAILED,
         TaskState.CANCELLED}
    ),
    TaskState.PLANNED: frozenset(
        {TaskState.WAITING_FOR_APPROVAL, TaskState.READY, TaskState.BLOCKED,
         TaskState.IMPLEMENTING, TaskState.TESTING, TaskState.REVIEWING,
         TaskState.FAILED, TaskState.NEEDS_HUMAN, TaskState.CANCELLED}
    ),
    TaskState.WAITING_FOR_APPROVAL: frozenset(
        {TaskState.READY, TaskState.IMPLEMENTING, TaskState.READY_FOR_MERGE,
         TaskState.NEEDS_HUMAN, TaskState.BLOCKED, TaskState.FAILED,
         TaskState.CANCELLED}
    ),
    TaskState.READY: frozenset(
        {TaskState.IMPLEMENTING, TaskState.REVIEWING, TaskState.TESTING,
         TaskState.BLOCKED, TaskState.FAILED, TaskState.NEEDS_HUMAN,
         TaskState.CANCELLED}
    ),
    TaskState.IMPLEMENTING: frozenset(
        {TaskState.IMPLEMENTED, TaskState.FAILED, TaskState.BLOCKED,
         TaskState.NEEDS_HUMAN, TaskState.CANCELLED}
    ),
    TaskState.IMPLEMENTED: frozenset(
        {TaskState.TESTING, TaskState.REVERIFYING, TaskState.REVIEWING,
         TaskState.WAITING_FOR_APPROVAL, TaskState.IMPLEMENTING, TaskState.FAILED,
         TaskState.BLOCKED, TaskState.NEEDS_HUMAN, TaskState.CANCELLED}
    ),
    TaskState.TESTING: frozenset(
        {TaskState.REVIEWING, TaskState.FIXING, TaskState.WAITING_FOR_APPROVAL,
         TaskState.VALIDATED, TaskState.READY_FOR_MERGE, TaskState.IMPLEMENTING,
         TaskState.COMPLETED, TaskState.FAILED, TaskState.BLOCKED,
         TaskState.NEEDS_HUMAN, TaskState.CANCELLED}
    ),
    TaskState.REVIEWING: frozenset(
        {TaskState.FIXING, TaskState.VALIDATED, TaskState.WAITING_FOR_APPROVAL,
         TaskState.REVIEWING, TaskState.FAILED, TaskState.BLOCKED,
         TaskState.NEEDS_HUMAN, TaskState.CANCELLED}
    ),
    # FIXING may re-enter IMPLEMENTING because a fix is an implementation job.
    TaskState.FIXING: frozenset(
        {TaskState.IMPLEMENTING, TaskState.TESTING, TaskState.REVIEWING,
         TaskState.REVERIFYING, TaskState.FAILED, TaskState.BLOCKED,
         TaskState.NEEDS_HUMAN, TaskState.CANCELLED}
    ),
    TaskState.REVERIFYING: frozenset(
        {TaskState.IMPLEMENTING, TaskState.TESTING, TaskState.REVIEWING,
         TaskState.FIXING, TaskState.VALIDATED, TaskState.FAILED,
         TaskState.BLOCKED, TaskState.NEEDS_HUMAN, TaskState.CANCELLED}
    ),
    TaskState.VALIDATED: frozenset(
        {TaskState.READY_FOR_MERGE, TaskState.COMPLETED, TaskState.IMPLEMENTING,
         TaskState.TESTING, TaskState.FIXING, TaskState.REVERIFYING,
         TaskState.REVIEWING, TaskState.WAITING_FOR_APPROVAL, TaskState.FAILED,
         TaskState.BLOCKED, TaskState.NEEDS_HUMAN, TaskState.CANCELLED}
    ),
    TaskState.READY_FOR_MERGE: frozenset(
        {TaskState.MERGED, TaskState.BLOCKED, TaskState.FAILED,
         TaskState.NEEDS_HUMAN, TaskState.CANCELLED}
    ),
    TaskState.MERGED: frozenset(
        {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.BLOCKED: frozenset(
        {TaskState.READY, TaskState.NEEDS_HUMAN, TaskState.FAILED,
         TaskState.CANCELLED}
    ),
    TaskState.NEEDS_HUMAN: frozenset(
        {TaskState.READY, TaskState.PLANNED, TaskState.FAILED,
         TaskState.CANCELLED}
    ),
    TaskState.CANCELLED: frozenset(),
}


class Role(str, Enum):
    """Roles are independent of agent names (plan.md section 3)."""

    ARCHITECT = "architect"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    TESTER = "tester"
    DEBUGGER = "debugger"
    SECURITY_REVIEWER = "security_reviewer"
    DOCUMENTATION = "documentation"
    FINAL_VERIFIER = "final_verifier"


class Capability(str, Enum):
    """Capabilities an agent can declare (plan.md section 2)."""

    PLANNING = "planning"
    IMPLEMENTATION = "implementation"
    CODE_REVIEW = "code_review"
    TESTING = "testing"
    DEBUGGING = "debugging"
    SHELL = "shell"
    FILE_EDIT = "file_edit"
    READ_ONLY_ANALYSIS = "read_only_analysis"
    STRUCTURED_OUTPUT = "structured_output"
    STREAMING = "streaming"
    SESSION_CONTINUATION = "session_continuation"
    ACP = "acp"
    CLI = "cli"
    GIT_WORKTREE = "git_worktree"
    SECURITY_REVIEW = "security_review"
    DOCUMENTATION = "documentation"


class Permission(str, Enum):
    """Agent-independent permission vocabulary (plan.md section 8)."""

    READ = "read"
    SEARCH = "search"
    WRITE = "write"
    DELETE = "delete"
    SHELL = "shell"
    PACKAGE_INSTALL = "package_install"
    GIT_READ = "git_read"
    GIT_COMMIT = "git_commit"
    GIT_MERGE = "git_merge"
    GIT_PUSH = "git_push"
    MIGRATION = "migration"
    DEPLOYMENT = "deployment"
    NETWORK = "network"
    SECRET_ACCESS = "secret_access"


class ApprovalLevel(int, Enum):
    """Human approval levels (plan.md section 20)."""

    AUTOMATIC = 0
    CONTROLLED = 1
    HUMAN_APPROVAL = 2
    ALWAYS_HUMAN = 3


class JobStatus(str, Enum):
    """Status of a single agent job."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ApprovalState(str, Enum):
    """State of a human approval request."""

    PENDING = "pending"
    GRANTED = "granted"
    DENIED = "denied"


class FindingStatus(str, Enum):
    """Review finding lifecycle (plan.md section 10/16)."""

    OPEN = "open"
    RESOLVED = "resolved"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    INFORMATIONAL = "informational"
