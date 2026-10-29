"""Permission enforcement shared by lex's REST endpoints.

One place for the checks that every endpoint applies the same way:

* :func:`read_result` – ``permission_read`` for one record, evaluated at most
  once per request (the list filter, the serializer and the reserved-scopes
  field all ask; BUG-011).
* :func:`is_readable` – whether the caller may see a record at all.
* :func:`edit_denial` – the 400 answer when a write touches fields the caller
  may not edit (BUG-010), or ``None``.
* :func:`refusal` – the 400 body ``{code, detail, error}`` every refusal uses.
* :func:`guarded` – runs a permission hook; an exception denies when
  ``LEX_SEC_FAIL_CLOSED`` is on (the default) and allows as before when off.
* :func:`write_denial` – the 400 answer when a non-superuser writes to a
  history table (``LEX_SEC_HISTORY_WRITE``) or to Django's users and groups
  (``LEX_SEC_INTERNAL``), or ``None``.
* :func:`list_denied` – a model's own ``permission_list`` refuses the list.
* :func:`error_detail` – an error body without its server traceback unless
  tracebacks may be shown (``LEX_SEC_TRACEBACKS``, ``LEX_API_TRACEBACKS``).

Denials never answer 403: the bundled frontend signs the user out on a 403,
so a record the caller may not read is 404 and a field it may not edit is 400.
"""
from __future__ import annotations

import functools
import logging
import re
from typing import Any, Callable, Iterable, Mapping, Optional

from rest_framework import status
from rest_framework.response import Response

from lex.core.security_flags import expose_tracebacks, is_superuser, strict

logger = logging.getLogger(__name__)

_MEMO_ATTR = "_lex_permission_memo"

#: Request keys that steer an update but are not fields of the record.
CONTROL_KEYS = frozenset({"calculate", "cancel", "is_calculated", "id"})


def _holder(request):
    return getattr(request, "_request", request)


def _memo(request) -> dict:
    holder = _holder(request)
    memo = getattr(holder, _MEMO_ATTR, None)
    if memo is None:
        memo = {}
        try:
            setattr(holder, _MEMO_ATTR, memo)
        except AttributeError:  # pragma: no cover - exotic request objects
            pass
    return memo


def _memo_key(kind: str, instance):
    pk = getattr(instance, "pk", None)
    meta = getattr(type(instance), "_meta", None)
    label = getattr(meta, "label", None)
    if pk is None or label is None:
        return None
    try:
        hash(pk)
    except TypeError:
        return None
    return (kind, label, pk)


def remember(request, kind: str, instance, result) -> None:
    key = _memo_key(kind, instance)
    if request is None or key is None:
        return
    _memo(request)[key] = result


def recalled(request, kind: str, instance):
    key = _memo_key(kind, instance)
    if request is None or key is None:
        return None
    return _memo(request).get(key)


def guarded(hook: Callable[..., Any], *args, deny: Any, allow: Any, what: str):
    """Call a permission hook; on an exception deny (fail closed) or allow (legacy)."""
    try:
        return hook(*args)
    except Exception:
        if strict("FAIL_CLOSED"):
            logger.exception("Permission check %s raised; access denied (LEX_SEC_FAIL_CLOSED).", what)
            return deny
        logger.warning("Permission check %s raised; access allowed (LEX_SEC_FAIL_CLOSED is off).", what, exc_info=True)
        return allow


def read_result(request, instance):
    """``permission_read`` for ``instance``, once per request and record."""
    from lex.core.models.LexModel import PermissionResult, UserContext

    cached = recalled(request, "read", instance)
    if cached is not None:
        return cached
    if not hasattr(instance, "permission_read"):
        result = PermissionResult.allow_all("no permission_read")
    else:
        context = UserContext.from_request(request, instance)
        result = guarded(
            instance.permission_read,
            context,
            deny=PermissionResult.deny("permission_read raised"),
            allow=PermissionResult.allow_all("permission_read raised (legacy allow)"),
            what=f"{type(instance).__name__}.permission_read",
        )
    remember(request, "read", instance, result)
    return result


