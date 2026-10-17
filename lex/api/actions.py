"""Record and collection actions: verbs beyond create/read/update/delete.

Some operations are not CRUD on one model — "submit", "approve", "erase",
"accept invitation". Declare them on the model and lex gives each its own
URL with the same guarantees as its other endpoints::

    from lex.api.project import lex_action

    class Invoice(LexModel):
        @lex_action(permission="edit", input=ApproveInput, status=201)
        def approve(self, context, data):
            self.status = "approved"
            self.save()
            return {"status": self.status}

        @lex_action(detail=False, permission="permission_import", throttle="imports")
        def import_batch(cls, context, data):
            ...

        @classmethod
        def permission_import(cls, user_context, data):
            return "finance" in user_context.groups

* ``POST api/model_entries/<model>/<calculationId>/one/<pk>/action/approve``
  (also ``.../one/<uuid>/action/...`` with ``lex_lookup_field``)
* ``POST api/model_entries/<model>/<calculationId>/action/import_batch``

For each call lex finds the record (404 when it does not exist, the caller
may not read it or it is outside the request's scope), validates the input
(unknown keys refused; a GET action validates its query parameters), checks
the action's permission with the validated input, runs the method in one
transaction with the caller's database context and audit context, writes an
audit-log entry after it succeeded (a failed run leaves a "failure" entry
that the rollback does not take with it) and returns the result: a dict or
list (JSON), data for the ``output`` serializer, ``None`` (204),
:class:`ActionResult` for a status or headers decided while running, or any
``HttpResponse`` (files pass through).

``permission`` is required: ``"edit"``, ``"create"`` or ``"delete"`` use the
model's ``permission_edit``/``permission_create``/``permission_delete``; any
other string names a method on the model (``(user_context)`` or
``(user_context, data)`` — a classmethod for collection actions). It may
return a bool or raise ``LexActionError``/``NotFound`` to answer in its own
words. A refusal answers 400 ``ACTION_NOT_ALLOWED`` (never 403, which signs
the bundled frontend's user out), or 404 with ``denied="not_found"`` or
``lex_config.ACTION_DENIED = "not_found"`` for projects where anything
outside a caller's access does not exist. An action without a permission is
refused.

More options: ``status`` (success status, e.g. 201), ``headers`` (e.g.
``{"Cache-Control": "no-store"}``), ``throttle`` (a scope of
``lex_config.THROTTLE_RATES``, answered 429 when exceeded) and, for the
OpenAPI schema, ``summary``, ``description``, ``operation_id``, ``tags``,
``responses`` (extra statuses) and ``media="multipart"`` for file uploads.
``input`` and ``output`` may be dotted paths to the serializer classes; ``many=True`` documents an
output that is a list of ``output``.

A model inherits the actions of its bases, a projection those of the model it projects.
``lex_actions = ("name", ...)`` on a model names the actions it offers; the others do not exist on it.
"""
from __future__ import annotations

import inspect
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.http import HttpResponseBase
from rest_framework import status
from rest_framework.exceptions import MethodNotAllowed, NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey

from lex.api.errors import LexActionError

_ATTR = "_lex_action"
DENIED_ANSWERS = ("refused", "not_found")


@dataclass(frozen=True)
class ActionSpec:
    name: str
    function: Callable[..., Any]
    detail: bool
    methods: Sequence[str]
    input: Any
    output: Any
    permission: Optional[str]
    atomic: bool
    status: Optional[int] = None
    headers: Mapping[str, str] = field(default_factory=dict)
    throttle: Optional[str] = None
    denied: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    operation_id: Optional[str] = None
    tags: Sequence[str] = ()
    responses: Mapping[int, str] = field(default_factory=dict)
    media: str = "json"
    many: bool = False


@dataclass
class ActionResult:
    """An action's answer whose status or headers are decided while it runs."""

    data: Any = None
    status: Optional[int] = None
    headers: Mapping[str, str] = field(default_factory=dict)


