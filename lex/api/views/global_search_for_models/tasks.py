"""
Celery tasks for the global search index.

The actual indexing logic lives in :mod:`indexer`; these tasks just
re-hydrate the model instance and call into it. Kept in their own
module so importing :mod:`indexer` doesn't force a Celery import at
module-load time.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.apps import apps as django_apps

from .indexer import delete_document_sync, upsert_document_sync

logger = logging.getLogger(__name__)


@shared_task(name="lex.api.global_search.index_instance", ignore_result=True)
def index_instance_task(app_label: str, model_name: str, pk: str, deleted: bool) -> None:
    """Re-index a single model instance.

    ``app_label`` / ``model_name`` / ``pk`` come from the signal handler
    in :func:`indexer.enqueue_index_update`; we re-fetch the row here so
    the worker doesn't depend on the saving process's in-memory state.
    """
    try:
        model_class = django_apps.get_model(app_label, model_name)
    except LookupError:
        logger.warning("Search index task: unknown model %s.%s", app_label, model_name)
        return

    if deleted:
        delete_document_sync(model_class, pk)
        return

    instance = model_class._default_manager.filter(pk=pk).first()
    if instance is None:
        # Row vanished between the signal and the task running — treat
        # as a delete to keep the index consistent.
        delete_document_sync(model_class, pk)
        return

    upsert_document_sync(instance)


@shared_task(name="lex.api.global_search.reconcile_search_index", ignore_result=True)
def reconcile_search_index_task() -> dict:
    """Periodic drift check — repairs index rows that were lost.

    Compares per-container row counts in :class:`LexSearchDocument`
    against the live tables and triggers a rebuild for any container
    whose counts diverge by more than ``DRIFT_THRESHOLD`` rows.
    """
    from django.db.models import Count

    from lex.api.models import LexSearchDocument
    from lex.process_admin.settings import processAdminSite

    DRIFT_THRESHOLD = 5

    if not processAdminSite.initialized:
        # Touch the urls property so the model_collection is built.
        _ = processAdminSite.urls

    indexed_counts = dict(
        LexSearchDocument.objects.values_list("container_id")
        .annotate(n=Count("pk"))
        .values_list("container_id", "n")
    )

    rebuilt: list[str] = []
    for container in processAdminSite.model_collection.all_containers:
        container_id = getattr(container, "id", None)
        model_class = getattr(container, "model_class", None)
        if not container_id or model_class is None:
            continue
        try:
            live = model_class._default_manager.count()
        except Exception:
            continue
        if abs(live - indexed_counts.get(container_id, 0)) > DRIFT_THRESHOLD:
            rebuild_container_task.delay(container_id)
            rebuilt.append(container_id)

    return {"rebuilt": rebuilt}


@shared_task(name="lex.api.global_search.rebuild_container", ignore_result=True)
def rebuild_container_task(container_id: str) -> int:
    """Rebuild every index row for a single container."""
    from lex.api.models import LexSearchDocument
    from lex.process_admin.settings import processAdminSite

    if not processAdminSite.initialized:
        _ = processAdminSite.urls

    container = next(
        (
            c
            for c in processAdminSite.model_collection.all_containers
            if getattr(c, "id", None) == container_id
        ),
        None,
    )
    if container is None:
        return 0

    model_class = getattr(container, "model_class", None)
    if model_class is None:
        return 0

    LexSearchDocument.objects.filter(container_id=container_id).delete()

    n = 0
    BATCH = 1000
    qs = model_class._default_manager.all().iterator(chunk_size=BATCH)
    for instance in qs:
        try:
            upsert_document_sync(instance)
            n += 1
        except Exception:
            logger.exception(
                "Failed to index %s pk=%s", container_id, getattr(instance, "pk", "?")
            )
    return n
