import unittest

from nao.approval import ApprovalManager
from nao.artifacts import ArtifactManager
from nao.audit import AuditLogger
from nao.models.enums import ApprovalState
from tests.support import temp_dir


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir()
        self.artifacts = ArtifactManager(self.root / "runs")
        self.audit = AuditLogger(self.root / "audit")
        self.approvals = ApprovalManager(self.artifacts, self.audit)

    def test_request_is_pending_and_persisted(self):
        request = self.approvals.request("task-1", "approval:plan", "please review")
        self.assertEqual(request.state, ApprovalState.PENDING)
        pending = self.approvals.pending("task-1")
        self.assertEqual(len(pending), 1)

        # Reload through a fresh manager to prove it is persisted state.
        reloaded = ApprovalManager(self.artifacts, self.audit)
        self.assertEqual(len(reloaded.pending("task-1")), 1)

    def test_duplicate_request_reuses_existing(self):
        first = self.approvals.request("task-1", "approval:plan", "x")
        second = self.approvals.request("task-1", "approval:plan", "x")
        self.assertEqual(first.approval_id, second.approval_id)

    def test_grant_and_deny(self):
        request = self.approvals.request("task-2", "permission:git_merge", "merge")
        self.assertFalse(self.approvals.is_granted("task-2", "permission:git_merge"))
        self.approvals.grant("task-2", request.approval_id, "alice", "looks good")
        self.assertTrue(self.approvals.is_granted("task-2", "permission:git_merge"))

    def test_double_decision_rejected(self):
        request = self.approvals.request("task-3", "permission:git_merge", "merge")
        self.approvals.grant("task-3", request.approval_id, "alice")
        with self.assertRaises(ValueError):
            self.approvals.deny("task-3", request.approval_id, "bob")

    def test_denial_recorded(self):
        request = self.approvals.request("task-4", "permission:deployment", "deploy")
        self.approvals.deny("task-4", request.approval_id, "bob", "not yet")
        self.assertTrue(self.approvals.is_denied("task-4", "permission:deployment"))

    def test_approval_is_never_implicit(self):
        # No request, no grant: the system must not pretend approval happened.
        self.assertFalse(self.approvals.is_granted("task-5", "permission:git_push"))

    def test_audit_events_recorded(self):
        request = self.approvals.request("task-6", "approval:plan", "x")
        self.approvals.grant("task-6", request.approval_id, "alice")
        events = [e["event"] for e in self.audit.events("task-6")]
        self.assertIn("approval_requested", events)
        self.assertIn("approval_granted", events)


if __name__ == "__main__":
    unittest.main()
