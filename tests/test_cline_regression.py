"""Regression tests for the preserved Cline prototype (plan.md section 26).

The prototype's parser, adapter and result shape are already verified in
production. These tests pin that behaviour so the new platform cannot silently
regress it.
"""

import json
import unittest

from adapters.cline import ClineAdapter as LegacyClineAdapter
from nao.adapters.cline import ClineAdapter
from nao.config import load_config
from nao.models.agent_contract import AgentRequest
from nao.models.enums import Permission, Role
from nao.process import ProcessResult, ProcessRunner
from orchestrator.parser import parse_cline_output

SAMPLE = "\n".join(
    [
        json.dumps({"type": "system", "message": "Cline started"}),
        json.dumps({"type": "tool_call", "tool": "read_files"}),
        json.dumps({"type": "tool_result", "tool": "read_files"}),
        json.dumps(
            {
                "type": "run_result",
                "finishReason": "completed",
                "text": "The function greets a name.",
                "iterations": 2,
                "usage": {
                    "inputTokens": 9065,
                    "outputTokens": 259,
                    "cacheReadTokens": 0,
                    "cacheWriteTokens": 0,
                    "totalCost": 0,
                },
                "sessionId": "sess-123",
            }
        ),
    ]
)


class LegacyParserRegression(unittest.TestCase):
    """The original prototype parser must keep working unchanged."""

    def test_completed_run_is_successful(self):
        result = parse_cline_output(SAMPLE)
        self.assertTrue(result.success)
        self.assertEqual(result.finish_reason, "completed")
        self.assertEqual(result.final_response, "The function greets a name.")
        self.assertEqual(result.iterations, 2)
        self.assertEqual(result.usage["inputTokens"], 9065)

    def test_non_completed_run_is_not_successful(self):
        output = json.dumps(
            {"type": "run_result", "finishReason": "max_tokens", "text": "partial"}
        )
        result = parse_cline_output(output)
        self.assertFalse(result.success)
        self.assertEqual(result.finish_reason, "max_tokens")

    def test_non_json_output_is_ignored(self):
        result = parse_cline_output("plain text output\nnot json")
        self.assertFalse(result.success)
        self.assertIsNone(result.final_response)


class ClineAdapterRegression(unittest.TestCase):
    def setUp(self):
        self.adapter = ClineAdapter(load_config().agent("cline"), ProcessRunner())

    def _request(self, permissions=()):
        return AgentRequest(
            job_id="job-cline",
            role=Role.ARCHITECT,
            agent="cline",
            task_id="task-cline",
            prompt="analyze",
            permissions=tuple(permissions),
        )

    def test_parse_matches_legacy_parser(self):
        result = ProcessResult(command=["cline"], exit_code=0, stdout=SAMPLE)
        response = self.adapter.parse(self._request(), result)
        legacy = parse_cline_output(SAMPLE)

        self.assertTrue(response.succeeded)
        self.assertEqual(response.response, legacy.final_response)
        self.assertEqual(response.usage, legacy.usage)
        self.assertEqual(
            response.native_metadata["iterations"], legacy.iterations
        )
        self.assertEqual(response.native_metadata["finish_reason"], "completed")
        self.assertEqual(response.session_id, "sess-123")

    def test_incomplete_finish_reason_marks_failure(self):
        output = json.dumps(
            {"type": "run_result", "finishReason": "aborted", "text": "stopped"}
        )
        result = ProcessResult(command=["cline"], exit_code=0, stdout=output)
        response = self.adapter.parse(self._request(), result)
        self.assertEqual(response.status, "failed")
        self.assertEqual(
            response.errors[0]["failure_class"], "implementation_failure"
        )

    def test_plan_mode_for_read_only_job(self):
        from nao.discovery import Executable

        argv = self.adapter.build_command(
            self._request(permissions=[Permission.READ]),
            Executable(("node", "cline-bin"), "test"),
        )
        self.assertIn("--json", argv)
        self.assertIn("--plan", argv)
        self.assertNotIn("--auto-approve=true", argv)

    def test_legacy_adapter_class_still_importable(self):
        # The original top-level adapter must remain loadable for the prototype.
        self.assertEqual(LegacyClineAdapter.name, "cline")

    def test_legacy_adapter_has_no_hardcoded_user_path(self):
        import inspect

        from adapters import cline as legacy

        source = inspect.getsource(legacy)
        self.assertNotIn("C:\\\\Users\\\\", source)
        command = legacy.build_cline_command()
        self.assertIn("--json", command)
        self.assertIn("--plan", command)


if __name__ == "__main__":
    unittest.main()
