"""
Unit tests for ``lex.audit_logging.utils.config``.

**What this tests (customer-visible behaviour)**

``AuditLoggingConfig`` controls whether audit logging is active.  It
reads the ``INITIAL_DATA_AUDIT_LOGGING`` env var, accepts a broad range
of truthy/falsy strings, and raises on invalid input.

**Why it matters**

If the parser silently swallows an invalid value, audit logging
may be disabled in production without any visible error — leading
to a compliance gap.

**Methodology**

Pure env-var parsing — no DB.

Run::

    python manage.py test lex.tests.test_audit_config
"""

from unittest.mock import patch

from django.test import SimpleTestCase
from lex.audit_logging.utils.config import (
    AuditLoggingConfig,
    get_audit_logging_config,
    reset_audit_logging_config,
    is_audit_logging_enabled,
)


class TestAuditLoggingConfigParsing(SimpleTestCase):
    """Prove ``_parse_audit_logging_enabled`` handles all value forms."""

    def tearDown(self):
        reset_audit_logging_config()

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": "true"})
    def test_true_lowercase(self):
        self.assertTrue(AuditLoggingConfig().audit_logging_enabled)

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": "1"})
    def test_true_one(self):
        self.assertTrue(AuditLoggingConfig().audit_logging_enabled)

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": "yes"})
    def test_true_yes(self):
        self.assertTrue(AuditLoggingConfig().audit_logging_enabled)

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": "on"})
    def test_true_on(self):
        self.assertTrue(AuditLoggingConfig().audit_logging_enabled)

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": "enabled"})
    def test_true_enabled(self):
        self.assertTrue(AuditLoggingConfig().audit_logging_enabled)

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": "false"})
    def test_false_lowercase(self):
        self.assertFalse(AuditLoggingConfig().audit_logging_enabled)

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": "0"})
    def test_false_zero(self):
        self.assertFalse(AuditLoggingConfig().audit_logging_enabled)

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": "no"})
    def test_false_no(self):
        self.assertFalse(AuditLoggingConfig().audit_logging_enabled)

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": "off"})
    def test_false_off(self):
        self.assertFalse(AuditLoggingConfig().audit_logging_enabled)

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": "disabled"})
    def test_false_disabled(self):
        self.assertFalse(AuditLoggingConfig().audit_logging_enabled)

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": "TRUE"})
    def test_case_insensitive(self):
        self.assertTrue(AuditLoggingConfig().audit_logging_enabled)

    @patch.dict("os.environ", {}, clear=False)
    def test_missing_env_defaults_to_true(self):
        import os
        os.environ.pop("INITIAL_DATA_AUDIT_LOGGING", None)
        self.assertTrue(AuditLoggingConfig().audit_logging_enabled)

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": ""})
    def test_empty_string_defaults_to_true(self):
        self.assertTrue(AuditLoggingConfig().audit_logging_enabled)

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": "maybe"})
    def test_invalid_value_raises(self):
        with self.assertRaises(ValueError) as ctx:
            AuditLoggingConfig()
        self.assertIn("maybe", str(ctx.exception))


class TestAuditLoggingConfigSummary(SimpleTestCase):
    """Prove ``get_configuration_summary`` returns structured info."""

    def tearDown(self):
        reset_audit_logging_config()

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": "true"})
    def test_summary_structure(self):
        config = AuditLoggingConfig()
        summary = config.get_configuration_summary()
        self.assertIn("audit_logging_enabled", summary)
        self.assertIn("environment_variables", summary)
        self.assertIn("defaults_used", summary)


class TestGlobalAccessors(SimpleTestCase):
    """Prove singleton and convenience functions work correctly."""

    def tearDown(self):
        reset_audit_logging_config()

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": "true"})
    def test_get_config_returns_same_instance(self):
        a = get_audit_logging_config()
        b = get_audit_logging_config()
        self.assertIs(a, b)

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": "true"})
    def test_reset_clears_singleton(self):
        a = get_audit_logging_config()
        reset_audit_logging_config()
        b = get_audit_logging_config()
        self.assertIsNot(a, b)

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": "true"})
    def test_is_audit_logging_enabled(self):
        self.assertTrue(is_audit_logging_enabled())

    @patch.dict("os.environ", {"INITIAL_DATA_AUDIT_LOGGING": "false"})
    def test_is_audit_logging_disabled(self):
        self.assertFalse(is_audit_logging_enabled())
