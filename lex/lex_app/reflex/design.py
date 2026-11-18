"""RFDS, configured for lex-app: the look and the platform of the Reflex dashboards.

RFDS (``rfds`` on PyPI, the Reflex Finance Design System) supplies tokens,
components, patterns and page templates. lex-app keeps the runtime -- sign-in,
data, permissions, the app object, routing, embedding -- and this module is
where the two meet, so a project's ``rxconfig.py`` and dashboards configure
neither by hand:

========================  ====================================================
:func:`rfds_plugin`       ``RFDSPlugin`` with lex-app's brand, locale, currency
                          and time zone; listed by ``lex_config()``
:func:`theme_plugin`      ``RadixThemesPlugin`` with RFDS's root theme
:func:`lex_security_headers`  RFDS's security headers, allowing exactly lex-app
                          to frame the dashboards
:func:`lex_app_origins`   the origins lex-app's own pages are served from
:func:`lex_head_components`  lex-app's overrides of RFDS's chrome
========================  ====================================================

Everything here must stay importable before Django is set up and before any
Reflex state exists, like :mod:`lex.lex_app.reflex.config`, which calls it while
``rxconfig.py`` loads: RFDS is imported inside the functions, never at module
level, because ``import rfds`` changes process-wide state (Reflex's breakpoints)
and Django, ``lex start`` and Celery never import it.
"""

from __future__ import annotations

import os
from typing import Any

#: ``LEX_FORMAT_LOCALE`` default: how numbers, money and dates are written.
DEFAULT_FORMAT_LOCALE = "en-US"
#: ``LEX_UI_LANGUAGE`` default: the language of RFDS's own labels.
DEFAULT_UI_LANGUAGE = "en"
#: ``LEX_BASE_CURRENCY`` default: what a money value without a currency is in.
DEFAULT_BASE_CURRENCY = "EUR"
#: The instance's time zone: the same variable and default as Django's
#: ``TIME_ZONE`` (``lex/lex_app/settings.py``), read here because the plugin is
#: built while ``rxconfig.py`` loads, before Django's settings exist.
DEFAULT_TIME_ZONE = "Europe/Berlin"

#: The values ``LEX_RFDS_CONFORMANCE`` accepts: RFDS's compile-time checks that
#: dashboards use its components and tokens (decision D14).
CONFORMANCE_LEVELS = ("off", "warn", "error")

_TRUTHY = {"1", "true", "yes", "y", "on"}

#: Where lex-app is reached on a developer's machine: any local port, since the
#: React dev server, ``lex start`` and the e2e harness each pick their own.
LOCAL_FRAMERS = ("http://localhost:*", "http://127.0.0.1:*")


def brand_spec():
    """RFDS's ``BrandSpec`` for lex-app: the LEX teal.

    RFDS derives its accent scales from it and checks it against its contrast
    gates when the app compiles; the navy chrome lex-app's own sidebar uses is
    set by :func:`lex_head_components`, since RFDS derives chrome from the brand.
    """
    from rfds import BrandSpec

    from lex.lex_app.design_system.lex_tokens import TEAL

    return BrandSpec(primary=TEAL)


def conformance_level() -> str:
    """How strictly RFDS checks dashboards at compile time: warn, or fail in CI.

    ``LEX_RFDS_CONFORMANCE`` wins when it names a level; otherwise a CI run
    (``CI`` truthy) fails the compile on a finding and a developer gets a
    warning, so the edit loop never stops over one.
    """
    level = (os.getenv("LEX_RFDS_CONFORMANCE") or "").strip().lower()
    if level in CONFORMANCE_LEVELS:
        return level
    return "error" if (os.getenv("CI") or "").strip().lower() in _TRUTHY else "warn"


def format_settings() -> dict[str, str]:
    """The locale, language, currency and time zone RFDS formats values with."""
    return {
        "format_locale": os.getenv("LEX_FORMAT_LOCALE") or DEFAULT_FORMAT_LOCALE,
        "ui_language": os.getenv("LEX_UI_LANGUAGE") or DEFAULT_UI_LANGUAGE,
        "base_currency": os.getenv("LEX_BASE_CURRENCY") or DEFAULT_BASE_CURRENCY,
        "timezone": os.getenv("LEX_TIME_ZONE") or DEFAULT_TIME_ZONE,
    }


