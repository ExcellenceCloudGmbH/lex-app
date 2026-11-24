"""A project's model data in RFDS's tables, grids and charts -- as the signed-in user sees it.

Every row a dashboard shows from a lex-app model comes through lex-app's own
REST API, called with the user's Keycloak access token, and every write goes
there too (decision D5). That is what makes a dashboard show exactly what the
grid shows: the same model and row permissions, the same field-level
filtering, the same validation, lifecycle hooks, audit log and calculations --
because it is the same code, not a copy of it.

========================  ====================================================
:func:`lex_api`           the lex-app API as the signed-in user
:func:`lex_table`         RFDS's server-paged ``data_table`` over a model
:func:`lex_grid`          RFDS's AG Grid over a model's rows
:func:`lex_columns`       RFDS column specs for a model's fields
:class:`LexRecordState`   the record ``?model=&pk=`` names, checked against the API
:func:`lex_rows`          a queryset as wire rows, for charts (inside ``run_orm``)
========================  ====================================================

``run_orm`` stays for what the API has no endpoint for -- an aggregate, a
calculation's inputs -- behind an ``auth=has_permission(...)`` check of the
handler that runs it.

A state module: no ``from __future__ import annotations`` here, because Reflex
reads the annotations of the state classes defined below at class creation.
"""

import dataclasses
import datetime
import decimal
import itertools
import os
import re
import uuid
from typing import Any, ClassVar, Iterable, Mapping, Sequence

import reflex as rx
from rfds.state.table import DataTableMixin

#: The most rows :func:`lex_grid` holds client-side: RFDS keeps state small
#: (no more than 2,000 rows travel to the browser).
GRID_ROW_LIMIT = 2000

#: How long an API call may take before the dashboard reports an error.
API_TIMEOUT_SECONDS = 30.0

_TRUTHY = {"1", "true", "yes", "y", "on"}


class LexApiError(Exception):
    """The lex-app API refused or failed a call: ``status`` is the HTTP status (0 = unreachable)."""

    def __init__(self, status: int, detail: Any = None):
        self.status = int(status)
        self.detail = detail
        super().__init__(f"lex-app API answered {self.status}: {detail!r}")

    @property
    def denied(self) -> bool:
        """The user may not do this, or the record is not theirs to see."""
        return self.status in (401, 403, 404)


