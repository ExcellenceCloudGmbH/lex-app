"""Intent: the SPA bundle must be downloaded once, not once per frame per load.

Every file served out of the React build carried
``Cache-Control: no-store, no-cache, must-revalidate, max-age=0``. ``no-store``
is the strongest form: the browser is forbidden from keeping any copy at all,
so nothing is reused between page loads, and nothing is shared between the page
and the iframes embedded in it.

The bundle is a single chunk of roughly 6 MB, so a Streamlit page with three
lex-app widgets was re-fetching about 24 MB on every load, and the same again on
the next one. Nothing was wrong with the widgets; the bytes simply could not be
kept.

Hashed assets are content-addressed -- ``assets/index-BfLMwcsL.js`` changes name
when its bytes change -- so they are safe to cache forever, and same-origin
iframes share one HTTP cache. What must NOT be cached is anything whose URL
outlives its content: ``index.html``, because it names the hashed files, and
``config.js``, because it is rewritten from the environment per request.

These tests pin both halves. Getting the split backwards is worse than the
original bug: a pinned ``index.html`` points browsers at a build that no longer
exists, and there is no way to publish a correction.

Cluster 01-init, batch 1ag, scenarios 1.302-1.303.

Run:
    python -m lex pytest lex/test_project/tests/init/test_1ag_spa_asset_caching.py
"""

import pytest
from django.conf import settings

pytestmark = pytest.mark.init


def _serve(path: str, root) -> str:
    """Call the view directly and return the Cache-Control it chose.

    Directly rather than through a test Client: going through the URL conf drags
    in the auth middleware and a real build directory, neither of which has
    anything to do with the header policy under test. A synthetic document root
    keeps the assertion about the one decision this view makes.
    """
    from django.test import RequestFactory

    from lex.react.views import serve_react

    request = RequestFactory().get(f"/{path}")
    response = serve_react(request, path, document_root=str(root))
    return response.headers.get("Cache-Control", "")


@pytest.fixture
def build(tmp_path):
    """A minimal SPA build: a shell, an env config, and one hashed asset."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>lex</title>")
    (tmp_path / "config.js").write_text("window.REACT_APP_KEYCLOAK_REALM = undefined\n")
    (tmp_path / "assets" / "index-BfLMwcsL.js").write_text("console.log(1)")
    (tmp_path / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return tmp_path


class TestCluster1ag_SpaAssetCaching:
    """What may be cached forever, and what may never be cached."""

    def test_01_302_hashed_assets_are_cacheable_forever(self, tmp_path):
        """Scenario 1.302: a content-hashed asset is immutable and long-lived.

        Given a build asset whose filename carries its content hash
        When it is served
        Then it is publicly cacheable for a year and marked immutable, so a
             reload does not even revalidate -- which is the difference between
             one 304 per asset per frame and no request at all
        """
        from lex.react.views import _HASHED_ASSET, _IMMUTABLE

        for name in (
            "assets/index-BfLMwcsL.js",
            "assets/index-gYV2k0qb.css",
            "assets/index.es-RDCGkIZI.js",
            "assets/vendor-a1B2c3D4e5.js",
        ):
            assert _HASHED_ASSET.match(name), name

        assert "immutable" in _IMMUTABLE
        assert "max-age=31536000" in _IMMUTABLE
        assert "no-store" not in _IMMUTABLE

    def test_01_302_an_unhashed_file_is_not_pinned(self):
        """Scenario 1.302 (second half): no hash, no long cache.

        The hash is what makes forever-caching safe. A file without one cannot
        be told apart from a future version of itself, and pinning it would put
        it in every user's browser for a year with no way to correct it. So the
        directory alone must not be enough.
        """
        from lex.react.views import _HASHED_ASSET

        for name in (
            "assets/logo.png",          # in assets/, but no hash
            "assets/style.css",
            "index.html",
            "config.js",
            "favicon.ico",
            "mockServiceWorker.js",
            "assets/../index.html",     # traversal must not read as hashed
        ):
            assert not _HASHED_ASSET.match(name), name

    def test_01_303_the_policy_split_holds_end_to_end(self, build):
        """Scenario 1.303: forever for hashed assets, never for the rest.

        Both directions matter, and getting them backwards is worse than the
        original bug. ``index.html`` names the hashed bundles, so a cached copy
        survives a deploy and points the browser at filenames that no longer
        exist -- a blank app with 404s, fixable only by clearing the cache by
        hand. ``config.js`` is rewritten from the environment per request.
        """
        # Cacheable forever: the name carries the content hash.
        hashed = _serve("assets/index-BfLMwcsL.js", build)
        assert "immutable" in hashed and "max-age=31536000" in hashed
        assert "no-store" not in hashed

        # Never cacheable: URL outlives content.
        for path in ("index.html", "config.js"):
            assert "no-store" in _serve(path, build), path

        # An unknown route falls back to the shell and inherits its policy.
        assert "no-store" in _serve("embed/widgets", build)

        # embed.html is a REAL file (a second Vite entry), not a route, so it
        # takes the file branch -- and like index.html it names hashed assets,
        # so it must not be pinned either.
        (build / "embed.html").write_text("<!doctype html><title>widgets</title>")
        assert "no-store" in _serve("embed.html", build)

        # In assets/ but unhashed: not pinned, because a correction could never
        # reach a browser that had cached it.
        assert "no-store" in _serve("assets/logo.png", build)


class TestCluster1ag_ResponseCompression:
    """Text responses are compressed, so a first load is not the full 6 MB."""

    def test_01_303_gzip_is_enabled_and_ordered_correctly(self):
        """Scenario 1.303 (third half): compression is on, and wraps the response.

        The bundle gzips from ~6 MB to ~1.9 MB, which is what a browser pays on
        a cold cache. GZipMiddleware compresses on the way OUT, so it has to sit
        above the middleware whose output it should cover -- placement is part of
        the behaviour, not a style choice.
        """
        gzip = "django.middleware.gzip.GZipMiddleware"
        assert gzip in settings.MIDDLEWARE, "responses ship uncompressed"

        mw = list(settings.MIDDLEWARE)
        assert mw.index(gzip) <= 1, (
            "GZipMiddleware must sit near the top so it wraps the responses below it"
        )