def is_readable(request, instance) -> bool:
    """Whether the caller may see ``instance``.

    Judged by ``permission_read`` for this record — the decision the
    serializer made before, when a refused record came back as ``{}``.
    Models without ``permission_read`` (plain Django models) and the audit
    trail, whose own ``permission_read`` allows everyone while its list
    judges each entry by the record it names, use the list endpoint's rule.
    """
    from lex.api.views.model_entries.filter_backends import apply_read_restriction

    audit_trail = strict("READ_SURFACES") and type(instance).__name__ in ("AuditLog", "AuditLogStatus")
    if audit_trail or not hasattr(instance, "permission_read"):
        queryset = type(instance)._default_manager.filter(pk=instance.pk)
        return apply_read_restriction(queryset, request).exists()
    return bool(read_result(request, instance).allowed)


def _camel_to_snake(name: str) -> str:
    name = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", name).lower()


def _field_name(key: str, model_fields: Mapping[str, Any]) -> Optional[str]:
    if key in model_fields:
        return key
    snake = _camel_to_snake(key)
    if snake in model_fields:
        return snake
    for field in model_fields.values():
        if getattr(field, "attname", None) in (key, snake):
            return field.name
    return None


def _changed(serializer, key: str, raw: Any, instance, name: str) -> bool:
    field = serializer.fields.get(key) if serializer is not None else None
    try:
        if field is not None:
            new_value = field.to_internal_value(raw)
        else:
            # Not in the serializer (e.g. a withheld field): compare as the model field would store it.
            model_field = instance._meta.get_field(name)
            if getattr(model_field, "is_relation", False):
                old_pk = getattr(instance, model_field.attname, None)
                raw_pk = getattr(raw, "pk", raw)
                return str(raw_pk) != str(old_pk) if raw_pk not in (None, "") or old_pk is not None else False
            new_value = model_field.to_python(raw)
    except Exception:
        return True
    old_value = getattr(instance, name, None)
    if hasattr(old_value, "pk") and not hasattr(new_value, "pk"):
        old_value = old_value.pk
    if hasattr(new_value, "pk") and hasattr(old_value, "pk"):
        return new_value.pk != old_value.pk
    return new_value != old_value


def edit_violations(request, instance, data: Mapping[str, Any], serializer=None,
                    ignore: Iterable[str] = ()) -> Optional[list]:
    """Fields in ``data`` the caller may not change, or ``None`` if the record is not editable at all.

    Only keys that name editable model fields are checked, and only when the
    value actually changes — the same rules as ``@add_permission_checks``.
    """
    from lex.core.models.LexModel import PermissionResult, UserContext

    if not hasattr(instance, "permission_edit"):
        return []
    context = UserContext.from_request(request, instance)
    result = guarded(
        instance.permission_edit,
        context,
        deny=PermissionResult.deny("permission_edit raised"),
        allow=PermissionResult.allow_all("permission_edit raised (legacy allow)"),
        what=f"{type(instance).__name__}.permission_edit",
    )
    model_fields = {f.name: f for f in instance._meta.concrete_fields}
    if not result.allowed:
        return None
    editable = set(result.get_fields(set(model_fields)))
    skip = set(CONTROL_KEYS) | set(ignore)
    violations = []
    for key, raw in (data or {}).items():
        if key in skip or key.startswith(("lexReserved", "lex_reserved")) or key.endswith("__short_description"):
            continue
        name = _field_name(key, model_fields)
        if name is None:
            continue
        field = model_fields[name]
        if not getattr(field, "editable", True) or field.primary_key:
            continue
        if name in editable:
            continue
        if _changed(serializer, key, raw, instance, name):
            violations.append(key)
    return violations


def is_django_auth_model(model) -> bool:
    """Django's user, group and permission models (no lex permission hooks)."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group, Permission

    return model in (get_user_model(), Group, Permission)


@functools.lru_cache(maxsize=None)
def is_history_model(model) -> bool:
    """A history table: lex's ``Historical*`` (Level 1) or ``MetaHistorical*`` (Level 2) models."""
    try:
        names = {field.name for field in model._meta.get_fields()}
    except Exception:
        return False
    return "history_id" in names or "meta_history_id" in names


def write_denial(request, model_class) -> Optional[Response]:
    """The 400 answer for any write (create, update, delete) the model only allows superusers, or ``None``.

    * History rows are the record of what was true and when; only a superuser
      may correct them through the API (``LEX_SEC_HISTORY_WRITE``).
    * Django's users, groups and permissions have no permission hooks; only a
      superuser may change them through the generic API (``LEX_SEC_INTERNAL``).
    """
    if is_superuser(request):
        return None
    if strict("HISTORY_WRITE") and is_history_model(model_class):
        return refusal("EDIT_NOT_ALLOWED", "History records can only be changed by a superuser.")
    if strict("INTERNAL") and is_django_auth_model(model_class):
        return refusal("EDIT_NOT_ALLOWED", f"Only a superuser may change a {model_class.__name__}.")
    return None


