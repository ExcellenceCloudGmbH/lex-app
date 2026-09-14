"""A proxied response must be legal over HTTP/2, not merely accepted over HTTP/1.1.

Intent: the auth proxy relays Streamlit's responses, and in production those
responses reach the browser over HTTP/2 through an intermediary. HTTP/1.1 is
forgiving about several things HTTP/2 is not, so a response that looks fine in
local development can be rejected outright in production -- and the rejection
arrives as a stream reset, which the browser reports as
``net::ERR_HTTP2_PROTOCOL_ERROR`` against a status that was already ``200``.

That is how this was reported: clicking a download started it, the browser sat
at 0 bytes, and it ended in a network error -- while the proxy's own log showed
nothing but ``GET /media/....pdf -> 200``. Three separate defects combined:

* ``Date`` and ``Server`` were forwarded from the upstream, so uvicorn's own
  copies made them duplicates. Both are singleton fields (RFC 9110 5.5.2) and a
  duplicated singleton is a protocol violation.
* ``Content-Length`` was dropped from every proxied response, forcing
  ``Transfer-Encoding: chunked`` -- which HTTP/2 forbids outright
  (RFC 9113 8.2.2) -- and denying the browser any idea of how big the download
  was, hence "0 bytes" with no progress.
* GZip was applied app-wide, so already-compressed payloads (a PDF, a ZIP) were
  re-compressed for nothing on a loop this process shares with the Streamlit
  script runner.

Cluster 1an — scenarios 1.336–1.341. Type: U.
Covers: lex/proxy.py (_SERVER_OWNED_RESPONSE_HEADERS, _REQUEST_DROP,
        _build_proxied_response, _build_static_routes' gzip scope).
Run: python -m lex pytest lex/test_project/tests/init/test_1an_proxy_http2_safe_headers.py -v
"""

from __future__ import annotations

import glob
import os
from unittest.mock import patch

import httpx
import pytest
from django.test import SimpleTestCase
from starlette.testclient import TestClient

import lex.proxy as proxy

pytestmark = pytest.mark.init


