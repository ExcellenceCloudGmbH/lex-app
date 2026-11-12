"""Which origins the dashboard hand-off may use, and which app host a dashboard hands off to.

Every origin comes from :mod:`lex.core.instance_hosts` (``LEX_INSTANCE_HOSTS``, with its
``DOMAIN_HOSTED`` / ``STREAMLIT_URL`` / ``REFLEX_URL`` fallbacks). Nothing here ever accepts or
redirects to an origin that list does not name -- with one exception for development: when the
instance is not deployed (no ``DEPLOYMENT_ENVIRONMENT``), loopback origins (``localhost``,
``127.0.0.1``, ``[::1]``, any port) are accepted too, and a dashboard with no configured app host
hands off to ``http://localhost:8000``, where ``lex start`` serves the app.

**Same-site.** The hand-off runs *inside* the dashboard's frame: the browser goes from the
dashboard host to ``<app host>/oidc/handoff`` and back. Django's session cookie is
``SameSite=Lax``, so it is only sent when the app host shares a site (registrable domain) with
the page that frames the dashboard -- the frontend, served by the app host. The frontend picks
the dashboard host that is same-site with itself, and the proxy picks the app host that is
same-site with the dashboard host, by the same rule (:func:`site_of`, the approximation the
frontend uses too: no public-suffix list).
"""
from __future__ import annotations

import os
from typing import Iterable, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from lex.core.instance_hosts import instance_hosts

#: Hosts that are their own machine, accepted in development only.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})
#: Where a dashboard without a configured app host hands off to in development.
DEV_APP_ORIGIN = "http://localhost:8000"

