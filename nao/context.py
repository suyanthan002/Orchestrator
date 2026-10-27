"""Context Manager (plan.md sections 6 and 7).

NAO never sends the whole repository to every agent. It builds a **Context
Pack**: task description, acceptance criteria, relevant architecture, relevant
files, previous findings, test results, Git state, constraints, permission
policy and previous failures. Construction is modular so it can be improved
without touching orchestration.

The pack is split into two kinds of knowledge on purpose:

* **project knowledge** - persistent architecture/conventions, supplied by the
  task or the repository documentation;
* **task knowledge** - transient job findings, diffs, tests and failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .artifacts import ArtifactManager
from .models.enums import Permission, Role
from .models.evidence import TestEvidence
from .models.job import AgentJob
from .models.review import Finding
from .models.task import Task
from .safety import is_within

#: Never inline more than this many bytes of a single file.
MAX_FILE_BYTES = 8_000
#: Never inline more than this many files.
MAX_FILES = 20


@dataclass
class ContextSection:
    title: str
    body: str

    def render(self) -> str:
        return f"## {self.title}\n{self.body.strip()}\n"


@dataclass
class ContextPack:
    task_id: str
    job_id: str
    role: Role
    sections: list[ContextSection] = field(default_factory=list)

    def add(self, title: str, body: str) -> "ContextPack":
        if body and body.strip():
            self.sections.append(ContextSection(title, body))
        return self

    def render(self) -> str:
        header = (
            f"# NAO Context Pack\n"
            f"Task: {self.task_id} | Job: {self.job_id} | Role: {self.role.value}\n"
        )
        return header + "\n" + "\n".join(s.render() for s in self.sections)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "job_id": self.job_id,
            "role": self.role.value,
            "sections": [
                {"title": s.title, "body": s.body} for s in self.sections
            ],
        }


class ContextManager:
    def __init__(
        self,
        artifacts: Optional[ArtifactManager] = None,
        project_root: Optional[Path] = None,
        max_file_bytes: int = MAX_FILE_BYTES,
        max_files: int = MAX_FILES,
    ) -> None:
        self.artifacts = artifacts
        self.project_root = Path(project_root).resolve() if project_root else None
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files

    # -- public API --------------------------------------------------------
    def build(
        self,
        task: Task,
        job: AgentJob,
        git_state: Optional[dict[str, Any]] = None,
        findings: Optional[list[Finding]] = None,
        test_evidence: Optional[list[TestEvidence]] = None,
        diff: Optional[str] = None,
        previous_failures: Optional[list[dict[str, Any]]] = None,
    ) -> ContextPack:
        target_root = (
            Path(task.repo_path).resolve()
            if task.repo_path
            else (self.project_root or Path.cwd().resolve())
        )
        pack = ContextPack(task_id=task.task_id, job_id=job.job_id, role=job.role)

        pack.add("Task", self._task_section(task))
        pack.add("Acceptance Criteria", self._criteria_section(task))
        pack.add("Constraints", self._constraints_section(task))
        pack.add("Permission Policy", self._permission_section(job.permissions))

        architecture = task.metadata.get("architecture") or self._project_knowledge(task, target_root)
        pack.add("Relevant Architecture", architecture)

        pack.add("Relevant Files", self._files_section(task, target_root))
        pack.add("Relevant Documentation", self._docs_section(task, target_root))

        if git_state:
            pack.add("Git State", self._git_section(git_state))
        if diff:
            pack.add("Current Diff", self._truncate(diff))
        if findings:
            pack.add("Previous Review Findings", self._findings_section(findings))
        if test_evidence:
            pack.add("Test Results", self._tests_section(test_evidence))
        if previous_failures:
            pack.add(
                "Previous Failed Attempts", self._failures_section(previous_failures)
            )

        pack.add("Role Instruction", self._role_instruction(job.role))

        if self.artifacts:
            self.artifacts.save_context(task.task_id, job.job_id, pack.render())
        return pack

    # -- sections ----------------------------------------------------------
    def _task_section(self, task: Task) -> str:
        lines = [f"Title: {task.title or task.task_type}", "", task.description]
        if task.task_type:
            lines.append(f"\nTask type: {task.task_type}")
        return "\n".join(lines)

    def _criteria_section(self, task: Task) -> str:
        if not task.acceptance_criteria:
            return "No explicit acceptance criteria were provided."
        return "\n".join(f"- {c}" for c in task.acceptance_criteria)

    def _constraints_section(self, task: Task) -> str:
        constraints = task.metadata.get("constraints")
        if not constraints:
            return (
                "Do not modify files outside the assigned workspace. "
                "Do not commit, merge or push; NAO controls Git."
            )
        if isinstance(constraints, list):
            return "\n".join(f"- {c}" for c in constraints)
        return str(constraints)

    def _permission_section(self, permissions: "tuple[Permission, ...]") -> str:
        if not permissions:
            return "No mutating permissions granted; operate read-only."
        return "Granted: " + ", ".join(p.value for p in permissions)

    def _files_section(self, task: Task, target_root: Path) -> str:
        paths = self._select_files(task, target_root)
        if not paths:
            return "No files were selected for this task."
        rendered = []
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rendered.append(
                f"### {self._display_path(path, target_root)}\n```\n{self._truncate(text)}\n```"
            )
        return "\n\n".join(rendered) or "No readable files were selected."

    def _select_files(self, task: Task, target_root: Path) -> list[Path]:
        candidates: list[Path] = []
        explicit = task.metadata.get("relevant_files", [])
        for entry in explicit:
            path = Path(entry)
            if not path.is_absolute():
                path = target_root / path
            # Prevent path traversal outside target_root (F-09)
            if is_within(path, target_root) and path.is_file():
                candidates.append(path)

        for pattern in task.metadata.get("relevant_globs", []):
            for match in sorted(target_root.glob(pattern)):
                if is_within(match, target_root) and match.is_file():
                    candidates.append(match)

        # De-duplicate, cap the count.
        seen: set[Path] = set()
        unique: list[Path] = []
        for path in candidates:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            unique.append(path)
        return unique[: self.max_files]

    def _docs_section(self, task: Task, target_root: Path) -> str:
        docs = task.metadata.get("relevant_docs")
        if not docs:
            return ""
        rendered = []
        for entry in docs:
            path = Path(entry)
            if not path.is_absolute():
                path = target_root / path
            if is_within(path, target_root) and path.is_file():
                rendered.append(
                    f"### {self._display_path(path, target_root)}\n{self._truncate(path.read_text(encoding='utf-8', errors='replace'))}"
                )
        return "\n\n".join(rendered)

    def _project_knowledge(self, task: Task, target_root: Path) -> str:
        knowledge = []
        # Target project architecture / conventions
        target_docs = ["ARCHITECTURE.md", "README.md", "CONTRIBUTING.md"]
        if task.metadata.get("include_nao_docs") or (target_root / "nao").is_dir():
            target_docs.append("doc/plan.md")

        for name in target_docs:
            path = target_root / name
            if is_within(path, target_root) and path.is_file():
                knowledge.append(
                    f"### {name}\n{self._truncate(path.read_text(encoding='utf-8', errors='replace'))}"
                )
        return "\n\n".join(knowledge)

    def _git_section(self, state: dict[str, Any]) -> str:
        lines = [
            f"- repository: {state.get('repo_path')}",
            f"- branch: {state.get('branch')}",
            f"- head: {state.get('head')}",
            f"- dirty: {state.get('dirty')}",
            f"- worktree: {state.get('worktree')}",
        ]
        return "\n".join(lines)

    def _findings_section(self, findings: list[Finding]) -> str:
        lines = []
        for f in findings:
            where = f" ({f.file})" if f.file else ""
            lines.append(
                f"- [{f.severity}/{f.status.value}] {f.title}{where}: {f.description}"
            )
        return "\n".join(lines)

    def _tests_section(self, evidence: list[TestEvidence]) -> str:
        lines = []
        for e in evidence:
            lines.append(
                f"- {e.kind}: {e.status} "
                f"(passed={e.passed}, failed={e.failed}, exit={e.exit_code})"
            )
            if e.status != "passed" and e.stderr:
                lines.append(f"  stderr: {self._truncate(e.stderr, 2000)}")
        return "\n".join(lines)

    def _failures_section(self, failures: list[dict[str, Any]]) -> str:
        lines = []
        for failure in failures:
            lines.append(
                f"- {failure.get('failure_class')}: {failure.get('message')}"
            )
        return "\n".join(lines)

    def _role_instruction(self, role: Role) -> str:
        instructions = {
            Role.ARCHITECT: (
                "Produce a concise plan and decomposition. Do not modify code."
            ),
            Role.IMPLEMENTER: (
                "Implement the change inside the workspace. Keep the diff minimal "
                "and do not touch files outside the scope."
            ),
            Role.REVIEWER: (
                "Independently review the diff against the acceptance criteria and "
                "return structured findings. Do not modify code."
            ),
            Role.TESTER: (
                "Run or design tests and report concrete evidence, not assurances."
            ),
            Role.DEBUGGER: "Diagnose the failure and propose the smallest fix.",
            Role.SECURITY_REVIEWER: (
                "Review specifically for security concerns and return findings."
            ),
            Role.DOCUMENTATION: "Update only the required documentation.",
            Role.FINAL_VERIFIER: (
                "Verify each acceptance criterion against the actual diff and test "
                "evidence. Report pass/fail per criterion."
            ),
        }
        return instructions.get(role, "Complete the assigned job.")

    def _truncate(self, text: str, limit: Optional[int] = None) -> str:
        limit = limit or self.max_file_bytes
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"

    def _display_path(self, path: Path, target_root: Optional[Path] = None) -> str:
        target = target_root or self.project_root
        try:
            if target:
                return str(path.resolve().relative_to(target.resolve()))
        except ValueError:
            pass
        return str(path)
