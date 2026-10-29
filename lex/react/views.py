import json
import os
import posixpath
import re
from pathlib import Path

from django.conf import settings as django_settings
from django.http import HttpResponse, JsonResponse
from django.utils._os import safe_join
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.static import serve as static_serve
from lex.lex_app import settings
from lex.runtime_config import frame_ancestors_directive, merge_frame_ancestors

#: A build asset whose filename contains its own content hash, as the bundler
#: emits them: ``assets/index-BfLMwcsL.js``. The hash is the whole point -- the
#: name changes when the bytes change, so the bytes behind one URL can never go
#: stale, and the response is safe to cache forever.
#:
#: The hash is REQUIRED rather than assumed from the directory. An unhashed file
#: that happens to sit in ``assets/`` would otherwise be pinned in every user's
#: browser for a year with no way to publish a correction.
_HASHED_ASSET = re.compile(r"^assets/.+-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+$")

#: One year, the maximum the spec gives meaning to. ``immutable`` additionally
#: tells the browser not to revalidate on reload, which is the difference between
#: a 304 round-trip per asset and no request at all.
_IMMUTABLE = "public, max-age=31536000, immutable"


def _api_path(path) -> bool:
    """Whether ``path`` (below ``DJANGO_BASE_PATH``) lies under ``api/``."""
    base = os.getenv("DJANGO_BASE_PATH", "").strip().strip("/")
    if base and (path == base or path.startswith(f"{base}/")):
        path = path[len(base):].lstrip("/")
    return path == "api" or path.startswith("api/")


def _no_store(response):
    """Forbid caching -- for anything whose URL outlives its content.

    ``index.html`` names the hashed assets, so a stale copy would point a
    browser at a build that no longer exists. ``config.js`` is rewritten per
    request from the environment. Both must be fetched every time.
    """
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def _apply_embed_csp(response):
    """Restrict who may frame an HTML document response to
    ``settings.LEX_EMBED_ALLOWED_ORIGINS``.

    ``serve_react`` is ``@xframe_options_exempt`` -- it must allow being framed
    at all when ``embed=true`` -- so Django's own ``XFrameOptionsMiddleware``
    never protects its documents, and nor does ``EmbedXFrameOptionsMiddleware``
    unless a deployment has registered it. This view's own HTML documents (the
    SPA fallback, and any other real ``.html``/``.htm`` file it serves, e.g.
    ``embed.html``) are therefore the one place that actually needs the
    restriction applied directly. Call only for documents -- never for assets
    or ``config.js``, where a framing restriction has no meaning.

    Reads ``django.conf.settings`` (not the ``lex.lex_app.settings`` module
    ``settings.repo_name`` above uses) specifically so ``override_settings``
    reaches it in tests -- the two are independently-imported objects in this
    codebase, confirmed empirically; see ``test_1az_embed_allowed_origins.py``.

    Unset *and* not configured leaves the response untouched, exactly as
    before this setting existed. Configured but with nothing valid in it
    still restricts to ``frame-ancestors 'self'`` (fail closed) rather than
    behaving as if unset -- see ``LEX_EMBED_ORIGINS_CONFIGURED``.

    Gotcha this creates: this sets ``Content-Security-Policy`` from inside the
    view, before any middleware's response phase runs. Django's own
    ``django.middleware.csp.ContentSecurityPolicyMiddleware`` (``SECURE_CSP``)
    skips adding its header when one is already present -- so a deployment
    that both enables ``SECURE_CSP`` *and* configures
    ``LEX_EMBED_ALLOWED_ORIGINS`` would silently lose its site-wide policy on
    every SPA document, left with only the narrow ``frame-ancestors`` directive
    set here. Not handled by this function; flagged for whoever wires the two
    together.
    """
    allowed_origins = getattr(django_settings, "LEX_EMBED_ALLOWED_ORIGINS", None) or []
    origins_configured = getattr(django_settings, "LEX_EMBED_ORIGINS_CONFIGURED", False)
    if not allowed_origins and not origins_configured:
        return response
    directive = frame_ancestors_directive(allowed_origins)
    response["Content-Security-Policy"] = merge_frame_ancestors(
        response.get("Content-Security-Policy", ""), directive
    )
    return response


