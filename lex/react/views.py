import os
import posixpath
import re
from pathlib import Path

from django.http import HttpResponse
from django.utils._os import safe_join
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.static import serve as static_serve
from lex.lex_app import settings

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


@ensure_csrf_cookie
@xframe_options_exempt
def serve_react(request, path, document_root=None):
    path = posixpath.normpath(path).lstrip("/")

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
        return _no_store(response)

    # SPA fallback: index.html names the hashed assets, so it must never be a
    # stale copy pointing at a build that no longer exists.
    return _no_store(static_serve(request, "index.html", document_root))
