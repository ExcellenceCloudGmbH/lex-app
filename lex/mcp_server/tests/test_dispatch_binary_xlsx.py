"""xlsx MIME (application/vnd.openxmlformats-...spreadsheetml.sheet) is binary, not text."""
from __future__ import annotations

from io import BytesIO

from django.http import HttpResponse

from lex.mcp_server import dispatch


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_xlsx_mime_is_treated_as_binary():
    assert dispatch._is_textual_mime(XLSX_MIME) is False


def test_decode_response_treats_xlsx_as_binary():
    raw = b"PK\x03\x04 fake xlsx body"
    response = HttpResponse(raw, content_type=XLSX_MIME)
    response["Content-Disposition"] = 'attachment; filename="export.xlsx"'

    decoded = dispatch._decode_response(response)

    assert decoded["_binary"] is True
    assert decoded["content_bytes"] == raw
    assert decoded["content_type"] == XLSX_MIME
    assert decoded["filename"] == "export.xlsx"


def test_application_json_remains_textual():
    assert dispatch._is_textual_mime("application/json") is True
    assert dispatch._is_textual_mime("application/vnd.api+json") is True
    assert dispatch._is_textual_mime("text/plain; charset=utf-8") is True
