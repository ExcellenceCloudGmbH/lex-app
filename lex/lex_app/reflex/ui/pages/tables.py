"""ui.table and ui.column: rows from a model or a ``@ui.rows`` handler, paged on the server."""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Callable

import reflex as rx

from lex.lex_app.reflex.ui.pages import links
from lex.lex_app.reflex.ui.pages.parts import Build, Part, called, say

#: What a column's kind is called in RFDS's column spec; the last four are drawn by lex-app.
_RFDS_KIND = {"text": "text", "number": "number", "money": "money", "percent": "percent", "date": "date",
              "datetime": "instant", "status": "text", "tag": "text", "tags": "text"}
KINDS = tuple(_RFDS_KIND)
_DRAWN = ("status", "tag", "tags")
_NUMERIC = ("number", "money", "percent")
_TEXTUAL = (None, "text", "status", "tag", "tags")
_CODE = re.compile(r"[A-Z]{3}")
_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclasses.dataclass(frozen=True)
class Column:
    key: str
    label: str | None = None
    kind: str | None = None
    currency: str | None = None
    tones: tuple = ()
    open: str | None = None
    show: Callable[[Any], Any] | None = None


def column(key: str, *, label: str | None = None, kind: str | None = None, currency: str | None = None,
           tones: dict | None = None, open: str | None = None, show: Callable[[Any], Any] | None = None) -> Column:
    """A table column that needs more than its name.

    ``kind`` is text, number, money, percent, date, datetime, status, tag or tags, each drawn the
    same way on every page. ``currency`` goes with money: a code (``"EUR"``) or the name of the
    row's field that holds the code (``currency="currency"``). ``tones`` goes with status:
    ``{"Open": "info", "Late": "warning"}``. ``open`` names the row's field holding a
    ``ui.address``: the cell becomes a link. ``show`` draws a cell no kind describes: a function
    of the row returning ``ui.text``, ``ui.status`` or ``ui.link``.
    """
    from lex.lex_app.reflex.ui.pages.vocab import TONES

    where = f"ui.column({key!r}"
    if not (isinstance(key, str) and _FIELD.fullmatch(key)):
        raise ValueError(f"ui.column() takes a field name such as \"amount\"; got {key!r}")
    if kind is not None and kind not in _RFDS_KIND:
        raise ValueError(f"{where}, kind=) must be one of {', '.join(KINDS)}; got {kind!r}")
    if label is not None and not (isinstance(label, str) and label):
        raise ValueError(f"{where}, label=) takes a sentence; got {label!r}")
    if currency is not None and kind != "money":
        raise ValueError(f"{where}, currency=) goes with kind=\"money\"")
    if currency is not None and not (isinstance(currency, str) and _FIELD.fullmatch(currency)):
        raise ValueError(f"{where}, currency=) takes a code such as \"EUR\", or the name of the row's field that "
                         f"holds the code; got {currency!r}")
    if tones is not None:
        if kind != "status":
            raise ValueError(f"{where}, tones=) goes with kind=\"status\"")
        wrong = [tone for tone in dict(tones).values() if tone not in TONES]
        if wrong:
            raise ValueError(f"{where}, tones=) takes the tones {', '.join(TONES)}; got {wrong[0]!r}")
    if open is not None and not (isinstance(open, str) and _FIELD.fullmatch(open)):
        raise ValueError(f"{where}, open=) names the row's field that holds a ui.address; got {open!r}. "
                         "To open one page from every row, give the table `open=page`.")
    if show is not None and kind is not None:
        raise ValueError(f"{where}): give kind= or show=, not both. show= draws the cell itself.")
    if show is not None and not callable(show):
        raise TypeError(f"{where}, show=) takes a function of the row: `show=lambda row: ui.text(row[\"x\"])`")
    return Column(key, label, kind, currency, tuple(dict(tones or {}).items()), open, show)


class _Recorder:
    """A row that notes the fields read from it: which fields the browser must receive."""

    def __init__(self, row: Any, seen: set[str]):
        self._row, self._seen = row, seen

    def __getitem__(self, name: str) -> Any:
        self._seen.add(str(name))
        return self._row[name]

    def get(self, name: str, default: Any = None) -> Any:
        return self[name]


