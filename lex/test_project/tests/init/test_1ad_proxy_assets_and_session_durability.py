"""Streamlit's asset bundle is public, cheap to fetch, and still fenced off from everything else.

Intent: Streamlit's frontend is code-split. Version 1.61 ships 365 JS chunks and
names 107 of them in eager ``<link rel="modulepreload">`` tags, so a single page
load is over a hundred asset requests. The proxy in front of it authenticated
every request via one catch-all route, which turned that fan-out into the
framework's three loudest bug reports at once: a flood of 401s in the server
log; a cold start that transferred 1.77 MB of plaintext because the decoded body
forced ``Content-Encoding`` to be stripped; and -- the one customers saw --
``TypeError: Failed to fetch dynamically imported module`` in the browser,
because a *lazily* imported chunk that 401s has no other way to surface. Vite
reports an HTTP failure on a dynamic ``import()`` as a type error, so an expired
credential reached the user as a type error in code they never wrote.

The bundle is therefore served by the proxy itself and never gated. What makes
that safe is that it is package content from the installed wheel -- identical
for every install, no tenant data, no identity -- and the reason it must stay
narrow is that everything *else* on the same host is exactly the opposite. So
the boundary scenarios below matter as much as the public ones: the moment
``/media/**`` or the WebSocket stops requiring a credential, this change has
turned a performance fix into a data leak.

A regression is not subtle in either direction. Gate the bundle again and the
TypeErrors come back; un-gate one path too many and a dashboard is readable by
anyone who knows the URL.

Cluster 1ad — scenarios 1.247–1.269. Type: U.
Covers: lex/proxy.py (_streamlit_static_dir, _build_static_routes,
        _apply_asset_cache_headers, PUBLIC_PROXY_PATHS, public_proxy,
        _get_upstream_client, _upstream_send, _build_proxied_response,
        _ensure_jwks_ready, _fetch_jwks_blocking, _get_jwks, _safe_next_path,
        _login_path, _login_url, _current_relative_path,
        _query_without_auth_token, login, auth_callback, proxy,
        SESSION_SECRET/SESSION_SAMESITE/_build_token_store startup guards),
        lex/bin/lex.py (streamlit launch args, _warn_if_sessions_are_not_durable).
Run: python -m lex pytest lex/test_project/tests/init/test_1ad_proxy_assets_and_session_durability.py -v
"""

from __future__ import annotations

import asyncio
import glob
import gzip
import importlib
import os
from unittest.mock import patch

import httpx
import pytest
from django.test import SimpleTestCase
from starlette.testclient import TestClient

import lex.proxy as proxy

pytestmark = pytest.mark.init


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _a_shipped_chunk() -> str:
    """The filename of a real hashed JS chunk from the installed Streamlit wheel.

    Read off disk rather than hard-coded: the hashes change with every Streamlit
    release, and a test pinned to one would fail for the wrong reason.
    """
    static_dir = proxy._streamlit_static_dir()
    assert static_dir, "the installed streamlit wheel has no static directory"
    matches = sorted(glob.glob(os.path.join(static_dir, "static", "js", "*.js")))
    assert matches, "the installed streamlit wheel ships no JS chunks"
    return os.path.basename(matches[0])


class _RawStream(httpx.AsyncByteStream):
    """A not-yet-consumed byte stream, so a stub response behaves like a real one.

    ``httpx.Response(content=...)`` is born already consumed, and its
    ``.content`` is *decoded* -- which cannot exercise the encoding passthrough,
    because the production path reads raw bytes off an open stream. This gives
    the stub the same shape ``client.send(..., stream=True)`` produces.
    """

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def __aiter__(self):
        yield self._payload

    async def aclose(self) -> None:
        return None


class _Upstream:
    """Stands in for Streamlit, returning a response the caller dictates.

    Patched over ``proxy._upstream_send`` -- the single seam every proxied HTTP
    request leaves through.
    """

    calls: list = []
    response_factory = staticmethod(lambda: httpx.Response(200, content=b"upstream-ok"))

    @classmethod
    def reset(cls, response_factory=None) -> None:
        cls.calls = []
        if response_factory is not None:
            cls.response_factory = staticmethod(response_factory)

    @classmethod
    async def send(cls, method, url, *, content=None, headers=None):
        cls.calls.append({"method": method, "url": str(url), "headers": dict(headers or {})})
        return cls.response_factory()


