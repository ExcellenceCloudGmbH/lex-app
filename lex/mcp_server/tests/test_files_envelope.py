"""Verify ``binary_envelope`` and ``clamp_max_bytes`` from the shared helpers."""
from __future__ import annotations

import base64

from lex.mcp_server.tools._common import binary_envelope, clamp_max_bytes


def test_clamp_max_bytes_defaults_to_ceiling_when_unspecified():
    assert clamp_max_bytes(None, 5_000) == 5_000
    assert clamp_max_bytes(0, 5_000) == 5_000
    assert clamp_max_bytes(-1, 5_000) == 5_000


def test_clamp_max_bytes_caps_at_ceiling():
    assert clamp_max_bytes(10_000, 5_000) == 5_000
    assert clamp_max_bytes(2_000, 5_000) == 2_000


def test_binary_envelope_under_cap_returns_base64():
    payload = {
        "_binary": True,
        "content_bytes": b"hello world",
        "content_type": "application/pdf",
        "filename": "x.pdf",
        "status_code": 200,
    }

    env = binary_envelope(payload, max_bytes=1024)

    assert env["status"] == 200
    file = env["result"]["file"]
    assert file["name"] == "x.pdf"
    assert file["content_type"] == "application/pdf"
    assert file["size_bytes"] == len(b"hello world")
    assert file["encoding"] == "base64"
    assert base64.b64decode(file["base64"]) == b"hello world"


def test_binary_envelope_over_cap_returns_413():
    payload = {
        "_binary": True,
        "content_bytes": b"x" * 100,
        "content_type": "application/pdf",
        "filename": "big.pdf",
        "status_code": 200,
    }

    env = binary_envelope(payload, max_bytes=10)

    assert env["status"] == 413
    result = env["result"]
    assert result["error"] == "file_too_large"
    assert result["size_bytes"] == 100
    assert result["max_bytes"] == 10
    assert result["name"] == "big.pdf"
    assert "base64" not in result


def test_binary_envelope_passthrough_for_json_payload():
    env = binary_envelope({"download_url": "https://example/x"}, max_bytes=1024)

    assert env["status"] == 200
    assert env["result"] == {"download_url": "https://example/x"}


def test_binary_envelope_uses_default_filename_when_missing():
    payload = {
        "_binary": True,
        "content_bytes": b"abc",
        "content_type": "application/pdf",
        "filename": None,
        "status_code": 200,
    }

    env = binary_envelope(payload, max_bytes=1024, default_filename="fallback.pdf")

    assert env["result"]["file"]["name"] == "fallback.pdf"
