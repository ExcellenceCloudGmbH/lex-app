"""Phase 9 settings defaults are exposed via mcp_setting()."""
from __future__ import annotations

import django
from django.conf import settings


def _ensure_django():
    if not settings.configured:
        settings.configure(
            DEBUG=True,
            INSTALLED_APPS=[],
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            USE_TZ=True,
        )
        django.setup()


_ensure_django()


def test_phase9_defaults_present():
    from lex.mcp_server.config import mcp_setting

    assert mcp_setting("RATE_LIMIT_ENABLED") is True
    assert isinstance(mcp_setting("RATE_LIMIT_PER_MINUTE"), int)
    assert isinstance(mcp_setting("RATE_LIMIT_BURST"), int)
    assert mcp_setting("RATE_LIMIT_CACHE")
    assert mcp_setting("RATE_LIMIT_NAMESPACE")
    assert mcp_setting("OBSERVABILITY_ENABLED") is True
