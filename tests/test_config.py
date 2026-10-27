import json
import os
import unittest
from pathlib import Path

from nao.config import AgentConfig, load_config, redact, resolve_env
from nao.errors import ConfigError
from nao.models.enums import Capability
from tests.support import temp_dir


class ConfigTests(unittest.TestCase):
    def test_loads_all_seven_agents(self):
        config = load_config()
        self.assertEqual(len(config.agents), 7)
        self.assertEqual(
            set(config.agents),
            {"cline", "kilo", "goose", "aider", "copilot", "cursor", "antigravity"},
        )

    def test_every_agent_declares_capabilities(self):
        config = load_config()
        for agent in config.agents.values():
            self.assertTrue(agent.capabilities, f"{agent.name} has no capabilities")

    def test_capabilities_are_typed(self):
        config = load_config()
        self.assertIn(Capability.IMPLEMENTATION, config.agents["cline"].capabilities)

    def test_routing_references_real_agents(self):
        config = load_config()
        for role, agents in config.routing.items():
            self.assertTrue(agents)
            for agent in agents:
                self.assertIn(agent, config.agents)

    def test_resource_default_is_single_agent(self):
        self.assertEqual(load_config().resources.max_concurrent_agents, 1)

    def test_unknown_capability_rejected(self):
        with self.assertRaises(ConfigError):
            AgentConfig.from_dict("x", {"capabilities": ["not-a-capability"]})

    def test_invalid_routing_role_rejected(self):
        root = temp_dir()
        agents = root / "agents.json"
        agents.write_text(
            json.dumps({"agents": {"cline": {"capabilities": ["implementation"]}}}),
            encoding="utf-8",
        )
        nao = root / "nao.json"
        nao.write_text(json.dumps({"routing": {"not-a-role": ["cline"]}}), encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(agents, nao)

    def test_routing_unknown_agent_rejected(self):
        root = temp_dir()
        agents = root / "agents.json"
        agents.write_text(
            json.dumps({"agents": {"cline": {"capabilities": ["implementation"]}}}),
            encoding="utf-8",
        )
        nao = root / "nao.json"
        nao.write_text(json.dumps({"routing": {"reviewer": ["ghost"]}}), encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(agents, nao)

    def test_missing_file_raises(self):
        with self.assertRaises(ConfigError):
            load_config(Path("does-not-exist.json"), Path("also-missing.json"))

    def test_resolve_env(self):
        os.environ["NAO_TEST_SECRET"] = "s3cr3t"
        try:
            self.assertEqual(resolve_env("${ENV:NAO_TEST_SECRET}"), "s3cr3t")
        finally:
            del os.environ["NAO_TEST_SECRET"]
        self.assertIsNone(resolve_env("${ENV:NAO_TEST_SECRET}"))
        self.assertIsNone(resolve_env("literal-value"))

    def test_redact_masks_secrets(self):
        self.assertNotIn("abcd1234", redact("api_key = abcd1234"))
        self.assertIn("[redacted", redact("api_key=abcd1234"))
        self.assertNotIn("sk-abcdefghijklmnop", redact("token sk-abcdefghijklmnop"))
        self.assertIn("[redacted-env-ref]", redact("${ENV:MY_TOKEN}"))


if __name__ == "__main__":
    unittest.main()
