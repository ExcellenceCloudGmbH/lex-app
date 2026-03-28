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
            derive_repo_name("/Users/melihsunbul/LUND_IT/lex-app"),
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
