"""Where this instance is reachable: the hosts of its app, Streamlit and Reflex surfaces.

The platform sets ``LEX_INSTANCE_HOSTS`` to a JSON object with one entry per surface::

    {
      "app":       {"canonical": "acme.lex.example", "extra": ["app.acme.com"], "platform": "acme.lex.example"},
      "streamlit": {"canonical": "acme-streamlit.lex.example", "extra": [], "platform": "acme-streamlit.lex.example"},
      "reflex":    {"canonical": "acme-reflex.lex.example", "extra": [], "platform": "acme-reflex.lex.example"}
    }

* ``canonical`` -- the host users are sent to;
* ``extra`` -- further hosts the same surface answers on (custom domains attached to it);
* ``platform`` -- the host the platform generated (often the same as ``canonical``).

Every key is optional. A value may be a bare host (``app.acme.com``, ``localhost:8501``) or
a URL (``https://app.acme.com/``); hosts are kept lower-case with their port, and the scheme
of a URL is kept for :meth:`InstanceHosts.origins` (a bare host means ``https``).

Without the variable (older platform releases, local runs) the hosts come from the variables
that existed before it: ``DOMAIN_HOSTED`` (app), ``STREAMLIT_URL`` and ``REFLEX_URL``. A
surface the variable leaves out falls back the same way.

Nothing here raises on a malformed value: the module is read while Django's settings load,
where an exception means a crash-looping pod. Unusable values are dropped with a warning.

Used by the settings (CORS origins, the OIDC redirect allowlist) and by the dashboard
hand-off, which picks the app host that shares a site with a dashboard host.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

#: The environment variable the platform sets.
ENV_VAR = "LEX_INSTANCE_HOSTS"

#: The surfaces an instance has, in the order they are reported.
ROLES = ("app", "streamlit", "reflex")

#: The pre-``LEX_INSTANCE_HOSTS`` variable each surface falls back to.
FALLBACK_VARS = {"app": "DOMAIN_HOSTED", "streamlit": "STREAMLIT_URL", "reflex": "REFLEX_URL"}


@dataclass(frozen=True)
class Endpoint:
    """One host of a surface: ``host`` is ``name[:port]``, lower-case; ``scheme`` is http(s)."""

    host: str
    scheme: str = "https"

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.host}"


def parse_endpoint(value: object) -> Optional[Endpoint]:
    """An :class:`Endpoint` from a bare host or an http(s) URL, or ``None`` when unusable.

    >>> parse_endpoint("https://App.Acme.com/")
    Endpoint(host='app.acme.com', scheme='https')
    >>> parse_endpoint("localhost:8501")
    Endpoint(host='localhost:8501', scheme='https')
    >>> parse_endpoint("*") is None
    True
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    scheme = "https"
    if "://" in text:
        parts = urlsplit(text)
        if parts.scheme.lower() not in ("http", "https"):
            return None
        scheme = parts.scheme.lower()
        text = parts.netloc
    else:
        text = text.split("/", 1)[0]
    host = text.strip().lower()
    # No credentials, wildcards, spaces or empty names: a host, not a pattern.
    if not host or any(c in host for c in "@*\\ \t") or host.startswith(":"):
        return None
    return Endpoint(host=host, scheme=scheme)


@dataclass(frozen=True)
class SurfaceHosts:
    """The hosts of one surface (``app``, ``streamlit`` or ``reflex``)."""

    role: str
    canonical: Optional[Endpoint] = None
    extra: tuple[Endpoint, ...] = ()
    platform: Optional[Endpoint] = None

    @property
    def endpoints(self) -> tuple[Endpoint, ...]:
        """Canonical first, then platform, then the extra hosts; each host once."""
        seen: set[str] = set()
        ordered = []
        for endpoint in (self.canonical, self.platform, *self.extra):
            if endpoint is not None and endpoint.host not in seen:
                seen.add(endpoint.host)
                ordered.append(endpoint)
        return tuple(ordered)

    @property
    def hosts(self) -> tuple[str, ...]:
        return tuple(endpoint.host for endpoint in self.endpoints)

    @property
    def origins(self) -> tuple[str, ...]:
        return tuple(endpoint.origin for endpoint in self.endpoints)