# ---------------------------------------------------------------------------
# the public bundle: no credential, cached hard, compressed
# ---------------------------------------------------------------------------
class TestCluster01ad_PublicAssetBundle(SimpleTestCase):
    """Cluster 1ad: Streamlit's own static bundle is served without authentication."""

    # -- 1.247 ---------------------------------------------------------
    def test_1_247_a_js_chunk_is_served_without_any_credential(self) -> None:
        """
        Scenario 1.247: a hashed JS chunk is served to a caller holding no credential.
        Given: no session cookie, no auth_token, no Authorization header
        When: the browser requests one of Streamlit's code-split chunks
        Then: it gets 200 and the file's bytes.

        This is the scenario the three bug reports reduce to. Under the old
        catch-all every one of the 107 eagerly-preloaded chunks went through the
        credential check, so one credential-less moment produced a 401 flood --
        and a *lazy* chunk 401ing is what reached the customer as
        "TypeError: Failed to fetch dynamically imported module".
        """
        chunk = _a_shipped_chunk()
        with TestClient(proxy.app) as client:
            resp = client.get(f"/static/js/{chunk}")

        self.assertEqual(
            resp.status_code, 200,
            msg=f"/static/js/{chunk} must be served without a credential, got {resp.status_code}",
        )
        self.assertTrue(resp.content, msg="the chunk must be served with a body")

    # -- 1.248 ---------------------------------------------------------
    def test_1_248_hashed_assets_are_immutable_and_manifest_is_not(self) -> None:
        """
        Scenario 1.248: content-addressed files cache forever; the manifest never does.
        Given: the bundle is served by the proxy
        When: a hashed chunk and manifest.json are fetched
        Then: the chunk is `public, immutable, max-age=<a year>` and manifest.json is `no-cache`.

        Both halves matter, and the second is the one that bites. A cached
        manifest or index.html naming a previous deploy's chunk hashes makes the
        browser ask for files that no longer exist -- which produces exactly the
        dynamic-import TypeErrors this batch exists to remove, from a completely
        different cause. Mirrors Streamlit's own contract in
        starlette_static_routes._apply_cache_headers.
        """
        chunk = _a_shipped_chunk()
        with TestClient(proxy.app) as client:
            hashed = client.get(f"/static/js/{chunk}")
            manifest = client.get("/manifest.json")

        self.assertIn(
            "immutable", hashed.headers.get("cache-control", ""),
            msg="a content-addressed chunk must be immutable, or every reload refetches 365 files",
        )
        self.assertIn(
            f"max-age={proxy.STATIC_ASSET_MAX_AGE}", hashed.headers.get("cache-control", ""),
            msg="the immutable chunk must carry the configured max-age",
        )
        self.assertEqual(
            manifest.headers.get("cache-control"), "no-cache",
            msg="manifest.json must never be cached; a stale one names dead chunk hashes",
        )

    # -- 1.249 ---------------------------------------------------------
    def test_1_249_assets_are_compressed_when_the_client_accepts_it(self) -> None:
        """
        Scenario 1.249: the bundle goes out compressed.
        Given: a client advertising `Accept-Encoding: gzip`
        When: it fetches a chunk large enough to be worth compressing
        Then: the response is gzip-encoded, and materially smaller than the raw file.

        The proxy used to read the decoded upstream body, which forced it to
        strip `Content-Encoding` to stay honest -- silently disabling
        compression for everything Streamlit serves. Measured over the 107
        eagerly-preloaded chunks: 1.77 MB plaintext against 0.42 MB gzipped.
        This asserts the saving exists rather than its exact size, which is a
        property of the installed wheel.
        """
        static_dir = proxy._streamlit_static_dir()
        candidates = sorted(
            glob.glob(os.path.join(static_dir, "static", "js", "*.js")),
            key=os.path.getsize,
            reverse=True,
        )
        biggest = candidates[0]
        raw_size = os.path.getsize(biggest)

        with TestClient(proxy.app) as client:
            resp = client.get(
                f"/static/js/{os.path.basename(biggest)}",
                headers={"Accept-Encoding": "gzip"},
            )

        self.assertEqual(resp.status_code, 200, msg="the chunk must still be served")
        self.assertEqual(
            resp.headers.get("content-encoding"), "gzip",
            msg="a gzip-accepting client must get a compressed bundle, not plaintext",
        )
        # httpx decodes transparently, so compare the wire size we can compute.
        self.assertLess(
            len(gzip.compress(open(biggest, "rb").read(), proxy.STATIC_GZIP_LEVEL)),
            raw_size,
            msg="compression must actually reduce the bytes on the wire",
        )

    # -- 1.250 ---------------------------------------------------------
    def test_1_250_the_bundle_is_located_through_streamlits_own_helper(self) -> None:
        """
        Scenario 1.250: the static directory comes from Streamlit, not a hand-built path.
        Given: the installed Streamlit wheel
        When: the proxy resolves where the bundle lives
        Then: it is the directory `streamlit.file_util.get_static_dir()` reports, and it
              contains the index.html that names the hashes.

        Load-bearing rather than cosmetic. The chunk filenames are content
        hashes that change every release, and index.html is what references
        them. Resolving the directory from the same interpreter that runs the
        Streamlit server is what makes it impossible to serve one release's
        index.html alongside another's chunks -- the mismatch would surface as
        dynamic-import TypeErrors indistinguishable from the original bug.
        """
        from streamlit import file_util

        resolved = proxy._streamlit_static_dir()

        self.assertEqual(
            resolved, file_util.get_static_dir(),
            msg="the proxy must serve exactly the directory Streamlit reports as its own",
        )
        self.assertTrue(
            os.path.isfile(os.path.join(resolved, "index.html")),
            msg="the resolved directory must be the real bundle root (index.html present)",
        )

    # -- 1.251 ---------------------------------------------------------
    def test_1_251_a_missing_bundle_degrades_instead_of_breaking_startup(self) -> None:
        """
        Scenario 1.251: an unlocatable bundle falls back to proxying, it does not crash.
        Given: Streamlit's static directory cannot be resolved
        When: the proxy builds its routes
        Then: it contributes no static routes and raises nothing.

        Refusing to boot would be the wrong trade: a slow, authenticated
        `/static` is bad, an application that will not start is worse. The
        operator is told loudly instead (`_assert_static_bundle_present` logs an
        error naming the consequence), and `requirements.txt` pins the Streamlit
        minor so this should only ever fire on a hand-edited install.
        """
        with patch.object(proxy, "_streamlit_static_dir", lambda: None):
            routes = proxy._build_static_routes()
            proxy._assert_static_bundle_present()  # must not raise

        self.assertEqual(
            routes, [],
            msg="with no bundle the proxy must add no static routes and let the catch-all serve",
        )


