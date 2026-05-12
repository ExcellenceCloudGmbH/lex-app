"""
Sub-cluster 6j — Audit-utils singletons & data-model contracts.

Two small, customer-invisible-but-pipeline-critical files from PR-7's audit
batch that no other test currently pins:

* ``lex/audit_logging/utils/config.py`` — `AuditLoggingConfig` env parser +
  the global-singleton accessor (`get_audit_logging_config` /
  `reset_audit_logging_config` / `is_audit_logging_enabled`).
  These decide whether `InitialDataAuditLogger` runs at all on the
  initial-data upload path. A regression that flipped the default from
  True → False would silently disable every audit row on first deploy.

* ``lex/audit_logging/utils/DataModels.py`` — the dataclasses
  (`ContextInfo`, `CacheCleanupResult`) + custom-exception hierarchy
  (`CalculationLogError` / `CacheOperationError` /
  `ContextResolutionError`) consumed by `ContextResolver`,
  `CacheManager`, and every audit-side log writer. The exception classes
  carry diagnostic fields (`calculation_id`, `cache_key`, `stack_length`)
  that operator dashboards parse — silently dropping one would lose
  triage signal on production failures.

All scenarios are pure Python — no DB, no Keycloak, no Celery, no
fixtures — so the entire batch runs as `SimpleTestCase` in well under
0.01s.

Scenario IDs 6.80 – 6.95 (range deliberately leaves 6.74 – 6.79 free
for any future immutability / retry extension to 6f / 6g).

Run with:
    lex test lex.test_project.tests.audit_logging.test_6j_audit_utils \\
        --verbosity=2 --noinput --keepdb
"""
from __future__ import annotations

import os
from unittest import mock

from django.test import SimpleTestCase

from lex.audit_logging.utils.config import (
    AuditLoggingConfig,
    get_audit_logging_config,
    is_audit_logging_enabled,
    reset_audit_logging_config,
)
from lex.audit_logging.utils.DataModels import (
    CacheCleanupResult,
    CacheOperationError,
    CalculationLogError,
    ContextInfo,
    ContextResolutionError,
)


# ---------------------------------------------------------------------------
# AuditLoggingConfig parser
# ---------------------------------------------------------------------------


