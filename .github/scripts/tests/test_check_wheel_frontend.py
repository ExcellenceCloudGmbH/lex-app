"""Tests for the pre-publish guard that a wheel delivers a frontend.

A lex-app wheel serves the SPA either by carrying the compiled bundle or by
depending on `lex-app-frontend`. Exactly one is enough; neither is a release
where every page 404s.

That combination is reachable by a single innocuous commit — dropping the
bundle from MANIFEST.in before the pin exists — and nothing else catches it
until a customer installs the release, by which point it cannot be unpublished.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import check_wheel_frontend as guard  # noqa: E402


def _wheel(tmp_path: Path, *, bundle: bool, requires: list[str] | None = None,
           name: str = "lex_app-9.9.9-py3-none-any.whl") -> Path:
    """A minimal wheel with just the two properties under test."""
    path = tmp_path / name
    metadata = ["Metadata-Version: 2.1", "Name: lex-app", "Version: 9.9.9"]
    metadata += [f"Requires-Dist: {r}" for r in (requires or [])]
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("lex_app-9.9.9.dist-info/METADATA", "\n".join(metadata))
        archive.writestr("lex/lex_app/settings.py", "# ...")
        if bundle:
            archive.writestr(guard.BUNDLE_ENTRY, "<!doctype html>")
            archive.writestr("lex/react/build/assets/main.js", "//")
    return path


# ── Name normalisation ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "spelling",
    ["lex-app-frontend", "lex_app_frontend", "LEX-App-Frontend", "lex.app.frontend"],
)
def test_every_spelling_pypi_treats_as_one_package_is_recognised(spelling: str):
    assert guard.normalise(spelling) == guard.normalise(guard.FRONTEND_DIST)


def test_a_different_package_is_not_confused_with_it():
    assert guard.normalise("lex-app-frontend-tools") != guard.normalise(guard.FRONTEND_DIST)


# ── The decision table ────────────────────────────────────────────────

def test_a_wheel_with_neither_is_refused():
    code, message = guard.check(has_bundle=False, requires=["django", "celery"])
    assert code == 1
    assert "no frontend" in message


def test_the_refusal_names_both_ways_out():
    _, message = guard.check(has_bundle=False, requires=[])
    assert guard.REQUIREMENTS in message
    assert "MANIFEST.in" in message


def test_the_dependency_alone_is_enough():
    code, _ = guard.check(has_bundle=False, requires=["django", "lex-app-frontend"])
    assert code == 0


def test_the_bundle_alone_is_enough():
    """Today's state, and it must keep passing until the pin lands."""
    code, _ = guard.check(has_bundle=True, requires=["django"])
    assert code == 0


def test_both_passes_but_says_the_bundle_is_now_redundant():
    code, message = guard.check(has_bundle=True, requires=["lex-app-frontend"])
    assert code == 0
    assert "dead weight" in message


def test_the_underscore_spelling_counts_as_the_dependency():
    code, _ = guard.check(has_bundle=False, requires=["lex_app_frontend"])
    assert code == 0


# ── Reading a real zip ────────────────────────────────────────────────

def test_inspect_finds_the_bundle_and_the_requirements(tmp_path):
    wheel = _wheel(tmp_path, bundle=True, requires=["django>=5", "celery"])
    has_bundle, requires = guard.inspect(wheel)
    assert has_bundle is True
    assert requires == ["django", "celery"]


def test_inspect_strips_version_specifiers_from_the_name(tmp_path):
    wheel = _wheel(tmp_path, bundle=False, requires=["lex-app-frontend~=1.12.0"])
    _, requires = guard.inspect(wheel)
    assert requires == ["lex-app-frontend"]


def test_inspect_keeps_a_dependency_that_carries_an_environment_marker(tmp_path):
    wheel = _wheel(tmp_path, bundle=False,
                   requires=['lex-app-frontend~=1.12.0; python_version >= "3.12"'])
    _, requires = guard.inspect(wheel)
    assert requires == ["lex-app-frontend"]


def test_assets_without_an_index_are_not_a_bundle(tmp_path):
    """A directory of chunks with no entry point serves nothing."""
    path = tmp_path / "lex_app-9.9.9-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("lex_app-9.9.9.dist-info/METADATA", "Name: lex-app")
        archive.writestr("lex/react/build/assets/main.js", "//")
    has_bundle, _ = guard.inspect(path)
    assert has_bundle is False


# ── Locating the wheel ────────────────────────────────────────────────

def test_one_wheel_is_found(tmp_path):
    wheel = _wheel(tmp_path, bundle=True)
    assert guard.find_wheel(tmp_path) == wheel


def test_an_empty_dist_yields_nothing(tmp_path):
    assert guard.find_wheel(tmp_path) is None


