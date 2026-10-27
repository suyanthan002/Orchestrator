import unittest

from nao.adapters import ADAPTER_CLASSES
from nao.adapters.base import Availability, BaseCliAdapter
from nao.config import load_config
from nao.discovery import Executable
from nao.errors import UnsupportedPermissionError
from nao.models.agent_contract import AgentRequest
from nao.models.enums import Permission, Role
from nao.permissions import analyze_native_flags
from nao.process import ProcessResult, ProcessRunner
from tests.support import dummy_agent_config

ALL_AGENTS = ["cline", "kilo", "goose", "aider", "copilot", "cursor", "antigravity"]


def make_request(prompt="do the thing", permissions=(), workspace="."):
    return AgentRequest(
        job_id="job-1",
        role=Role.IMPLEMENTER,
        agent="x",
        task_id="task-1",
        prompt=prompt,
        workspace=workspace,
        permissions=tuple(permissions),
    )


class AdapterConstructionTests(unittest.TestCase):
    def test_every_agent_has_an_adapter(self):
        self.assertEqual(set(ADAPTER_CLASSES), set(ALL_AGENTS))

    def test_all_adapters_construct_and_declare_name(self):
        config = load_config()
        runner = ProcessRunner()
        for name, cls in ADAPTER_CLASSES.items():
            adapter = cls(config.agent(name), runner)
            self.assertEqual(adapter.name, name)
            self.assertTrue(adapter.capabilities)

    def test_unknown_agent_falls_back_to_generic_adapter(self):
        from nao.registry import AgentRegistry
        from nao.config import NaoConfig

        cfg = NaoConfig(agents={"mystery": dummy_agent_config("mystery")})
        registry = AgentRegistry(cfg, ProcessRunner())
        adapter = registry.adapter("mystery")
        self.assertIsInstance(adapter, BaseCliAdapter)
        self.assertEqual(adapter.name, "mystery")

    def test_unavailable_agent_reports_unavailable(self):
        adapter = BaseCliAdapter(
            dummy_agent_config("ghost"), ProcessRunner()
        )
        self.assertEqual(adapter.name, "ghost")
        response = adapter.execute(make_request())
        self.assertEqual(response.status, "unavailable")
        self.assertFalse(response.succeeded)
        self.assertEqual(response.errors[0]["failure_class"], "unavailable")


