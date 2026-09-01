"""Tests for the backfill span helpers and the backfill command."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import __main__ as cli  # noqa: E402

ORDERED = [
    "v2.0.0rc221", "v2.1.1", "v2.1.2", "v2.1.3",
    "v2.1.4", "v2.1.5", "v2.1.6", "v2.1.7",
]


# ── span selection ────────────────────────────────────────────────────

def test_tag_span_is_inclusive_at_both_ends():
    assert cli._tag_span("v2.1.2", "v2.1.4", tags=ORDERED) == ["v2.1.2", "v2.1.3", "v2.1.4"]


def test_tag_span_of_a_single_tag_is_that_tag():
    assert cli._tag_span("v2.1.6", "v2.1.6", tags=ORDERED) == ["v2.1.6"]


def test_tag_span_rejects_a_reversed_range():
    with pytest.raises(SystemExit) as exc:
        cli._tag_span("v2.1.6", "v2.1.2", tags=ORDERED)
    assert "newer than" in str(exc.value)


def test_tag_span_rejects_an_unknown_tag():
    with pytest.raises(SystemExit) as exc:
        cli._tag_span("v9.9.9", "v2.1.6", tags=ORDERED)
    assert "v9.9.9" in str(exc.value)


def test_tag_span_spans_the_whole_v21x_range():
    # The scope this plan actually targets.
    got = cli._tag_span("v2.1.1", "v2.1.7", tags=ORDERED)
    assert got == ["v2.1.1", "v2.1.2", "v2.1.3", "v2.1.4", "v2.1.5", "v2.1.6", "v2.1.7"]
    assert "v2.0.0rc221" not in got


# ── already-rendered detection ────────────────────────────────────────

def test_already_rendered_detects_an_existing_section():
    text = "# Changelog\n\n## [2.1.6] - 2026-07-23\n\n### Fixed\n- a\n"
    assert cli._already_rendered(text, "v2.1.6") is True
    assert cli._already_rendered(text, "v2.1.5") is False


def test_already_rendered_is_not_confused_by_a_version_prefix():
    # "## [2.1.6]" must not satisfy a query for v2.1.60 or v2.1.
    text = "# Changelog\n\n## [2.1.60] - 2026-07-23\n"
    assert cli._already_rendered(text, "v2.1.6") is False


# ── the command ───────────────────────────────────────────────────────

def _no_write_env(monkeypatch, tmp_path, *, existing: str = "# Changelog\n"):
    path = tmp_path / "CHANGELOG.md"
    if existing is not None:
        path.write_text(existing, encoding="utf-8")
    monkeypatch.setattr(cli, "CHANGELOG_PATH", path)
    monkeypatch.setattr(cli, "_release_tags_in_order", lambda: ORDERED)
    return path


def test_backfill_dry_run_writes_nothing(monkeypatch, capsys, tmp_path):
    path = _no_write_env(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_render_one", lambda tag, pac: f"## [{tag[1:]}] - x\n")

    rc = cli.main(["backfill", "--from", "v2.1.6", "--to", "v2.1.6", "--dry-run"])

    assert rc == 0
    assert path.read_text(encoding="utf-8") == "# Changelog\n"
    assert "2.1.6" in capsys.readouterr().out


def test_backfill_skip_existing_leaves_a_rendered_tag_alone(monkeypatch, tmp_path):
    path = _no_write_env(
        monkeypatch, tmp_path,
        existing="# Changelog\n\n## [2.1.6] - 2026-07-23\n\n### Fixed\n- keep me\n",
    )
    calls = []
    monkeypatch.setattr(
        cli, "_render_one", lambda tag, pac: calls.append(tag) or "## [x] - y\n"
    )

    cli.main(["backfill", "--from", "v2.1.6", "--to", "v2.1.6", "--skip-existing"])

    assert calls == []                                   # never rendered
    assert "keep me" in path.read_text(encoding="utf-8")  # never rewritten


def test_backfill_force_replaces_an_existing_section(monkeypatch, tmp_path):
    path = _no_write_env(
        monkeypatch, tmp_path,
        existing="# Changelog\n\n## [2.1.6] - 2026-07-23\n\n### Fixed\n- stale\n",
    )
    monkeypatch.setattr(
        cli, "_render_one",
        lambda tag, pac: "## [2.1.6] - 2026-07-23\n\n### Fixed\n- fresh\n",
    )

    cli.main(["backfill", "--from", "v2.1.6", "--to", "v2.1.6", "--force"])

    text = path.read_text(encoding="utf-8")
    assert "fresh" in text
    assert "stale" not in text
    assert text.count("## [2.1.6]") == 1     # replaced, not duplicated


def test_backfill_repairs_one_gap_and_leaves_others(monkeypatch, tmp_path):
    # The repair path. Two marked versions; re-rendering one must clear only
    # its own marker.
    from release_notes import changelog
    existing = (
        "# Changelog\n\n"
        "## [2.1.7] - 2026-08-14\n\n" + changelog.GAP_MARKER + "\n\n### Fixed\n- a\n\n"
        "## [2.1.6] - 2026-07-23\n\n" + changelog.GAP_MARKER + "\n\n### Fixed\n- b\n"
    )
    path = _no_write_env(monkeypatch, tmp_path, existing=existing)
    monkeypatch.setattr(
        cli, "_render_one",
        lambda tag, pac: "## [2.1.7] - 2026-08-14\n\n### Fixed\n- a\n",
    )

    cli.main(["backfill", "--tag", "v2.1.7", "--force"])

    text = path.read_text(encoding="utf-8")
    assert changelog.find_gaps(text) == ["2.1.6"]     # only 2.1.6 still marked


def test_backfill_creates_the_changelog_when_absent(monkeypatch, tmp_path):
    # CHANGELOG.md does not exist in this repo yet, so the first real run
    # creates it — `prepend` emits the preamble for `existing=None`.
    path = tmp_path / "CHANGELOG.md"
    monkeypatch.setattr(cli, "CHANGELOG_PATH", path)
    monkeypatch.setattr(cli, "_release_tags_in_order", lambda: ORDERED)
    monkeypatch.setattr(cli, "_render_one", lambda tag, pac: f"## [{tag[1:]}] - x\n")

    rc = cli.main(["backfill", "--tag", "v2.1.6"])

    assert rc == 0
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Changelog")     # preamble emitted
    assert "## [2.1.6]" in text


def test_backfill_renders_a_span_oldest_first(monkeypatch, tmp_path):
    # Rendering oldest-first matters: `prepend` puts each new section at the
    # top, so processing in ascending order leaves the file newest-first.
    path = _no_write_env(monkeypatch, tmp_path)
    order = []
    monkeypatch.setattr(
        cli, "_render_one",
        lambda tag, pac: order.append(tag) or f"## [{tag[1:]}] - x\n",
    )

    cli.main(["backfill", "--from", "v2.1.5", "--to", "v2.1.7"])

    assert order == ["v2.1.5", "v2.1.6", "v2.1.7"]
    text = path.read_text(encoding="utf-8")
    assert text.index("## [2.1.7]") < text.index("## [2.1.5]")   # newest first


def test_backfill_requires_a_range_or_a_tag():
    with pytest.raises(SystemExit) as exc:
        cli.main(["backfill"])
    assert "--tag" in str(exc.value)
