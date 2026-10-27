import unittest

from nao.approval import ApprovalManager
from nao.artifacts import ArtifactManager
from nao.audit import AuditLogger
from nao.config import NaoConfig
from nao.models.enums import ApprovalLevel, Permission
from nao.policy import PermissionPolicy
from tests.support import temp_dir


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir()
        self.artifacts = ArtifactManager(self.root / "runs")
        self.audit = AuditLogger(self.root / "audit")
        self.approvals = ApprovalManager(self.artifacts, self.audit)
        self.policy = PermissionPolicy(NaoConfig(), self.approvals, self.audit)

    def test_level_zero_is_automatic(self):
        for permission in (Permission.READ, Permission.SEARCH, Permission.GIT_READ):
            decision = self.policy.evaluate(permission, granted=[])
            self.assertTrue(decision.allowed, permission)
            self.assertEqual(decision.level, ApprovalLevel.AUTOMATIC)

    def test_level_one_requires_task_grant(self):
        denied = self.policy.evaluate(Permission.WRITE, granted=[])
        self.assertFalse(denied.allowed)
        self.assertFalse(denied.requires_approval)
        allowed = self.policy.evaluate(Permission.WRITE, granted=[Permission.WRITE])
        self.assertTrue(allowed.allowed)

    def test_level_two_requires_human_approval(self):
        pending = self.policy.evaluate(
            Permission.GIT_COMMIT, granted=[], task_id="task-1"
        )
        self.assertFalse(pending.allowed)
        self.assertTrue(pending.requires_approval)

        request = self.approvals.request(
            "task-1", "permission:git_commit", "test"
        )
        self.approvals.grant("task-1", request.approval_id, "human")
        granted = self.policy.evaluate(
            Permission.GIT_COMMIT, granted=[], task_id="task-1"
        )
        self.assertTrue(granted.allowed)

    def test_level_three_is_never_automatic(self):
        decision = self.policy.evaluate(
            Permission.GIT_PUSH, granted=[Permission.GIT_PUSH], task_id="task-1"
        )
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.requires_approval)
        self.assertEqual(decision.level, ApprovalLevel.ALWAYS_HUMAN)

    def test_explicit_denial_wins(self):
        request = self.approvals.request("task-2", "permission:deployment", "x")
        self.approvals.deny("task-2", request.approval_id, "human")
        decision = self.policy.evaluate(
            Permission.DEPLOYMENT, granted=[], task_id="task-2"
        )
        self.assertFalse(decision.allowed)
        self.assertFalse(decision.requires_approval)

    def test_authorize_all_splits_effective_and_blocking(self):
        effective, blocking = self.policy.authorize_all(
            [Permission.READ, Permission.WRITE, Permission.GIT_MERGE],
            granted=[Permission.WRITE],
            task_id="task-3",
        )
        self.assertIn(Permission.READ, effective)
        self.assertIn(Permission.WRITE, effective)
        self.assertEqual([d.permission for d in blocking], [Permission.GIT_MERGE])

    def test_policy_decisions_are_audited(self):
        self.policy.evaluate(Permission.WRITE, granted=[], task_id="task-4")
        events = self.audit.events("task-4")
        self.assertTrue(any(e["event"] == "permission_denied" for e in events))

    def test_config_can_override_levels(self):
        config = NaoConfig()
        config.approval_levels = {Permission.WRITE: ApprovalLevel.AUTOMATIC}
        policy = PermissionPolicy(config, self.approvals, self.audit)
        self.assertTrue(policy.evaluate(Permission.WRITE, granted=[]).allowed)


if __name__ == "__main__":
    unittest.main()