class CommandConstructionTests(unittest.TestCase):
    def setUp(self):
        self.runner = ProcessRunner()
        self.exe = Executable(("test-agent",), "unit-test")

    def test_read_only_permissions_use_read_only_args(self):
        adapter = BaseCliAdapter(dummy_agent_config(), self.runner)
        argv = adapter.build_command(
            make_request(permissions=[Permission.READ, Permission.GIT_READ]), self.exe
        )
        self.assertEqual(argv[0], "test-agent")
        self.assertIn("--json", argv)
        self.assertIn("--plan", argv)
        self.assertNotIn("--auto-approve=true", argv)

    def test_write_and_shell_permission_adds_allow_flag(self):
        adapter = BaseCliAdapter(dummy_agent_config(), self.runner)
        argv = adapter.build_command(
            make_request(permissions=[Permission.WRITE, Permission.SHELL]), self.exe
        )
        self.assertIn("--auto-approve=true", argv)
        self.assertNotIn("--plan", argv)

    def test_prompt_is_last_argument(self):
        adapter = BaseCliAdapter(dummy_agent_config(), self.runner)
        req = make_request(prompt="hello")
        argv = adapter.build_command(req, self.exe)
        self.assertIn("hello", argv[-1])
        self.assertIn("=== TASK INSTRUCTION ===", argv[-1])

    def test_copilot_denies_network_when_ungranted(self):
        adapter = ADAPTER_CLASSES["copilot"](load_config().agent("copilot"), self.runner)
        argv = adapter.build_command(make_request(permissions=[Permission.READ]), self.exe)
        self.assertIn("--deny-tool=network", argv)

    def test_copilot_allows_shell_when_granted(self):
        adapter = ADAPTER_CLASSES["copilot"](load_config().agent("copilot"), self.runner)
        argv = adapter.build_command(
            make_request(permissions=[Permission.SHELL]), self.exe
        )
        self.assertIn("--allow-tool=shell", argv)

    def test_aider_uses_message_flag_when_fully_authorized(self):
        # Aider's base_args include `--yes` (a blanket flag), so it is only
        # permitted when the policy authorizes exactly the capability it grants.
        adapter = ADAPTER_CLASSES["aider"](load_config().agent("aider"), self.runner)
        req = make_request(
            prompt="fix it", permissions=[Permission.WRITE, Permission.SHELL]
        )
        argv = adapter.build_command(req, self.exe)
        self.assertEqual(argv[-2], "--message")
        self.assertIn("fix it", argv[-1])
        self.assertIn("=== TASK INSTRUCTION ===", argv[-1])

    def test_cursor_uses_prompt_flag(self):
        adapter = ADAPTER_CLASSES["cursor"](load_config().agent("cursor"), self.runner)
        argv = adapter.build_command(make_request(prompt="go"), self.exe)
        self.assertEqual(argv[-2], "-p")

    def test_antigravity_never_uses_dangerous_flag(self):
        adapter = ADAPTER_CLASSES["antigravity"](
            load_config().agent("antigravity"), self.runner
        )
        argv = adapter.build_command(
            make_request(permissions=[Permission.WRITE, Permission.SHELL]), self.exe
        )
        self.assertNotIn("--dangerously-skip-permissions", argv)