# ---------------------------------------------------------------------------
# the boundary: exactly two proxied paths are public, nothing else moved
# ---------------------------------------------------------------------------
class TestCluster01ad_AuthBoundary(SimpleTestCase):
    """Cluster 1ad: un-gating the bundle must not un-gate anything else."""

    # -- 1.252 ---------------------------------------------------------
    def test_1_252_the_two_bootstrap_endpoints_need_no_credential(self) -> None:
        """
        Scenario 1.252: /_stcore/health and /_stcore/host-config answer without a session.
        Given: no credential of any kind
        When: each is requested
        Then: the request reaches the upstream rather than being rejected by auth.

        Neither can require one. Kubernetes probes have no session, and
        host-config is fetched during client bootstrap -- before the WebSocket
        exists, so before any credential could have been established. It returns
        client feature flags only (allowedOrigins, useExternalAuthToken, ...),
        no identity and no tenant data. Asserted by observing that the upstream
        was actually called: a 401 would mean auth stopped it first.
        """
        _Upstream.reset()
        with patch.object(proxy, "_upstream_send", _Upstream.send):
            with TestClient(proxy.app) as client:
                for path in sorted(proxy.PUBLIC_PROXY_PATHS):
                    with self.subTest(path=path):
                        resp = client.get(path)
                        self.assertEqual(
                            resp.status_code, 200,
                            msg=f"{path} must reach the upstream without a credential",
                        )

        reached = {call["url"].rsplit("8080", 1)[-1] for call in _Upstream.calls}
        self.assertEqual(
            reached, {"/_stcore/health", "/_stcore/host-config"},
            msg=f"both bootstrap paths must be forwarded upstream, saw {reached}",
        )

    # -- 1.253 ---------------------------------------------------------
    def test_1_253_public_paths_are_forwarded_without_identity_headers(self) -> None:
        """
        Scenario 1.253: an unauthenticated passthrough asserts no identity.
        Given: /_stcore/health requested with no credential
        When: the proxy forwards it
        Then: none of the X-Streamlit-User-* / X-Forwarded-User headers are attached.

        The authenticated path injects those headers and Streamlit trusts them
        as the caller's identity. If the public path injected them too -- even
        empty -- anything downstream reading them could mistake an anonymous
        probe for a signed-in user.
        """
        _Upstream.reset()
        with patch.object(proxy, "_upstream_send", _Upstream.send):
            with TestClient(proxy.app) as client:
                client.get("/_stcore/health")

        forwarded = {k.lower() for k in _Upstream.calls[0]["headers"]}
        for header in (
            "x-streamlit-user-id",
            "x-streamlit-user-email",
            "x-streamlit-user-username",
            "x-streamlit-auth-method",
            "x-forwarded-user",
            "x-forwarded-access-token",
        ):
            with self.subTest(header=header):
                self.assertNotIn(
                    header, forwarded,
                    msg=f"the public passthrough must not assert an identity via {header}",
                )

    # -- 1.254 ---------------------------------------------------------
    def test_1_254_every_other_path_still_requires_a_credential(self) -> None:
        """
        Scenario 1.254: the paths that carry tenant data still deny anonymous callers.
        Given: no credential
        When: the app root, a media file, the upload endpoint, and an arbitrary app route
              are requested as XHR
        Then: each is refused with 401.

        The negative half of the change, and the one that turns a performance
        fix into a data leak if it ever regresses. `/media/**` serves files the
        dashboard was given, and `/_stcore/upload_file` accepts them.
        """
        for path in ("/", "/media/quarterly-report.xlsx", "/_stcore/upload_file", "/07-tax/2025"):
            with self.subTest(path=path):
                with TestClient(proxy.app) as client:
                    resp = client.get(path, headers={"Accept": "*/*"}, follow_redirects=False)
                self.assertEqual(
                    resp.status_code, 401,
                    msg=f"{path} must still require a credential, got {resp.status_code}",
                )

    # -- 1.255 ---------------------------------------------------------
    def test_1_255_the_websocket_still_requires_a_credential(self) -> None:
        """
        Scenario 1.255: the Streamlit stream socket is not public.
        Given: no credential
        When: a WebSocket upgrade to /_stcore/stream is attempted
        Then: it is refused rather than connected.

        Separate from 1.254 because it is a different code path -- `ws_proxy`,
        not `proxy` -- reached by a different route entry, and the socket is
        where the dashboard's actual data flows.
        """
        from starlette.websockets import WebSocketDisconnect

        with TestClient(proxy.app) as client:
            with self.assertRaises(
                WebSocketDisconnect,
                msg="an unauthenticated WebSocket must be closed, not accepted",
            ):
                with client.websocket_connect("/_stcore/stream"):
                    pass


