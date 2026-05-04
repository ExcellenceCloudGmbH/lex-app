"""Verify ``view_init_kwargs`` is forwarded into ``as_view``."""
from __future__ import annotations

from unittest import mock

from lex.mcp_server import dispatch
from lex.mcp_server.context import McpPrincipal


class _SentinelView:
    """Test double that records the kwargs ``as_view`` receives."""

    last_init_kwargs: dict | None = None

    @classmethod
    def as_view(cls, **kwargs):
        cls.last_init_kwargs = kwargs

        class _Response:
            status_code = 204
            data = None

        def _view(request, **view_kwargs):  # noqa: ARG001
            return _Response()

        return _view


def test_view_init_kwargs_forwarded_to_as_view():
    principal = McpPrincipal(user=mock.Mock(is_authenticated=True), auth_kind="api_key")

    with mock.patch.object(dispatch, "RequestFactory") as factory_cls:
        factory = factory_cls.return_value
        factory.get.return_value = mock.Mock(META={})
        # Avoid calling into simple_history when the test runs without Django apps.
        with mock.patch("simple_history.models.HistoricalRecords"):
            status, payload = dispatch._call_view_sync(
                _SentinelView,
                principal=principal,
                method="GET",
                view_kwargs={},
                view_init_kwargs={"model_collection": "<sentinel>"},
            )

    assert status == 204
    assert payload is None
    assert _SentinelView.last_init_kwargs == {"model_collection": "<sentinel>"}


def test_view_init_kwargs_default_is_empty():
    principal = McpPrincipal(user=mock.Mock(is_authenticated=True), auth_kind="api_key")
    _SentinelView.last_init_kwargs = None

    with mock.patch.object(dispatch, "RequestFactory") as factory_cls:
        factory = factory_cls.return_value
        factory.get.return_value = mock.Mock(META={})
        with mock.patch("simple_history.models.HistoricalRecords"):
            dispatch._call_view_sync(
                _SentinelView,
                principal=principal,
                method="GET",
                view_kwargs={},
            )

    assert _SentinelView.last_init_kwargs == {}
