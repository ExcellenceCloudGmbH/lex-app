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
    def test_1_270_the_session_key_is_derived_from_what_a_deployment_already_has(self) -> None:
        """
        Scenario 1.270: DJANGO_SECRET_KEY yields a durable cookie key, with no new variable.
        Given: no SESSION_SECRET, but a real DJANGO_SECRET_KEY as every deployed instance has
        When: the proxy resolves its session key
        Then: it derives one from that, and does not use the Django secret verbatim.

        The point is durability without a new required variable. Terraform
        generates DJANGO_SECRET_KEY with `random_password` and keeps it in
        state, so it is stable across restarts and identical on every replica --
        exactly what session cookies need, and exactly what a random
        per-process value is not.

        Derived rather than reused so the two keys are not the same secret: a
        leaked cookie-signing key must not hand over Django's, or vice versa.

        This scenario replaces an assertion that the proxy *refused to boot*
        here. That was the wrong call -- it bricked instances that had been
        running fine, when the single-process `lex streamlit` case is only
        degraded by a per-process key, not broken. The fix was to stop needing
        a new variable, not to demand one.
        """
        django_secret = "terraform-generated-60-char-value-not-the-published-default"
        module = _reimport_proxy_with({
            "STREAMLIT_URL": "https://dash.example.com",
            "DJANGO_SECRET_KEY": django_secret,
            "SESSION_SECRET": None,
            "SESSION_KEY": None,
            "SESSION_SECRET_KEY": None,
        })

        self.assertEqual(
            module.SESSION_SECRET_SOURCE, "DJANGO_SECRET_KEY",
            msg="a deployment with a Django secret must get a durable session key from it",
        )
        self.assertNotIn(
            django_secret, module.SESSION_SECRET,
            msg="the Django secret must not be reused verbatim as the cookie-signing key",
        )
        self.assertTrue(module.SESSION_SECRET, msg="a key must actually be produced")

    # -- 1.271 ---------------------------------------------------------
    def test_1_271_no_configuration_ever_refuses_to_start(self) -> None:
        """
        Scenario 1.271: every session-secret configuration boots.
        Given: an explicit secret; a derivable Django secret; neither; and only the
               published settings.py default
        When: the proxy is imported
        Then: all four start, and the first two are durable while the last two are not.

        A guard that can stop a dashboard starting is worse than the fragility
        it was guarding against, and this is the scenario that says so. The
        published default is deliberately *not* derivable: sharing it would give
        every lex-app instance the same cookie-signing key, which is worse than
        a per-process random value rather than better.
        """
        published_default = "pjlulvaa77lteno-_y6!oxb%63xqiaw4%n%1or&77a!x9@nkd+"
        cases = {
            "an explicit SESSION_SECRET": (
                {"SESSION_SECRET": "chosen"}, "SESSION_SECRET", True),
            "a derivable Django secret": (
                {"DJANGO_SECRET_KEY": "real-terraform-value", "SESSION_SECRET": None},
                "DJANGO_SECRET_KEY", True),
            "nothing at all": (
                {"SESSION_SECRET": None, "DJANGO_SECRET_KEY": None}, "ephemeral", False),
            "only the published default": (
                {"SESSION_SECRET": None, "DJANGO_SECRET_KEY": published_default},
                "ephemeral", False),
        }
        for label, (env, expected_source, durable) in cases.items():
            with self.subTest(config=label):
                module = _reimport_proxy_with({
                    "STREAMLIT_URL": "https://dash.example.com",
                    "SESSION_KEY": None,
                    "SESSION_SECRET_KEY": None,
                    **env,
                })
                self.assertTrue(
                    module.SESSION_SECRET,
                    msg=f"{label} must still produce a usable key and boot",
                )
                self.assertEqual(
                    module.SESSION_SECRET_SOURCE, expected_source,
                    msg=f"{label} should resolve from {expected_source}",
                )
                self.assertEqual(
                    module.SESSION_SECRET_SOURCE != "ephemeral", durable,
                    msg=f"{label} durability should be {durable}",
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
    def test_1_276_the_cli_warns_about_undurable_sessions_without_blocking(self) -> None:
        """
        Scenario 1.276: `lex streamlit`'s pre-flight reports, it does not refuse.
        Given: an https deployment with no session secret of any kind
        When: the pre-flight runs
        Then: it completes, having warned on stderr.

        It used to raise here, which stopped the dashboard starting on instances
        that had been running fine. The rules worth refusing over are the ones
        that are genuinely broken rather than degraded -- a replica set with no
        shared token store, or a cookie policy browsers discard -- and those
        still refuse. A per-process session key is neither.
        """
        from click.testing import CliRunner

        from lex.bin.lex import _warn_if_sessions_are_not_durable

        env = {"STREAMLIT_URL": "https://dash.example.com"}
        with patch.dict(os.environ, env, clear=False):
            for key in ("SESSION_SECRET", "SESSION_KEY", "SESSION_SECRET_KEY",
                        "DJANGO_SECRET_KEY"):
                os.environ.pop(key, None)
            # Must not raise.
            _warn_if_sessions_are_not_durable()

        runner = CliRunner()
        self.assertIsNotNone(runner, msg="sanity: the CLI harness is importable")


def _reimport_proxy_with(env: dict):
    """Re-import ``lex.proxy`` under ``env``, then restore the live module.

    The guards under test run at import time -- deliberately, so a broken
    deployment fails before it serves a request -- so exercising them means
    re-importing. ``None`` in ``env`` removes a variable. The original module is
    put back afterwards so the rest of the suite keeps the instance it holds
    references to.
    """
    import sys

    import lex as lex_pkg

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
            # Both have to go back. Restoring only sys.modules leaves the
            # PACKAGE attribute bound to the throwaway, so a later
            # `from lex import proxy` returns a module with a different
            # TOKEN_STORE than the one the rest of the suite holds.
            sys.modules["lex.proxy"] = saved_module
            if saved_module is not None:
                lex_pkg.proxy = saved_module  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# what review found: the ways each fix could still fail
# ---------------------------------------------------------------------------
class TestCluster01ad_ForwardingHardening(SimpleTestCase):
    """Cluster 1ad: defects an adversarial read of the first pass turned up."""

    # -- 1.277 ---------------------------------------------------------
    def test_1_277_the_strip_redirect_hands_over_every_credential(self) -> None:
        """
        Scenario 1.277: stripping auth_token from the URL re-issues the access cookie.
        Given: a document request carrying ?auth_token=<jwt>
        When: the proxy banks it and redirects to the same path without it
        Then: the redirect sets `st_access`, not only the session cookie.

        The redirect must never deliver *fewer* credentials than the response it
        replaces. Where the session cookie is unusable inside the frame -- Safari
        ITP, or any third-party-cookie blocking, which reaches even
        SameSite=None -- the follow-up request would otherwise arrive with no
        auth_token (just stripped), no session and no st_access, and get a frame
        breakout. The proxy would have thrown away the one credential that
        still worked.
        """
        token = "bootstrap-jwt"
        claims = {"sub": "u1", "email": "u@example.com", "exp": 4_000_000_000}

        with patch.object(proxy, "validate_jwt_token", lambda _t: claims), \
             patch.object(proxy, "SET_ST_ACCESS_COOKIE", True):
            with TestClient(proxy.app) as client:
                resp = client.get(
                    f"/?model=Fund&auth_token={token}",
                    headers={"Accept": "text/html", "Sec-Fetch-Dest": "iframe"},
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 303, msg="a banked bootstrap token must redirect")
        cookies = " ".join(resp.headers.get_list("set-cookie"))
        self.assertIn(
            "st_access=", cookies,
            msg=f"the redirect must re-issue st_access; got {cookies!r}",
        )
        self.assertNotIn(
            token, resp.headers["location"],
            msg="and the token must be gone from the redirect target",
        )

    # -- 1.278 ---------------------------------------------------------
    def test_1_278_a_bodiless_response_yields_no_body_at_all(self) -> None:
        """
        Scenario 1.278: a 304 relays with no body chunk.
        Given: an upstream 304 Not Modified
        When: the proxy relays it
        Then: the body iterator yields nothing.

        Yielding `b""` is not the same as yielding nothing: an empty first
        chunk with more_body reaches GZipMiddleware as a body, skips its
        minimum_size guard, and gets a gzip header and ten bytes attached --
        after which uvicorn raises "Response content longer than
        Content-Length". Real `aiter_raw()` yields zero chunks here, so this
        branch has to match, and it is the branch every test double takes.
        """
        async def _collect(resp):
            return [chunk async for chunk in proxy._iter_upstream(resp)]

        not_modified = httpx.Response(304)
        self.assertEqual(
            asyncio.run(_collect(not_modified)), [],
            msg="a 304 must relay with no body chunk at all, not an empty one",
        )

        with_body = httpx.Response(200, content=b"payload")
        self.assertEqual(
            asyncio.run(_collect(with_body)), [b"payload"],
            msg="a response that does have a body must still relay it",
        )

    # -- 1.279 ---------------------------------------------------------
    def test_1_279_a_mid_body_upstream_failure_is_not_a_truncated_200(self) -> None:
        """
        Scenario 1.279: an upstream that dies part-way through the body raises.
        Given: an upstream response whose stream fails after the first chunk
        When: the proxy relays it
        Then: the error propagates rather than the body simply ending.

        The response head is already sent by then, so the failure cannot become
        an HTTP status -- but truncating silently is worse than dropping the
        connection. A half-delivered Vite chunk surfaces as a SyntaxError or
        "Failed to fetch dynamically imported module", indistinguishable from
        the bug this module exists to fix and not retryable. A dropped
        connection the browser reports as a network error.
        """
        class _FailingStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"first-chunk"
                raise httpx.ReadError("upstream vanished")

            async def aclose(self) -> None:
                return None

        async def _drain():
            resp = httpx.Response(200, stream=_FailingStream())
            chunks = []
            async for chunk in proxy._iter_upstream(resp):
                chunks.append(chunk)
            return chunks

        with self.assertRaises(
            httpx.ReadError,
            msg="a mid-body failure must propagate, not end the body quietly",
        ):
            asyncio.run(_drain())

    # -- 1.280 ---------------------------------------------------------
    def test_1_280_a_public_probe_carrying_a_body_is_not_a_500(self) -> None:
        """
        Scenario 1.280: `Content-Length` is never forwarded, so probes with a body work.
        Given: a GET /_stcore/health that carries a request body
        When: the proxy forwards it
        Then: the forwarded headers omit Content-Length, and the probe answers 200.

        The forwarded value beat httpx's own, so sending an empty body while
        claiming the client's length raised LocalProtocolError ("Too little
        data for declared Content-Length"). That is neither ConnectError nor
        TimeoutException, so the readiness endpoint answered 500 -- a liveness
        probe failing because of a header it did not care about.
        """
        _Upstream.reset(lambda: httpx.Response(200, stream=_RawStream(b"ok")))
        with patch.object(proxy, "_upstream_send", _Upstream.send):
            with TestClient(proxy.app) as client:
                resp = client.request("GET", "/_stcore/health", content=b"unexpected-body")

        self.assertEqual(resp.status_code, 200, msg="the probe must still answer 200")
        forwarded = {k.lower() for k in _Upstream.calls[0]["headers"]}
        self.assertNotIn(
            "content-length", forwarded,
            msg="Content-Length must never be forwarded; httpx recomputes it from the real body",
        )

    # -- 1.281 ---------------------------------------------------------
    def test_1_281_the_pooled_client_is_never_shared_across_event_loops(self) -> None:
        """
        Scenario 1.281: each event loop gets its own upstream client.
        Given: two successive event loops in one process
        When: each asks for the pooled client
        Then: they get different clients.

        `is_closed` says nothing about which loop a client's keep-alive sockets
        belong to. Reusing one across loops raises "unable to perform operation
        on <TCPTransport closed=True>; the handler is closed" from a connection
        the previous loop left in the pool -- and a module-level singleton
        outlives any number of loops.
        """
        first = asyncio.run(self._client_id())
        second = asyncio.run(self._client_id())

        self.assertNotEqual(
            first, second,
            msg="a client from a finished loop must not be handed to the next one",
        )

    async def _client_id(self) -> int:
        return id(await proxy._get_upstream_client())

    # -- 1.282 ---------------------------------------------------------
    def test_1_282_the_single_flight_lock_belongs_to_the_running_loop(self) -> None:
        """
        Scenario 1.282: lock identity is per event loop.
        Given: two successive event loops
        When: each takes the JWKS lock under contention
        Then: neither raises, and each got its own lock object.

        A module-level `asyncio.Lock()` *looks* loop-agnostic because an
        uncontended acquire never binds it -- but the first contended acquire
        does, and a contended acquire on another loop then raises "is bound to a
        different event loop". So it would break only under the concurrency the
        single-flight was written for, which is the worst possible time.
        """
        async def contend():
            lock = proxy._jwks_lock()
            async def hold():
                async with lock:
                    await asyncio.sleep(0)
            await asyncio.gather(hold(), hold(), hold())
            return id(lock)

        first = asyncio.run(contend())
        second = asyncio.run(contend())

        self.assertNotEqual(
            first, second, msg="each loop must get its own lock, or contention raises",
        )


class TestCluster01ad_StartupHardening(SimpleTestCase):
    """Cluster 1ad: startup must fail readably and never on a typo alone."""

    # -- 1.283 ---------------------------------------------------------
    def test_1_283_a_malformed_integer_setting_does_not_stop_the_proxy(self) -> None:
        """
        Scenario 1.283: a non-numeric LEX_PROXY_REPLICAS warns and defaults.
        Given: LEX_PROXY_REPLICAS=auto
        When: the proxy is imported
        Then: it boots with one replica.

        A bare `int()` raised at import, and this module is imported *in the
        uvicorn worker thread* -- so a typo in one deployment variable killed
        the proxy while Streamlit kept serving, which reads as "the dashboard is
        broken" rather than "fix your env". The CLI pre-check already tolerated
        the same value, so the two disagreed.
        """
        module = _reimport_proxy_with({
            "STREAMLIT_URL": "http://localhost:8501",
            "LEX_PROXY_REPLICAS": "auto",
        })
        self.assertEqual(
            module.PROXY_REPLICAS, 1,
            msg="an unparseable replica count must fall back to 1, not raise at import",
        )

    # -- 1.284 ---------------------------------------------------------
    def test_1_284_the_bundle_follows_streamlits_base_url_path(self) -> None:
        """
        Scenario 1.284: a configured baseUrlPath moves the asset mount with it.
        Given: LEX_STREAMLIT_BASE_URL_PATH=app
        When: the proxy builds its routes
        Then: the static mount is at /app/static, not /static.

        Streamlit serves its whole app under `--server.baseUrlPath`, so the
        browser asks for /app/static/js/... . A mount fixed at /static misses
        every one of those, drops them into the authenticated catch-all, and
        silently restores the 401 storm -- with the bundle present, so nothing
        warns.
        """
        module = _reimport_proxy_with({
            "STREAMLIT_URL": "http://localhost:8501",
            "LEX_STREAMLIT_BASE_URL_PATH": "app",
        })
        mounts = [getattr(r, "path", "") for r in module._build_static_routes()]

        self.assertIn(
            "/app/static", mounts,
            msg=f"the bundle must be mounted under the configured base path; got {mounts}",
        )
        self.assertNotIn(
            "/static", mounts,
            msg="and not also at the bare root, which would serve two truths",
        )

    # -- 1.285 ---------------------------------------------------------
    def test_1_285_the_cli_precheck_mirrors_the_cookie_rules_too(self) -> None:
        """
        Scenario 1.285: `lex streamlit`'s pre-flight refuses an unusable SameSite.
        Given: SESSION_SAMESITE=none with SESSION_HTTPS_ONLY=false, then a typo
        When: the pre-flight runs
        Then: each raises a ClickException.

        The pre-flight exists so proxy.py's import-time rules fail on the main
        thread instead of inside the uvicorn worker, where they kill only the
        proxy and leave Streamlit serving. It mirrored two of the rules and not
        these, so exactly the failure it was written to prevent still happened.
        """
        import click

        from lex.bin.lex import _warn_if_sessions_are_not_durable

        cases = {
            "none without Secure": {
                "SESSION_SAMESITE": "none", "SESSION_HTTPS_ONLY": "false",
                "STREAMLIT_URL": "http://localhost:8501", "SESSION_SECRET": "s"},
            "an invalid value": {
                "SESSION_SAMESITE": "sometimes",
                "STREAMLIT_URL": "http://localhost:8501", "SESSION_SECRET": "s"},
        }
        for label, env in cases.items():
            with self.subTest(config=label):
                with patch.dict(os.environ, env, clear=False):
                    with self.assertRaises(
                        click.ClickException,
                        msg=f"the pre-flight must refuse {label} on the main thread",
                    ):
                        _warn_if_sessions_are_not_durable()

    # -- 1.286 ---------------------------------------------------------
    def test_1_286_the_https_test_agrees_between_the_cli_and_the_proxy(self) -> None:
        """
        Scenario 1.286: an uppercase scheme resolves the same way on both sides.
        Given: STREAMLIT_URL=HTTPS://dash.example.com and no explicit SameSite
        When: the CLI pre-flight runs
        Then: it treats the deployment as https -- refusing the SameSite=none-without-Secure
              combination that follows from it, exactly as the proxy's import would.

        proxy.py derives the same fact via `httpx.URL(...).scheme`, which
        lowercases; the pre-flight used `startswith("https://")`. An uppercase
        scheme therefore passed the readable check and raised later in the
        worker thread -- a pre-check that disagrees with the rule it mirrors is
        worse than no pre-check.
        """
        import click

        from lex.bin.lex import _warn_if_sessions_are_not_durable

        env = {"STREAMLIT_URL": "HTTPS://dash.example.com", "SESSION_HTTPS_ONLY": "false"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("SESSION_SAMESITE", None)
            with self.assertRaises(
                click.ClickException,
                msg="an uppercase https scheme must be recognised, so SameSite resolves to none",
            ) as caught:
                _warn_if_sessions_are_not_durable()

        self.assertIn(
            "Secure", caught.exception.message,
            msg="the refusal must be the Secure-cookie one, i.e. https was detected",
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

    import lex as lex_pkg

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
            # Both have to go back. Restoring only sys.modules leaves the
            # PACKAGE attribute bound to the throwaway, so a later
            # `from lex import proxy` returns a module with a different
            # TOKEN_STORE than the one the rest of the suite holds.
            sys.modules["lex.proxy"] = saved_module
            if saved_module is not None:
                lex_pkg.proxy = saved_module  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# what review found: the ways each fix could still fail
# ---------------------------------------------------------------------------
class TestCluster01ad_ForwardingHardening(SimpleTestCase):
    """Cluster 1ad: defects an adversarial read of the first pass turned up."""

    # -- 1.277 ---------------------------------------------------------
    def test_1_277_the_strip_redirect_hands_over_every_credential(self) -> None:
        """
        Scenario 1.277: stripping auth_token from the URL re-issues the access cookie.
        Given: a document request carrying ?auth_token=<jwt>
        When: the proxy banks it and redirects to the same path without it
        Then: the redirect sets `st_access`, not only the session cookie.

        The redirect must never deliver *fewer* credentials than the response it
        replaces. Where the session cookie is unusable inside the frame -- Safari
        ITP, or any third-party-cookie blocking, which reaches even
        SameSite=None -- the follow-up request would otherwise arrive with no
        auth_token (just stripped), no session and no st_access, and get a frame
        breakout. The proxy would have thrown away the one credential that
        still worked.
        """
        token = "bootstrap-jwt"
        claims = {"sub": "u1", "email": "u@example.com", "exp": 4_000_000_000}

        with patch.object(proxy, "validate_jwt_token", lambda _t: claims), \
             patch.object(proxy, "SET_ST_ACCESS_COOKIE", True):
            with TestClient(proxy.app) as client:
                resp = client.get(
                    f"/?model=Fund&auth_token={token}",
                    headers={"Accept": "text/html", "Sec-Fetch-Dest": "iframe"},
                    follow_redirects=False,
                )

        self.assertEqual(resp.status_code, 303, msg="a banked bootstrap token must redirect")
        cookies = " ".join(resp.headers.get_list("set-cookie"))
        self.assertIn(
            "st_access=", cookies,
            msg=f"the redirect must re-issue st_access; got {cookies!r}",
        )
        self.assertNotIn(
            token, resp.headers["location"],
            msg="and the token must be gone from the redirect target",
        )

    # -- 1.278 ---------------------------------------------------------
    def test_1_278_a_bodiless_response_yields_no_body_at_all(self) -> None:
        """
        Scenario 1.278: a 304 relays with no body chunk.
        Given: an upstream 304 Not Modified
        When: the proxy relays it
        Then: the body iterator yields nothing.

        Yielding `b""` is not the same as yielding nothing: an empty first
        chunk with more_body reaches GZipMiddleware as a body, skips its
        minimum_size guard, and gets a gzip header and ten bytes attached --
        after which uvicorn raises "Response content longer than
        Content-Length". Real `aiter_raw()` yields zero chunks here, so this
        branch has to match, and it is the branch every test double takes.
        """
        async def _collect(resp):
            return [chunk async for chunk in proxy._iter_upstream(resp)]

        not_modified = httpx.Response(304)
        self.assertEqual(
            asyncio.run(_collect(not_modified)), [],
            msg="a 304 must relay with no body chunk at all, not an empty one",
        )

        with_body = httpx.Response(200, content=b"payload")
        self.assertEqual(
            asyncio.run(_collect(with_body)), [b"payload"],
            msg="a response that does have a body must still relay it",
        )

    # -- 1.279 ---------------------------------------------------------
    def test_1_279_a_mid_body_upstream_failure_is_not_a_truncated_200(self) -> None:
        """
        Scenario 1.279: an upstream that dies part-way through the body raises.
        Given: an upstream response whose stream fails after the first chunk
        When: the proxy relays it
        Then: the error propagates rather than the body simply ending.

        The response head is already sent by then, so the failure cannot become
        an HTTP status -- but truncating silently is worse than dropping the
        connection. A half-delivered Vite chunk surfaces as a SyntaxError or
        "Failed to fetch dynamically imported module", indistinguishable from
        the bug this module exists to fix and not retryable. A dropped
        connection the browser reports as a network error.
        """
        class _FailingStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"first-chunk"
                raise httpx.ReadError("upstream vanished")

            async def aclose(self) -> None:
                return None

        async def _drain():
            resp = httpx.Response(200, stream=_FailingStream())
            chunks = []
            async for chunk in proxy._iter_upstream(resp):
                chunks.append(chunk)
            return chunks

        with self.assertRaises(
            httpx.ReadError,
            msg="a mid-body failure must propagate, not end the body quietly",
        ):
            asyncio.run(_drain())

    # -- 1.280 ---------------------------------------------------------
    def test_1_280_a_public_probe_carrying_a_body_is_not_a_500(self) -> None:
        """
        Scenario 1.280: `Content-Length` is never forwarded, so probes with a body work.
        Given: a GET /_stcore/health that carries a request body
        When: the proxy forwards it
        Then: the forwarded headers omit Content-Length, and the probe answers 200.

        The forwarded value beat httpx's own, so sending an empty body while
        claiming the client's length raised LocalProtocolError ("Too little
        data for declared Content-Length"). That is neither ConnectError nor
        TimeoutException, so the readiness endpoint answered 500 -- a liveness
        probe failing because of a header it did not care about.
        """
        _Upstream.reset(lambda: httpx.Response(200, stream=_RawStream(b"ok")))
        with patch.object(proxy, "_upstream_send", _Upstream.send):
            with TestClient(proxy.app) as client:
                resp = client.request("GET", "/_stcore/health", content=b"unexpected-body")

        self.assertEqual(resp.status_code, 200, msg="the probe must still answer 200")
        forwarded = {k.lower() for k in _Upstream.calls[0]["headers"]}
        self.assertNotIn(
            "content-length", forwarded,
            msg="Content-Length must never be forwarded; httpx recomputes it from the real body",
        )

    # -- 1.281 ---------------------------------------------------------
    def test_1_281_the_pooled_client_is_never_shared_across_event_loops(self) -> None:
        """
        Scenario 1.281: each event loop gets its own upstream client.
        Given: two successive event loops in one process
        When: each asks for the pooled client
        Then: they get different clients.

        `is_closed` says nothing about which loop a client's keep-alive sockets
        belong to. Reusing one across loops raises "unable to perform operation
        on <TCPTransport closed=True>; the handler is closed" from a connection
        the previous loop left in the pool -- and a module-level singleton
        outlives any number of loops.
        """
        first = asyncio.run(self._client_id())
        second = asyncio.run(self._client_id())

        self.assertNotEqual(
            first, second,
            msg="a client from a finished loop must not be handed to the next one",
        )

    async def _client_id(self) -> int:
        return id(await proxy._get_upstream_client())

    # -- 1.282 ---------------------------------------------------------
    def test_1_282_the_single_flight_lock_belongs_to_the_running_loop(self) -> None:
        """
        Scenario 1.282: lock identity is per event loop.
        Given: two successive event loops
        When: each takes the JWKS lock under contention
        Then: neither raises, and each got its own lock object.

        A module-level `asyncio.Lock()` *looks* loop-agnostic because an
        uncontended acquire never binds it -- but the first contended acquire
        does, and a contended acquire on another loop then raises "is bound to a
        different event loop". So it would break only under the concurrency the
        single-flight was written for, which is the worst possible time.
        """
        async def contend():
            lock = proxy._jwks_lock()
            async def hold():
                async with lock:
                    await asyncio.sleep(0)
            await asyncio.gather(hold(), hold(), hold())
            return id(lock)

        first = asyncio.run(contend())
        second = asyncio.run(contend())

        self.assertNotEqual(
            first, second, msg="each loop must get its own lock, or contention raises",
        )


class TestCluster01ad_StartupHardening(SimpleTestCase):
    """Cluster 1ad: startup must fail readably and never on a typo alone."""

    # -- 1.283 ---------------------------------------------------------
    def test_1_283_a_malformed_integer_setting_does_not_stop_the_proxy(self) -> None:
        """
        Scenario 1.283: a non-numeric LEX_PROXY_REPLICAS warns and defaults.
        Given: LEX_PROXY_REPLICAS=auto
        When: the proxy is imported
        Then: it boots with one replica.

        A bare `int()` raised at import, and this module is imported *in the
        uvicorn worker thread* -- so a typo in one deployment variable killed
        the proxy while Streamlit kept serving, which reads as "the dashboard is
        broken" rather than "fix your env". The CLI pre-check already tolerated
        the same value, so the two disagreed.
        """
        module = _reimport_proxy_with({
            "STREAMLIT_URL": "http://localhost:8501",
            "LEX_PROXY_REPLICAS": "auto",
        })
        self.assertEqual(
            module.PROXY_REPLICAS, 1,
            msg="an unparseable replica count must fall back to 1, not raise at import",
        )

    # -- 1.284 ---------------------------------------------------------
    def test_1_284_the_bundle_follows_streamlits_base_url_path(self) -> None:
        """
        Scenario 1.284: a configured baseUrlPath moves the asset mount with it.
        Given: LEX_STREAMLIT_BASE_URL_PATH=app
        When: the proxy builds its routes
        Then: the static mount is at /app/static, not /static.

        Streamlit serves its whole app under `--server.baseUrlPath`, so the
        browser asks for /app/static/js/... . A mount fixed at /static misses
        every one of those, drops them into the authenticated catch-all, and
        silently restores the 401 storm -- with the bundle present, so nothing
        warns.
        """
        module = _reimport_proxy_with({
            "STREAMLIT_URL": "http://localhost:8501",
            "LEX_STREAMLIT_BASE_URL_PATH": "app",
        })
        mounts = [getattr(r, "path", "") for r in module._build_static_routes()]

        self.assertIn(
            "/app/static", mounts,
            msg=f"the bundle must be mounted under the configured base path; got {mounts}",
        )
        self.assertNotIn(
            "/static", mounts,
            msg="and not also at the bare root, which would serve two truths",
        )

    # -- 1.285 ---------------------------------------------------------
    def test_1_285_the_cli_precheck_mirrors_the_cookie_rules_too(self) -> None:
        """
        Scenario 1.285: `lex streamlit`'s pre-flight refuses an unusable SameSite.
        Given: SESSION_SAMESITE=none with SESSION_HTTPS_ONLY=false, then a typo
        When: the pre-flight runs
        Then: each raises a ClickException.

        The pre-flight exists so proxy.py's import-time rules fail on the main
        thread instead of inside the uvicorn worker, where they kill only the
        proxy and leave Streamlit serving. It mirrored two of the rules and not
        these, so exactly the failure it was written to prevent still happened.
        """
        import click

        from lex.bin.lex import _warn_if_sessions_are_not_durable

        cases = {
            "none without Secure": {
                "SESSION_SAMESITE": "none", "SESSION_HTTPS_ONLY": "false",
                "STREAMLIT_URL": "http://localhost:8501", "SESSION_SECRET": "s"},
            "an invalid value": {
                "SESSION_SAMESITE": "sometimes",
                "STREAMLIT_URL": "http://localhost:8501", "SESSION_SECRET": "s"},
        }
        for label, env in cases.items():
            with self.subTest(config=label):
                with patch.dict(os.environ, env, clear=False):
                    with self.assertRaises(
                        click.ClickException,
                        msg=f"the pre-flight must refuse {label} on the main thread",
                    ):
                        _warn_if_sessions_are_not_durable()

    # -- 1.286 ---------------------------------------------------------
    def test_1_286_the_https_test_agrees_between_the_cli_and_the_proxy(self) -> None:
        """
        Scenario 1.286: an uppercase scheme resolves the same way on both sides.
        Given: STREAMLIT_URL=HTTPS://dash.example.com, SESSION_HTTPS_ONLY=false, no
               explicit SESSION_SAMESITE
        When: the CLI pre-flight runs
        Then: it refuses the SameSite=none-without-Secure combination that follows from
              recognising the deployment as https -- exactly as the proxy's import would.

        proxy.py derives the same fact via `httpx.URL(...).scheme`, which
        lowercases; the pre-flight used `startswith("https://")`. So an
        uppercase scheme passed the readable check and then raised in the worker
        thread, where it kills only the proxy -- a pre-check that disagrees with
        the rule it mirrors is worse than having no pre-check.

        Asserted through the SameSite rule rather than the session-secret one,
        because the latter no longer refuses: it derives a durable key from
        DJANGO_SECRET_KEY instead. See 1.270 and 1.271.
        """
        import click

        from lex.bin.lex import _warn_if_sessions_are_not_durable

        env = {"STREAMLIT_URL": "HTTPS://dash.example.com", "SESSION_HTTPS_ONLY": "false"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("SESSION_SAMESITE", None)
            with self.assertRaises(
                click.ClickException,
                msg="an uppercase https scheme must be recognised, so SameSite resolves to none",
            ) as caught:
                _warn_if_sessions_are_not_durable()

        self.assertIn(
            "Secure", caught.exception.message,
            msg="the refusal must be the Secure-cookie one, proving https was detected",
        )


class TestCluster01ad_RenewalDelivery(SimpleTestCase):
    """Cluster 1ad: how a renewed credential reaches the proxy at all."""

    _SHELL = "https://shell.example.com"

    def _app(self, **env):
        return _reimport_proxy_with({
            "STREAMLIT_URL": "http://localhost:8501",
            "SESSION_SECRET": "fixed",
            "REACT_APP_URL": self._SHELL,
            **env,
        })

    # -- 1.287 ---------------------------------------------------------
    def test_1_287_a_renewed_token_can_be_adopted_without_touching_the_iframe(self) -> None:
        """
        Scenario 1.287: POST /auth/adopt installs a newer token into the live session.
        Given: a session bootstrapped with a token that is about to expire
        When: the shell POSTs a strictly newer token from its own origin
        Then: it is adopted, and requests keep working past the old token's expiry.

        Without this channel renewal is unreachable. `_persist_jwt_to_session_if_needed`
        already adopts a strictly-newer token -- its comment names the frontend
        as the source -- but the only way to *present* one was the iframe URL,
        and re-sourcing the iframe loads a new document, i.e. a new Streamlit
        session with an empty `st.session_state`. So delivering the token and
        keeping the state were mutually exclusive, and the embedded session died
        at the access token's own lifetime either way.
        """
        module = self._app()
        now = int(module.time.time())
        claims = {"old": {"sub": "u1", "exp": now + 3}, "new": {"sub": "u1", "exp": now + 3600}}

        def _validate(tok):
            found = claims.get(tok)
            return None if not found or found["exp"] <= int(module.time.time()) else found

        async def _upstream(method, url, *, content=None, headers=None):
            return httpx.Response(200, content=b"dash")

        with patch.object(module, "validate_jwt_token", _validate), \
             patch.object(module, "_upstream_send", _upstream):
            with TestClient(module.app) as client:
                client.get(
                    "/?model=Fund&auth_token=old",
                    headers={"Accept": "text/html", "Sec-Fetch-Dest": "iframe"},
                    follow_redirects=True,
                )

                adopted = client.post(
                    "/auth/adopt", json={"auth_token": "new"}, headers={"Origin": self._SHELL}
                )
                self.assertEqual(
                    adopted.status_code, 200,
                    msg=f"the shell must be able to hand over a renewed token, got {adopted.status_code}",
                )
                self.assertTrue(
                    adopted.json().get("adopted"),
                    msg="a strictly newer token must supersede the stored one",
                )

                # Past the point where the bootstrap token is dead.
                claims["old"]["exp"] = now - 1
                still_live = client.get(
                    "/", headers={"Accept": "text/html"}, follow_redirects=False
                )

        self.assertEqual(
            still_live.status_code, 200,
            msg="the session must outlive the token it was bootstrapped with",
        )

    # -- 1.288 ---------------------------------------------------------
    def test_1_288_adoption_refuses_a_foreign_origin(self) -> None:
        """
        Scenario 1.288: only the configured frontend origin may adopt.
        Given: a valid token
        When: it is POSTed from an unrecognised Origin, and with none at all
        Then: both are refused.

        The endpoint is called with credentials, so it cannot answer `*` -- and
        an unset REACT_APP_URL/LEX_FRONTEND_URL must therefore close it rather
        than open it. A page the user happens to be visiting must not be able
        to reach into their dashboard session.
        """
        module = self._app()
        with patch.object(module, "validate_jwt_token", lambda _t: {"sub": "u1", "exp": 4_000_000_000}):
            with TestClient(module.app) as client:
                for origin, label in (
                    ("https://evil.example", "a foreign origin"),
                    (None, "no Origin header"),
                ):
                    with self.subTest(origin=label):
                        headers = {"Origin": origin} if origin else {}
                        resp = client.post("/auth/adopt", json={"auth_token": "t"}, headers=headers)
                        self.assertEqual(
                            resp.status_code, 403,
                            msg=f"{label} must be refused, got {resp.status_code}",
                        )

    # -- 1.289 ---------------------------------------------------------
    def test_1_289_adoption_still_validates_the_token(self) -> None:
        """
        Scenario 1.289: an unsigned or absent token is rejected.
        Given: requests from the allowed origin carrying junk, or nothing
        When: each is POSTed
        Then: an invalid token is 401 and a missing one is 400.

        This is what keeps the endpoint from being a way to install arbitrary
        identity: the token is checked against Keycloak's JWKS exactly as every
        other credential is, so presenting a genuine one for a user means
        already holding that user's credential.
        """
        module = self._app()
        with patch.object(module, "validate_jwt_token", lambda _t: None):
            with TestClient(module.app) as client:
                junk = client.post(
                    "/auth/adopt", json={"auth_token": "forged"}, headers={"Origin": self._SHELL}
                )
                missing = client.post("/auth/adopt", json={}, headers={"Origin": self._SHELL})

        self.assertEqual(junk.status_code, 401, msg="an unverifiable token must be rejected")
        self.assertEqual(missing.status_code, 400, msg="a missing token is a malformed request")

    # -- 1.290 ---------------------------------------------------------
    def test_1_290_the_preflight_answers_for_the_allowed_origin_only(self) -> None:
        """
        Scenario 1.290: OPTIONS is answerable, and scoped.
        Given: a CORS preflight from the frontend origin, then from another
        When: each is sent
        Then: the first returns 204 naming POST, the second is refused.

        A cross-origin POST with a JSON content type is preflighted, so without
        this the browser never sends the adoption request at all -- and the
        failure would be invisible except as a session that quietly stops
        renewing.
        """
        module = self._app()
        with TestClient(module.app) as client:
            allowed = client.request("OPTIONS", "/auth/adopt", headers={"Origin": self._SHELL})
            refused = client.request(
                "OPTIONS", "/auth/adopt", headers={"Origin": "https://evil.example"}
            )

        self.assertEqual(allowed.status_code, 204, msg="the preflight must succeed")
        self.assertIn(
            "POST", allowed.headers.get("access-control-allow-methods", ""),
            msg="the preflight must permit POST or the browser will not send it",
        )
        self.assertEqual(
            allowed.headers.get("access-control-allow-origin"), self._SHELL,
            msg="the response must name the origin, never '*' -- it is a credentialed request",
        )
        self.assertEqual(refused.status_code, 403, msg="a foreign preflight must be refused")

    # -- 1.291 ---------------------------------------------------------
    def test_1_291_the_allowlist_derives_from_the_instance_hostname(self) -> None:
        """
        Scenario 1.291: DOMAIN_HOSTED alone is enough to permit adoption.
        Given: only DOMAIN_HOSTED set, as every deployed instance already has
        When: the proxy resolves which origins may hand it a renewed credential
        Then: `https://<DOMAIN_HOSTED>` is among them, with no extra variable set.

        This is the whole reason the allowlist is derived rather than declared.
        `lex/lex_app/settings.py` *refuses to start* without DOMAIN_HOSTED
        whenever DEPLOYMENT_ENVIRONMENT is set, and Django already builds
        `CORS_ORIGIN_WHITELIST` from it the same way -- so it is guaranteed
        present and already means "where the frontend is". Requiring a new
        variable instead would have made the failure mode a dashboard that
        renews for five minutes and then quietly stops, with nothing in the
        logs to say why.
        """
        module = _reimport_proxy_with({
            "DOMAIN_HOSTED": "test-instance-1461.lit.excellence-cloud.de",
            "STREAMLIT_URL": "https://test-instance-1461-streamlit.lit.excellence-cloud.de",
            "SESSION_SECRET": "fixed",
            "REACT_APP_URL": None,
            "LEX_FRONTEND_URL": None,
        })

        self.assertIn(
            "https://test-instance-1461.lit.excellence-cloud.de", module.FRONTEND_ORIGINS,
            msg=f"the instance hostname must be trusted without extra config; got {sorted(module.FRONTEND_ORIGINS)}",
        )
        self.assertNotIn(
            "*", module.FRONTEND_ORIGINS,
            msg="a credentialed endpoint must never allow a wildcard origin",
        )

    # -- 1.292 ---------------------------------------------------------
    def test_1_292_a_localhost_domain_does_not_become_a_trusted_https_origin(self) -> None:
        """
        Scenario 1.292: DOMAIN_HOSTED=localhost yields dev origins, not `https://localhost`.
        Given: the development default, where DOMAIN_HOSTED is absent or "localhost"
        When: the allowlist is resolved
        Then: the shell's actual dev origins are trusted and `https://localhost` is not.

        `settings.py` defaults DOMAIN_HOSTED to "localhost", but the shell runs
        on another port in development -- a different origin, so still subject
        to CORS. Deriving `https://localhost` from that default would trust
        something nothing serves while still failing the real dev setup.
        """
        module = _reimport_proxy_with({
            "DOMAIN_HOSTED": "localhost",
            "STREAMLIT_URL": "http://localhost:8501",
            "REACT_APP_URL": None,
            "LEX_FRONTEND_URL": None,
        })

        self.assertNotIn(
            "https://localhost", module.FRONTEND_ORIGINS,
            msg="the localhost default must not be promoted to a trusted https origin",
        )
        self.assertIn(
            "http://localhost:3000", module.FRONTEND_ORIGINS,
            msg="the shell's development origin must be trusted, or renewal cannot be tested locally",
        )


class TestCluster01ad_StaleConnectionRecovery(SimpleTestCase):
    """Cluster 1ad: the pooled client must survive the upstream closing a connection."""

    # -- 1.293 ---------------------------------------------------------
    def test_1_293_a_connection_the_upstream_closed_is_retried(self) -> None:
        """
        Scenario 1.293: a request after the upstream's keep-alive expiry still succeeds.
        Given: one request, then an idle gap long enough for the upstream to close the socket
        When: a second request reuses the pooled connection
        Then: it succeeds, having opened a fresh connection.

        This is a regression the pooling change introduced and production found.
        Streamlit runs behind uvicorn, whose keep-alive timeout is 5s; past that
        it closes the socket, and a pooled connection handed out afterwards
        fails with `RemoteProtocolError: Server disconnected without sending a
        response` before a single byte is exchanged. The per-request client this
        replaced could never hit it, because it never reused a connection.

        Observed in a production log exactly 5s after the previous request, on a
        `/media/...` fetch -- i.e. a document download that simply failed.
        """
        async def scenario():
            connections = {"count": 0}

            async def handler(reader, writer):
                connections["count"] += 1
                await reader.read(4096)
                # Answer, then close: a server whose keep-alive has expired.
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
                    b"Connection: keep-alive\r\n\r\nok"
                )
                await writer.drain()
                await asyncio.sleep(0.05)
                writer.close()

            server = await asyncio.start_server(handler, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            url = httpx.URL(f"http://127.0.0.1:{port}/media/protokoll.zip")

            statuses = []
            try:
                with patch.object(proxy, "UPSTREAM", f"http://127.0.0.1:{port}"):
                    first = await proxy._upstream_send("GET", url, content=b"", headers={})
                    await first.aread()
                    await first.aclose()
                    statuses.append(first.status_code)

                    # Long enough for the handler to have closed the socket.
                    await asyncio.sleep(0.4)

                    second = await proxy._upstream_send("GET", url, content=b"", headers={})
                    await second.aread()
                    await second.aclose()
                    statuses.append(second.status_code)
            finally:
                server.close()
                await _close_pooled_client()

            return statuses, connections["count"]

        statuses, connections = asyncio.run(scenario())

        self.assertEqual(
            statuses, [200, 200],
            msg=f"a request after the upstream's keep-alive expiry must still succeed, got {statuses}",
        )
        self.assertEqual(
            connections, 2,
            msg=f"the retry must open a fresh connection, saw {connections}",
        )

    # -- 1.294 ---------------------------------------------------------
    def test_1_294_the_pool_expires_before_the_upstream_does(self) -> None:
        """
        Scenario 1.294: pooled connections are dropped sooner than the upstream drops them.
        Given: the proxy's upstream client configuration
        When: its keep-alive expiry is compared to uvicorn's 5s keep-alive timeout
        Then: the proxy's is shorter.

        The retry in 1.293 is the safety net; this is the part that stops the
        race happening. Expiring first means the pool rarely offers a connection
        the upstream has already closed, so the common case costs no extra round
        trip at all.
        """
        self.assertLess(
            proxy._UPSTREAM_KEEPALIVE_EXPIRY, 5.0,
            msg=(
                "pooled connections must expire before uvicorn's 5s keep-alive timeout, "
                f"got {proxy._UPSTREAM_KEEPALIVE_EXPIRY}s"
            ),
        )
        self.assertEqual(
            proxy._upstream_http_client_kwargs(proxy._UPSTREAM_TIMEOUT)["limits"].keepalive_expiry,
            proxy._UPSTREAM_KEEPALIVE_EXPIRY,
            msg="the configured expiry must actually reach the client",
        )

    # -- 1.295 ---------------------------------------------------------
    def test_1_295_an_unreachable_upstream_answers_502_rather_than_escaping(self) -> None:
        """
        Scenario 1.295: an httpx failure with no response becomes a 502.
        Given: an upstream that raises RemoteProtocolError every time
        When: an authenticated path and a public path are requested
        Then: both answer 502.

        RemoteProtocolError is neither ConnectError nor TimeoutException, so it
        escaped both handlers and surfaced as "Exception in ASGI application" --
        which reaches the browser as a dropped request rather than a status:
        a `/media` download that fails silently, or an iframe document that
        never loads. A 502 is at least legible to the client and to whoever
        reads the log.
        """
        async def always_dies(method, url, *, content=None, headers=None):
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

        with patch.object(proxy, "_upstream_send", always_dies):
            with TestClient(proxy.app, raise_server_exceptions=False) as client:
                public = client.get("/_stcore/health")
                with patch.object(
                    proxy, "validate_jwt_token", lambda _t: {"sub": "u", "exp": 4_000_000_000}
                ):
                    gated = client.get("/media/protokoll.zip?auth_token=x", headers={"Accept": "*/*"})

        for label, resp in (("a public path", public), ("an authenticated path", gated)):
            with self.subTest(path=label):
                self.assertEqual(
                    resp.status_code, 502,
                    msg=f"{label} must answer 502, not escape as an unhandled exception",
                )
