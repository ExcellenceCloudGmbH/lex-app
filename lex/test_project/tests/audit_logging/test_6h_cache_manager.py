"""
Cluster 6h: ``CacheManager`` — calculation-log cache primitives.

Targets ``lex/audit_logging/utils/CacheManager.py`` (baseline 19.35%).
The CacheManager owns the read/write/cleanup surface for live
calculation-log streaming — every ``LexLogger`` call inside a calc
ends up here, and the on-completion cleanup lives here too.

Customer-visible promises (from the module docstring + ``logger.info``
strings inside the module):

* ``store_message`` — appends with a separator, returns False (not
  raises) when the cache backend is gone.
* ``build_cache_key`` — deterministic ``{record}_{calc_id}`` shape;
  blank inputs raise ``ValueError`` (not produce a degenerate key).
* ``get_message`` / ``cleanup_specific_key`` — round-trip and removal.
* ``is_cache_available`` — used by callers to decide whether to even
  try; must return ``False`` (not raise) on outage.
* ``cleanup_calculation`` — pattern-based bulk cleanup; gracefully
  degrades to empty result when the backend can't enumerate keys.

Tests use Django's ``LocMemCache`` (already wired as the ``local``
alias the framework picks up by default), so no Redis or external
boundary is touched.

Scenario numbering matches the Coverage Roadmap **Tier 3.1** in
``docs/test-plan/test-clusters.md``.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from django.core.cache import caches
from django.test import SimpleTestCase

from lex.audit_logging.utils.CacheManager import CacheManager


class TestCluster06h_CacheManagerKeyBuilder(SimpleTestCase):
    """``build_cache_key`` shape contract."""

    # -- 6.80 ----------------------------------------------------------
    def test_6_80_key_format_is_record_underscore_calc_id(self) -> None:
        """The key shape is the contract every other helper depends on
        (``cleanup_calculation`` matches against ``*_<calc_id>``)."""
        key = CacheManager.build_cache_key("invoice_42", "calc-001")
        self.assertEqual(key, "invoice_42_calc-001")

    # -- 6.81 ----------------------------------------------------------
    def test_6_81_blank_record_raises(self) -> None:
        with self.assertRaises(ValueError):
            CacheManager.build_cache_key("", "calc-001")

    # -- 6.82 ----------------------------------------------------------
    def test_6_82_blank_calc_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            CacheManager.build_cache_key("invoice_42", "")


class TestCluster06h_CacheManagerStoreAndGet(SimpleTestCase):
    """``store_message`` / ``get_message`` / ``cleanup_specific_key``
    round-trip on the live cache backend."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Force the local LocMemCache so the test does not require Redis.
        cls._cache_patch = patch.object(
            CacheManager, "CALC_CACHE_NAME", "local"
        )
        cls._cache_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._cache_patch.stop()
        super().tearDownClass()

    def setUp(self) -> None:
        # Each test gets a clean cache so they don't bleed into each other.
        caches["local"].clear()

    # -- 6.83 ----------------------------------------------------------
    def test_6_83_store_then_get_round_trips(self) -> None:
        key = CacheManager.build_cache_key("calc_record", "c-001")

        ok = CacheManager.store_message(key, "first line")
        self.assertTrue(ok, "store_message must return True on success")

        out = CacheManager.get_message(key)
        self.assertEqual(out, "first line")

    # -- 6.84 ----------------------------------------------------------
    def test_6_84_store_appends_with_newline_separator(self) -> None:
        """Appended messages must be separated by a newline so the
        UI can render them as separate log lines."""
        key = CacheManager.build_cache_key("calc_record", "c-002")

        CacheManager.store_message(key, "line 1")
        CacheManager.store_message(key, "line 2")

        self.assertEqual(CacheManager.get_message(key), "line 1\nline 2")

    # -- 6.85 ----------------------------------------------------------
    def test_6_85_get_missing_key_returns_none(self) -> None:
        self.assertIsNone(CacheManager.get_message("never_set"))

    # -- 6.86 ----------------------------------------------------------
    def test_6_86_cleanup_specific_key_removes_entry(self) -> None:
        key = CacheManager.build_cache_key("calc_record", "c-003")
        CacheManager.store_message(key, "to be removed")

        ok = CacheManager.cleanup_specific_key(key)
        self.assertTrue(ok)
        self.assertIsNone(
            CacheManager.get_message(key),
            "After cleanup_specific_key, get_message must return None",
        )

    # -- 6.87 ----------------------------------------------------------
    def test_6_87_cleanup_specific_key_idempotent(self) -> None:
        """Removing an already-removed key must not raise — callers in
        the calc-finalize path call this opportunistically."""
        ok = CacheManager.cleanup_specific_key("never_set")
        self.assertTrue(
            ok,
            "Deleting a missing key must not be a failure — the cache "
            "backend's delete() is a no-op for missing keys",
        )

    # -- 6.88 ----------------------------------------------------------
    def test_6_88_is_cache_available_true_with_local_cache(self) -> None:
        self.assertTrue(CacheManager.is_cache_available())


