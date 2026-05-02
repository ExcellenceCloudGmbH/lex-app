"""
Search backends for the global search endpoint.

Two strategies are provided:

* :class:`PostgresSearchBackend` — uses ``SearchVector`` + ``SearchQuery``
  (websearch syntax) + ``SearchRank`` for full-text search. Falls back to
  ``icontains`` if the Postgres FTS query raises (e.g. on a database that
  *says* it's Postgres but is missing required extensions).
* :class:`FallbackSearchBackend` — token-AND ``icontains`` search across
  the allowed text fields. Works on any DB engine (SQLite, MySQL, …) so
  global search remains usable in dev/test environments.

The right backend is chosen via :func:`get_backend_for_connection` based
on ``connection.vendor``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from django.db import connection
from django.db.models import Q, QuerySet


SNIPPET_LENGTH = 120
SNIPPET_ELLIPSIS = "…"

# Field internal types we *include* — narrow allowlist of textual fields.
# Anything else (numeric, boolean, FK, file, M2M, reverse relations, …)
# is silently skipped so a query like "100" doesn't crash on a
# DecimalField and so reverse relations don't poison ``SearchVector``.
SEARCHABLE_FIELD_TYPES = {
    "CharField",
    "TextField",
    "EmailField",
    "SlugField",
    "URLField",
    "UUIDField",
}


@dataclass
class SearchHit:
    """Single match returned by a backend."""

    pk: object
    title: str
    snippet: str
    rank: float
    matched_field: str | None
    # The actual model instance the hit came from. Kept on the hit so
    # callers (e.g. the per-row permission check in ``Search``) don't
    # have to issue another ``objects.get(pk=...)`` per result, which
    # quickly blows past the search time budget on large models.
    obj: object | None = None


def get_searchable_field_names(model_class) -> List[str]:
    """
    Return the list of concrete text-field names safe to search on
    ``model_class``.

    Honors an optional opt-in override:
      ``class SearchMeta: fields = ['name', 'description', 'customer__name']``
    on the model. When present, the override wins (so model authors can
    expose FK-traversal labels like ``customer__name``).
    """
    meta = getattr(model_class, "SearchMeta", None)
    override = getattr(meta, "fields", None) if meta is not None else None
    if override:
        return list(override)

    fields: List[str] = []
    for f in model_class._meta.get_fields(include_parents=False):
        # Skip reverse relations / M2M / generic relations entirely —
        # they don't have ``get_internal_type`` in a useful way and
        # ``SearchVector`` will choke on them.
        if not getattr(f, "concrete", False):
            continue
        if f.is_relation:
            continue
        try:
            internal = f.get_internal_type()
        except Exception:
            continue
        if internal in SEARCHABLE_FIELD_TYPES:
            fields.append(f.name)
    return fields


def _make_snippet(value: str, query_tokens: Sequence[str]) -> str:
    """Build a ~SNIPPET_LENGTH-character window around the first token hit."""
    if not value:
        return ""
    text = str(value)
    lower = text.lower()
    hit_at = -1
    for tok in query_tokens:
        if not tok:
            continue
        idx = lower.find(tok.lower())
        if idx != -1 and (hit_at == -1 or idx < hit_at):
            hit_at = idx
    if hit_at == -1:
        return text[:SNIPPET_LENGTH] + (SNIPPET_ELLIPSIS if len(text) > SNIPPET_LENGTH else "")

    half = SNIPPET_LENGTH // 2
    start = max(0, hit_at - half)
    end = min(len(text), start + SNIPPET_LENGTH)
    snippet = text[start:end]
    if start > 0:
        snippet = SNIPPET_ELLIPSIS + snippet
    if end < len(text):
        snippet = snippet + SNIPPET_ELLIPSIS
    return snippet


def _tokenize(query: str) -> List[str]:
    return [t for t in re.split(r"\s+", (query or "").strip()) if t]


# ─────────────────────────────────────────────────────────────────────
# Backends
# ─────────────────────────────────────────────────────────────────────


class FallbackSearchBackend:
    """``icontains`` token-AND search. Works on every DB engine."""

    name = "fallback"

    # Hard per-query timeout (Postgres only). Without an index, an
    # ``icontains`` over many TEXT columns on a multi-million-row table
    # can run for tens of seconds and starve every other model in the
    # global search loop. ``statement_timeout`` aborts the query at the
    # database level so the loop can move on.
    PG_STATEMENT_TIMEOUT_MS = 800

    def _run_with_timeout(self, qs: "QuerySet"):
        """Materialise ``qs`` honouring a Postgres ``statement_timeout``.

        Falls back to a plain ``list(qs)`` on non-Postgres backends.
        """
        if connection.vendor != "postgresql":
            return list(qs)

        try:
            # Set the timeout at session scope. Each search worker
            # thread owns its own connection (we ``close_old_connections``
            # at the end of the worker), so this can't leak across
            # requests. ``SET LOCAL`` would require a wrapping
            # transaction which doesn't always cover lazy queryset
            # evaluation cleanly.
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SET statement_timeout = {self.PG_STATEMENT_TIMEOUT_MS}"
                )
            try:
                return list(qs)
            finally:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SET statement_timeout = 0")
                except Exception:
                    pass
        except Exception:
            # Timeout (or any other DB error) — treat as "no hits" so
            # the global search keeps moving across other models.
            return []

    def search(
        self,
        model_class,
        query: str,
        fields: Sequence[str],
        *,
        max_rows: int,
    ) -> List[SearchHit]:
        tokens = _tokenize(query)
        if not tokens or not fields:
            return []

        combined = Q()
        for token in tokens:
            per_token = Q()
            for field in fields:
                per_token |= Q(**{f"{field}__icontains": token})
            combined &= per_token

        try:
            qs: QuerySet = model_class.objects.filter(combined).order_by("-pk")[: max_rows]
            rows = self._run_with_timeout(qs)
        except Exception:
            return []

        hits: List[SearchHit] = []
        for obj in rows:
            best_field = None
            best_value = ""
            best_score = 0
            for field in fields:
                try:
                    raw = getattr(obj, field, None)
                except Exception:
                    raw = None
                if raw is None:
                    continue
                value = str(raw)
                score = sum(1 for t in tokens if t.lower() in value.lower())
                if score > best_score or (score == best_score and len(value) > len(best_value)):
                    best_field = field
                    best_value = value
                    best_score = score

            snippet = _make_snippet(best_value, tokens) if best_value else str(obj)
            hits.append(
                SearchHit(
                    pk=obj.pk,
                    title=str(obj),
                    snippet=snippet,
                    rank=float(best_score),
                    matched_field=best_field,
                    obj=obj,
                )
            )
        return hits


class PostgresSearchBackend:
    """
    Postgres full-text search via ``SearchVector`` + ``SearchQuery``
    (websearch syntax) + ``SearchRank``. Degrades to the fallback
    backend if any FTS-specific query raises at execution time.
    """

    name = "postgres"

    def __init__(self) -> None:
        self._fallback = FallbackSearchBackend()

    def search(
        self,
        model_class,
        query: str,
        fields: Sequence[str],
        *,
        max_rows: int,
    ) -> List[SearchHit]:
        tokens = _tokenize(query)
        if not tokens or not fields:
            return []

        try:
            from django.contrib.postgres.search import (
                SearchQuery,
                SearchRank,
                SearchVector,
            )
        except Exception:
            return self._fallback.search(model_class, query, fields, max_rows=max_rows)

        try:
            vector = SearchVector(*fields)
            try:
                pg_query = SearchQuery(query, search_type="websearch")
            except TypeError:
                pg_query = SearchQuery(query)
            qs = (
                model_class.objects.annotate(_lex_search_vector=vector)
                .filter(_lex_search_vector=pg_query)
                .annotate(_lex_search_rank=SearchRank(vector, pg_query))
                .order_by("-_lex_search_rank", "-pk")[: max_rows]
            )
            rows = self._fallback._run_with_timeout(qs)
        except Exception:
            return self._fallback.search(model_class, query, fields, max_rows=max_rows)

        # Postgres FTS works on stemmed lexemes and word boundaries, so it
        # misses substring matches (e.g. ``kl`` inside ``klausur``) and
        # short / stop-word tokens. Fall back to ``icontains`` whenever
        # FTS produces no hits so exact substring searches still work.
        if not rows:
            return self._fallback.search(model_class, query, fields, max_rows=max_rows)

        hits: List[SearchHit] = []
        for obj in rows:
            # Pick the first non-empty searchable field for snippet/title hint.
            best_field = None
            best_value = ""
            for field in fields:
                try:
                    raw = getattr(obj, field, None)
                except Exception:
                    raw = None
                if raw is None:
                    continue
                value = str(raw)
                if not best_value or any(t.lower() in value.lower() for t in tokens):
                    best_field = field
                    best_value = value
                    if any(t.lower() in value.lower() for t in tokens):
                        break

            snippet = _make_snippet(best_value, tokens) if best_value else str(obj)
            hits.append(
                SearchHit(
                    pk=obj.pk,
                    title=str(obj),
                    snippet=snippet,
                    rank=float(getattr(obj, "_lex_search_rank", 0.0) or 0.0),
                    matched_field=best_field,
                    obj=obj,
                )
            )
        return hits


@dataclass
class IndexedHit:
    """One row from the unified ``LexSearchDocument`` index."""

    container_id: str
    object_id: str
    model_label: str
    title: str
    body: str
    url: str
    rank: float


class IndexedSearchBackend:
    """
    Single-query search over :class:`lex.api.models.LexSearchDocument`.

    Postgres only. The index is maintained incrementally by
    :mod:`lex.api.views.global_search_for_models.indexer`, so a search
    is one indexed lookup instead of fanning out across every
    registered model.

    Returns plain dicts (not the per-model ``SearchHit``) because the
    caller doesn't need to refetch the originating row to render the
    listing — every field the listing needs is denormalized into the
    document itself. Permission filtering happens after the query, on
    the bounded result page, in :class:`Search`.
    """

    name = "indexed"

    def search(
        self,
        query: str,
        *,
        limit: int,
        offset: int = 0,
        model: Optional[str] = None,
        overfetch: int = 3,
    ) -> tuple[List[IndexedHit], int]:
        """Run the index lookup.

        ``overfetch`` widens the candidate set by ``limit*overfetch`` so
        the per-row permission post-filter has room to discard hits
        without leaving the page short.

        Returns ``(hits, total_estimate)``. ``total_estimate`` reflects
        the index-side count *before* the permission filter — the
        view layer is responsible for adjusting if it cares.
        """
        tokens = _tokenize(query)
        if not tokens:
            return [], 0

        # Lazy imports so the module loads before Django apps are ready
        # (e.g. during ``manage.py makemigrations`` of unrelated apps).
        from django.contrib.postgres.search import (
            SearchQuery,
            SearchRank,
        )
        from django.db.models import F, Q

        from lex.api.models import LexSearchDocument

        try:
            pg_query = SearchQuery(query, search_type="websearch", config="simple")
        except TypeError:
            pg_query = SearchQuery(query, config="simple")

        page_size = max(1, limit) * max(1, overfetch)

        def _hydrate(rows):
            return [
                IndexedHit(
                    container_id=row["container_id"],
                    object_id=row["object_id"],
                    model_label=row["model_label"],
                    title=row["title"],
                    body=row["body"] or "",
                    url=row["url"],
                    rank=float(row.get("_lex_rank") or 0.0),
                )
                for row in rows
            ]

        base = LexSearchDocument.objects.all()
        if model:
            base = base.filter(container_id=model)

        # 1) Fast path: GIN-indexed full-text match. This is the only
        #    branch most queries ever take and stays in O(log n) on the
        #    GIN index. We skip ``.count()`` entirely — the user only
        #    needs to know how many results filled the page; an exact
        #    total over 1M+ documents would dominate the response time.
        fts_qs = (
            base.filter(tsv=pg_query)
            .annotate(_lex_rank=SearchRank(F("tsv"), pg_query))
            .order_by("-_lex_rank", "-updated_at")
        )
        rows = list(
            fts_qs.values(
                "container_id",
                "object_id",
                "model_label",
                "title",
                "body",
                "url",
                "_lex_rank",
            )[offset : offset + page_size + 1]
        )
        if rows:
            has_more = len(rows) > page_size
            rows = rows[:page_size]
            # ``has_more`` lets the caller decide whether to advertise
            # paging without paying the cost of an exact count.
            total = offset + len(rows) + (1 if has_more else 0)
            return _hydrate(rows), total

        # 2) Fallback: trigram / substring match against the
        #    denormalized ``title``/``body`` columns. Hits the
        #    ``lex_search_doc_trgm_gin`` index. Reserved for queries
        #    where FTS misses (substrings, partial words, accents).
        sub_qs = (
            base.filter(Q(title__icontains=query) | Q(body__icontains=query))
            .order_by("-updated_at")
        )
        rows = list(
            sub_qs.values(
                "container_id",
                "object_id",
                "model_label",
                "title",
                "body",
                "url",
            )[offset : offset + page_size + 1]
        )
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        total = offset + len(rows) + (1 if has_more else 0)
        return _hydrate(rows), total


def make_snippet(body: str, query: str) -> str:
    """Public helper exposing :func:`_make_snippet` for the indexed backend."""
    return _make_snippet(body or "", _tokenize(query))


def get_backend_for_connection() -> object:
    """Pick a backend based on the active database vendor."""
    try:
        vendor = connection.vendor
    except Exception:
        vendor = "unknown"
    if vendor == "postgresql":
        return PostgresSearchBackend()
    return FallbackSearchBackend()

