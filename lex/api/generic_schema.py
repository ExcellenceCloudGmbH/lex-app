"""OpenAPI operations of lex's generic endpoints for the models a project exposes.

Opt in with ``lex_config.SCHEMA["generic"]``::

    SCHEMA = {
        ...,
        "generic": {"short_paths": True},
        "error_component": "app.api.ErrorSerializer",   # body of every 4xx
    }

Every model with ``lex_api_exposed`` (see :mod:`lex.api.middleware.generic_api_gate`)
gets one operation per exposed operation and per action:

* ``GET  …/<model>/list`` → ``<model>_list`` (a JSON array; the documented
  filters are the model's ``lex_api_filters``, plus ``ordering``),
* ``POST …/<model>/one`` → ``<model>_create`` (201),
* ``GET|PATCH|DELETE …/<model>/one/{<lookup>}`` → ``<model>_retrieve``,
  ``<model>_update``, ``<model>_delete``,
* ``GET …/<model>/one/{<lookup>}/history`` → ``<model>_history``,
* ``…/<model>/one/{<lookup>}/action/<name>`` and ``…/<model>/action/<name>``
  → ``<model>_<name>`` (or the action's ``operation_id``), with the action's
  input (query parameters for GET, multipart for ``media="multipart"``),
  output and status.

Request and response bodies come from the model's serializer
(``api_serializers["default"]``); the request scope's parameter
(``REQUEST_SCOPE``) and the model's ``lex_query_parameters`` are documented
on every operation of the model. With ``short_paths`` the paths have no
``calculationId`` segment (lex serves both forms).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from lex.core import extensions

_RECORD = ("retrieve", "update", "delete")


def generic_config() -> Optional[dict]:
    from lex.api.schema import schema_config

    value = schema_config().get("generic")
    if value is None or value is False:
        return None
    return value if isinstance(value, dict) else {}


def _error_serializer():
    from lex.api.schema import schema_config

    dotted = schema_config().get("error_component")
    if not dotted:
        return None
    from django.utils.module_loading import import_string

    return import_string(dotted)


def exposed_models() -> List[type]:
    from django.apps import apps

    from lex.api.middleware.generic_api_gate import exposed_operations

    return sorted(
        (model for model in apps.get_models() if exposed_operations(model)),
        key=lambda model: model._meta.model_name,
    )


def model_serializer(model):
    """The serializer a generic endpoint of ``model`` answers with (the project's own, unwrapped)."""
    serializers = getattr(model, "api_serializers", None) or {}
    custom = serializers.get("default") if isinstance(serializers, dict) else None
    if custom is not None:
        return custom
    from lex.api.serializers.base_serializers import model2serializer

    return model2serializer(model)


def _base(config) -> str:
    from lex.api.schema import _base_path

    return f"{_base_path()}/api/model_entries"


def _query_parameters(model, *, listing: bool) -> list:
    from drf_spectacular.types import OpenApiTypes
    from drf_spectacular.utils import OpenApiParameter

    from lex.core import request_scope

    parameters = []
    scope = request_scope.param()
    if scope:
        parameters.append(OpenApiParameter(scope, OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
                                           description="The scope the request acts in (REQUEST_SCOPE)."))
    for name, spec in (getattr(model, "lex_query_parameters", None) or {}).items():
        kind, description = (spec if isinstance(spec, tuple) else (spec, ""))
        parameters.append(OpenApiParameter(name, kind or OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
                                           description=description))
    if listing:
        parameters.append(OpenApiParameter("ordering", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
                                           description="Comma-separated fields; a leading - sorts descending."))
        for name in getattr(model, "lex_api_filters", None) or ():
            parameters.append(OpenApiParameter(name, OpenApiTypes.STR, OpenApiParameter.QUERY, required=False))
    return parameters


def _errors(*statuses) -> Dict[int, Any]:
    error = _error_serializer()
    return {status: error for status in statuses} if error is not None else {}


def _lookup(model) -> str:
    return getattr(model, "lex_lookup_field", None) or "pk"


def _path_parameters(model, *, record: bool, short: bool) -> list:
    """The typed path parameters of an operation (the record's id, and calculationId on long paths)."""
    from django.db import models as dj
    from drf_spectacular.types import OpenApiTypes
    from drf_spectacular.utils import OpenApiParameter

    parameters = []
    if not short:
        parameters.append(OpenApiParameter("calculationId", OpenApiTypes.STR, OpenApiParameter.PATH))
    if record:
        lookup = _lookup(model)
        field = model._meta.pk if lookup == "pk" else model._meta.get_field(lookup)
        kind = OpenApiTypes.UUID if isinstance(field, dj.UUIDField) else (
            OpenApiTypes.INT if isinstance(field, (dj.AutoField, dj.IntegerField)) else OpenApiTypes.STR)
        parameters.append(OpenApiParameter(lookup, kind, OpenApiParameter.PATH))
    return parameters


def _view_class(model, operation_id, method, *, summary, request=None, responses, parameters, tags,
                description=None, multipart=False):
    """A view that only documents one operation (it is never routed)."""
    from drf_spectacular.utils import extend_schema
    from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
    from rest_framework.views import APIView

    decorated = extend_schema(
        operation_id=operation_id,
        summary=summary,
        description=description,
        request=request,
        responses=responses,
        parameters=parameters,
        tags=tags,
    )(lambda self, request, *args, **kwargs: None)
    attrs = {method: decorated, "__doc__": None}
    if multipart:
        attrs["parser_classes"] = [MultiPartParser, FormParser]
    else:
        attrs["parser_classes"] = [JSONParser]
    return type(f"Lex{model.__name__}{operation_id.title().replace('_', '')}Doc", (APIView,), attrs)


def _endpoint(path, method, view_class):
    import re

    regex = "^" + re.sub(r"\{[^}]+\}", "[^/]+", path.lstrip("/")) + "$"
    return (path, regex, method.upper(), view_class.as_view())


def generic_endpoints() -> List[Tuple[str, str, str, Any]]:
    """drf-spectacular endpoints for every exposed model's operations and actions."""
    config = generic_config()
    if config is None:
        return []
    from drf_spectacular.types import OpenApiTypes

    from lex.api.actions import actions_for, resolve_serializer
    from lex.api.middleware.generic_api_gate import exposed_operations

    short = bool(config.get("short_paths", True))
    base = _base(config)
    found = []
    for model in exposed_models():
        name = model._meta.model_name
        operations = exposed_operations(model)
        serializer = model_serializer(model)
        tags = [getattr(model, "lex_api_tag", None) or model.__name__]
        lookup = _lookup(model)
        calc = "" if short else "/{calculationId}"
        one = f"{base}/{name}{calc}/one"
        record = f"{one}/{{{lookup}}}"
        plain = _query_parameters(model, listing=False)
        on_record = _path_parameters(model, record=True, short=short) + plain
        on_model = _path_parameters(model, record=False, short=short) + plain

        def add(path, method, operation, **kwargs):
            view = _view_class(model, f"{name}_{operation}", method, tags=tags, **kwargs)
            found.append(_endpoint(path, method, view))

        if "list" in operations:
            add(f"{base}/{name}/list", "get", "list", summary=f"List {model._meta.verbose_name_plural}",
                responses={200: serializer(many=True), **_errors(400, 404)},
                parameters=_query_parameters(model, listing=True))
        if "create" in operations:
            add(one, "post", "create", summary=f"Create a {model._meta.verbose_name}", request=serializer,
                responses={201: serializer, **_errors(400, 404)}, parameters=on_model)
        if "retrieve" in operations:
            add(record, "get", "retrieve", summary=f"Read a {model._meta.verbose_name}",
                responses={200: serializer, **_errors(404)}, parameters=on_record)
        if "update" in operations:
            add(record, "patch", "update", summary=f"Change a {model._meta.verbose_name}", request=serializer,
                responses={200: serializer, **_errors(400, 404)}, parameters=on_record)
        if "delete" in operations:
            add(record, "delete", "delete", summary=f"Delete a {model._meta.verbose_name}",
                responses={200: serializer, **_errors(400, 404)}, parameters=on_record)
        if "history" in operations:
            history_path = f"{record}/history" if short else f"{base}/{name}{calc}/history/{{{lookup}}}"
            add(history_path, "get", "history", summary=f"History of a {model._meta.verbose_name}",
                responses={200: OpenApiTypes.OBJECT, **_errors(404)}, parameters=on_record)
        for spec in sorted(actions_for(model).values(), key=lambda s: s.name):
            path = f"{record}/action/{spec.name}" if spec.detail else f"{base}/{name}{calc}/action/{spec.name}"
            reading = list(spec.methods) == ["get"]
            parameters = list(on_record if spec.detail else on_model)
            request = None
            action_input = resolve_serializer(spec.input)
            if action_input is not None:
                if reading:
                    parameters.append(action_input)
                else:
                    request = action_input
            success = spec.status or 200
            output = resolve_serializer(spec.output) if spec.output is not None else OpenApiTypes.OBJECT
            if output is OpenApiTypes.BINARY:
                # A file: any content type (the action's HttpResponse sets the real one).
                responses = {(success, "application/octet-stream"): OpenApiTypes.BINARY}
            elif spec.many and output is not OpenApiTypes.OBJECT:
                responses = {success: output(many=True)}  # the action answers with a list of output
            else:
                responses = {success: output}
            responses.update(_errors(400, 404, *spec.responses))
            for method in spec.methods:
                operation_id = spec.operation_id or f"{name}_{spec.name}"
                if len(spec.methods) > 1:
                    operation_id = f"{operation_id}_{method}"
                view = _view_class(model, operation_id, method, tags=[*tags, *spec.tags],
                                   summary=spec.summary or spec.name.replace("_", " ").capitalize(),
                                   description=spec.description, request=request, responses=responses,
                                   parameters=parameters, multipart=spec.media == "multipart")
                found.append(_endpoint(path, method, view))
    return found
