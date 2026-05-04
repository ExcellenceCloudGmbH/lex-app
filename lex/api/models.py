# API models will be added here as needed (typically API apps don't have models)


from django.contrib.postgres.search import SearchVectorField
from django.db import models


class LexSearchDocument(models.Model):
    """
    Denormalized search document — one row per indexed model instance.

    Populated incrementally via the ``post_save`` / ``post_delete`` signals
    wired in ``lex.process_admin.sites.process_admin_site.ProcessAdminSite.register``
    and read by :class:`lex.api.views.global_search_for_models.backends.IndexedSearchBackend`.

    Read-side query::

        SELECT ... FROM lex_search_document
         WHERE tsv @@ websearch_to_tsquery(:q) OR title %% :q OR body %% :q
         ORDER BY ts_rank(tsv, q) DESC LIMIT :n;

    GIN indexes on ``tsv`` and on ``(title || ' ' || body) gin_trgm_ops``
    make this a single-digit-millisecond lookup even with millions of rows.

    The row carries everything the listing UI needs (``container_id``,
    ``object_id``, ``model_label``, ``title``, ``body`` source for the
    snippet, ``url``) so a hit doesn't require a join back to the
    originating model — the join only happens on the (≤50-row) page
    actually returned to the user, where it's needed for the per-row
    permission check.
    """

    # ``container_id`` is the same string used in URL routes
    # (``/<container_id>/<pk>/show``). Stored as plain text instead of an
    # FK to ``ContentType`` because containers and Django models are not
    # 1:1 in lex-app (legacy / dynamic models).
    container_id = models.CharField(max_length=255, db_index=True)
    object_id = models.CharField(max_length=64)

    # Optional ContentType pointer — kept for future joins / cross-app
    # tooling. Nullable so dynamic legacy models that don't map to a
    # ContentType can still be indexed.
    content_type = models.ForeignKey(
        "contenttypes.ContentType",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )

    # Human-readable label for the container (e.g. "Accounting Provision").
    model_label = models.CharField(max_length=255, blank=True, default="")

    # ``str(instance)`` — what the result list renders as the row title.
    title = models.TextField(blank=True, default="")
    # Concatenation of the searchable text fields, capped to keep the
    # index size bounded. See ``indexer.MAX_BODY_CHARS``.
    body = models.TextField(blank=True, default="")
    # Pre-computed deep-link target so the read path doesn't have to
    # know about URL conventions.
    url = models.CharField(max_length=512, blank=True, default="")

    # ``tsv`` is materialised as a Postgres ``GENERATED ... STORED`` column
    # by the migration; declaring it here (without managing it via
    # Django) lets the ORM use it in ``F('tsv')`` / ``filter(tsv=…)``
    # while keeping the column read-only.
    tsv = SearchVectorField(null=True, editable=False)

    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        app_label = "api"
        db_table = "lex_search_document"
        constraints = [
            models.UniqueConstraint(
                fields=["container_id", "object_id"],
                name="lex_search_document_container_object_uniq",
            ),
        ]
        indexes = [
            # Fast filter for ``?model=<container_id>``.
            models.Index(fields=["container_id"], name="lex_search_doc_container_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover — debug helper
        return f"{self.container_id}#{self.object_id} :: {self.title[:60]}"
