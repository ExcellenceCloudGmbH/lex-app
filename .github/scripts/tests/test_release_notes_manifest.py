"""Tests for release_notes.manifest — provenance-file validation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from release_notes import manifest  # noqa: E402

VALID_SHA = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"


def test_a_well_formed_manifest_is_valid():
    blob = (
        '{"repo": "ExcellenceCloudGmbH/process-admin-general-client",'
        ' "branch": "lex-app-v2-pac-latest",'
        f' "sha": "{VALID_SHA}",'
        ' "built_at": "2026-09-01T10:00:00Z"}'
    )
    assert manifest.validate(blob) is None


def test_absent_manifest_is_reported():
    assert "absent" in manifest.validate(None)


@pytest.mark.parametrize("blob", ["{not json", "", "   "])
def test_unparseable_manifest_is_reported(blob):
    reason = manifest.validate(blob)
    assert reason is not None and "JSON" in reason


@pytest.mark.parametrize("blob", ["[1, 2]", '"a string"', "42"])
def test_non_object_manifest_is_reported(blob):
    assert "object" in manifest.validate(blob)


@pytest.mark.parametrize("blob", ['{}', '{"sha": ""}', '{"sha": null}', '{"sha": 7}'])
def test_missing_or_empty_sha_is_reported(blob):
    assert "sha" in manifest.validate(blob)


@pytest.mark.parametrize(
    "sha",
    [
        "a388985a",                                    # abbreviated
        "A1B2C3D4E5F6708192A3B4C5D6E7F8091A2B3C4D",    # uppercase
        "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",    # not hex
        "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4",     # 39 chars
    ],
)
def test_sha_must_be_forty_lowercase_hex(sha):
    reason = manifest.validate('{"sha": "%s"}' % sha)
    assert reason is not None and "40" in reason
