"""The MCP embed ticket: a single-use, 60-second ``lex_embed_ticket`` instead of a token in a URL.

The MCP tool ``lex_embed_view`` puts the app inside an MCP host's iframe (Claude, ChatGPT, VS Code,
...). That frame has none of the user's cookies, so it used to carry the MCP client's access token
in its URL (``auth_token``) -- in history, in logs, in every ``Referer``. Now:

1. :func:`issue_embed_ticket` (called by the tool) validates the MCP access token exactly as a
   bearer request would be, keeps it server-side in a refresh-less ``OIDCSession`` (the library's
   ``sessions.attach_access_token``: encrypted, ends with the token) and mints a single-use
   hand-off code for it (``aud="embed"``, valid 60 s, bound to the frontend origin).
2. The embed URL carries only ``?lex_embed_ticket=<code>``.
3. :class:`EmbedTicketMiddleware` redeems it on the frame's first navigation into a binding,
   sets the ``__Host-lex_embed`` cookie -- ``Max-Age`` no longer than the MCP token lives -- and
   redirects to the same path on the same origin without the ticket. Only a GET redeems (a
   ``HEAD`` from a link prefetcher never spends a ticket). A ticket presented again revokes the
   binding it produced -- unless it comes from the very browser holding that binding's cookie
   (an MCP host rendering the same widget again): that replay is only stripped from the URL.
4. Later requests carrying the cookie (and no sign-in of their own) are authenticated as that
   user, **read-only**: any method other than GET/HEAD/OPTIONS is refused with the contract's
   403 ``permission_denied``. The cookie is ``SameSite=None`` (the frame is cross-site), so a
   write through it would be a cross-site request forgery waiting to happen.

When the token expires, a back-channel logout ends its sign-in, or the binding is revoked, the
cookie stops working at its next use and is removed.
"""
from __future__ import annotations

import logging
import time
from importlib import import_module
from typing import Any, Optional
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpRequest, HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.utils.encoding import escape_uri_path
from django.utils.http import url_has_allowed_host_and_scheme

from lex.authentication.dashboards import cookies, sites

logger = logging.getLogger(__name__)

#: The query parameter carrying the ticket.
TICKET_PARAM = "lex_embed_ticket"
#: A token with less life left than this is not worth an embed.
MIN_TOKEN_SECONDS = 30
#: What the embed cookie may do.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _attach(user: Any, access_token: str, claims: dict):
    """A refresh-less sign-in row holding the MCP access token (ends with the token).

    The library's own ``attach_access_token`` -- built for embeds -- writes the row; its request
    is a throwaway whose session is never saved, because the embed's standing is the cookie the
    ticket is redeemed into, not a Django session.
    """
    from oauth2_authcodeflow import sessions as oidc_sessions

    request = HttpRequest()
    request.session = import_module(settings.SESSION_ENGINE).SessionStore()
    return oidc_sessions.attach_access_token(request, user, access_token, claims)


def issue_embed_ticket(access_token: Optional[str], *, frontend_origin: str) -> Optional[str]:
    """A single-use ticket that signs an embed of ``frontend_origin`` in, read-only, or ``None``.

    ``access_token`` is the MCP caller's; it is validated as any bearer token is (signature,
    issuer, ``typ``, audience, lifetime). ``None`` when it is missing, invalid, about to expire,
    or Keycloak's keys cannot be fetched -- the embed then simply loads signed out.
    """
    from oauth2_authcodeflow.auth import BearerAuthenticationBackend
    from oauth2_authcodeflow.exceptions import OIDCAuthUnavailable, OIDCConfigurationError, OIDCTokenError

    from lex.authentication.dashboards import handoff

    origin = sites.normalize_origin(frontend_origin)
    if not access_token or origin is None:
        return None
    try:
        user, claims = BearerAuthenticationBackend().authenticate_token(access_token)
    except OIDCTokenError as exc:
        logger.warning("No embed ticket: the MCP access token was refused (%s)", exc.reason)
        return None
    except (OIDCAuthUnavailable, OIDCConfigurationError) as exc:
        logger.warning("No embed ticket: the token could not be validated now (%s)", type(exc).__name__)
        return None
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp - time.time() < MIN_TOKEN_SECONDS:
        logger.info("No embed ticket: the MCP access token expires too soon")
        return None
    row = _attach(user, access_token, claims)
    return handoff.issue_code(row, aud=handoff.AUD_EMBED, return_origin=origin)


def _request_origin(request: HttpRequest) -> Optional[str]:
    try:
        return sites.normalize_origin(f"{request.scheme}://{request.get_host()}")
    except Exception:  # DisallowedHost: CommonMiddleware has answered already
        return None


