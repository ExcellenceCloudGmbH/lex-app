"""Tests for the append-frontend-note command — the only CLI path that
writes to a published release."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import __main__ as cli  # noqa: E402
from release_notes import notes  # noqa: E402


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_gh(monkeypatch, *, view=None, edit=None):
    """Record every subprocess call as (argv, kwargs); serve canned gh responses.

    Recording kwargs alongside argv (not just argv) matters here: the release
    body written by `gh release edit --notes-file -` travels via the `input=`
    kwarg, not argv, so a test that needs to see what was actually written has
    to look at kwargs.
    """
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if "view" in argv:
            return view or _Result(stdout="## Main changes\n\n- something\n")
        if "edit" in argv:
            return edit or _Result()
        return _Result()

    monkeypatch.setattr(cli.subprocess, "run", run)
    return calls


def _digest(monkeypatch, *, frontend=True):
    changes = [
        {"sha": "aaa1111", "component": "backend", "type": "fix", "scope": None,
         "breaking": False, "subject": "a backend fix", "pr_number": 1,
         "internal": False},
    ]
    if frontend:
        changes.append(
            {"sha": "bbb2222", "component": "frontend", "type": "fix", "scope": None,
             "breaking": False, "subject": "grouping shows the name", "pr_number": 2,
             "internal": False}
        )
    # _digest_for is replaced wholesale, so none of its own subprocess calls
    # (git tag listing, `gh api` PR enrichment) reach the fake `run` above —
    # only the two `gh release ...` calls inside cmd_append_frontend_note do.
    monkeypatch.setattr(
        cli, "_digest_for",
        lambda tag, pac_checkout=None: {"tag": tag, "previous_tag": None,
                                        "changes": changes},
    )


def test_dry_run_prints_the_updated_body_and_never_edits(monkeypatch, capsys):
    _digest(monkeypatch)
    calls = _fake_gh(monkeypatch)

    rc = cli.main(["append-frontend-note", "--tag", "v2.1.6", "--dry-run"])

    assert rc == 0
    out = capsys.readouterr().out
    assert notes.ADDENDUM_MARKER in out
    assert "grouping shows the name" in out
    assert "- something" in out                          # original body preserved
    assert not any("edit" in argv for argv, _ in calls)   # nothing written


def test_no_frontend_changes_returns_zero_without_reading_the_release(monkeypatch):
    _digest(monkeypatch, frontend=False)
    calls = _fake_gh(monkeypatch)

    rc = cli.main(["append-frontend-note", "--tag", "v2.1.6"])

    assert rc == 0
    # No frontend changes means the function returns before it ever shells out.
    assert calls == []


def test_an_existing_addendum_is_left_alone(monkeypatch):
    _digest(monkeypatch)
    body = "## Main changes\n\n- x\n\n" + notes.ADDENDUM_MARKER + "\n\n- old\n"
    calls = _fake_gh(monkeypatch, view=_Result(stdout=body))

    rc = cli.main(["append-frontend-note", "--tag", "v2.1.6"])

    assert rc == 0
    assert not any("edit" in argv for argv, _ in calls)   # must not overwrite


def test_a_failed_read_reports_and_returns_one(monkeypatch):
    _digest(monkeypatch)
    calls = _fake_gh(monkeypatch, view=_Result(returncode=1, stderr="release not found"))

    rc = cli.main(["append-frontend-note", "--tag", "v9.9.9"])

    assert rc == 1
    assert not any("edit" in argv for argv, _ in calls)   # never write after a failed read


def test_a_failed_write_reports_and_returns_one(monkeypatch):
    _digest(monkeypatch)
    _fake_gh(monkeypatch, edit=_Result(returncode=1, stderr="permission denied"))

    rc = cli.main(["append-frontend-note", "--tag", "v2.1.6"])

    assert rc == 1


def test_the_success_path_writes_the_appended_body(monkeypatch):
    _digest(monkeypatch)
    calls = _fake_gh(monkeypatch)

    rc = cli.main(["append-frontend-note", "--tag", "v2.1.6"])

    assert rc == 0
    edit_calls = [(argv, kwargs) for argv, kwargs in calls if "edit" in argv]
    assert len(edit_calls) == 1
    # What was actually written travels via `input=`, not argv (argv only
    # carries "--notes-file -", telling gh to read the body from stdin) — so
    # asserting on argv alone cannot verify the content that got published.
    written = edit_calls[0][1]["input"]
    assert notes.ADDENDUM_MARKER in written
    assert "grouping shows the name" in written
    assert "- something" in written                       # original body preserved


# ── _digest_for: the branch-to-flag wiring ──────────────────────────────
#
# The tests above replace `_digest_for` wholesale, so none of them exercise
# its own logic. These do: they stub out git/gh at the boundary and drive the
# three branches directly, so the `built["frontend_recorded"] = ...` line —
# the only place a real release sets the flag the changelog marker reads —
# has coverage of its own instead of relying on the changelog tests' hand
# -built dicts.

def _stub_backend(monkeypatch):
    """Neutralise the backend half of _digest_for — git and gh are not under test."""
    monkeypatch.setattr(cli, "_all_tags", lambda tag: ["v2.1.8", "v2.1.7"])
    monkeypatch.setattr(cli.digest, "collect_commits", lambda *a, **kw: [])
    monkeypatch.setattr(cli.digest, "enrich_with_prs", lambda commits: commits)


def test_digest_for_flags_an_unresolvable_frontend_range(monkeypatch):
    _stub_backend(monkeypatch)
    monkeypatch.setattr(cli.ranges, "frontend_range", lambda prev, tag: None)

    built = cli._digest_for("v2.1.8")

    assert built["frontend_recorded"] is False


def test_digest_for_flags_a_resolved_range_with_no_pac_checkout(monkeypatch):
    _stub_backend(monkeypatch)
    monkeypatch.setattr(
        cli.ranges, "frontend_range",
        lambda prev, tag: cli.ranges.Range(from_sha="aaa", to_sha="bbb"),
    )

    built = cli._digest_for("v2.1.8", pac_checkout=None)

    assert built["frontend_recorded"] is False


def test_digest_for_records_a_resolved_range_with_a_pac_checkout(monkeypatch, tmp_path):
    _stub_backend(monkeypatch)
    monkeypatch.setattr(
        cli.ranges, "frontend_range",
        lambda prev, tag: cli.ranges.Range(from_sha="aaa", to_sha="bbb"),
    )

    built = cli._digest_for("v2.1.8", pac_checkout=tmp_path)

    assert built["frontend_recorded"] is True


# ── check-manifest: the PR-guard subcommand ─────────────────────────────

def test_check_manifest_passes_on_a_valid_manifest(monkeypatch, tmp_path):
    root = tmp_path
    (root / "lex" / "react" / "build").mkdir(parents=True)
    (root / cli.ranges.MANIFEST_PATH).write_text(
        '{"sha": "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"}', encoding="utf-8"
    )
    monkeypatch.setattr(cli.ranges, "REPO_ROOT", root)

    assert cli.main(["check-manifest"]) == 0


def test_check_manifest_fails_when_absent(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.ranges, "REPO_ROOT", tmp_path)

    rc = cli.main(["check-manifest"])

    assert rc == 1
    assert "::error" in capsys.readouterr().out


def test_check_manifest_fails_on_a_blank_sha(monkeypatch, tmp_path, capsys):
    root = tmp_path
    (root / "lex" / "react" / "build").mkdir(parents=True)
    (root / cli.ranges.MANIFEST_PATH).write_text('{"sha": ""}', encoding="utf-8")
    monkeypatch.setattr(cli.ranges, "REPO_ROOT", root)

    rc = cli.main(["check-manifest"])

    assert rc == 1
    out = capsys.readouterr().out
    assert "::error" in out
    assert "sha" in out


def test_digest_for_records_a_gap_when_the_pac_checkout_cannot_be_read(
    monkeypatch, tmp_path
):
    # A PAC directory that exists but is unreadable used to raise straight out
    # of render-changelog and fail the publish job. It must degrade to a gap,
    # the same as an unresolvable range.
    monkeypatch.setattr(cli, "_all_tags", lambda tag: ["v2.1.8", "v2.1.7"])
    monkeypatch.setattr(cli.digest, "enrich_with_prs", lambda commits: commits)
    monkeypatch.setattr(
        cli.ranges, "frontend_range",
        lambda prev, tag: cli.ranges.Range(from_sha="aaa", to_sha="bbb"),
    )

    def collect(*args, **kwargs):
        # Only the frontend call passes run_log; the backend call must succeed.
        if "run_log" in kwargs:
            raise FileNotFoundError("no such directory: ./pac")
        return []

    monkeypatch.setattr(cli.digest, "collect_commits", collect)

    built = cli._digest_for("v2.1.8", pac_checkout=tmp_path)

    assert built["frontend_recorded"] is False
    assert built["changes"] == []


# ── Production wiring for the new prompt context ──────────────────────

def test_digest_for_records_the_frontend_commit_count_and_the_facts(monkeypatch, tmp_path):
    """The two pieces of context the drafter cannot compute for itself."""
    from release_notes import __main__ as main, digest as digest_mod, ranges, facts

    monkeypatch.setattr(main, "_previous_tag_for", lambda tag: "v1.0.0", raising=False)
    monkeypatch.setattr(ranges, "frontend_range", lambda p, c, **k: None)
    monkeypatch.setattr(digest_mod, "collect_commits", lambda a, b, **k: [])
    monkeypatch.setattr(facts, "collect", lambda a, b, **k: {
        "migrations": ["0007_x"], "commands": [], "env_vars": [],
        "needs_migration": True,
    })

    built = main._digest_for("v1.0.1", pac_checkout=None)
    assert built["frontend_commits"] == 0
    assert "0007_x" in built["facts"]


def test_draft_uses_the_facts_the_digest_carries():
    from release_notes import notes
    seen = {}
    d = {"tag": "v1", "previous_tag": "v0", "facts": "- A COMPUTED FACT",
         "changes": [{"sha": "1", "component": "backend", "type": "fix", "scope": "grid",
                      "breaking": False, "subject": "x", "pr_number": None,
                      "internal": False, "detail": "y"}]}
    def model(prompt):
        seen["prompt"] = prompt
        return "## Bug fixes\n\n- **A fix.** Text.\n"
    notes.draft(d, exemplar="X", model=model)
    assert "A COMPUTED FACT" in seen["prompt"]
