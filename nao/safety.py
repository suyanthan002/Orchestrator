"""Workspace safety and isolation boundaries (plan.md section 24 / F-06).

Isolation model:
1. Git workspace isolation: Isolated git worktrees (.nao-worktrees/) keep
   uncommitted changes and branches separate from the main tree.
2. Process isolation: Child processes are launched via subprocess.Popen with
   shell=False, tracked by PID/job_id, and cancellable.
3. Environment isolation: Child processes receive a sanitized environment
   constructed from an explicit allowlist; host secrets are stripped by default.
4. Filesystem containment: The WorkspaceGuard validates that assigned working
   directories lie strictly within authorized repository or worktree roots.
5. Explicit limitation: Worktrees and cwd validation do NOT constitute an OS-level
   kernel sandbox on Windows. Native agent processes run with host user privileges.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from .errors import PolicyError


def is_within(child: Path, parent: Path) -> bool:
    """Return True when ``child`` is inside ``parent`` (or equals it)."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


class WorkspaceGuard:
    def __init__(self, allowed_roots: Optional[Iterable[str]] = None) -> None:
        self.allowed_roots = [Path(p).resolve() for p in (allowed_roots or [])]

    def validate(
        self,
        workspace: Optional[str],
        repo_path: Optional[str],
    ) -> Path:
        """Ensure the workspace is authorized; return the resolved path."""
        if not workspace:
            raise PolicyError("no workspace assigned for this job")

        workspace_path = Path(workspace).resolve()
        if not workspace_path.exists():
            raise PolicyError(f"workspace does not exist: {workspace_path}")

        roots: list[Path] = list(self.allowed_roots)
        if repo_path:
            repo = Path(repo_path).resolve()
            roots.append(repo)
            # NAO worktrees live beside the repository: <parent>/.nao-worktrees
            worktree_root = repo.parent / ".nao-worktrees"
            roots.append(worktree_root)

        if roots and not any(is_within(workspace_path, root) for root in roots):
            raise PolicyError(
                f"workspace {workspace_path} is outside the authorized roots "
                f"({', '.join(str(r) for r in roots)})"
            )
        return workspace_path
