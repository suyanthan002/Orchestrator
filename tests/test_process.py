"""F-13 regression tests: task-scoped cancellation and process-tree cleanup.

Cancelling task A must never touch task B's jobs. Child processes must be
cleaned up (Windows process-tree termination) and timeouts must not leak
running jobs.
"""

import sys
import threading
import time
import unittest

from nao.launch import LaunchAuthority
from nao.process import InternalProcessService, ProcessRunner
from tests.support import temp_dir

# A parent that spawns a grandchild which appends to a heartbeat file forever.
_PARENT_SCRIPT = (
    "import subprocess, sys, time\n"
    "hb = sys.argv[1]\n"
    "child = subprocess.Popen([sys.executable, '-c',\n"
    "    \"import sys, time\\nhb = sys.argv[1]\\nwhile True:\\n"
    "    open(hb, 'a').write('x')\\n    time.sleep(0.1)\\n\", hb])\n"
    "sys.stdout.write(str(child.pid))\n"
    "sys.stdout.flush()\n"
    "time.sleep(30)\n"
)


def _sleeper(seconds):
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def _wait_for(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class TaskScopedCancellationTests(unittest.TestCase):
    def setUp(self):
        authority = LaunchAuthority()
        self.runner = ProcessRunner(authority)
        self.internal = InternalProcessService(authority, self.runner)

    def _start(self, results, job_id, task_id, seconds=5):
        def target():
            results[job_id] = self.internal.run(
                _sleeper(seconds),
                cwd=".",
                timeout=30,
                job_id=job_id,
                task_id=task_id,
                purpose="test",
            )

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        return thread

    def test_cancel_task_only_affects_its_own_jobs(self):
        results = {}
        a = self._start(results, "job-a1", "task-A", seconds=5)
        b = self._start(results, "job-b1", "task-B", seconds=1)
        self.assertTrue(
            _wait_for(
                lambda: {"job-a1", "job-b1"} <= set(self.runner.active_jobs())
            )
        )
        cancelled = self.runner.cancel_task("task-A")
        self.assertEqual(cancelled, ["job-a1"])
        a.join(20)
        b.join(20)
        self.assertTrue(results["job-a1"].cancelled)
        self.assertFalse(results["job-b1"].cancelled)
        self.assertEqual(results["job-b1"].exit_code, 0)

    def test_active_jobs_are_filtered_by_task(self):
        results = {}
        thread = self._start(results, "job-a2", "task-A", seconds=2)
        self.assertTrue(_wait_for(lambda: "job-a2" in self.runner.active_jobs()))
        self.assertIn("job-a2", self.runner.active_jobs("task-A"))
        self.assertNotIn("job-a2", self.runner.active_jobs("task-X"))
        self.runner.cancel_task("task-A")
        thread.join(20)

    def test_cancel_unknown_job_returns_false(self):
        self.assertFalse(self.runner.cancel("does-not-exist"))

    def test_timeout_is_cleaned_up(self):
        result = self.internal.run(
            _sleeper(30),
            cwd=".",
            timeout=0.5,
            job_id="job-t",
            task_id="task-T",
            purpose="test",
        )
        self.assertTrue(result.timed_out)
        self.assertLess(result.duration_seconds, 15)
        self.assertEqual(self.runner.active_jobs(), [])

    @unittest.skipUnless(
        sys.platform == "win32", "process-tree termination is Windows-specific"
    )
    def test_child_process_tree_is_terminated(self):
        root = temp_dir()
        heartbeat = root / "heartbeat.txt"
        heartbeat.write_text("", encoding="utf-8")
        results = {}

        def target():
            results["job-tree"] = self.internal.run(
                [sys.executable, "-c", _PARENT_SCRIPT, str(heartbeat)],
                cwd=".",
                timeout=30,
                job_id="job-tree",
                task_id="task-tree",
                purpose="test",
            )

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        self.assertTrue(
            _wait_for(lambda: "job-tree" in self.runner.active_jobs())
        )
        self.assertTrue(_wait_for(lambda: heartbeat.stat().st_size > 0))
        self.runner.cancel_task("task-tree")
        thread.join(20)

        # Give the tree a moment to die, then confirm the grandchild stopped.
        size_after_cancel = heartbeat.stat().st_size
        time.sleep(2.0)
        self.assertEqual(
            heartbeat.stat().st_size,
            size_after_cancel,
            "grandchild process kept running: process tree was not terminated",
        )


if __name__ == "__main__":
    unittest.main()
