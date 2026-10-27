import json
import unittest
from pathlib import Path

from nao.artifacts import ArtifactManager
from nao.audit import AuditLogger, NullAuditLogger
from nao.models.evidence import TestEvidence
from nao.models.job import AgentJob
from nao.models.review import Finding
from nao.models.task import Task
from nao.models.enums import Role
from tests.support import temp_dir


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir()
        self.audit = AuditLogger(self.root / "audit")

    def test_events_are_ndjson(self):
        self.audit.record("task_created", task_id="task-1", message="hello")
        path = self.root / "audit" / "task-1" / "events.ndjson"
        self.assertTrue(path.exists())
        records = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(records[0]["event"], "task_created")

    def test_events_are_readable_back(self):
        self.audit.record("task_created", task_id="task-1")
        self.audit.record("job_started", task_id="task-1", job_id="job-1")
        events = self.audit.events("task-1")
        self.assertEqual([e["event"] for e in events], ["task_created", "job_started"])

    def test_secrets_are_redacted_in_events(self):
        self.audit.record(
            "job_started",
            task_id="task-2",
            message="api_key=supersecretvalue",
            data={"token": "bearer abc"},
        )
        raw = (self.root / "audit" / "task-2" / "events.ndjson").read_text()
        self.assertNotIn("supersecretvalue", raw)
        self.assertIn("[redacted", raw)

    def test_commands_are_redacted(self):
        self.audit.record(
            "job_started",
            task_id="task-3",
            data={"command": ["agent", "${ENV:OPENAI_API_KEY}"]},
        )
        raw = (self.root / "audit" / "task-3" / "events.ndjson").read_text()
        self.assertIn("[redacted-env-ref]", raw)

    def test_null_logger_keeps_memory_only(self):
        audit = NullAuditLogger()
        audit.record("task_created", task_id="task-x")
        self.assertEqual(len(audit.memory_events()), 1)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir()
        self.artifacts = ArtifactManager(self.root / "runs")

    def test_task_roundtrip(self):
        task = Task(task_id="task-a", title="t", description="d")
        self.artifacts.save_task(task)
        loaded = self.artifacts.load_task("task-a")
        self.assertEqual(loaded.title, "t")

    def test_job_and_response_persistence(self):
        task = Task(task_id="task-b")
        self.artifacts.save_task(task)
        job = AgentJob(
            job_id="job-b",
            task_id="task-b",
            agent="cline",
            role=Role.IMPLEMENTER,
        )
        self.artifacts.save_job("task-b", job, {"status": "succeeded"})
        jobs = self.artifacts.load_jobs("task-b")
        self.assertEqual(jobs[0]["job"]["job_id"], "job-b")
        self.assertEqual(jobs[0]["response"]["status"], "succeeded")

    def test_test_evidence_roundtrip(self):
        evidence = TestEvidence(
            command=["pytest"], working_directory=".", status="passed", passed=3
        )
        self.artifacts.save_test("task-c", evidence)
        tests = self.artifacts.load_tests("task-c")
        self.assertEqual(tests[0]["passed"], 3)

    def test_findings_roundtrip(self):
        finding = Finding.create(title="x", severity="high")
        self.artifacts.save_findings("task-d", [finding])
        loaded = self.artifacts.load_findings("task-d")
        self.assertEqual(loaded[0].title, "x")

    def test_diff_roundtrip(self):
        self.artifacts.save_diff("task-e", "job-1", "diff --git a b")
        self.assertEqual(
            self.artifacts.load_diff("task-e", "job-1"), "diff --git a b"
        )

    def test_approvals_roundtrip(self):
        self.artifacts.save_approvals("task-f", [{"approval_id": "x"}])
        self.assertEqual(self.artifacts.load_approvals("task-f")[0]["approval_id"], "x")

    def test_list_tasks_and_run_status(self):
        self.artifacts.save_task(Task(task_id="task-g", title="g"))
        self.assertIn("task-g", self.artifacts.list_tasks())
        status = self.artifacts.run_status("task-g")
        self.assertTrue(status["exists"])
        self.assertEqual(status["state"], "received")

    def test_missing_task_returns_none(self):
        self.assertIsNone(self.artifacts.load_task("nope"))