class TestCluster06j_ConfigParser(SimpleTestCase):
    """Direct construction of `AuditLoggingConfig` reads
    ``INITIAL_DATA_AUDIT_LOGGING`` and applies the documented value table."""

    def setUp(self):
        # Each test gets a clean env — never leak between scenarios.
        self._env_patch = mock.patch.dict(
            os.environ,
            {k: v for k, v in os.environ.items() if k != "INITIAL_DATA_AUDIT_LOGGING"},
            clear=True,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_6_80_unset_falls_back_to_default_true(self):
        """6.80: env unset → `DEFAULT_AUDIT_LOGGING_ENABLED` (True).

        A regression flipping the default would silently disable audit on
        every fresh deployment that hasn't set the env var.
        """
        cfg = AuditLoggingConfig()
        self.assertTrue(
            cfg.audit_logging_enabled,
            "Default must be True — flipping it disables audit silently on every fresh deploy",
        )
        self.assertIs(
            AuditLoggingConfig.DEFAULT_AUDIT_LOGGING_ENABLED, True,
            "DEFAULT_AUDIT_LOGGING_ENABLED constant must stay True (drift canary)",
        )

    def test_6_81_blank_string_treated_as_unset(self):
        """6.81: empty / whitespace env value → default (not raise)."""
        for value in ("", "   ", "\t\n"):
            with self.subTest(value=repr(value)):
                with mock.patch.dict(os.environ, {"INITIAL_DATA_AUDIT_LOGGING": value}):
                    cfg = AuditLoggingConfig()
                    self.assertTrue(
                        cfg.audit_logging_enabled,
                        f"Blank value {value!r} must be treated as unset, not raise",
                    )

    def test_6_82_true_values_table(self):
        """6.82: every advertised TRUE_VALUE → True (case-insensitive)."""
        for raw in AuditLoggingConfig.TRUE_VALUES:
            for variant in (raw, raw.upper(), raw.title()):
                with self.subTest(value=variant):
                    with mock.patch.dict(os.environ, {"INITIAL_DATA_AUDIT_LOGGING": variant}):
                        self.assertTrue(
                            AuditLoggingConfig().audit_logging_enabled,
                            f"{variant!r} must enable audit (TRUE_VALUES table drift)",
                        )

    def test_6_83_false_values_table(self):
        """6.83: every advertised FALSE_VALUE → False (case-insensitive)."""
        for raw in AuditLoggingConfig.FALSE_VALUES:
            for variant in (raw, raw.upper(), raw.title()):
                with self.subTest(value=variant):
                    with mock.patch.dict(os.environ, {"INITIAL_DATA_AUDIT_LOGGING": variant}):
                        self.assertFalse(
                            AuditLoggingConfig().audit_logging_enabled,
                            f"{variant!r} must disable audit (FALSE_VALUES table drift)",
                        )

    def test_6_84_invalid_value_raises_with_helpful_message(self):
        """6.84: gibberish env value → ValueError naming the env var
        AND listing the accepted values.

        Operators see this in the deployment log when they typo. Silent
        coercion to True/False would hide the typo and lead to surprise
        behaviour on the next deploy.
        """
        with mock.patch.dict(os.environ, {"INITIAL_DATA_AUDIT_LOGGING": "maybe"}):
            with self.assertRaises(ValueError) as ctx:
                AuditLoggingConfig()
        msg = str(ctx.exception)
        self.assertIn("INITIAL_DATA_AUDIT_LOGGING", msg, "Error must name the env var so the operator can find it")
        self.assertIn("maybe", msg, "Error must echo the offending value verbatim")
        # Spot-check that a couple of accepted values appear in the help text.
        self.assertIn("true", msg, "Error must list accepted true values")
        self.assertIn("false", msg, "Error must list accepted false values")

    def test_6_85_constants_pinned(self):
        """6.85: TRUE_VALUES / FALSE_VALUES sets pinned to documented members.

        A drift here (e.g. silently dropping ``yes``) would cause
        environments using the dropped alias to fail-loud at startup
        instead of working as before — surfaces as P1 deploy regression.
        """
        self.assertEqual(
            AuditLoggingConfig.TRUE_VALUES,
            {"true", "1", "yes", "on", "enabled"},
            "TRUE_VALUES drift — operators rely on the documented synonyms",
        )
        self.assertEqual(
            AuditLoggingConfig.FALSE_VALUES,
            {"false", "0", "no", "off", "disabled"},
            "FALSE_VALUES drift — operators rely on the documented synonyms",
        )
        self.assertEqual(
            AuditLoggingConfig.ENV_AUDIT_LOGGING_ENABLED,
            "INITIAL_DATA_AUDIT_LOGGING",
            "Env var rename would silently disable audit on every existing deployment",
        )


class TestCluster06j_ConfigSummary(SimpleTestCase):
    """`get_configuration_summary()` is the diagnostic dump used in `lex`
    CLI debug output and must always include the required keys."""

    def test_6_86_summary_shape(self):
        """6.86: summary always has the 3 documented top-level keys, and
        `defaults_used` correctly reports whether the env var was set."""
        with mock.patch.dict(os.environ, {}, clear=True):
            summary = AuditLoggingConfig().get_configuration_summary()
        self.assertEqual(summary["audit_logging_enabled"], True)
        self.assertEqual(
            summary["environment_variables"]["INITIAL_DATA_AUDIT_LOGGING"], "not set",
        )
        self.assertTrue(
            summary["defaults_used"]["audit_logging_enabled"],
            "When env unset, summary must flag that the default was used",
        )

        with mock.patch.dict(os.environ, {"INITIAL_DATA_AUDIT_LOGGING": "false"}):
            summary = AuditLoggingConfig().get_configuration_summary()
        self.assertEqual(summary["audit_logging_enabled"], False)
        self.assertEqual(
            summary["environment_variables"]["INITIAL_DATA_AUDIT_LOGGING"], "false",
        )
        self.assertFalse(
            summary["defaults_used"]["audit_logging_enabled"],
            "When env set, summary must NOT flag default-used (operator wants to see real source)",
        )


# ---------------------------------------------------------------------------
# Singleton accessor + reset helper
# ---------------------------------------------------------------------------


class TestCluster06j_ConfigSingleton(SimpleTestCase):
    """`get_audit_logging_config()` must memoize once + `reset_*` must
    force a re-read so test suites can swap env between cases."""

    def setUp(self):
        # Always start from a clean singleton — earlier tests may have cached.
        reset_audit_logging_config()
        self.addCleanup(reset_audit_logging_config)

    def test_6_87_singleton_is_memoized(self):
        """6.87: two calls return the *same* object; this is the property
        that prevents us from re-parsing the env on every audit write."""
        with mock.patch.dict(os.environ, {"INITIAL_DATA_AUDIT_LOGGING": "true"}, clear=False):
            first = get_audit_logging_config()
            second = get_audit_logging_config()
        self.assertIs(
            first, second,
            "Singleton must be memoized — repeated env-parse on every audit write would hammer the boot path",
        )

    def test_6_88_reset_forces_fresh_read(self):
        """6.88: after `reset_audit_logging_config`, the next call re-reads env.

        This is the only seam tests have for swapping audit on/off
        mid-run. Silently keeping the old value would make every
        env-driven test flaky based on call order.
        """
        with mock.patch.dict(os.environ, {"INITIAL_DATA_AUDIT_LOGGING": "true"}, clear=False):
            first = get_audit_logging_config()
            self.assertTrue(first.audit_logging_enabled)
        reset_audit_logging_config()
        with mock.patch.dict(os.environ, {"INITIAL_DATA_AUDIT_LOGGING": "false"}, clear=False):
            second = get_audit_logging_config()
        self.assertIsNot(first, second, "Reset must mint a fresh instance")
        self.assertFalse(second.audit_logging_enabled, "Fresh instance must reflect the new env value")

    def test_6_89_is_audit_logging_enabled_passes_through(self):
        """6.89: convenience helper just delegates to the singleton."""
        with mock.patch.dict(os.environ, {"INITIAL_DATA_AUDIT_LOGGING": "off"}, clear=False):
            self.assertFalse(is_audit_logging_enabled())
        reset_audit_logging_config()
        with mock.patch.dict(os.environ, {"INITIAL_DATA_AUDIT_LOGGING": "on"}, clear=False):
            self.assertTrue(is_audit_logging_enabled())

    def test_6_90_create_with_validation_returns_validated_instance(self):
        """6.90: `create_with_validation` is a classmethod that constructs
        AND validates. Today validation is a no-op pass — the test pins
        that contract so a future check that erroneously raises on
        valid configs is caught here.
        """
        with mock.patch.dict(os.environ, {"INITIAL_DATA_AUDIT_LOGGING": "true"}, clear=False):
            cfg = AuditLoggingConfig.create_with_validation()
        self.assertIsInstance(cfg, AuditLoggingConfig)
        self.assertTrue(cfg.audit_logging_enabled)


# ---------------------------------------------------------------------------
# DataModels — dataclasses + exception hierarchy
# ---------------------------------------------------------------------------


class TestCluster06j_DataclassShapes(SimpleTestCase):
    """`ContextInfo` and `CacheCleanupResult` are passed by value
    between `ContextResolver`, `CacheManager`, and the LexLogger
    handlers. Their field set is a contract — callers unpack by name."""

    def test_6_91_context_info_minimal_construction(self):
        """6.91: only `calculation_id` + `audit_log` are required; the
        other 7 fields default to None.

        A regression that promoted any of the optional fields to
        required would crash every audit-log writer at construction
        time, deep inside the create/update path.
        """
        info = ContextInfo(calculation_id="calc-1", audit_log="<sentinel>")
        self.assertEqual(info.calculation_id, "calc-1")
        self.assertEqual(info.audit_log, "<sentinel>")
        for optional_field in (
            "current_model", "parent_model", "current_record",
            "parent_record", "content_type", "parent_content_type", "root_record",
        ):
            with self.subTest(field=optional_field):
                self.assertIsNone(
                    getattr(info, optional_field),
                    f"{optional_field} must default to None — promoting it to required breaks every audit writer",
                )

    def test_6_92_cache_cleanup_result_post_init_normalizes_none(self):
        """6.92: passing `None` for `cleaned_keys` / `errors` → empty list.

        Callers iterate these directly (`for k in result.cleaned_keys`)
        — a `None` slipping through here would `TypeError: NoneType is
        not iterable` deep inside the cache-cleanup hot path.
        """
        result = CacheCleanupResult(success=True, cleaned_keys=None, errors=None)
        self.assertEqual(result.cleaned_keys, [], "None must be normalized to empty list")
        self.assertEqual(result.errors, [], "None must be normalized to empty list")
        # Real lists pass through unchanged.
        keys, errs = ["calc:1"], ["redis timeout"]
        passthrough = CacheCleanupResult(success=False, cleaned_keys=keys, errors=errs)
        self.assertIs(passthrough.cleaned_keys, keys, "Real list must NOT be re-wrapped")
        self.assertIs(passthrough.errors, errs, "Real list must NOT be re-wrapped")


class TestCluster06j_ExceptionHierarchy(SimpleTestCase):
    """Operator dashboards parse the diagnostic fields off these
    exceptions (`calculation_id`, `cache_key`, `stack_length`) — silent
    drops or attribute renames would lose triage signal in production."""

    def test_6_93_base_exception_carries_calculation_id(self):
        """6.93: `CalculationLogError(message, calculation_id=...)` exposes
        both via standard `str()` and via the `calculation_id` attribute.
        """
        exc = CalculationLogError("boom", calculation_id="calc-42")
        self.assertEqual(str(exc), "boom", "Message must round-trip through str()")
        self.assertEqual(exc.calculation_id, "calc-42")
        # calculation_id is optional and defaults to None.
        plain = CalculationLogError("oops")
        self.assertIsNone(plain.calculation_id, "Optional kwarg must default to None")

    def test_6_94_cache_operation_error_carries_cache_key(self):
        """6.94: `CacheOperationError` extends the base with `cache_key`,
        the field operator dashboards group on for "redis hot key" alerts.
        """
        exc = CacheOperationError("redis down", calculation_id="c-1", cache_key="calc:c-1:state")
        self.assertEqual(str(exc), "redis down")
        self.assertEqual(exc.calculation_id, "c-1", "Inherited field must propagate from base __init__")
        self.assertEqual(exc.cache_key, "calc:c-1:state", "cache_key must be exposed for dashboards")
        self.assertIsInstance(exc, CalculationLogError, "Subclass must inherit from base for `except` clauses")

    def test_6_95_context_resolution_error_carries_stack_length(self):
        """6.95: `ContextResolutionError` extends the base with
        `stack_length`. ContextResolver's stack-walk failure path stamps
        the depth at the moment of failure — operators chart this to
        spot recursion regressions.
        """
        exc = ContextResolutionError(
            "no parent context", calculation_id="c-2", stack_length=37,
        )
        self.assertEqual(str(exc), "no parent context")
        self.assertEqual(exc.calculation_id, "c-2")
        self.assertEqual(exc.stack_length, 37, "stack_length must be exposed for recursion-depth charting")
        self.assertIsInstance(exc, CalculationLogError, "Subclass must inherit from base for `except` clauses")
        # stack_length is optional and defaults to None.
        plain = ContextResolutionError("transient")
        self.assertIsNone(plain.stack_length)

