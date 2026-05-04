"""Verify the dispatcher detects binary HTTP responses (PDF FileResponse)."""
from __future__ import annotations

from io import BytesIO

from django.http import FileResponse

from lex.mcp_server import dispatch


def test_decode_response_returns_binary_envelope_for_pdf():
    body = b"%PDF-1.4 hello world"
    bio = BytesIO(body)
    response = FileResponse(
        bio,
        content_type="application/pdf",
    )
    response["Content-Disposition"] = 'attachment; filename="report.pdf"'

    decoded = dispatch._decode_response(response)

    assert isinstance(decoded, dict)
    assert decoded.get("_binary") is True
    assert decoded["content_bytes"] == body
    assert decoded["content_type"].startswith("application/pdf")
    assert decoded["filename"] == "report.pdf"
    # FileResponse should have been closed (its underlying BytesIO is closed).
    assert bio.closed


def test_decode_response_handles_rfc5987_filename_star():
    body = b"binary"
    response = FileResponse(BytesIO(body), content_type="application/octet-stream")
    response["Content-Disposition"] = (
        "attachment; filename=\"fallback.bin\"; filename*=UTF-8''na%C3%AFve.bin"
    )

    decoded = dispatch._decode_response(response)

    assert decoded["_binary"] is True
    assert decoded["filename"] == "naïve.bin"
