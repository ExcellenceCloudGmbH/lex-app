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
