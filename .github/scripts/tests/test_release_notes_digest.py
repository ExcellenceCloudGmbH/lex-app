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
        return (675, "fix(calc): never stamp edited_at for a calculation-owned save",
                "## Why\n\nedited_at records the last user edit.")

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


def test_commits_from_one_pr_collapse_to_a_single_entry():
    # 17 commits from PR #696 produced 17 identical lines on v2.1.7rc1,
    # because enrichment gives every commit in a PR the same title.
    commits = [
        digest.Commit(sha=f"{i:07d}", subject=f"work {i}", pr_number=696,
                      pr_title="feat(release-notes): automation")
        for i in range(17)
    ]
    got = digest.build_digest("v2.1.7", "v2.1.6", commits, [])
    assert len(got["changes"]) == 1
    assert got["changes"][0]["pr_number"] == 696
    # The first commit in the range represents the PR.
    assert got["changes"][0]["sha"] == "0000000"


def test_distinct_prs_are_kept_apart():
    commits = [
        digest.Commit(sha="aaa", subject="x", pr_number=696, pr_title="fix(a): one"),
        digest.Commit(sha="bbb", subject="y", pr_number=697, pr_title="fix(b): two"),
        digest.Commit(sha="ccc", subject="z", pr_number=696, pr_title="fix(a): one"),
    ]
    got = digest.build_digest("v2.1.7", "v2.1.6", commits, [])
    assert [c["pr_number"] for c in got["changes"]] == [696, 697]


def test_commits_without_a_pr_are_never_collapsed():
    commits = [
        digest.Commit(sha="aaa", subject="fix(core): direct push one"),
        digest.Commit(sha="bbb", subject="fix(core): direct push two"),
    ]
    got = digest.build_digest("v2.1.7", "v2.1.6", commits, [])
    assert len(got["changes"]) == 2


def test_the_same_pr_number_in_each_repo_stays_separate():
    # Backend #12 and frontend #12 are different pull requests.
    back = [digest.Commit(sha="aaa", subject="x", pr_number=12, pr_title="fix(api): backend")]
    front = [digest.Commit(sha="bbb", subject="y", pr_number=12, pr_title="fix(ui): frontend")]
    got = digest.build_digest("v2.1.7", "v2.1.6", back, front)
    assert len(got["changes"]) == 2
    assert {c["component"] for c in got["changes"]} == {"backend", "frontend"}


# ── Internal-vs-user-facing classification ────────────────────────────

def test_housekeeping_types_are_flagged_internal():
    for t in ("ci", "build", "chore", "test", "docs"):
        assert digest.is_internal(t, None) is True, t


def test_toolchain_scopes_are_flagged_internal_whatever_the_type():
    # feat(release-notes) is a feature *of our pipeline*, not of LEX. Read as
    # a product change it becomes "LEX can now connect to Gemini" — which is
    # what v2.1.7rc1 actually published.
    for scope in ("release-notes", "test-plan", "ci", "gate", "showcase", "plan", "spec"):
        assert digest.is_internal("feat", scope) is True, scope


def test_product_changes_are_not_flagged_internal():
    for typ, scope in (("fix", "auth"), ("fix", "calc"), ("feat", "grid"), ("fix", "proxy")):
        assert digest.is_internal(typ, scope) is False, (typ, scope)


def test_the_digest_carries_the_flag():
    commits = [
        digest.Commit(sha="aaa", subject="feat(release-notes): pluggable providers"),
        digest.Commit(sha="bbb", subject="fix(auth): renew the embedded token"),
    ]
    got = digest.build_digest("v2.1.7", "v2.1.6", commits, [])
    by_scope = {c["scope"]: c["internal"] for c in got["changes"]}
    assert by_scope["release-notes"] is True
    assert by_scope["auth"] is False


# ── PR bodies as context ──────────────────────────────────────────────
#
# Subjects alone make the drafter invent detail. "renew the embedded Streamlit
# token instead of prompting re-login" is nine words; its PR body explains that
# embedded sessions previously died at the original deadline regardless. That
# is the sentence a reader needs, and the author already wrote it.

def test_the_pr_body_reaches_the_entry():
    commits = [digest.Commit(sha="aaa", subject="fix(auth): renew the token",
                             pr_number=678, pr_title="fix(auth): renew the token",
                             pr_body="## Why\n\nSessions died at the original deadline.")]
    got = digest.build_digest("v1", None, commits, [])
    assert "died at the original deadline" in got["changes"][0]["detail"]


def test_a_long_body_is_truncated_with_a_marker():
    body = "x" * (digest.PR_BODY_LIMIT + 500)
    commits = [digest.Commit(sha="a", subject="fix(auth): x", pr_number=1,
                             pr_title="fix(auth): x", pr_body=body)]
    got = digest.build_digest("v1", None, commits, [])
    detail = got["changes"][0]["detail"]
    assert len(detail) <= digest.PR_BODY_LIMIT + 40
    assert detail.endswith("…[truncated]")


def test_internal_changes_carry_no_body():
    # They are omitted from the note, so their bodies are pure prompt cost.
    commits = [digest.Commit(sha="a", subject="feat(release-notes): providers",
                             pr_number=1, pr_title="feat(release-notes): providers",
                             pr_body="A very long explanation of our CI tooling.")]
    got = digest.build_digest("v1", None, commits, [])
    assert got["changes"][0]["internal"] is True
    assert got["changes"][0]["detail"] == ""


def test_a_change_without_a_pr_body_has_an_empty_detail():
    commits = [digest.Commit(sha="a", subject="fix(auth): direct push")]
    got = digest.build_digest("v1", None, commits, [])
    assert got["changes"][0]["detail"] == ""


def test_html_comments_and_trailers_are_stripped_from_the_body():
    body = ("## Why\n\nReal content.\n\n<!-- a template comment -->\n"
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n"
            "Co-Authored-By: Someone <x@y.z>\n")
    commits = [digest.Commit(sha="a", subject="fix(auth): x", pr_number=1,
                             pr_title="fix(auth): x", pr_body=body)]
    detail = digest.build_digest("v1", None, commits, [])["changes"][0]["detail"]
    assert "Real content." in detail
    assert "template comment" not in detail
    assert "Co-Authored-By" not in detail
    assert "Generated with" not in detail
