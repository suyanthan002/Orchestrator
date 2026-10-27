import threading
import time
import unittest

from nao.config import ResourceConfig
from nao.resource import ResourceManager, ResourceUnavailable


class ResourceManagerTests(unittest.TestCase):
    def test_single_slot_blocks_second_acquire(self):
        manager = ResourceManager(
            ResourceConfig(max_concurrent_agents=1, poll_interval_seconds=0.01)
        )
        holder = manager.acquire(timeout=1)
        holder.__enter__()
        try:
            self.assertEqual(manager.active_jobs, 1)
            with self.assertRaises(ResourceUnavailable):
                with manager.acquire(timeout=0.2):
                    pass
        finally:
            holder.__exit__(None, None, None)
        self.assertEqual(manager.active_jobs, 0)

    def test_slot_is_released_after_use(self):
        manager = ResourceManager(
            ResourceConfig(max_concurrent_agents=1, poll_interval_seconds=0.01)
        )
        with manager.acquire():
            pass
        with manager.acquire(timeout=1):
            pass  # must not raise

    def test_two_slots_allow_concurrency(self):
        manager = ResourceManager(
            ResourceConfig(max_concurrent_agents=2, poll_interval_seconds=0.01)
        )
        a = manager.acquire(timeout=1)
        b = manager.acquire(timeout=1)
        a.__enter__()
        try:
            b.__enter__()
            try:
                self.assertEqual(manager.active_jobs, 2)
            finally:
                b.__exit__(None, None, None)
        finally:
            a.__exit__(None, None, None)

    def test_stats_shape(self):
        manager = ResourceManager(ResourceConfig(max_concurrent_agents=1))
        stats = manager.stats().to_dict()
        self.assertEqual(stats["max_concurrent_agents"], 1)
        self.assertEqual(stats["active_jobs"], 0)

    def test_memory_gate_reports_ok_when_unknown(self):
        manager = ResourceManager(ResourceConfig())
        ok, reason = manager.memory_ok()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(reason, str)


if __name__ == "__main__":
    unittest.main()
