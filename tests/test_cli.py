import io
import json
import unittest
from contextlib import redirect_stdout

from nao.cli import build_parser, main


class CliTests(unittest.TestCase):
    def _run(self, argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_parser_has_expected_commands(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([])

    def test_inspect_json(self):
        code, output = self._run(["--json", "inspect"])
        self.assertEqual(code, 0)
        data = json.loads(output)
        self.assertEqual(len(data["agents"]), 7)

    def test_agent_doctor_human(self):
        code, output = self._run(["agent", "doctor"])
        self.assertEqual(code, 0)
        self.assertIn("cline", output)

    def test_agent_list_json(self):
        code, output = self._run(["--json", "agent", "list"])
        self.assertEqual(code, 0)
        self.assertIsInstance(json.loads(output), list)

    def test_workflow_list(self):
        code, output = self._run(["--json", "workflow", "list"])
        self.assertEqual(code, 0)
        names = {r["name"] for r in json.loads(output)}
        self.assertIn("feature_implementation", names)
        self.assertIn("bug_fix", names)
        self.assertIn("security_audit", names)

    def test_git_status_on_default_repo(self):
        code, output = self._run(["--json", "git", "status"])
        self.assertEqual(code, 0)
        self.assertIn("is_repo", json.loads(output))

    def test_task_list_runs(self):
        code, output = self._run(["task", "list"])
        self.assertEqual(code, 0)

    def test_unknown_task_status(self):
        code, output = self._run(["task", "status", "task-does-not-exist"])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output)["exists"])

    def test_approve_without_subject_errors(self):
        code, output = self._run(["approve", "task-x"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