def rfds_plugin(**overrides: Any):
    """``rfds.RFDSPlugin`` with lex-app's defaults; any field can be overridden.

    It ships RFDS's stylesheets and fonts, checks the brand's contrast and the
    pages' conformance at compile time, and is what ``rfds.install()`` refuses
    to run without. ``lex_config()`` lists it after the Radix plugin, the order
    RFDS requires for its stylesheets to win.
    """
    import rfds

    settings: dict[str, Any] = {
        "brand": brand_spec(),
        "conformance": conformance_level(),
        "tenant_mode": "single",
        **format_settings(),
    }
    settings.update(overrides)
    return rfds.RFDSPlugin(**settings)


def theme_plugin():
    """``RadixThemesPlugin`` carrying RFDS's root theme.

    RFDS forbids ``rx.App(theme=...)``: the theme is configured here, once, and
    RFDS's own CSS variables carry everything else.
    """
    import reflex as rx
    import rfds

    return rx.plugins.RadixThemesPlugin(theme=rfds.radix_theme())


def lex_app_origins() -> list[str]:
    """The origins lex-app's pages are served from: who may frame a dashboard.

    One ``https://`` origin per hostname the instance's app answers on, its
    canonical domain first and then any customer-owned domain attached to it.
    With none configured -- every local run -- lex-app is on ``localhost`` at
    whatever port the developer runs it on, so any local port is allowed.
    """
    from lex import hosts

    origins = []
    for host in hosts.public_hosts("app"):
        if not host or host.startswith("localhost") or host.startswith("127."):
            continue
        origins.append(f"https://{host}")
    return origins or list(LOCAL_FRAMERS)


def frame_ancestors(origins: list[str] | None = None) -> str:
    """The CSP ``frame-ancestors`` directive allowing the dashboards themselves and lex-app."""
    sources = ["'self'", *(lex_app_origins() if origins is None else origins)]
    return "frame-ancestors " + " ".join(dict.fromkeys(sources))


def lex_security_headers():
    """RFDS's security headers, configured so that lex-app -- and only lex-app -- frames dashboards.

    RFDS's defaults send ``X-Frame-Options: DENY`` and ``frame-ancestors
    'none'``, which would leave every dashboard blank inside lex-app's Analytics
    tab. ``X-Frame-Options`` cannot name an origin, so it is left out and the CSP
    names lex-app's origins instead (decision D10). The CSP stays report-only,
    as RFDS ships it: ``frame-ancestors`` is the one directive browsers ignore in
    a report-only policy, so it is also sent as an enforcing policy of its own.
    """
    from rfds.platform import SecurityHeaders

    return SecurityHeaders(frame_options=None)


def framing_policy_transformer(origins: list[str] | None = None):
    """An ``api_transformer`` entry that sends the enforcing ``frame-ancestors`` policy.

    Separate from RFDS's report-only CSP (see :func:`lex_security_headers`):
    a browser enforces a policy only from the ``Content-Security-Policy``
    header, and a second such header is combined with any other, so each one
    restricts on its own. Existing headers of a response are never replaced.
    """
    directive = frame_ancestors(origins).encode()

    def transform(app):
        async def middleware(scope, receive, send):
            if scope.get("type") != "http":
                await app(scope, receive, send)
                return

            async def send_with_policy(message):
                if message.get("type") == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"content-security-policy", directive))
                    message = {**message, "headers": headers}
                await send(message)

            await app(scope, receive, send_with_policy)

        return middleware

    transform.lex_framing_policy = True
    return transform


def lex_head_components() -> list:
    """lex-app's styles over RFDS's, inlined in every page's head: navy chrome, the LEX font,
    and the embedded layout.

    Inline rather than an app stylesheet, so they need no asset linked into the
    project; their ``html:root`` selectors outrank RFDS's own, so where in the
    head they land does not matter. The source is ``_js/lex_rfds.css``.
    """
    import reflex as rx

    from lex.lex_app.reflex._js import HERE

    return [rx.el.style((HERE / "lex_rfds.css").read_text(encoding="utf-8"))]