def _record(fn: Callable[[Any], Any]) -> tuple[Any, set[str]]:
    seen: set[str] = set()
    return fn(_Recorder(rx.Var("row").to(dict), seen)), seen


@dataclasses.dataclass(eq=False)
class Table(Part):
    source: Any
    columns: tuple = ()
    open: Any = None
    empty: Any = None
    visible: Any = None
    key: str | None = None
    where: dict | None = None
    search: bool = False
    filters: tuple = ()
    export: bool = False
    totals: dict | None = None
    row_actions: Callable[[Any], Any] | None = None

    def build(self, ctx: Build) -> rx.Component:
        # A model table reads through the API as the user; without the grant it could only show an error.
        from lex.lex_app.reflex.ui import guard

        if self.visible is None and _is_model(self.source):
            return dataclasses.replace(self, visible=guard.read(self.source)).build(ctx)
        return super().build(ctx)

    # -- shared checks -------------------------------------------------------------------------------
    def _columns(self) -> list[Column]:
        return [c if isinstance(c, Column) else Column(str(c)) for c in self.columns]

    def _check(self, ctx: Build, cols: list[Column]) -> None:
        keys = [c.key for c in cols]
        if not cols:
            raise ctx.fail("the table has no columns.", "Name them: `columns=[\"name\", ui.column(\"amount\", kind=\"money\")]`.")
        first = cols[0]
        if first.kind in _DRAWN or first.show is not None or first.open is not None:
            raise ctx.fail(f"the first column, `{first.key}`, is drawn by `{'show=' if first.show else 'open=' if first.open else first.kind}`.",
                           "The first column names the row and is drawn as text (and is the row's link). Put a "
                           "plain column first, such as the name.")
        unknown = [name for name in self.filters if name not in keys]
        if unknown:
            raise ctx.fail(f"`filters=` names {', '.join(unknown)}, which is not a column.",
                           f"Filter on columns of the table: {', '.join(keys)}.")
        for name, how in dict(self.totals or {}).items():
            col = next((c for c in cols if c.key == name), None)
            if col is None:
                raise ctx.fail(f"`totals=` names {name}, which is not a column.",
                               f"Total columns of the table: {', '.join(keys)}.")
            if how not in ("sum", "count"):
                raise ctx.fail(f"`totals=` asks for {how!r} of {name}.", "A total is \"sum\" or \"count\".")
            if how == "sum" and col.kind not in _NUMERIC:
                raise ctx.fail(f"`totals=` sums {name}, which is not a number.",
                               f"Give it kind=\"number\", \"money\" or \"percent\", or count it: `{{{name!r}: \"count\"}}`.")
        if self.search and not any(c.kind in _TEXTUAL and c.show is None for c in cols):
            raise ctx.fail("`search=True`, and the table has no text column to search.",
                           "Add a text column, or leave search out.")
        from lex.lex_app.reflex.ui.pages import vocab

        for c in cols:
            if c.show is not None:
                part, _ = _record(c.show)
                if not isinstance(part, (vocab.Text, vocab.Status, vocab.Link)):
                    raise ctx.fail(f"`show=` of column `{c.key}` returned {called(part)}.",
                                   "Return one of `ui.text(...)`, `ui.status(...)`, `ui.link(...)`.")
        if self.row_actions is not None:
            items, _ = _record(self.row_actions)
            items = items if isinstance(items, (list, tuple)) else [items]
            for item in items:
                if not isinstance(item, (vocab.Button, vocab.Link)):
                    raise ctx.fail(f"`row_actions=` returned {called(item)}.",
                                   "Return a list of `ui.button(...)` / `ui.link(...)`: "
                                   "`row_actions=lambda row: [ui.button(\"Approve\", State.approve, row[\"id\"])]`.")

    def _specs(self, ctx: Build, cols: list[Column], headers: dict[str, str]) -> list[dict]:
        from rfds.components.tables import column as rfds_column

        specs = []
        for c in cols:
            header = c.label or headers.get(c.key) or c.key.replace("_", " ").capitalize()
            options: dict[str, Any] = {"header_id": header}
            if c.currency is not None:
                if _CODE.fullmatch(c.currency):
                    options["ccy"] = c.currency
                else:
                    options["ccy_field"] = c.currency
            render = _renderer(ctx, c)
            if render is not None:
                options["render"] = render
            if c.kind in _DRAWN or c.show is not None:
                options["sortable"] = c.kind in ("status", "tag")
            specs.append(rfds_column(c.key, header, kind=_RFDS_KIND[c.kind or "text"], **options))
        return specs

    def _toolbar(self, ctx: Build, state: Any, cols: list[Column], *, choices: Any) -> rx.Component | None:
        import rfds

        from lex.lex_app.reflex.ui.pages import rows_table
        from rfds.patterns.exports import export_menu

        search = rfds.search_input(value=state.query, on_change=state.set_query.debounce(250),
                                   label=say("Search")) if self.search else None
        filters = None
        if self.filters:
            labels = {c.key: (c.label or c.key.replace("_", " ").capitalize()) for c in cols}
            boxes = []
            for name in self.filters:
                options = state.choices.get(name, []).to(list[dict]) if choices is True else choices[name]
                boxes.append(rfds.select(options=options, value=state.chosen.get(name, rows_table.ALL).to(str),
                                         on_change=lambda value, name=name: state.set_choice(name, value),
                                         label=say(labels[name]), name=f"filter_{name}", width="sm"))
            filters = rfds.cluster(*boxes, gap="2")
        export = export_menu(on_export=state.export, kinds=("csv", "xlsx"), loading=state.exporting) if self.export else None
        if search is None and filters is None and export is None:
            return None
        return rfds.table_toolbar(search=search, filters=filters, export=export, density_toggle=False,
                                  result_count=state.total_rows)

    def _row_actions(self, ctx: Build) -> Any:
        if self.row_actions is None:
            return None

        def draw(row: Any) -> list:
            items = self.row_actions(row)
            items = items if isinstance(items, (list, tuple)) else [items]
            return [item.build(ctx.at("row_actions")) for item in items]

        return draw

    def _empty(self) -> Any:
        import rfds

        if self.empty is None:
            return None
        return rfds.empty_state(kind="no_data", title=say(self.empty),
                                description=say("Nothing has been added here yet."))

    def _render(self, ctx: Build) -> rx.Component:
        from lex.lex_app.reflex.ui import rows as rows_mod

        if _is_model(self.source):
            return self._model(ctx)
        if rows_mod.is_rows(self.source):
            return self._rows(ctx)
        raise ctx.fail(f"`ui.table` got {called(self.source)} as its rows.",
                       "Return the rows from a `@ui.rows` handler on the state: `ui.table(State.rows, ...)`, "
                       "or pass a model: `ui.table(Fund, ...)`.")

    # -- a model ---------------------------------------------------------------------------------
    def _model(self, ctx: Build) -> rx.Component:
        import rfds

        from lex.lex_app.reflex import data

        model = self.source
        cols = self._columns()
        known = {field.name: field for field in model._meta.concrete_fields}
        unknown = [c.key for c in cols if c.key not in known]
        if unknown:
            raise ctx.fail(f"{model.__name__} has no column {', '.join(unknown)}.",
                           f"Its columns are: {', '.join(sorted(known))}. A many-to-many field cannot be a column.")
        for name in dict(self.where or {}):
            if name.split("__")[0] not in known and name.split("__")[0] not in {f.name for f in model._meta.get_fields()}:
                raise ctx.fail(f"`where=` names {name}, which {model.__name__} does not have.",
                               f"Narrow by its fields: {', '.join(sorted(known))}.")
        if self.export or self.totals:
            raise ctx.fail("`export=` and `totals=` work on tables over a `@ui.rows` handler in this version.",
                           "Read the rows in a `@ui.rows` handler, or leave them out.")
        for name in self.filters:
            field = known.get(name)
            if field is not None and not (field.choices or field.get_internal_type() == "BooleanField"):
                raise ctx.fail(f"`filters=` names {name}, whose values are not a fixed set.",
                               "A model table filters on fields with choices, or yes/no fields; search the others.")
        self._check(ctx, cols)
        headers = {c["field"]: c.get("header", "") for c in data.lex_columns(model, [c.key for c in cols])}
        specs = self._specs(ctx, cols, headers)
        key = model._meta.pk.name
        props: dict[str, Any] = {"row_key": key, "row_fields": (key,)}     # the key stays in each row, shown or not
        if self.open is not None:
            if isinstance(self.open, str):
                raise ctx.fail("`open=` names a field, which only a `@ui.rows` table's rows can carry.",
                               "Give a page: `open=fund`.")
            props["row_href"] = links.row_href(ctx, self.open, key)
        empty = self._empty()
        if empty is not None:
            props["empty"] = empty
        actions = self._row_actions(ctx)
        if actions is not None:
            props["row_actions"] = actions
        search_field = "" if not self.search else data._default_search_field(model)
        state = data.table_state(model, columns=specs, search_field=search_field, filters=self.where,
                                 row_fields=(key,))
        from lex.lex_app.reflex.ui.pages import rows_table

        options = {}
        for name in self.filters:
            field = known[name]
            pairs = field.choices or [(True, "Yes"), (False, "No")]
            options[name] = [{"value": rows_table.ALL, "label": "All"}, *({"value": str(v).lower() if isinstance(v, bool) else str(v),
                                                               "label": str(label)} for v, label in pairs)]
        toolbar = self._toolbar(ctx, state, cols, choices=options)
        if toolbar is not None:
            props["toolbar"] = toolbar
        table = rfds.data_table(state=state, columns=specs, caption=str(model._meta.verbose_name_plural).capitalize(),
                                **{k: v for k, v in props.items() if k != "row_fields"})
        return rx.box(table, on_mount=state.open(dict(self.where or {})), width="100%")

    # -- a @ui.rows handler -------------------------------------------------------------------------
    def _rows(self, ctx: Build) -> rx.Component:
        import rfds

        from lex.lex_app.reflex.ui.pages import rows_table

        if self.where is not None:
            raise ctx.fail("`where=` narrows a model; this table's rows come from a `@ui.rows` handler.",
                           "Filter inside the handler.")
        cols = self._columns()
        self._check(ctx, cols)
        specs = self._specs(ctx, cols, {})
        key = self.key or "id"
        needed = tuple(c.key for c in cols)
        fields: set[str] = {key, *needed}
        open_fields = [c.open for c in cols if c.open]
        row_open = ""
        if isinstance(self.open, str):
            row_open = self.open
            open_fields.append(self.open)
        for c in cols:
            if c.currency and not _CODE.fullmatch(c.currency):
                fields.add(c.currency)
            if c.show is not None:
                fields |= _record(c.show)[1]
        if self.row_actions is not None:
            fields |= _record(self.row_actions)[1]
        fields |= set(open_fields)
        money = [c for c in cols if c.kind == "money" and c.currency and not _CODE.fullmatch(c.currency)]
        totals_spec = tuple({**spec, "aggregate": dict(self.totals or {}).get(spec["field"], "none")}
                            for spec in specs) if self.totals else ()
        state = rows_table.state_for(
            self.source, columns=specs, row_fields=sorted(fields),
            key_field=key, needed=needed, open_fields=tuple(open_fields), row_open=row_open,
            tags_fields=tuple(c.key for c in cols if c.kind == "tags"), choice_fields=tuple(self.filters),
            search_fields=tuple(c.key for c in cols if c.kind in ("text", None, "status", "tag") and c.show is None),
            totals_spec=totals_spec, totals_currency=money[0].currency if money and self.totals else "",
            export_columns=tuple((c.key, c.label or c.key.replace("_", " ").capitalize()) for c in cols),
            query_param="q" if (self.search and ctx.slot == "table" and ctx.route) else "",
            entity=self.source.fn.__name__.replace("_", " "))
        rows_table.on_route(ctx.route, state)
        props: dict[str, Any] = {"row_key": "id"}
        if self.open is not None and not isinstance(self.open, str):
            props["row_href"] = links.row_href(ctx, self.open, "id")
        elif row_open:
            props["on_row_click"] = state.go
        empty = self._empty()
        if empty is not None:
            props["empty"] = empty
        actions = self._row_actions(ctx)
        if actions is not None:
            props["row_actions"] = actions
        if self.totals:
            props["totals"] = state.totals_rows
        toolbar = self._toolbar(ctx, state, cols, choices=True)
        if toolbar is not None:
            props["toolbar"] = toolbar
        caption = say(ctx.caption) if ctx.caption else say("Rows")
        table = rfds.data_table(state=state, columns=specs, caption=caption, **props)
        return rx.box(table, on_mount=state.open, on_unmount=state.close, width="100%")


