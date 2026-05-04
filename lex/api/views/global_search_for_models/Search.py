"""
Global search endpoint.

Backed by the unified :class:`lex.api.models.LexSearchDocument` index
(populated incrementally via the post_save signal hooks in
:mod:`lex.api.views.global_search_for_models.indexer`). One indexed
Postgres query replaces the previous per-model fan-out, taking the
typical response from 5–25 s down to single-digit milliseconds.

Routes:

* ``GET /api/global-search/?q=<query>&limit=<n>&offset=<n>&model=<id>``
  — preferred querystring contract.
* ``GET /api/global-search/<query>``
  — legacy path-based contract; the path segment is read off
  ``self.kwargs['query']`` for backward compatibility.

Response shape (always JSON, never a bare string)::

    {
      "data": [
        {
          "id": "<pk as string>",
          "model": "<container id>",
          "model_label": "<readable model name>",
          "type": "<container title — legacy alias>",
          "url": "/<model id>/<pk>/show",
          "title": "<title from index>",
          "snippet": "<~120-char window around the match>",
          "rank": <float>,
          "matched_field": null,
          "content": {                       # legacy frontend shape
            "id": "<pk as string>",
            "label": "Model: <title>",
            "description": "<snippet>"
          }
        },
        ...
      ],
      "total": <int>,
      "meta": {
        "took_ms": <int>,
        "engine": "indexed",
        "query": "<the original query>"
      }
    }
"""

from __future__ import annotations

import time
from typing import Optional

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey

from lex.api.views.permissions.UserPermission import UserPermission
from lex.process_admin.models.ModelCollection import ModelCollection

from .backends import IndexedSearchBackend, make_snippet


# Hard caps so a single search can't melt anything.
MIN_QUERY_LEN = 2
DEFAULT_LIMIT = 20
MAX_LIMIT = 50


def _parse_int(raw, default: int, *, lo: int, hi: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def _resolve_query(request, kwargs) -> str:
    """Pull the query from ``?q=`` first, then the legacy path kwarg."""
    raw = None
    if hasattr(request, "query_params"):
        raw = request.query_params.get("q")
    if raw is None or raw == "":
        raw = (kwargs or {}).get("query", "") or ""
    return str(raw).strip()


def _qparam(request, name) -> Optional[str]:
    if hasattr(request, "query_params"):
        return request.query_params.get(name)
    return None


def _short_query_response(query: str) -> Response:
    return Response(
        {
            "data": [],
            "total": 0,
            "meta": {
                "took_ms": 0,
                "engine": "n/a",
                "query": query,
                "error": "query too short",
            },
        },
        status=400,
    )


class Search(APIView):
    permission_classes = [HasAPIKey | IsAuthenticated]
    model_collection: ModelCollection = None

    def get(self, request, *args, **kwargs):
        started = time.monotonic()
        query = _resolve_query(request, self.kwargs)

        if len(query) < MIN_QUERY_LEN:
            return _short_query_response(query)

        limit = _parse_int(_qparam(request, "limit"), default=DEFAULT_LIMIT, lo=1, hi=MAX_LIMIT)
        offset = _parse_int(_qparam(request, "offset"), default=0, lo=0, hi=10_000)
        model_filter = _qparam(request, "model") or None

        backend = IndexedSearchBackend()
        try:
            hits, total = backend.search(
                query, limit=limit, offset=offset, model=model_filter
            )
        except Exception:
            # Index missing / migration not yet applied — degrade
            # gracefully to an empty response so the UI keeps working.
            hits, total = [], 0

        # Build a quick lookup so we can call ``has_object_permission``
        # without re-scanning the container list per hit.
        containers_by_id = {
            getattr(c, "id", None): c
            for c in (self.model_collection.all_containers if self.model_collection else [])
        }

        permission = UserPermission()

        # Group hits by container so we can hydrate originals in
        # batched ``filter(pk__in=…)`` queries (one per container) for
        # the per-row permission check.
        by_container: dict[str, list] = {}
        for hit in hits:
            by_container.setdefault(hit.container_id, []).append(hit)

        permitted_pks: dict[str, set] = {}
        for container_id, container_hits in by_container.items():
            container = containers_by_id.get(container_id)
            if container is None:
                # Container was retired but the index hasn't caught up
                # yet — surface the row anyway; the URL will 404 if
                # the user clicks through.
                permitted_pks[container_id] = {h.object_id for h in container_hits}
                continue

            model_class = getattr(container, "model_class", None)
            if model_class is None:
                permitted_pks[container_id] = {h.object_id for h in container_hits}
                continue

            view_for_perm = APIView()
            view_for_perm.kwargs = {"model_container": container}

            # Cheap model-level gate first.
            try:
                if not permission.has_permission(request=request, view=view_for_perm):
                    continue
            except Exception:
                continue

            pks = [h.object_id for h in container_hits]
            try:
                rows = {
                    str(obj.pk): obj
                    for obj in model_class._default_manager.filter(pk__in=pks)
                }
            except Exception:
                rows = {}

            allowed: set = set()
            for hit in container_hits:
                obj = rows.get(hit.object_id)
                if obj is None:
                    continue
                try:
                    if permission.has_object_permission(
                        request=request, view=view_for_perm, obj=obj
                    ):
                        allowed.add(hit.object_id)
                except Exception:
                    continue
            permitted_pks[container_id] = allowed

        # Rebuild the response in original (rank-sorted) order, dropping
        # any hit the user isn't allowed to see.
        data: list[dict] = []
        for hit in hits:
            allowed = permitted_pks.get(hit.container_id)
            if allowed is None or hit.object_id not in allowed:
                continue
            snippet = make_snippet(hit.body or hit.title, query)
            data.append(
                {
                    "id": hit.object_id,
                    "model": hit.container_id,
                    "model_label": hit.model_label,
                    "type": hit.model_label,
                    "url": hit.url or f"/{hit.container_id}/{hit.object_id}/show",
                    "title": hit.title,
                    "snippet": snippet,
                    "rank": hit.rank,
                    "matched_field": None,
                    "content": {
                        "id": hit.object_id,
                        "label": f"Model: {hit.model_label}",
                        "description": snippet or hit.title,
                    },
                }
            )
            if len(data) >= limit:
                break

        took_ms = int((time.monotonic() - started) * 1000)
        return Response(
            {
                "data": data,
                "total": total,
                "meta": {
                    "took_ms": took_ms,
                    "engine": backend.name,
                    "query": query,
                },
            }
        )
