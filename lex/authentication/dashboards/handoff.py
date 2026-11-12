"""Issuing and redeeming hand-off codes, and resolving the bindings they turn into.

Synchronous Django ORM code. The app (``oidc/handoff``, the MCP embed tool) calls it in a request
thread; the Streamlit proxy calls it off its event loop, in a worker thread (same image, same
database, so the redeem is in-process -- no HTTP hop, no shared secret).

* :func:`issue_code` -- a single-use code, valid :data:`CODE_TTL_SECONDS`, bound to a sign-in, an
  audience, the origin it may be redeemed for and (for dashboards) the browser's ``state``.
* :func:`redeem` -- turn a code into a :class:`DashboardBinding` and its opaque handle, once.
  A code presented a second time is refused *and* revokes the binding its first use created
  (RFC 6749 §4.1.2: a replayed code means the first redemption may not have been the user's).
* :func:`resolve` -- the live binding a handle names.
* :func:`revoke` -- end a binding (logout, an ended sign-in, an expired embed).

Codes and handles are random 256-bit URL-safe strings; only their SHA-256 digests are stored.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from django.db import transaction
from django.utils import timezone

from lex.authentication.dashboards.cookies import HANDLE_RESET_INTERVAL

#: Audience of a Streamlit dashboard hand-off.
AUD_STREAMLIT = "streamlit"
#: Audience of an MCP embed ticket.
AUD_EMBED = "embed"
AUDIENCES = (AUD_STREAMLIT, AUD_EMBED)

#: How long a code may wait to be redeemed.
CODE_TTL_SECONDS = 60


class HandoffError(Exception):
    """A code that cannot be redeemed. ``reason`` is a short code, never the code itself."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True)
class Redemption:
    """A redeemed code: the new binding and its handle (the only time the handle exists in clear)."""

    binding: Any
    handle: str


def digest(secret: str) -> str:
    """The stored form of a code, handle or state: SHA-256, hex."""
    return hashlib.sha256((secret or "").encode("utf-8")).hexdigest()


def new_secret() -> str:
    """A fresh code or handle: 256 random bits, URL-safe."""
    return secrets.token_urlsafe(32)


def _now() -> datetime:
    return timezone.now()


def _models():
    from lex.authentication.models import DashboardBinding, DashboardHandoffCode

    return DashboardBinding, DashboardHandoffCode


def issue_code(session: Any, *, aud: str, return_origin: str, state: str = "",
               ttl: int = CODE_TTL_SECONDS) -> str:
    """Mint a single-use code for ``session`` (an active ``OIDCSession``); returns the raw code."""
    if aud not in AUDIENCES:
        raise ValueError(f"unknown hand-off audience {aud!r}")
    _, DashboardHandoffCode = _models()
    raw = new_secret()
    DashboardHandoffCode.objects.create(
        code_hash=digest(raw),
        session=session,
        aud=aud,
        return_origin=return_origin,
        state_hash=digest(state) if state else "",
        expires_at=_now() + timedelta(seconds=ttl),
    )
    return raw


