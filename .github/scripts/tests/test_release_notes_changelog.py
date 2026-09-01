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


def _gap_digest(**over):
    base = {
        "tag": "v2.1.8",
        "previous_tag": "v2.1.7",
        "changes": [
            {
                "sha": "abc1234", "component": "backend", "type": "fix",
                "scope": None, "breaking": False,
                "subject": "stop the grid dropping rows",
                "pr_number": 900, "internal": False,
            }
        ],
    }
    base.update(over)
    return base


def test_marker_is_written_when_frontend_is_unrecorded():
    out = changelog.render(
        _gap_digest(frontend_recorded=False), date="2026-09-01", repo="o/r"
    )
    assert changelog.GAP_MARKER in out


def test_no_marker_when_frontend_is_recorded():
    out = changelog.render(
        _gap_digest(frontend_recorded=True), date="2026-09-01", repo="o/r"
    )
    assert changelog.GAP_MARKER not in out


def test_absent_flag_means_recorded():
    # Callers that never resolved a frontend range at all must not be marked —
    # otherwise every historical re-render would sprout a false gap.
    out = changelog.render(_gap_digest(), date="2026-09-01", repo="o/r")
    assert changelog.GAP_MARKER not in out


def test_an_unknown_frontend_flag_is_treated_as_a_gap():
    # `None` means "we do not know", which is what the marker records.
    out = changelog.render(
        _gap_digest(frontend_recorded=None), date="2026-09-01", repo="o/r"
    )
    assert changelog.GAP_MARKER in out


def test_marker_sits_directly_under_the_version_heading():
    out = changelog.render(
        _gap_digest(frontend_recorded=False), date="2026-09-01", repo="o/r"
    ).splitlines()
    assert out[0].startswith("## [2.1.8]")
    assert out[1] == ""
    assert out[2] == changelog.GAP_MARKER
    assert out[3] == ""          # blank line, or the blockquote glues to the next block


def test_the_marker_survives_a_release_with_no_shippable_changes():
    # A release of pure internal work still renders a heading. If the frontend
    # range was unresolvable, that fact must not be lost just because there
    # were no user-facing entries to list.
    out = changelog.render(
        _gap_digest(frontend_recorded=False, changes=[]),
        date="2026-09-01", repo="o/r",
    )
    assert changelog.GAP_MARKER in out


def test_find_gaps_lists_only_marked_versions():
    text = (
        "# Changelog\n\n"
        "## [2.1.8] - 2026-09-01\n\n" + changelog.GAP_MARKER + "\n\n"
        "### Fixed\n- **backend** a\n\n"
        "## [2.1.7] - 2026-08-14\n\n"
        "### Fixed\n- **backend** b\n\n"
        "## [2.1.6] - 2026-07-23\n\n" + changelog.GAP_MARKER + "\n"
    )
    assert changelog.find_gaps(text) == ["2.1.8", "2.1.6"]


def test_find_gaps_is_empty_when_nothing_is_marked():
    text = "# Changelog\n\n## [2.1.7] - 2026-08-14\n\n### Fixed\n- **backend** b\n"
    assert changelog.find_gaps(text) == []


def test_find_gaps_ignores_a_marker_before_any_heading():
    # A marker in the preamble belongs to no version and must not be reported
    # as one, or `list-gaps` would emit a bogus work item.
    text = "# Changelog\n\n" + changelog.GAP_MARKER + "\n\n## [2.1.7] - 2026-08-14\n"
    assert changelog.find_gaps(text) == []


def test_find_gaps_reports_a_version_once_even_with_a_duplicated_marker():
    text = (
        "## [2.1.8] - 2026-09-01\n\n"
        + changelog.GAP_MARKER + "\n"
        + changelog.GAP_MARKER + "\n"
    )
    assert changelog.find_gaps(text) == ["2.1.8"]


def test_a_rerender_replaces_a_marked_section_without_leaving_the_marker():
    # `prepend` already replaces a same-version section. A gap that has since
    # been repaired must not keep its marker.
    marked = changelog.render(
        _gap_digest(frontend_recorded=False), date="2026-09-01", repo="o/r"
    )
    existing = changelog.prepend(None, marked)
    assert changelog.GAP_MARKER in existing

    repaired = changelog.render(
        _gap_digest(frontend_recorded=True), date="2026-09-01", repo="o/r"
    )
    updated = changelog.prepend(existing, repaired)

    assert changelog.GAP_MARKER not in updated
    assert updated.count("## [2.1.8]") == 1