class PermissionFailClosedTests(unittest.TestCase):
    """F-01 regression tests: Fail-closed agent permission translation."""

    def setUp(self):
        self.runner = ProcessRunner()
        self.exe = Executable(("test-agent",), "unit-test")
        self.config = load_config()

    def test_supported_permission_passes(self):
        adapter = ADAPTER_CLASSES["cline"](self.config.agent("cline"), self.runner)
        valid, _ = adapter.validate_permissions((Permission.READ, Permission.SEARCH, Permission.GIT_READ))
        self.assertTrue(valid)

    def test_unsupported_permission_rejected(self):
        adapter = ADAPTER_CLASSES["cline"](self.config.agent("cline"), self.runner)
        # cline does not declare network or delete support
        valid, reason = adapter.validate_permissions((Permission.NETWORK,))
        self.assertFalse(valid)
        self.assertIn("UNSUPPORTED_PERMISSION", reason)

    def test_dangerous_permission_rejected(self):
        for name in ALL_AGENTS:
            adapter = ADAPTER_CLASSES[name](self.config.agent(name), self.runner)
            for dangerous in (Permission.GIT_PUSH, Permission.DEPLOYMENT, Permission.DELETE, Permission.SECRET_ACCESS):
                valid, reason = adapter.validate_permissions((dangerous,))
                self.assertFalse(valid, f"Expected {name} to reject {dangerous}")
                self.assertIn("UNSUPPORTED_PERMISSION", reason)

    def test_attempted_escalation_write_without_shell_rejected_on_blanket_adapter(self):
        # Cline uses blanket --auto-approve=true; granting WRITE alone without SHELL is unsafe
        adapter = ADAPTER_CLASSES["cline"](self.config.agent("cline"), self.runner)
        valid, reason = adapter.validate_permissions((Permission.WRITE,))
        self.assertFalse(valid)
        self.assertIn("blanket auto-approval", reason)

    def test_copilot_allows_granular_write_without_shell(self):
        # Copilot has explicit --allow-tool and --deny-tool controls
        adapter = ADAPTER_CLASSES["copilot"](self.config.agent("copilot"), self.runner)
        valid, reason = adapter.validate_permissions((Permission.WRITE,))
        self.assertTrue(valid, reason)

    def test_missing_native_mapping_rejected(self):
        # If an agent does not have a mapping for a requested mutating permission
        adapter = ADAPTER_CLASSES["cline"](self.config.agent("cline"), self.runner)
        # Attempting unmapped permission
        valid, reason = adapter.validate_permissions((Permission.NETWORK,))
        self.assertFalse(valid)
        self.assertIn("UNSUPPORTED_PERMISSION", reason)

    def test_unsafe_adapter_configuration_cannot_launch(self):
        adapter = ADAPTER_CLASSES["cline"](self.config.agent("cline"), self.runner)
        req = make_request(permissions=[Permission.DEPLOYMENT])
        response = adapter.execute(req)
        self.assertEqual(response.status, "failed")
        self.assertEqual(response.errors[0]["failure_class"], "unsupported_permission")
        self.assertIn("UNSUPPORTED_PERMISSION", response.errors[0]["message"])


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.runner = ProcessRunner()

    def _result(self, stdout, exit_code=0, stderr=""):
        return ProcessResult(
            command=["agent"], exit_code=exit_code, stdout=stdout, stderr=stderr
        )

    def test_ndjson_run_result_is_parsed(self):
        adapter = BaseCliAdapter(dummy_agent_config(), self.runner)
        stdout = (
            '{"type":"system","message":"start"}\n'
            '{"type":"run_result","finishReason":"completed","text":"done",'
            '"usage":{"inputTokens":5}}\n'
        )
        response = adapter.parse(make_request(), self._result(stdout))
        self.assertEqual(response.status, "succeeded")
        self.assertEqual(response.response, "done")
        self.assertEqual(response.usage, {"inputTokens": 5})

    def test_single_bare_json_object_is_parsed(self):
        adapter = BaseCliAdapter(dummy_agent_config(), self.runner)
        stdout = '{"response":"hi","session_id":"abc"}\n'
        response = adapter.parse(make_request(), self._result(stdout))
        self.assertEqual(response.response, "hi")
        self.assertEqual(response.session_id, "abc")

    def test_empty_output_is_a_failure(self):
        adapter = BaseCliAdapter(dummy_agent_config(), self.runner)
        response = adapter.parse(make_request(), self._result(""))
        self.assertEqual(response.status, "failed")
        self.assertEqual(
            response.errors[0]["failure_class"], "malformed_output"
        )

    def test_nonzero_exit_is_classified(self):
        adapter = BaseCliAdapter(dummy_agent_config(), self.runner)
        result = self._result("", exit_code=1, stderr="Error: not authenticated")
        response = adapter.parse(make_request(), result)
        response = adapter._finalize(make_request(), response, result)
        self.assertEqual(response.status, "failed")
        self.assertEqual(response.errors[0]["failure_class"], "authentication")
        self.assertFalse(response.errors[0]["retryable"])

    def test_timeout_is_classified(self):
        adapter = BaseCliAdapter(dummy_agent_config(), self.runner)
        result = self._result("partial", exit_code=None)
        result.timed_out = True
        response = adapter.parse(make_request(), result)
        response = adapter._finalize(make_request(), response, result)
        self.assertEqual(response.status, "timeout")
        self.assertEqual(response.errors[0]["failure_class"], "timeout")

    def test_files_changed_are_collected(self):
        adapter = BaseCliAdapter(dummy_agent_config(), self.runner)
        stdout = (
            '{"type":"tool_call","tool":"write_file","path":"a.py"}\n'
            '{"type":"result","response":"ok","files_changed":["b.py"]}\n'
        )
        response = adapter.parse(make_request(), self._result(stdout))
        self.assertEqual(sorted(response.files_changed), ["a.py", "b.py"])


