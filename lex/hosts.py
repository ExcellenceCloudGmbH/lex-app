"""The hostnames an instance answers on, and which one to frame a dashboard from.

An instance used to have exactly one hostname per role -- ``DOMAIN_HOSTED`` for
the app, ``STREAMLIT_URL`` and ``REFLEX_URL`` for its two dashboards -- and
every absolute URL in the product was built from those three. Customer-owned
domains ended that: the Instance Controller can attach ``app.customer.com`` to
an instance that is also still reachable at ``<sub>.lit.<platform domain>``, and
both serve the very same pods at the same time.

Two separate things follow from that, and this module exists for both.

**Accepting a hostname.** Django refuses a request whose ``Host`` is not in
``ALLOWED_HOSTS``, and ``AllowedHostsOriginValidator`` refuses a WebSocket on
the same grounds. A hostname the ingress routes but the process rejects is a
custom domain that answers "400 Bad Request", so the process has to be told
every hostname it may be reached on. :func:`public_hosts` is that list.

**Framing a dashboard.** A Streamlit or Reflex dashboard is shown inside an
``<iframe>`` in the app shell. When the shell and the dashboard are on
different registrable domains the frame is a *third-party* context: Safari
blocks its cookies outright and Chrome is removing them, so the dashboard
cannot hold a session and simply never signs in. Framing
``<sub>-streamlit.lit.<platform domain>`` inside ``app.customer.com`` is
exactly that case. :func:`same_site_url` picks, out of the hostnames the
dashboard is served on, the one that shares a registrable domain with the host
the shell is being served from -- so a customer on their own domain gets
``streamlit.app.customer.com`` and stays first-party.

The environment is written by the lex-instance terraform module
(``locals.computed.tf``), which lists the canonical host first and the attached
customer-owned ones after it. The variables are absent entirely for an instance
with no custom domain, and every function here falls back to the single
canonical URL -- which is what such an instance, and an older image, both see.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from urllib.parse import urlsplit

#: Where each role's hostname list and its single canonical URL come from.
#: ``hosts`` is written only when a custom domain is attached; ``url`` has been
#: set on every instance since long before custom domains existed.
_ROLE_ENV = {
    "app": ("LEX_APP_HOSTS", "DOMAIN_HOSTED"),
    "streamlit": ("LEX_STREAMLIT_HOSTS", "STREAMLIT_URL"),
    "reflex": ("LEX_REFLEX_HOSTS", "REFLEX_URL"),
}

#: What a dashboard falls back to with nothing configured at all: the ports
#: ``lex streamlit`` and ``lex reflex`` serve on locally.
_LOCAL_DEFAULT = {
    "app": "",
    "streamlit": "http://localhost:8501",
    "reflex": "http://localhost:8502",
}

#: Second-level suffixes under which registrations happen, so that
#: ``customer.co.uk`` is read as one registrable domain rather than as
#: ``co.uk``. Not a public-suffix list: a wrong answer here only means a
#: dashboard is framed from the canonical host instead of a same-site one, so
#: the common shapes are enough and a dependency is not worth it. Mirrors
#: ``_SECOND_LEVEL_SUFFIXES`` in the Instance Controller's
#: ``instance_api/models/custom_hostname.py``.
_SECOND_LEVEL_SUFFIXES = frozenset({
    "co", "com", "net", "org", "gov", "ac", "edu", "ltd", "plc", "me", "sch",
})

#: The hostname the request being served arrived on, set by
#: :class:`CurrentHostMiddleware`. A ContextVar rather than a thread-local
#: because lex-app is served over ASGI, where asgiref copies the context into
#: the thread a sync view runs in and back out again -- a thread-local would
#: survive that too, but would also leak between the pool's reused threads.
_current_host: ContextVar[str] = ContextVar("lex_current_host", default="")


def host_of(url_or_host: str) -> str:
    """The bare lower-case hostname in ``url_or_host``, without port or path."""
    value = (url_or_host or "").strip().lower().rstrip("/")
    if not value:
        return ""
    if "://" in value:
        value = urlsplit(value).netloc
    # An IPv6 literal keeps its brackets; everything else loses its port.
    if value.startswith("["):
        return value.split("]")[0] + "]"
    return value.split("/")[0].split(":")[0].rstrip(".")


def public_hosts(role: str) -> list:
    """Every hostname ``role`` is served on, canonical first, no duplicates.

    Falls back to the single canonical host when the list is not configured,
    which is both the no-custom-domain case and what an instance deployed by an
    older terraform module looks like.
    """
    list_var, url_var = _ROLE_ENV[role]
    configured = [host_of(part) for part in (os.getenv(list_var) or "").split(",")]
    hosts = [host for host in configured if host]
    if not hosts:
        canonical = host_of(os.getenv(url_var) or _LOCAL_DEFAULT[role])
        hosts = [canonical] if canonical else []
    # dict.fromkeys rather than set(): the canonical host must stay first, as
    # it is what every fallback below returns.
    return list(dict.fromkeys(hosts))


def canonical_url(role: str) -> str:
    """The URL ``role`` presents itself as -- its ``*_URL`` env, or localhost."""
    _list_var, url_var = _ROLE_ENV[role]
    return (os.getenv(url_var) or _LOCAL_DEFAULT[role]).rstrip("/")


def registrable_domain(host: str) -> str:
    """The domain ``host`` is registered under: ``a.b.customer.co.uk`` -> ``customer.co.uk``.

    Cookies are scoped to this, which is why it -- and not the hostname -- is
    what decides whether an iframe is first- or third-party.
    """
    host = host_of(host)
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if labels[-2] in _SECOND_LEVEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def same_site_host(role: str, viewer_host: str = "") -> str:
    """The ``role`` hostname sharing a registrable domain with ``viewer_host``.

    Falls back to the canonical hostname when no attached hostname matches,
    which keeps a dashboard reachable even where the frame has to be
    third-party -- a degraded dashboard beats one that is not shown at all.
    """
    hosts = public_hosts(role)
    if not hosts:
        return ""

    viewer = host_of(viewer_host) or current_host()
    if viewer:
        site = registrable_domain(viewer)
        for host in hosts:
            if registrable_domain(host) == site:
                return host

    return hosts[0]


def same_site_url(role: str, viewer_host: str = "") -> str:
    """:func:`same_site_host` as a URL, keeping the canonical URL's scheme and port.

    The scheme and port come from the canonical URL rather than being assumed
    to be ``https``: a local ``lex streamlit`` run serves
    ``http://localhost:8501``, and hard-coding https there would frame a
    dashboard that cannot load.
    """
    canonical = canonical_url(role)
    host = same_site_host(role, viewer_host)
    if not host:
        return canonical

    parts = urlsplit(canonical if "://" in canonical else f"https://{canonical}")
    if host_of(parts.netloc) == host:
        # The canonical host itself: return it verbatim, port included.
        return canonical
    return f"{parts.scheme}://{host}"


def set_current_host(host: str) -> None:
    _current_host.set(host_of(host))


def current_host() -> str:
    """The hostname of the request being served, or '' outside a request."""
    return _current_host.get()


class CurrentHostMiddleware:
    """Record each request's ``Host`` so a report can frame a same-site dashboard.

    An ``HTMLReport`` renders through ``get_html(self, user)``, a signature
    every project's own reports implement, so it cannot grow a ``request``
    parameter without breaking them. The host is put here instead, where
    :func:`same_site_url` reads it by default.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Read from META rather than through `request.get_host()`, which
        # validates against ALLOWED_HOSTS and raises: a rejected host must fail
        # where Django decides it does, not earlier and not in this middleware.
        raw = request.META.get("HTTP_X_FORWARDED_HOST") or request.META.get("HTTP_HOST") or ""
        token = _current_host.set(host_of(raw.split(",")[0]))
        try:
            return self.get_response(request)
        finally:
            _current_host.reset(token)
