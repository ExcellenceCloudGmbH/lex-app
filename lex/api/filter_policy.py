"""Which fields a list request may filter, order or group by (LEX_SEC_FILTERS).

Filtering on a field reveals its value even when the field is never returned:
``?salary__gte=100000`` answers "who earns that much" one request at a time.
A list request may only use fields a client can already see:

* fields of any serializer registered for the model (what the API returns),
* the ``limit_choices_to`` keys of foreign keys pointing at the model (the
  frontend's pickers filter by them),
* fields listed in the model's ``lex_filterable_fields``,

minus credential-shaped names (the serializer denylist) and minus fields the
caller's ``permission_read`` withholds model-wide. The primary key is always
allowed. Names that are not fields (``id_field``, AG Grid's
``ag-Grid-AutoColumn``) are ignored as before, never refused. ``lex_filterable_fields =
"__all__"`` restores the old behaviour for a model. Framework models
(audit log, calculation log, history tables) are not restricted: the frontend
filters them by internal fields.
"""
from __future__ import annotations

from typing import Optional, Set

from rest_framework import status
from rest_framework.exceptions import APIException

from lex.core.security_flags import strict

EXEMPT_MODEL_NAMES = frozenset({"auditlog", "auditlogstatus", "calculationlog"})


class FilterNotAllowed(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "FILTER_NOT_ALLOWED"

    def __init__(self, field_path: str):
        message = f"Filtering or ordering by '{field_path}' is not allowed."
        super().__init__({"code": "FILTER_NOT_ALLOWED", "detail": message, "error": message})


def _is_exempt(model_class) -> bool:
    name = model_class._meta.model_name
    return (
        name in EXEMPT_MODEL_NAMES
        or name.startswith("historical")
        or name.startswith("metahistorical")
        or getattr(model_class, "lex_filterable_fields", None) == "__all__"
    )


def _container(model_class):
    try:
        from lex.process_admin.settings import processAdminSite

        collection = processAdminSite.model_collection
        if collection is None:
            return None
        return collection.get_container(model_class._meta.model_name)
    except Exception:
        return None


def _serializer_field_names(model_class) -> Set[str]:
    container = _container(model_class)
    if container is None:
        return set()
    names: Set[str] = set()
    for serializer_class in (container.get_serializers_map() or {}).values():
        try:
            names.update(serializer_class().fields.keys())
        except Exception:
            continue
    return names


def _limit_choices_keys(model_class) -> Set[str]:
    keys: Set[str] = set()
    for related in model_class._meta.related_objects:
        remote = getattr(related, "field", None)
        limit = getattr(getattr(remote, "remote_field", None), "limit_choices_to", None) if remote else None
        if isinstance(limit, dict):
            keys.update(key.split("__")[0] for key in limit)
    return keys


def _model_wide_read_exclusions(model_class, request) -> Set[str]:
    """Fields permission_read withholds even when it admits the row (judged on a blank record)."""
    if not hasattr(model_class, "permission_read"):
        return set()
    from lex.core.models.LexModel import UserContext

    try:
        blank = model_class()
        result = blank.permission_read(UserContext.from_request(request, blank))
    except Exception:
        return set()
    if not getattr(result, "allowed", False):
        return set()  # a row-level rule; it says nothing about fields
    all_fields = {f.name for f in model_class._meta.fields}
    return all_fields - set(result.get_fields(all_fields))


def allowed_fields(model_class, request) -> Optional[Set[str]]:
    """Top-level field names ``model_class`` may be filtered by, or ``None`` for "any"."""
    if not strict("FILTERS") or _is_exempt(model_class):
        return None
    cache = getattr(getattr(request, "_request", request), "_lex_filter_policy", None)
    if cache is None:
        cache = {}
        try:
            setattr(getattr(request, "_request", request), "_lex_filter_policy", cache)
        except AttributeError:  # pragma: no cover
            pass
    if model_class in cache:
        return cache[model_class]

    from lex.api.serializers.sensitive_fields import is_sensitive_field_name

    model_fields = {f.name for f in model_class._meta.get_fields()}
    allowed = (_serializer_field_names(model_class) | _limit_choices_keys(model_class)) & model_fields
    extra = getattr(model_class, "lex_filterable_fields", None) or ()
    allowed |= set(extra) & model_fields
    allowed -= {name for name in allowed if is_sensitive_field_name(name)}
    allowed -= _model_wide_read_exclusions(model_class, request)
    # The primary key is in every response (``id``/``id_field``) and the
    # bundled frontend always appends ``ordering=<pk>``: never refuse it.
    allowed.add(model_class._meta.pk.name)
    cache[model_class] = allowed
    return allowed


def path_allowed(model_class, field_path: str, request) -> bool:
    """Whether every segment of ``field_path`` (``a__b__c``) is filterable on its model."""
    current = model_class
    parts = [part for part in field_path.split("__") if part]
    for index, part in enumerate(parts):
        allowed = allowed_fields(current, request)
        try:
            field = current._meta.get_field(part)
        except Exception:
            return True  # not a field: a lookup suffix or JSON key, judged by its field
        if allowed is not None and part not in allowed:
            return False
        if index < len(parts) - 1:
            related = getattr(field, "related_model", None)
            if not getattr(field, "is_relation", False) or related is None:
                return True  # the rest is a lookup on a plain field
            if allowed is not None and _container(related) is None:
                return False  # no API exposes that model; nothing of it is visible
            current = related
    return True


def check_path(model_class, field_path: str, request) -> None:
    if not path_allowed(model_class, field_path, request):
        raise FilterNotAllowed(field_path)
