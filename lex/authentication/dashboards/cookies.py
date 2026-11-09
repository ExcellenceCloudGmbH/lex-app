"""The dashboard cookies: names, lifetimes and the exact ``Set-Cookie`` lines.

* ``__Host-lex_dash`` -- the opaque handle of a :class:`DashboardBinding` (stored hashed). 30 days,
  re-set at most daily while the dashboard is used.
* ``__Host-lex_dash_hs`` -- the nonces of hand-offs in flight (``state``), so a code comes back
  only to the browser that asked for it. Ten minutes; up to five at once (several tabs).
* ``__Host-lex_embed`` -- the read-only cookie an MCP embed ticket is redeemed into.

Over https every cookie is ``HttpOnly; Secure; SameSite=None; Partitioned; Path=/``: a dashboard
or an embed is usually framed by another site, and a partitioned cookie is still accepted where
third-party cookies are blocked. The ``__Host-`` prefix makes the browser refuse the cookie unless
it is ``Secure``, host-only and ``Path=/`` -- a sibling subdomain cannot plant one.

Plain-http development (``lex streamlit`` on ``http://localhost``) cannot satisfy ``Secure``, and
the prefix would make the browser drop the cookie. There the prefix is left off (``lex_dash``,
``lex_dash_hs``, ``lex_embed``) and the cookie is ``HttpOnly; SameSite=Lax; Path=/`` -- the
frontend and the dashboard share the ``localhost`` site, so ``Lax`` reaches the frame. A
prefixed cookie is never read over http and an unprefixed one never over https.

Neither Starlette nor Django can write ``Partitioned`` on Python < 3.14, so the line is built
here, from the standard library, for both: :func:`set_cookie_header` for the proxy and
:func:`django_morsel` for Django responses.
"""
from __future__ import annotations

import re
from http.cookies import Morsel
from typing import Iterable, List, Optional

HOST_PREFIX = "__Host-"

#: The handle of a dashboard binding.
HANDLE_COOKIE = "__Host-lex_dash"
#: The hand-off nonces (``state``) in flight.
NONCE_COOKIE = "__Host-lex_dash_hs"
#: The read-only embed cookie.
EMBED_COOKIE = "__Host-lex_embed"

#: Lifetime of the handle cookie; it is re-set (sliding) at most once per
#: :data:`HANDLE_RESET_INTERVAL` while the dashboard is in use.
HANDLE_MAX_AGE = 30 * 24 * 3600
HANDLE_RESET_INTERVAL = 24 * 3600
#: Lifetime of the nonce cookie: one hand-off round trip, with room for a slow sign-in.
NONCE_MAX_AGE = 10 * 60
#: Hand-offs that may be in flight at once in one browser (one per tab or frame).
MAX_PENDING_NONCES = 5

#: Query parameter marking a URL the proxy reaches *after* a hand-off. Seen again without a
#: handle cookie, it means the browser did not keep the cookie: the proxy shows the
#: blocked-cookie page instead of starting another hand-off (no redirect loop).
LOOP_MARKER = "lex_hs"

_NONCE_SEPARATOR = "."
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_\-.]*$")
_EPOCH = "Thu, 01 Jan 1970 00:00:00 GMT"


class _PartitionedMorsel(Morsel):
    """A morsel whose ``Set-Cookie`` line ends in ``; Partitioned`` (CHIPS)."""

    def OutputString(self, attrs=None) -> str:  # noqa: N802 (stdlib name)
        return super().OutputString(attrs) + "; Partitioned"


def cookie_name(name: str, secure: bool) -> str:
    """The name used for ``name`` on a secure (https) or plain-http connection."""
    if secure or not name.startswith(HOST_PREFIX):
        return name
    return name[len(HOST_PREFIX):]


def django_morsel(name: str, value: str, *, max_age: int, secure: bool, expires: Optional[str] = None) -> Morsel:
    """A morsel for ``name`` (already the name of the connection, see :func:`cookie_name`).

    Assign it with ``response.cookies[morsel.key] = morsel``: Django writes ``response.cookies``
    with ``Morsel.output()``, so the ``Partitioned`` attribute reaches the wire.
    """
    if not _SAFE_VALUE.match(value or ""):
        raise ValueError("cookie values written here are URL-safe tokens")
    morsel: Morsel = _PartitionedMorsel() if secure else Morsel()
    morsel.set(name, value, value)
    morsel["path"] = "/"
    morsel["max-age"] = int(max_age)
    morsel["httponly"] = True
    if expires:
        morsel["expires"] = expires
    if secure:
        morsel["secure"] = True
        morsel["samesite"] = "None"
    else:
        morsel["samesite"] = "Lax"
    return morsel


def set_cookie_header(name: str, value: str, *, max_age: int, secure: bool) -> str:
    """The value of a ``Set-Cookie`` header that sets ``name`` (the connection's name)."""
    return django_morsel(name, value, max_age=max_age, secure=secure).OutputString()


def delete_cookie_header(name: str, *, secure: bool) -> str:
    """The value of a ``Set-Cookie`` header that removes ``name`` (same attributes, expired)."""
    return django_morsel(name, "", max_age=0, secure=secure, expires=_EPOCH).OutputString()


def pending_nonces(raw: Optional[str]) -> List[str]:
    """The nonces in a nonce-cookie value, newest first."""
    return [nonce for nonce in (raw or "").split(_NONCE_SEPARATOR) if nonce]


def nonce_cookie_value(nonces: Iterable[str]) -> str:
    """A nonce-cookie value holding at most :data:`MAX_PENDING_NONCES` nonces (newest first)."""
    kept: List[str] = []
    for nonce in nonces:
        if nonce and nonce not in kept and _SAFE_VALUE.match(nonce) and _NONCE_SEPARATOR not in nonce:
            kept.append(nonce)
        if len(kept) >= MAX_PENDING_NONCES:
            break
    return _NONCE_SEPARATOR.join(kept)


__all__ = [
    "EMBED_COOKIE",
    "HANDLE_COOKIE",
    "HANDLE_MAX_AGE",
    "HANDLE_RESET_INTERVAL",
    "HOST_PREFIX",
    "LOOP_MARKER",
    "MAX_PENDING_NONCES",
    "NONCE_COOKIE",
    "NONCE_MAX_AGE",
    "cookie_name",
    "delete_cookie_header",
    "django_morsel",
    "nonce_cookie_value",
    "pending_nonces",
    "set_cookie_header",
]