@dataclass(frozen=True)
class InstanceHosts:
    """The hosts of every surface, and where they came from (``source``)."""

    surfaces: Mapping[str, SurfaceHosts] = field(default_factory=dict)
    #: ``"LEX_INSTANCE_HOSTS"`` when the variable was used, else ``"fallback"``.
    source: str = "fallback"

    def surface(self, role: str) -> SurfaceHosts:
        if role not in ROLES:
            raise KeyError(f"Unknown surface {role!r}; expected one of {', '.join(ROLES)}")
        return self.surfaces.get(role) or SurfaceHosts(role=role)

    def hosts(self, *roles: str) -> tuple[str, ...]:
        """Hosts of ``roles`` (all surfaces when none is given), each once, in order."""
        return _unique(host for role in (roles or ROLES) for host in self.surface(role).hosts)

    def origins(self, *roles: str) -> tuple[str, ...]:
        """``scheme://host`` of ``roles`` (all surfaces when none is given), each once."""
        return _unique(origin for role in (roles or ROLES) for origin in self.surface(role).origins)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)


def _surface_from_json(role: str, raw: object) -> Optional[SurfaceHosts]:
    if not isinstance(raw, Mapping):
        if raw is not None:
            logger.warning("%s: %r is not an object; ignored", ENV_VAR, role)
        return None
    extra_raw = raw.get("extra") or []
    if isinstance(extra_raw, str):
        extra_raw = [extra_raw]
    if not isinstance(extra_raw, (list, tuple)):
        logger.warning("%s: %s.extra is not a list; ignored", ENV_VAR, role)
        extra_raw = []
    surface = SurfaceHosts(
        role=role,
        canonical=parse_endpoint(raw.get("canonical")),
        extra=tuple(e for e in (parse_endpoint(v) for v in extra_raw) if e is not None),
        platform=parse_endpoint(raw.get("platform")),
    )
    return surface if surface.endpoints else None


def parse_instance_hosts(
    raw: Optional[str],
    *,
    domain_hosted: Optional[str] = None,
    streamlit_url: Optional[str] = None,
    reflex_url: Optional[str] = None,
) -> InstanceHosts:
    """Parse a ``LEX_INSTANCE_HOSTS`` value; surfaces it does not name fall back to the old variables.

    Never raises: invalid JSON, or a value that is not an object, is logged and treated as
    absent.
    """
    fallback = {"app": domain_hosted, "streamlit": streamlit_url, "reflex": reflex_url}
    parsed: Mapping[str, object] = {}
    if raw is not None and raw.strip():
        try:
            data = json.loads(raw)
        except ValueError:
            logger.warning("%s is not valid JSON; using DOMAIN_HOSTED, STREAMLIT_URL and REFLEX_URL", ENV_VAR)
            data = None
        if isinstance(data, Mapping):
            parsed = data
        elif data is not None:
            logger.warning("%s must be a JSON object; using the fallback variables", ENV_VAR)

    surfaces: dict[str, SurfaceHosts] = {}
    used_variable = False
    for role in ROLES:
        surface = _surface_from_json(role, parsed.get(role))
        if surface is not None:
            used_variable = True
        else:
            endpoint = parse_endpoint(fallback[role])
            surface = SurfaceHosts(role=role, canonical=endpoint) if endpoint is not None else None
        if surface is not None:
            surfaces[role] = surface
    return InstanceHosts(surfaces=surfaces, source=ENV_VAR if used_variable else "fallback")


def instance_hosts(environ: Optional[Mapping[str, str]] = None) -> InstanceHosts:
    """The instance's hosts from the process environment (or ``environ``)."""
    env = os.environ if environ is None else environ
    return parse_instance_hosts(
        env.get(ENV_VAR),
        domain_hosted=env.get(FALLBACK_VARS["app"]),
        streamlit_url=env.get(FALLBACK_VARS["streamlit"]),
        reflex_url=env.get(FALLBACK_VARS["reflex"]),
    )


__all__ = [
    "ENV_VAR",
    "Endpoint",
    "FALLBACK_VARS",
    "InstanceHosts",
    "ROLES",
    "SurfaceHosts",
    "instance_hosts",
    "parse_endpoint",
    "parse_instance_hosts",
]