def lex_action(
    name: Optional[str] = None,
    *,
    detail: bool = True,
    methods: Sequence[str] = ("post",),
    input: Any = None,  # noqa: A002 - mirrors the documented keyword
    output: Any = None,
    permission: Optional[str] = None,
    atomic: bool = True,
    status: Optional[int] = None,  # noqa: A002
    headers: Optional[Mapping[str, str]] = None,
    throttle: Optional[str] = None,
    denied: Optional[str] = None,
    summary: Optional[str] = None,
    description: Optional[str] = None,
    operation_id: Optional[str] = None,
    tags: Sequence[str] = (),
    responses: Optional[Mapping[int, str]] = None,
    media: str = "json",
    many: bool = False,
):
    """Declare a model method as an action (see the module docstring)."""
    if denied is not None and denied not in DENIED_ANSWERS:
        raise ImproperlyConfigured(f"lex_action(denied=...) must be one of {DENIED_ANSWERS}, not {denied!r}")
    if media not in ("json", "multipart"):
        raise ImproperlyConfigured(f"lex_action(media=...) must be 'json' or 'multipart', not {media!r}")

    def decorator(function):
        target = function.__func__ if isinstance(function, (classmethod, staticmethod)) else function
        spec = ActionSpec(
            name=name or target.__name__,
            function=target,
            detail=detail,
            methods=tuple(m.lower() for m in methods),
            input=input,
            output=output,
            permission=permission,
            atomic=atomic,
            status=status,
            headers=dict(headers or {}),
            throttle=throttle,
            denied=denied,
            summary=summary,
            description=description,
            operation_id=operation_id,
            tags=tuple(tags),
            responses=dict(responses or {}),
            media=media,
            many=many,
        )
        setattr(target, _ATTR, spec)
        return function

    return decorator


_registry: Dict[type, Dict[str, ActionSpec]] = {}


def actions_for(model_class) -> Dict[str, ActionSpec]:
    """The actions declared on ``model_class`` (and its bases), by name."""
    cached = _registry.get(model_class)
    if cached is not None:
        return cached
    found: Dict[str, ActionSpec] = {}
    for klass in reversed(model_class.__mro__):
        for attribute in vars(klass).values():
            target = attribute.__func__ if isinstance(attribute, (classmethod, staticmethod)) else attribute
            spec = getattr(target, _ATTR, None)
            if isinstance(spec, ActionSpec):
                found[spec.name] = spec
    allowed = getattr(model_class, "lex_actions", None)
    if allowed is not None:  # e.g. a projection that offers none or only some of its base model's actions
        found = {name: spec for name, spec in found.items() if name in set(allowed)}
    _registry[model_class] = found
    return found


def resolve_serializer(value):
    """An action's input or output: a serializer class, or its dotted path (resolved on first use).

    A dotted path lets a model declare actions whose serializers live in a module that imports the model.
    """
    if isinstance(value, str):
        from django.utils.module_loading import import_string

        return import_string(value)
    return value


def _strict_input(serializer_class, data, context):
    serializer_class = resolve_serializer(serializer_class)
    serializer = serializer_class(data=data, context=context)
    if hasattr(data, "keys"):
        unknown = sorted(set(data.keys()) - set(serializer.fields))
        if unknown:
            raise LexActionError(
                "Unexpected fields: " + ", ".join(unknown) + ".",
                code="UNKNOWN_FIELDS",
                fields={key: "Unknown field." for key in unknown},
            )
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _query_input(request):
    """A GET action's input: its query parameters, without the request scope's own."""
    from lex.core import request_scope

    params = request.query_params.dict()
    scope_param = request_scope.param()
    if scope_param:
        params.pop(scope_param, None)
    return params


