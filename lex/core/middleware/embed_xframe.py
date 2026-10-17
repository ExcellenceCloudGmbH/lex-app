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
      those named sites may actually frame *this* response -- stripping
      ``X-Frame-Options`` alone would otherwise exempt every site, not just
      the named ones. An empty/unset list leaves framing exactly as before
      this setting existed.

Note: this middleware is **not currently registered** in this repo's
``MIDDLEWARE`` (see ``lex/lex_app/settings.py``), so (c) above has no effect
here today -- it matters only for a deployment that does register it. The SPA
document itself (``lex/react/views.py::serve_react``) is ``@xframe_options_exempt``,
so it is never covered by ``XFrameOptionsMiddleware`` *or* this middleware;
``serve_react`` therefore applies the same ``frame-ancestors`` directive to its
own HTML document responses directly (see that module), which is what actually
restricts who may frame lex-app as this repo ships today.

Middleware position
-------------------
Must be the **first** entry in ``MIDDLEWARE`` so that ``process_response``
runs **last** – after both ``XFrameOptionsMiddleware`` (header) and
``SessionMiddleware`` (cookie) have already written their values.
"""

from django.conf import settings as django_settings
from django.utils.deprecation import MiddlewareMixin

from lex.runtime_config import merge_frame_ancestors

_COOKIE_NAME = "_lex_embed"
_COOKIE_MAX_AGE = 86400  # 24 h – covers the full embed session


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
                response["Content-Security-Policy"] = merge_frame_ancestors(
                    response.get("Content-Security-Policy", ""), directive
                )

        return response
