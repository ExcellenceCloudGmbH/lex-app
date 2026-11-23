"""The table state lex-app generates for each table over a ``@ui.rows`` handler.

The handler returns every row; this state searches, filters, sorts and pages them on the server
(RFDS's ``query_rows``), works out the totals and the filters' choices, and keeps only the visible
page -- cut down to the fields the table uses -- in the state the browser receives.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

import reflex as rx
from rfds.patterns.exports.state import ExportMixin
from rfds.state.table import DataTableMixin, query_rows

from lex.lex_app.reflex.ui import rows as rows_mod

#: Most distinct values a filter offers; a column with more is searched, not filtered.
MAX_CHOICES = 50
#: The value of a filter's "All" (a dropdown has no empty value).
ALL = "__all__"

#: Rows handler key -> the table states that read it; route -> the table states on that page.
_BY_HANDLER: dict[str, list[type]] = {}
_BY_ROUTE: dict[str, list[type]] = {}
_BY_OWNER: dict[str, list[type]] = {}
_CACHE: dict[tuple, type] = {}
#: id of a ui.table part -> the state class built for it (for ``ui.testing.rows(table=...)``).
_CACHE_BY_TABLE: dict[int, type] = {}
_COUNTER = itertools.count(1)


def handler_key(handler: Any) -> str:
    return f"{handler.state.get_full_name()}.{handler.fn.__name__}"


class LexRowsTableState(DataTableMixin, ExportMixin, mixin=True):
    """RFDS's server-paged table over the rows a ``@ui.rows`` handler returns."""

    owner: ClassVar[Any] = None
    handler_name: ClassVar[str] = ""
    key_field: ClassVar[str] = "id"
    takes_search: ClassVar[bool] = False
    needed: ClassVar[tuple[str, ...]] = ()            # fields every row must have (the columns')
    open_fields: ClassVar[tuple[str, ...]] = ()       # fields that hold a ui.address
    row_open: ClassVar[str] = ""                      # the row's own open= field, if any
    tags_fields: ClassVar[tuple[str, ...]] = ()
    choice_fields: ClassVar[tuple[str, ...]] = ()
    search_fields: ClassVar[tuple[str, ...]] = ()     # the text columns a search looks in
    totals_spec: ClassVar[tuple[Mapping[str, Any], ...]] = ()
    totals_currency: ClassVar[str] = ""               # the money field whose currency splits the totals
    export_columns: ClassVar[tuple[tuple[str, str], ...]] = ()
    export_kinds: ClassVar[dict[str, str]] = {}       # an exported column's label -> its semantic kind
    query_param: ClassVar[str] = ""
    searchable: ClassVar[bool] = False                # the table has a search box
    exportable: ClassVar[bool] = False                # the table has an export menu

    shown: bool = False
    totals_rows: list[dict] = []
    choices: dict[str, list[dict[str, str]]] = {}
    chosen: dict[str, str] = {}
    _row_to: dict[str, str] = {}

    # -- events ----------------------------------------------------------------------------------
    @rx.event
    async def open(self):
        """The table appeared: read the first page (on a list page, with the address's search)."""
        self.shown = True
        self.page = 0
        name = type(self).query_param
        if name:
            self.query = " ".join(str(self.router.page.params.get(name, "") or "").split())[:200]
        async for _ in self.load():
            yield

    @rx.event
    def close(self):
        self.shown = False

    @rx.event
    async def reread(self):
        """Read again if the table is on screen: after a handler of its state, or ``ui.refresh``."""
        if not self.shown:
            return
        async for _ in self.load():
            yield

    @rx.event
    async def reopen(self):
        """The page opened again (a new record, the same page): the first page, read again."""
        if not self.shown:
            return
        self.page = 0
        async for _ in self.load():
            yield

    @rx.event(supersedes=True)
    async def set_query(self, value: str):
        text = " ".join(str(value or "").split())[:200]
        if text == self.query or not type(self).searchable:      # no search box: no search
            return
        self.query = text
        self.page = 0
        async for _ in self.load():
            yield
        name = type(self).query_param
        if name:
            yield rx.call_script(
                "(() => { const u = new URL(window.location.href); const v = " + json.dumps(text) + "; "
                f"if (v) {{ u.searchParams.set({json.dumps(name)}, v); }} else {{ u.searchParams.delete({json.dumps(name)}); }} "
                "window.history.replaceState(window.history.state, '', u); })()")

    @rx.event
    async def set_choice(self, field: str, value: str):
        """A filter's choice; ``""`` removes the filter."""
        if field not in type(self).choice_fields:
            return
        async for _ in choose(self, field, value):
            yield

    def _store_filter(self, key: str, value: Mapping[str, Any]) -> bool:
        # Only the table's own filters: a filter on any other field would let the browser learn,
        # from the count of matching rows, a value the table does not show.
        if key not in type(self).choice_fields:
            raise ValueError(f"{type(self).__name__}: {key!r} is not one of the table's filters")
        return super()._store_filter(key, value)

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
    def go(self, key: str):
        """A row whose link is decided per row: open the address its rows handler gave it."""
        where = self._row_to.get(str(key), "")
        if where:
            return rx.redirect(where)
        return None

    # -- reading ---------------------------------------------------------------------------------
    async def _all(self, query: str) -> list[dict]:
        """Every row, as the table shows it: read as the user, checked, in wire form."""
        from rfds.errors import RFDSUserError

        cls = type(self)
        handler = getattr(cls.owner, cls.handler_name)
        try:
            owner = await self.get_state(cls.owner)
            raw = await rows_mod.read(handler, owner, search=query if cls.takes_search else "")
            return self._wire(handler.fn.__ui_key__, raw)
        except Exception as exc:  # noqa: BLE001 - the table's error slot, never the exception's text
            raise RFDSUserError(rows_mod.describe(exc, handler.fn.__ui_key__, "")) from None

    @classmethod
    def _wire(cls, key: str, raw: list) -> list[dict]:
        from lex.lex_app.reflex.data import wire_value
        from lex.lex_app.reflex.ui.pages.links import Address

        keep = set(cls.row_fields)
        out: list[dict] = []
        seen: set[str] = set()
        for index, row in enumerate(raw):
            missing = [name for name in (cls.key_field, *cls.needed) if name not in row]
            if missing:
                raise rows_mod.RowsError(f"{key}: row {index} has no {', '.join(missing)}; every row needs the "
                                         f"table's key ({cls.key_field!r}) and each column's field.")
            ident = str(row[cls.key_field])
            if ident in seen:
                raise rows_mod.RowsError(f"{key}: two rows have the key {ident!r}; `key=` names a field whose "
                                         "value is different on every row.")
            seen.add(ident)
            wire: dict[str, Any] = {}
            for name, value in row.items():
                if name not in keep:            # a field the table does not use never leaves the server
                    continue
                if name in cls.open_fields:
                    wire[name] = str(value) if isinstance(value, Address) else ""
                elif name in cls.tags_fields:
                    items = value if isinstance(value, (list, tuple, set)) else ([] if value in (None, "") else [value])
                    wire[name] = [str(item) for item in items]
                elif isinstance(value, Address):
                    wire[name] = str(value)
                else:
                    wire[name] = wire_value(value)
            wire["id"] = ident          # RFDS keys a row by "id"
            out.append(wire)
        return out

    def _choices(self, rows: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, str]]]:
        cls = type(self)
        found: dict[str, list[dict[str, str]]] = {}
        for name in cls.choice_fields:
            values: set[str] = set()
            for row in rows:
                value = row.get(name)
                for item in (value if isinstance(value, list) else [value]):
                    if item not in (None, ""):
                        values.add(str(item))
            if len(values) > MAX_CHOICES:
                raise rows_mod.RowsError(f"the column {name!r} has {len(values)} different values, more than a "
                                         f"filter offers ({MAX_CHOICES}); search it instead (`search=True`).")
            found[name] = [{"value": ALL, "label": self._t("All")},
                           *({"value": v, "label": v} for v in sorted(values, key=str.casefold))]
        return found

    @classmethod
    def _matching(cls, rows: list[dict], *, sort: list, filters: dict, query: str) -> list[dict]:
        plain = {k: v for k, v in (filters or {}).items() if k not in cls.tags_fields}
        for name, spec in (filters or {}).items():
            if name in cls.tags_fields:
                wanted = str(spec.get("value", ""))
                rows = [r for r in rows if wanted in (r.get(name) or [])]
        text = "" if cls.takes_search or not cls.searchable else query
        if text and cls.tags_fields:              # a word may also be found among a row's tags
            words = text.casefold().split()

            def hit(row: dict) -> bool:
                hay = " ".join([*(str(row.get(n) or "") for n in cls.search_fields),
                                *(tag for n in cls.tags_fields for tag in (row.get(n) or []))]).casefold()
                return all(word in hay for word in words)

            rows, text = [r for r in rows if hit(r)], ""
        found, _ = query_rows(rows, sort=sort, filters=plain, query=text, columns=cls.table_columns,
                              search_fields=list(cls.search_fields), offset=0, limit=max(len(rows), 1))
        return found

    def _totals(self, rows: list[dict]) -> list[dict]:
        cls = type(self)
        if not cls.totals_spec:
            return []
        from rfds.data import compute_totals

        if not cls.totals_currency:
            return compute_totals(rows, cls.totals_spec, label=self._t("Total"))
        groups: dict[str, list[dict]] = {}
        for row in rows:
            groups.setdefault(str(row.get(cls.totals_currency, "") or ""), []).append(row)
        out: list[dict] = []
        for code, members in sorted(groups.items()):
            label = self._t("Total") + (f" ({code})" if code and len(groups) > 1 else "")
            for total in compute_totals(members, cls.totals_spec, label=label):
                total = {**total, "id": f"__total__{code}", cls.totals_currency: code}
                out.append(total)
        return out

    def _t(self, sentence: str) -> str:
        try:
            from rfds.i18n import translate

            return translate(self._ui_locale or "en", sentence, sentence)
        except Exception:  # noqa: BLE001 - English when translation is unavailable
            return sentence

    async def _fetch_page(self, *, offset: int, limit: int, sort: list, filters: dict,
                          query: str) -> tuple[list, int]:
        rows = await self._all(query)
        if type(self).choice_fields:
            try:
                self.choices = self._choices(rows)
            except rows_mod.RowsError as exc:
                from rfds.errors import RFDSUserError

                key = getattr(type(self).owner, type(self).handler_name).fn.__ui_key__
                raise RFDSUserError(rows_mod.describe(exc, key, "")) from None
        found = self._matching(rows, sort=sort, filters=filters, query=query)
        self.totals_rows = self._totals(found)
        page = found[offset:offset + limit]
        if type(self).row_open:
            self._row_to = {str(r["id"]): str(r.get(type(self).row_open, "") or "") for r in page}
        return page, len(found)

    # -- export ------------------------------------------------------------------------------------
    async def _export_column_kinds(self) -> dict[str, str]:
        return dict(type(self).export_kinds)

    async def _export_rows(self, kind: str) -> tuple[list[str], list[dict], dict[str, str]]:
        cls = type(self)
        if not cls.exportable:                    # no export menu: no export
            from rfds.errors import RFDSUserError

            raise RFDSUserError("This table cannot be exported.")
        rows = await self._all(self.query)
        found = self._matching(rows, sort=list(self.sort), filters=dict(self.filters), query=self.query)
        labels = [label for _, label in cls.export_columns]
        out = []
        for row in found:
            line = {}
            for name, label in cls.export_columns:
                value = row.get(name)
                line[label] = "; ".join(value) if isinstance(value, list) else value
            out.append(line)
        about = {"Search": self.query} if self.query else {}
        if self.filters:
            about["Filters"] = "; ".join(f"{name} = {dict(spec).get('value', '')}"
                                         for name, spec in sorted(dict(self.filters).items()))
        return labels, out, about


