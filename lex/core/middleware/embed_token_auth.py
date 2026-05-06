"""Middleware that bootstraps a Django session from a ``?auth_token=`` query
parameter.

Purpose
-------
When the MCP Apps widget embeds the React frontend inside a sandboxed
iframe, the iframe has no pre-existing session cookies (VS Code Copilot,
Claude Desktop, etc. don't share the user's browser cookie jar).

The MCP tool already runs in an authenticated context — the
:class:`~lex.mcp_server.context.McpPrincipal` carries the caller's OIDC
``access_token``.  That token is appended to the embed URL as
``?auth_token=<jwt>``.

**Two extraction paths:**

1. **Query-param path** — On the initial page request
   (``/period?embed=true&auth_token=XXX``), the token is read from the
   query string and injected as an ``Authorization: Bearer`` header.
   ``login()`` is called in ``process_response`` to persist the session.

2. **Referer path** — Subsequent sub-resource requests (CSS, JS, config,
   API ``fetch()`` calls) originating from that page carry the full page
   URL in the ``Referer`` header.  The middleware extracts the token from
   there and injects it per-request, avoiding the need for cookies.

This dual approach ensures the embed works even in environments where
third-party cookies are entirely blocked (e.g. VS Code webview iframes).

Security notes
--------------
* The token is a short-lived OIDC access token (same as the one the MCP
  server was authenticated with).
* The token remains in the iframe URL (not the browser address bar) and is
  invisible to the end user.
* Only ``embed=true`` requests are eligible (regular app requests are
  unaffected).
* Referer extraction is limited to same-origin requests (browsers use
  ``strict-origin-when-cross-origin`` by default — cross-origin Referers
  omit query parameters).

Middleware position
-------------------
Must be placed **after** ``AuthenticationMiddleware`` and **before**
``BearerAuthMiddleware`` in ``MIDDLEWARE``.
"""

import logging
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import login as auth_login
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)


def _extract_token_from_referer(request):
    """Try to read ``auth_token`` from the Referer URL (same-origin only).

    Returns the token string or *None*.
    """
    referer = request.META.get("HTTP_REFERER")
    if not referer:
        return None
    try:
        parsed = urlparse(referer)
    except Exception:
        return None
    qs = parse_qs(parsed.query)
    # Only act if the referer is an embed page.
    if qs.get("embed", [""])[0].lower() not in ("true", "1"):
        return None
    tokens = qs.get("auth_token")
    return tokens[0] if tokens else None


class EmbedTokenAuthMiddleware(MiddlewareMixin):

    def process_request(self, request):
        # Skip if user is already authenticated (session cookie worked).
        if hasattr(request, "user") and getattr(request.user, "is_authenticated", False):
            return None

        # ── Path 1: auth_token in query params (initial page load) ──
        auth_token = request.GET.get("auth_token")
        is_embed = request.GET.get("embed", "").lower() in ("true", "1")

        if auth_token and is_embed:
            request.META["HTTP_AUTHORIZATION"] = f"Bearer {auth_token}"
            request._embed_token_injected = True  # triggers login() in process_response
            return None

        # ── Path 2: auth_token in Referer (sub-resource / API requests) ──
        auth_token = _extract_token_from_referer(request)
        if auth_token:
            request.META["HTTP_AUTHORIZATION"] = f"Bearer {auth_token}"
            request._embed_referer_auth = True  # per-request only, no login()
            return None

        return None

    def process_response(self, request, response):
        # Only call login() for the initial page load (query-param path).
        # Referer-based auth is per-request and doesn't need a session.
        if not getattr(request, "_embed_token_injected", False):
            return response

        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            logger.warning("embed_token_auth: token injected but user not authenticated")
            return response

        # Persist the authentication in the session (works when cookies
        # ARE supported, e.g. ChatGPT iframes).
        backend = getattr(user, "backend", None)
        if not backend:
            user.backend = "oauth2_authcodeflow.auth.BearerAuthenticationBackend"
        auth_login(request, user)
        logger.info("embed_token_auth: session created for %s", user)

        # Store the access token in the session for downstream use.
        auth_token = request.GET.get("auth_token", "")
        if auth_token:
            request.session["oidc_access_token"] = auth_token

        return response
