"""MCP tools for file/SharePoint/PDF endpoints.

Each tool delegates to the existing DRF view so storage-type behaviour
(SharePoint vs GCS vs LOCAL), permission filtering and audit hooks
stay identical to the regular HTTP API.

Binary responses are streamed through the dispatcher and emitted as
``{file: {name, content_type, size_bytes, base64, encoding}}`` envelopes
with hard size caps (see ``FILE_MAX_BYTES`` / ``EXPORT_MAX_BYTES``).

* ``lex.files.download``        \u2192 :class:`lex.api.views.file_operations.FileDownload.FileDownloadView`
* ``lex.files.export``          \u2192 :class:`lex.api.views.file_operations.ModelExport.ModelExportView`
* ``lex.sharepoint.download``   \u2192 :class:`lex.api.views.sharepoint.SharePointFileDownload.SharePointFileDownload`
* ``lex.sharepoint.preview_link`` \u2192 :class:`lex.api.views.sharepoint.SharePointPreview.SharePointPreview`
* ``lex.sharepoint.share_link`` \u2192 :class:`lex.api.views.sharepoint.SharePointShareLink.SharePointShareLink`
* ``lex.calculations.download_pdf`` \u2192 :class:`lex.api.views.calculations.DownloadMarkdownPdf.DownloadMarkdownPdf`
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

from lex.mcp_server.config import mcp_setting
from lex.mcp_server.dispatch import call_view
from lex.mcp_server.tools._common import (
    binary_envelope,
    clamp_max_bytes,
    current_principal,
    require_container as _require_container,
)

logger = logging.getLogger(__name__)


def _model_collection():
    """Lazy-resolve the ``processAdminSite.model_collection`` reference."""
    from lex.process_admin.settings import processAdminSite

    if not processAdminSite.initialized:
        _ = processAdminSite.urls
    return processAdminSite.model_collection


def register(server: FastMCP) -> None:
    server.add_tool(
        _file_download,
        name="lex_files_download",
        description=(
            "Download a file field from a model entry. Returns a base64 "
            "payload for LOCAL/SharePoint storage (capped by FILE_MAX_BYTES) "
            "or a `{download_url}` pass-through for GCS-backed storage."
        ),
    )
    server.add_tool(
        _model_export,
        name="lex_files_export",
        description=(
            "Export a model container to xlsx via the same pipeline used by "
            "the AG Grid UI. Forward `ag_export`, `filtered_export` and "
            "`as_of` exactly as the front-end does. Capped by EXPORT_MAX_BYTES."
        ),
    )
    server.add_tool(
        _sharepoint_download,
        name="lex_sharepoint_download",
        description=(
            "Download a file field via SharePoint storage (uses the configured "
            "default_storage). Returns a base64 envelope capped by FILE_MAX_BYTES."
        ),
    )
    server.add_tool(
        _sharepoint_preview_link,
        name="lex_sharepoint_preview_link",
        description="Return an embeddable SharePoint preview URL for the given file field.",
    )
    server.add_tool(
        _sharepoint_share_link,
        name="lex_sharepoint_share_link",
        description="Return a SharePoint share URL for the given file field.",
    )
    server.add_tool(
        _calculation_pdf,
        name="lex_calculations_download_pdf",
        description=(
            "Render a CalculationLog markdown report to PDF and return it as "
            "a base64 envelope capped by FILE_MAX_BYTES."
        ),
    )


# --------------------------------------------------------------------------- #
# Tool implementations                                                        #
# --------------------------------------------------------------------------- #


async def _file_download(
    model_container: str,
    pk: Any,
    field: str,
    max_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    container = _require_container(model_container)
    principal = current_principal()

    from lex.api.views.file_operations.FileDownload import FileDownloadView

    status_code, payload = await call_view(
        FileDownloadView,
        principal=principal,
        method="GET",
        view_kwargs={"model_container": container},
        query={"pk": pk, "field": field},
        view_init_kwargs={"model_collection": _model_collection()},
    )
    cap = clamp_max_bytes(max_bytes, int(mcp_setting("FILE_MAX_BYTES")))
    return _wrap_binary_or_pass_through(status_code, payload, cap, default_name=field)


async def _model_export(
    model_container: str,
    ag_export: Optional[Dict[str, Any]] = None,
    filtered_export: Optional[str] = None,
    as_of: Optional[str] = None,
    max_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    container = _require_container(model_container)
    principal = current_principal()

    from lex.api.views.file_operations.ModelExport import ModelExportView

    body: Dict[str, Any] = {}
    if ag_export is not None:
        body["ag_export"] = ag_export
    if filtered_export is not None:
        body["filtered_export"] = filtered_export

    query: Dict[str, Any] = {}
    if as_of:
        query["as_of"] = as_of

    status_code, payload = await call_view(
        ModelExportView,
        principal=principal,
        method="POST",
        view_kwargs={"model_container": container},
        query=query,
        body=body,
        view_init_kwargs={"model_collection": _model_collection()},
    )
    cap = clamp_max_bytes(max_bytes, int(mcp_setting("EXPORT_MAX_BYTES")))
    return _wrap_binary_or_pass_through(
        status_code, payload, cap, default_name=f"{model_container}.xlsx"
    )


async def _sharepoint_download(
    model_container: str,
    pk: Any,
    field: str,
    max_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    container = _require_container(model_container)
    principal = current_principal()

    from lex.api.views.sharepoint.SharePointFileDownload import SharePointFileDownload

    status_code, payload = await call_view(
        SharePointFileDownload,
        principal=principal,
        method="GET",
        view_kwargs={"model_container": container},
        query={"pk": pk, "field": field},
    )
    cap = clamp_max_bytes(max_bytes, int(mcp_setting("FILE_MAX_BYTES")))
    return _wrap_binary_or_pass_through(status_code, payload, cap, default_name=field)


async def _sharepoint_preview_link(
    model_container: str,
    pk: Any,
    field: str,
) -> Dict[str, Any]:
    container = _require_container(model_container)
    principal = current_principal()

    from lex.api.views.sharepoint.SharePointPreview import SharePointPreview

    status_code, payload = await call_view(
        SharePointPreview,
        principal=principal,
        method="GET",
        view_kwargs={"model_container": container},
        query={"pk": pk, "field": field},
    )
    return _wrap_binary_or_pass_through(status_code, payload, max_bytes=0)


async def _sharepoint_share_link(
    model_container: str,
    pk: Any,
    field: str,
) -> Dict[str, Any]:
    container = _require_container(model_container)
    principal = current_principal()

    from lex.api.views.sharepoint.SharePointShareLink import SharePointShareLink

    status_code, payload = await call_view(
        SharePointShareLink,
        principal=principal,
        method="GET",
        view_kwargs={"model_container": container},
        query={"pk": pk, "field": field},
    )
    return _wrap_binary_or_pass_through(status_code, payload, max_bytes=0)


async def _calculation_pdf(
    pk: Any,
    max_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    principal = current_principal()

    from lex.api.views.calculations.DownloadMarkdownPdf import DownloadMarkdownPdf

    status_code, payload = await call_view(
        DownloadMarkdownPdf,
        principal=principal,
        method="GET",
        view_kwargs={"pk": pk},
    )
    cap = clamp_max_bytes(max_bytes, int(mcp_setting("FILE_MAX_BYTES")))
    return _wrap_binary_or_pass_through(
        status_code, payload, cap, default_name=f"calculation_{pk}.pdf"
    )


# --------------------------------------------------------------------------- #
# Internal                                                                    #
# --------------------------------------------------------------------------- #


def _wrap_binary_or_pass_through(
    status_code: int,
    payload: Any,
    max_bytes: int,
    *,
    default_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply ``binary_envelope`` for binary payloads, fall back to status echo otherwise."""
    if isinstance(payload, dict) and payload.get("_binary") is True:
        env = binary_envelope(payload, max_bytes=max_bytes, default_filename=default_name)
        # Preserve upstream status code when the response was OK-shaped binary
        # but the view returned a non-2xx (e.g. 404). ``binary_envelope``
        # already passes through ``status_code`` from the decoded dict.
        return env

    # JSON / pass-through payloads (e.g. GCS download_url, preview/share links).
    return {"status": status_code, "result": payload}
