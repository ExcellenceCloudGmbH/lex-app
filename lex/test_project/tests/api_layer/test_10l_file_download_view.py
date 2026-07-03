"""
Sub-cluster 10l — `FileDownloadView` storage-type dispatch.

Targets `lex/api/views/file_operations/FileDownload.py` (33.33% baseline,
~20 missed lines). This APIView is the **single GET endpoint** the
React file widget hits when a user clicks a download link on a
`FileField` column. A regression in **any** of its branches —
missing-pk, LOCAL filesystem, cloud public-URL, SharePoint binary — is
customer-visible the moment they click a download:

1. Wrong pk handling → "File not found" toast on rows the user can
   actually see (data is there, lookup is wrong).
2. Wrong `STORAGE_TYPE` dispatch → a customer deployed on cloud storage gets a
   filesystem `FileResponse` (or vice versa), which either streams
   `None.path` (AttributeError 500) or returns an unsigned URL the
   browser can't reach.
3. SharePoint leading-slash drift → `get_server_relative_path` builds
   a malformed URL and the call to `open_binary` raises a confusing
   "site not found" error in the SharePoint SDK.

All scenarios use `SimpleTestCase` — DRF's view-state plumbing is
mocked end-to-end; no DB, no filesystem touch, no broker.

Scenarios 10.47 – 10.54.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.http import FileResponse, JsonResponse
from django.test import SimpleTestCase

from lex.api.views.file_operations.FileDownload import FileDownloadView

import pytest

pytestmark = pytest.mark.api_layer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_request(pk="42", field="document"):
    """Build a stub DRF-ish request carrying just `query_params`."""
    return SimpleNamespace(query_params={"pk": pk, "field": field})


def _build_kwargs(instance=None, *, model_class=None):
    """Build the URL kwargs the dispatcher injects into `get()`.

    The view reads `kwargs['model_container'].model_class`, then
    calls `.objects.filter(pk=...).first()`. We wire the chain through
    a MagicMock so each scenario can pin the resolved instance.
    """
    model_class = model_class or MagicMock(name="ModelClass")
    model_class.objects.filter.return_value.first.return_value = instance
    container = SimpleNamespace(model_class=model_class)
    return {"model_container": container}


def _build_view():
    """Instantiate the APIView without going through the URL resolver."""
    view = FileDownloadView()
    view.kwargs = {}
    view.request = None
    return view


# ---------------------------------------------------------------------------
# 10.47 — missing pk → 404 JsonResponse
# ---------------------------------------------------------------------------


class TestCluster10l_MissingInstance(SimpleTestCase):
    """10.47: pk that does not resolve must return a 404 JsonResponse.

    The view uses `.filter(pk=...).first()` (not `.get(...)`) precisely
    so it can return a typed 404 body instead of bubbling an unhandled
    `DoesNotExist`. Customer impact of a regression: the React file
    widget would see a 500 + generic toast instead of the "File not
    found" message it actually branches on.
    """

    def test_10_47_returns_404_json_when_filter_first_returns_none(self):
        view = _build_view()
        request = _build_request(pk="9999")
        kwargs = _build_kwargs(instance=None)

        response = view.get(request, **kwargs)

        self.assertIsInstance(
            response, JsonResponse,
            "Missing-instance branch must return JsonResponse (not "
            "FileResponse / not raise DoesNotExist) so the SPA branches "
            "on the typed JSON error.",
        )
        self.assertEqual(
            response.status_code, 404,
            "Missing-pk lookup must surface as 404, not 200 or 500.",
        )
        # filter().first() must be called with pk from query_params.
        kwargs["model_container"].model_class.objects.filter.assert_called_once_with(pk="9999")


# ---------------------------------------------------------------------------
# 10.48 / 10.49 — LOCAL storage branch
# ---------------------------------------------------------------------------


class TestCluster10l_LocalStorage(SimpleTestCase):
    """10.48 + 10.49 + 10.53 + 10.54: the LOCAL filesystem branch.

    LOCAL is the default storage in dev + most single-host
    deployments. The view must use `.path` (absolute OS path) not
    `.url` (URL relative to MEDIA_URL) — drift to `.url` would either
    open the wrong path or 500 with a `FileNotFoundError` for every
    download.
    """

    def test_10_48_local_happy_path_returns_file_response(self):
        instance = SimpleNamespace(document=SimpleNamespace(path="/tmp/test-fixture.bin"))
        view = _build_view()
        request = _build_request()
        kwargs = _build_kwargs(instance=instance)

        # Patch the builtin `open` referenced from the view's module
        # so we don't touch the filesystem.
        fake_fp = MagicMock(name="file_pointer")
        with patch.dict("os.environ", {"STORAGE_TYPE": "LOCAL"}, clear=False), \
             patch("lex.api.views.file_operations.FileDownload.open",
                   return_value=fake_fp, create=True) as mock_open:
            response = view.get(request, **kwargs)

        mock_open.assert_called_once_with("/tmp/test-fixture.bin", "rb")
        self.assertIsInstance(
            response, FileResponse,
            "LOCAL happy path must return FileResponse so DRF streams "
            "the file bytes inline (not buffer the whole file in memory).",
        )

    def test_10_49_local_file_not_found_returns_404_json(self):
        instance = SimpleNamespace(document=SimpleNamespace(path="/tmp/missing.bin"))
        view = _build_view()
        request = _build_request()
        kwargs = _build_kwargs(instance=instance)

        with patch.dict("os.environ", {"STORAGE_TYPE": "LOCAL"}, clear=False), \
             patch("lex.api.views.file_operations.FileDownload.open",
                   side_effect=FileNotFoundError("no such file"), create=True):
            response = view.get(request, **kwargs)

        self.assertIsInstance(
            response, JsonResponse,
            "FileNotFoundError must be caught and returned as a typed "
            "JsonResponse — a raw OSError would surface as a 500 to "
            "the SPA, masking the 'file gone' diagnostic.",
        )
        self.assertEqual(response.status_code, 404)

    def test_10_53_storage_type_unset_defaults_to_local(self):
        """`os.getenv("STORAGE_TYPE", "LOCAL")` — unset env hits LOCAL.

        Drift to a different default (e.g. SHAREPOINT) would silently
        crash every dev / single-host deployment that never set the
        env var.
        """
        instance = SimpleNamespace(document=SimpleNamespace(path="/tmp/x.bin"))
        view = _build_view()
        request = _build_request()
        kwargs = _build_kwargs(instance=instance)

        fake_fp = MagicMock()
        # Wipe STORAGE_TYPE so the default kicks in.
        with patch.dict("os.environ", {}, clear=False) as _env:
            import os as _os
            _os.environ.pop("STORAGE_TYPE", None)
            with patch("lex.api.views.file_operations.FileDownload.open",
                       return_value=fake_fp, create=True) as mock_open:
                response = view.get(request, **kwargs)

        mock_open.assert_called_once_with("/tmp/x.bin", "rb")
        self.assertIsInstance(response, FileResponse)

    def test_10_54_unknown_storage_type_falls_through_to_local(self):
        """Any value that is not SHAREPOINT / GCS / AZURE_BLOB lands in LOCAL.

        Pinned so a future unsupported provider fails loudly at the
        request boundary instead of silently dispatching to another
        cloud branch because the elif chain happened to fall through.
        """
        instance = SimpleNamespace(document=SimpleNamespace(path="/tmp/y.bin"))
        view = _build_view()
        request = _build_request()
        kwargs = _build_kwargs(instance=instance)

        fake_fp = MagicMock()
        with patch.dict("os.environ", {"STORAGE_TYPE": "S3"}, clear=False), \
             patch("lex.api.views.file_operations.FileDownload.open",
                   return_value=fake_fp, create=True) as mock_open:
            response = view.get(request, **kwargs)

        # Falls through to else → opens .path → FileResponse.
        mock_open.assert_called_once_with("/tmp/y.bin", "rb")
        self.assertIsInstance(response, FileResponse)


# ---------------------------------------------------------------------------
# 10.50 / 10.51 — SHAREPOINT branch
# ---------------------------------------------------------------------------


class TestCluster10l_SharePointStorage(SimpleTestCase):
    """10.50 + 10.51: the SHAREPOINT binary-download branch.

    The view builds a `SharePointContext`, resolves a server-relative
    URL via `get_server_relative_path`, calls `open_binary`, wraps the
    bytes in a `BytesIO`, and streams via `FileResponse`. Two regression
    surfaces matter most:

    1. Leading-slash drift — `file_obj.url` typically starts with `/`,
       and the SharePoint SDK rejects server-relative paths with double
       slashes. The view does `lstrip('/')` on the rel_url passed to
       `open_binary` for that reason.
    2. The wrapped `BytesIO` must carry the SDK's `.content` bytes
       verbatim; a regression that dropped the wrapper (passing the
       BinaryFile directly) would either 500 in DRF's render layer or
       silently truncate.
    """

    def _patch_sp(self, content=b"sharepoint-bytes"):
        """Return (SharePointContext mock, fake_target, fake_binary)."""
        fake_binary = SimpleNamespace(content=content)
        fake_target = MagicMock(name="TargetFile")
        fake_target.open_binary.return_value = fake_binary
        # web.get_file_by_server_relative_path(...).execute_query()
        fake_target_chain = MagicMock()
        fake_target_chain.execute_query.return_value = fake_target
        fake_web = MagicMock()
        fake_web.get_file_by_server_relative_path.return_value = fake_target_chain
        shrp_ctx = MagicMock(name="SharePointContext")
        shrp_ctx.ctx.web = fake_web
        return shrp_ctx, fake_target, fake_binary

    def test_10_50_sharepoint_returns_file_response_with_binary_content(self):
        shrp_ctx, fake_target, fake_binary = self._patch_sp(b"hello-sp")
        instance = SimpleNamespace(document=SimpleNamespace(url="/sites/x/file.pdf"))
        view = _build_view()
        request = _build_request()
        kwargs = _build_kwargs(instance=instance)

        with patch.dict("os.environ", {"STORAGE_TYPE": "SHAREPOINT"}, clear=False), \
             patch("lex.api.views.file_operations.FileDownload.SharePointContext",
                   return_value=shrp_ctx), \
             patch("lex.api.views.file_operations.FileDownload.get_server_relative_path",
                   side_effect=lambda u: f"/srv{u.lstrip('/')}"):
            response = view.get(request, **kwargs)

        self.assertIsInstance(
            response, FileResponse,
            "SHAREPOINT branch must return FileResponse so the SPA "
            "streams the binary the same way as the LOCAL branch.",
        )
        fake_target.open_binary.assert_called_once()
        # The streaming source must carry the SDK's .content bytes.
        # FileResponse buffers the file under .streaming_content; we
        # iterate it to assert the underlying BytesIO had our bytes.
        streamed = b"".join(response.streaming_content)
        self.assertEqual(
            streamed, b"hello-sp",
            "BytesIO wrapper must carry the SDK's `.content` verbatim — "
            "regression that dropped the wrapper would truncate or 500.",
        )

    def test_10_51_sharepoint_strips_leading_slash_on_relative_url(self):
        """rel_url passed to `open_binary` (via `get_server_relative_path`)
        must NOT have a leading slash — drift here malforms the
        server-relative path and the SDK raises a confusing
        'site not found' error."""
        shrp_ctx, fake_target, _ = self._patch_sp()
        instance = SimpleNamespace(document=SimpleNamespace(url="/sites/x/has-slash.pdf"))
        view = _build_view()
        request = _build_request()
        kwargs = _build_kwargs(instance=instance)

        seen_paths = []

        def _capture(u):
            seen_paths.append(u)
            return f"/srv/{u.lstrip('/')}"

        with patch.dict("os.environ", {"STORAGE_TYPE": "SHAREPOINT"}, clear=False), \
             patch("lex.api.views.file_operations.FileDownload.SharePointContext",
                   return_value=shrp_ctx), \
             patch("lex.api.views.file_operations.FileDownload.get_server_relative_path",
                   side_effect=_capture):
            view.get(request, **kwargs)

        # `get_server_relative_path` is called twice — once with the
        # raw URL (for the get-file lookup), once with the lstripped
        # url (for `open_binary`). The second call MUST receive the
        # leading-slash-stripped version.
        self.assertEqual(len(seen_paths), 2)
        self.assertTrue(
            seen_paths[0].startswith("/"),
            "First call uses raw `file_obj.url` — keeps leading slash.",
        )
        self.assertFalse(
            seen_paths[1].startswith("/"),
            "Second call uses `rel_url = file_obj.url.lstrip('/')` — "
            "must NOT start with '/' or the SDK builds a malformed "
            "server-relative path.",
        )


# ---------------------------------------------------------------------------
# 10.52 / 10.55 — cloud storage URL branches
# ---------------------------------------------------------------------------


class TestCluster10l_GCSStorage(SimpleTestCase):
    """10.52 + 10.55: cloud storage returns a signed/public URL envelope.

    The browser downloads directly from cloud storage — the API never streams the
    bytes through Django. A regression that returned `FileResponse`
    instead (e.g. someone reused the LOCAL branch by mistake) would
    burn server bandwidth + memory on every download and also break
    very large files that exceed Django's request timeout.
    """

    def test_10_52_gcs_returns_download_url_json(self):
        file_obj = SimpleNamespace(url="https://storage.googleapis.com/bucket/file.pdf?signed")
        instance = SimpleNamespace(document=file_obj)
        view = _build_view()
        request = _build_request()
        kwargs = _build_kwargs(instance=instance)

        with patch.dict("os.environ", {"STORAGE_TYPE": "GCS"}, clear=False):
            response = view.get(request, **kwargs)

        self.assertIsInstance(
            response, JsonResponse,
            "GCS branch must return JsonResponse (not FileResponse) — "
            "the browser fetches from GCS directly via the signed URL.",
        )
        self.assertEqual(response.status_code, 200)
        # JsonResponse exposes the JSON via .content; quick assertion
        # rather than full JSON parse since the body is a tiny dict.
        self.assertIn(
            b"https://storage.googleapis.com/bucket/file.pdf?signed",
            response.content,
            "Response body must carry the full signed URL — drift that "
            "stripped query params would break the GCS signature check.",
        )
        self.assertIn(b"download_url", response.content)

    def test_10_55_azure_blob_returns_download_url_json(self):
        file_obj = SimpleNamespace(
            url="https://lexstore.blob.core.windows.net/uploads/file.pdf?signed"
        )
        instance = SimpleNamespace(document=file_obj)
        view = _build_view()
        request = _build_request()
        kwargs = _build_kwargs(instance=instance)

        with patch.dict("os.environ", {"STORAGE_TYPE": "AZURE_BLOB"}, clear=False):
            response = view.get(request, **kwargs)

        self.assertIsInstance(
            response,
            JsonResponse,
            "AZURE_BLOB branch must match GCS semantics: the browser fetches "
            "the signed Blob URL directly instead of streaming through Django.",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"https://lexstore.blob.core.windows.net/uploads/file.pdf?signed",
            response.content,
        )
        self.assertIn(b"download_url", response.content)

