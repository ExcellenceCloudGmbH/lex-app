"""Tests for release_notes.digest — commit parsing, filtering, PR enrichment."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import digest  # noqa: E402


def test_parses_type_scope_and_subject():
    got = digest.parse_subject("fix(calc): never stamp edited_at for a calculation-owned save")
    assert got == digest.Parsed(
        type="fix",
        scope="calc",
        breaking=False,
        subject="never stamp edited_at for a calculation-owned save",
    )


def test_parses_a_type_without_a_scope():
    got = digest.parse_subject("feat: add the widget")
    assert got.type == "feat"
    assert got.scope is None
    assert got.subject == "add the widget"


def test_detects_a_breaking_marker():
    got = digest.parse_subject("feat(api)!: drop the v1 endpoint")
    assert got.breaking is True
    assert got.type == "feat"
    assert got.subject == "drop the v1 endpoint"


def test_non_conforming_subjects_become_other_and_keep_their_text():
    got = digest.parse_subject("Mode change is a beauty")
    assert got == digest.Parsed(
        type="other", scope=None, breaking=False, subject="Mode change is a beauty"
    )


def test_an_unknown_prefix_is_not_treated_as_a_type():
    # "wibble" is not in the conventional set, so this is not a typed commit.
    got = digest.parse_subject("wibble(core): something")
    assert got.type == "other"
    assert got.subject == "wibble(core): something"


def test_build_digest_maps_commits_to_entries():
    commits = [
        digest.Commit(sha="705850d", subject="fix(calc): stop stamping edited_at"),
        digest.Commit(sha="81edcbc", subject="feat(setup): onboard any agentic IDE"),
    ]
    got = digest.build_digest(
        tag="v2.1.7",
        previous_tag="v2.1.6",
        backend_commits=commits,
        frontend_commits=[],
    )
    assert got["tag"] == "v2.1.7"
    assert got["previous_tag"] == "v2.1.6"
    assert [c["sha"] for c in got["changes"]] == ["705850d", "81edcbc"]
    assert got["changes"][0]["component"] == "backend"
    assert got["changes"][0]["type"] == "fix"


def test_build_digest_labels_frontend_commits():
    got = digest.build_digest(
        tag="v2.1.7",
        previous_tag="v2.1.6",
        backend_commits=[],
        frontend_commits=[digest.Commit(sha="a3f91c2", subject="fix(export): send the viewer timezone")],
    )
    assert got["changes"][0]["component"] == "frontend"


def test_merge_commits_are_dropped():
    commits = [
        digest.Commit(sha="1111111", subject="Merge pull request #678 from Excellence/fix/x"),
        digest.Commit(sha="2222222", subject="Merge branch 'lex-app-v2' into feature"),
        digest.Commit(sha="3333333", subject="fix(core): a real change"),
    ]
    got = digest.build_digest("v2.1.7", "v2.1.6", commits, [])
    assert [c["sha"] for c in got["changes"]] == ["3333333"]


def test_bundle_update_commits_are_dropped():
    commits = [
        digest.Commit(sha="4444444", subject="build(frontend): update bundle from Excellence/pac@abc123"),
        digest.Commit(sha="5555555", subject="feat(core): a real change"),
    ]
    got = digest.build_digest("v2.1.7", "v2.1.6", commits, [])
    assert [c["sha"] for c in got["changes"]] == ["5555555"]


def test_empty_subjects_are_dropped():
    commits = [
        digest.Commit(sha="6666666", subject="   "),
        digest.Commit(sha="7777777", subject="fix(core): real"),
    ]
    got = digest.build_digest("v2.1.7", "v2.1.6", commits, [])
    assert [c["sha"] for c in got["changes"]] == ["7777777"]


def test_collect_commits_parses_the_git_log_format():
    raw = "705850d\x1ffix(calc): stop stamping edited_at\n81edcbc\x1ffeat(setup): onboard IDEs"
    got = digest.collect_commits("v2.1.6", "v2.1.7", run_log=lambda a, b: raw)
    assert got == [
        digest.Commit(sha="705850d", subject="fix(calc): stop stamping edited_at"),
        digest.Commit(sha="81edcbc", subject="feat(setup): onboard IDEs"),
    ]


def test_collect_commits_returns_empty_for_an_empty_log():
    assert digest.collect_commits("v2.1.6", "v2.1.7", run_log=lambda a, b: "") == []


def test_enrich_prefers_the_pr_title():
    commits = [digest.Commit(sha="705850d", subject="publishable")]

    def fake_lookup(sha: str):
        assert sha == "705850d"
        return (675, "fix(calc): never stamp edited_at for a calculation-owned save")

    got = digest.enrich_with_prs(commits, lookup=fake_lookup)
    assert got[0].pr_number == 675
    assert got[0].pr_title == "fix(calc): never stamp edited_at for a calculation-owned save"
    # The original subject is preserved, not overwritten.
    assert got[0].subject == "publishable"


def test_enrich_leaves_a_commit_without_a_pr_untouched():
    commits = [digest.Commit(sha="deadbee", subject="fix(core): direct push")]
    got = digest.enrich_with_prs(commits, lookup=lambda sha: None)
    assert got[0].pr_number is None
    assert got[0].pr_title is None
    assert got[0].subject == "fix(core): direct push"


def test_enrich_survives_a_lookup_failure():
    def boom(sha: str):
        raise RuntimeError("gh api rate limited")

    commits = [digest.Commit(sha="deadbee", subject="fix(core): x")]
    got = digest.enrich_with_prs(commits, lookup=boom)
    assert got[0].pr_number is None
