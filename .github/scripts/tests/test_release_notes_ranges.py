"""Tests for release_notes.ranges — tag classification and range resolution."""

from __future__ import annotations

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
