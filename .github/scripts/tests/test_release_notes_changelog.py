"""Tests for release_notes.changelog — deterministic Keep a Changelog output."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import changelog  # noqa: E402

REPO = "ExcellenceCloudGmbH/lex-app"


def _change(**kw):
    base = {
        "sha": "abc1234", "component": "backend", "type": "fix",
        "scope": None, "breaking": False, "subject": "a thing", "pr_number": None,
    }
    base.update(kw)
    return base


def test_renders_the_version_heading_with_the_date():
    out = changelog.render({"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": []},
                           date="2026-08-05", repo=REPO)
    assert out.startswith("## [2.1.7] - 2026-08-05")


def test_groups_feat_under_added_and_fix_under_fixed():
    d = {"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": [
        _change(type="feat", subject="add the widget", sha="1111111"),
        _change(type="fix", subject="stop the crash", sha="2222222"),
    ]}
    out = changelog.render(d, date="2026-08-05", repo=REPO)
    assert "### Added" in out
    assert "### Fixed" in out
    assert out.index("### Added") < out.index("### Fixed")


def test_marks_the_component_and_links_the_commit():
    d = {"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": [
        _change(component="frontend", type="fix", subject="send the viewer timezone", sha="a3f91c2"),
    ]}
    out = changelog.render(d, date="2026-08-05", repo=REPO)
    assert "**frontend** send the viewer timezone" in out
    assert f"https://github.com/{REPO}/commit/a3f91c2" in out


def test_includes_the_pr_number_when_present():
    d = {"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": [
        _change(pr_number=675, subject="stop stamping edited_at"),
    ]}
    assert "(#675)" in changelog.render(d, date="2026-08-05", repo=REPO)


def test_breaking_changes_come_first_and_appear_once():
    d = {"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": [
        _change(type="feat", subject="normal feature", sha="1111111"),
        _change(type="feat", breaking=True, subject="drop the v1 endpoint", sha="2222222"),
    ]}
    out = changelog.render(d, date="2026-08-05", repo=REPO)
    assert out.index("### Breaking") < out.index("### Added")
    assert out.count("drop the v1 endpoint") == 1


def test_housekeeping_types_are_excluded():
    d = {"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": [
        _change(type="docs", subject="update the README"),
        _change(type="ci", subject="bump the runner"),
        _change(type="chore", subject="tidy up"),
        _change(type="test", subject="add a case"),
        _change(type="build", subject="pin a dep"),
    ]}
    out = changelog.render(d, date="2026-08-05", repo=REPO)
    assert "update the README" not in out
    assert "bump the runner" not in out
    assert "### " not in out          # no section headings at all


def test_non_conforming_commits_land_under_changed():
    d = {"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": [
        _change(type="other", subject="Mode change is a beauty"),
    ]}
    out = changelog.render(d, date="2026-08-05", repo=REPO)
    assert "### Changed" in out
    assert "Mode change is a beauty" in out


def test_an_empty_section_emits_no_heading():
    d = {"tag": "v2.1.7", "previous_tag": "v2.1.6", "changes": [_change(type="feat", subject="only a feature")]}
    out = changelog.render(d, date="2026-08-05", repo=REPO)
    assert "### Added" in out
    assert "### Fixed" not in out


def test_prepend_creates_the_file_with_a_preamble():
    out = changelog.prepend(None, "## [2.1.7] - 2026-08-05\n")
    assert out.startswith("# Changelog")
    assert "## [2.1.7] - 2026-08-05" in out


def test_prepend_puts_the_newest_release_directly_below_the_preamble():
    existing = changelog.prepend(None, "## [2.1.6] - 2026-07-23\n")
    out = changelog.prepend(existing, "## [2.1.7] - 2026-08-05\n")
    assert out.count("# Changelog") == 1
    assert out.index("## [2.1.7]") < out.index("## [2.1.6]")


def test_prepend_tolerates_a_file_without_the_expected_preamble():
    out = changelog.prepend("## [2.1.6] - 2026-07-23\n", "## [2.1.7] - 2026-08-05\n")
    assert out.index("## [2.1.7]") < out.index("## [2.1.6]")


def test_prepend_replaces_an_existing_section_for_the_same_version():
    # publish_release_notes.yml advertises a "(re)generate" tag input, and
    # re-running a failed job is routine, so the same version can be rendered
    # twice. It must replace, not duplicate.
    first = changelog.prepend(None, "## [2.1.7] - 2026-08-05\n\n### Added\n- one\n")
    again = changelog.prepend(first, "## [2.1.7] - 2026-08-05\n\n### Added\n- one\n")
    assert again.count("## [2.1.7]") == 1
    assert again.count("- one") == 1


def test_prepend_replaces_even_when_the_date_differs():
    first = changelog.prepend(None, "## [2.1.7] - 2026-08-05\n\n### Added\n- one\n")
    again = changelog.prepend(first, "## [2.1.7] - 2026-08-09\n\n### Added\n- two\n")
    assert again.count("## [2.1.7]") == 1
    assert "2026-08-09" in again
    assert "- two" in again
    assert "- one" not in again


def test_prepend_leaves_other_versions_untouched_when_replacing():
    doc = changelog.prepend(None, "## [2.1.6] - 2026-07-23\n\n### Fixed\n- old\n")
    doc = changelog.prepend(doc, "## [2.1.7] - 2026-08-05\n\n### Added\n- new\n")
    doc = changelog.prepend(doc, "## [2.1.7] - 2026-08-06\n\n### Added\n- newer\n")
    assert doc.count("## [2.1.6]") == 1
    assert doc.count("## [2.1.7]") == 1
    assert "- old" in doc          # the untouched older release survives
    assert "- newer" in doc
    assert "- new\n" not in doc
