"""Tests for release_notes.ranges — tag classification and range resolution."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Make .github/scripts importable when pytest runs from the repo root.
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import ranges  # noqa: E402


@pytest.mark.parametrize(
    "tag",
    ["v2.1.6", "v2.0.0rc215", "v10.0.1", "v2.0.0rc1"],
)
def test_release_tags_are_recognised(tag: str):
    assert ranges.is_release_tag(tag) is True


@pytest.mark.parametrize(
    "tag",
    [
        "v0.0.0-hazem",                      # a real tag in this repo
        "recovery-supervisor-on-demand.0",   # also real
        "2.1.6",                             # no v prefix
        "v2.1",                              # not three components
        "v2.1.6-rc1",                        # hyphenated rc is not our scheme
        "",
        None,
    ],
)
def test_non_release_tags_are_rejected(tag):
    assert ranges.is_release_tag(tag) is False


def test_previous_release_tag_skips_junk_tags():
    # Newest first, as `git tag --sort=-creatordate` returns them.
    tags = ["v2.1.6", "v0.0.0-hazem", "recovery-supervisor-on-demand.0", "v2.1.5"]
    assert ranges.previous_release_tag("v2.1.6", tags=tags) == "v2.1.5"


def test_previous_release_tag_accepts_an_rc_as_a_baseline():
    tags = ["v2.1.1", "v2.0.0rc221", "v2.0.0rc220"]
    assert ranges.previous_release_tag("v2.1.1", tags=tags) == "v2.0.0rc221"


def test_previous_release_tag_is_none_when_only_the_tag_itself_is_present():
    assert ranges.previous_release_tag("v2.1.6", tags=["v2.1.6"]) is None


def test_previous_release_tag_is_none_when_only_junk_precedes_it():
    tags = ["v2.1.6", "v0.0.0-hazem", "recovery-supervisor-on-demand.0"]
    assert ranges.previous_release_tag("v2.1.6", tags=tags) is None


def test_frontend_sha_at_reads_the_manifest():
    def fake_show(ref: str, path: str) -> str | None:
        assert path == ranges.MANIFEST_PATH
        return '{"repo": "x/y", "branch": "b", "sha": "a3f91c2", "built_at": "2026-08-05T09:14:22Z"}'

    assert ranges.frontend_sha_at("v2.1.6", show=fake_show) == "a3f91c2"


def test_frontend_sha_at_returns_none_when_the_manifest_is_absent():
    assert ranges.frontend_sha_at("v2.1.6", show=lambda ref, path: None) is None


def test_frontend_sha_at_returns_none_on_malformed_manifest():
    assert ranges.frontend_sha_at("v2.1.6", show=lambda ref, path: "not json") is None
    assert ranges.frontend_sha_at("v2.1.6", show=lambda ref, path: '{"no_sha": 1}') is None


def test_frontend_range_is_none_when_the_manifest_is_missing_at_either_end():
    # Missing at the current tag.
    shas = {"v2.1.5": "aaa1111"}
    got = ranges.frontend_range("v2.1.5", "v2.1.6", show=_shas(shas))
    assert got is None

    # Missing at the previous tag.
    shas = {"v2.1.6": "bbb2222"}
    got = ranges.frontend_range("v2.1.5", "v2.1.6", show=_shas(shas))
    assert got is None


def test_frontend_range_resolves_when_present_at_both_ends():
    shas = {"v2.1.5": "aaa1111", "v2.1.6": "bbb2222"}
    got = ranges.frontend_range("v2.1.5", "v2.1.6", show=_shas(shas))
    assert got == ranges.Range(from_sha="aaa1111", to_sha="bbb2222")


def test_frontend_range_on_the_first_release_has_no_lower_bound():
    shas = {"v2.1.6": "bbb2222"}
    got = ranges.frontend_range(None, "v2.1.6", show=_shas(shas))
    assert got == ranges.Range(from_sha=None, to_sha="bbb2222")


def _shas(mapping: dict[str, str]):
    """Build a `show` stub that serves manifests for the given refs only."""
    import json

    def show(ref: str, path: str) -> str | None:
        if ref not in mapping:
            return None
        return json.dumps({"repo": "x/y", "branch": "b", "sha": mapping[ref]})

    return show


def test_frontend_sha_at_treats_a_blank_sha_as_absent():
    # A hand-written manifest can carry an empty or null sha. Both are as
    # untruthful as a missing file, so both must collapse to None rather
    # than producing a Range with a blank end.
    assert ranges.frontend_sha_at("v2.1.6", show=lambda ref, path: '{"sha": ""}') is None
    assert ranges.frontend_sha_at("v2.1.6", show=lambda ref, path: '{"sha": null}') is None


def test_frontend_range_is_none_when_a_recorded_sha_is_blank():
    shas = {"v2.1.5": "aaa1111", "v2.1.6": ""}

    def show(ref: str, path: str) -> str | None:
        import json as _json
        return _json.dumps({"sha": shas[ref]}) if ref in shas else None

    assert ranges.frontend_range("v2.1.5", "v2.1.6", show=show) is None


def test_bundle_commit_at_returns_the_sha():
    def fake_run(ref: str) -> str | None:
        assert ref == "v2.1.6"
        return "a388985a1111111111111111111111111111aaaa"

    got = ranges.bundle_commit_at("v2.1.6", run=fake_run)
    assert got == "a388985a1111111111111111111111111111aaaa"


def test_bundle_commit_at_returns_none_when_no_bundle_history():
    assert ranges.bundle_commit_at("v1.0.0", run=lambda ref: None) is None


def test_bundle_commit_at_treats_blank_output_as_none():
    assert ranges.bundle_commit_at("v2.1.6", run=lambda ref: "") is None
    assert ranges.bundle_commit_at("v2.1.6", run=lambda ref: "   ") is None


def test_run_rev_list_pins_the_git_invocation(monkeypatch):
    seen = {}

    class Result:
        returncode = 0
        stdout = "a388985a1111111111111111111111111111aaaa\n"
        stderr = ""

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["cwd"] = kwargs.get("cwd")
        return Result()

    monkeypatch.setattr(ranges.subprocess, "run", fake_run)

    out = ranges._run_rev_list("v2.1.6")

    # Pinned because dropping -1 returns EVERY bundle commit, and the
    # downstream prefix lookup would still match the first line — a wrong
    # answer that looks right.
    assert seen["argv"] == ["git", "rev-list", "-1", "v2.1.6", "--", ranges.BUNDLE_PATH]
    assert seen["cwd"] == ranges.REPO_ROOT
    assert out == "a388985a1111111111111111111111111111aaaa\n"   # raw, unnormalised


def test_run_rev_list_reports_and_returns_empty_on_git_failure(monkeypatch, capsys):
    class Result:
        returncode = 128
        stdout = ""
        stderr = "fatal: bad revision 'v99.99.99'\n"

    monkeypatch.setattr(ranges.subprocess, "run", lambda argv, **kw: Result())

    out = ranges._run_rev_list("v99.99.99")

    assert out == ""
    err = capsys.readouterr().err
    assert "v99.99.99" in err
    assert "bad revision" in err


def test_bundle_commit_at_resolves_the_real_bundle_commit(tmp_path, monkeypatch):
    """Drives the real git runner against a purpose-built repository.

    Hermetic rather than asserting against this checkout's own history: the
    suite runs under `actions/checkout@v4` at depth 1, where rev-list returns
    the graft boundary and any assertion on shape alone passes on a wrong SHA.
    Building the history removes the dependency on the environment entirely.
    """
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True
        ).stdout.strip()

    git("init", "-q", ".")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "test")

    bundle_dir = tmp_path / ranges.BUNDLE_PATH
    bundle_dir.mkdir(parents=True)

    # TWO bundle commits, so that dropping `-1` yields multi-line output and
    # the exact-SHA assertion below catches it. One commit would not.
    (bundle_dir / "app.js").write_text("v1", encoding="utf-8")
    git("add", "-A"); git("commit", "-qm", "bundle 1")

    (bundle_dir / "app.js").write_text("v2", encoding="utf-8")
    git("add", "-A"); git("commit", "-qm", "bundle 2")
    expected = git("rev-parse", "HEAD")

    # A later commit that does not touch the bundle.
    (tmp_path / "readme.md").write_text("x", encoding="utf-8")
    git("add", "-A"); git("commit", "-qm", "unrelated")
    head = git("rev-parse", "HEAD")

    monkeypatch.setattr(ranges, "REPO_ROOT", tmp_path)

    got = ranges.bundle_commit_at("HEAD")

    assert got == expected          # the newest BUNDLE commit, exactly
    assert got != head              # not the tip, not a graft artifact
    assert len(got) == 40           # full sha, which the Task 3 lookup needs


def test_bundle_commit_at_warns_on_a_shallow_clone(capsys):
    got = ranges.bundle_commit_at(
        "HEAD",
        run=lambda ref: "a388985a1111111111111111111111111111aaaa\n",
        shallow=lambda: True,
    )

    assert got == "a388985a1111111111111111111111111111aaaa"   # still returned
    err = capsys.readouterr().err
    assert "shallow" in err
    assert "fetch-depth" in err


def test_bundle_commit_at_is_quiet_on_a_full_clone(capsys):
    ranges.bundle_commit_at(
        "HEAD",
        run=lambda ref: "a388985a1111111111111111111111111111aaaa\n",
        shallow=lambda: False,
    )

    assert "shallow" not in capsys.readouterr().err
