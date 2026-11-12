"""``LEX_STREAMLIT_AUTH``: how Streamlit dashboards sign in (the rollout flag of the hand-off).

* ``legacy`` (the default) -- exactly the pre-2.5 behaviour: the frontend hands the dashboard an
  access token in the iframe URL (``auth_token``), the proxy keeps its own token store and may
  run its own Keycloak sign-in, and ``/auth/adopt`` takes renewed tokens.
* ``dual`` -- the Django-anchored hand-off (``oidc/handoff``) is served and advertised
  (``api/auth/session`` reports ``dashboard_handoff: ["streamlit"]``), and every legacy
  credential is still accepted, so frontends that predate the hand-off keep working.
* ``handoff`` -- only the hand-off. ``auth_token``, the proxy's own sign-in and its token store
  are ignored, ``/auth/adopt`` is a no-op and ``api/auth/streamlit-token/`` answers a
  placeholder, so an old frontend still loads its dashboards -- through the hand-off.

Rolling back is setting the flag back. The value is read by Django (the capability and the
placeholder), by the dashboard proxy (at import) and by ``lex streamlit`` (which refuses an
unknown value before anything starts).

This module imports nothing but the standard library: the proxy reads it before Django is
involved, and in ``legacy`` mode it never involves Django at all.
"""
from __future__ import annotations

import logging
import os
from typing import Mapping, Optional

logger = logging.getLogger(__name__)

#: The environment variable.
ENV_VAR = "LEX_STREAMLIT_AUTH"

LEGACY = "legacy"
DUAL = "dual"
HANDOFF = "handoff"

#: Every accepted value, in rollout order.
MODES = (LEGACY, DUAL, HANDOFF)

#: Unset means ``legacy``: nothing changes until the platform turns the hand-off on.
DEFAULT_MODE = LEGACY


class InvalidStreamlitAuthMode(ValueError):
    """``LEX_STREAMLIT_AUTH`` holds a value that is not one of :data:`MODES`."""


def parse_streamlit_auth(raw: Optional[str]) -> str:
    """The mode a raw value names; unset or blank is :data:`DEFAULT_MODE`.

    Case and surrounding spaces are ignored. Anything else raises
    :class:`InvalidStreamlitAuthMode` -- a typo must not silently pick a mode.
    """
    value = (raw or "").strip().lower()
    if not value:
        return DEFAULT_MODE
    if value not in MODES:
        raise InvalidStreamlitAuthMode(
            f"{ENV_VAR} must be one of {', '.join(MODES)}; got {raw!r}."
        )
    return value


def streamlit_auth_mode(environ: Optional[Mapping[str, str]] = None) -> str:
    """The mode of this process (``os.environ`` unless ``environ`` is given).

    Lenient: an unknown value is logged and read as :data:`DEFAULT_MODE`. Django's side (the
    capability) must not crash on a typo -- the proxy and ``lex streamlit`` refuse it loudly
    with :func:`parse_streamlit_auth`, and advertising nothing keeps every frontend on the
    path that still works.
    """
    env = os.environ if environ is None else environ
    raw = env.get(ENV_VAR)
    try:
        return parse_streamlit_auth(raw)
    except InvalidStreamlitAuthMode:
        logger.warning("%s=%r is not one of %s; treating it as %r", ENV_VAR, raw, ", ".join(MODES), DEFAULT_MODE)
        return DEFAULT_MODE


def handoff_enabled(mode: str) -> bool:
    """Whether ``mode`` serves (and advertises) the Django-anchored hand-off."""
    return mode in (DUAL, HANDOFF)


def legacy_enabled(mode: str) -> bool:
    """Whether ``mode`` still accepts the pre-2.5 dashboard credentials."""
    return mode in (LEGACY, DUAL)


__all__ = [
    "DEFAULT_MODE",
    "DUAL",
    "ENV_VAR",
    "HANDOFF",
    "InvalidStreamlitAuthMode",
    "LEGACY",
    "MODES",
    "handoff_enabled",
    "legacy_enabled",
    "parse_streamlit_auth",
    "streamlit_auth_mode",
]
