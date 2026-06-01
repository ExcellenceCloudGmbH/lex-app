"""Tests for copilot_coverage_detect — source-file scope + match rule.

The coverage gate originally only watched ``lex/lex_app/**``, so a change
to any other framework app (``lex/core/``, ``lex/api/``,
``lex/audit_logging/``, ``lex/process_admin/``, …) shipped with NO test
enforcement. These tests pin the widened scope: every package under
``lex/`` is source EXCEPT the test trees, docs, and other non-source
packages, and loose top-level ``lex/*.py`` scripts are out of scope.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make .github/scripts importable when pytest runs from repo root.
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import copilot_coverage_detect as ccd  # noqa: E402


# ── _is_source_file: which changed files demand a test ──────────────

@pytest.mark.parametrize(
    "path",
    [
        "lex/lex_app/views.py",
        "lex/core/models.py",
        "lex/api/serializers/foo.py",
        "lex/audit_logging/mixins.py",
        "lex/process_admin/utils/model_structure.py",
        "lex/authentication/backends.py",
    ],
)
def test_app_package_files_are_source(path: str) -> None:
    """A .py file inside ANY framework app package is source — not just
    lex_app. This is the whole point of the widened scope."""
    assert ccd._is_source_file(path) is True


@pytest.mark.parametrize(
    "path",
    [
        # Loose top-level scripts directly under lex/ are out of scope.
        "lex/runtime_config.py",
        "lex/backfill_history_sql.py",
        "lex/proxy.py",
        # Non-source packages under lex/.
        "lex/test_project/tests/calculations/test_7a_foo.py",
        "lex/tests/unit/api/test_views.py",
        "lex/docs/index.py",
        "lex/react/app.py",
        "lex/assets/gen.py",
        "lex/legacy_data/loader.py",
        # Test files committed inside an app tree are not source.
        "lex/core/tests/test_models.py",
        # Non-python files.
        "lex/core/templates/foo.html",
        # Outside lex/ entirely.
        ".github/scripts/copilot_coverage_detect.py",
        "docs/index.md",
    ],
)
def test_non_source_paths_are_excluded(path: str) -> None:
    assert ccd._is_source_file(path) is False


# ── _is_allowlisted: meaningless-coverage files, across all apps ────

@pytest.mark.parametrize(
    "path",
    [
        "lex/lex_app/__init__.py",
        "lex/core/__init__.py",
        "lex/api/apps.py",
        "lex/lex_app/settings.py",
        "lex/lex_app/settings_dev.py",
        "lex/core/migrations/0001_initial.py",
        "lex/audit_logging/migrations/0007_x.py",
    ],
)
def test_allowlist_applies_across_apps(path: str) -> None:
    assert ccd._is_allowlisted(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "lex/core/models.py",
        "lex/api/views.py",
        # A loose top-level __init__ / file is not in a package → not allowlisted.
        "lex/__init__.py",
    ],
)
def test_allowlist_does_not_swallow_real_source(path: str) -> None:
    assert ccd._is_allowlisted(path) is False


# ── _package_of: the loose-file boundary ────────────────────────────

def test_package_of_returns_none_for_loose_top_level_file() -> None:
    assert ccd._package_of("lex/runtime_config.py") is None


def test_package_of_returns_none_outside_lex() -> None:
    assert ccd._package_of("docs/index.md") is None


def test_package_of_returns_first_segment() -> None:
    assert ccd._package_of("lex/core/foo/bar.py") == "core"


# ── detect(): end-to-end miss / pass over a mixed diff ──────────────

def test_detect_flags_untested_source_in_a_non_lex_app_package(tmp_path: Path) -> None:
    """A change under lex/core/ with no matching test is a MISS — the
    exact case the old single-prefix heuristic let through."""
    result = ccd.detect(["lex/core/widget.py"], repo_root=tmp_path)
    assert result.status == "miss"
    assert "lex/core/widget.py" in result.untested


def test_detect_passes_when_test_references_source_stem(tmp_path: Path) -> None:
    test_path = tmp_path / "lex" / "test_project" / "tests" / "core" / "test_9a_widget.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text("def test_widget_behaviour():\n    widget()\n")
    changed = [
        "lex/core/widget.py",
        "lex/test_project/tests/core/test_9a_widget.py",
    ]
    result = ccd.detect(changed, repo_root=tmp_path)
    assert result.status == "pass"
    assert result.matched["lex/core/widget.py"].endswith("test_9a_widget.py")


def test_detect_ignores_loose_top_level_scripts(tmp_path: Path) -> None:
    """Loose lex/*.py scripts never demand a test, so a diff containing
    only them passes."""
    result = ccd.detect(["lex/sanitize_v2_migrations.py"], repo_root=tmp_path)
    assert result.status == "pass"
    assert result.untested == []