class SecretRedactionTests(unittest.TestCase):
    """F-05 regression tests: Complete Artifact Secret Redaction."""

    def setUp(self):
        self.root = temp_dir()
        self.artifacts = ArtifactManager(self.root / "runs")
        self.audit = AuditLogger(self.root / "audit")

    def test_nested_dict_and_list_redaction(self):
        task = Task(
            task_id="task-redact-1",
            title="Secure Task",
            metadata={
                "api_key": "sk-1234567890abcdef12345",
                "nested": {
                    "token": "ghp_123456789012345678901234",
                    "list": ["clean", "password=supersecretpass", "sk-abcdef1234567890"],
                },
            },
        )
        self.artifacts.save_task(task)
        raw_text = (self.root / "runs" / "task-redact-1" / "task.json").read_text(encoding="utf-8")
        self.assertNotIn("sk-1234567890abcdef12345", raw_text)
        self.assertNotIn("ghp_123456789012345678901234", raw_text)
        self.assertNotIn("supersecretpass", raw_text)
        # Verify JSON integrity
        loaded = json.loads(raw_text)
        self.assertEqual(loaded["title"], "Secure Task")
        self.assertEqual(loaded["metadata"]["nested"]["list"][0], "clean")

    def test_job_stdout_stderr_diff_redaction(self):
        job = AgentJob(job_id="job-sec", task_id="task-sec", agent="cline", role=Role.IMPLEMENTER)
        response_payload = {
            "stdout": "Running with key sk-openai1234567890...\nDone.",
            "stderr": "Error: authorization: Bearer secret-token-value-123",
            "environment": {"MY_KEY": "${ENV:SECRET_VAR}", "PLAIN": "hello"},
        }
        self.artifacts.save_job("task-sec", job, response_payload)
        self.artifacts.save_diff("task-sec", "job-sec", "+ api_key=topsecretdiff\n+ line2")
        
        job_raw = (self.root / "runs" / "task-sec" / "jobs" / "job-sec.json").read_text(encoding="utf-8")
        diff_raw = (self.root / "runs" / "task-sec" / "diffs" / "job-sec.diff").read_text(encoding="utf-8")
        
        self.assertNotIn("sk-openai1234567890", job_raw)
        self.assertNotIn("secret-token-value-123", job_raw)
        self.assertNotIn("topsecretdiff", diff_raw)
        self.assertIn("+ line2", diff_raw)

    def test_multiline_private_key_redaction(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...\n-----END RSA PRIVATE KEY-----"
        self.audit.record("key_event", task_id="task-pem", message=f"loaded key: {pem}")
        audit_raw = (self.root / "audit" / "task-pem" / "events.ndjson").read_text(encoding="utf-8")
        self.assertNotIn("MIIEowIBAAKCAQEA0", audit_raw)
        self.assertIn("[redacted-private-key]", audit_raw)


class AuditDegradationTests(unittest.TestCase):
    """F-15 regression tests: audit durability failures are surfaced, not silent."""

    def setUp(self):
        self.root = temp_dir()

    def _degraded_audit(self) -> AuditLogger:
        # Point the audit base directory at a regular file so persistence fails.
        blocked = self.root / "not-a-directory"
        blocked.write_text("x", encoding="utf-8")
        audit = AuditLogger(blocked)
        audit.record("task_created", task_id="probe")
        return audit

    def test_persistence_failure_marks_audit_degraded(self):
        audit = self._degraded_audit()
        self.assertTrue(audit.is_degraded)
        self.assertTrue(audit.degraded_errors)
        # Events are retained in memory rather than silently dropped.
        self.assertTrue(audit.events("probe"))

    def test_healthy_logger_is_not_degraded(self):
        audit = AuditLogger(self.root / "audit")
        audit.record("task_created", task_id="task-ok")
        self.assertFalse(audit.is_degraded)

    def test_degraded_state_reaches_workflow_result(self):
        from nao.config import load_config
        from nao.services import build_services
        from nao.task_manager import TaskManager
        from tests.support import make_adapter_class

        audit = self._degraded_audit()
        self.assertTrue(audit.is_degraded)
        config = load_config()
        adapter_class = make_adapter_class({})
        services = build_services(
            config=config,
            runs_dir=self.root / "runs",
            audit_dir=self.root / "audit",
            audit=audit,
            adapter_classes={name: adapter_class for name in config.agents},
        )
        manager = TaskManager(services)
        task = manager.create_task(
            "Review the code", recipe="code_review", repo_path=str(self.root)
        )
        result = manager.run(task)
        # A run whose audit storage failed must never claim a clean audit.
        self.assertTrue(result.audit_degraded)
        self.assertTrue(result.to_dict()["audit_degraded"])


class RedactionConsistencyTests(unittest.TestCase):
    """F-05: one redaction boundary across artifacts, keys and values."""

    def test_common_secret_key_names_are_redacted(self):
        from nao.config import redact_data

        keys = [
            "SECRET", "TOKEN", "API_KEY", "APIKEY", "PASSWORD", "PASSWD",
            "CLIENT_SECRET", "PRIVATE_KEY", "AUTH_TOKEN", "ACCESS_TOKEN",
            "GITHUB_TOKEN",
        ]
        payload = {key: f"value-{index}" for index, key in enumerate(keys)}
        out = redact_data(payload)
        for key in keys:
            self.assertEqual(out[key], "[redacted]", key)

    def test_casing_and_prefix_variations_are_redacted(self):
        from nao.config import redact_data

        out = redact_data(
            {
                "ApiKey": "x",
                "my_token": "y",
                "DB_PASSWORD": "z",
                "AwsSecretAccessKey": "s",
                "AccessToken": "t",
            }
        )
        self.assertTrue(all(value == "[redacted]" for value in out.values()))

    def test_token_shaped_values_are_redacted_in_text(self):
        from nao.config import redact_text

        self.assertIn("[redacted-key]", redact_text("OPENAI sk-abcdefghijklmnopqrstuv"))
        self.assertIn(
            "[redacted-github-token]",
            redact_text("token ghp_abcdefghijklmnopqrstuvwxyz"),
        )

    def test_key_value_pairs_with_prefixed_names_are_redacted(self):
        from nao.config import redact_text

        redacted = redact_text("GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz")
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz", redacted)
        self.assertIn("[redacted]", redacted)

    def test_sessions_persistence_is_redacted(self):
        from nao.sessions import SessionManager

        root = temp_dir()
        sessions = SessionManager(root / "sessions.json")
        sessions.set(
            "cursor",
            "ghp_abcdefghijklmnopqrstuvwxyz",
            task_id="task-sec",
        )
        raw = (root / "sessions.json").read_text(encoding="utf-8")
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz", raw)

    def test_legacy_result_persistence_is_redacted(self):
        import orchestrator.main as legacy
        from orchestrator.agent import AgentResult

        original = legacy.RUNS_DIR
        legacy.RUNS_DIR = temp_dir() / "runs"
        try:
            result = AgentResult(
                task_id="legacy-task",
                agent="cline",
                success=True,
                exit_code=0,
                stdout="export GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz",
                stderr="api_key=supersecretvalue",
                command=["cline", "--json"],
            )
            raw = legacy.save_result(result).read_text(encoding="utf-8")
            self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz", raw)
            self.assertNotIn("supersecretvalue", raw)
        finally:
            legacy.RUNS_DIR = original


if __name__ == "__main__":
    unittest.main()
