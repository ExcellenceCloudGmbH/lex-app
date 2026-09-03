"""Tests for release_notes.facts — the things an upgrade note must state.

A model cannot infer any of these from commit prose, and getting them wrong is
expensive in both directions. v2.1.3's published note claimed a migration that
had actually shipped in v2.1.1; v2.1.4 shipped a data-repair command whose most
important property is that its --cutoff has no safe default, and no commit
subject says so. So they are computed, not guessed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import facts  # noqa: E402


def _added(paths):
    """Stand in for `git diff --diff-filter=A --name-only`."""
    return lambda a, b: "\n".join(paths)


def test_migrations_are_reported_when_a_release_adds_one():
    got = facts.collect("v1", "v2", added=_added([
        "lex/audit_logging/migrations/0007_calculationlog_heading.py",
    ]))
    assert got["migrations"] == ["0007_calculationlog_heading"]
    assert got["needs_migration"] is True


def test_a_release_with_no_migration_says_so_definitively():
    got = facts.collect("v1", "v2", added=_added(["lex/core/models/LexModel.py"]))
    assert got["migrations"] == []
    assert got["needs_migration"] is False


def test_migration_detection_ignores_the_migrations_package_init():
    got = facts.collect("v1", "v2", added=_added([
        "lex/audit_logging/migrations/__init__.py",
    ]))
    assert got["migrations"] == []


def test_a_new_management_command_is_reported_by_the_name_you_type():
    got = facts.collect("v1", "v2", added=_added([
        "lex/lex_app/management/commands/rebase_incident_datetimes.py",
    ]))
    assert got["commands"] == ["rebase_incident_datetimes"]


def test_a_command_test_file_is_not_a_command():
    got = facts.collect("v1", "v2", added=_added([
        "lex/test_project/tests/init/test_1i_rebase_incident_datetimes.py",
    ]))
    assert got["commands"] == []


def test_new_environment_variables_are_reported():
    diff = (
        '+    TIME_ZONE = os.environ.get("LEX_TIME_ZONE", "Europe/Berlin")\n'
        '+    USE_TZ = True\n'
        '-    USE_TZ = False\n'
    )
    got = facts.collect("v1", "v2", added=_added([]), settings_diff=lambda a, b: diff)
    assert "LEX_TIME_ZONE" in got["env_vars"]


def test_a_removed_environment_variable_is_not_reported_as_new():
    diff = '-    OLD = os.environ.get("LEX_GONE", "x")\n'
    got = facts.collect("v1", "v2", added=_added([]), settings_diff=lambda a, b: diff)
    assert got["env_vars"] == []


def test_facts_render_as_a_prompt_block_only_when_there_is_something_to_say():
    empty = facts.collect("v1", "v2", added=_added([]), settings_diff=lambda a, b: "")
    assert "adds no migration" in facts.render(empty).splitlines()[0]

    rich = facts.collect("v1", "v2", added=_added([
        "lex/audit_logging/migrations/0007_calculationlog_heading.py",
        "lex/lex_app/management/commands/rebase_incident_datetimes.py",
    ]), settings_diff=lambda a, b: '+X = os.environ.get("LEX_TIME_ZONE", "Europe/Berlin")')
    out = facts.render(rich)
    assert "0007_calculationlog_heading" in out
    assert "rebase_incident_datetimes" in out
    assert "LEX_TIME_ZONE" in out


def test_collect_never_raises_when_git_fails():
    def boom(a, b):
        raise RuntimeError("not a git repository")
    got = facts.collect("v1", "v2", added=boom, settings_diff=boom)
    assert got["migrations"] == [] and got["commands"] == [] and got["env_vars"] == []
    # An unknown is reported as unknown, never as a confident "no migration".
    assert got["needs_migration"] is None


@pytest.mark.parametrize("line,expected", [
    ('+TIME_ZONE = os.getenv("LEX_TIME_ZONE", "Europe/Berlin")', "LEX_TIME_ZONE"),
    ('+X = os.environ.get("LEX_A", "1")', "LEX_A"),
    ('+X = os.environ["LEX_B"]', "LEX_B"),
    ("+X = os.getenv('LEX_C')", "LEX_C"),
], ids=["getenv", "environ.get", "environ[]", "single-quotes"])
def test_every_spelling_of_reading_the_environment_is_recognised(line, expected):
    # v2.1.4 used os.getenv and the first pattern only matched os.environ.get,
    # so LEX_TIME_ZONE — the one setting operators outside Berlin must change —
    # went unreported.
    got = facts.collect("v1", "v2", added=lambda a, b: "", settings_diff=lambda a, b: line)
    assert got["env_vars"] == [expected]
