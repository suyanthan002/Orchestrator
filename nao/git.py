"""Git Manager (plan.md sections 9 and 14).

NAO owns Git. Agents never decide when to commit or merge.

Safety rules enforced here:

* Destructive commands (``reset --hard``, ``clean -fd``) are refused unless the
  task policy explicitly authorizes them.
* A dirty working tree is never overwritten. NAO records a baseline and, for
  implementation work, prefers an isolated worktree so uncommitted human work
  in the main tree is left untouched.
* Every mutating operation is auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from .audit import AuditLogger
from .errors import GitError
from .launch import LaunchAuthority
from .models.events import EventType
from .process import InternalProcessService, ProcessRunner


@dataclass
class GitState:
    repo_path: str
    is_repo: bool
    branch: Optional[str] = None
    head: Optional[str] = None
    dirty: bool = False
    staged: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "repo_path": self.repo_path,
            "is_repo": self.is_repo,
            "branch": self.branch,
            "head": self.head,
            "dirty": self.dirty,
            "staged": list(self.staged),
            "untracked": list(self.untracked),
            "modified": list(self.modified),
        }


@dataclass
class Baseline:
    task_id: str
    repo_path: str
    branch: Optional[str]
    head: Optional[str]
    dirty: bool
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "repo_path": self.repo_path,
            "branch": self.branch,
            "head": self.head,
            "dirty": self.dirty,
            "created_at": self.created_at,
        }


@dataclass
class Worktree:
    task_id: str
    path: str
    branch: str
    base_ref: Optional[str]
    repo_path: str

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "path": self.path,
            "branch": self.branch,
            "base_ref": self.base_ref,
            "repo_path": self.repo_path,
        }


class GitManager:
    def __init__(
        self,
        repo_path: str | Path,
        audit: Optional[AuditLogger] = None,
        runner: Optional[ProcessRunner] = None,
        internal: Optional[InternalProcessService] = None,
        allow_destructive: bool = False,
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.audit = audit
        if internal is not None:
            self.internal = internal
            self.runner = runner or internal._runner
        else:
            authority = LaunchAuthority()
            self.runner = runner or ProcessRunner(authority.verifier)
            self.internal = InternalProcessService(authority, self.runner)
        self.allow_destructive = allow_destructive

    # -- primitives --------------------------------------------------------
    def _git(
        self, args: list[str], cwd: Optional[str | Path] = None, timeout: float = 120
    ) -> tuple[int, str, str]:
        result = self.internal.run(
            argv=["git", *args],
            cwd=str(cwd or self.repo_path),
            timeout=timeout,
            purpose="git",
        )
        if result.error:
            raise GitError(f"git {' '.join(args)} failed: {result.error}")
        return result.exit_code or 0, result.stdout, result.stderr

    def _git_ok(self, args: list[str], cwd: Optional[str | Path] = None) -> str:
        code, stdout, stderr = self._git(args, cwd)
        if code != 0:
            raise GitError(f"git {' '.join(args)} exited {code}: {stderr.strip()}")
        return stdout

    def is_repo(self) -> bool:
        try:
            code, _, _ = self._git(["rev-parse", "--is-inside-work-tree"])
            return code == 0
        except GitError:
            return False

    def head_commit(self) -> Optional[str]:
        try:
            return self._git_ok(["rev-parse", "HEAD"]).strip()
        except GitError:
            return None

    def current_branch(self) -> Optional[str]:
        try:
            branch = self._git_ok(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
            return branch or None
        except GitError:
            return None

    # -- state -------------------------------------------------------------
    def state(self) -> GitState:
        if not self.is_repo():
            return GitState(repo_path=str(self.repo_path), is_repo=False)

        porcelain = self._git_ok(["status", "--porcelain"])
        staged: list[str] = []
        modified: list[str] = []
        untracked: list[str] = []
        for line in porcelain.splitlines():
            if not line.strip():
                continue
            code = line[:2]
            path = line[3:].strip()
            if code == "??":
                untracked.append(path)
            else:
                if code[0] not in (" ", "?"):
                    staged.append(path)
                if code[1] not in (" ", "?"):
                    modified.append(path)

        return GitState(
            repo_path=str(self.repo_path),
            is_repo=True,
            branch=self.current_branch(),
            head=self.head_commit(),
            dirty=bool(staged or modified or untracked),
            staged=staged,
            untracked=untracked,
            modified=modified,
        )

    # -- baseline ----------------------------------------------------------
    def create_baseline(self, task_id: str) -> Baseline:
        state = self.state()
        if not state.is_repo:
            raise GitError(f"not a git repository: {self.repo_path}")
        baseline = Baseline(
            task_id=task_id,
            repo_path=str(self.repo_path),
            branch=state.branch,
            head=state.head,
            dirty=state.dirty,
        )
        if self.audit:
            self.audit.record(
                EventType.GIT_BASELINE_CREATED,
                task_id=task_id,
                status="dirty" if state.dirty else "clean",
                message=f"baseline at {state.head}",
                data=baseline.to_dict(),
            )
        return baseline

    # -- worktrees ---------------------------------------------------------
    def worktree_root(self) -> Path:
        return self.repo_path.parent / f".nao-worktrees"

    def create_worktree(
        self, task_id: str, base_ref: Optional[str] = None
    ) -> Worktree:
        if not self.is_repo():
            raise GitError(f"not a git repository: {self.repo_path}")

        base = base_ref or self.head_commit()
        if not base:
            raise GitError("cannot create a worktree without a base commit")

        branch = f"nao/{task_id}-{uuid4().hex[:6]}"
        path = self.worktree_root() / branch.replace("/", "-")
        path.parent.mkdir(parents=True, exist_ok=True)

        self._git_ok(["worktree", "add", "-b", branch, str(path), base])
        worktree = Worktree(
            task_id=task_id,
            path=str(path),
            branch=branch,
            base_ref=base,
            repo_path=str(self.repo_path),
        )
        if self.audit:
            self.audit.record(
                EventType.WORKTREE_CREATED,
                task_id=task_id,
                status="created",
                message=f"worktree {branch}",
                data=worktree.to_dict(),
            )
        return worktree

    def list_worktrees(self) -> list[dict]:
        if not self.is_repo():
            return []
        try:
            output = self._git_ok(["worktree", "list", "--porcelain"])
        except GitError:
            return []
        entries: list[dict] = []
        current: dict = {}
        for line in output.splitlines():
            if not line.strip():
                if current:
                    entries.append(current)
                    current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value
        if current:
            entries.append(current)
        return entries

    def remove_worktree(
        self, path: str, force: bool = False, task_id: Optional[str] = None
    ) -> None:
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(path)
        self._git_ok(args)
        if self.audit:
            self.audit.record(
                EventType.WORKTREE_REMOVED,
                task_id=task_id,
                status="removed",
                message=path,
            )

    # -- diff / commit -----------------------------------------------------
    def diff(self, base_ref: Optional[str], cwd: Optional[str | Path] = None) -> str:
        target = cwd or self.repo_path
        # In an isolated NAO worktree, mark untracked files intent-to-add so new
        # files appear in the diff. Never do this to the main working tree.
        if self._in_worktree(target):
            self._git(["add", "--intent-to-add", "."], cwd=target)
        code, stdout, stderr = self._git(["diff", base_ref or "HEAD"], cwd=target)
        if code not in (0, 1):
            raise GitError(f"git diff failed: {stderr.strip()}")
        return stdout

    def _in_worktree(self, path: str | Path) -> bool:
        from .safety import is_within

        try:
            return is_within(Path(path), self.worktree_root())
        except OSError:  # pragma: no cover - defensive
            return False

    def changed_files(
        self, base_ref: Optional[str], cwd: Optional[str | Path] = None
    ) -> list[str]:
        target = cwd or self.repo_path
        try:
            output = self._git_ok(
                ["diff", "--name-only", base_ref or "HEAD"], cwd=target
            )
        except GitError:
            return []
        files = [line.strip() for line in output.splitlines() if line.strip()]
        # Include untracked, non-ignored files in an isolated worktree.
        try:
            untracked = self._git_ok(
                ["ls-files", "--others", "--exclude-standard"], cwd=target
            )
            files.extend(line.strip() for line in untracked.splitlines() if line.strip())
        except GitError:
            pass
        return sorted(set(files))

    def commit(
        self,
        message: str,
        cwd: Optional[str | Path] = None,
        task_id: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> Optional[str]:
        """Stage everything in the (isolated) workspace and commit it."""
        target = cwd or self.repo_path
        self._git_ok(["add", "-A"], cwd=target)
        code, stdout, stderr = self._git(
            ["commit", "-m", message], cwd=target
        )
        if code != 0:
            if "nothing to commit" in (stdout + stderr).lower():
                return None
            raise GitError(f"git commit failed: {stderr.strip()}")
        commit_hash = self._git_ok(["rev-parse", "HEAD"], cwd=target).strip()
        if self.audit:
            self.audit.record(
                EventType.GIT_COMMITTED,
                task_id=task_id,
                job_id=job_id,
                status="committed",
                message=message,
                data={"commit": commit_hash},
            )
        return commit_hash

    def conflicts(self, cwd: Optional[str | Path] = None) -> list[str]:
        try:
            output = self._git_ok(
                ["diff", "--name-only", "--diff-filter=U"], cwd=cwd
            )
        except GitError:
            return []
        return [line.strip() for line in output.splitlines() if line.strip()]

    def merge(
        self,
        branch: str,
        cwd: Optional[str | Path] = None,
        task_id: Optional[str] = None,
        message: Optional[str] = None,
    ) -> dict:
        target = cwd or self.repo_path
        code, stdout, stderr = self._git(
            ["merge", "--no-ff", "-m", message or f"NAO merge {branch}", branch],
            cwd=target,
        )
        if code != 0:
            conflicts = self.conflicts(cwd=target)
            if self.audit:
                self.audit.record(
                    EventType.GIT_CONFLICT,
                    task_id=task_id,
                    status="conflict",
                    message=f"merge of {branch} conflicted",
                    data={"conflicts": conflicts},
                )
            # Safe recovery: abort the merge to restore pre-merge state (plan.md section 9 / F-07)
            try:
                self._git(["merge", "--abort"], cwd=target)
            except Exception:
                pass
            raise GitError(
                f"merge of {branch} failed with conflicts: {conflicts or stderr.strip()}"
            )
        if self.audit:
            self.audit.record(
                EventType.GIT_MERGED,
                task_id=task_id,
                status="merged",
                message=f"merged {branch}",
            )
        return {"branch": branch, "output": stdout.strip()}

    # -- destructive (guarded) --------------------------------------------
    def hard_reset(self, ref: str, cwd: Optional[str | Path] = None) -> None:
        if not self.allow_destructive:
            raise GitError(
                "refusing 'reset --hard': destructive Git is not authorized by "
                "task policy"
            )
        self._git_ok(["reset", "--hard", ref], cwd=cwd)

    def clean(self, cwd: Optional[str | Path] = None) -> None:
        if not self.allow_destructive:
            raise GitError(
                "refusing 'clean -fd': destructive Git is not authorized by "
                "task policy"
            )
        self._git_ok(["clean", "-fd"], cwd=cwd)
