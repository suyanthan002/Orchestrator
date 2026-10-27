"""Workflow recipes (plan.md section 16).

Workflows are data, not hard-coded control flow. Each recipe is an ordered list
of steps; the engine executes the recipe and owns the state machine. New
workflows can be added here without touching ``main.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class RecipeStep:
    """One step of a recipe.

    ``kind`` selects the engine behaviour; ``role`` is the agent role used for
    the step; ``approval`` marks a human approval gate.
    """

    kind: str
    role: Optional[str] = None
    approval: Optional[str] = None
    description: str = ""


@dataclass(frozen=True)
class Recipe:
    name: str
    description: str
    steps: tuple[RecipeStep, ...]
    #: Whether the recipe mutates code (and therefore needs a worktree).
    mutating: bool = True
    #: Roles whose jobs are subject to the fix/re-audit loop.
    requires_review: bool = True
    default_permissions: tuple[str, ...] = field(default_factory=tuple)

    def step_kinds(self) -> list[str]:
        return [s.kind for s in self.steps]


# -- step kinds -------------------------------------------------------------
# analyze, plan, approve, worktree, implement, test, review, security_review,
# validate, merge, cleanup, report

_IMPLEMENT_LOOP = (
    RecipeStep("implement", role="implementer", description="Implement the change"),
    RecipeStep("test", description="Run tests and record evidence"),
    RecipeStep("review", role="reviewer", description="Independent review"),
)

RECIPES: dict[str, Recipe] = {
    "feature_implementation": Recipe(
        name="feature_implementation",
        description="Plan, approve, implement, test, review, fix, validate, merge.",
        steps=(
            RecipeStep("analyze", role="architect", description="Analyze the task"),
            RecipeStep("plan", role="architect", description="Produce a plan"),
            RecipeStep("approve", approval="plan", description="Human approves the plan"),
            RecipeStep("worktree", description="Create an isolated worktree"),
            *_IMPLEMENT_LOOP,
            RecipeStep("validate", role="final_verifier", description="Validate criteria"),
            RecipeStep("merge", description="Merge the worktree branch"),
            RecipeStep("cleanup", description="Remove the worktree"),
        ),
        default_permissions=("write",),
    ),
    "bug_fix": Recipe(
        name="bug_fix",
        description="Reproduce, diagnose, fix, test, review, regression-test, merge.",
        steps=(
            RecipeStep("analyze", role="debugger", description="Reproduce and diagnose"),
            RecipeStep("worktree", description="Create an isolated worktree"),
            RecipeStep("implement", role="implementer", description="Implement the fix"),
            RecipeStep("test", description="Run targeted tests"),
            RecipeStep("review", role="reviewer", description="Independent review"),
            RecipeStep("test", description="Run regression tests"),
            RecipeStep("merge", description="Merge the worktree branch"),
            RecipeStep("cleanup", description="Remove the worktree"),
        ),
        default_permissions=("write",),
    ),
    "refactoring": Recipe(
        name="refactoring",
        description="Refactor with tests and review, without changing behaviour.",
        steps=(
            RecipeStep("analyze", role="architect", description="Analyze the refactor"),
            RecipeStep("plan", role="architect", description="Plan the refactor"),
            RecipeStep("worktree", description="Create an isolated worktree"),
            *_IMPLEMENT_LOOP,
            RecipeStep("validate", role="final_verifier", description="Validate criteria"),
            RecipeStep("merge", description="Merge the worktree branch"),
            RecipeStep("cleanup", description="Remove the worktree"),
        ),
        default_permissions=("write",),
    ),
    "code_review": Recipe(
        name="code_review",
        description="Review existing code and report findings. Read-only.",
        steps=(
            RecipeStep("analyze", role="architect", description="Scope the review"),
            RecipeStep("review", role="reviewer", description="Independent review"),
            RecipeStep("report", description="Report findings"),
        ),
        mutating=False,
        requires_review=False,
        default_permissions=(),
    ),
    "security_audit": Recipe(
        name="security_audit",
        description="Threat analysis and independent security review. Read-only.",
        steps=(
            RecipeStep("analyze", role="architect", description="Threat analysis"),
            RecipeStep(
                "security_review",
                role="security_reviewer",
                description="Independent security review",
            ),
            RecipeStep("report", description="Report findings"),
        ),
        mutating=False,
        requires_review=False,
        default_permissions=(),
    ),
    "testing": Recipe(
        name="testing",
        description="Run tests, diagnose failures and report evidence. Read-only.",
        steps=(
            RecipeStep("analyze", role="architect", description="Analyze the test surface"),
            RecipeStep("test", description="Run tests"),
            RecipeStep("report", description="Report results"),
        ),
        mutating=False,
        requires_review=False,
        default_permissions=(),
    ),
    "documentation": Recipe(
        name="documentation",
        description="Update required documentation and review it.",
        steps=(
            RecipeStep("analyze", role="architect", description="Identify doc gaps"),
            RecipeStep("worktree", description="Create an isolated worktree"),
            RecipeStep(
                "implement", role="documentation", description="Update documentation"
            ),
            RecipeStep("review", role="reviewer", description="Review the docs"),
            RecipeStep("merge", description="Merge the worktree branch"),
            RecipeStep("cleanup", description="Remove the worktree"),
        ),
        default_permissions=("write",),
    ),
}


def get_recipe(name: str) -> Recipe:
    try:
        return RECIPES[name]
    except KeyError as exc:
        raise KeyError(f"unknown recipe '{name}'") from exc


def list_recipes() -> list[Recipe]:
    return list(RECIPES.values())
