from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from lex.runtime_config import (
    derive_repo_name,
    format_db_connection_unicode_error,
    resolve_local_sqlite_path,
    resolve_project_root,
)


class RuntimeConfigTests(TestCase):
    def test_derive_repo_name_from_windows_path(self):
        self.assertEqual(
            derive_repo_name(r"C:\Users\MikaBauerfeind\PythonProjectsV2\ACP_IPT_DI"),
            "ACP_IPT_DI",
        )

    def test_derive_repo_name_from_posix_path(self):
        self.assertEqual(
            derive_repo_name("/Users/dev/projects/lex-app"),
            "lex-app",
        )

    def test_resolve_project_root_matches_setup_marker_resolution(self):
        with TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir) / "sample-project"
            nested_dir = project_root / "src" / "nested"
            nested_dir.mkdir(parents=True)
            (project_root / "pyproject.toml").write_text("[project]\nname='sample-project'\n", encoding="utf-8")

            resolved = resolve_project_root(nested_dir)

            self.assertEqual(resolved, project_root.resolve())

    def test_resolve_local_sqlite_path_uses_setup_project_root(self):
        with TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir) / "sample-project"
            nested_dir = project_root / "venv" / "lib" / "python3.12" / "site-packages" / "lex_app"
            nested_dir.mkdir(parents=True)
            (project_root / "pyproject.toml").write_text("[project]\nname='sample-project'\n", encoding="utf-8")

            sqlite_path = resolve_local_sqlite_path(nested_dir)

            self.assertEqual(sqlite_path, project_root.resolve() / "sample-project.sqlite3")

    def test_format_unicode_error_includes_windows_hints(self):
        error = UnicodeDecodeError("utf-8", b"abc\xbb", 3, 4, "invalid start byte")
        message = format_db_connection_unicode_error(
            error,
            {
                "ENGINE": "django.db.backends.postgresql_psycopg2",
                "NAME": "db_acp_ipt_di",
                "HOST": "localhost",
                "PORT": "5432",
                "USER": "django",
            },
            environ={"APPDATA": r"C:\Users\Mika\AppData\Roaming"},
            os_name="nt",
        )

        self.assertIn("pgpass.conf", message)
        self.assertIn("DATABASE_DEPLOYMENT_TARGET=default", message)


# ── Extended tests merged from lex/tests/test_runtime_config.py ───────


class TestDeriveRepoName(TestCase):
    """Additional ``derive_repo_name`` edge cases."""

    def test_trailing_slash_stripped(self):
        self.assertEqual(derive_repo_name("/home/user/my-project/"), "my-project")

    def test_path_object(self):
        self.assertEqual(derive_repo_name(Path("/opt/app")), "app")

    def test_none_uses_cwd_fallback(self):
        """When project_root is None, cwd is used."""
        result = derive_repo_name(None, cwd="/fallback/dir")
        self.assertEqual(result, "dir")

    def test_nested_path(self):
        result = derive_repo_name("/a/b/c/d/repo-name")
        self.assertEqual(result, "repo-name")


class TestFormatDbConnectionUnicodeError(TestCase):
    """Detailed ``format_db_connection_unicode_error`` tests."""

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
