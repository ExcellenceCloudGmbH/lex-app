"""ToolCallSpan + record_auth emit log records and Sentry tags safely."""
from __future__ import annotations

import logging
from unittest import mock

import django
import pytest
from django.conf import settings


def _ensure_django():
    if not settings.configured:
        settings.configure(
            DEBUG=True,
            INSTALLED_APPS=[],
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            USE_TZ=True,
            MCP_SERVER={"OBSERVABILITY_ENABLED": True},
        )
        django.setup()


_ensure_django()


@pytest.fixture
def captured_logs(caplog):
    caplog.set_level(logging.INFO, logger="lex.mcp_server")
    return caplog


def test_tool_call_span_emits_call_and_done(captured_logs):
    from lex.mcp_server.observability import ToolCallSpan

    with ToolCallSpan("ListView", principal=None):
        pass

    msgs = [r.message for r in captured_logs.records]
    assert "mcp.tool.call" in msgs
    assert "mcp.tool.done" in msgs
    done = [r for r in captured_logs.records if r.message == "mcp.tool.done"][0]
    assert getattr(done, "status") == "ok"
    assert isinstance(getattr(done, "duration_ms"), int)


def test_tool_call_span_records_error_and_propagates(captured_logs):
    from lex.mcp_server.observability import ToolCallSpan

    with pytest.raises(RuntimeError):
        with ToolCallSpan("ListView", principal=None):
            raise RuntimeError("boom")

    err_records = [r for r in captured_logs.records if r.message == "mcp.tool.done"]
    assert err_records
    assert getattr(err_records[0], "status") == "error"
    assert getattr(err_records[0], "error_type") == "RuntimeError"


def test_tool_call_span_sets_sentry_tags():
    fake_sentry = mock.MagicMock()
    with mock.patch("lex.mcp_server.observability._sentry", fake_sentry):
        from lex.mcp_server.observability import ToolCallSpan

        with ToolCallSpan("ListView", principal=None):
            pass

    tag_calls = [c.args[0] for c in fake_sentry.set_tag.call_args_list]
    assert "mcp.tool" in tag_calls


def test_record_auth_logs_decision(captured_logs):
    from lex.mcp_server.observability import record_auth

    record_auth("ok", auth_kind="api_key", principal_id="alice")
    record_auth("denied", auth_kind="oidc_bearer", reason="invalid")

    messages = [r.message for r in captured_logs.records]
    assert "mcp.auth.ok" in messages
    assert "mcp.auth.denied" in messages


def test_works_without_sentry_installed():
    """If sentry_sdk is not importable, helpers must not raise."""
    with mock.patch("lex.mcp_server.observability._sentry", None):
        from lex.mcp_server.observability import ToolCallSpan, record_auth

        with ToolCallSpan("X"):
            pass
        record_auth("ok", auth_kind="api_key")
