"""Tests for the frontend pin — the version a release declared.

requirements.txt carries `lex-app-frontend~=X.Y.Z`. That one line is both the
dependency pip installs and the provenance record: plain text, readable from
any tag, visible in the pull request that changed it.

The pin is NOT in requirements.txt yet, deliberately. It is a real dependency,
so adding it before the package is published breaks every `pip install
lex-app`. The invariant test below passes today and enforces the shape the
moment the line lands.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import ranges  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("line,expected", [
    ("lex-app-frontend~=1.12.0", "1.12.0"),
    ("lex-app-frontend==1.12.0", "1.12.0"),
    ("lex-app-frontend ~= 1.12.0", "1.12.0"),
    ("lex-app-frontend~=1.12.0rc1", "1.12.0rc1"),
    ("lex-app-frontend>=1.12.0", None),
    ("lex-app-frontend", None),
    ("lex-app-frontend~=1.12.*", None),
    ("lex-frontend~=1.12.0", None),
], ids=["compatible", "exact", "spaced", "rc", "gte", "bare", "wildcard", "old-name"])
def test_only_a_pinned_version_counts_as_provenance(line, expected):
    match = ranges.PIN_RE.fullmatch(line)
    assert (match["version"] if match else None) == expected


def test_the_version_is_read_from_requirements_at_a_ref():
    def show(ref, path):
        assert path == "requirements.txt", f"read the wrong file: {path}"
        return "django==5.0\nlex-app-frontend~=1.12.0\ncelery==5.3\n"

    assert ranges.frontend_version_at("v2.3.0", show=show) == "1.12.0"


def test_a_tag_with_no_pin_returns_none():
    # Every tag before the pin existed. Routes to the side-car path instead.
    show = lambda ref, path: "django==5.0\ncelery==5.3\n"
    assert ranges.frontend_version_at("v2.2.0", show=show) is None


def test_a_missing_requirements_file_returns_none():
    assert ranges.frontend_version_at("v1.0.0", show=lambda r, p: None) is None


def test_a_commented_out_pin_is_ignored():
    # A commented pin is not a dependency, and reading it would attribute a
    # release to a frontend it does not install.
    show = lambda ref, path: "# lex-app-frontend~=9.9.9\nlex-app-frontend~=1.12.0\n"
    assert ranges.frontend_version_at("v2.3.0", show=show) == "1.12.0"


def test_the_version_becomes_a_frontend_tag():
    assert ranges.pac_tag_for("1.12.0") == "v1.12.0"


def test_any_committed_pin_must_be_a_shape_we_can_read():
    """Passes while there is no pin; enforces the shape once one lands.

    Written as an invariant rather than asserting the pin exists, because it
    cannot be added until the package is published.
    """
    text = (REPO_ROOT / "requirements.txt").read_text()
    lines = [
        ln.split("#", 1)[0].strip() for ln in text.splitlines()
        if ln.split("#", 1)[0].strip().startswith("lex-app-frontend")
    ]
    assert len(lines) <= 1, f"more than one lex-app-frontend line: {lines}"
    for line in lines:
        assert ranges.PIN_RE.fullmatch(line), (
            f"{line!r} cannot be read as a version — the release note would "
            "silently report a frontend gap for every release cut from it"
        )