@ensure_csrf_cookie
@xframe_options_exempt
def serve_react(request, path, document_root=None):
    path = posixpath.normpath(path).lstrip("/")
    from lex.core.security_flags import strict

    if strict("NOT_FOUND") and _api_path(path):
        # No route under api/ matched: an unknown endpoint answers a JSON 404,
        # never the app's index.html with 200 (LEX_SEC_NOT_FOUND).
        return _no_store(JsonResponse({"detail": "Not found.", "error": "Not found."}, status=404))

    if path == "config.js":
        config_path = safe_join(document_root, path)
        with open(config_path, 'r') as file:
            content = file.read()

        # Replace placeholders with actual environment variable values
        replacements = {
            'undefined': {  # Only replace 'undefined' entries
                'REACT_APP_KEYCLOAK_REALM': os.getenv('KEYCLOAK_REALM'),
                'REACT_APP_KEYCLOAK_URL': os.getenv('KEYCLOAK_URL'),
                'REACT_APP_KEYCLOAK_CLIENT_ID': os.getenv('KEYCLOAK_CLIENT_ID'),
                'REACT_APP_STORAGE_TYPE': os.getenv('STORAGE_TYPE', "LEGACY"),
                'REACT_APP_DOMAIN_BASE': os.getenv("REACT_APP_DOMAIN_BASE", "localhost"),
                'REACT_APP_PROJECT_DISPLAY_NAME': os.getenv('PROJECT_DISPLAY_NAME', settings.repo_name),
                'REACT_APP_GRAFANA_DASHBOARD_URL': os.getenv("REACT_APP_GRAFANA_DASHBOARD_URL", "localhost"),
            }
        }

        for key, value in replacements['undefined'].items():
            content = content.replace(f"window.{key} = undefined", f"window.{key} = \"{value}\"")

        # Comma-joined allowed embed origins, from the validated LEX_EMBED_ALLOWED_ORIGINS
        # setting -- not a raw env var. Read from django.conf.settings (not the
        # lex.lex_app.settings module `settings.repo_name` above uses) so
        # override_settings reaches it in tests -- see _apply_embed_csp's docstring.
        # Escaped with json.dumps (not the naive f'"{value}"' used above) so a value
        # can never break out of the JS string literal; origins are already validated,
        # but this is cheap defence in depth. A no-op when the frontend build predates
        # this placeholder.
        embed_origins = ",".join(getattr(django_settings, "LEX_EMBED_ALLOWED_ORIGINS", None) or [])
        content = content.replace(
            "window.REACT_APP_EMBED_ALLOWED_ORIGINS = undefined",
            f"window.REACT_APP_EMBED_ALLOWED_ORIGINS = {json.dumps(embed_origins)}",
        )

        response = HttpResponse(content, content_type='application/javascript')
        # Rewritten from the environment on every request, so never cached.
        return _no_store(response)

    fullpath = Path(safe_join(document_root, path))
    if fullpath.is_file():
        response = static_serve(request, path, document_root)
        if _HASHED_ASSET.match(path):
            # Content-addressed, so cacheable forever. This is not a micro
            # optimisation: every one of these was previously sent `no-store`,
            # which forbids the browser from keeping ANY copy. The SPA bundle is
            # a single ~6 MB chunk, so a page embedding N lex-app iframes
            # re-downloaded it N+1 times on every load -- 24 MB for three
            # widgets, and again on the next reload. Same-origin iframes share
            # the HTTP cache, so this collapses that to one fetch per build.
            response["Cache-Control"] = _IMMUTABLE
            return response
        # An unhashed file cannot be told apart from a future version of itself.
        response = _no_store(response)
        if path.lower().endswith((".html", ".htm")):
            # A real, non-fallback HTML entry point (e.g. embed.html) is a
            # document too -- restrict who may frame it the same as index.html.
            # Case-insensitive: a build is free to emit either case/extension.
            response = _apply_embed_csp(response)
        return response

    # SPA fallback: index.html names the hashed assets, so it must never be a
    # stale copy pointing at a build that no longer exists. It is also the
    # document every client-routed path ultimately renders, so it is the one
    # that needs the frame-ancestors restriction applied.
    return _apply_embed_csp(_no_store(static_serve(request, "index.html", document_root)))