def redeem(code: str, *, aud: str, origin: str, state: str = "", holder_handle: str = "") -> Redemption:
    """Redeem ``code`` once into a binding for ``origin``; raises :class:`HandoffError`.

    The code is marked used before anything else is checked, so a code presented with the
    wrong ``state``, audience or origin is spent too. The outcome is decided inside the
    transaction and raised after it, so marking a code used (or revoking the binding of a
    replayed one) is never rolled back by the refusal itself. An embed binding expires with
    the access token its sign-in was made from; a dashboard binding lives as long as its
    sign-in.

    ``holder_handle``: the handle the presenting browser already holds. A used code presented
    by the browser holding the very binding it produced (an MCP host rendering the same widget
    URL again) is refused as ``code_already_redeemed`` and changes nothing; presented by anyone
    else, a used code revokes that binding.

    Only the code row is locked (``FOR UPDATE OF`` the code table): the sign-in row is read
    with it but never locked, so the read never queues behind a refresh that holds the sign-in
    across a Keycloak call, and a refresh never queues behind a redeem. (The commit still
    checks the new binding's foreign key with ``FOR KEY SHARE`` on the sign-in row, which waits
    while a refresh holds that row ``FOR UPDATE``; a refresh locking it ``FOR NO KEY UPDATE`` --
    it changes no key -- would not hold the commit up.)
    """
    DashboardBinding, DashboardHandoffCode = _models()
    now = _now()
    error: Optional[str] = None
    redemption: Optional[Redemption] = None
    with transaction.atomic():
        row = (
            DashboardHandoffCode.objects.select_for_update(of=("self",))
            .select_related("session")
            .filter(code_hash=digest(code))
            .first()
        )
        if row is None:
            error = "invalid_code"
        elif row.used_at is not None:
            holder = (
                row.binding_id is not None and bool(holder_handle)
                and DashboardBinding.objects.filter(
                    pk=row.binding_id, handle_hash=digest(holder_handle), revoked_at__isnull=True
                ).exists()
            )
            if holder:
                error = "code_already_redeemed"
            else:
                error = "code_reused"
                if row.binding_id is not None:
                    DashboardBinding.objects.filter(pk=row.binding_id, revoked_at__isnull=True).update(
                        revoked_at=now, revoke_reason="code_reused"
                    )
        else:
            row.used_at = now
            row.save(update_fields=["used_at"])
            if row.expires_at <= now:
                error = "code_expired"
            elif row.aud != aud:
                error = "aud_mismatch"
            elif not hmac.compare_digest(row.return_origin, origin or ""):
                error = "origin_mismatch"
            elif row.state_hash and not hmac.compare_digest(row.state_hash, digest(state)):
                error = "state_mismatch"
            elif row.session.ended_at is not None:
                error = "session_ended"
            else:
                handle = new_secret()
                binding = DashboardBinding.objects.create(
                    handle_hash=digest(handle),
                    session=row.session,
                    aud=aud,
                    origin=origin,
                    cookie_set_at=now,
                    expires_at=row.session.access_expires_at if aud == AUD_EMBED else None,
                )
                row.binding = binding
                row.save(update_fields=["binding"])
                redemption = Redemption(binding=binding, handle=handle)
    if error is not None:
        raise HandoffError(error)
    assert redemption is not None
    return redemption


def resolve(handle: Optional[str], *, aud: str) -> Optional[Any]:
    """The unrevoked binding of ``aud`` that ``handle`` names (with its session and user), or None.

    Whether the sign-in behind it is still alive is the broker's question
    (:func:`lex.authentication.dashboards.broker.access_token`), asked on every use.
    """
    if not handle:
        return None
    DashboardBinding, _ = _models()
    return (
        DashboardBinding.objects.select_related("session", "session__user")
        .filter(handle_hash=digest(handle), aud=aud, revoked_at__isnull=True)
        .first()
    )


def revoke(binding: Any, reason: str) -> bool:
    """Revoke ``binding`` once; False when it was revoked already."""
    DashboardBinding, _ = _models()
    now = _now()
    updated = DashboardBinding.objects.filter(pk=binding.pk, revoked_at__isnull=True).update(
        revoked_at=now, revoke_reason=reason[:32]
    )
    if binding.revoked_at is None:
        binding.revoked_at = now
        binding.revoke_reason = reason[:32]
    return bool(updated)


def cookie_due(binding: Any, now: Optional[datetime] = None) -> bool:
    """Whether the handle cookie should be re-set (it is re-set at most daily, sliding 30 days)."""
    now = now or _now()
    return binding.cookie_set_at + timedelta(seconds=HANDLE_RESET_INTERVAL) <= now


def mark_cookie_set(binding: Any, now: Optional[datetime] = None) -> None:
    DashboardBinding, _ = _models()
    now = now or _now()
    DashboardBinding.objects.filter(pk=binding.pk).update(cookie_set_at=now)
    binding.cookie_set_at = now


def identity(binding: Any) -> Dict[str, str]:
    """Who the binding signs in as: ``sub``, ``email``, ``preferred_username`` and ``name``.

    From the sign-in's validated profile claims, falling back to the user record. No token.
    """
    session = binding.session
    claims = dict(session.claims or {})
    user = session.user
    username = user.get_username() if hasattr(user, "get_username") else ""
    email = str(claims.get("email") or getattr(user, "email", "") or "")
    preferred = str(claims.get("preferred_username") or username or (email.split("@")[0] if email else ""))
    name = str(claims.get("name") or " ".join(
        part for part in (getattr(user, "first_name", ""), getattr(user, "last_name", "")) if part
    ) or preferred)
    return {
        "sub": str(session.sub or claims.get("sub") or ""),
        "email": email,
        "preferred_username": preferred,
        "name": name,
    }


__all__ = [
    "AUDIENCES",
    "AUD_EMBED",
    "AUD_STREAMLIT",
    "CODE_TTL_SECONDS",
    "HandoffError",
    "Redemption",
    "cookie_due",
    "digest",
    "identity",
    "issue_code",
    "mark_cookie_set",
    "new_secret",
    "redeem",
    "resolve",
    "revoke",
]
