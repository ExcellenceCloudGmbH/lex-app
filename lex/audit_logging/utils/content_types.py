import logging

from django.contrib.contenttypes.models import ContentType


logger = logging.getLogger(__name__)


def _get_content_type_manager(using=None):
    return ContentType.objects.db_manager(using) if using else ContentType.objects


def _describe_model(model_or_instance):
    meta = getattr(model_or_instance, "_meta", None)
    if meta is not None:
        return meta.label_lower
    return getattr(model_or_instance, "__name__", str(model_or_instance))


def safe_get_content_type(model_or_instance=None, *, content_type_id=None, using=None):
    """Return a ContentType while defending against stale manager caches.

    Supports both model/instance lookups (``get_for_model``) and existing
    content-type id lookups (``get_for_id``). In both cases the returned object
    is verified against the database and retried after clearing the cache when a
    stale cached entry is detected.
    """
    if model_or_instance is None and content_type_id is None:
        raise ValueError("Either model_or_instance or content_type_id must be provided.")

    manager = _get_content_type_manager(using)

    def _resolve():
        if content_type_id is not None:
            ct = manager.get_for_id(content_type_id)
        else:
            ct = manager.get_for_model(model_or_instance)
        manager.get(pk=ct.pk)
        return ct

    try:
        return _resolve()
    except ContentType.DoesNotExist:
        logger.warning(
            "Stale ContentType cache detected for %s. Clearing cache and retrying.",
            f"content_type_id={content_type_id}" if content_type_id is not None else _describe_model(model_or_instance),
        )
        ContentType.objects.clear_cache()
        manager = _get_content_type_manager(using)
        return _resolve()


def safe_get_generic_related_object(instance, *, content_type_field="content_type", object_id_field="object_id"):
    """Resolve a GenericForeignKey target without dereferencing the relation descriptor."""
    content_type_id = getattr(instance, f"{content_type_field}_id", None)
    object_id = getattr(instance, object_id_field, None)
    if not content_type_id or object_id is None:
        return None

    using = getattr(getattr(instance, "_state", None), "db", None)

    try:
        content_type = safe_get_content_type(content_type_id=content_type_id, using=using)
        model_class = content_type.model_class()
        if model_class is None:
            return None
        return model_class._default_manager.filter(pk=object_id).first()
    except Exception as exc:
        logger.debug(
            "Failed to resolve generic related object for %s.%s (ct_id=%s, object_id=%s): %s",
            instance.__class__.__name__,
            content_type_field,
            content_type_id,
            object_id,
            exc,
        )
        return None


_safe_get_content_type = safe_get_content_type