class TestCluster06h_CacheManagerCleanupCalculation(SimpleTestCase):
    """``cleanup_calculation`` — bulk removal contract."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._cache_patch = patch.object(
            CacheManager, "CALC_CACHE_NAME", "local"
        )
        cls._cache_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._cache_patch.stop()
        super().tearDownClass()

    def setUp(self) -> None:
        caches["local"].clear()

    # -- 6.90 ----------------------------------------------------------
    def test_6_90_cleanup_with_specific_keys_removes_all_named(self) -> None:
        """When the caller supplies ``specific_keys`` (the reliable
        path), every named key is deleted and the result reports the
        count."""
        keys = [
            CacheManager.build_cache_key(f"rec-{i}", "c-090")
            for i in range(3)
        ]
        for k in keys:
            CacheManager.store_message(k, "x")

        result = CacheManager.cleanup_calculation(specific_keys=keys)

        self.assertTrue(result.success)
        self.assertEqual(
            sorted(result.cleaned_keys), sorted(keys),
            "cleanup_calculation must report every supplied key as cleaned",
        )
        self.assertEqual(result.errors, [])
        for k in keys:
            self.assertIsNone(
                CacheManager.get_message(k),
                f"Key {k!r} must be removed by cleanup_calculation",
            )

    # -- 6.91 ----------------------------------------------------------
    def test_6_91_cleanup_pattern_path_degrades_gracefully(self) -> None:
        """``LocMemCache`` does not expose ``iter_keys`` / ``keys``
        pattern matching, so the pattern path falls through to the
        documented graceful-degradation branch and reports zero keys
        removed without raising. Customers running on LocMem (e.g.
        local dev) must not see crashes from cache cleanup."""
        # Seed the cache so we know the call would *want* to remove
        # something if pattern matching worked.
        seeded_key = CacheManager.build_cache_key("rec-091", "c-091")
        CacheManager.store_message(seeded_key, "x")

        result = CacheManager.cleanup_calculation(calculation_id="c-091")

        self.assertEqual(
            result.cleaned_keys, [],
            "Without pattern matching, cleanup_calculation must report "
            "an empty cleaned_keys list (graceful degradation, not crash). "
            f"Got {result.cleaned_keys!r}.",
        )

    # -- 6.92 ----------------------------------------------------------
    def test_6_92_cleanup_with_neither_arg_returns_zero(self) -> None:
        """Defensive contract: no calc_id, no specific_keys → no-op
        result, never raises."""
        result = CacheManager.cleanup_calculation()
        self.assertEqual(result.cleaned_keys, [])
        self.assertEqual(result.errors, [])


class TestCluster06h_CacheManagerDegradation(SimpleTestCase):
    """Backend-outage and generic-error paths.

    Customers running without Redis (``InvalidCacheBackendError``) or
    with a transient backend hiccup must see ``False`` / ``None`` /
    ``CacheCleanupResult(success=False, ...)`` rather than exceptions
    bubbling into the calc pipeline. These tests pin those promises.
    """

    # -- 6.93 ----------------------------------------------------------
    def test_6_93_store_message_returns_false_when_backend_invalid(self) -> None:
        from django.core.cache.backends.base import InvalidCacheBackendError

        with patch(
            "lex.audit_logging.utils.CacheManager.caches",
            new={},
        ):
            # dict KeyError is *not* InvalidCacheBackendError — patch a
            # mapping whose __getitem__ raises the right type.
            class _Boom:
                def __getitem__(self, _key):
                    raise InvalidCacheBackendError("no redis here")

            with patch(
                "lex.audit_logging.utils.CacheManager.caches", new=_Boom()
            ):
                self.assertFalse(CacheManager.store_message("k", "m"))

    # -- 6.94 ----------------------------------------------------------
    def test_6_94_store_message_returns_false_on_generic_exception(self) -> None:
        class _Boom:
            def __getitem__(self, _key):
                raise RuntimeError("transient backend error")

        with patch(
            "lex.audit_logging.utils.CacheManager.caches", new=_Boom()
        ):
            self.assertFalse(CacheManager.store_message("k", "m"))

    # -- 6.95 ----------------------------------------------------------
    def test_6_95_get_message_returns_none_when_backend_invalid(self) -> None:
        from django.core.cache.backends.base import InvalidCacheBackendError

        class _Boom:
            def __getitem__(self, _key):
                raise InvalidCacheBackendError("no redis here")

        with patch(
            "lex.audit_logging.utils.CacheManager.caches", new=_Boom()
        ):
            self.assertIsNone(CacheManager.get_message("k"))

    # -- 6.96 ----------------------------------------------------------
    def test_6_96_get_message_returns_none_on_generic_exception(self) -> None:
        class _Boom:
            def __getitem__(self, _key):
                raise RuntimeError("backend wobble")

        with patch(
            "lex.audit_logging.utils.CacheManager.caches", new=_Boom()
        ):
            self.assertIsNone(CacheManager.get_message("k"))

    # -- 6.97 ----------------------------------------------------------
    def test_6_97_is_cache_available_false_on_exception(self) -> None:
        class _Boom:
            def __getitem__(self, _key):
                raise RuntimeError("down")

        with patch(
            "lex.audit_logging.utils.CacheManager.caches", new=_Boom()
        ):
            self.assertFalse(CacheManager.is_cache_available())

    # -- 6.98 ----------------------------------------------------------
    def test_6_98_cleanup_specific_key_invalid_backend_returns_false(self) -> None:
        from django.core.cache.backends.base import InvalidCacheBackendError

        class _Boom:
            def __getitem__(self, _key):
                raise InvalidCacheBackendError("no redis here")

        with patch(
            "lex.audit_logging.utils.CacheManager.caches", new=_Boom()
        ):
            self.assertFalse(CacheManager.cleanup_specific_key("k"))

    # -- 6.99 ----------------------------------------------------------
    def test_6_99_cleanup_specific_key_returns_false_on_generic_exception(self) -> None:
        class _BoomCache:
            def delete(self, _key):
                raise RuntimeError("delete failed")

        class _Boom:
            def __getitem__(self, _key):
                return _BoomCache()

        with patch(
            "lex.audit_logging.utils.CacheManager.caches", new=_Boom()
        ):
            self.assertFalse(CacheManager.cleanup_specific_key("k"))

    # -- 6.100 ---------------------------------------------------------
    def test_6_100_cleanup_calculation_invalid_backend_returns_failure(self) -> None:
        from django.core.cache.backends.base import InvalidCacheBackendError

        class _Boom:
            def __getitem__(self, _key):
                raise InvalidCacheBackendError("no redis here")

        with patch(
            "lex.audit_logging.utils.CacheManager.caches", new=_Boom()
        ):
            result = CacheManager.cleanup_calculation(specific_keys=["k"])
        self.assertFalse(result.success)
        self.assertEqual(result.cleaned_keys, [])
        self.assertTrue(any("not available" in e for e in result.errors))

    # -- 6.101 ---------------------------------------------------------
    def test_6_101_cleanup_calculation_per_key_delete_error_recorded(self) -> None:
        """If individual key deletes raise, the error is recorded but
        the loop keeps going and the result reports partial success."""
        class _PartialCache:
            def __init__(self):
                self.deleted = []

            def delete(self, k):
                if k == "bad":
                    raise RuntimeError("specific key error")
                self.deleted.append(k)

        class _Wrap:
            def __init__(self):
                self.c = _PartialCache()

            def __getitem__(self, _key):
                return self.c

        wrap = _Wrap()
        with patch(
            "lex.audit_logging.utils.CacheManager.caches", new=wrap
        ):
            result = CacheManager.cleanup_calculation(
                specific_keys=["good1", "bad", "good2"]
            )
        self.assertFalse(result.success)
        self.assertEqual(sorted(result.cleaned_keys), ["good1", "good2"])
        self.assertEqual(len(result.errors), 1)
        self.assertIn("bad", result.errors[0])

    # -- 6.102 ---------------------------------------------------------
    def test_6_102_cleanup_calculation_outer_exception_returns_failure(self) -> None:
        """Raises *after* entering the try block (e.g. iter_keys path
        explodes mid-flight) → outer ``except Exception`` returns a
        failure result without raising."""
        class _ExplodingCache:
            # No iter_keys or keys; but accessing the cache attribute
            # itself raises — simulates a backend wobble after dispatch
            def delete(self, *_a, **_kw):
                raise RuntimeError("late wobble")

        class _Wrap:
            def __getitem__(self, _key):
                # Raise something that's *not* InvalidCacheBackendError
                # *after* the function has chosen the calculation path.
                raise RuntimeError("late wobble")

        with patch(
            "lex.audit_logging.utils.CacheManager.caches", new=_Wrap()
        ):
            result = CacheManager.cleanup_calculation(
                calculation_id="c-102"
            )
        self.assertFalse(result.success)
        self.assertTrue(any("Unexpected error" in e for e in result.errors))


class TestCluster06h_CacheManagerPatternFinders(SimpleTestCase):
    """``_find_calculation_keys`` — the iter_keys / keys / fallback
    branches. Real Redis exposes ``iter_keys``; LocMem doesn't. We
    fake both shapes so we don't need a Redis at test time."""

    # -- 6.103 ---------------------------------------------------------
    def test_6_103_iter_keys_branch_returns_matches(self) -> None:
        class _IterCache:
            def iter_keys(self, pattern):
                assert pattern == "*_c-103"
                return iter(["a_c-103", "b_c-103"])

        keys = CacheManager._find_calculation_keys(_IterCache(), "c-103")
        self.assertEqual(sorted(keys), ["a_c-103", "b_c-103"])

    # -- 6.104 ---------------------------------------------------------
    def test_6_104_keys_fallback_branch_returns_matches(self) -> None:
        class _KeysCache:
            def keys(self, pattern):
                assert pattern == "*_c-104"
                return ["a_c-104"]

        keys = CacheManager._find_calculation_keys(_KeysCache(), "c-104")
        self.assertEqual(keys, ["a_c-104"])

    # -- 6.105 ---------------------------------------------------------
    def test_6_105_iter_keys_exception_returns_empty(self) -> None:
        class _BadIter:
            def iter_keys(self, _pattern):
                raise RuntimeError("connection reset")

        keys = CacheManager._find_calculation_keys(_BadIter(), "c-105")
        self.assertEqual(keys, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