class _RawStream(httpx.AsyncByteStream):
    """A not-yet-consumed stream, so a stub takes the production relay path."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def __aiter__(self):
        yield self._payload

    async def aclose(self) -> None:
        return None


def _media_like(payload: bytes, content_type: str = "application/pdf") -> httpx.Response:
    """A response shaped exactly like Streamlit's /media endpoint returns.

    It sets Content-Length, Accept-Ranges and Content-Disposition itself, and
    adds Date/Server via its own ASGI server -- which is what the proxy then
    has to relay without duplicating.
    """
    return httpx.Response(
        200,
        headers=[
            ("content-type", content_type),
            ("content-length", str(len(payload))),
            ("content-disposition", 'attachment; filename="p.pdf"'),
            ("accept-ranges", "bytes"),
            ("date", "Thu, 10 Sep 2026 08:00:00 GMT"),
            ("server", "uvicorn"),
        ],
        stream=_RawStream(payload),
    )


class TestCluster01an_Http2SafeResponseHeaders(SimpleTestCase):
    """Cluster 1an: what the proxy is allowed to put on a relayed response."""

    _PAYLOAD = b"%PDF-1.4\n" + b"x" * 200_000 + b"\n%%EOF"

    def _fetch(self, path: str = "/_stcore/health", **client_kwargs):
        async def _upstream(method, url, *, content=None, headers=None):
            return _media_like(self._PAYLOAD)

        with patch.object(proxy, "_upstream_send", _upstream):
            with TestClient(proxy.app) as client:
                return client.get(path, **client_kwargs)

    # -- 1.336 ---------------------------------------------------------
    def test_1_336_date_and_server_are_never_duplicated(self) -> None:
        """
        Scenario 1.336: the upstream's Date and Server are not relayed.
        Given: an upstream response carrying its own Date and Server
        When: the proxy relays it
        Then: each appears exactly once, being the ASGI server's own.

        `Date` and `Server` are singleton fields. HTTP/1.1 tolerates a repeat,
        so this passed unnoticed in development; the HTTP/2 intermediary in
        front of production does not, and rejects the message by resetting the
        stream. Measured before the fix: two of each on every proxied response.
        """
        resp = self._fetch()

        # Asserted as "the proxy does not relay the upstream's copy", not as an
        # exact count: under a real ASGI server uvicorn supplies exactly one of
        # each, but TestClient's in-process transport supplies none, so a count
        # of 1 would only hold in production. What must be true either way is
        # that the proxy contributes nothing to duplicate.
        for header, upstream_value in (
            ("date", "Thu, 10 Sep 2026 08:00:00 GMT"),
            ("server", "uvicorn"),
        ):
            with self.subTest(header=header):
                relayed = resp.headers.get_list(header)
                self.assertLessEqual(
                    len(relayed), 1,
                    msg=(
                        f"{header} must never appear twice -- a duplicated singleton is "
                        f"rejected over HTTP/2; got {relayed}"
                    ),
                )
                self.assertNotIn(
                    upstream_value, relayed,
                    msg=(
                        f"the upstream's {header} must not be relayed, or the server's "
                        f"own copy becomes a duplicate; got {relayed}"
                    ),
                )

    # -- 1.337 ---------------------------------------------------------
    def test_1_337_a_relayed_response_keeps_its_length(self) -> None:
        """
        Scenario 1.337: Content-Length survives, so the response is not chunked.
        Given: an upstream response with a known Content-Length
        When: the proxy relays the bytes unchanged
        Then: the same Content-Length is sent, and no Transfer-Encoding is.

        The relay passes the upstream's bytes through untouched, so its length
        is still exactly right -- dropping it bought nothing and cost two
        things: `Transfer-Encoding: chunked`, which HTTP/2 forbids, and any
        chance for the browser to show download progress. "Stays at 0 BYTE" was
        the literal report.
        """
        resp = self._fetch()

        self.assertEqual(
            resp.headers.get("content-length"), str(len(self._PAYLOAD)),
            msg="the relayed response must carry the upstream's own length",
        )
        self.assertIsNone(
            resp.headers.get("transfer-encoding"),
            msg="a sized response must not also be chunked; HTTP/2 forbids chunked",
        )
        self.assertEqual(
            len(resp.content), len(self._PAYLOAD),
            msg="and the body must actually be that long",
        )

    # -- 1.338 ---------------------------------------------------------
    def test_1_338_an_already_compressed_payload_is_not_recompressed(self) -> None:
        """
        Scenario 1.338: a proxied PDF comes back uncompressed.
        Given: a client that accepts gzip, and an upstream PDF
        When: the proxy relays it
        Then: no Content-Encoding is added.

        GZip used to wrap the whole app, so every proxied PDF and ZIP was
        re-compressed -- already-compressed bytes, for no saving, on an event
        loop this process shares with the Streamlit script runner. It also
        forced the response into chunked encoding, which is what made the
        duplicated singletons fatal rather than merely wrong.
        """
        resp = self._fetch(headers={"Accept-Encoding": "gzip"})

        self.assertIsNone(
            resp.headers.get("content-encoding"),
            msg=(
                "an already-compressed payload must be relayed as-is; got "
                f"{resp.headers.get('content-encoding')!r}"
            ),
        )

    # -- 1.339 ---------------------------------------------------------
    def test_1_339_the_asset_bundle_is_still_compressed(self) -> None:
        """
        Scenario 1.339: narrowing GZip must not lose the saving it was added for.
        Given: a client that accepts gzip
        When: it fetches a chunk of Streamlit's bundle
        Then: the response is gzip-encoded.

        The guard on 1.338. GZip now wraps only the static mount, and the whole
        point of adding it was the eagerly-preloaded bundle -- measured at
        1.77 MB plaintext against 0.42 MB gzipped. Scoping it must not quietly
        undo that.
        """
        static_dir = proxy._streamlit_static_dir()
        if not static_dir:  # pragma: no cover - streamlit always present here
            self.skipTest("the installed streamlit wheel has no static directory")

        chunks = sorted(
            glob.glob(os.path.join(static_dir, "static", "js", "*.js")),
            key=os.path.getsize,
            reverse=True,
        )
        with TestClient(proxy.app) as client:
            resp = client.get(
                f"/static/js/{os.path.basename(chunks[0])}",
                headers={"Accept-Encoding": "gzip"},
            )

        self.assertEqual(resp.status_code, 200, msg="the chunk must still be served")
        self.assertEqual(
            resp.headers.get("content-encoding"), "gzip",
            msg="the bundle must still be compressed after scoping GZip to it",
        )

    # -- 1.340 ---------------------------------------------------------
    def test_1_340_content_length_is_still_dropped_from_the_request(self) -> None:
        """
        Scenario 1.340: keeping the response's length must not keep the request's.
        Given: a request that carries a body
        When: the proxy forwards it
        Then: Content-Length is not among the forwarded headers.

        The two directions need opposite treatment and share a helper, so this
        is the guard that stops one fix undoing the other. httpx recomputes
        Content-Length from the body it is handed, and a forwarded value beats
        its own -- so a mismatch raises LocalProtocolError instead of sending a
        request, which is what made a probe carrying a body answer 500.
        """
        seen: dict = {}

        async def _upstream(method, url, *, content=None, headers=None):
            seen.update({k.lower(): v for k, v in (headers or {}).items()})
            return _media_like(b"ok", content_type="text/plain")

        with patch.object(proxy, "_upstream_send", _upstream):
            with TestClient(proxy.app) as client:
                resp = client.request("GET", "/_stcore/health", content=b"unexpected-body")

        self.assertEqual(resp.status_code, 200, msg="the probe must still answer 200")
        self.assertNotIn(
            "content-length", seen,
            msg="Content-Length must never be forwarded on the request side",
        )

    # -- 1.341 ---------------------------------------------------------
    def test_1_341_a_consumed_response_drops_the_length_and_the_encoding(self) -> None:
        """
        Scenario 1.341: the buffered relay path keeps neither Content-Length nor
        Content-Encoding.
        Given: an already-consumed upstream response carrying `Content-Encoding: gzip`
               and the COMPRESSED length, whose `.content` httpx has decoded
        When: the proxy relays it
        Then: both headers are gone, and the body is the decoded bytes.

        `_build_proxied_response` treats its two branches differently, and 1.336–1.340
        only exercise the streaming one -- `_RawStream` exists precisely to take that
        path. This is the branch where a stale header is *fatal* rather than merely
        wrong, and the two halves fail for different reasons. Both measured:

        * a stale **length** -- the compressed 40 against a decoded 5000 -- makes a
          real uvicorn raise `RuntimeError: Response content longer than
          Content-Length`, and the client sees `http=200 bytes=0`. That is the
          reported symptom verbatim: a download that starts, sits at 0 bytes and
          ends in a network error.
        * a stale **encoding** raises `DecodingError: incorrect header check` in the
          client, which tries to gunzip bytes httpx already decoded.

        The streaming path keeps Content-Length on purpose, so this asymmetry is
        deliberate -- which is exactly why it needs a guard rather than a comment.
        """
        import gzip

        plain = b"x" * 5000
        packed = gzip.compress(plain)
        self.assertNotEqual(
            len(plain), len(packed),
            msg="the fixture is only meaningful if the two lengths differ",
        )

        def _consumed() -> httpx.Response:
            # `content=` makes the response born consumed, and `.content` is then
            # the DECODED body -- so neither the declared length nor the declared
            # encoding describes what we are about to send.
            return httpx.Response(
                200,
                headers=[
                    ("content-type", "text/plain"),
                    ("content-encoding", "gzip"),
                    ("content-length", str(len(packed))),
                ],
                content=packed,
            )

        self.assertTrue(
            _consumed().is_stream_consumed,
            msg="precondition: this fixture must take the buffered branch",
        )

        async def _upstream(method, url, *, content=None, headers=None):
            return _consumed()

        with patch.object(proxy, "_upstream_send", _upstream):
            with TestClient(proxy.app) as client:
                resp = client.get("/_stcore/health")

        self.assertIsNone(
            resp.headers.get("content-length"),
            msg=(
                "a consumed response must not declare the upstream's compressed length; "
                "uvicorn raises 'Response content longer than Content-Length' and the "
                "client is left with a 200 and zero bytes"
            ),
        )
        self.assertIsNone(
            resp.headers.get("content-encoding"),
            msg=(
                "nor its encoding -- httpx already decoded the body, so a client that "
                "believes the header fails with 'incorrect header check'"
            ),
        )
        self.assertEqual(
            resp.content, plain,
            msg="and the body relayed must be the decoded bytes",
        )