def _is_model(source: Any) -> bool:
    return isinstance(source, type) and hasattr(source, "_meta")


def _renderer(ctx: Build, c: Column) -> Callable[[Any], rx.Component] | None:
    """How lex-app draws a cell RFDS has no kind for: a toned status, tags, a link, or ``show=``."""
    import rfds

    field = c.key
    if c.show is not None:
        return lambda row: c.show(row).build(ctx.at(f"columns[{field}]"))
    if c.open is not None:
        target = c.open
        return lambda row: rx.cond(row[target].to(str) != "", rfds.link(row[field].to(str), href=row[target].to(str)),
                                   rx.el.span(row[field].to(str)))
    if c.kind == "status":
        tones = dict(c.tones)

        def status(row: Any) -> rx.Component:
            word = row[field].to(str)
            badge = rx.match(word, *[(w, rfds.lozenge(say(w), tone=t)) for w, t in tones.items()],
                             rfds.lozenge(word, tone="neutral")) if tones else rfds.lozenge(word, tone="neutral")
            return rx.cond(word != "", badge, rfds.empty_value())

        return status
    if c.kind == "tag":
        return lambda row: rx.cond(row[field].to(str) != "", rfds.tag(row[field].to(str)), rx.fragment())
    if c.kind == "tags":
        return lambda row: rfds.cluster(rx.foreach(row[field].to(list[str]), lambda tag: rfds.tag(tag)), gap="1")
    return None


