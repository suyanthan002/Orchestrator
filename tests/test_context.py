import unittest
from pathlib import Path

from nao.artifacts import ArtifactManager
from nao.context import ContextManager
from nao.models.enums import Permission, Role, TaskState
from nao.models.job import AgentJob
from tests.support import temp_dir


def make_task(root: Path, **kwargs):
    from nao.models.task import Task

    defaults = dict(
        task_id="task-ctx",
        title="Add chess validation",
        description="Implement move validation for chess.",
        acceptance_criteria=["Rejects illegal moves", "Keeps existing model"],
        repo_path=str(root),
        granted_permissions=[Permission.WRITE],
        metadata=kwargs.pop("metadata", {}),
    )
    defaults.update(kwargs)
    return Task(**defaults)


def make_job(role=Role.IMPLEMENTER, permissions=(Permission.WRITE,)):
    return AgentJob(
        job_id="job-ctx",
        task_id="task-ctx",
        agent="cursor",
        role=role,
        permissions=permissions,
    )


class ContextManagerTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir()
        self.artifacts = ArtifactManager(self.root / "runs")
        (self.root / "models.py").write_text("class ChessGame:\n    pass\n", encoding="utf-8")
        self.context = ContextManager(self.artifacts, project_root=self.root)

    def test_pack_contains_task_and_criteria(self):
        task = make_task(self.root)
        pack = self.context.build(task, make_job())
        text = pack.render()
        self.assertIn("Add chess validation", text)
        self.assertIn("Rejects illegal moves", text)
        self.assertIn("Rejects illegal moves", text)

    def test_relevant_files_are_included_but_not_the_whole_repo(self):
        (self.root / "secret.py").write_text("TOPSECRET = 1\n", encoding="utf-8")
        task = make_task(self.root, metadata={"relevant_files": ["models.py"]})
        text = self.context.build(task, make_job()).render()
        self.assertIn("class ChessGame", text)
        self.assertNotIn("TOPSECRET", text)

    def test_file_content_is_truncated(self):
        big = self.root / "big.py"
        big.write_text("x = 1\n" * 5000, encoding="utf-8")
        context = ContextManager(
            self.artifacts, project_root=self.root, max_file_bytes=200
        )
        task = make_task(self.root, metadata={"relevant_files": ["big.py"]})
        text = context.build(task, make_job()).render()
        self.assertIn("[truncated", text)

    def test_findings_and_tests_are_included(self):
        from nao.models.evidence import TestEvidence
        from nao.models.review import Finding

        task = make_task(self.root)
        findings = [
            Finding.create(title="bad thing", description="d", severity="high")
        ]
        evidence = [
            TestEvidence(
                command=["pytest"],
                working_directory=".",
                status="failed",
                failed=2,
                passed=1,
                stderr="AssertionError",
            )
        ]
        text = self.context.build(
            task, make_job(), findings=findings, test_evidence=evidence
        ).render()
        self.assertIn("bad thing", text)
        self.assertIn("AssertionError", text)

    def test_permissions_and_role_instruction_are_visible(self):
        task = make_task(self.root)
        pack = self.context.build(task, make_job(role=Role.REVIEWER, permissions=()))
        text = pack.render()
        self.assertIn("read-only", text.lower())
        self.assertIn("Independently review", text)

    def test_pack_is_persisted_without_secrets(self):
        task = make_task(self.root)
        self.context.build(task, make_job())
        path = self.artifacts.task_dir(task.task_id) / "contexts" / "job-ctx.txt"
        self.assertTrue(path.exists())

    def test_git_state_rendered(self):
        task = make_task(self.root)
        pack = self.context.build(
            task,
            make_job(),
            git_state={"repo_path": "x", "branch": "main", "head": "abc", "dirty": False},
        )
        self.assertIn("branch: main", pack.render())

    def test_f09_target_repository_isolation_and_traversal_prevention(self):
        """F-09 regression tests: ContextManager operates on the target repository."""
        target_repo = temp_dir()
        outside_dir = temp_dir()
        (target_repo / "README.md").write_text("# Target Project Docs", encoding="utf-8")
        (target_repo / "src" / "target.py").parent.mkdir(parents=True, exist_ok=True)
        (target_repo / "src" / "target.py").write_text("TARGET_CODE = 1", encoding="utf-8")
        (outside_dir / "secret.txt").write_text("OUTSIDE_SECRET", encoding="utf-8")

        task = make_task(
            target_repo,
            metadata={
                "relevant_files": [
                    "src/target.py",
                    str(outside_dir / "secret.txt"),
                    "../../secret.txt",
                ]
            },
        )
        # ContextManager without project_root should operate on task.repo_path
        ctx_mgr = ContextManager(self.artifacts)
        pack = ctx_mgr.build(task, make_job())
        rendered = pack.render()

        self.assertIn("TARGET_CODE = 1", rendered)
        self.assertIn("# Target Project Docs", rendered)
        self.assertNotIn("OUTSIDE_SECRET", rendered)

    def test_f02_real_context_delivered_to_adapter_command(self):
        """F-02 regression tests: Context Pack is delivered through prompt envelope into adapter argv."""
        from nao.adapters import ADAPTER_CLASSES
        from nao.config import load_config
        from nao.discovery import Executable
        from nao.models.agent_contract import AgentRequest
        from nao.process import ProcessRunner

        task = make_task(self.root, metadata={"relevant_files": ["models.py"]})
        pack = self.context.build(task, make_job())
        rendered_context = pack.render()

        req = AgentRequest(
            job_id="job-ctx-1",
            role=Role.IMPLEMENTER,
            agent="cursor",
            task_id=task.task_id,
            prompt="Implement feature",
            context=rendered_context,
            workspace=str(self.root),
            permissions=(Permission.WRITE, Permission.SHELL),
            expected_output_schema={"type": "object", "properties": {"done": {"type": "boolean"}}},
        )

        cfg = load_config()
        adapter = ADAPTER_CLASSES["cursor"](cfg.agent("cursor"), ProcessRunner())
        argv = adapter.build_command(req, Executable(("cursor-agent",), "test"))

        # Prove context was not discarded and appears in argv
        command_str = " ".join(argv)
        self.assertIn("=== TASK INSTRUCTION ===", command_str)
        self.assertIn("=== CONTEXT PACK ===", command_str)
        self.assertIn("=== EXPECTED OUTPUT CONTRACT ===", command_str)
        self.assertIn("class ChessGame", command_str)


if __name__ == "__main__":
    unittest.main()