# ---------------------------------------------------------------------------
# forwarding: pooled, streamed, and byte-faithful
# ---------------------------------------------------------------------------
class TestCluster01ad_UpstreamForwarding(SimpleTestCase):
    """Cluster 1ad: what the proxy does to a response on its way back."""

    # -- 1.256 ---------------------------------------------------------
    def test_1_256_the_upstream_client_is_reused_across_requests(self) -> None:
        """
        Scenario 1.256: one pooled client serves every request, not one per request.
        Given: several sequential proxied requests
        When: each obtains the upstream client
        Then: it is the same object every time.

        The old code opened a fresh `httpx.AsyncClient` per request, so a cold
        start meant 107+ brand-new connections to the upstream with no
        keep-alive -- inside a process that shares one GIL with the Streamlit
        script runner, because `lex streamlit` runs the proxy in a thread
        alongside it.
        """
        async def _collect():
            return [id(await proxy._get_upstream_client()) for _ in range(4)]

        try:
            identities = asyncio.run(_collect())
            self.assertEqual(
                len(set(identities)), 1,
                msg=f"the upstream client must be pooled and reused, saw {len(set(identities))} distinct clients",
            )
        finally:
            asyncio.run(_close_pooled_client())

    # -- 1.257 ---------------------------------------------------------
    def test_1_257_content_encoding_survives_the_proxy(self) -> None:
        """
        Scenario 1.257: an encoded upstream body is passed through still encoded.
        Given: an upstream response carrying `Content-Encoding: gzip`
        When: the proxy relays it
        Then: the header survives, so the client can still decode it.

        This is the compression fix stated as a contract. The old code read the
        *decoded* body and therefore had to drop the header to stay truthful,
        which threw away the whole saving. Keeping the encoded bytes untouched
        costs no CPU and preserves it -- and GZipMiddleware deliberately skips
        responses that already carry the header, so nothing recompresses.
        """
        payload = gzip.compress(b"x" * 4096)
        _Upstream.reset(
            lambda: httpx.Response(
                200,
                headers={"content-encoding": "gzip", "content-type": "application/javascript"},
                stream=_RawStream(payload),
            )
        )
        with patch.object(proxy, "_upstream_send", _Upstream.send):
            with TestClient(proxy.app) as client:
                resp = client.get("/_stcore/health")

        self.assertEqual(
            resp.headers.get("content-encoding"), "gzip",
            msg="Content-Encoding must survive the proxy or compression is silently disabled",
        )

    # -- 1.258 ---------------------------------------------------------
    def test_1_258_every_upstream_set_cookie_survives(self) -> None:
        """
        Scenario 1.258: multiple Set-Cookie headers are all relayed, not collapsed.
        Given: an upstream response setting two cookies
        When: the proxy relays it
        Then: both reach the client.

        Streamlit sets its own cookies, XSRF among them, and a dict-shaped
        header copy keeps only the last of a repeated key. Losing the XSRF
        cookie breaks the WebSocket handshake, which costs the user their
        session state.
        """
        def _two_cookies():
            return httpx.Response(
                200,
                headers=[
                    ("set-cookie", "_streamlit_xsrf=abc; Path=/"),
                    ("set-cookie", "_other=def; Path=/"),
                ],
                stream=_RawStream(b"ok"),
            )

        _Upstream.reset(_two_cookies)
        with patch.object(proxy, "_upstream_send", _Upstream.send):
            with TestClient(proxy.app) as client:
                resp = client.get("/_stcore/health")

        relayed = [c for c in resp.headers.get_list("set-cookie") if "_streamlit_xsrf" in c or "_other" in c]
        self.assertEqual(
            len(relayed), 2,
            msg=f"both upstream cookies must be relayed, got {relayed}",
        )

    # -- 1.259 ---------------------------------------------------------
    def test_1_259_proxied_responses_forbid_leaking_the_referrer(self) -> None:
        """
        Scenario 1.259: a proxied response carries `Referrer-Policy: no-referrer`.
        Given: any response relayed from Streamlit
        When: the client receives it
        Then: the header is present.

        The embedded dashboard is bootstrapped with `?auth_token=<jwt>` in the
        iframe's document URL. Without this, every subresource that document
        requests puts the JWT in a `Referer` header, and any outbound link
        hands it to a third party.
        """
        _Upstream.reset(lambda: httpx.Response(200, content=b"ok"))
        with patch.object(proxy, "_upstream_send", _Upstream.send):
            with TestClient(proxy.app) as client:
                resp = client.get("/_stcore/health")

        self.assertEqual(
            resp.headers.get("referrer-policy"), "no-referrer",
            msg="a proxied response must forbid referrer leakage; the document URL holds a JWT",
        )


async def _close_pooled_client() -> None:
    """Drop the pooled client so a later test builds a fresh one on its own loop."""
    client = proxy._UPSTREAM_CLIENT
    proxy._UPSTREAM_CLIENT = None
    if client is not None and not client.is_closed:
        await client.aclose()


