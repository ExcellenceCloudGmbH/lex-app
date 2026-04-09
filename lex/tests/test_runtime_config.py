"""
Unit tests for ``lex.runtime_config`` helper functions.

**What this tests (customer-visible behaviour)**

``derive_repo_name`` determines the project name shown in the admin UI
and used as the database name prefix.
``format_db_connection_unicode_error`` builds the diagnostic message
displayed when a PostgreSQL connection fails due to non-UTF-8 credentials.

**Why it matters**

If ``derive_repo_name`` returns an empty string, the SQLite database
file gets a bare ``.sqlite3`` extension, Django silently uses an
in-memory database, and all data vanishes on restart.
If the error formatter omits key details, developers waste hours
debugging credential issues.

**Methodology**

Pure functions — no DB, no filesystem writes.

Run::

    python manage.py test lex.tests.test_runtime_config
"""

from pathlib import Path

from django.test import SimpleTestCase

from lex.runtime_config import derive_repo_name, format_db_connection_unicode_error


class TestDeriveRepoName(SimpleTestCase):
    """Prove ``derive_repo_name`` extracts a meaningful directory name."""

    def test_simple_path(self):
        self.assertEqual(derive_repo_name("/home/user/my-project"), "my-project")

    def test_trailing_slash_stripped(self):
        self.assertEqual(derive_repo_name("/home/user/my-project/"), "my-project")

    def test_path_object(self):
        self.assertEqual(derive_repo_name(Path("/opt/app")), "app")

    def test_none_uses_cwd_fallback(self):
        """When project_root is None, cwd is used."""
        result = derive_repo_name(None, cwd="/fallback/dir")
        self.assertEqual(result, "dir")

    def test_windows_style_path(self):
        result = derive_repo_name("C:\\Users\\dev\\project")
        self.assertEqual(result, "project")

    def test_nested_path(self):
        result = derive_repo_name("/a/b/c/d/repo-name")
        self.assertEqual(result, "repo-name")


class TestFormatDbConnectionUnicodeError(SimpleTestCase):
    """Prove ``format_db_connection_unicode_error`` builds a useful message."""

    def _make_exc(self):
        try:
            b"\xff".decode("utf-8")
        except UnicodeDecodeError as e:
            return e

    def test_contains_engine(self):
        exc = self._make_exc()
        settings = {"ENGINE": "django.db.backends.postgresql"}
        msg = format_db_connection_unicode_error(exc, settings)
        self.assertIn("postgresql", msg)

    def test_contains_db_name(self):
        exc = self._make_exc()
        settings = {"NAME": "my_db"}
        msg = format_db_connection_unicode_error(exc, settings)
        self.assertIn("my_db", msg)

    def test_contains_host_and_port(self):
        exc = self._make_exc()
        settings = {"HOST": "db.example.com", "PORT": "5432"}
        msg = format_db_connection_unicode_error(exc, settings)
        self.assertIn("db.example.com", msg)
        self.assertIn("5432", msg)

    def test_contains_deployment_target(self):
        exc = self._make_exc()
        msg = format_db_connection_unicode_error(
            exc, {}, environ={"DATABASE_DEPLOYMENT_TARGET": "staging"}
        )
        self.assertIn("staging", msg)

    def test_default_deployment_target(self):
        exc = self._make_exc()
        msg = format_db_connection_unicode_error(exc, {}, environ={})
        self.assertIn("default", msg)

    def test_windows_advice(self):
        exc = self._make_exc()
        msg = format_db_connection_unicode_error(
            exc, {}, environ={}, os_name="nt"
        )
        self.assertIn("pgpass.conf", msg)

    def test_no_windows_advice_on_posix(self):
        exc = self._make_exc()
        msg = format_db_connection_unicode_error(
            exc, {}, environ={}, os_name="posix"
        )
        self.assertNotIn("pgpass.conf", msg)

    def test_missing_settings_show_unknown(self):
        exc = self._make_exc()
        msg = format_db_connection_unicode_error(exc, {})
        self.assertIn("<unknown>", msg)
