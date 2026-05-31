"""Tests for docs_mirror — manifest parsing + read-only guard classification."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make .github/scripts importable when pytest runs from repo root.
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import docs_mirror  # noqa: E402


@pytest.fixture
def manifest() -> dict:
    return {
        "source_repo": "ExcellenceCloudGmbH/lex-app-docs",
        "source_ref": "main",
        "source_root": "content",
        "managed_paths": ["features", "reference", "getting started.md"],
    }


def test_managed_paths_normalises_slashes_and_whitespace():
    m = {"managed_paths": ["features/", " reference ", "/images/"], "source_root": "content"}
    assert docs_mirror.managed_paths(m) == ["features", "reference", "images"]


def test_load_manifest_rejects_missing_managed_paths(tmp_path: Path):
    bad = tmp_path / ".docs-sync.yml"
    bad.write_text("source_root: content\n")
    with pytest.raises(ValueError, match="managed_paths"):
        docs_mirror.load_manifest(bad)


def test_load_manifest_rejects_missing_source_root(tmp_path: Path):
    bad = tmp_path / ".docs-sync.yml"
    bad.write_text("managed_paths:\n  - features\n")
    with pytest.raises(ValueError, match="source_root"):
        docs_mirror.load_manifest(bad)


@pytest.mark.parametrize(
    "rel,expected",
    [
        ("features", True),                       # exact dir match
        ("features/tracking/history.md", True),   # nested under managed dir
        ("getting started.md", True),             # exact file match
        ("reference/api.md", True),
        ("ci-cd/overview.md", False),             # internal-only dir
        ("featuresX/foo.md", False),              # prefix must be a path boundary
        ("testing-methodology.md", False),        # internal-only root file
    ],
)
def test_is_under_managed(manifest, rel, expected):
    managed = docs_mirror.managed_paths(manifest)
    assert docs_mirror._is_under_managed(rel, managed) is expected


def test_check_readonly_flags_mirror_edit(manifest, monkeypatch, capsys):
    monkeypatch.setattr(docs_mirror, "load_manifest", lambda *a, **k: manifest)
    args = type("A", (), {"files": ["docs/features/tracking/history.md", "lex/core/x.py"]})()
    assert docs_mirror.cmd_check_readonly(args) == 1
    assert "MIRRORED" in capsys.readouterr().err


def test_check_readonly_allows_internal_and_source_edits(manifest, monkeypatch):
    monkeypatch.setattr(docs_mirror, "load_manifest", lambda *a, **k: manifest)
    args = type("A", (), {"files": ["docs/ci-cd/overview.md", "lex/core/x.py", "README.md"]})()
    assert docs_mirror.cmd_check_readonly(args) == 0
