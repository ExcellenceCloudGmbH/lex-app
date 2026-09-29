"""Tests for the pre-publish guard that a wheel carries its runtime files.

The core migration 0001 executes a SQL file that sits next to it. From 2.2.2
to 2.3.1 the wheel shipped the migration without that file: an editable install
reads the source tree, so the whole suite passed, while every installed release
aborted `lex init` on its first start. This guard reads the built zip, which is
the only place that gap can be seen before PyPI.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import check_wheel_runtime_files as guard  # noqa: E402

SQL = "lex/core/sql/bitemporal_activation.sql"


def _wheel(dist: Path, names: list[str], wheel: str = "lex_app-9.9.9-py3-none-any.whl") -> Path:
    dist.mkdir(parents=True, exist_ok=True)
    path = dist / wheel
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("lex_app-9.9.9.dist-info/METADATA", "Name: lex-app\nVersion: 9.9.9")
        for name in names:
            archive.writestr(name, "x")
    return path


def _tree(root: Path, files: list[str]) -> Path:
    for rel in files:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
    return root


# ── The real repository ───────────────────────────────────────────────

def test_the_migrations_sql_file_is_among_the_guarded_files():
    # If this fails, the guard no longer covers the file the core migration
    # executes -- the exact file whose absence took instances down.
    expected, empty = guard.expected_files(guard.REPO_ROOT)
    assert SQL in expected
    assert empty == []


def test_the_migration_still_reads_the_guarded_path():
    # Moving the SQL without moving RUNTIME_DIRS would make the guard pass on
    # a wheel that cannot run the migration.
    migration = guard.REPO_ROOT / "lex/core/migrations/0001_bitemporal_activation.py"
    source = migration.read_text(encoding="utf-8")
    assert '"sql" / "bitemporal_activation.sql"' in source


# ── The rule ──────────────────────────────────────────────────────────

def test_a_wheel_carrying_every_runtime_file_passes():
    assert guard.missing([SQL, "lex/core/migrations/0001_bitemporal_activation.py"], [SQL]) == []


FRONTENDS = ["lex/lex_app/streamlit/_lex_view_component/frontend/index.html",
             "lex/lex_app/streamlit/_widget_host_component/frontend/index.html"]


def test_a_wheel_without_the_sql_file_is_refused(tmp_path: Path, capsys):
    # The 2.2.2-2.3.1 wheel exactly: the migration ships, its SQL does not.
    root = _tree(tmp_path / "src", [SQL, *FRONTENDS])
    _wheel(tmp_path / "dist", ["lex/core/migrations/0001_bitemporal_activation.py", *FRONTENDS])
    code = guard.main(["--dist", str(tmp_path / "dist"), "--root", str(root)])
    out = capsys.readouterr().out
    assert code == 1
    assert SQL in out
    assert "package-data" in out and "MANIFEST.in" in out


def test_a_wheel_with_the_sql_file_is_accepted(tmp_path: Path, capsys):
    files = [SQL, *FRONTENDS]
    root = _tree(tmp_path / "src", files)
    _wheel(tmp_path / "dist", files)
    assert guard.main(["--dist", str(tmp_path / "dist"), "--root", str(root)]) == 0
    assert "carries all 3 runtime files" in capsys.readouterr().out


def test_a_runtime_dir_with_no_files_fails_as_stale(tmp_path: Path, capsys):
    # Renaming a directory must break the guard, not quietly empty its list.
    root = _tree(tmp_path / "src", FRONTENDS)
    _wheel(tmp_path / "dist", [])
    assert guard.main(["--dist", str(tmp_path / "dist"), "--root", str(root)]) == 1
    assert "lex/core/sql" in capsys.readouterr().out


def test_bytecode_and_finder_litter_are_not_expected(tmp_path: Path):
    root = _tree(tmp_path, [SQL, "lex/core/sql/__pycache__/x.cpython-312.pyc", "lex/core/sql/.DS_Store"])
    expected, _ = guard.expected_files(root, ("lex/core/sql",))
    assert expected == [SQL]


@pytest.mark.parametrize("wheels", [0, 2])
def test_exactly_one_wheel_is_required(tmp_path: Path, wheels: int):
    root = _tree(tmp_path / "src", [SQL, *FRONTENDS])
    dist = tmp_path / "dist"
    dist.mkdir()
    for i in range(wheels):
        _wheel(dist, [SQL], wheel=f"lex_app-9.9.{i}-py3-none-any.whl")
    assert guard.main(["--dist", str(dist), "--root", str(root)]) == 1
