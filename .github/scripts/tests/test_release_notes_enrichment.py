"""Tests for PR enrichment across two repositories.

A frontend sha means nothing in lex-app. `gh api repos/{owner}/{repo}/...`
expands the placeholder from the git remote of its working directory, so a
lookup run in the wrong directory does not fail — it answers about a different
repository. These pin which directory each half is asked in.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import digest  # noqa: E402


class _Result:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout, self.returncode, self.stderr = stdout, returncode, ""


def test_a_bound_lookup_runs_in_the_given_checkout(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(cwd=kwargs.get("cwd"), env=kwargs.get("env"))
        return _Result('[7, "A title", "A body"]')

    monkeypatch.setattr(digest.subprocess, "run", fake_run)
    lookup = digest.pr_lookup_for(Path("/somewhere/pac"), token="t0ken")

    assert lookup("abc1234") == (7, "A title", "A body")
    assert seen["cwd"] == Path("/somewhere/pac")


def test_a_token_is_passed_under_both_names(monkeypatch):
    # `gh` prefers GH_TOKEN but falls back to GITHUB_TOKEN. Overriding only one
    # lets a stale value in the other win over the credential we meant to use.
    seen = {}
    monkeypatch.setattr(
        digest.subprocess, "run",
        lambda cmd, **kw: (seen.update(env=kw.get("env")), _Result('[1, "t", ""]'))[1],
    )
    digest.pr_lookup_for(Path("/pac"), token="t0ken")("abc")
    assert seen["env"]["GH_TOKEN"] == "t0ken"
    assert seen["env"]["GITHUB_TOKEN"] == "t0ken"


def test_no_token_inherits_the_ambient_environment(monkeypatch):
    """env=None, not env={} — an empty environment loses PATH and HOME."""
    seen = {}
    monkeypatch.setattr(
        digest.subprocess, "run",
        lambda cmd, **kw: (seen.update(env=kw.get("env")), _Result('[1, "t", ""]'))[1],
    )
    digest.pr_lookup_for(Path("/pac"))("abc")
    assert seen["env"] is None


def test_the_default_lookup_still_runs_in_this_repository(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        digest.subprocess, "run",
        lambda cmd, **kw: (seen.update(cwd=kw.get("cwd")), _Result(""))[1],
    )
    digest._lookup_pr("abc1234")
    assert seen["cwd"] == digest.REPO_ROOT


def test_the_tally_names_which_half_it_is_reporting(capsys):
    digest.enrich_with_prs(
        [digest.Commit(sha="a", subject="s")],
        lookup=lambda sha: None,
        label="Frontend PR enrichment",
    )
    assert "Frontend PR enrichment: 0/1" in capsys.readouterr().err


def test_a_lookup_that_raises_leaves_the_commit_unenriched():
    """A release is worth shipping with partial enrichment."""
    def boom(sha):
        raise RuntimeError("403")

    out = digest.enrich_with_prs([digest.Commit(sha="a", subject="s")], lookup=boom)
    assert out[0].pr_number is None
