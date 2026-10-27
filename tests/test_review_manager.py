import json
import unittest

from nao.artifacts import ArtifactManager
from nao.audit import AuditLogger
from nao.models.agent_contract import AgentResponse
from nao.models.enums import FindingStatus, Role
from nao.review_manager import ReviewManager
from tests.support import temp_dir


def response_structured(structured):
    return AgentResponse(job_id="job-1", agent="reviewer", structured_output=structured)


def response_text(text):
    return AgentResponse(job_id="job-1", agent="reviewer", response=text)


class ReviewManagerTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir()
        self.artifacts = ArtifactManager(self.root / "runs")
        self.audit = AuditLogger(self.root / "audit")
        self.review = ReviewManager(self.artifacts, self.audit)

    def test_extracts_findings_from_structured_output(self):
        response = response_structured(
            {
                "findings": [
                    {
                        "title": "Missing null check",
                        "severity": "high",
                        "description": "value may be None",
                        "file": "a.py",
                        "line": 12,
                    }
                ]
            }
        )
        findings = self.review.collect_findings(
            response, task_id="task-1", job_id="job-1"
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "high")
        self.assertEqual(findings[0].status, FindingStatus.OPEN)

    def test_extracts_findings_from_json_in_text(self):
        payload = json.dumps(
            {"findings": [{"title": "typo", "severity": "low", "description": "x"}]}
        )
        response = response_text(f"Here is my review:\n{payload}\nDone.")
        findings = self.review.collect_findings(response, task_id="task-2")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].title, "typo")

    def test_invalid_severity_defaults_to_medium(self):
        response = response_structured(
            {"findings": [{"title": "x", "severity": "apocalyptic"}]}
        )
        findings = self.review.collect_findings(response)
        self.assertEqual(findings[0].severity, "medium")

    def test_review_passes_with_no_findings(self):
        outcome = self.review.evaluate([])
        self.assertTrue(outcome.passed)
        self.assertFalse(outcome.blocking)

    def test_medium_and_above_block_completion(self):
        response = response_structured(
            {
                "findings": [
                    {"title": "a", "severity": "high", "description": "x"},
                    {"title": "b", "severity": "low", "description": "y"},
                ]
            }
        )
        findings = self.review.collect_findings(response)
        outcome = self.review.evaluate(findings)
        self.assertFalse(outcome.passed)
        self.assertEqual([f.title for f in outcome.blocking], ["a"])

    def test_resolving_findings_unblocks(self):
        response = response_structured(
            {"findings": [{"title": "a", "severity": "high", "description": "x"}]}
        )
        findings = self.review.collect_findings(response, task_id="task-3")
        self.review.resolve(
            findings,
            [findings[0].finding_id],
            fix_job_id="job-fix",
            task_id="task-3",
        )
        outcome = self.review.evaluate(findings)
        self.assertTrue(outcome.passed)
        self.assertEqual(findings[0].status, FindingStatus.RESOLVED)

    def test_persist_and_reload(self):
        response = response_structured(
            {"findings": [{"title": "a", "severity": "high", "description": "x"}]}
        )
        findings = self.review.collect_findings(response)
        self.review.persist("task-4", findings)
        reloaded = self.artifacts.load_findings("task-4")
        self.assertEqual(len(reloaded), 1)
        self.assertEqual(reloaded[0].title, "a")

    def test_finding_creation_is_audited(self):
        response = response_structured(
            {"findings": [{"title": "a", "severity": "high", "description": "x"}]}
        )
        self.review.collect_findings(response, task_id="task-5", job_id="job-1")
        events = [e["event"] for e in self.audit.events("task-5")]
        self.assertIn("finding_created", events)


class ReviewFailClosedTests(unittest.TestCase):
    """F-03: malformed reviewer output is a failure, never "no findings"."""

    def setUp(self):
        self.root = temp_dir()
        self.review = ReviewManager(
            ArtifactManager(self.root / "runs"), AuditLogger(self.root / "audit")
        )

    def _parse(self, structured=None, text=None, role=Role.REVIEWER):
        response = AgentResponse(
            job_id="j",
            agent="reviewer",
            structured_output=structured,
            response=text,
        )
        return self.review.parse(response, role=role, task_id="t", job_id="j")

    def test_valid_findings_pass(self):
        parsed = self._parse(structured={"findings": [{"title": "x", "severity": "high"}]})
        self.assertTrue(parsed.valid)
        self.assertEqual(len(parsed.findings), 1)

    def test_valid_empty_findings_pass(self):
        parsed = self._parse(structured={"findings": []})
        self.assertTrue(parsed.valid)
        self.assertEqual(parsed.findings, [])

    def test_missing_findings_fails(self):
        parsed = self._parse(structured={})
        self.assertFalse(parsed.valid)

    def test_findings_wrong_type_fails(self):
        parsed = self._parse(structured={"findings": "none found"})
        self.assertFalse(parsed.valid)

    def test_finding_missing_required_field_fails(self):
        parsed = self._parse(structured={"findings": [{"severity": "high"}]})
        self.assertFalse(parsed.valid)

    def test_unknown_severity_fails_schema(self):
        parsed = self._parse(
            structured={"findings": [{"title": "x", "severity": "apocalyptic"}]}
        )
        self.assertFalse(parsed.valid)

    def test_malformed_json_fails(self):
        parsed = self._parse(text="this is not json {broken")
        self.assertFalse(parsed.valid)

    def test_empty_output_fails(self):
        parsed = self._parse(text="")
        self.assertFalse(parsed.valid)

    def test_security_reviewer_schema_is_enforced(self):
        parsed = self._parse(
            structured={"findings": [{"title": "x", "severity": "critical"}]},
            role=Role.SECURITY_REVIEWER,
        )
        self.assertTrue(parsed.valid)


if __name__ == "__main__":
    unittest.main()