# ---------------------------------------------------------------------------
# JWKS: never block the loop, never drop good keys
# ---------------------------------------------------------------------------
class TestCluster01ad_JwksResilience(SimpleTestCase):
    """Cluster 1ad: fetching signing keys must not stall the loop or invalidate tokens."""

    def setUp(self) -> None:
        self._saved = (proxy._JWKS_CACHE, proxy._JWKS_CACHE_TIME)

    def tearDown(self) -> None:
        proxy._JWKS_CACHE, proxy._JWKS_CACHE_TIME = self._saved

    # -- 1.260 ---------------------------------------------------------
    def test_1_260_a_failed_refresh_keeps_the_cached_keys(self) -> None:
        """
        Scenario 1.260: a refresh that fails leaves the previously-fetched keys in place.
        Given: keys already cached, the TTL lapsed, and Keycloak unreachable
        When: the proxy tries to refresh
        Then: the cached keys are still there, and the retry is deferred.

        The old code returned None on a failed fetch while holding perfectly
        good keys. None propagated to `validate_jwt_token`, which then rejected
        every token it was handed -- so a one-second Keycloak blip landing just
        after the hourly TTL lapse would 401 every request in the cluster, all
        365 asset chunks included, using keys that were still valid. Signing
        keys rotate on the order of months; stale ones beat none.
        """
        proxy._JWKS_CACHE = {"keys": [{"kid": "still-good"}]}
        proxy._JWKS_CACHE_TIME = 0.0  # forces "stale"

        with patch.object(proxy, "KEYCLOAK_URL", "https://keycloak.invalid"), \
             patch.object(proxy, "KEYCLOAK_REALM", "lex"):
            self.assertFalse(proxy._jwks_is_fresh(), msg="precondition: the cache must read as stale")
            asyncio.run(proxy._ensure_jwks_ready())

        self.assertEqual(
            proxy._get_jwks(), {"keys": [{"kid": "still-good"}]},
            msg="a failed refresh must keep the cached keys, or every token in the cluster is rejected",
        )
        self.assertTrue(
            proxy._jwks_is_fresh(),
            msg="the retry must be deferred by the backoff, not attempted on every request",
        )

    # -- 1.261 ---------------------------------------------------------
    def test_1_261_a_cold_cache_plus_a_failure_yields_no_keys_without_raising(self) -> None:
        """
        Scenario 1.261: with nothing cached and Keycloak down, the proxy reports no keys.
        Given: an empty cache and an unreachable Keycloak
        When: the proxy tries to fetch
        Then: it returns no keys and raises nothing.

        The one case where rejecting tokens is correct -- there is genuinely
        nothing to verify against. It must still not raise: an exception here
        would escape into a request handler and become a 500 rather than the
        401 the caller can act on.
        """
        proxy._JWKS_CACHE, proxy._JWKS_CACHE_TIME = None, 0.0

        with patch.object(proxy, "KEYCLOAK_URL", "https://keycloak.invalid"), \
             patch.object(proxy, "KEYCLOAK_REALM", "lex"):
            asyncio.run(proxy._ensure_jwks_ready())  # must not raise

        self.assertIsNone(
            proxy._get_jwks(),
            msg="with no cache and no Keycloak there are no keys to report",
        )

    # -- 1.262 ---------------------------------------------------------
    def test_1_262_concurrent_callers_trigger_exactly_one_fetch(self) -> None:
        """
        Scenario 1.262: a stale cache and many simultaneous requests cause one fetch.
        Given: a stale cache and 25 callers awaiting readiness at once
        When: they all proceed
        Then: the blocking fetch ran exactly once.

        Without single-flight the fan-out is the problem: over a hundred
        preloaded chunks arriving together would each start their own fetch,
        turning one slow Keycloak call into a hundred and starving the loop
        just when the page is trying to paint.
        """
        proxy._JWKS_CACHE, proxy._JWKS_CACHE_TIME = None, 0.0
        fetches = []

        def _record() -> None:
            fetches.append(1)
            proxy._JWKS_CACHE = {"keys": [{"kid": "fetched"}]}
            proxy._JWKS_CACHE_TIME = proxy.time.time()

        async def _stampede():
            await asyncio.gather(*(proxy._ensure_jwks_ready() for _ in range(25)))

        with patch.object(proxy, "_fetch_jwks_blocking", _record):
            asyncio.run(_stampede())

        self.assertEqual(
            len(fetches), 1,
            msg=f"25 concurrent callers must share one fetch, saw {len(fetches)}",
        )

    # -- 1.263 ---------------------------------------------------------
    def test_1_263_token_validation_never_fetches(self) -> None:
        """
        Scenario 1.263: reading the keys is a pure cache read, with no network call.
        Given: an empty JWKS cache
        When: `_get_jwks` is consulted
        Then: it returns None rather than fetching.

        `validate_jwt_token` is synchronous and is called from async handlers.
        It used to fetch inline with a *sync* `httpx.Client` -- a blocking call
        on the event loop for up to ten seconds, during which nothing else in
        the process could progress: not the other in-flight asset requests, and
        not the WebSocket pumps, so a badly-timed refresh could drop a live
        dashboard's socket and cost the user their session state. Keeping the
        read pure is what moves that cost to `_ensure_jwks_ready`, awaited
        off-loop.
        """
        proxy._JWKS_CACHE, proxy._JWKS_CACHE_TIME = None, 0.0

        def _must_not_run(*_args, **_kwargs):
            raise AssertionError("reading the JWKS cache must not perform a fetch")

        with patch.object(proxy.httpx, "Client", _must_not_run):
            self.assertIsNone(
                proxy._get_jwks(),
                msg="a cold cache must read as empty rather than triggering a blocking fetch",
            )