_SECOND_LEVEL_LABELS = frozenset({"co", "com", "net", "org", "gov", "ac", "edu"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _env(environ: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def is_deployed(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Whether this process runs as a deployed instance (``DEPLOYMENT_ENVIRONMENT`` is set)."""
    return _env(environ).get("DEPLOYMENT_ENVIRONMENT") is not None


def split_host(host: str) -> Tuple[str, Optional[int]]:
    """``(hostname, port)`` of ``host[:port]`` (IPv6 in brackets); the hostname is lower-case."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        if end == -1:
            return host, None
        name, rest = host[: end + 1], host[end + 1:]
        if rest.startswith(":") and rest[1:].isdigit():
            return name, int(rest[1:])
        return name, None
    name, sep, port = host.rpartition(":")
    if sep and port.isdigit() and name:
        return name, int(port)
    return host, None


def site_of(hostname: str) -> str:
    """The site (registrable domain) of a hostname, the unit SameSite cookies are scoped to.

    Approximated without the public-suffix list, exactly as the frontend does: the last two
    labels, or three under a two-letter TLD with a generic second level (``example.co.uk``).
    IP addresses and ``localhost`` are their own site.
    """
    host = split_host(hostname)[0].rstrip(".")
    if host in LOOPBACK_HOSTS or host.startswith("[") or ":" in host:
        return host
    labels = host.split(".")
    if all(label.isdigit() for label in labels) and len(labels) == 4:
        return host
    if len(labels) <= 2:
        return host
    keep = 3 if len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL_LABELS else 2
    return ".".join(labels[-keep:])


def same_site(host_a: str, host_b: str) -> bool:
    """Whether two hosts (ports ignored) belong to the same site."""
    return bool(host_a) and bool(host_b) and site_of(host_a) == site_of(host_b)


def normalize_origin(value: object) -> Optional[str]:
    """``scheme://host[:port]`` (lower-case, default port dropped) of an http(s) origin, else None.

    Only a bare origin is accepted -- a path other than ``/``, a query, a fragment or
    credentials make it something else, and something else is not an origin.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        return None
    hostname = parts.hostname.lower()
    if ":" in hostname:  # IPv6
        hostname = f"[{hostname}]"
    if any(c in hostname for c in "*\\ @"):
        return None
    if port is None or port == _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{hostname}"
    return f"{scheme}://{hostname}:{port}"


def origin_of(url: object) -> Optional[str]:
    """The normalized origin of an absolute http(s) URL (any path), else None."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return normalize_origin(f"{parts.scheme}://{parts.netloc}")


def local_path(path: object) -> str:
    """A path that can only ever mean this origin -- never ``//host`` or ``\\\\host``.

    A redirect to ``//evil.example/`` is scheme-relative: the browser leaves for another host. A
    request for ``/%2Fevil.example/`` decodes to exactly that path, so any path taken from a
    request must pass through here before it becomes a ``Location``, a meta refresh or a script
    target. Browsers read a backslash as a slash and drop tabs and newlines, so both are dealt
    with before the leading slashes collapse to one.
    """
    text = "".join(ch for ch in str(path or "") if ch >= " " and ch != "\x7f").replace("\\", "/")
    return "/" + text.lstrip("/")


def is_loopback_origin(origin: Optional[str]) -> bool:
    if not origin:
        return False
    host = urlsplit(origin).netloc
    return split_host(host)[0] in LOOPBACK_HOSTS


def _origins(role: str, environ: Optional[Mapping[str, str]]) -> Tuple[str, ...]:
    hosts = instance_hosts(_env(environ))
    out = []
    for origin in hosts.origins(role):
        normalized = normalize_origin(origin)
        if normalized and normalized not in out:
            out.append(normalized)
    return tuple(out)


def dashboard_origins(environ: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    """The configured origins of the Streamlit dashboards."""
    return _origins("streamlit", environ)


def app_origins(environ: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    """The configured origins of the app (Django and the frontend it serves)."""
    return _origins("app", environ)


def _allowed(origin: Optional[str], listed: Iterable[str], environ: Optional[Mapping[str, str]]) -> bool:
    if not origin:
        return False
    if origin in listed:
        return True
    return not is_deployed(environ) and is_loopback_origin(origin)


def is_dashboard_origin(origin: object, environ: Optional[Mapping[str, str]] = None) -> bool:
    """Whether ``origin`` is one of this instance's dashboard origins (loopback in development)."""
    return _allowed(normalize_origin(origin), dashboard_origins(environ), environ)


def is_app_origin(origin: object, environ: Optional[Mapping[str, str]] = None, extra: Iterable[str] = ()) -> bool:
    """Whether ``origin`` is one of this instance's app origins (or ``extra``; loopback in development)."""
    normalized = normalize_origin(origin)
    listed = set(app_origins(environ))
    listed.update(o for o in (normalize_origin(e) for e in extra) if o)
    return _allowed(normalized, listed, environ)


def pick_app_origin(dashboard_host: str, environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The app origin a dashboard served on ``dashboard_host`` hands off to.

    The first configured app host (canonical, platform, then the extra ones) that is same-site
    with the dashboard host; else the canonical one. In development, an unconfigured app -- or
    one configured only as a bare loopback name, which says nothing about its port -- is
    :data:`DEV_APP_ORIGIN`. ``None`` when a deployed instance names no app host at all.
    """
    env = _env(environ)
    endpoints = instance_hosts(env).surface("app").endpoints
    deployed = is_deployed(env)
    candidates = []
    for endpoint in endpoints:
        name, port = split_host(endpoint.host)
        if not deployed and name in LOOPBACK_HOSTS and port is None:
            continue  # a bare "localhost" (e.g. DOMAIN_HOSTED) names no port: use the dev origin
        origin = normalize_origin(endpoint.origin)
        if origin:
            candidates.append((endpoint.host, origin))
    for host, origin in candidates:
        if same_site(host, dashboard_host):
            return origin
    if candidates:
        return candidates[0][1]
    return None if deployed else DEV_APP_ORIGIN


__all__ = [
    "DEV_APP_ORIGIN",
    "LOOPBACK_HOSTS",
    "app_origins",
    "dashboard_origins",
    "is_app_origin",
    "is_dashboard_origin",
    "is_deployed",
    "is_loopback_origin",
    "local_path",
    "normalize_origin",
    "origin_of",
    "pick_app_origin",
    "same_site",
    "site_of",
    "split_host",
]