def api_base_url() -> str:
    """Where the Reflex backend reaches lex-app's API.

    ``LEX_API_URL`` when set -- inside a cluster, the app's internal service
    address -- else the instance's public app domain (``DOMAIN_HOSTED``), else
    a local ``lex start``.
    """
    explicit = (os.getenv("LEX_API_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    domain = (os.getenv("DOMAIN_HOSTED") or "").strip().rstrip("/")
    if domain:
        return domain if "://" in domain else f"https://{domain}"
    return "http://localhost:8000"


def _verify_ssl() -> bool:
    return (os.getenv("OIDC_VERIFY_SSL") or "true").strip().lower() in _TRUTHY


def model_id(model: Any) -> str:
    """The id lex-app's routes use for a model: its lowercase model name."""
    if isinstance(model, str):
        return model.strip().lower()
    meta = getattr(model, "_meta", None)
    if meta is None:
        raise TypeError(f"expected a model class or a model id, got {model!r}")
    return meta.model_name


def operation_id(model: Any, action: str) -> str:
    """The ``calculationId`` lex-app records an operation's audit entries under."""
    return f"{model_id(model)}_{action}_{uuid.uuid4().hex[:12]}"


class LexApi:
    """lex-app's REST API, called as one user (their bearer token).

    The endpoints are the ones lex-app's own frontend calls, so permissions,
    validation, hooks, audit and calculations apply exactly as in the grid.
    """

    def __init__(self, token: str, base_url: str | None = None, *, client: Any = None):
        if not token:
            raise LexApiError(401, "not signed in")
        self._token = token
        self.base_url = (base_url or api_base_url()).rstrip("/")
        self._client = client

    async def _request(self, method: str, path: str, *, params: Any = None, json: Any = None) -> Any:
        import httpx

        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        url = f"{self.base_url}/api/{path.lstrip('/')}"
        try:
            if self._client is not None:
                response = await self._client.request(method, url, params=params, json=json, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS, verify=_verify_ssl()) as client:
                    response = await client.request(method, url, params=params, json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise LexApiError(0, str(exc)) from exc
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text[:500]
            raise LexApiError(response.status_code, detail)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def list(
        self,
        model: Any,
        *,
        page: int = 1,
        per_page: int = 25,
        ordering: str | None = None,
        params: Iterable[tuple[str, str]] = (),
    ) -> dict:
        """One page of a model's records: ``{"count": n, "results": [...]}``.

        ``params`` are lex-app's list filters -- ``field=value``, a lookup such as
        ``amount__gte=100``, ``field!=value`` to exclude, a field repeated for
        any of several values.
        """
        query = [("page", str(int(page))), ("perPage", str(int(per_page)))]
        if ordering:
            query.append(("ordering", ordering))
        query.extend((str(k), str(v)) for k, v in params)
        data = await self._request("GET", f"model_entries/{model_id(model)}/list", params=query)
        if isinstance(data, list):
            return {"count": len(data), "results": data}
        return {"count": int((data or {}).get("count", 0)), "results": list((data or {}).get("results", []))}

    async def get(self, model: Any, pk: Any) -> dict:
        """One record; :class:`LexApiError` with ``denied`` when it is not the user's to see."""
        return await self._request("GET", f"model_entries/{model_id(model)}/{operation_id(model, 'read')}/one/{pk}")

    async def create(self, model: Any, data: Mapping[str, Any]) -> dict:
        """Create a record as the user; returns it as lex-app serialises it."""
        return await self._request("POST", f"model_entries/{model_id(model)}/{operation_id(model, 'create')}/one",
                                   json=dict(data))

    async def update(self, model: Any, pk: Any, data: Mapping[str, Any]) -> dict:
        """Change some fields of a record as the user (a partial update)."""
        return await self._request("PATCH",
                                   f"model_entries/{model_id(model)}/{operation_id(model, 'update')}/one/{pk}",
                                   json=dict(data))

    async def delete(self, model: Any, pk: Any) -> None:
        """Delete a record as the user."""
        await self._request("DELETE", f"model_entries/{model_id(model)}/{operation_id(model, 'delete')}/one/{pk}")


async def lex_api(state: rx.State) -> LexApi:
    """The lex-app API as the user signed in to ``state``'s session.

    Call it from an event handler::

        api = await lex_api(self)
        await api.update(Fund, self.pk, {"status": "approved"})

    Raises :class:`LexApiError` (401) when nobody is signed in, or their access
    token expired and has not been renewed yet.
    """
    from lex.lex_app.reflex.auth import current_access_token

    return LexApi(await current_access_token(state))


# -- wire values ------------------------------------------------------------


def wire_value(value: Any) -> Any:
    """A value as RFDS carries it to the browser: Decimals as strings, dates as ISO strings."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, decimal.Decimal):
        return format(value, "f") if value.is_finite() else None
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): wire_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [wire_value(v) for v in value]
    pk = getattr(value, "pk", None)
    if pk is not None:
        return wire_value(pk)
    return str(value)


def lex_rows(rows: Any, fields: Sequence[str] | None = None) -> list[dict]:
    """Records as wire rows (``fields`` of each), for a chart or a client-side table.

    Takes a queryset -- run it inside ``run_orm`` -- or any iterable of model
    instances or mappings. A queryset is read with ``values(*fields)``, so a
    related field's path (``fund__name``) works too.
    """
    values = getattr(rows, "values", None)
    if fields and callable(values) and hasattr(rows, "model"):
        return [{k: wire_value(v) for k, v in row.items()} for row in values(*fields)]
    result = []
    for row in rows:
        if isinstance(row, Mapping):
            source = row if fields is None else {f: row.get(f) for f in fields}
        else:
            names = fields or [f.name for f in row._meta.concrete_fields]
            source = {f: getattr(row, f, None) for f in names}
        result.append({k: wire_value(v) for k, v in source.items()})
    return result


# -- columns ----------------------------------------------------------------


def column_kind(field: Any) -> str:
    """The RFDS column kind a Django field is shown as."""
    from django.db import models

    if field.primary_key or isinstance(field, (models.AutoField, models.BigAutoField)):
        return "id"
    if isinstance(field, models.BooleanField):
        return "boolean"
    if isinstance(field, (models.IntegerField, models.BigIntegerField, models.SmallIntegerField,
                          models.PositiveIntegerField, models.PositiveSmallIntegerField)):
        return "int"
    if isinstance(field, (models.DecimalField, models.FloatField)):
        return "number"
    if isinstance(field, models.DateTimeField):
        return "instant"
    if isinstance(field, models.DateField):
        return "date"
    if getattr(field, "choices", None):
        return "status"
    return "text"


def visible_fields(model: Any) -> list[Any]:
    """The fields a model's table shows by default: concrete, not internal, not credential-shaped.

    Credential-shaped names (``password``, ``*_token``...) are the ones lex-app's
    serialisers refuse to publish, so the API never returns them and a column
    for one would stay empty.
    """
    from lex.api.serializers.sensitive_fields import is_sensitive_field_name

    return [
        field
        for field in model._meta.concrete_fields
        if not field.name.startswith("_")
        and not getattr(field, "lex_hidden", False)
        and not is_sensitive_field_name(field.name)
    ]


def lex_columns(model: Any, fields: Sequence[str] | None = None, **overrides: Mapping[str, Any]) -> list:
    """RFDS ``column`` specs for ``fields`` of a model (default: :func:`visible_fields`).

    Each column's kind follows its Django field (ids, numbers, dates, booleans,
    choices); its header is the field's verbose name. ``overrides`` maps a field
    to the ``rfds.column`` options to change, e.g. ``nav={"kind": "money",
    "ccy_field": "currency"}``.
    """
    from rfds.components.tables import column

    by_name = {field.name: field for field in model._meta.concrete_fields}
    chosen = list(fields) if fields is not None else [field.name for field in visible_fields(model)]
    specs = []
    for name in chosen:
        field = by_name.get(name)
        if field is None:
            raise ValueError(f"{model.__name__} has no field {name!r}")
        options = {"kind": column_kind(field), **dict(overrides.get(name, {}))}
        header = options.pop("header", None) or str(field.verbose_name).capitalize()
        specs.append(column(name, header, **options))
    return specs


def lex_grid_columns(model: Any, fields: Sequence[str] | None = None, **overrides: Mapping[str, Any]) -> list:
    """RFDS grid ``col`` specs, the AG Grid twin of :func:`lex_columns`."""
    from rfds.grids import col

    grid_kinds = {"text", "id", "int", "number", "money", "percent", "date", "instant", "status", "boolean"}
    by_name = {field.name: field for field in model._meta.concrete_fields}
    chosen = list(fields) if fields is not None else [field.name for field in visible_fields(model)]
    specs = []
    for name in chosen:
        field = by_name.get(name)
        if field is None:
            raise ValueError(f"{model.__name__} has no field {name!r}")
        kind = column_kind(field)
        options = {"kind": kind if kind in grid_kinds else "text", **dict(overrides.get(name, {}))}
        header = options.pop("header", None) or str(field.verbose_name).capitalize()
        specs.append(col(name, header, **options))
    return specs


# -- filters ----------------------------------------------------------------

_OPS = {"lt": "__lt", "lte": "__lte", "gt": "__gt", "gte": "__gte",
        "contains": "__icontains", "starts_with": "__istartswith"}


def filter_params(filters: Mapping[str, Mapping[str, Any]]) -> list[tuple[str, str]]:
    """RFDS table filters (``{field: {op, value, values, value_to}}``) as lex-app list parameters."""
    params: list[tuple[str, str]] = []
    for field, spec in (filters or {}).items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(__[A-Za-z_][A-Za-z0-9_]*)*", str(field)):
            continue
        op = spec.get("op")
        value = spec.get("value")
        if op == "eq":
            params.append((field, str(value)))
        elif op == "ne":
            params.append((f"{field}!", str(value)))
        elif op in ("in", "not_in"):
            key = field if op == "in" else f"{field}!"
            params.extend((key, str(v)) for v in spec.get("values") or [])
        elif op in _OPS:
            params.append((f"{field}{_OPS[op]}", str(value)))
        elif op == "between":
            params.append((f"{field}__gte", str(value)))
            params.append((f"{field}__lte", str(spec.get("value_to"))))
        elif op in ("blank", "not_blank"):
            params.append((f"{field}__blank", "true" if op == "blank" else "false"))
    return params


def scope_params(scope: Mapping[str, Any]) -> list[tuple[str, str]]:
    """A table's fixed filters (``lex_table(..., filters={"fund": pk})``) as list parameters."""
    params = []
    for field, value in (scope or {}).items():
        if value is None or value == "":
            continue
        for item in value if isinstance(value, (list, tuple)) else [value]:
            params.append((str(field), str(item)))
    return params


# -- table and grid states ----------------------------------------------------


class ChosenColumns(rx.State, mixin=True):
    """The columns a user chose in RFDS's "Columns" menu (``ui.table(choose_columns=True)``).

    Only the table's own columns are kept; the first column names the row, so it is always shown
    and stays first; a width is whole pixels from 40 to 1,200 (RFDS's column chooser's range).
    They live in the browser tab's state until the tab is closed: a new visit keeps them, and a
    new tab starts from the table as declared.
    """

    view_columns: list[str] = []          # shown columns ([] = all)
    column_order: list[str] = []          # every column in display order ([] = as declared)
    column_widths: dict[str, int] = {}    # a user's width in px per column

    def _column_ids(self) -> list[str]:
        return [str(spec.get("field")) for spec in (getattr(type(self), "table_columns", ()) or ())]

    def _chosen(self, ids: Any) -> list[str]:
        known = self._column_ids()
        picked = [str(i) for i in (ids if isinstance(ids, (list, tuple)) else []) if str(i) in known]
        picked = list(dict.fromkeys(picked))
        if known and picked:
            picked = [known[0], *(i for i in picked if i != known[0])]
        return picked

    @rx.event
    def set_view_columns(self, ids: list[str]):
        self.view_columns = self._chosen(ids)

    @rx.event
    def set_column_order(self, ids: list[str]):
        self.column_order = self._chosen(ids)

    @rx.event
    def set_column_widths(self, widths: dict):
        known = set(self._column_ids())
        clean: dict[str, int] = {}
        for key, value in (widths if isinstance(widths, Mapping) else {}).items():
            if str(key) in known and isinstance(value, (int, float)) and not isinstance(value, bool) \
                    and 40 <= value <= 1200:
                clean[str(key)] = int(value)
        self.column_widths = clean


class LexTableState(DataTableMixin, ChosenColumns, mixin=True):
    """RFDS's server-paged table over one model, read through the lex-app API as the user.

    ``resource`` names the model; ``search_field`` is the field the quick
    search matches (``icontains``). :func:`lex_table` builds one per table.
    """

    resource: ClassVar[str] = ""
    search_field: ClassVar[str] = ""
    #: The table's fixed filters, set when it mounts (``lex_table(filters=...)``).
    scope: dict[str, str] = {}
    #: A filter dropdown's choice per field (``ui.table(filters=[...])``).
    chosen: dict[str, str] = {}
    #: The fields the browser may filter on (``ui.table(filters=[...])``); ``None``: any field,
    #: as ``lex_table`` has always allowed (a page that builds its own filter bar over it).
    choice_fields: ClassVar[tuple[str, ...] | None] = None
    #: The fixed filters' fields the browser may send when the table appears (``ui.table(where=)``);
    #: ``None``: any, as ``lex_table`` has always allowed.
    scope_fields: ClassVar[tuple[str, ...] | None] = None

    def _store_filter(self, key: str, value: Mapping[str, Any]) -> bool:
        allowed = type(self).choice_fields
        if allowed is not None and key not in allowed:
            raise ValueError(f"{type(self).__name__}: {key!r} is not one of the table's filters")
        return super()._store_filter(key, value)

    @rx.event
    async def set_choice(self, field: str, value: str):
        """A filter dropdown's choice; ``""`` removes the filter."""
        from lex.lex_app.reflex.ui.pages.rows_table import choose

        allowed = type(self).choice_fields
        if allowed is not None and field not in allowed:
            return
        async for _ in choose(self, field, value):
            yield

    @rx.event
    async def clear_filters(self):
        """"Clear filters": every filter, and the dropdowns that show them."""
        self.chosen = {}
        if self.filters:
            self.filters = {}
            await self._after_filter_change()

    @rx.event
    async def remove_filter(self, key: str):
        self.chosen = {k: v for k, v in self.chosen.items() if k != key}
        if key in self.filters:
            self.filters = {k: v for k, v in self.filters.items() if k != key}
            await self._after_filter_change()

    @rx.event
    async def open(self, scope: dict):
        """Mount: take the table's fixed filters (state vars resolved by now), then load."""
        allowed = type(self).scope_fields
        self.scope = {str(k): "" if v is None else str(v) for k, v in (scope or {}).items()
                      if allowed is None or str(k) in allowed}
        self.page = 0
        async for _ in self.load():
            yield

    async def _fetch_page(self, *, offset, limit, sort, filters, query):
        params = scope_params(self.scope) + filter_params(filters)
        if query and type(self).search_field:
            params.append((f"{type(self).search_field}__icontains", query))
        ordering = ",".join(("-" if level.get("sort") == "desc" else "") + str(level.get("col_id"))
                            for level in sort or [])
        api = await lex_api(self)
        data = await api.list(type(self).resource, page=offset // max(limit, 1) + 1, per_page=limit,
                              ordering=ordering or None, params=params)
        return [{k: wire_value(v) for k, v in row.items()} for row in data["results"]], data["count"]


class LexGridState(rx.State, mixin=True):
    """A model's rows for RFDS's AG Grid, read once through the lex-app API as the user."""

    resource: ClassVar[str] = ""
    rows: list[dict] = []
    total_rows: int = 0
    truncated: bool = False
    dataset_version: int = 0
    error: str = ""
    scope: dict[str, str] = {}

    @rx.event
    async def open(self, scope: dict):
        """Mount: take the grid's fixed filters, then load up to :data:`GRID_ROW_LIMIT` rows."""
        self.scope = {str(k): "" if v is None else str(v) for k, v in (scope or {}).items()}
        await self._reload()

    @rx.event
    async def refresh(self):
        """Read the rows again (after a write, or on a button)."""
        await self._reload()

    async def _reload(self) -> None:
        try:
            api = await lex_api(self)
            data = await api.list(type(self).resource, page=1, per_page=GRID_ROW_LIMIT,
                                  params=scope_params(self.scope))
        except LexApiError as exc:
            self.rows, self.total_rows, self.error = [], 0, "denied" if exc.denied else "unavailable"
            return
        self.rows = [{k: wire_value(v) for k, v in row.items()} for row in data["results"]]
        self.total_rows = data["count"]
        self.truncated = data["count"] > len(self.rows)
        self.error = ""
        self.dataset_version += 1


_STATE_CACHE: dict[tuple, type] = {}
_COUNTER = itertools.count(1)


def stable(value: Any, depth: int = 0) -> Any:
    """``value`` as a key that is the same for the same declaration in every build of a page.

    A function counts by its code's place and what its closure holds, never by its address: a
    page is built when it is registered and again when it is compiled, and both builds must
    find one state class.
    """
    if depth > 6:
        return "..."
    if isinstance(value, rx.Var):
        return ("var", str(value))
    if isinstance(value, Mapping):
        return tuple(sorted((str(k), stable(v, depth + 1)) for k, v in value.items()))
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [stable(v, depth + 1) for v in value]
        return tuple(sorted(items, key=repr)) if isinstance(value, (set, frozenset)) else tuple(items)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return (type(value).__qualname__,) + tuple(stable(getattr(value, f.name), depth + 1)
                                                   for f in dataclasses.fields(value))
    code = getattr(value, "__code__", None)
    if code is not None:
        cells = tuple(stable(cell.cell_contents, depth + 1) for cell in (getattr(value, "__closure__", None) or ())
                      if _filled(cell))
        bound = getattr(value, "__self__", None)
        return ("fn", getattr(value, "__qualname__", ""), code.co_filename, code.co_firstlineno, code.co_code,
                repr(code.co_consts), cells, id(bound) if bound is not None else None)
    return repr(value)


def _filled(cell: Any) -> bool:
    try:
        cell.cell_contents
    except ValueError:
        return False
    return True


def _state_for(base: type, model: Any, classvars: Mapping[str, Any], *, defaults: Mapping[str, Any] | None = None,
               identity: tuple = ()) -> type:
    """One state class per distinct table or grid: same model, configuration and fixed filters.

    ``identity`` is what else tells two of them apart -- their fixed filters, a
    state var's name included -- so two tables of one model filtered
    differently never share rows. ``defaults`` override the base's state vars.
    """
    defaults = dict(defaults or {})
    key = (base.__name__, model_id(model), tuple(sorted((k, stable(v)) for k, v in classvars.items())),
           tuple(sorted((k, stable(v)) for k, v in defaults.items())), identity)
    cls = _STATE_CACHE.get(key)
    if cls is None:
        name = f"{base.__name__.removeprefix('Lex').removesuffix('State')}_{model_id(model)}_{next(_COUNTER)}"
        annotations = {"resource": ClassVar[str], **{k: ClassVar[Any] for k in classvars}}
        annotations.update({k: type(v) for k, v in defaults.items()})
        namespace = {"__module__": __name__, "resource": model_id(model), **classvars, **defaults,
                     "__annotations__": annotations}
        cls = type(name, (base, rx.State), namespace)
        # Reachable by name, as a class written in this module would be: Reflex's state managers
        # pickle state -- to disk in development, to Redis across workers -- and pickle finds a
        # class by its module and name. Every worker compiles the same pages in the same order,
        # so the name means the same class in each.
        globals()[name] = cls
        _STATE_CACHE[key] = cls
    return cls


def _filters_identity(filters: Mapping[str, Any] | None) -> tuple:
    return tuple(sorted((str(k), repr(v)) for k, v in (filters or {}).items()))


def table_state(model: Any, *, columns: Sequence[Mapping[str, Any]], search_field: str = "",
                page_size: int = 25, filters: Mapping[str, Any] | None = None,
                row_fields: Sequence[str] = (), choice_fields: Sequence[str] | None = None,
                scope_fields: Sequence[str] | None = None) -> type:
    """The :class:`LexTableState` for one table of ``model`` (one class per distinct table).

    The table opens at ``page_size`` rows, which is also among the sizes the
    user can pick. ``row_fields`` are kept in each row besides the columns'
    own fields -- a primary key that is not shown, for the row's link.
    """
    sizes = tuple(sorted({25, 50, 100, int(page_size)}))
    classvars: dict[str, Any] = {
        "table_columns": tuple(columns),
        "search_field": search_field,
        "page_size_options": sizes,
    }
    if row_fields:
        classvars["row_fields"] = tuple(row_fields)
    if choice_fields is not None:
        classvars["choice_fields"] = tuple(choice_fields)
    if scope_fields is not None:
        classvars["scope_fields"] = tuple(scope_fields)
    return _state_for(LexTableState, model, classvars, defaults={"page_size": int(page_size)},
                      identity=_filters_identity(filters))


def _default_search_field(model: Any) -> str:
    from django.db import models

    for field in visible_fields(model):
        if isinstance(field, (models.CharField, models.TextField)) and not field.choices:
            return field.name
    return ""


def lex_table(
    model: Any,
    *,
    filters: Mapping[str, Any] | None = None,
    fields: Sequence[str] | None = None,
    columns: Sequence[Mapping[str, Any]] | None = None,
    caption: Any = None,
    search_field: str | None = None,
    page_size: int = 25,
    row_fields: Sequence[str] = (),
    **table_props: Any,
) -> rx.Component:
    """RFDS's ``data_table`` over a model, server-paged through the lex-app API as the user.

    Args:
        model: The model class.
        filters: Fixed filters, ``{field: value}``; a value may be a state var
            (``{"fund": FundDashboard.pk}``) -- it is read when the table mounts.
        fields: The fields to show (default: :func:`visible_fields`).
        columns: RFDS column specs, replacing ``fields`` entirely.
        caption: The table's accessible caption (default: the model's plural name).
        search_field: The field the quick search matches (default: the first plain text field).
        page_size: Rows per page (RFDS shows at most 200).
        row_fields: Fields kept in each row besides the columns' own (a key that is not shown).
        **table_props: Anything else ``rfds.data_table`` takes (``row_href``,
            ``on_row_click``, ``toolbar``, ``density``...).
    """
    import rfds

    cols = list(columns) if columns is not None else lex_columns(model, fields)
    search = _default_search_field(model) if search_field is None else search_field
    state = table_state(model, columns=cols, search_field=search, page_size=page_size, filters=filters,
                        row_fields=row_fields)
    table = rfds.data_table(
        state=state,
        columns=cols,
        caption=caption or str(model._meta.verbose_name_plural).capitalize(),
        **table_props,
    )
    return rx.box(table, on_mount=state.open(dict(filters or {})), width="100%")


def grid_state(model: Any, *, fields: Sequence[str] | None = None, filters: Mapping[str, Any] | None = None) -> type:
    """The :class:`LexGridState` for one grid of ``model`` (one class per fields and fixed filters)."""
    return _state_for(LexGridState, model, {},
                      identity=(tuple(fields or ()),) + _filters_identity(filters))


def lex_grid(
    model: Any,
    *,
    filters: Mapping[str, Any] | None = None,
    fields: Sequence[str] | None = None,
    columns: Sequence[Any] | None = None,
    caption: Any = None,
    grid_id: str | None = None,
    height: str = "60vh",
    **grid_props: Any,
) -> rx.Component:
    """RFDS's AG Grid (Community) over a model's rows, read through the lex-app API as the user.

    Holds up to :data:`GRID_ROW_LIMIT` rows client-side, so sorting, filtering,
    copying and the status bar's sums are instant; a model with more rows than
    that says so under the grid -- use :func:`lex_table`, which pages on the
    server, for those.
    """
    import rfds

    state = grid_state(model, fields=fields if columns is None else [repr(c) for c in columns], filters=filters)
    cols = list(columns) if columns is not None else lex_grid_columns(model, fields)
    grid = rfds.data_grid(
        id=grid_id or f"lex-grid-{model_id(model)}",
        columns=cols,
        caption=caption or str(model._meta.verbose_name_plural).capitalize(),
        row_data=state.rows,
        dataset_version=state.dataset_version,
        height=height,
        **grid_props,
    )
    return rx.vstack(
        grid,
        rx.cond(
            state.truncated,
            rx.text(f"Showing the first {GRID_ROW_LIMIT} rows.", size="1", color_scheme="gray"),
        ),
        on_mount=state.open(dict(filters or {})),
        width="100%",
        # A stack shrinks its children to their content by default ("start"). RFDS sizes the
        # grid's frame at 100% of its block, and the block has no width of its own: shrunk,
        # the grid collapses to its first column. Stretched, it fills the page.
        align="stretch",
        spacing="2",
    )


# -- the record a dashboard is about ------------------------------------------


def record_model(name: Any) -> Any:
    """The project model a record dashboard's ``?model=`` names (case-insensitively), or ``None``."""
    from lex.lex_app.reflex.dashboards import _lookup_model, _project_app_label

    name = str(name or "").strip()
    return _lookup_model(name, _project_app_label()) if name else None


class LexRecordState(rx.State):
    """The record ``?model=&pk=`` names -- or ``model``'s, for a shared dashboard -- checked against the API.

    A state, not a mixin: a class whose only state base is a mixin is itself a
    mixin in Reflex, so ``class FundDashboard(LexRecordState)`` -- the way a
    dashboard is written -- would never become a state and its handlers would
    not bind. Each dashboard is a substate of this one.

    :meth:`record` asks lex-app's API for the record as the user first: only if
    the API would show it does the dashboard get the model instance, so a
    record dashboard never shows a record the grid would hide::

        class FundDashboard(LexRecordState):
            model = Fund

            @rx.event
            async def load(self):
                fund = await self.record()
                ...
    """

    model: ClassVar[Any] = None
    record_title: str = ""
    record_data: dict = {}
    record_state: str = ""

    @rx.var
    def pk(self) -> str:
        """The record's primary key, from the URL."""
        return str(self.router.url.query_parameters.get("pk") or "").strip()

    def _record_model(self):
        if type(self).model is not None:
            return type(self).model
        return record_model(self.router.url.query_parameters.get("model"))

    async def record(self):
        """The model instance, or ``None`` when there is none or the user may not see it.

        Sets ``record_data`` (the record as the API serialises it, wire-safe),
        ``record_title`` and ``record_state`` -- ``"ok"``, ``"missing"``,
        ``"denied"``, or ``"unavailable"`` when lex-app cannot answer -- for the
        page to show.
        """
        from lex.lex_app.reflex.dashboards import current_record
        from lex.lex_app.reflex.django_orm import run_orm

        model = self._record_model()
        pk = self.pk
        if model is None or not pk:
            self.record_state, self.record_data, self.record_title = "missing", {}, ""
            return None
        try:
            data = await (await lex_api(self)).get(model, pk)
        except LexApiError as exc:
            if exc.status in (401, 403):
                self.record_state = "denied"
            elif exc.status == 404:
                self.record_state = "missing"
            else:
                # lex-app unreachable, or failing: not a record that does not exist.
                self.record_state = "unavailable"
            self.record_data, self.record_title = {}, ""
            return None
        instance = await current_record(self, model)
        if instance is None:
            self.record_state, self.record_data, self.record_title = "missing", {}, ""
            return None
        self.record_data = wire_value(data or {})
        self.record_title = await run_orm(str, instance)
        self.record_state = "ok"
        return instance