# ---------------------------------------------------------------------------
# coming back to the page you were on
# ---------------------------------------------------------------------------
class TestCluster01ad_LoginReturnPath(SimpleTestCase):
    """Cluster 1ad: re-authenticating must not drop the user on the first page."""

    # -- 1.264 ---------------------------------------------------------
    def test_1_264_the_callback_returns_to_the_stashed_path(self) -> None:
        """
        Scenario 1.264: after the OIDC round trip the user lands where they started.
        Given: `login` stashed a return path in the session
        When: `auth_callback` completes
        Then: it redirects there, not to `/`.

        This is "clicking a button throws me back to the first page", stated as
        a contract. `auth_callback` ended with `RedirectResponse(url="/")` and
        nothing carried a return path, so every re-auth landed on the app root
        -- and because a fresh document is a fresh Streamlit session,
        st.session_state was empty when it got there.
        """
        asyncio.run(self._assert_callback_returns_to("/07-tax/2025?step=2", "/07-tax/2025?step=2"))

    # -- 1.265 ---------------------------------------------------------
    def test_1_265_a_hostile_return_path_is_refused(self) -> None:
        """
        Scenario 1.265: only a same-origin path can be returned to.
        Given: a `next` value pointing off-origin, in each of its usual disguises
        When: the guard validates it
        Then: every one is rejected, and a genuine same-origin path is accepted.

        The value ends up in a `Location` header after an OIDC round trip,
        which is the exact shape of an open redirect -- and a login flow is the
        most valuable place in the app to have one, because the user has just
        proved who they are. Backslashes are included because some browsers
        normalise them to slashes, making `/\\evil.com` an authority.
        """
        hostile = ["//evil.example", "https://evil.example", "http://evil.example",
                   "/\\evil.example", "\\\\evil.example", "evil.example", "javascript:alert(1)"]
        for raw in hostile:
            with self.subTest(next=raw):
                self.assertIsNone(
                    proxy._safe_next_path(raw),
                    msg=f"{raw!r} must never become a redirect target",
                )

        for raw in ("/", "/07-tax/2025", "/07-tax/2025?step=2#top"):
            with self.subTest(next=raw):
                self.assertEqual(
                    proxy._safe_next_path(raw), raw,
                    msg=f"{raw!r} is same-origin and must be preserved",
                )

    # -- 1.266 ---------------------------------------------------------
    def test_1_266_a_poisoned_session_cannot_become_an_open_redirect(self) -> None:
        """
        Scenario 1.266: the callback re-validates, it does not trust the session.
        Given: a session whose stashed return path is off-origin
        When: `auth_callback` completes
        Then: it falls back to `/` instead of honouring it.

        `login` already validates on the way in, so this is defence in depth --
        but the session is the one input to the redirect that an attacker might
        reach by another route, and validating only on entry is how open
        redirects survive code review.
        """
        asyncio.run(self._assert_callback_returns_to("https://evil.example", "/"))

    async def _assert_callback_returns_to(self, stashed: str, expected: str) -> None:
        from starlette.requests import Request

        request = Request({
            "type": "http", "method": "GET", "path": "/auth/callback",
            "query_string": b"", "headers": [], "scheme": "https",
            "server": ("dash.example.com", 443),
            "session": {proxy._NEXT_SESSION_KEY: stashed},
        })

        async def _token(_request):
            return {"access_token": "at", "id_token": "it", "userinfo": {"email": "u@example.com"}}

        with patch.object(proxy.oauth, "oidc") as oidc, \
             patch.object(proxy, "_put_tokens", _noop_put_tokens):
            oidc.authorize_access_token = _token
            resp = await proxy.auth_callback(request)

        self.assertEqual(
            resp.headers["location"], expected,
            msg=f"a stashed {stashed!r} must redirect to {expected!r}, got {resp.headers['location']!r}",
        )

    # -- 1.267 ---------------------------------------------------------
    def test_1_267_the_breakout_offers_a_sign_in_link_carrying_the_return_path(self) -> None:
        """
        Scenario 1.267: the iframe recovery page can get the user back to this view.
        Given: an unauthenticated framed document load of a deep view
        When: the proxy builds the breakout response
        Then: the page's sign-in link is absolute and carries the view as `next`.

        Absolute because that URL is handed to script navigating the *top*
        window, out of a frame whose own origin differs. And it must carry
        `next` for the same reason 1.264 does: a recovery that lands on `/`
        has recovered the session and lost the work.
        """
        from starlette.requests import Request

        request = Request({
            "type": "http", "method": "GET", "path": "/07-tax/2025",
            "query_string": b"step=2", "scheme": "https",
            "server": ("dash.example.com", 443),
            "headers": [(b"accept", b"text/html"), (b"sec-fetch-dest", b"iframe")],
        })

        resp = proxy._unauthenticated_response(request)
        body = resp.body.decode()

        self.assertEqual(resp.status_code, 401, msg="the breakout must be a 401, not a redirect")
        self.assertIn(
            "next=%2F07-tax%2F2025%3Fstep%3D2", body,
            msg="the breakout's sign-in link must carry the view being recovered",
        )
        self.assertIn(
            'target="_top"', body,
            msg="the link must escape the frame; a sandboxed iframe blocks programmatic top nav",
        )


async def _noop_put_tokens(*_args, **_kwargs) -> None:
    return None


# ---------------------------------------------------------------------------
# the bootstrap credential leaves the URL once it has been banked
# ---------------------------------------------------------------------------
class TestCluster01ad_BootstrapTokenHygiene(SimpleTestCase):
    """Cluster 1ad: `?auth_token=` is a handoff, not a place to keep a JWT."""

    # -- 1.268 ---------------------------------------------------------
    def test_1_268_stripping_preserves_every_other_query_parameter(self) -> None:
        """
        Scenario 1.268: removing auth_token leaves the rest of the query intact.
        Given: a URL carrying auth_token alongside the dashboard's own parameters
        When: the redirect target is computed
        Then: the token is gone and `model` and `pk` survive.

        The parameters are how `StreamlitIframe` tells the dashboard which
        record to render, so dropping them would strip the credential and the
        destination together and land the user on a blank dashboard -- trading
        one bug for another.
        """
        from starlette.datastructures import QueryParams
        from starlette.requests import Request

        request = Request({
            "type": "http", "method": "GET", "path": "/",
            "query_string": b"model=Fund&auth_token=SECRET-JWT&pk=42&is_logout_enabled=false",
            "headers": [], "scheme": "https", "server": ("dash.example.com", 443),
        })

        target = proxy._current_relative_path(request)

        self.assertNotIn(
            "SECRET-JWT", target,
            msg="the credential must not survive into the redirect target",
        )
        self.assertNotIn("auth_token", target, msg="the parameter itself must be gone")
        surviving = QueryParams(target.split("?", 1)[1])
        self.assertEqual(
            (surviving.get("model"), surviving.get("pk"), surviving.get("is_logout_enabled")),
            ("Fund", "42", "false"),
            msg=f"every non-credential parameter must survive, got {target!r}",
        )

    # -- 1.269 ---------------------------------------------------------
    def test_1_269_only_a_document_load_is_redirected(self) -> None:
        """
        Scenario 1.269: an XHR carrying auth_token is served, not redirected.
        Given: the same credential presented by a document load and by an XHR
        When: each is handled
        Then: only the document load is a candidate for the strip redirect.

        A document URL is what sits in the address bar, in history, and in the
        `Referer` of every subresource -- that is what the strip is for. An XHR
        URL is in none of those, and redirecting one would surprise a caller
        that asked for data and got a 303. Streamlit's client makes many such
        requests, so the distinction has to hold.
        """
        for dest, expected in (("document", True), ("iframe", True), ("empty", False), ("script", False)):
            with self.subTest(sec_fetch_dest=dest):
                from starlette.requests import Request

                request = Request({
                    "type": "http", "method": "GET", "path": "/",
                    "query_string": b"auth_token=SECRET", "scheme": "https",
                    "server": ("dash.example.com", 443),
                    "headers": [(b"sec-fetch-dest", dest.encode()), (b"accept", b"*/*")],
                })
                self.assertEqual(
                    proxy._is_document_request(request), expected,
                    msg=f"Sec-Fetch-Dest: {dest} must{'' if expected else ' not'} count as a document load",
                )


