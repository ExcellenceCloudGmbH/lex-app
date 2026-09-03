"""Tests for vendor_frontend — installing the frontend at release time.

lex-app is a pip package, so the frontend travels the same way: published to
PyPI, installed at release time, and copied into lex/react/build so it ships
inside the lex-app wheel exactly as it does today.

The fact this step exists to establish is WHICH version shipped. With `latest`
that is not known in advance and is not recoverable from the git tag, so it is
read back from what pip actually installed and written into the bundle.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import vendor_frontend as vf  # noqa: E402


# ── The spec file ─────────────────────────────────────────────────────

def test_a_missing_spec_file_means_latest(tmp_path):
    # The default the team asked for, and it must not require the file.
    assert vf.read_spec(tmp_path / "nope.txt") == "latest"


def test_an_empty_spec_file_means_latest(tmp_path):
    p = tmp_path / "frontend-version.txt"
    p.write_text("\n\n")
    assert vf.read_spec(p) == "latest"


def test_an_exact_version_is_read(tmp_path):
    p = tmp_path / "frontend-version.txt"
    p.write_text("1.10.0\n")
    assert vf.read_spec(p) == "1.10.0"


def test_comments_and_whitespace_are_ignored(tmp_path):
    p = tmp_path / "frontend-version.txt"
    p.write_text("# which frontend to ship\n\n  1.11.2   # pinned for the audit\n")
    assert vf.read_spec(p) == "1.11.2"


@pytest.mark.parametrize("requested,expected", [
    ("latest", "lex-frontend"),
    ("1.10.0", "lex-frontend==1.10.0"),
])
def test_the_pip_argument_matches_the_request(requested, expected):
    assert vf.pip_spec(requested) == expected


# ── Vendoring ─────────────────────────────────────────────────────────

def _fake_install(target_holder, version="1.10.0", files=("index.html",)):
    """A `run` that materialises a plausible pip --target install."""
    def run(argv, **kwargs):
        target = Path(argv[argv.index("--target") + 1])
        target_holder.append(target)
        (target / f"lex_frontend-{version}.dist-info").mkdir(parents=True)
        build = target / "lex_frontend" / "build"
        (build / "assets").mkdir(parents=True)
        for name in files:
            (build / name).write_text("x")
        (build / "assets" / "index-ABC123.js").write_text("console.log(1)")

        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()
    return run


def test_the_bundle_is_copied_into_the_tree(tmp_path):
    bundle = tmp_path / "lex" / "react" / "build"
    vf.vendor("latest", run=_fake_install([]), bundle_path=bundle)
    assert (bundle / "index.html").is_file()
    assert (bundle / "assets" / "index-ABC123.js").is_file()


def test_the_resolved_version_is_read_back_from_what_pip_installed(tmp_path):
    bundle = tmp_path / "build"
    got = vf.vendor("latest", run=_fake_install([], version="1.12.3"), bundle_path=bundle)
    # With `latest` the version is not known in advance. Reading it back is the
    # whole point of the step.
    assert got == "1.12.3"


def test_the_resolved_version_is_recorded_in_the_bundle(tmp_path):
    bundle = tmp_path / "build"
    vf.vendor("latest", run=_fake_install([], version="1.12.3"), bundle_path=bundle)
    manifest = json.loads((bundle / vf.MANIFEST_NAME).read_text())
    assert manifest["version"] == "1.12.3"
    assert manifest["requested"] == "latest"
    assert manifest["package"] == "lex-frontend"


def test_vendoring_replaces_rather_than_merges(tmp_path):
    bundle = tmp_path / "build"
    bundle.mkdir(parents=True)
    stale = bundle / "assets"
    stale.mkdir()
    (stale / "index-OLD.js").write_text("stale")
    vf.vendor("latest", run=_fake_install([]), bundle_path=bundle)
    # Asset filenames are content hashes. Merging would leave a previous
    # frontend's assets beside the new one, and the shipped bundle would depend
    # on what happened to be in the tree beforehand.
    assert not (bundle / "assets" / "index-OLD.js").exists()


def test_a_failed_install_stops_the_release(tmp_path):
    def run(argv, **kwargs):
        class R:
            returncode = 1
            stdout = ""
            stderr = "ERROR: No matching distribution found for lex-frontend==9.9.9"
        return R()

    with pytest.raises(SystemExit, match="could not install"):
        vf.vendor("9.9.9", run=run, bundle_path=tmp_path / "build")


def test_an_install_with_no_dist_info_stops_the_release(tmp_path):
    def run(argv, **kwargs):
        target = Path(argv[argv.index("--target") + 1])
        (target / "lex_frontend" / "build").mkdir(parents=True)
        class R:
            returncode = 0
            stdout = stderr = ""
        return R()

    with pytest.raises(SystemExit, match="version cannot be established"):
        vf.vendor("latest", run=run, bundle_path=tmp_path / "build")


def test_a_package_with_no_usable_bundle_stops_the_release(tmp_path):
    def run(argv, **kwargs):
        target = Path(argv[argv.index("--target") + 1])
        (target / "lex_frontend-1.10.0.dist-info").mkdir(parents=True)
        (target / "lex_frontend" / "build").mkdir(parents=True)   # no index.html
        class R:
            returncode = 0
            stdout = stderr = ""
        return R()

    # Shipping an empty bundle would 404 every page with nothing in the logs.
    with pytest.raises(SystemExit, match="no usable bundle"):
        vf.vendor("latest", run=run, bundle_path=tmp_path / "build")
