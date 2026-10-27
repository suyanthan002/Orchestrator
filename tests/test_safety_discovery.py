import os
import sys
import unittest
from pathlib import Path

from nao.config import AgentConfig
from nao.discovery import clear_discovery_cache, resolve_executable
from nao.errors import PolicyError
from nao.safety import WorkspaceGuard, is_within
from tests.support import temp_dir


class SafetyTests(unittest.TestCase):
    def test_is_within(self):
        root = temp_dir()
        child = root / "a" / "b"
        child.mkdir(parents=True)
        self.assertTrue(is_within(child, root))
        self.assertTrue(is_within(root, root))
        self.assertFalse(is_within(root, child))

    def test_workspace_inside_repo_is_allowed(self):
        root = temp_dir()
        repo = root / "repo"
        repo.mkdir()
        guard = WorkspaceGuard()
        result = guard.validate(str(repo), str(repo))
        self.assertEqual(result, repo.resolve())

    def test_workspace_in_worktree_root_is_allowed(self):
        root = temp_dir()
        repo = root / "repo"
        repo.mkdir()
        worktree = root / ".nao-worktrees" / "x"
        worktree.mkdir(parents=True)
        self.assertEqual(
            WorkspaceGuard().validate(str(worktree), str(repo)),
            worktree.resolve(),
        )

    def test_workspace_outside_repo_is_rejected(self):
        root = temp_dir()
        repo = root / "repo"
        repo.mkdir()
        elsewhere = root / "elsewhere"
        elsewhere.mkdir()
        with self.assertRaises(PolicyError):
            WorkspaceGuard().validate(str(elsewhere), str(repo))

    def test_missing_workspace_is_rejected(self):
        root = temp_dir()
        with self.assertRaises(PolicyError):
            WorkspaceGuard().validate(str(root / "ghost"), str(root))

    def test_empty_workspace_is_rejected(self):
        with self.assertRaises(PolicyError):
            WorkspaceGuard().validate(None, None)


class DiscoveryTests(unittest.TestCase):
    def tearDown(self):
        clear_discovery_cache()

    def test_undiscoverable_agent_returns_none(self):
        config = AgentConfig.from_dict(
            "ghost",
            {"discovery": {"commands": ["nao-not-a-real-binary-xyz"], "paths": []}},
        )
        self.assertIsNone(resolve_executable(config))

    def test_env_override_wins(self):
        os.environ["NAO_TESTBOT_EXECUTABLE"] = sys.executable
        try:
            config = AgentConfig.from_dict(
                "testbot",
                {"discovery": {"commands": ["nao-not-real"], "paths": []}},
            )
            executable = resolve_executable(config)
            self.assertIsNotNone(executable)
            self.assertEqual(executable.argv[0], sys.executable)
            self.assertIn("env override", executable.source)
        finally:
            del os.environ["NAO_TESTBOT_EXECUTABLE"]

    def test_configured_path_wins_over_path_search(self):
        config = AgentConfig.from_dict(
            "testbot",
            {"discovery": {"commands": ["nao-not-real"], "paths": [sys.executable]}},
        )
        executable = resolve_executable(config)
        self.assertIsNotNone(executable)
        self.assertEqual(executable.argv[0], sys.executable)

    def test_shim_parsing_does_not_execute_the_shim(self):
        # A .cmd shim with a bogus node entry should not be returned as a bare
        # .cmd (which cannot be launched with shell=False).
        root = temp_dir()
        shim = root / "fake.cmd"
        shim.write_text(
            '@"node"  "%~dp0\\node_modules\\pkg\\bin\\pkg" %*\n', encoding="utf-8"
        )
        config = AgentConfig.from_dict(
            "shimtest",
            {"discovery": {"commands": [str(shim)], "paths": []}},
        )
        executable = resolve_executable(config)
        # Either we resolved node + a real script, or we fell back to cmd /c.
        if executable:
            self.assertNotEqual(executable.argv[0].lower(), str(shim).lower())


class EnvironmentIsolationTests(unittest.TestCase):
    """F-06 regression tests: Workspace and Environment Isolation."""

    def test_host_secrets_are_stripped_by_default(self):
        from nao.adapters.base import build_sanitized_environment

        os.environ["SUPER_SECRET_KEY"] = "very-secret-token"
        os.environ["GITHUB_TOKEN"] = "ghp_12345678901234567890"
        os.environ["SOME_PASSWORD"] = "mypassword"
        try:
            env = build_sanitized_environment()
            self.assertNotIn("SUPER_SECRET_KEY", env)
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertNotIn("SOME_PASSWORD", env)
            # Standard system keys must still exist
            self.assertTrue(any(k in env for k in ("PATH", "Path", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP")))
        finally:
            os.environ.pop("SUPER_SECRET_KEY", None)
            os.environ.pop("GITHUB_TOKEN", None)
            os.environ.pop("SOME_PASSWORD", None)

    def test_custom_environment_is_passed_and_resolved(self):
        from nao.adapters.base import build_sanitized_environment

        os.environ["SOURCE_VAR"] = "resolved_value"
        try:
            env = build_sanitized_environment(
                {"EXPLICIT_VAR": "hello", "RESOLVED_VAR": "${ENV:SOURCE_VAR}"}
            )
            self.assertEqual(env.get("EXPLICIT_VAR"), "hello")
            self.assertEqual(env.get("RESOLVED_VAR"), "resolved_value")
        finally:
            os.environ.pop("SOURCE_VAR", None)


class SubprocessEnvironmentTests(unittest.TestCase):
    """F-06: agent, Git and test subprocesses all get the sanitized env."""

    def test_generic_subprocess_does_not_inherit_host_secret(self):
        # The internal process service is the path used by Git and test subprocesses.
        from tests.support import trusted_internal_service

        os.environ["NAO_STYLE_HOST_SECRET_TOKEN"] = "should-not-leak"
        try:
            result = trusted_internal_service().run(
                [
                    sys.executable,
                    "-c",
                    "import os; print(os.environ.get('NAO_STYLE_HOST_SECRET_TOKEN'))",
                ],
                cwd=".",
                timeout=30,
                purpose="test",
            )
            self.assertEqual(result.stdout.strip(), "None")
        finally:
            os.environ.pop("NAO_STYLE_HOST_SECRET_TOKEN", None)

    def test_test_manager_subprocess_is_sanitized(self):
        from nao.test_manager import TestManager, TestPlan

        os.environ["NAO_TEST_SECRET_TOKEN"] = "should-not-leak"
        try:
            plan = TestPlan(
                name="env",
                command=[
                    sys.executable,
                    "-c",
                    "import os; print(os.environ.get('NAO_TEST_SECRET_TOKEN'))",
                ],
                cwd=".",
            )
            evidence = TestManager().run(plan, task_id="env-task")
            self.assertNotIn("should-not-leak", evidence.stdout)
        finally:
            os.environ.pop("NAO_TEST_SECRET_TOKEN", None)

    def test_required_launcher_variables_survive_sanitization(self):
        from nao.env import build_sanitized_environment

        env = build_sanitized_environment()
        self.assertTrue(any(key in env for key in ("PATH", "Path")))

    def test_workspace_traversal_is_rejected(self):
        root = temp_dir()
        repo = root / "repo"
        repo.mkdir()
        outside = root / "outside"
        outside.mkdir()
        with self.assertRaises(PolicyError):
            WorkspaceGuard().validate(str(repo / ".." / "outside"), str(repo))


if __name__ == "__main__":
    unittest.main()
