"""
Cluster 9c: ``CacheManager`` cleanup discipline.

Intent (from docs/features/calculations/ + cache-manager docs):

    ``CacheManager.cleanup_calculation`` must run exactly once per
    root calculation — in the root process. Child processes spawned
    by the framework must NOT re-run the cleanup (would wipe the
    parent's cache prematurely).

Scenario numbering matches
docs/test-plan/test-clusters.md#9-signals--websocket.
"""

from __future__ import annotations

import unittest


class TestCluster09c_CacheCleanup(unittest.TestCase):
    """Cache cleanup runs only in the root process."""

    @unittest.skip(
        "Scenario 9.4: root-process cache cleanup. Requires observing "
        "CacheManager.cleanup_calculation from outside the default "
        "store_message/build_cache_key patches E2ETestCase installs. "
        "Re-add with the cluster-9 signals fixture."
    )
    def test_9_4_root_process_cleans_up_cache(self) -> None:
        """Scenario 9.4: Root process → cleanup_calculation called once."""

    @unittest.skip("Scenario 9.5: child process skip (see 9.4)")
    def test_9_5_child_process_skips_cache_cleanup(self) -> None:
        """Scenario 9.5: Child process → cleanup_calculation NOT called."""


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