async def choose(state: Any, field: str, value: str):
    """Set or remove one filter (``""`` or "All" removes it), then read the first page again."""
    filters, chosen = dict(state.filters), dict(state.chosen)
    if value and value != ALL:
        filters[field], chosen[field] = {"op": "eq", "value": str(value)}, str(value)
    else:
        filters.pop(field, None)
        chosen.pop(field, None)
    state.filters, state.chosen, state.page = filters, chosen, 0
    async for _ in state.load():
        yield


def state_for(handler: Any, *, columns: Sequence[Mapping[str, Any]], row_fields: Sequence[str], **config: Any) -> type:
    """One table state class per distinct table: the same rows handler and the same configuration."""
    from lex.lex_app.reflex.data import stable

    key = (handler_key(handler), stable(config), stable(tuple(columns)), tuple(row_fields))
    cls = _CACHE.get(key)
    if cls is not None:
        return cls
    owner = handler.state
    name = f"RowsTable_{owner.__name__}_{handler.fn.__name__}_{next(_COUNTER)}"
    classvars = {"owner": owner, "handler_name": handler.fn.__name__, "table_columns": tuple(columns),
                 "page_size_options": (25, 50, 100, 200),
                 "row_fields": tuple(row_fields), "takes_search": rows_mod.takes_search(handler),
                 "export_entity": str(config.pop("entity", handler.fn.__name__)), **config}
    annotations = {k: ClassVar[Any] for k in classvars}
    annotations["page_size"] = int
    cls = type(name, (LexRowsTableState, rx.State), {"__module__": __name__, "__annotations__": annotations,
                                                     "page_size": 25, **classvars})
    globals()[name] = cls          # reachable by name, for Reflex's state managers (see data._state_for)
    _CACHE[key] = cls
    _BY_HANDLER.setdefault(handler_key(handler), []).append(cls)
    _BY_OWNER.setdefault(owner.get_full_name(), []).append(cls)
    return cls


def on_route(route: str, cls: type) -> None:
    """``cls`` is a table on the page at ``route``: the page's opener tells it to read again."""
    if route:
        listed = _BY_ROUTE.setdefault(route, [])
        if cls not in listed:
            listed.append(cls)


def reopen_events(route: str) -> list:
    return [cls.reopen for cls in _BY_ROUTE.get(route, ())]


def reread_events(*, handlers: Sequence[Any] = (), owner: Any = None) -> list:
    """The events that make the tables reading ``handlers`` (or any rows handler of ``owner``) read again."""
    classes: list[type] = []
    for handler in handlers:
        classes.extend(_BY_HANDLER.get(handler_key(handler), ()))
    if owner is not None:
        classes.extend(_BY_OWNER.get(owner.get_full_name(), ()))
    return [cls.reread for cls in dict.fromkeys(classes)]
