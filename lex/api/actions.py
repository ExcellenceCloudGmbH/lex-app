"""Record and collection actions: verbs beyond create/read/update/delete.

Some operations are not CRUD on one model — "submit", "approve", "erase",
"accept invitation". Declare them on the model and lex gives each its own
URL with the same guarantees as its other endpoints::

    from lex.api.project import lex_action

    class Invoice(LexModel):
        @lex_action(permission="edit", input=ApproveInput)
        def approve(self, context, data):
            self.status = "approved"
            self.save()
            return {"status": self.status}

        @lex_action(detail=False, permission="permission_import")
        def import_batch(cls, context, data):
            ...

        @classmethod
        def permission_import(cls, user_context):
            return "finance" in user_context.groups

* ``POST api/model_entries/<model>/<calculationId>/one/<pk>/action/approve``
  (also ``.../one/<uuid>/action/...`` with ``lex_lookup_field``)
* ``POST api/model_entries/<model>/<calculationId>/action/import_batch``

For each call lex finds the record (404 when it does not exist or the caller
may not read it), checks the action's permission (400 ``ACTION_NOT_ALLOWED``
— never 403, which signs the bundled frontend's user out), validates the
input (unknown keys refused), runs the method in one transaction with the
caller's database context and audit context, writes an audit-log entry, and
returns the result: a dict or list (JSON), data for the ``output``
serializer, ``None`` (204) or any ``HttpResponse`` (files pass through).

``permission`` is required: ``"edit"``, ``"create"`` or ``"delete"`` use the
model's ``permission_edit``/``permission_create``/``permission_delete``; any
other string names a method on the model (``(user_context) -> bool``, a
classmethod for collection actions). An action without one is refused.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence

from django.db import transaction
from django.http import HttpResponseBase
from rest_framework import status
from rest_framework.exceptions import MethodNotAllowed, NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey

from lex.api.errors import LexActionError

_ATTR = "_lex_action"


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


def lex_action(
    name: Optional[str] = None,
    *,
    detail: bool = True,
    methods: Sequence[str] = ("post",),
    input: Any = None,  # noqa: A002 - mirrors the documented keyword
    output: Any = None,
    permission: Optional[str] = None,
    atomic: bool = True,
):
    """Declare a model method as an action (see the module docstring)."""

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
    _registry[model_class] = found
    return found


def _strict_input(serializer_class, data, context):
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


class ModelActionView(APIView):
    """Runs a declared action (routes in ``ProcessAdminSite``)."""

    permission_classes = [HasAPIKey | IsAuthenticated]
    lex_structured_errors = True
    lex_maps_db_errors = True

    def _dispatch_action(self, request, *args, **kwargs):
        from lex.api.security import create_allowed, delete_allowed, edit_violations, guarded, is_readable
        from lex.api.utils.Context import OperationContext
        from lex.audit_logging.utils.ModelContext import model_logging_context
        from lex.core import db_context
        from lex.core.models.LexModel import UserContext

        container = kwargs["model_container"]
        model_class = container.model_class
        spec = actions_for(model_class).get(kwargs["action"])
        detail = "pk" in kwargs or "lookup" in kwargs
        if spec is None or spec.detail != detail:
            raise NotFound()
        if request.method.lower() not in spec.methods:
            raise MethodNotAllowed(request.method)

        instance = None
        if detail:
            instance = self._resolve_instance(model_class, kwargs)
            if instance is None or not is_readable(request, instance):
                raise NotFound()

        context_target = instance if instance is not None else model_class()
        user_context = UserContext.from_request(request, context_target)
        if not self._permitted(spec, model_class, instance, request, user_context,
                               guarded, create_allowed, delete_allowed, edit_violations):
            detail_message = f"You may not run '{spec.name}' here."
            raise LexActionError(detail_message, code="ACTION_NOT_ALLOWED")

        data = request.data
        if spec.input is not None:
            data = _strict_input(spec.input, request.data, {"request": request, "view": self})

        calculation_id = kwargs.get("calculationId") or f"action_{uuid.uuid4().hex}"
        atomic = transaction.atomic() if spec.atomic else _null()
        with OperationContext(request, calculation_id):
            with atomic:
                db_context.apply(request.user, local=True)
                self._audit(request, spec, model_class, instance, data, calculation_id)
                logging_target = instance if instance is not None else model_class
                with model_logging_context(logging_target):
                    if instance is not None:
                        result = spec.function(instance, user_context, data)
                    else:
                        result = spec.function(model_class, user_context, data)
        return self._respond(spec, result, request)

    @staticmethod
    def _resolve_instance(model_class, kwargs):
        lookup_field = getattr(model_class, "lex_lookup_field", None)
        if "lookup" in kwargs:
            if not lookup_field:
                return None
            return model_class._default_manager.filter(**{lookup_field: kwargs["lookup"]}).first()
        if lookup_field and getattr(model_class, "lex_lookup_allow_pk", True) is False:
            return None
        return model_class._default_manager.filter(pk=kwargs["pk"]).first()

    @staticmethod
    def _permitted(spec, model_class, instance, request, user_context,
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
        return bool(guarded(method, user_context, deny=False, allow=True, what=f"{model_class.__name__}.{rule}"))

    @staticmethod
    def _audit(request, spec, model_class, instance, data, calculation_id):
        from lex.audit_logging.models.AuditLog import AuditLog
        from lex.audit_logging.models.AuditLogStatus import AuditLogStatus

        payload: Dict[str, Any] = {"lex_action": spec.name}
        if instance is not None:
            payload["id"] = instance.pk
        try:
            payload["input"] = dict(data) if hasattr(data, "keys") else data
        except Exception:
            payload["input"] = None
        from lex.core.models.LexModel import LexModel

        user = getattr(request, "user", None)
        from lex.audit_logging.utils.policy import audit_actor, audit_payload

        payload["input"] = audit_payload(payload.get("input"))
        entry = AuditLog.objects.create(
            author=(audit_actor(user) or "") if user is not None else "",
            resource=model_class.__name__.lower(),
            action="update",
            payload=_json_safe(payload),
            calculation_id=calculation_id,
            object_id=instance.pk if instance is not None and isinstance(instance.pk, int) else None,
        )
        AuditLogStatus.objects.create(audit_log=entry, status="success")

    def _respond(self, spec, result, request):
        if isinstance(result, HttpResponseBase):
            return result
        if result is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        if spec.output is not None:
            many = isinstance(result, (list, tuple)) or hasattr(result, "model")
            return Response(spec.output(result, many=many, context={"request": request, "view": self}).data)
        return Response(result)

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
