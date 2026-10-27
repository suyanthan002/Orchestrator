import unittest
from pathlib import Path

from nao.errors import GitError
from nao.git import GitManager
from tests.support import init_repo, run_git, temp_dir


class GitManagerTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir()
        self.repo = init_repo(self.root / "repo")
        self.git = GitManager(self.repo)

    def test_state_is_clean_after_init(self):
        state = self.git.state()
        self.assertTrue(state.is_repo)
        self.assertFalse(state.dirty)
        self.assertIsNotNone(state.head)

    def test_state_detects_dirty_tree(self):
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        (self.repo / "new.txt").write_text("x", encoding="utf-8")
        state = self.git.state()
        self.assertTrue(state.dirty)
        self.assertIn("app.py", state.modified)
        self.assertIn("new.txt", state.untracked)

    def test_baseline_records_dirty_flag(self):
        (self.repo / "app.py").write_text("value = 3\n", encoding="utf-8")
        baseline = self.git.create_baseline("task-1")
        self.assertTrue(baseline.dirty)
        self.assertEqual(baseline.head, self.git.head_commit())

    def test_not_a_repository(self):
        plain = temp_dir()
        git = GitManager(plain)
        self.assertFalse(git.is_repo())

    def test_worktree_lifecycle(self):
        worktree = self.git.create_worktree("task-2")
        path = Path(worktree.path)
        self.assertTrue(path.exists())
        self.assertTrue(worktree.branch.startswith("nao/"))

        (path / "feature.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        files = self.git.changed_files(worktree.base_ref, cwd=path)
        self.assertIn("feature.py", files)
        diff = self.git.diff(worktree.base_ref, cwd=path)
        self.assertIn("feature.py", diff)

        commit = self.git.commit("add feature", cwd=path, task_id="task-2")
        self.assertIsNotNone(commit)

        self.git.merge(worktree.branch, task_id="task-2")
        self.assertTrue((self.repo / "feature.py").exists())

        self.git.remove_worktree(worktree.path, force=True)
        self.assertFalse(path.exists())

    def test_untracked_files_are_reported(self):
        worktree = self.git.create_worktree("task-3")
        path = Path(worktree.path)
        (path / "brand_new.py").write_text("x = 1\n", encoding="utf-8")
        files = self.git.changed_files(worktree.base_ref, cwd=path)
        self.assertIn("brand_new.py", files)

    def test_hard_reset_is_refused_by_default(self):
        with self.assertRaises(GitError):
            self.git.hard_reset("HEAD")

    def test_clean_is_refused_by_default(self):
        with self.assertRaises(GitError):
            self.git.clean()

    def test_destructive_allowed_when_authorized(self):
        git = GitManager(self.repo, allow_destructive=True)
        (self.repo / "junk.txt").write_text("junk", encoding="utf-8")
        git.clean()
        self.assertFalse((self.repo / "junk.txt").exists())

    def test_conflict_detection(self):
        self.assertEqual(self.git.conflicts(), [])

    def test_merge_conflict_raises_and_aborts_cleanly(self):
        # Two branches editing the same line will conflict.
        git = self.git
        worktree = git.create_worktree("task-conflict")
        path = Path(worktree.path)
        (path / "app.py").write_text("value = 100\n", encoding="utf-8")
        git.commit("branch change", cwd=path)
        (self.repo / "app.py").write_text("value = 200\n", encoding="utf-8")
        git.commit("main change")

        with self.assertRaises(GitError) as ctx:
            git.merge(worktree.branch, task_id="task-conflict")
        self.assertIn("conflicts", str(ctx.exception).lower())

        # Safe recovery (F-07): target repository is not left in a conflicted unmerged state
        self.assertEqual(git.conflicts(), [])
        self.assertEqual((self.repo / "app.py").read_text(encoding="utf-8"), "value = 200\n")


if __name__ == "__main__":
    unittest.main()