def list_denied(request, model_class) -> bool:
    """A model's own ``permission_list`` refuses the caller (the list then answers 404).

    Only an override is consulted: LexModel's default reads the Keycloak
    ``list`` scope and would close every list of a project without scopes.
    """
    from lex.core.models.LexModel import LexModel, UserContext

    hook = getattr(model_class, "permission_list", None)
    if hook is None or hook is getattr(LexModel, "permission_list", None):
        return False
    instance = model_class()
    allowed = guarded(
        instance.permission_list,
        UserContext.from_request(request, instance),
        deny=False,
        allow=True,
        what=f"{model_class.__name__}.permission_list",
    )
    return not allowed


def error_detail(body: Mapping[str, Any]) -> dict:
    """``body`` without its ``traceback`` key unless tracebacks may be shown (LEX_SEC_TRACEBACKS)."""
    cleaned = dict(body)
    if "traceback" in cleaned and not expose_tracebacks():
        cleaned.pop("traceback")
    return cleaned


def refusal(code: str, detail: str, **extra) -> Response:
    """A 400 answer ``{code, detail, error, **extra}`` for a refused request.

    Refusals never answer 403 (the bundled frontend signs the user out on a
    403); ``error`` repeats ``detail`` for clients that read the old key.
    """
    body = {"code": code, "detail": detail, "error": detail}
    body.update(extra)
    return Response(body, status=status.HTTP_400_BAD_REQUEST)


def edit_denial(request, instance, data: Mapping[str, Any], serializer=None,
                ignore: Iterable[str] = ()) -> Optional[Response]:
    """The 400 answer for a write the caller may not make, or ``None``.

    A record whose ``permission_edit`` denies everything refuses every write,
    including ones that name no field (calculate, cancel).
    """
    if not strict("EDIT"):
        return None
    violations = edit_violations(request, instance, data, serializer, ignore)
    if violations is None:
        return refusal("EDIT_NOT_ALLOWED", f"You may not edit this {type(instance).__name__}.")
    if violations:
        return refusal(
            "EDIT_NOT_ALLOWED",
            "You may not edit: " + ", ".join(sorted(violations)) + ".",
            fields={key: "Not editable." for key in sorted(violations)},
        )
    return None


def delete_allowed(request, instance) -> bool:
    """``permission_delete`` for one record (fail-closed aware)."""
    from lex.core.models.LexModel import UserContext

    if hasattr(instance, "permission_delete"):
        return bool(
            guarded(
                instance.permission_delete,
                UserContext.from_request(request, instance),
                deny=False,
                allow=True,
                what=f"{type(instance).__name__}.permission_delete",
            )
        )
    if hasattr(instance, "can_delete"):
        return bool(guarded(instance.can_delete, request, deny=False, allow=True, what="can_delete"))
    return True


def filled_record(model_class, validated_data):
    """An unsaved ``model_class`` carrying the request's validated values.

    Only concrete fields are set (a foreign key as its related object); many-to-many
    values and serializer-only fields are left out, as ``save()`` would set them later.
    """
    concrete = {field.name for field in model_class._meta.concrete_fields}
    values = {name: value for name, value in (validated_data or {}).items() if name in concrete}
    return model_class(**values)


def ask_create(request, instance) -> bool:
    """``permission_create`` (or legacy ``can_create``) on ``instance``, raising as the hook raises."""
    from lex.core.models.LexModel import UserContext

    if hasattr(instance, "permission_create"):
        return bool(instance.permission_create(UserContext.from_request(request, instance)))
    if hasattr(instance, "can_create"):
        return bool(instance.can_create(request))
    return True


def create_allowed(request, model_class) -> bool:
    """``permission_create`` for a model (fail-closed aware)."""
    from lex.core.models.LexModel import UserContext

    instance = model_class()
    if hasattr(instance, "permission_create"):
        return bool(
            guarded(
                instance.permission_create,
                UserContext.from_request(request, instance),
                deny=False,
                allow=True,
                what=f"{model_class.__name__}.permission_create",
            )
        )
    if hasattr(instance, "can_create"):
        return bool(guarded(instance.can_create, request, deny=False, allow=True, what="can_create"))
    return True
