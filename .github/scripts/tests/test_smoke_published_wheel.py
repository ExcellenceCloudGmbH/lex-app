"""Tests for smoke_published_wheel — checking what we just published.

The pipeline used to publish and walk away, so a release that forgot its
frontend, or pinned a version that does not exist, would have been found by a
customer. These pin the behaviour worth getting right: indexing lag is waited
out, a resolution failure is not, and a missing or empty frontend fails loudly.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "smoke_published_wheel.py"
spec = importlib.util.spec_from_file_location("smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
sys.modules["smoke"] = smoke
spec.loader.exec_module(smoke)


def _run(results):
    """A `run` returning (returncode, stderr) pairs in order."""
    seq = list(results)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        code, err = seq.pop(0)
        class R:
            returncode = code
            stdout = ""
            stderr = err
        return R()

    return run, calls


def test_a_version_that_appears_on_the_second_try_is_accepted(tmp_path):
    run, calls = _run([(1, "No matching distribution found for lex-app"), (0, "")])
    slept = []
    smoke.install("2.3.0", tmp_path, run=run, sleep=slept.append, delay=0.1)
    assert len(calls) == 2 and slept == [0.1]


def test_a_resolution_failure_is_not_waited_out(tmp_path):
    # A pin naming a version that does not exist will not fix itself. Waiting
    # two minutes to report a bad release helps nobody.
    run, calls = _run([(1, "ERROR: ResolutionImpossible: lex-app-frontend~=9.9.9")])
    with pytest.raises(SystemExit, match="cannot be resolved"):
        smoke.install("2.3.0", tmp_path, run=run, sleep=lambda _: None)
    assert len(calls) == 1, "should not have retried"


def test_a_version_that_never_appears_fails(tmp_path):
    run, calls = _run([(1, "No matching distribution found for lex-app")] * 3)
    with pytest.raises(SystemExit, match="not installable after 3 attempts"):
        smoke.install("2.3.0", tmp_path, attempts=3, run=run, sleep=lambda _: None)
    assert len(calls) == 3


def test_dependencies_are_installed_not_skipped(tmp_path):
    # --no-deps would defeat the entire point: the frontend IS a dependency.
    run, calls = _run([(0, "")])
    smoke.install("2.3.0", tmp_path, run=run, sleep=lambda _: None)
    assert "--no-deps" not in calls[0]
    assert "lex-app==2.3.0" in calls[0]


def _installed(tmp_path, *, version="1.12.0", bundle=True, index=True):
    (tmp_path / f"lex_app_frontend-{version}.dist-info").mkdir(parents=True)
    if bundle:
        build = tmp_path / "lex_app_frontend" / "build"
        build.mkdir(parents=True)
        if index:
            (build / "index.html").write_text("<!doctype html>")
    return tmp_path


def test_the_installed_frontend_version_is_reported(tmp_path):
    assert smoke.check_frontend(_installed(tmp_path, version="1.12.3")) == "1.12.3"


def test_an_install_without_the_frontend_fails(tmp_path):
    # The realistic cause: the pin dropped out of requirements.txt, which is
    # what pyproject reads as the dependency list.
    with pytest.raises(SystemExit, match="installed without"):
        smoke.check_frontend(tmp_path)


def test_a_frontend_with_no_usable_bundle_fails(tmp_path):
    with pytest.raises(SystemExit, match="no usable bundle"):
        smoke.check_frontend(_installed(tmp_path, index=False))
