"""Tests for the frontend version pin — the provenance record after the switch.

`frontend_version_at` parses a pin from requirements.txt at two tags to derive
a PAC tag range. A loose specifier cannot identify one revision, so it is not
provenance and must not be read as one.

The pin is NOT in requirements.txt yet, deliberately: it makes lex-frontend a
hard dependency, and adding it before the package is published would break
every `pip install lex-app`. The invariant test below is written so it passes
today and enforces correctness the moment the line lands.
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
    ("lex-frontend==1.10.0", True),
    ("lex-frontend==1.10.0rc1", True),
    ("lex-frontend>=1.10.0", False),
    ("lex-frontend~=1.10.0", False),
    ("lex-frontend", False),
    ("lex-frontend==1.10.*", False),
], ids=["exact", "exact-rc", "gte", "compatible", "bare", "wildcard"])
def test_only_an_exact_pin_is_accepted(line, ok):
    assert bool(ranges.PIN_RE.fullmatch(line)) is ok


def test_any_lex_frontend_line_in_requirements_must_be_an_exact_pin():
    """Passes while there is no pin; enforces the shape once one lands.

    Written as an invariant rather than an assertion that the pin exists,
    because the pin cannot be added until the package is published.
    """
    text = (REPO_ROOT / "requirements.txt").read_text()
    lines = [
        ln.strip() for ln in text.splitlines()
        if ln.strip().startswith("lex-frontend")
    ]
    assert len(lines) <= 1, f"more than one lex-frontend line: {lines}"
    for line in lines:
        assert ranges.PIN_RE.fullmatch(line), (
            f"{line!r} is not an exact pin — a range cannot identify one revision"
        )


def test_the_pinned_version_is_read_from_requirements_at_a_ref():
    def show(ref, path):
        assert path == "requirements.txt", f"read the wrong file: {path}"
        return "django==5.0\nlex-frontend==1.10.0\ncelery==5.3\n"

    assert ranges.frontend_version_at("v2.2.0", show=show) == "1.10.0"


def test_a_tag_with_no_pin_returns_none():
    # Every tag before the switch has no pin. That is not an error — it is what
    # routes resolution to the side-car path instead.
    show = lambda ref, path: "django==5.0\ncelery==5.3\n"
    assert ranges.frontend_version_at("v2.1.4", show=show) is None


def test_a_missing_requirements_file_returns_none():
    assert ranges.frontend_version_at("v1.0.0", show=lambda r, p: None) is None


def test_a_loose_specifier_is_not_treated_as_provenance():
    show = lambda ref, path: "lex-frontend>=1.10.0\n"
    assert ranges.frontend_version_at("v2.2.0", show=show) is None


def test_a_commented_out_pin_is_ignored():
    # A commented pin is not a dependency, and reading it would attribute a
    # release to a frontend it does not ship.
    show = lambda ref, path: "# lex-frontend==9.9.9\nlex-frontend==1.10.0\n"
    assert ranges.frontend_version_at("v2.2.0", show=show) == "1.10.0"


def test_the_pinned_version_becomes_a_pac_tag():
    assert ranges.pac_tag_for("1.10.0") == "v1.10.0"
