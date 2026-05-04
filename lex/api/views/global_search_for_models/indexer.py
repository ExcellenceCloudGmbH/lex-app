"""
Write side of the global search index.

Listens on ``post_save`` / ``post_delete`` for every model registered with
``ProcessAdminSite``, builds a denormalized
:class:`lex.api.models.LexSearchDocument` for the saved instance, and
upserts it via Celery (so writes don't pay the indexing cost and rolled-
back transactions don't pollute the index).

Public surface:

* :func:`build_document` — instance → ``dict`` ready to upsert.
* :func:`upsert_document_sync` — synchronous upsert (used by tests, the
  backfill command, and the async fallback).
* :func:`delete_document_sync` — synchronous delete.
* :func:`enqueue_index_update` — schedule an async upsert / delete via
  :func:`transaction.on_commit`.
* :func:`connect_indexing_signals` — wire ``post_save`` / ``post_delete``
  for a single model class. Called from
  :class:`lex.process_admin.sites.process_admin_site.ProcessAdminSite.register`.
"""

from __future__ import annotations

import logging
from typing import Optional

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver  # noqa: F401 — re-exported

from lex.api.models import LexSearchDocument

from .backends import get_searchable_field_names

logger = logging.getLogger(__name__)


# Long ``TextField``s would balloon the search index without improving
# recall. Cap the body at a sane limit; the field that actually matched
# is still resolvable from the originating row when the user clicks
# through.
MAX_BODY_CHARS = 4096

# Track which model classes already have signal handlers connected so
# repeated registrations (autoreload, tests) don't fan out duplicates.
_CONNECTED_MODELS: set[type] = set()

# Container metadata is needed to compute the deep-link URL and the
# human-readable label. ``ProcessAdminSite.register`` doesn't have a
# ``container`` yet at the time it calls us — the ``ModelCollection`` is
# materialised lazily — so we look it up via a thunk supplied by
# :func:`set_container_resolver`.
_container_resolver = None


def set_container_resolver(resolver) -> None:
    """Install a callable ``model_class -> container | None``.

    Called once from ``ProcessAdminSite.urls`` after the
    ``ModelCollection`` has been built.
    """
    global _container_resolver
    _container_resolver = resolver


def _resolve_container(model_class):
    if _container_resolver is None:
        return None
    try:
        return _container_resolver(model_class)
    except Exception:
        return None


def _container_id_for(model_class) -> Optional[str]:
    container = _resolve_container(model_class)
    if container is not None:
        cid = getattr(container, "id", None)
        if cid:
            return cid
    # Fallback: the URL-route slug used by the legacy ``model_converter``
    # is the lowercased model name. This keeps the indexer functional
    # even before the container resolver is installed (e.g. signal fires
    # during a fixture load before the urls property is touched).
    return model_class._meta.model_name


def _model_label_for(model_class) -> str:
    container = _resolve_container(model_class)
    if container is not None:
        title = getattr(container, "title", None)
        if title:
            return str(title)
    return model_class._meta.verbose_name.title()


def build_document(instance) -> Optional[dict]:
    """Return the ``LexSearchDocument`` field dict for ``instance``.

    Returns ``None`` if the instance has no searchable fields (the
    indexer treats that as "skip" rather than indexing an empty body).
    """
    model_class = type(instance)
    fields = get_searchable_field_names(model_class)
    if not fields:
        return None

    parts: list[str] = []
    for field in fields:
        try:
            raw = getattr(instance, field, None)
        except Exception:
            raw = None
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            parts.append(text)

    body = " ".join(parts)
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS]

    container_id = _container_id_for(model_class)
    if not container_id:
        return None

    try:
        title = str(instance)
    except Exception:
        title = ""
    if len(title) > MAX_BODY_CHARS:
        title = title[:MAX_BODY_CHARS]

    try:
        ct = ContentType.objects.get_for_model(model_class, for_concrete_model=False)
    except Exception:
        ct = None

    return {
        "container_id": container_id,
        "object_id": str(instance.pk),
        "content_type": ct,
        "model_label": _model_label_for(model_class),
        "title": title,
        "body": body,
        "url": f"/{container_id}/{instance.pk}/show",
    }


def upsert_document_sync(instance) -> None:
    """Build the document for ``instance`` and upsert it."""
    doc = build_document(instance)
    if doc is None:
        return
    LexSearchDocument.objects.update_or_create(
        container_id=doc["container_id"],
        object_id=doc["object_id"],
        defaults={
            "content_type": doc["content_type"],
            "model_label": doc["model_label"],
            "title": doc["title"],
            "body": doc["body"],
            "url": doc["url"],
        },
    )


def delete_document_sync(model_class, pk) -> None:
    container_id = _container_id_for(model_class)
    if not container_id:
        return
    LexSearchDocument.objects.filter(
        container_id=container_id, object_id=str(pk)
    ).delete()


def enqueue_index_update(instance, *, deleted: bool = False) -> None:
    """Schedule an async indexer task after the current transaction commits.

    Falls back to a synchronous upsert when Celery is not configured
    (dev / tests). Never raises — indexing failures must not block the
    user's write.
    """
    model_class = type(instance)
    app_label = model_class._meta.app_label
    model_name = model_class._meta.model_name
    pk = instance.pk

    def _dispatch():
        # Late import so importing this module doesn't pull in Celery
        # at module-load time (keeps the management command and tests
        # cheap to import).
        try:
            from .tasks import index_instance_task

            index_instance_task.delay(app_label, model_name, str(pk), deleted)
        except Exception as exc:  # pragma: no cover — Celery not configured
            logger.debug("Search index: falling back to sync update (%s)", exc)
            try:
                if deleted:
                    delete_document_sync(model_class, pk)
                else:
                    # ``instance`` may be stale by the time on_commit
                    # fires; reload from the DB for accuracy.
                    fresh = model_class._default_manager.filter(pk=pk).first()
                    if fresh is not None:
                        upsert_document_sync(fresh)
            except Exception:
                logger.exception("Search index sync fallback failed")

    try:
        transaction.on_commit(_dispatch)
    except Exception:
        # No active transaction (rare path) — run inline.
        _dispatch()


def _on_post_save(sender, instance, **kwargs):
    if instance is None or instance.pk is None:
        return
    enqueue_index_update(instance, deleted=False)


def _on_post_delete(sender, instance, **kwargs):
    if instance is None or instance.pk is None:
        return
    enqueue_index_update(instance, deleted=True)


def connect_indexing_signals(model_class) -> None:
    """Wire ``post_save`` and ``post_delete`` for ``model_class``.

    Idempotent: repeated calls (autoreload, repeated registration) are
    a no-op.
    """
    if model_class in _CONNECTED_MODELS:
        return
    if model_class is LexSearchDocument:
        return  # never index the index itself
    # ``simple_history`` generates ``Historical*`` shadow models for
    # every tracked model. They're append-only audit trails — indexing
    # them would double the write cost without enabling any user-facing
    # search.
    name = getattr(getattr(model_class, "_meta", None), "model_name", "") or ""
    if name.startswith(("historical", "metahistorical")):
        return

    dispatch_uid_base = f"lex_search_index::{model_class._meta.label}"
    post_save.connect(
        _on_post_save,
        sender=model_class,
        dispatch_uid=f"{dispatch_uid_base}::save",
    )
    post_delete.connect(
        _on_post_delete,
        sender=model_class,
        dispatch_uid=f"{dispatch_uid_base}::delete",
    )
    _CONNECTED_MODELS.add(model_class)
