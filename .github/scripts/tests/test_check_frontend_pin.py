"""Tests for the pre-publish guard on the frontend pin.

The pin is both a dependency and a provenance record, so an unpublished
version breaks `pip install lex-app` for every customer AND silently degrades
the frontend half of the release note. The guard has to catch it before the
build, because nothing can be unpublished afterwards.
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import check_frontend_pin as guard  # noqa: E402
from release_notes import ranges  # noqa: E402


# ── Reading the pin ───────────────────────────────────────────────────

def _requirements(tmp_path: Path, text: str) -> Path:
    target = tmp_path / "requirements.txt"
    target.write_text(text, encoding="utf-8")
    return target


def test_reads_a_compatible_release_pin(tmp_path):
    path = _requirements(tmp_path, "django\nlex-app-frontend~=1.12.0\ncelery\n")
    assert guard.read_pin(path) == "1.12.0"


def test_reads_an_exact_pin_too(tmp_path):
    path = _requirements(tmp_path, "lex-app-frontend==1.12.3\n")
    assert guard.read_pin(path) == "1.12.3"


def test_no_pin_reads_as_none(tmp_path):
    assert guard.read_pin(_requirements(tmp_path, "django\ncelery\n")) is None


def test_a_commented_pin_is_not_a_pin(tmp_path):
    assert guard.read_pin(_requirements(tmp_path, "# lex-app-frontend~=1.12.0\n")) is None


def test_a_missing_requirements_file_is_not_a_failure(tmp_path):
    assert guard.read_pin(tmp_path / "absent.txt") is None


# ── The decision table ────────────────────────────────────────────────

def test_no_pin_passes():
    code, message = guard.check(None, None)
    assert code == 0
    assert "nothing to check" in message


def test_a_published_version_passes():
    code, message = guard.check("1.12.0", {"1.11.0", "1.12.0"})
    assert code == 0
    assert "is published" in message


def test_an_unpublished_version_fails_and_names_the_newest():
    code, message = guard.check("1.13.0", {"1.11.0", "1.12.0"})
    assert code == 1
    assert "1.13.0" in message
    assert "newest published: 1.12.0" in message


def test_an_unpublished_version_names_the_tag_the_notes_would_look_for():
    _, message = guard.check("1.13.0", {"1.12.0"})
    assert ranges.pac_tag_for("1.13.0") in message


def test_a_project_that_does_not_exist_fails_with_its_own_message():
    code, message = guard.check("1.12.0", set())
    assert code == 1
    assert "no `lex-app-frontend` project exists" in message


def test_an_unreachable_pypi_does_not_block_a_release():
    """A network blip is not a bad pin, and must not be reported as one."""
    code, message = guard.check("1.12.0", None)
    assert code == 0
    assert "Could not reach PyPI" in message


# ── Talking to PyPI ───────────────────────────────────────────────────

class _Response:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_versions_come_from_the_releases_map():
    got = guard.published_versions(
        "lex-app-frontend",
        opener=lambda url, timeout=None: _Response({"releases": {"1.12.0": [], "1.12.1": []}}),
    )
    assert got == {"1.12.0", "1.12.1"}


def test_a_404_means_the_project_does_not_exist():
    def opener(url, timeout=None):
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    assert guard.published_versions("lex-app-frontend", opener=opener) == set()


def test_any_other_failure_is_unknown_rather_than_empty():
    """set() blocks a release; None does not. They must not be confused."""
    def opener(url, timeout=None):
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", None, None)

    assert guard.published_versions("lex-app-frontend", opener=opener) is None


def test_a_timeout_is_unknown_too():
    def opener(url, timeout=None):
        raise TimeoutError("timed out")

    assert guard.published_versions("lex-app-frontend", opener=opener) is None


def test_malformed_json_is_unknown():
    class _Bad(_Response):
        def read(self):
            return b"not json"

    assert guard.published_versions("x", opener=lambda url, timeout=None: _Bad({})) is None


# ── End to end ────────────────────────────────────────────────────────

def test_main_fails_loudly_on_an_unpublished_pin(tmp_path, monkeypatch, capsys):
    path = _requirements(tmp_path, "lex-app-frontend~=9.9.9\n")
    monkeypatch.setattr(guard, "published_versions", lambda name, **kw: {"1.12.0"})

    assert guard.main(["--requirements", str(path)]) == 1
    assert "::error title=Unpublished frontend pin::" in capsys.readouterr().out


def test_main_passes_a_published_pin(tmp_path, monkeypatch, capsys):
    path = _requirements(tmp_path, "lex-app-frontend~=1.12.0\n")
    monkeypatch.setattr(guard, "published_versions", lambda name, **kw: {"1.12.0"})

    assert guard.main(["--requirements", str(path)]) == 0
    assert "::error" not in capsys.readouterr().out


def test_main_does_not_call_pypi_when_there_is_no_pin(tmp_path, monkeypatch):
    """No pin, no network. The guard must be free on every release until then."""
    called = []
    monkeypatch.setattr(guard, "published_versions",
                        lambda name, **kw: called.append(name) or set())
    path = _requirements(tmp_path, "django\n")

    assert guard.main(["--requirements", str(path)]) == 0
    assert called == []


# ── Ordering the "newest published" hint ──────────────────────────────

def test_the_newest_hint_orders_numerically_not_as_text():
    """1.12.0 is newer than 1.9.0. A string sort says otherwise."""
    _, message = guard.check("9.9.9", {"1.9.0", "1.10.0", "1.12.0"})
    assert "newest published: 1.12.0" in message


def test_a_prerelease_does_not_outrank_the_release_it_precedes():
    _, message = guard.check("9.9.9", {"2.0.0rc221", "2.0.0", "1.9.0"})
    assert "newest published: 2.0.0" in message


@pytest.mark.parametrize(
    "lower, higher",
    [
        ("1.9.0", "1.10.0"),
        ("1.2.9", "1.2.10"),
        ("1.0.0rc1", "1.0.0"),
        ("2.1.11", "2.2.0"),
    ],
)
def test_version_ordering(lower: str, higher: str):
    assert guard._version_key(lower) < guard._version_key(higher)
