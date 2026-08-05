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