def table(source: Any, *, columns: Any = (), key: str | None = None, where: dict | None = None, open: Any = None,
          search: bool = False, filters: Any = (), export: bool = False, totals: dict | None = None,
          row_actions: Callable[[Any], Any] | None = None, empty: Any = None, visible: Any = None) -> Table:
    """Rows from a model, or from a ``@ui.rows`` handler, paged on the server and read as the user.

    ``columns`` are field names or ``ui.column(...)``s. ``key`` names the field that tells rows
    apart (a model: its primary key; rows: ``"id"``). ``where=`` narrows a model:
    ``{"fund": FundState.id}``. ``open=`` is a page (each row's key goes into its one argument),
    or the name of the field holding each row's ``ui.address``. ``search=True`` adds a search box;
    ``filters=`` names columns the user can filter on; ``export=True`` adds CSV and Excel export;
    ``totals={"amount": "sum"}`` adds a totals row over all matching rows; ``row_actions`` is a
    function of the row returning buttons. ``empty=`` is the sentence when there are no rows.

    A model table is shown only to users who may read the model; ``visible=True`` shows it to
    everyone, and the rows are still read as the user.
    """
    if isinstance(columns, str):
        raise TypeError(f"ui.table(columns=) takes a list of names: columns=[{columns!r}]")
    if isinstance(filters, str):
        raise TypeError(f"ui.table(filters=) takes a list of column names: filters=[{filters!r}]")
    if totals is not None and not isinstance(totals, dict):
        raise TypeError("ui.table(totals=) takes {column: \"sum\" or \"count\"}: totals={\"amount\": \"sum\"}")
    if where is not None and not isinstance(where, dict):
        raise TypeError("ui.table(where=) takes {field: value}: where={\"fund\": FundState.id}")
    if row_actions is not None and not callable(row_actions):
        raise TypeError("ui.table(row_actions=) takes a function of the row: "
                        "row_actions=lambda row: [ui.button(\"Approve\", State.approve, row[\"id\"])]")
    if key is not None and not (isinstance(key, str) and _FIELD.fullmatch(key)):
        raise ValueError(f"ui.table(key=) names the field that tells rows apart; got {key!r}")
    cols = tuple(columns) if isinstance(columns, (list, tuple)) else (columns,)
    return Table(source, cols, open, empty, visible, key, where, bool(search), tuple(filters), bool(export),
                 totals, row_actions)
