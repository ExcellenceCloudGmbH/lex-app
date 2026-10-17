"""
Middleware that makes embed sessions work inside cross-site iframes.

Problem
-------
When the React frontend is loaded inside an external iframe (e.g. a ChatGPT
MCP Apps widget) the browser treats every request as *cross-site*.  Django's
session cookie defaults to ``SameSite=Lax`` which means the browser will
**not** send it on the redirect back from Keycloak → the ``/oidc/callback``
view finds an empty session (no ``oidc_next_url``, no OIDC state) and
returns 400.  On top of that, ``X-Frame-Options: DENY`` blocks framing.

Strategy
-------
1. A ``_lex_embed=1`` marker cookie (``SameSite=None; Secure``) is set on
   the first request that carries ``embed=true``.  It survives cross-site
   navigations.
2. On every response where the marker cookie is present (or ``embed=true``
   is in the query string, or the path starts with ``/oidc/``), the
   middleware:
   a. strips the ``X-Frame-Options`` header,
   b. patches the Django **session cookie** and **CSRF cookie** to
      ``SameSite=None; Secure`` so the browser sends them on cross-site
      requests (Keycloak redirects + API fetches from the iframe), and
   c. when ``settings.LEX_EMBED_ALLOWED_ORIGINS`` is non-empty, adds
      ``Content-Security-Policy: frame-ancestors 'self' <origins>`` so only
      those named sites may actually frame lex-app -- stripping
      ``X-Frame-Options`` alone would otherwise exempt every site, not just
      the named ones. An empty/unset list leaves framing exactly as before
      this setting existed.

Middleware position
-------------------
Must be the **first** entry in ``MIDDLEWARE`` so that ``process_response``
runs **last** – after both ``XFrameOptionsMiddleware`` (header) and
``SessionMiddleware`` (cookie) have already written their values.
"""

from django.conf import settings as django_settings
from django.utils.deprecation import MiddlewareMixin

_COOKIE_NAME = "_lex_embed"
_COOKIE_MAX_AGE = 86400  # 24 h – covers the full embed session


def _merge_frame_ancestors(existing_csp: str, directive: str) -> str:
    """Merge a ``frame-ancestors`` directive into an existing CSP header value.

    Replaces an existing ``frame-ancestors`` directive in place if present,
    otherwise appends the new directive. Every other directive is preserved
    verbatim, in its original order -- this must never clobber a CSP another
    part of the stack (or a downstream project) already set.
    """
    if not existing_csp:
        return directive

    parts = [p.strip() for p in existing_csp.split(";") if p.strip()]
    for i, part in enumerate(parts):
        if part.lower().startswith("frame-ancestors"):
            parts[i] = directive
            break
    else:
        parts.append(directive)
    return "; ".join(parts)


class EmbedXFrameOptionsMiddleware(MiddlewareMixin):

    def process_response(self, request, response):
        is_oidc = request.path.startswith("/oidc/")
        embed_param = request.GET.get("embed", "").lower() in ("true", "1")
        embed_cookie = request.COOKIES.get(_COOKIE_NAME) == "1"

        should_exempt = is_oidc or embed_param or embed_cookie

        # Set / refresh the marker cookie so it survives the cross-site
        # redirect through Keycloak.
        if (embed_param or embed_cookie) and not embed_cookie:
            response.set_cookie(
                _COOKIE_NAME,
                "1",
                max_age=_COOKIE_MAX_AGE,
                httponly=True,
                secure=True,
                samesite="None",
            )

        if should_exempt:
            # --- X-Frame-Options ----------------------------------------- #
            response.headers.pop("X-Frame-Options", None)

            # --- Session & CSRF cookies ---------------------------------- #
            # Patch cookies that SessionMiddleware / CsrfViewMiddleware
            # already placed on the response so the browser sends them on
            # cross-site requests (Keycloak OIDC round-trip + API fetches
            # from the iframe).
            session_cookie_name = getattr(
                django_settings, "SESSION_COOKIE_NAME", "sessionid"
            )
            csrf_cookie_name = getattr(
                django_settings, "CSRF_COOKIE_NAME", "csrftoken"
            )
            for name in (session_cookie_name, csrf_cookie_name):
                if name in response.cookies:
                    response.cookies[name]["samesite"] = "None"
                    response.cookies[name]["secure"] = True

            # The CSRF cookie may not be on this response (already in the
            # browser from a previous non-embed visit with SameSite=Lax).
            # Force-set it with SameSite=None so the browser sends it on
            # cross-site requests from the iframe.
            if csrf_cookie_name not in response.cookies:
                csrf_token = request.META.get("CSRF_COOKIE")
                if csrf_token:
                    response.set_cookie(
                        csrf_cookie_name,
                        csrf_token,
                        max_age=getattr(
                            django_settings, "CSRF_COOKIE_AGE", 60 * 60 * 24 * 365
                        ),
                        domain=getattr(django_settings, "CSRF_COOKIE_DOMAIN", None),
                        path=getattr(django_settings, "CSRF_COOKIE_PATH", "/"),
                        secure=True,
                        httponly=getattr(
                            django_settings, "CSRF_COOKIE_HTTPONLY", False
                        ),
                        samesite="None",
                    )

            # --- Named-site embedding (Content-Security-Policy) ----------- #
            # LEX_EMBED_ALLOWED_ORIGINS, when non-empty, restricts who may
            # frame lex-app to the named origins -- `X-Frame-Options` is
            # already gone above, so without this an empty allow-list is the
            # only thing stopping this from exempting every site. See
            # Amendment 14 of the multi-value-filters design.
            allowed_origins = getattr(django_settings, "LEX_EMBED_ALLOWED_ORIGINS", [])
            if allowed_origins:
                directive = "frame-ancestors 'self' " + " ".join(allowed_origins)
                response["Content-Security-Policy"] = _merge_frame_ancestors(
                    response.get("Content-Security-Policy", ""), directive
                )

        return response
