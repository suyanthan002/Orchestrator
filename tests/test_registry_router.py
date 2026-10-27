import sys
import unittest

from nao.config import AgentConfig, NaoConfig
from nao.models.enums import Capability, Permission, Role
from nao.process import ProcessRunner
from nao.registry import AgentRegistry
from nao.router import AgentRouter, RoutingError


def agent(name, caps, priority=0, enabled=True, commands=None):
    # Point discovery at the running interpreter so the agent is 'available'
    # without depending on any real agent being installed.
    commands = commands if commands is not None else [sys.executable]
    return AgentConfig.from_dict(
        name,
        {
            "enabled": enabled,
            "priority": priority,
            "capabilities": [c.value for c in caps],
            "discovery": {"commands": list(commands), "paths": []},
        },
    )


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.config = NaoConfig(
            agents={
                "impl": agent("impl", [Capability.IMPLEMENTATION, Capability.FILE_EDIT]),
                "rev": agent("rev", [Capability.CODE_REVIEW, Capability.READ_ONLY_ANALYSIS]),
                "off": agent("off", [Capability.IMPLEMENTATION], enabled=False),
            }
        )
        self.registry = AgentRegistry(self.config, ProcessRunner())

    def test_specs_and_lookup(self):
        self.assertEqual(set(self.registry.names()), {"impl", "rev", "off"})
        self.assertTrue(self.registry.spec("impl").has_capability(Capability.FILE_EDIT))

    def test_enabled_names_excludes_disabled(self):
        self.assertNotIn("off", self.registry.enabled_names())

    def test_unknown_agent_raises(self):
        with self.assertRaises(Exception):
            self.registry.adapter("ghost")

    def test_capability_query_respects_enabled(self):
        names = self.registry.with_capabilities(
            [Capability.IMPLEMENTATION], available_only=False
        )
        self.assertEqual(names, ["impl"])

    def test_doctor_shape(self):
        report = self.registry.doctor()
        self.assertEqual(len(report), 3)
        for item in report:
            self.assertIn("available", item)
            self.assertIn("reason", item)


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.config = NaoConfig(
            agents={
                "a": agent("a", [Capability.IMPLEMENTATION, Capability.FILE_EDIT]),
                "b": agent("b", [Capability.IMPLEMENTATION, Capability.FILE_EDIT]),
                "r": agent("r", [Capability.CODE_REVIEW, Capability.READ_ONLY_ANALYSIS]),
                "off": agent("off", [Capability.IMPLEMENTATION], enabled=False),
            },
            routing={
                Role.IMPLEMENTER: ["b", "a"],
                Role.REVIEWER: ["r"],
            },
        )
        self.registry = AgentRegistry(self.config, ProcessRunner())
        self.router = AgentRouter(self.registry, self.config)

    def test_uses_configured_preference_order(self):
        decision = self.router.select(Role.IMPLEMENTER)
        self.assertEqual(decision.agent, "b")
        self.assertEqual(decision.candidates, ["b", "a"])

    def test_capability_filter_rejects(self):
        with self.assertRaises(RoutingError):
            self.router.select(Role.REVIEWER, required_capabilities=[Capability.SHELL])

    def test_permission_compatibility_filter(self):
        # 'r' has no file_edit capability, so it cannot exercise WRITE.
        decision = self.router.select(Role.REVIEWER)
        self.assertEqual(decision.agent, "r")
        with self.assertRaises(RoutingError):
            self.router.select(
                Role.REVIEWER,
                granted_permissions=[Permission.WRITE],
            )

    def test_excluded_agents_are_skipped(self):
        decision = self.router.select(Role.IMPLEMENTER, exclude={"b"})
        self.assertEqual(decision.agent, "a")
        self.assertTrue(any(name == "b" for name, _ in decision.rejected))

    def test_disabled_agents_rejected(self):
        with self.assertRaises(RoutingError):
            self.router.select(Role.IMPLEMENTER, exclude={"a", "b"})

    def test_task_routing_override_wins(self):
        decision = self.router.select(Role.IMPLEMENTER, overrides={"implementer": "a"})
        self.assertEqual(decision.agent, "a")
        self.assertIn("override", decision.reason)

    def test_rejection_reasons_are_explicit(self):
        with self.assertRaises(RoutingError) as ctx:
            self.router.select(Role.IMPLEMENTER, exclude={"a", "b"})
        reasons = dict(ctx.exception.rejected)
        self.assertTrue(reasons)
        self.assertTrue(all(reason for reason in reasons.values()))

    def test_availability_only_rejections_are_classified(self):
        cfg = NaoConfig(
            agents={
                "ghost": agent("ghost", [Capability.CODE_REVIEW], commands=["nao-not-real-xyz"])
            },
            routing={Role.REVIEWER: ["ghost"]},
        )
        router = AgentRouter(AgentRegistry(cfg, ProcessRunner()), cfg)
        with self.assertRaises(RoutingError) as ctx:
            router.select(Role.REVIEWER)
        self.assertTrue(ctx.exception.is_availability_only())


class IndependentReviewerRoutingTests(unittest.TestCase):
    """F-12 regression tests: the reviewer is explicitly not the implementer."""

    def setUp(self):
        agents = {
            "impl": agent("impl", [Capability.IMPLEMENTATION, Capability.FILE_EDIT]),
            "rev1": agent("rev1", [Capability.CODE_REVIEW, Capability.READ_ONLY_ANALYSIS]),
            "rev2": agent("rev2", [Capability.CODE_REVIEW, Capability.READ_ONLY_ANALYSIS]),
        }
        config = NaoConfig(
            agents=agents,
            routing={
                Role.IMPLEMENTER: ["impl"],
                Role.REVIEWER: ["rev1", "rev2"],
            },
        )
        self.router = AgentRouter(AgentRegistry(config, ProcessRunner()), config)

    def test_normal_routing_picks_preferred_reviewer(self):
        self.assertEqual(self.router.select(Role.REVIEWER).agent, "rev1")

    def test_implementer_exclusion_keeps_review_independent(self):
        decision = self.router.select(Role.REVIEWER, exclude={"impl"})
        self.assertNotEqual(decision.agent, "impl")

    def test_multiple_eligible_agents_fall_through(self):
        decision = self.router.select(Role.REVIEWER, exclude={"rev1"})
        self.assertEqual(decision.agent, "rev2")

    def test_only_one_eligible_agent_escalates(self):
        with self.assertRaises(RoutingError):
            self.router.select(Role.REVIEWER, exclude={"rev1", "rev2"})

    def test_routing_override_selects_alternate_reviewer(self):
        decision = self.router.select(
            Role.REVIEWER, overrides={"reviewer": "rev2"}
        )
        self.assertEqual(decision.agent, "rev2")


if __name__ == "__main__":
    unittest.main()
