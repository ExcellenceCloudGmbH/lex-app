"""Chunked uploads: files larger than one request body can carry.

Every request to an instance passes three body limits of about 100 MB before
Django sees it: Cloudflare (100 MB on the Free and Pro plans, 500 MB even on
Enterprise), the nginx Ingress (``proxy-body-size: 100M``) and the cluster-wide
ModSecurity ``SecRequestBodyLimit`` of 100 MiB. A file above that -- a 606 MB
claims CSV, for instance -- fails in the browser as "Failed to fetch" without
reaching the backend at all, and no infrastructure setting can lift the
Cloudflare limit far enough.

So a large file is sent in pieces instead, each well below every limit:

1. ``POST   api/chunked-uploads/``                       ``{filename, size}``
   -> ``{upload_id, chunk_size, chunk_count}``
2. ``POST   api/chunked-uploads/<upload_id>/chunks/<index>``  multipart ``chunk``
   (once per piece, any order, a retried piece simply overwrites itself)
3. The create / update request sends ``lex-chunked-upload:<upload_id>`` as the
   value of the file field. :class:`LexClearableFileField` hands that token to
   :func:`resolve_upload_token`, which joins the pieces into one temporary
   file, and the record is saved exactly as with a direct multipart upload.
4. ``DELETE api/chunked-uploads/<upload_id>`` once the client is done with it.
   Uploads nobody deletes are removed after :data:`UPLOAD_TTL`.

The pieces are kept in the instance's default storage. That is the one place
every backend replica shares whatever ``STORAGE_TYPE`` is (the uploads volume
for LEGACY, the bucket for GCS), so a piece can land on one pod and the create
request on another.

An upload belongs to the user who started it; nobody else can add to it,
read it into a record, or delete it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from typing import Optional

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import TemporaryUploadedFile
from django.http import HttpResponse, JsonResponse
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from lex.api.utils.api_key_requests import HasAPIKey

logger = logging.getLogger(__name__)

#: Prefix of the file-field value that refers to a finished chunked upload.
TOKEN_PREFIX = "lex-chunked-upload:"

#: Piece size. Small enough for every hop with room to spare: the piece is a
#: multipart file part, so ModSecurity counts it against its 100 MiB body
#: limit but not against the 50 MiB limit for non-file content.
CHUNK_SIZE = int(getattr(settings, "LEX_CHUNKED_UPLOAD_CHUNK_SIZE", 32 * 1024 * 1024))

#: Largest file accepted through this path.
MAX_UPLOAD_SIZE = int(
    getattr(settings, "LEX_CHUNKED_UPLOAD_MAX_SIZE", 5 * 1024 * 1024 * 1024)
)

#: Unfinished or forgotten uploads older than this are deleted.
UPLOAD_TTL = int(getattr(settings, "LEX_CHUNKED_UPLOAD_TTL_SECONDS", 24 * 60 * 60))

STAGING_DIR = "_chunked_uploads"

_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class ChunkedUploadError(Exception):
    """A token that cannot be turned into a file. The message is user-facing."""


# ---------------------------------------------------------------------------
# storage layout
# ---------------------------------------------------------------------------


def _upload_dir(upload_id: str) -> str:
    return f"{STAGING_DIR}/{upload_id}"


def _manifest_path(upload_id: str) -> str:
    return f"{_upload_dir(upload_id)}/manifest.json"


def _chunk_path(upload_id: str, index: int) -> str:
    # Zero-padded so a listing sorts in order; 6 digits cover 5 GiB / 32 MiB.
    return f"{_upload_dir(upload_id)}/{index:06d}.part"


def _owner_of(request) -> str:
    user = getattr(request, "user", None)
    name = getattr(user, "username", None) or getattr(user, "pk", None)
    return str(name) if name else ""


def _chunk_count(size: int, chunk_size: int) -> int:
    return max(1, -(-size // chunk_size))


def _expected_chunk_length(manifest: dict, index: int) -> int:
    size, chunk_size = manifest["size"], manifest["chunk_size"]
    if index < manifest["chunk_count"] - 1:
        return chunk_size
    return size - chunk_size * (manifest["chunk_count"] - 1)


def _save_exact(path: str, content) -> None:
    """Write ``path``, replacing it. Storages rename on collision otherwise."""
    if default_storage.exists(path):
        default_storage.delete(path)
    saved = default_storage.save(path, content)
    if saved != path:
        # A concurrent writer won the name; keep exactly one copy.
        default_storage.delete(saved)


def _load_manifest(upload_id: str) -> Optional[dict]:
    if not _UPLOAD_ID_RE.match(upload_id or ""):
        return None
    path = _manifest_path(upload_id)
    try:
        if not default_storage.exists(path):
            return None
        with default_storage.open(path, "rb") as fh:
            return json.loads(fh.read().decode("utf-8"))
    except Exception:
        logger.warning("Unreadable chunked-upload manifest %s", path, exc_info=True)
        return None


def _delete_upload(upload_id: str) -> None:
    directory = _upload_dir(upload_id)
    try:
        _dirs, files = default_storage.listdir(directory)
    except Exception:
        files = []
    for name in files:
        try:
            default_storage.delete(f"{directory}/{name}")
        except Exception:
            logger.warning("Could not delete %s/%s", directory, name, exc_info=True)
    # FileSystemStorage leaves the empty directory behind.
    path = getattr(default_storage, "path", None)
    if path is not None:
        try:
            os.rmdir(default_storage.path(directory))
        except Exception:
            pass


def _sweep_expired(now: Optional[float] = None) -> None:
    """Delete uploads older than :data:`UPLOAD_TTL`. Best effort."""
    now = now if now is not None else time.time()
    try:
        upload_ids, _files = default_storage.listdir(STAGING_DIR)
    except Exception:
        return
    for upload_id in upload_ids:
        upload_id = upload_id.rstrip("/")
        if not _UPLOAD_ID_RE.match(upload_id):
            continue
        manifest = _load_manifest(upload_id)
        if manifest is not None:
            created = manifest.get("created_at")
            expired = not isinstance(created, (int, float)) or now - created > UPLOAD_TTL
        else:
            # No readable manifest: debris of a start that failed half-way,
            # or one being written right now. Judge it by its newest file.
            expired = _newest_file_age(upload_id, now) > UPLOAD_TTL
        if expired:
            _delete_upload(upload_id)


def _newest_file_age(upload_id: str, now: float) -> float:
    directory = _upload_dir(upload_id)
    try:
        _dirs, files = default_storage.listdir(directory)
        times = [default_storage.get_modified_time(f"{directory}/{f}").timestamp() for f in files]
    except Exception:
        return 0.0
    return now - max(times) if times else float("inf")


def _missing_chunks(upload_id: str, manifest: dict) -> list:
    return [
        index
        for index in range(manifest["chunk_count"])
        if not default_storage.exists(_chunk_path(upload_id, index))
    ]


# ---------------------------------------------------------------------------
# serializer hook
# ---------------------------------------------------------------------------


def is_upload_token(value) -> bool:
    return isinstance(value, str) and value.startswith(TOKEN_PREFIX)


def resolve_upload_token(token: str, request) -> TemporaryUploadedFile:
    """Return the finished upload ``token`` names as an uploaded file.

    A request validates its serializer more than once (permission check,
    no-op check, save), so the joined file is built once per request and
    handed out again, rewound, on every later call.
    """
    upload_id = token[len(TOKEN_PREFIX):].strip()
    cache = getattr(request, "_lex_chunked_uploads", None)
    if cache is None:
        cache = {}
        try:
            setattr(request, "_lex_chunked_uploads", cache)
        except Exception:
            pass
    if upload_id in cache:
        uploaded = cache[upload_id]
        uploaded.seek(0)
        return uploaded

    manifest = _load_manifest(upload_id)
    if manifest is None:
        raise ChunkedUploadError("The uploaded file has expired or does not exist. Upload it again.")
    if manifest.get("owner") != _owner_of(request):
        # Same answer as "does not exist": an upload id is not a capability.
        raise ChunkedUploadError("The uploaded file has expired or does not exist. Upload it again.")
    missing = _missing_chunks(upload_id, manifest)
    if missing:
        raise ChunkedUploadError(
            f"The upload is incomplete: {len(missing)} of {manifest['chunk_count']} parts are missing."
        )

    uploaded = TemporaryUploadedFile(
        name=manifest["filename"],
        content_type=manifest.get("content_type") or "application/octet-stream",
        size=manifest["size"],
        charset=None,
    )
    written = 0
    for index in range(manifest["chunk_count"]):
        with default_storage.open(_chunk_path(upload_id, index), "rb") as part:
            while True:
                block = part.read(4 * 1024 * 1024)
                if not block:
                    break
                uploaded.write(block)
                written += len(block)
    if written != manifest["size"]:
        uploaded.close()
        raise ChunkedUploadError(
            f"The upload is damaged: expected {manifest['size']} bytes, found {written}. Upload it again."
        )
    uploaded.flush()
    uploaded.seek(0)
    cache[upload_id] = uploaded
    return uploaded


# ---------------------------------------------------------------------------
# views
# ---------------------------------------------------------------------------


def _error(message: str, status: int) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


class ChunkedUploadStartView(APIView):
    http_method_names = ["post"]
    permission_classes = [HasAPIKey | IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request, *args, **kwargs):
        owner = _owner_of(request)
        if not owner:
            return _error("Authentication required.", 401)

        filename = os.path.basename(str(request.data.get("filename", "")).replace("\\", "/")).strip()
        if not filename or filename in {".", ".."} or len(filename) > 255:
            return _error("'filename' is required.", 400)
        try:
            size = int(request.data.get("size"))
        except (TypeError, ValueError):
            return _error("'size' must be the file size in bytes.", 400)
        if size <= 0:
            return _error("'size' must be positive.", 400)
        if size > MAX_UPLOAD_SIZE:
            return _error(
                f"The file is {size} bytes; at most {MAX_UPLOAD_SIZE} bytes can be uploaded.", 413
            )

        _sweep_expired()

        upload_id = secrets.token_hex(16)
        manifest = {
            "owner": owner,
            "filename": filename,
            "content_type": str(request.data.get("content_type") or "")[:255],
            "size": size,
            "chunk_size": CHUNK_SIZE,
            "chunk_count": _chunk_count(size, CHUNK_SIZE),
            "created_at": time.time(),
        }
        _save_exact(_manifest_path(upload_id), ContentFile(json.dumps(manifest).encode("utf-8")))
        return JsonResponse(
            {
                "upload_id": upload_id,
                "token": f"{TOKEN_PREFIX}{upload_id}",
                "chunk_size": manifest["chunk_size"],
                "chunk_count": manifest["chunk_count"],
            },
            status=201,
        )


class _OwnedUploadView(APIView):
    permission_classes = [HasAPIKey | IsAuthenticated]

    def _manifest_for(self, request, upload_id):
        manifest = _load_manifest(upload_id)
        if manifest is None or manifest.get("owner") != _owner_of(request):
            return None
        return manifest


class ChunkedUploadChunkView(_OwnedUploadView):
    http_method_names = ["post", "put"]
    parser_classes = [MultiPartParser]

    def post(self, request, upload_id, index, *args, **kwargs):
        manifest = self._manifest_for(request, upload_id)
        if manifest is None:
            return _error("Upload not found.", 404)
        if not 0 <= index < manifest["chunk_count"]:
            return _error(f"Part {index} is out of range (0..{manifest['chunk_count'] - 1}).", 400)
        chunk = request.FILES.get("chunk")
        if chunk is None:
            return _error("The part must be sent as the multipart file field 'chunk'.", 400)
        expected = _expected_chunk_length(manifest, index)
        if chunk.size != expected:
            return _error(f"Part {index} must be {expected} bytes, got {chunk.size}.", 400)
        _save_exact(_chunk_path(upload_id, index), chunk)
        return JsonResponse({"upload_id": upload_id, "index": index, "size": chunk.size})

    put = post


class ChunkedUploadDetailView(_OwnedUploadView):
    http_method_names = ["get", "delete"]

    def get(self, request, upload_id, *args, **kwargs):
        """Which parts the server already has, so a client can resume."""
        manifest = self._manifest_for(request, upload_id)
        if manifest is None:
            return _error("Upload not found.", 404)
        missing = set(_missing_chunks(upload_id, manifest))
        return JsonResponse(
            {
                "upload_id": upload_id,
                "filename": manifest["filename"],
                "size": manifest["size"],
                "chunk_size": manifest["chunk_size"],
                "chunk_count": manifest["chunk_count"],
                "received": [i for i in range(manifest["chunk_count"]) if i not in missing],
                "complete": not missing,
            }
        )

    def delete(self, request, upload_id, *args, **kwargs):
        manifest = self._manifest_for(request, upload_id)
        if manifest is None:
            return _error("Upload not found.", 404)
        _delete_upload(upload_id)
        return HttpResponse(status=204)
