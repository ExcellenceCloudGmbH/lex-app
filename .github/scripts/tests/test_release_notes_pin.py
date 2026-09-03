"""Tests for the frontend version spec — what a release says it ships.

`frontend-version.txt` holds an exact version or `latest`. An exact version is
the provenance record: it names one published artifact and is readable from the
tag. `latest` is not — it resolved to whatever was newest on build day, which
the tag does not record — so it must report as a gap rather than a guess.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import ranges  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("line,ok", [
    ("1.10.0", True),
    ("1.10.0rc1", True),
    ("latest", False),
    (">=1.10.0", False),
    ("1.10.*", False),
    ("lex-frontend==1.10.0", False),
], ids=["exact", "exact-rc", "latest", "range", "wildcard", "requirement-line"])
def test_only_an_exact_version_counts_as_provenance(line, ok):
    assert bool(ranges.VERSION_RE.fullmatch(line)) is ok


def test_an_exact_version_is_read_from_the_spec_at_a_ref():
    def show(ref, path):
        assert path == "frontend-version.txt", f"read the wrong file: {path}"
        return "1.10.0\n"

    assert ranges.frontend_version_at("v2.2.0", show=show) == "1.10.0"


def test_latest_is_not_provenance():
    # It resolved to whatever was newest on build day. Nothing in the tag
    # records that, so it is a gap — not a guess at the newest version now.
    show = lambda ref, path: "latest\n"
    assert ranges.frontend_version_at("v2.2.0", show=show) is None


def test_a_missing_spec_file_returns_none():
    # Every tag cut before this mechanism. Routes to the side-car path.
    assert ranges.frontend_version_at("v2.1.4", show=lambda r, p: None) is None


def test_comments_and_whitespace_are_ignored():
    show = lambda ref, path: "# which frontend to ship\n\n  1.11.2  # pinned\n"
    assert ranges.frontend_version_at("v2.2.0", show=show) == "1.11.2"


def test_junk_in_the_spec_is_not_read_as_a_version():
    show = lambda ref, path: "whatever-is-newest\n"
    assert ranges.frontend_version_at("v2.2.0", show=show) is None


def test_the_version_becomes_a_pac_tag():
    assert ranges.pac_tag_for("1.10.0") == "v1.10.0"


def test_the_committed_spec_file_is_readable_and_valid():
    """Whatever is committed must be `latest` or an exact version.

    Anything else silently disables frontend notes for every release cut from
    it, which is the kind of failure nobody looks for.
    """
    spec = REPO_ROOT / "frontend-version.txt"
    assert spec.is_file(), "frontend-version.txt must exist — it drives the release"
    value = next(
        (ln.split("#", 1)[0].strip() for ln in spec.read_text().splitlines()
         if ln.split("#", 1)[0].strip()),
        "",
    )
    assert value == ranges.LATEST or ranges.VERSION_RE.fullmatch(value), (
        f"{value!r} is neither 'latest' nor an exact version"
    )