class PermissionTranslationBoundaryTests(unittest.TestCase):
    """F-01: permission translation must fail closed, base_args included."""

    def setUp(self):
        self.runner = ProcessRunner()
        self.config = load_config()
        self.exe = Executable(("test-agent",), "unit-test")

    def _adapter(self, name):
        return ADAPTER_CLASSES[name](self.config.agent(name), self.runner)

    def test_a_aider_yes_cannot_bypass_validation(self):
        adapter = self._adapter("aider")
        valid, reason = adapter.validate_permissions((Permission.READ,))
        self.assertFalse(valid)
        self.assertIn("--yes", reason)
        self.assertIn("blanket auto-approval", reason)

    def test_aider_yes_permitted_only_with_exact_capability(self):
        adapter = self._adapter("aider")
        valid, reason = adapter.validate_permissions(
            (Permission.WRITE, Permission.SHELL)
        )
        self.assertTrue(valid, reason)

    def test_b_cline_auto_approve_cannot_bypass_validation(self):
        adapter = self._adapter("cline")
        valid, reason = adapter.validate_permissions((Permission.WRITE,))
        self.assertFalse(valid)
        self.assertIn("--auto-approve=true", reason)

    def test_c_write_only_cannot_imply_shell(self):
        adapter = self._adapter("cline")
        self.assertFalse(adapter.validate_permissions((Permission.WRITE,))[0])

    def test_c_shell_only_cannot_imply_write(self):
        # cline's shell mapping is the same blanket flag that grants write.
        adapter = self._adapter("cline")
        valid, reason = adapter.validate_permissions((Permission.SHELL,))
        self.assertFalse(valid)
        self.assertIn("write", reason)

    def test_d_shell_grant_cannot_imply_unrelated_permissions(self):
        # Antigravity's shell mapping uses the flag that grants WRITE, so a
        # shell-only grant over-grants and is rejected.
        adapter = self._adapter("antigravity")
        valid, reason = adapter.validate_permissions((Permission.SHELL,))
        self.assertFalse(valid)
        self.assertIn("write", reason)

    def test_e_blanket_flag_rejected_without_exact_policy(self):
        adapter = self._adapter("cline")
        req = make_request(permissions=[Permission.WRITE])
        with self.assertRaises(UnsupportedPermissionError):
            adapter.build_command(req, self.exe)

    def test_e_blanket_flag_allowed_with_exact_policy(self):
        # cline may use --auto-approve=true only when write+shell are both granted.
        adapter = self._adapter("cline")
        req = make_request(permissions=[Permission.WRITE, Permission.SHELL])
        argv = adapter.build_command(req, self.exe)
        self.assertIn("--auto-approve=true", argv)

    def test_f_every_adapter_command_passes_the_boundary(self):
        for name in ALL_AGENTS:
            adapter = self._adapter(name)
            req = make_request(permissions=[Permission.WRITE])
            valid, _ = adapter.validate_permissions(req.permissions)
            if valid:
                argv = adapter.build_command(req, self.exe)
                # Everything except the executable prefix and the trailing prompt
                # must be free of unauthorized granting flags.
                self.assertIsNone(
                    analyze_native_flags(argv[1:-1], req.permissions), name
                )

    def test_base_args_are_included_in_analysis(self):
        adapter = self._adapter("aider")
        static = adapter._static_native_arguments({Permission.READ})
        self.assertIn("--yes", static)

    def test_legacy_runner_rejects_blanket_flag_without_launching(self):
        from orchestrator.runner import AgentRunner
        from orchestrator.task import AgentTask

        runner = AgentRunner()
        task = AgentTask(
            task_id="legacy-1",
            prompt="inspect",
            working_directory=".",
            timeout_seconds=5,
        )
        result = runner.run(
            "cline", ["cline", "--auto-approve=true", "--json"], task
        )
        self.assertFalse(result.success)
        self.assertIn("UNSUPPORTED_PERMISSION", result.stderr)

    def test_legacy_cline_command_has_no_blanket_flag(self):
        from adapters.cline import CLINE_ARGS

        self.assertNotIn("--auto-approve=true", CLINE_ARGS)


if __name__ == "__main__":
    unittest.main()