# ---------------------------------------------------------------------------
# a session's lifetime must not be an accident of process identity
# ---------------------------------------------------------------------------
class TestCluster01ad_SessionDurabilityGuards(SimpleTestCase):
    """Cluster 1ad: configurations that silently log everyone out are refused."""

    # -- 1.270 ---------------------------------------------------------
    def test_1_270_a_deployment_without_a_session_secret_is_refused(self) -> None:
        """
        Scenario 1.270: no SESSION_SECRET on an https deployment fails at startup.
        Given: STREAMLIT_URL is https and no SESSION_SECRET is set
        When: the proxy is imported
        Then: it raises, naming the consequence.

        The old fallback minted a random secret at import. That is not a weak
        secret, it is a *different* secret in every process: cookies signed
        before a restart are undecodable after it, and cookies signed by one
        replica are rejected by the next. Users get bounced to a login they did
        nothing to deserve, which is indistinguishable from the expiry bug this
        batch's siblings fix -- so it has to fail where an operator can read it
        rather than degrade into the same symptom.
        """
        with self.assertRaises(RuntimeError, msg="an https deployment must refuse a random secret") as caught:
            _reimport_proxy_with({"STREAMLIT_URL": "https://dash.example.com", "SESSION_SECRET": None})
        self.assertIn(
            "SESSION_SECRET", str(caught.exception),
            msg="the error must name the variable the operator has to set",
        )

    # -- 1.271 ---------------------------------------------------------
    def test_1_271_the_permitted_configurations_still_boot(self) -> None:
        """
        Scenario 1.271: the guard refuses only what is genuinely broken.
        Given: an https deployment with a secret; the same with an explicit opt-out;
               and a local http run with neither
        When: the proxy is imported
        Then: each boots.

        A guard that also blocks legitimate setups gets disabled wholesale, and
        then protects nothing. Local development in particular must stay
        zero-config -- one process, one secret, nobody minding a restart.
        """
        cases = {
            "https with a fixed secret": {
                "STREAMLIT_URL": "https://dash.example.com", "SESSION_SECRET": "fixed"},
            "https with an explicit opt-out": {
                "STREAMLIT_URL": "https://dash.example.com", "SESSION_SECRET": None,
                "LEX_ALLOW_EPHEMERAL_SESSION_SECRET": "true"},
            "local http, zero config": {
                "STREAMLIT_URL": "http://localhost:8501", "SESSION_SECRET": None},
        }
        for label, env in cases.items():
            with self.subTest(config=label):
                module = _reimport_proxy_with(env)
                self.assertTrue(
                    module.SESSION_SECRET,
                    msg=f"{label} must boot with a usable session secret",
                )

    # -- 1.272 ---------------------------------------------------------
    def test_1_272_replicas_without_a_shared_token_store_are_refused(self) -> None:
        """
        Scenario 1.272: more than one replica requires a shared token store.
        Given: LEX_PROXY_REPLICAS=3 and no Redis URL
        When: the proxy is imported
        Then: it raises; with a Redis URL it boots.

        The token store holds the refresh tokens that keep dashboards alive. In
        memory it is process-local, so a request routed to another replica finds
        no `sid` and returns 401 -- again reaching the user as a session that
        expired for no reason. Unreplicated it is merely fragile, so that case
        warns and continues.
        """
        base = {"STREAMLIT_URL": "https://dash.example.com", "SESSION_SECRET": "fixed"}

        with self.assertRaises(RuntimeError, msg="replicas without Redis must be refused") as caught:
            _reimport_proxy_with({**base, "LEX_PROXY_REPLICAS": "3", "REDIS_URL": None,
                                  "TOKEN_REDIS_URL": None})
        self.assertIn(
            "REDIS", str(caught.exception).upper(),
            msg="the error must name the store the operator has to configure",
        )

        module = _reimport_proxy_with({**base, "LEX_PROXY_REPLICAS": "3",
                                       "REDIS_URL": "redis://localhost:6379/0"})
        self.assertEqual(
            module.PROXY_REPLICAS, 3,
            msg="with a shared store, a replicated deployment must boot",
        )

    # -- 1.273 ---------------------------------------------------------
    def test_1_273_a_samesite_that_browsers_discard_is_refused(self) -> None:
        """
        Scenario 1.273: SameSite=None without Secure fails at startup, and so does a typo.
        Given: SESSION_SAMESITE=none with SESSION_HTTPS_ONLY=false, then a nonsense value
        When: the proxy is imported
        Then: each raises.

        Browsers reject `SameSite=None` without `Secure` outright, so this
        combination does not degrade -- it silently drops every cookie the proxy
        sets and no session is ever established. The dashboard then looks broken
        for no visible reason, with the explanation only in a browser console
        nobody is watching.
        """
        with self.assertRaises(RuntimeError, msg="None without Secure must be refused") as caught:
            _reimport_proxy_with({"SESSION_SAMESITE": "none", "SESSION_HTTPS_ONLY": "false",
                                  "STREAMLIT_URL": "http://localhost:8501"})
        self.assertIn(
            "Secure", str(caught.exception),
            msg="the error must explain that Secure is the missing half",
        )

        with self.assertRaises(RuntimeError, msg="an invalid SameSite must be refused"):
            _reimport_proxy_with({"SESSION_SAMESITE": "sometimes",
                                  "STREAMLIT_URL": "http://localhost:8501"})

    # -- 1.274 ---------------------------------------------------------
    def test_1_274_a_cross_site_deployment_defaults_to_a_cookie_the_frame_receives(self) -> None:
        """
        Scenario 1.274: on https the session cookie defaults to SameSite=None.
        Given: an https deployment with no explicit SESSION_SAMESITE
        When: the proxy resolves its cookie policy
        Then: it is `none`; a local http run stays `lax`.

        The dashboard is loaded in an iframe owned by the React shell, and in a
        cross-site frame the browser compares a cookie's site against the
        *top-level* site. `Lax` therefore withholds the session and `st_access`
        cookies from the frame's own requests to its own origin -- every asset,
        every XHR, the WebSocket handshake. It happens to work today only
        because shell and dashboard share one registrable domain; any customer
        on their own domain would see a dashboard that never authenticates.
        """
        https_module = _reimport_proxy_with(
            {"STREAMLIT_URL": "https://dash.example.com", "SESSION_SECRET": "fixed",
             "SESSION_SAMESITE": None})
        self.assertEqual(
            https_module.SESSION_SAMESITE, "none",
            msg="an https deployment must default to a cookie a cross-site frame receives",
        )

        local_module = _reimport_proxy_with(
            {"STREAMLIT_URL": "http://localhost:8501", "SESSION_SAMESITE": None})
        self.assertEqual(
            local_module.SESSION_SAMESITE, "lax",
            msg="local http cannot satisfy Secure, so it must stay lax",
        )


