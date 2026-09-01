"""Tests for verify-frontend and list-gaps — reporting, never failing."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import __main__ as cli  # noqa: E402
from release_notes import changelog  # noqa: E402


def test_verify_frontend_warns_and_succeeds_when_unresolved(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_all_tags", lambda tag: ["v2.1.8", "v2.1.7"])
    monkeypatch.setattr(cli.ranges, "frontend_range", lambda prev, tag: None)
    monkeypatch.setattr(cli.ranges, "frontend_sha_at", lambda ref: None)

    rc = cli.main(["verify-frontend", "--tag", "v2.1.8"])

    out = capsys.readouterr().out
    assert rc == 0                       # a missing range must never block a release
    assert "::warning" in out            # GitHub Actions reads workflow commands from stdout
    assert "v2.1.8" in out
    assert "backfill" in out             # tells the reader how to repair it


def test_verify_frontend_is_quiet_when_resolved(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_all_tags", lambda tag: ["v2.1.8", "v2.1.7"])
    monkeypatch.setattr(
        cli.ranges, "frontend_range",
        lambda prev, tag: cli.ranges.Range(from_sha="aaa", to_sha="bbb"),
    )

    rc = cli.main(["verify-frontend", "--tag", "v2.1.8"])

    assert rc == 0
    assert "::warning" not in capsys.readouterr().out


def test_verify_frontend_names_only_the_missing_end(monkeypatch, capsys):
    # The current tag resolves; only the previous one is missing. The warning
    # must say which, so a reader knows whether the gap is old or new.
    monkeypatch.setattr(cli, "_all_tags", lambda tag: ["v2.1.8", "v2.1.7"])
    monkeypatch.setattr(cli.ranges, "frontend_range", lambda prev, tag: None)
    monkeypatch.setattr(
        cli.ranges, "frontend_sha_at",
        lambda ref: "aaa" if ref == "v2.1.8" else None,
    )

    cli.main(["verify-frontend", "--tag", "v2.1.8"])

    assert "No frontend provenance for v2.1.7." in capsys.readouterr().out


def test_verify_frontend_handles_a_first_release_with_no_previous_tag(monkeypatch, capsys):
    # previous_release_tag returns None for the very first release. The
    # command must not crash or report "None" as a missing tag.
    monkeypatch.setattr(cli, "_all_tags", lambda tag: ["v2.1.8"])
    monkeypatch.setattr(cli.ranges, "frontend_range", lambda prev, tag: None)
    monkeypatch.setattr(cli.ranges, "frontend_sha_at", lambda ref: None)

    rc = cli.main(["verify-frontend", "--tag", "v2.1.8"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "None" not in out
    assert "v2.1.8" in out


def test_verify_frontend_never_writes_to_the_changelog(monkeypatch, capsys, tmp_path):
    # The marker is written by render-changelog at publish time. At prerelease
    # there is no section for this tag yet, so there is nothing to mark.
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n", encoding="utf-8")
    monkeypatch.setattr(cli, "CHANGELOG_PATH", path)
    monkeypatch.setattr(cli, "_all_tags", lambda tag: ["v2.1.8", "v2.1.7"])
    monkeypatch.setattr(cli.ranges, "frontend_range", lambda prev, tag: None)
    monkeypatch.setattr(cli.ranges, "frontend_sha_at", lambda ref: None)

    cli.main(["verify-frontend", "--tag", "v2.1.8"])

    assert path.read_text(encoding="utf-8") == "# Changelog\n"


def test_verify_frontend_survives_a_git_failure(monkeypatch, capsys):
    # _all_tags exits on a shallow clone or an unfetched tag. The gate must
    # still pass — and must say why it could not check.
    def boom(tag):
        raise SystemExit(
            f"Could not list tags reachable from {tag!r}: fatal: bad object"
        )

    monkeypatch.setattr(cli, "_all_tags", boom)

    rc = cli.main(["verify-frontend", "--tag", "v2.1.8"])

    out = capsys.readouterr().out
    assert rc == 0                      # never blocks, even when git cannot answer
    assert "::warning" in out
    assert "v2.1.8" in out
    assert "bad object" in out          # the underlying git error is surfaced


def test_list_gaps_prints_marked_versions(monkeypatch, capsys, tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n"
        "## [2.1.8] - 2026-09-01\n\n" + changelog.GAP_MARKER + "\n\n"
        "### Fixed\n- a\n\n"
        "## [2.1.7] - 2026-08-14\n\n### Fixed\n- b\n\n"
        "## [2.1.6] - 2026-07-23\n\n" + changelog.GAP_MARKER + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "CHANGELOG_PATH", path)

    rc = cli.main(["list-gaps"])

    assert rc == 0
    assert capsys.readouterr().out.split() == ["2.1.8", "2.1.6"]


def test_list_gaps_is_silent_and_succeeds_with_no_changelog(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "CHANGELOG_PATH", tmp_path / "absent.md")

    rc = cli.main(["list-gaps"])

    assert rc == 0
    assert capsys.readouterr().out.strip() == ""


def test_list_gaps_is_silent_when_nothing_is_marked(monkeypatch, capsys, tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\n## [2.1.7] - 2026-08-14\n\n### Fixed\n- b\n",
                    encoding="utf-8")
    monkeypatch.setattr(cli, "CHANGELOG_PATH", path)

    rc = cli.main(["list-gaps"])

    assert rc == 0
    assert capsys.readouterr().out.strip() == ""