def _without_ticket(request: HttpRequest) -> str:
    """This URL (path and query) without the ticket; repeated parameters are kept.

    Always a path on this origin: ``/%2Fevil.example/`` decodes to ``//evil.example/``, which as a
    ``Location`` would be scheme-relative -- another host. :func:`sites.local_path` collapses it;
    a target that still names any host or scheme becomes ``/``.
    """
    items = [(key, value) for key, values in request.GET.lists() if key != TICKET_PARAM for value in values]
    query = urlencode(items)
    target = escape_uri_path(sites.local_path(request.path)) + (f"?{query}" if query else "")
    try:
        allowed_hosts = {request.get_host()}
    except Exception:  # DisallowedHost: only a path without any host passes
        allowed_hosts = set()
    if not url_has_allowed_host_and_scheme(target, allowed_hosts=allowed_hosts, require_https=request.is_secure()):
        return "/"
    return target


def _no_store(response):
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


def _read_only_refusal():
    return _no_store(JsonResponse(
        {"detail": "This embedded view is read-only.", "code": "permission_denied"}, status=403
    ))


class EmbedTicketMiddleware:
    """Redeem ``lex_embed_ticket`` into the read-only ``__Host-lex_embed`` cookie, and honour it.

    Placed after ``OIDCSessionMiddleware`` (a real sign-in always wins and is never touched) and
    before the login requirement and the permissions middleware (which then see the embed's
    user, roles and UMA permissions from its access token).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest):
        ticket = request.GET.get(TICKET_PARAM)
        if ticket is not None and request.method == "GET":
            return self._redeem(request, ticket)

        clear_cookie = False
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            name = cookies.cookie_name(cookies.EMBED_COOKIE, request.is_secure())
            handle = request.COOKIES.get(name)
            if handle:
                outcome = self._authenticate(request, handle)
                if outcome == "unavailable":
                    from oauth2_authcodeflow import responses as oidc_responses

                    return oidc_responses.auth_unavailable(request)
                clear_cookie = outcome == "ended"

        if getattr(request, "lex_embed", False) and request.method not in SAFE_METHODS:
            return _read_only_refusal()

        response = self.get_response(request)
        if clear_cookie:
            self._clear(response, request)
        return response

    # --- helpers ---------------------------------------------------------------------
    @staticmethod
    def _clear(response, request: HttpRequest) -> None:
        secure = request.is_secure()
        morsel = cookies.django_morsel(
            cookies.cookie_name(cookies.EMBED_COOKIE, secure), "", max_age=0, secure=secure,
            expires="Thu, 01 Jan 1970 00:00:00 GMT",
        )
        response.cookies[morsel.key] = morsel

    def _redeem(self, request: HttpRequest, ticket: str):
        from lex.authentication.dashboards import handoff

        target = _without_ticket(request)
        response = _no_store(HttpResponseRedirect(target))
        # The redeem happens inside the MCP host's frame, like the page it leads to (serve_react
        # is exempt too): no X-Frame-Options: DENY on it.
        response.xframe_options_exempt = True
        origin = _request_origin(request)
        if origin is None:
            return response
        secure = request.is_secure()
        holder = request.COOKIES.get(cookies.cookie_name(cookies.EMBED_COOKIE, secure), "")
        try:
            redemption = handoff.redeem(ticket, aud=handoff.AUD_EMBED, origin=origin, holder_handle=holder)
        except handoff.HandoffError as exc:
            # code_already_redeemed: this browser holds the binding already -- its cookie stays
            logger.info("Embed ticket refused: %s", exc.reason)
            return response
        expires_at = redemption.binding.expires_at
        max_age = int((expires_at - timezone.now()).total_seconds()) if expires_at else 0
        if max_age <= 0:
            handoff.revoke(redemption.binding, "embed_expired")
            return response
        morsel = cookies.django_morsel(
            cookies.cookie_name(cookies.EMBED_COOKIE, secure), redemption.handle, max_age=max_age, secure=secure,
        )
        response.cookies[morsel.key] = morsel
        return response

    @staticmethod
    def _authenticate(request: HttpRequest, handle: str) -> str:
        """``ok`` (request signed in, read-only), ``ended`` or ``unavailable``."""
        from oauth2_authcodeflow.context import OIDCContext
        from oauth2_authcodeflow.exceptions import OIDCAuthUnavailable, OIDCSessionEnded

        from lex.authentication.dashboards import broker, handoff

        binding = handoff.resolve(handle, aud=handoff.AUD_EMBED)
        if binding is None:
            return "ended"
        try:
            broker.access_token(binding)
        except OIDCSessionEnded:
            return "ended"
        except OIDCAuthUnavailable:
            return "unavailable"
        session = binding.session
        user = session.user
        if not getattr(user, "is_active", True):
            handoff.revoke(binding, "user_inactive")
            return "ended"
        request.user = user
        request._cached_user = user
        request.oidc = OIDCContext.from_row(session)
        request.lex_embed = True
        return "ok"


__all__ = ["EmbedTicketMiddleware", "MIN_TOKEN_SECONDS", "SAFE_METHODS", "TICKET_PARAM", "issue_embed_ticket"]