def _takes_data(method) -> bool:
    try:
        parameters = [
            p for p in inspect.signature(method).parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
    except (TypeError, ValueError):
        return False
    return len(parameters) >= 2


def denied_answer(spec) -> str:
    from lex.core.extensions import get_setting

    answer = spec.denied or get_setting("ACTION_DENIED") or "refused"
    if answer not in DENIED_ANSWERS:
        raise ImproperlyConfigured(f"lex_config.ACTION_DENIED must be one of {DENIED_ANSWERS}, not {answer!r}")
    return answer


class ActionThrottle(SimpleRateThrottle):
    """Rate limit of one action scope (``lex_config.THROTTLE_RATES``), per user (or client address)."""

    def __init__(self, scope):
        self.scope = scope
        super().__init__()

    def get_rate(self):
        from lex.core.extensions import get_setting

        rates = get_setting("THROTTLE_RATES") or {}
        if self.scope not in rates:
            raise ImproperlyConfigured(f"lex_config.THROTTLE_RATES has no rate for the action throttle {self.scope!r}")
        return rates[self.scope]

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        ident = user.pk if user is not None and user.is_authenticated else self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class ModelActionView(APIView):
    """Runs a declared action (routes in ``ProcessAdminSite``)."""

    permission_classes = [HasAPIKey | IsAuthenticated]
    lex_structured_errors = True
    lex_maps_db_errors = True

    def _spec(self):
        container = self.kwargs.get("model_container")
        if container is None:
            return None
        return actions_for(container.model_class).get(self.kwargs.get("action"))

    def get_permissions(self):
        from lex.api.views.permissions.UserPermission import UserPermission

        # The model's modification_restriction applies as on its CRUD endpoints.
        return [*super().get_permissions(), UserPermission()]

    def get_throttles(self):
        spec = self._spec()
        throttles = list(super().get_throttles())
        if spec is not None and spec.throttle:
            throttles.append(ActionThrottle(spec.throttle))
        return throttles

    def _dispatch_action(self, request, *args, **kwargs):
        from lex.api.security import create_allowed, delete_allowed, edit_violations, guarded, is_readable
        from lex.api.utils.Context import OperationContext
        from lex.audit_logging.utils.ModelContext import model_logging_context
        from lex.core import db_context, request_scope
        from lex.core.models.LexModel import UserContext

        container = kwargs["model_container"]
        model_class = container.model_class
        spec = actions_for(model_class).get(kwargs["action"])
        detail = "pk" in kwargs or "lookup" in kwargs
        if spec is None or spec.detail != detail:
            raise NotFound()
        if request.method.lower() not in spec.methods:
            raise MethodNotAllowed(request.method)

        scope = request_scope.resolve(request)
        instance = None
        if detail:
            instance = self._resolve_instance(model_class, kwargs, request)
            if instance is None or not is_readable(request, instance):
                raise NotFound()

        # The input first: the permission may depend on it (which step, which amount).
        reading = request.method.lower() in ("get", "head")
        data = _query_input(request) if reading else request.data
        if spec.input is not None:
            data = _strict_input(spec.input, data, {"request": request, "view": self, "instance": instance})

        context_target = instance if instance is not None else model_class()
        user_context = UserContext.from_request(request, context_target)
        calculation_id = kwargs.get("calculationId") or f"action_{uuid.uuid4().hex}"
        atomic = transaction.atomic() if spec.atomic else _null()
        try:
            # The permission runs in the operation context too: it may need the request (its scope, its key).
            with OperationContext(request, calculation_id):
                if not self._permitted(spec, model_class, instance, request, user_context, data,
                                       guarded, create_allowed, delete_allowed, edit_violations):
                    if denied_answer(spec) == "not_found":
                        raise _Refused(NotFound())
                    raise _Refused(LexActionError(f"You may not run '{spec.name}' here.", code="ACTION_NOT_ALLOWED"))
                with atomic:
                    db_context.apply(request.user, local=True, scope=scope)
                    logging_target = instance if instance is not None else model_class
                    with model_logging_context(logging_target):
                        if instance is not None:
                            result = spec.function(instance, user_context, data)
                        else:
                            result = spec.function(model_class, user_context, data)
                    self._audit(request, spec, model_class, instance, data, calculation_id, "success")
        except _Refused as refused:
            raise refused.error from None  # a refusal is not a failed run: no audit entry
        except Exception as exc:
            self._audit_failure(request, spec, model_class, instance, data, calculation_id, exc)
            raise
        return self._respond(spec, result, request)

    @staticmethod
    def _resolve_instance(model_class, kwargs, request=None):
        from lex.core import request_scope

        lookup_field = getattr(model_class, "lex_lookup_field", None)
        if "lookup" in kwargs:
            if not lookup_field:
                return None
            rows = model_class._default_manager.filter(**{lookup_field: kwargs["lookup"]})
        elif lookup_field and getattr(model_class, "lex_lookup_allow_pk", True) is False:
            return None
        else:
            rows = model_class._default_manager.filter(pk=kwargs["pk"])
        if request is not None:
            rows = request_scope.scope_queryset(model_class, rows, request)
        return rows.first()

    @staticmethod
    def _permitted(spec, model_class, instance, request, user_context, data,
                   guarded, create_allowed, delete_allowed, edit_violations) -> bool:
        rule = spec.permission
        if not rule:
            return False  # an action must say who may run it
        if rule == "edit":
            return instance is not None and edit_violations(request, instance, {}) is not None
        if rule == "create":
            return create_allowed(request, model_class)
        if rule == "delete":
            return instance is not None and delete_allowed(request, instance)
        owner = instance if instance is not None else model_class
        method = getattr(owner, rule, None)
        if not callable(method):
            return False
        try:
            allowed = method(user_context, data) if _takes_data(method) else method(user_context)
        except (LexActionError, NotFound):
            raise  # the permission answers in its own words
        except Exception:
            import logging

            from lex.core.security_flags import strict

            logging.getLogger(__name__).exception("%s.%s raised", model_class.__name__, rule)
            return not strict("FAIL_CLOSED")
        return bool(allowed)

    @staticmethod
    def _audit_payload(spec, instance, data):
        payload: Dict[str, Any] = {"lex_action": spec.name}
        if instance is not None:
            payload["id"] = instance.pk
            lookup_field = getattr(type(instance), "lex_lookup_field", None)
            if lookup_field:
                payload["ref"] = str(getattr(instance, lookup_field, "") or "")
        try:
            raw = dict(data) if hasattr(data, "keys") else data
        except Exception:
            raw = None
        if hasattr(raw, "keys"):
            # Files are recorded by field name, never by content.
            raw = {key: (f"<file {getattr(value, 'name', '')}>" if hasattr(value, "read") else value)
                   for key, value in raw.items()}
        from lex.audit_logging.utils.policy import audit_payload

        payload["input"] = audit_payload(raw)
        return payload

    @classmethod
    def _audit(cls, request, spec, model_class, instance, data, calculation_id, outcome, error=None):
        from lex.audit_logging.models.AuditLog import AuditLog
        from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
        from lex.audit_logging.utils.policy import audit_actor

        payload = cls._audit_payload(spec, instance, data)
        user = getattr(request, "user", None)
        lookup_field = getattr(model_class, "lex_lookup_field", None)
        entry = AuditLog.objects.create(
            author=(audit_actor(user) or "") if user is not None else "",
            resource=model_class.__name__.lower(),
            action="action",
            payload=_json_safe(payload),
            calculation_id=calculation_id,
            object_id=instance.pk if instance is not None and isinstance(instance.pk, int) else None,
            object_ref=str(getattr(instance, lookup_field)) if instance is not None and lookup_field else None,
        )
        AuditLogStatus.objects.create(audit_log=entry, status=outcome, error_traceback=error)

    @classmethod
    def _audit_failure(cls, request, spec, model_class, instance, data, calculation_id, exc):
        """The failure entry, in its own transaction: the run's rollback must not take it along."""
        import logging

        code = getattr(exc, "code", None) or type(exc).__name__
        try:
            with transaction.atomic():
                cls._audit(request, spec, model_class, instance, data, calculation_id, "failure", error=str(code))
        except Exception:
            logging.getLogger(__name__).exception("Could not write the failure audit of action %s", spec.name)

    def _respond(self, spec, result, request):
        if isinstance(result, HttpResponseBase):
            for header, value in spec.headers.items():
                result.setdefault(header, value)
            return result
        status_code = spec.status or status.HTTP_200_OK
        headers = dict(spec.headers)
        if isinstance(result, ActionResult):
            status_code = result.status or status_code
            headers.update(result.headers)
            result = result.data
        if result is None:
            response = Response(status=status.HTTP_204_NO_CONTENT)
        elif spec.output is not None:
            many = isinstance(result, (list, tuple)) or hasattr(result, "model")
            output = resolve_serializer(spec.output)
            response = Response(
                output(result, many=many, context={"request": request, "view": self}).data, status=status_code
            )
        else:
            response = Response(result, status=status_code)
        for header, value in headers.items():
            response[header] = value
        return response

    def get(self, request, *args, **kwargs):
        return self._dispatch_action(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self._dispatch_action(request, *args, **kwargs)

    def put(self, request, *args, **kwargs):
        return self._dispatch_action(request, *args, **kwargs)

    def patch(self, request, *args, **kwargs):
        return self._dispatch_action(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        return self._dispatch_action(request, *args, **kwargs)


class _Refused(Exception):
    """The permission refused the run (carries the answer)."""

    def __init__(self, error):
        super().__init__(str(error))
        self.error = error


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _json_safe(value):
    import json

    from django.core.serializers.json import DjangoJSONEncoder

    try:
        return json.loads(json.dumps(value, cls=DjangoJSONEncoder))
    except Exception:
        return {"lex_action": value.get("lex_action")} if isinstance(value, dict) else None