# ---------------------------------------------------------------------------
# what `lex streamlit` hands to Streamlit
# ---------------------------------------------------------------------------
class TestCluster01ad_LaunchConfiguration(SimpleTestCase):
    """Cluster 1ad: the launch command's session-survival settings."""

    # -- 1.275 ---------------------------------------------------------
    def test_1_275_the_launch_widens_streamlits_disconnected_session_window(self) -> None:
        """
        Scenario 1.275: `lex streamlit` passes a disconnectedSessionTTL well above the default.
        Given: the streamlit launch command
        When: it builds Streamlit's argument list
        Then: `--server.disconnectedSessionTTL` is passed, and it exceeds Streamlit's 120s default.

        Streamlit keeps a disconnected session's `st.session_state` and uploaded
        files for that long and resumes it when the same client reconnects
        carrying its session id. 120s is shorter than a Keycloak round trip that
        has to render a login form, so a re-auth came back to an evicted session
        and the user landed on the first page with their work gone. Asserted as
        "meaningfully longer than the default" rather than an exact number,
        which is a tunable.
        """
        recorded: dict = {}

        def _capture(args):
            recorded["args"] = list(args)

        with patch.dict(os.environ, {"LEX_STREAMLIT_DISCONNECTED_SESSION_TTL": "600"}, clear=False), \
             patch("streamlit.web.cli.main", _capture), \
             patch("threading.Thread") as thread:
            thread.return_value.start.return_value = None
            from click.testing import CliRunner
            from lex.bin.lex import lex as lex_cli

            CliRunner().invoke(lex_cli, ["streamlit"], catch_exceptions=False)

        args = recorded.get("args", [])
        self.assertIn(
            "--server.disconnectedSessionTTL", args,
            msg=f"the launch must widen the disconnected-session window, got {args}",
        )
        ttl = int(args[args.index("--server.disconnectedSessionTTL") + 1])
        self.assertGreater(
            ttl, 120,
            msg=f"the TTL must exceed Streamlit's 120s default to outlast a login round trip, got {ttl}",
        )

    # -- 1.276 ---------------------------------------------------------
    def test_1_276_the_cli_refuses_an_undurable_deployment_on_the_main_thread(self) -> None:
        """
        Scenario 1.276: the launch pre-checks durability itself.
        Given: an https deployment with no SESSION_SECRET
        When: `lex streamlit`'s pre-flight runs
        Then: it raises a ClickException.

        lex/proxy.py is the authority on this rule, but it is imported *in the
        uvicorn thread* -- where a RuntimeError kills only the proxy and leaves
        Streamlit running and unreachable, which reads as "the dashboard is
        broken" rather than "you forgot a variable". Checking the same
        environment on the main thread turns it into a readable CLI error.
        """
        import click

        from lex.bin.lex import _warn_if_sessions_are_not_durable

        env = {"STREAMLIT_URL": "https://dash.example.com"}
        with patch.dict(os.environ, env, clear=False):
            for key in ("SESSION_SECRET", "SESSION_KEY", "SESSION_SECRET_KEY",
                        "LEX_ALLOW_EPHEMERAL_SESSION_SECRET"):
                os.environ.pop(key, None)
            with self.assertRaises(
                click.ClickException,
                msg="the CLI must refuse an https launch with no session secret",
            ) as caught:
                _warn_if_sessions_are_not_durable()

        self.assertIn(
            "SESSION_SECRET", caught.exception.message,
            msg="the CLI error must name the variable to set",
        )


def _reimport_proxy_with(env: dict):
    """Re-import ``lex.proxy`` under ``env``, then restore the live module.

    The guards under test run at import time -- deliberately, so a broken
    deployment fails before it serves a request -- so exercising them means
    re-importing. ``None`` in ``env`` removes a variable. The original module is
    put back afterwards so the rest of the suite keeps the instance it holds
    references to.
    """
    import sys

    saved_module = sys.modules.get("lex.proxy")
    overrides = {k: v for k, v in env.items() if v is not None}
    removals = [k for k, v in env.items() if v is None]

    with patch.dict(os.environ, overrides, clear=False):
        for key in removals:
            os.environ.pop(key, None)
        sys.modules.pop("lex.proxy", None)
        try:
            return importlib.import_module("lex.proxy")
        finally:
            sys.modules["lex.proxy"] = saved_module
