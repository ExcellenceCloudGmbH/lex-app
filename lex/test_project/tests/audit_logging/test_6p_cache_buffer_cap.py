"""CacheManager backfill-buffer bounding — ``store_message`` must cap growth.

Intent: the calculation-log cache is only a *recent-history backfill* buffer —
the full log is persisted in ``CalculationLog`` and new lines stream over the
WebSocket. ``store_message`` previously did an unbounded get+concat+set on every
line, so the cached string grew without limit and the backend OOM-ed when the
log panel opened on a long/heavy calculation (the ``InitCalculationLogs``
endpoint loads this whole blob). The buffer must now stay bounded to the most
recent tail, start on a clean line boundary, and be written with the configured
one-week TTL. A regression here brings back the memory crash.
Cluster 6p — scenarios 6.109–6.113. Type: U.
Covers: lex/audit_logging/utils/CacheManager.py (store_message buffer cap + TTL).
Run: python -m lex pytest lex/test_project/tests/audit_logging/test_6p_cache_buffer_cap.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.core.cache import caches
from django.test import SimpleTestCase
from lex.audit_logging.utils.CacheManager import CacheManager

import pytest

pytestmark = pytest.mark.audit_logging


class TestCluster06p_CacheBufferCap(SimpleTestCase):
    """Cluster 6p: ``store_message`` keeps the cached backfill buffer bounded."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Force the local LocMemCache so the test never needs Redis.
        cls._cache_patch = patch.object(CacheManager, "CALC_CACHE_NAME", "local")
        cls._cache_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._cache_patch.stop()
        super().tearDownClass()

    def setUp(self) -> None:
        caches["local"].clear()

    # -- 6.109 ---------------------------------------------------------
    def test_6_109_buffer_never_exceeds_cap(self) -> None:
        """
        Scenario 6.109: stored buffer stays within the char cap
        Given: a small cap and many appended lines that together far exceed it
        When: store_message is called repeatedly
        Then: the cached value length never exceeds MAX_CACHE_MESSAGE_CHARS
        """
        key = CacheManager.build_cache_key("calc_record", "c-109")
        with patch.object(CacheManager, "MAX_CACHE_MESSAGE_CHARS", 50):
            for i in range(200):
                CacheManager.store_message(key, f"line-{i:04d}")
                stored = CacheManager.get_message(key) or ""
                self.assertLessEqual(
                    len(stored),
                    50,
                    f"buffer grew past the cap at iteration {i}: {len(stored)} chars",
                )

    # -- 6.110 ---------------------------------------------------------
    def test_6_110_trim_starts_on_clean_line_boundary(self) -> None:
        """
        Scenario 6.110: after trimming, no partial leading line survives
        Given: a small cap and enough lines to force several trims
        When: the buffer is read back
        Then: every retained line is a whole 'line-NNNN' token (no fragment head)
        """
        key = CacheManager.build_cache_key("calc_record", "c-110")
        with patch.object(CacheManager, "MAX_CACHE_MESSAGE_CHARS", 40):
            for i in range(100):
                CacheManager.store_message(key, f"line-{i:04d}")
        stored = CacheManager.get_message(key) or ""
        for retained in stored.split("\n"):
            self.assertRegex(
                retained,
                r"^line-\d{4}$",
                f"retained a partial/fragment line: {retained!r}",
            )

    # -- 6.111 ---------------------------------------------------------
    def test_6_111_newest_message_is_retained(self) -> None:
        """
        Scenario 6.111: trimming drops the oldest and keeps the newest
        Given: a small cap and a known final line
        When: the buffer is read back after overflow
        Then: the most recent line is present and the very first line is gone
        """
        key = CacheManager.build_cache_key("calc_record", "c-111")
        with patch.object(CacheManager, "MAX_CACHE_MESSAGE_CHARS", 40):
            for i in range(100):
                CacheManager.store_message(key, f"line-{i:04d}")
        stored = CacheManager.get_message(key) or ""
        self.assertIn("line-0099", stored, "newest line must survive the trim")
        self.assertNotIn("line-0000", stored, "oldest line must be trimmed away")

    # -- 6.112 ---------------------------------------------------------
    def test_6_112_under_cap_is_left_untouched(self) -> None:
        """
        Scenario 6.112: small logs are never trimmed
        Given: a couple of short lines well under the cap
        When: stored and read back
        Then: the full content is preserved verbatim (no behavioural change for
              the common case)
        """
        key = CacheManager.build_cache_key("calc_record", "c-112")
        CacheManager.store_message(key, "alpha")
        CacheManager.store_message(key, "beta")
        self.assertEqual(CacheManager.get_message(key), "alpha\nbeta")

    # -- 6.113 ---------------------------------------------------------
    def test_6_113_set_uses_configured_ttl(self) -> None:
        """
        Scenario 6.113: the one-week TTL is actually applied on write
        Given: a mocked cache backend
        When: store_message writes a line
        Then: cache.set is called with timeout=CACHE_TIMEOUT (previously omitted,
              so entries used the backend default instead of the documented week)
        """
        fake_cache = MagicMock()
        fake_cache.get.return_value = ""
        with patch(
            "lex.audit_logging.utils.CacheManager.caches",
            {"local": fake_cache},
        ):
            CacheManager.store_message("k", "hello")
        _, kwargs = fake_cache.set.call_args
        self.assertEqual(
            kwargs.get("timeout"),
            CacheManager.CACHE_TIMEOUT,
            "store_message must pass the configured one-week timeout to cache.set",
        )