def test_two_wheels_yield_nothing_rather_than_a_guess(tmp_path):
    """Publishing uploads all of them; attesting to one would be a lie."""
    _wheel(tmp_path, bundle=True, name="lex_app-9.9.9-py3-none-any.whl")
    _wheel(tmp_path, bundle=False, name="lex_app-9.9.8-py3-none-any.whl")
    assert guard.find_wheel(tmp_path) is None


def test_a_missing_directory_yields_nothing(tmp_path):
    assert guard.find_wheel(tmp_path / "absent") is None


# ── End to end ────────────────────────────────────────────────────────

def test_main_refuses_a_frontendless_wheel(tmp_path, capsys):
    _wheel(tmp_path, bundle=False, requires=["django"])
    assert guard.main(["--dist", str(tmp_path)]) == 1
    assert "::error title=Wheel ships no frontend::" in capsys.readouterr().out


def test_main_accepts_the_intended_end_state(tmp_path, capsys):
    """No bundle, pin declared — what this change is for."""
    _wheel(tmp_path, bundle=False, requires=["django", "lex-app-frontend~=1.12.0"])
    assert guard.main(["--dist", str(tmp_path)]) == 0
    assert "::error" not in capsys.readouterr().out


def test_main_fails_when_the_build_step_produced_nothing(tmp_path, capsys):
    assert guard.main(["--dist", str(tmp_path)]) == 1
    assert "::error title=No wheel to check::" in capsys.readouterr().out


# ── Drift ─────────────────────────────────────────────────────────────

def test_the_package_name_agrees_with_the_release_notes_definition():
    """`release_notes.ranges` reads the same name out of requirements.txt.

    Declared twice on purpose — see the comment in check_wheel_frontend.py —
    so this is what stops the two copies drifting apart.
    """
    ranges = pytest.importorskip("release_notes.ranges")
    pin_name = getattr(ranges, "PIN_NAME", None)
    if pin_name is None:
        pytest.skip("ranges.PIN_NAME lands with the frontend-range PR")
    assert guard.normalise(pin_name) == guard.normalise(guard.FRONTEND_DIST)
    assert ranges.REQUIREMENTS_PATH == guard.REQUIREMENTS


# ── The same invariant, checked on the source at PR time ──────────────
#
# The wheel guard above runs at publish. This one runs on every pull request
# that touches the four files involved, so the window where the repository is
# one merge away from a frontendless release never opens.

REPO_ROOT = Path(__file__).resolve().parents[3]


def _excludes_bundle(manifest: str) -> bool:
    """True when MANIFEST.in keeps lex/react/build out of the distribution."""
    for line in manifest.splitlines():
        line = line.split("#", 1)[0].strip()
        if line.startswith("recursive-exclude lex/react/build"):
            return True
    return False


def _declares_pin(requirements: str) -> bool:
    for line in requirements.splitlines():
        line = line.split("#", 1)[0].strip()
        if guard.normalise(line[: len(guard.FRONTEND_DIST)]) == guard.normalise(
            guard.FRONTEND_DIST
        ) and len(line) > len(guard.FRONTEND_DIST):
            return True
    return False


@pytest.mark.parametrize(
    "manifest, requirements, expected",
    [
        ("recursive-include lex/react *\n", "django\n", False),
        ("recursive-exclude lex/react/build *\n", "django\n", True),
        ("# recursive-exclude lex/react/build *\n", "django\n", False),
    ],
)
def test_the_manifest_reader_sees_only_a_live_exclude(manifest, requirements, expected):
    assert _excludes_bundle(manifest) is expected


@pytest.mark.parametrize(
    "line, expected",
    [
        ("lex-app-frontend~=1.12.0", True),
        ("lex_app_frontend==1.12.0", True),
        ("# lex-app-frontend~=1.12.0", False),
        ("lex-app-frontend", False),          # a name with no constraint is not a pin
        ("django", False),
    ],
)
def test_the_requirements_reader_sees_only_a_real_pin(line, expected):
    assert _declares_pin(line + "\n") is expected


def test_this_repository_will_not_publish_a_frontendless_wheel():
    """If the bundle is excluded from the distribution, the pin must be present.

    This test is EXPECTED TO FAIL while the bundle has been dropped and
    `lex-app-frontend` is not yet pinned — that combination is a release where
    every page 404s. It is the ordering constraint made mechanical: the change
    that drops the bundle cannot merge until the change that adds the pin has.

    Delete neither half. Fix it by adding the pin, which is possible as soon as
    lex-app-frontend has been published to PyPI once.
    """
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    requirements = (REPO_ROOT / guard.REQUIREMENTS).read_text(encoding="utf-8")

    if not _excludes_bundle(manifest):
        pytest.skip("the bundle still ships in the wheel; the pin is not required yet")

    assert _declares_pin(requirements), (
        f"MANIFEST.in excludes lex/react/build, so the wheel carries no frontend, "
        f"and {guard.REQUIREMENTS} declares no `{guard.FRONTEND_DIST}` pin to supply "
        "one. A release built from this tree would 404 on every page. Add the pin, "
        "or put the bundle back."
    )
