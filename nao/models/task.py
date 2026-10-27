"""The Task model.

A **task** is the desired work. It is deliberately distinct from a **job**
(one execution attempt by one agent) and a **result** (the outcome of a job).
A single task may produce many jobs: planning, implementation, review, fix,
verification (plan.md sections 5 and 8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .enums import Permission, TaskState


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def normalize_criterion_text(text: str) -> str:
    return " ".join(str(text).strip().casefold().split())


def detect_duplicate_criteria(criteria: list[str]) -> Optional[str]:
    seen: set[str] = set()
    for c in criteria:
        norm = normalize_criterion_text(c)
        if not norm:
            continue
        if norm in seen:
            return f"duplicate acceptance criterion: '{c}'"
        seen.add(norm)
    return None


_OPPOSING_PREFIX_PAIRS = [
    ("allow ", "deny "),
    ("allow ", "disallow "),
    ("allow ", "reject "),
    ("allow ", "forbid "),
    ("allow ", "prohibit "),
    ("allow ", "prevent "),
    ("permit ", "deny "),
    ("permit ", "disallow "),
    ("permit ", "reject "),
    ("permit ", "forbid "),
    ("permit ", "prohibit "),
    ("permit ", "prevent "),
    ("accept ", "reject "),
    ("accept ", "deny "),
    ("enable ", "disable "),
    ("enabled ", "disabled "),
    ("must not ", "must "),
    ("never ", "always "),
    ("should not ", "should "),
    ("do not require ", "require "),
    ("prohibit ", "require "),
    ("forbid ", "require "),
    ("reject ", "allow "),
    ("rejects ", "allows "),
    ("pass ", "fail "),
    ("passes ", "fails "),
    ("true", "false"),
    ("valid ", "invalid "),
    ("supported ", "unsupported "),
]


def detect_contradictory_criteria(criteria: list[str]) -> Optional[tuple[str, str]]:
    norms = [(normalize_criterion_text(c), c) for c in criteria if normalize_criterion_text(c)]
    for i, (n1, orig1) in enumerate(norms):
        for j, (n2, orig2) in enumerate(norms):
            if i >= j:
                continue
            for pos, neg in _OPPOSING_PREFIX_PAIRS:
                if n1.startswith(pos) and n2.startswith(neg):
                    s1 = n1[len(pos):].strip()
                    s2 = n2[len(neg):].strip()
                    if s1 == s2 and s1:
                        return orig1, orig2
                if n1.startswith(neg) and n2.startswith(pos):
                    s1 = n1[len(neg):].strip()
                    s2 = n2[len(pos):].strip()
                    if s1 == s2 and s1:
                        return orig1, orig2
            if (n1 == "true" and n2 == "false") or (n1 == "false" and n2 == "true"):
                return orig1, orig2
            if (n1 == "pass" and n2 == "fail") or (n1 == "fail" and n2 == "pass"):
                return orig1, orig2
    return None


@dataclass
class Task:
    """A unit of desired work and its orchestration state."""

    task_id: str
    title: str = ""
    description: str = ""
    #: Free-text acceptance criteria the task must satisfy to be complete.
    acceptance_criteria: list[str] = field(default_factory=list)
    task_type: str = "general"
    #: The repository NAO is allowed to operate on for this task.
    repo_path: Optional[str] = None
    #: The isolated workspace (worktree) assigned for implementation.
    workspace: Optional[str] = None
    #: Permissions the task is allowed to exercise. Empty means "none granted".
    granted_permissions: list[Permission] = field(default_factory=list)

    state: TaskState = TaskState.RECEIVED
    #: The workflow recipe this task runs under.
    recipe: str = "feature_implementation"
    #: Per-role model/agent overrides requested by the task spec.
    routing_overrides: dict[str, str] = field(default_factory=dict)
    #: Limits that bound the fix/re-audit loop.
    max_fix_attempts: int = 2
    max_jobs_per_role: int = 1

    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate acceptance criteria (F-03)."""
        dup = detect_duplicate_criteria(self.acceptance_criteria)
        if dup:
            raise ValueError(dup)
        contra = detect_contradictory_criteria(self.acceptance_criteria)
        if contra:
            raise ValueError(
                f"contradictory acceptance criteria: '{contra[0]}' vs '{contra[1]}'"
            )

    # -- serialization -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "acceptance_criteria": list(self.acceptance_criteria),
            "task_type": self.task_type,
            "repo_path": self.repo_path,
            "workspace": self.workspace,
            "granted_permissions": [p.value for p in self.granted_permissions],
            "state": self.state.value,
            "recipe": self.recipe,
            "routing_overrides": dict(self.routing_overrides),
            "max_fix_attempts": self.max_fix_attempts,
            "max_jobs_per_role": self.max_jobs_per_role,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        return cls(
            task_id=data["task_id"],
            title=data.get("title", ""),
            description=data.get("description", ""),
            acceptance_criteria=list(data.get("acceptance_criteria", [])),
            task_type=data.get("task_type", "general"),
            repo_path=data.get("repo_path"),
            workspace=data.get("workspace"),
            granted_permissions=[
                Permission(p) for p in data.get("granted_permissions", [])
            ],
            state=TaskState(data.get("state", TaskState.RECEIVED.value)),
            recipe=data.get("recipe", "feature_implementation"),
            routing_overrides=dict(data.get("routing_overrides", {})),
            max_fix_attempts=int(data.get("max_fix_attempts", 2)),
            max_jobs_per_role=int(data.get("max_jobs_per_role", 1)),
            created_at=_parse_dt(data.get("created_at")) or utc_now(),
            updated_at=_parse_dt(data.get("updated_at")) or utc_now(),
            metadata=dict(data.get("metadata", {})),
        )

    def has_permission(self, permission: Permission) -> bool:
        return permission in self.granted_permissions


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
