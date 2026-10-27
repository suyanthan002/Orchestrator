"""F-11 regression tests: Session isolation and continuation.

A session must be scoped by (agent, task). Task B must never inherit Task A's
session, and a missing task-specific session must never fall back to a generic
agent-wide session.
"""

import unittest

from nao.adapters.base import BaseCliAdapter
from nao.models.agent_contract import AgentRequest
from nao.models.enums import Role
from nao.process import ProcessRunner
from nao.sessions import SessionManager
from tests.support import dummy_agent_config, temp_dir


def make_request(session_id=None):
    return AgentRequest(
        job_id="job-1",
        role=Role.IMPLEMENTER,
        agent="x",
        task_id="task-1",
        prompt="go",
        session_id=session_id,
    )


class SessionManagerTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir()
        self.sessions = SessionManager(self.root / "sessions.json")

    def test_same_task_continuation(self):
        self.sessions.set("cursor", "sess-1", task_id="task-A", job_id="job-1")
        self.assertEqual(self.sessions.get("cursor", "task-A"), "sess-1")

    def test_different_task_isolation(self):
        self.sessions.set("cursor", "sess-A", task_id="task-A")
        # Task B has never run, so it must NOT see task A's session.
        self.assertIsNone(self.sessions.get("cursor", "task-B"))

    def test_missing_task_never_falls_back_to_agent_wide(self):
        self.sessions.set("cursor", "sess-A", task_id="task-A")
        self.assertIsNone(self.sessions.get("cursor", None))
        self.assertIsNone(self.sessions.get("cursor", "unrelated-task"))

    def test_different_agent_same_task_is_isolated(self):
        self.sessions.set("cursor", "sess-1", task_id="task-A")
        self.assertIsNone(self.sessions.get("cline", "task-A"))

    def test_session_is_persisted(self):
        self.sessions.set("cursor", "sess-1", task_id="task-A", job_id="job-1")
        reloaded = SessionManager(self.root / "sessions.json")
        self.assertEqual(reloaded.get("cursor", "task-A"), "sess-1")


class SessionAdapterTests(unittest.TestCase):
    """Continuation is only emitted when the agent declares support."""

    def setUp(self):
        self.runner = ProcessRunner()

    def test_supported_continuation_emits_flag(self):
        config = dummy_agent_config(
            "cursor",
            supports={"session_continuation": True},
            session_flag="--resume",
        )
        adapter = BaseCliAdapter(config, self.runner)
        self.assertEqual(
            adapter.session_args(make_request("sess-1")),
            ["--resume", "sess-1"],
        )

    def test_unsupported_continuation_starts_fresh(self):
        config = dummy_agent_config("x", supports={"session_continuation": False})
        adapter = BaseCliAdapter(config, self.runner)
        self.assertEqual(adapter.session_args(make_request("sess-1")), [])

    def test_no_session_id_emits_no_flag(self):
        config = dummy_agent_config(
            "x", supports={"session_continuation": True}, session_flag="--resume"
        )
        adapter = BaseCliAdapter(config, self.runner)
        self.assertEqual(adapter.session_args(make_request(None)), [])


if __name__ == "__main__":
    unittest.main()
