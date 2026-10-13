"""Explicit budgets are honored; auto budgets reserve host/container headroom."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from experiments import memory


class MemoryLimitTests(unittest.TestCase):
    @unittest.skipUnless(memory.sys.platform == "linux", "Linux allocation limits")
    def test_reported_budget_honors_inherited_address_space_limit(self):
        import resource

        with patch.object(memory, "available_memory_bytes", return_value=8_000_000_000), \
             patch.object(resource, "getrlimit", return_value=(128_000_000, 256_000_000)):
            self.assertEqual(memory.resolve_memory_limit_mb(512), 128)

    def test_auto_budget_reserves_half_available_memory(self):
        with patch.object(memory, "available_memory_bytes", return_value=2_000_000_000):
            self.assertEqual(memory.resolve_memory_limit_mb(), 1000)
        with patch.object(memory, "available_memory_bytes", return_value=16_000_000_000):
            self.assertEqual(memory.resolve_memory_limit_mb(), 4096)

    def test_explicit_budget_is_not_reduced_by_available_memory(self):
        with patch.object(memory, "available_memory_bytes", return_value=2_000_000_000):
            self.assertEqual(memory.resolve_memory_limit_mb(8000), 8000)
            self.assertEqual(memory.resolve_memory_limit_mb(256), 256)

    def test_invalid_explicit_budgets_are_rejected(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                memory.resolve_memory_limit_mb(value)

    def test_cgroup_ancestor_can_be_tighter_than_leaf_and_host(self):
        contents = {
            "/proc/self/cgroup": "0::/user.slice/session.scope\n",
            "/sys/fs/cgroup/user.slice/session.scope/memory.max": "max",
            "/sys/fs/cgroup/user.slice/memory.max": "2000000000",
            "/sys/fs/cgroup/user.slice/memory.current": "1500000000",
        }

        def read(path):
            if str(path) not in contents:
                raise FileNotFoundError(path)
            return contents[str(path)]

        with patch.object(memory.sys, "platform", "linux"), \
             patch.object(memory.psutil, "virtual_memory", return_value=SimpleNamespace(available=8_000_000_000)), \
             patch.object(Path, "is_dir", return_value=True), \
             patch.object(Path, "read_text", autospec=True, side_effect=read):
            self.assertEqual(memory.available_memory_bytes(), 500_000_000)

    def test_unreadable_cgroup_uses_host_availability(self):
        with patch.object(memory.sys, "platform", "linux"), \
             patch.object(memory.psutil, "virtual_memory", return_value=SimpleNamespace(available=123_000_000)), \
             patch.object(Path, "read_text", side_effect=PermissionError):
            self.assertEqual(memory.available_memory_bytes(), 123_000_000)


if __name__ == "__main__":
    unittest.main()
