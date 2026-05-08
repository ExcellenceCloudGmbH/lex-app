"""
Cluster 1n: ``lex Init`` — pure helper functions.

Targets module-level helpers in
``lex/lex_app/management/commands/init.py`` that the existing 1b suite
mocks past. Each helper is a customer-visible contract:

* ``_format_keycloak_import_error_details`` — the error string the
  operator sees in the log when Keycloak's authz import fails. If the
  formatting drifts, the on-call playbook stops working.
* ``_is_non_fatal_keycloak_import_timeout`` — the predicate ``lex Init``
  uses to decide whether a Keycloak hiccup should fail the whole
  pipeline or be retried. A regression here either swallows real
  outages (silent half-init) or breaks recoverable runs.
* ``Command._parse_extra_args`` — the parser behind
  ``--makemigrations-args`` and ``--migrate-args``. A drift drops the
  caller's flags silently.
* ``Command._database_alias_from_migrate_args`` — picks which DB
  alias the migration runs against. A bad parse migrates the wrong
  database.

All scenarios are pure-Python ``SimpleTestCase`` — no DB, no
boundaries.

Scenario numbering matches the Coverage Roadmap entry **Tier 1.1**
in ``docs/test-plan/test-clusters.md``.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from django.test import SimpleTestCase

from lex.lex_app.management.commands.init import (
    Command,
    _format_keycloak_import_error_details,
    _is_non_fatal_keycloak_import_timeout,
)


class TestCluster01n_KeycloakErrorFormatter(SimpleTestCase):
    """``_format_keycloak_import_error_details`` shapes operator logs."""

    # -- 1.110 ---------------------------------------------------------
    def test_1_110_none_returns_none(self) -> None:
        """No error info → no log line. The caller relies on this to
        decide whether to print anything at all."""
        self.assertIsNone(_format_keycloak_import_error_details(None))
        self.assertIsNone(_format_keycloak_import_error_details({}))

    # -- 1.111 ---------------------------------------------------------
    def test_1_111_timeout_kind_with_value_names_the_timeout(self) -> None:
        """Operator must see how long we waited before giving up."""
        out = _format_keycloak_import_error_details(
            {"kind": "timeout", "timeout": 30}
        )
        self.assertIn("30", out)
        self.assertIn("timed out", out)

    # -- 1.112 ---------------------------------------------------------
    def test_1_112_timeout_kind_without_value_falls_back(self) -> None:
        """Missing ``timeout`` key must not blow up the formatter."""
        out = _format_keycloak_import_error_details({"kind": "timeout"})
        self.assertEqual(out, "request timed out")

    # -- 1.113 ---------------------------------------------------------
    def test_1_113_gateway_timeout_status_and_body(self) -> None:
        """504 + body → both visible in the log line."""
        out = _format_keycloak_import_error_details({
            "kind": "gateway_timeout",
            "status_code": 504,
            "response_text": "upstream down",
        })
        self.assertIn("504", out)
        self.assertIn("upstream down", out)

    # -- 1.114 ---------------------------------------------------------
    def test_1_114_gateway_timeout_default_status(self) -> None:
        """No status code → defaults to 504 (the kind's documented code)."""
        out = _format_keycloak_import_error_details(
            {"kind": "gateway_timeout"}
        )
        self.assertIn("504", out)

    # -- 1.115 ---------------------------------------------------------
    def test_1_115_http_error_body_only_when_no_status(self) -> None:
        """``http_error`` with body but no status returns the body."""
        out = _format_keycloak_import_error_details({
            "kind": "http_error",
            "response_text": "bad request payload",
        })
        self.assertEqual(out, "bad request payload")

    # -- 1.116 ---------------------------------------------------------
    def test_1_116_http_error_status_with_body(self) -> None:
        """``http_error`` with both → ``HTTP <code>: <body>``."""
        out = _format_keycloak_import_error_details({
            "kind": "http_error",
            "status_code": 401,
            "response_text": "unauthorized",
        })
        self.assertIn("401", out)
        self.assertIn("unauthorized", out)

    # -- 1.117 ---------------------------------------------------------
    def test_1_117_unknown_kind_returns_none(self) -> None:
        """Unrecognised ``kind`` → ``None``. Caller must not log a
        useless half-formatted line."""
        out = _format_keycloak_import_error_details(
            {"kind": "supernova", "response_text": "x"}
        )
        self.assertIsNone(out)


class TestCluster01n_NonFatalTimeoutPredicate(SimpleTestCase):
    """``_is_non_fatal_keycloak_import_timeout`` gates retry vs abort."""

    @staticmethod
    def _mgr_with_error(err):
        return SimpleNamespace(kc_manager=SimpleNamespace(
            last_authz_import_error=err
        ))

    # -- 1.120 ---------------------------------------------------------
    def test_1_120_no_error_is_not_a_timeout(self) -> None:
        self.assertFalse(_is_non_fatal_keycloak_import_timeout(
            self._mgr_with_error(None)
        ))

    # -- 1.121 ---------------------------------------------------------
    def test_1_121_timeout_kind_is_non_fatal(self) -> None:
        """Plain ``timeout`` → retryable (the operator can re-run)."""
        self.assertTrue(_is_non_fatal_keycloak_import_timeout(
            self._mgr_with_error({"kind": "timeout"})
        ))

    # -- 1.122 ---------------------------------------------------------
    def test_1_122_gateway_timeout_kind_is_non_fatal(self) -> None:
        """``gateway_timeout`` → retryable (transient upstream issue)."""
        self.assertTrue(_is_non_fatal_keycloak_import_timeout(
            self._mgr_with_error({"kind": "gateway_timeout", "status_code": 504})
        ))

    # -- 1.123 ---------------------------------------------------------
    def test_1_123_http_error_is_fatal(self) -> None:
        """A 401/403 from Keycloak is a real misconfiguration — must
        not be silently retried into a half-init."""
        self.assertFalse(_is_non_fatal_keycloak_import_timeout(
            self._mgr_with_error({"kind": "http_error", "status_code": 401})
        ))

    # -- 1.124 ---------------------------------------------------------
    def test_1_124_missing_kc_manager_is_safe(self) -> None:
        """A sync manager without ``kc_manager`` (test fixture, partial
        init) must not crash the predicate."""
        bare = SimpleNamespace()
        self.assertFalse(_is_non_fatal_keycloak_import_timeout(bare))


class TestCluster01n_ParseExtraArgs(SimpleTestCase):
    """``Command._parse_extra_args`` parses ``--makemigrations-args``
    / ``--migrate-args`` strings.

    A drift here silently drops the operator's ``--database`` /
    ``--no-input`` flags and the migrations run with defaults — wrong
    DB, interactive prompt, hung CI.
    """

    # -- 1.130 ---------------------------------------------------------
    def test_1_130_empty_string_is_empty_pair(self) -> None:
        self.assertEqual(Command._parse_extra_args(""), ([], {}))
        self.assertEqual(Command._parse_extra_args("   "), ([], {}))
        self.assertEqual(Command._parse_extra_args(None), ([], {}))

    # -- 1.131 ---------------------------------------------------------
    def test_1_131_long_option_with_equals(self) -> None:
        """``--database=secondary`` → ``{"database": "secondary"}``.
        Hyphens normalise to underscores so the kwargs feed Django."""
        positional, options = Command._parse_extra_args(
            "--database=secondary --no-input"
        )
        self.assertEqual(positional, [])
        self.assertEqual(options.get("database"), "secondary")
        self.assertTrue(options.get("no_input"))

    # -- 1.132 ---------------------------------------------------------
    def test_1_132_long_option_with_value_no_equals(self) -> None:
        """``--database secondary`` (space-separated) parses identically
        to the equals form."""
        _, options = Command._parse_extra_args("--database secondary")
        self.assertEqual(options.get("database"), "secondary")

    # -- 1.133 ---------------------------------------------------------
    def test_1_133_positional_token_preserved(self) -> None:
        """``my_app --database=other`` → app label kept positional."""
        positional, options = Command._parse_extra_args(
            "my_app --database=other"
        )
        self.assertEqual(positional, ["my_app"])
        self.assertEqual(options.get("database"), "other")

    # -- 1.134 ---------------------------------------------------------
    def test_1_134_quoted_value_with_spaces(self) -> None:
        """``shlex`` round-trip: quoted values survive verbatim."""
        _, options = Command._parse_extra_args(
            '--message="hello world"'
        )
        self.assertEqual(options.get("message"), "hello world")


class TestCluster01n_DatabaseAliasFromMigrateArgs(SimpleTestCase):
    """``Command._database_alias_from_migrate_args`` picks which DB
    alias the migration runs against."""

    # -- 1.140 ---------------------------------------------------------
    def test_1_140_no_args_returns_default(self) -> None:
        from django.db import DEFAULT_DB_ALIAS
        self.assertEqual(
            Command._database_alias_from_migrate_args(""), DEFAULT_DB_ALIAS,
        )
        self.assertEqual(
            Command._database_alias_from_migrate_args(None), DEFAULT_DB_ALIAS,
        )

    # -- 1.141 ---------------------------------------------------------
    def test_1_141_explicit_database_flag_picked_up(self) -> None:
        """``--database=secondary`` routes the migrate call to the
        secondary alias. A bug here migrates the wrong DB."""
        self.assertEqual(
            Command._database_alias_from_migrate_args("--database=secondary"),
            "secondary",
        )

    # -- 1.142 ---------------------------------------------------------
    def test_1_142_blank_database_flag_ignored(self) -> None:
        """``--database=`` (empty value) must fall back to default,
        not migrate against an empty alias."""
        from django.db import DEFAULT_DB_ALIAS
        self.assertEqual(
            Command._database_alias_from_migrate_args("--database= "),
            DEFAULT_DB_ALIAS,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
