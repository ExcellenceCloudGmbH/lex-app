"""ui.table and ui.column: rows from a model or a ``@ui.rows`` handler, paged on the server."""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Callable

import reflex as rx
from reflex_base.components.component import field
from reflex_base.constants.compiler import Hooks
from reflex_base.event import EventHandler, no_args_event_spec
from reflex_base.vars.base import Var, VarData
from reflex_components_core.base.fragment import Fragment

from lex.lex_app.reflex.ui.pages import links
from lex.lex_app.reflex.ui.pages.parts import Build, Part, called, say

#: What a column's kind is called in RFDS's column spec; status, tag and tags are drawn by lex-app.
_RFDS_KIND = {"text": "text", "number": "number", "money": "money", "percent": "percent", "date": "date",
              "datetime": "instant", "status": "text", "tag": "text", "tags": "text", "name": "entity", "code": "id"}
#: A column's kind as an exported cell's type (RFDS's semantic types).
_EXPORT_KIND = {"number": "number", "money": "amount", "percent": "ratio", "date": "date", "datetime": "instant"}
KINDS = tuple(_RFDS_KIND)
_DRAWN = ("status", "tag", "tags")
_NUMERIC = ("number", "money", "percent")
_TEXTUAL = (None, "text", "status", "tag", "tags", "name", "code")
_CODE = re.compile(r"[A-Z]{3}")
_COMMON_CODES = {"EUR", "USD", "GBP", "CHF", "JPY", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "CAD", "AUD", "CNY"}
_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: The kinds that set a column's width, to RFDS's field widths: a name gets room, a code stays narrow.
_KIND_WIDTH = {"name": "var(--rfds-size-field-md)", "code": "var(--rfds-size-field-xs)"}
#: What ``totals=`` does with a column: its sum (``totals_of_all=True``: of every row, not the matching ones).
TOTALS = ("sum",)
#: The most rows RFDS lets a table hold in the browser (DA-09): the largest ``page_size=``.
MAX_PAGE = 200
#: The actions RFDS shows in a table's row (``rfds.row_actions``); the others go into the row's menu.
ROW_INLINE = 2


@dataclasses.dataclass(frozen=True)
class Column:
    key: str
    label: str | None = None
    kind: str | None = None
    currency: str | None = None
    tones: tuple = ()
    open: str | None = None
    show: Callable[[Any], Any] | None = None
    #: Set by a page kind, never by ``ui.column()``: the words a status column shows for its values.
    words: tuple = ()


def column(key: str, *, label: str | None = None, kind: str | None = None, currency: str | None = None,
           tones: dict | None = None, open: str | None = None, show: Callable[[Any], Any] | None = None) -> Column:
    """A table column that needs more than its name.

    ``kind`` says what the values are, and each kind is drawn the same way on every page: text,
    number, money, percent, date, datetime; ``status`` (a word with a tone), ``tag`` (one label),
    ``tags`` (a list of labels); ``name`` (a person's or a company's name: given room, RFDS's
    wide field width), ``code`` (a short identifier such as an order number: RFDS's identifier
    type, narrow).

    ``currency`` goes with money: three capitals is a code (``"EUR"``); anything else names the
    row's field that holds the code (``currency="currency"``). ``tones`` goes with status:
    ``{"Open": "info", "Late": "warning"}``. ``open`` names the row's field holding a
    ``ui.address``: the cell becomes a link.

    ``show`` draws a cell no kind describes: a function of the row returning ``ui.text``,
    ``ui.status`` or ``ui.link``. ``row["x"]`` there stands for the value in the browser, not the
    value itself: a Python ``if`` on it cannot work; use ``visible=`` or ``ui.switch``.

    The first column of a table names the row: it stays in place while the table scrolls
    sideways, with nothing to write.
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
    if isinstance(currency, str) and re.fullmatch(r"[a-z]{3}", currency) and currency.upper() in _COMMON_CODES:
        raise ValueError(f"{where}, currency=) got {currency!r}: a code is written in capitals, "
                         f"currency=\"{currency.upper()}\"; a field holding the code is named otherwise.")
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


#: Column kinds whose filter is a date range instead of a dropdown of the column's values.
_DATE_KINDS = ("date", "datetime")


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
    page_size: int = 25
    choose_columns: bool = False
    totals_of_all: bool = False
    #: Set by a page kind, never by ``ui.table()``: where each row of a model table leads (an address
    #: template with ``{<key>}``), and the order of its rows until the user sorts (``"-id"``).
    row_link: str | None = None
    ordering: str = ""

    def build(self, ctx: Build) -> rx.Component:
        # A model table reads through the API as the user; without the grant it could only show an error.
        from lex.lex_app.reflex.ui import guard

        if self.visible is None and _is_model(self.source):
            from lex.lex_app.reflex.ui.pages import rows_table

            copy = dataclasses.replace(self, visible=guard.read(self.source))
            component = copy.build(ctx)
            rows_table._CACHE_BY_TABLE[self] = rows_table._CACHE_BY_TABLE[copy]   # the table as written
            return component
        return super().build(ctx)

    # -- shared checks -------------------------------------------------------------------------------
    def _columns(self) -> list[Column]:
        return [c if isinstance(c, Column) else Column(str(c)) for c in self.columns]

    def _check(self, ctx: Build, cols: list[Column], first_kind: str | None = None) -> None:
        keys = [c.key for c in cols]
        if not cols:
            raise ctx.fail("the table has no columns.", "Name them: `columns=[\"name\", ui.column(\"amount\", kind=\"money\")]`.")
        first = cols[0]
        if first.kind in _DRAWN or first.show is not None or first.open is not None or first_kind in ("status",):
            what = ("a `show=` column" if first.show else "an `open=` column" if first.open
                    else f"a `{first.kind or first_kind}` column")
            raise ctx.fail(f"the first column, `{first.key}`, is {what}.",
                           "The first column names the row and is the row's link, so it is shown as plain text. "
                           "Put a plain column first, such as the name.")
        unknown = [name for name in self.filters if name not in keys]
        if unknown:
            raise ctx.fail(f"`filters=` names {', '.join(unknown)}, which is not a column.",
                           f"Filter on columns of the table: {', '.join(keys)}.")
        for name, how in dict(self.totals or {}).items():
            col = next((c for c in cols if c.key == name), None)
            if col is None:
                raise ctx.fail(f"`totals=` names {name}, which is not a column.",
                               f"Total columns of the table: {', '.join(keys)}.")
            if how == "sum of all rows":
                raise ctx.fail(f"`totals=` asks for the sum of all rows of {name}.",
                               f"Whether the totals cover every row is one choice for the table: write "
                               f"`totals={{\"{name}\": \"sum\"}}, totals_of_all=True`.")
            if how not in TOTALS:
                raise ctx.fail(f"`totals=` asks for {how!r} of {name}.",
                               "A total is the \"sum\" of a number, money or percent column, of the rows the "
                               "search and filters leave: `totals={\"amount\": \"sum\"}`; add `totals_of_all=True` "
                               "to sum every row instead.")
            if col.kind not in _NUMERIC:
                raise ctx.fail(f"`totals=` sums {name}, which is not a number.",
                               "Give it kind=\"number\", \"money\" or \"percent\".")
        if self.totals_of_all and not self.totals:
            raise ctx.fail("`totals_of_all=True` and no `totals=`.",
                           "Name the columns to total: `totals={\"amount\": \"sum\"}, totals_of_all=True`.")
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

    def _specs(self, ctx: Build, cols: list[Column], base: dict[str, dict]) -> list[dict]:
        from rfds.components.tables import column as rfds_column

        specs = []
        for index, c in enumerate(cols):
            given = base.get(c.key)
            layout: dict[str, Any] = {}
            if c.kind in _KIND_WIDTH:
                layout["width"] = _KIND_WIDTH[c.kind]
            if index == 0:                      # it names the row: in place while the table scrolls sideways
                layout["sticky"] = "start"
            if given is not None and c.kind is None and c.show is None and c.open is None and c.currency is None:
                spec = dict(given)                       # a model's field: its own kind and label
                if c.label is not None:
                    spec.update(header=c.label, header_id=c.label)
                specs.append({**spec, **layout})
                continue
            header = c.label or (given or {}).get("header") or c.key.replace("_", " ").capitalize()
            options: dict[str, Any] = {"header_id": header, **layout}
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
            field = (given or {}).get("field", c.key)       # a model's foreign key: its related record's name
            specs.append(rfds_column(field, header, kind=_RFDS_KIND[c.kind or "text"], **options))
        return specs

    def _toolbar(self, ctx: Build, state: Any, cols: list[Column], specs: list[dict], *,
                 choices: Any, dated: set | None = None) -> rx.Component | None:
        import rfds

        from lex.lex_app.reflex.ui.pages import rows_table

        search = rfds.search_input(value=state.query, on_change=state.set_query.debounce(250),
                                   label=say("Search")) if self.search else None
        filters = None
        if self.filters:
            labels = {c.key: (c.label or c.key.replace("_", " ").capitalize()) for c in cols}
            boxes = []
            dated = dated if dated is not None else {c.key for c in cols if c.kind in _DATE_KINDS}
            for name in self.filters:
                label = say(labels[name])
                if name in dated:           # a date column is filtered by a range of days, not a list of every day
                    boxes.append(rfds.date_range_picker(
                        value=state.dates.get(name, {"start": "", "end": ""}).to(dict),
                        on_change=lambda value, name=name: state.set_dates(name, value),
                        label=label, name=f"filter_{name}", width="sm", recent_key=None))
                    continue
                options = state.choices.get(name, []).to(list[dict]) if choices is True else choices[name]
                boxes.append(rfds.select(options=options, value=state.chosen.get(name, rows_table.ALL).to(str),
                                         on_change=lambda value, name=name: state.set_choice(name, value),
                                         label=label, name=f"filter_{name}", width="sm"))
            filters = rfds.cluster(*boxes, gap="2")
        export = _export_split(state) if self.export else None
        chooser = None
        if self.choose_columns:
            chooser = rfds.column_chooser(columns=specs, visible=state.view_columns, on_change=state.set_view_columns,
                                          order=state.column_order, on_reorder=state.set_column_order,
                                          widths=state.column_widths, on_width_change=state.set_column_widths)
        if search is None and filters is None and export is None and chooser is None:
            return None
        return rfds.table_toolbar(search=search, filters=filters, export=export, column_chooser=chooser,
                                  density_toggle=False, result_count=state.total_rows)

    def _paging(self, state: Any) -> dict[str, Any]:
        """The data table's paging and column choice.

        RFDS's pagination, with its rows-per-page choice, only while the rows do not fit one page:
        a table whose rows all fit shows no pager and no choice that would change nothing.
        """
        import rfds

        props: dict[str, Any] = {}
        if self.choose_columns:
            props.update(visible_columns=state.view_columns, column_order=state.column_order,
                         column_widths=state.column_widths)
        props["pager"] = False
        props["footer"] = rx.cond(state.total_rows > state.page_size, rfds.pagination(
            page=state.page, page_count=state.page_count, on_page_change=state.set_page,
            page_size=state.page_size, page_size_options=tuple(state.page_size_options),
            on_page_size_change=state.set_page_size, total=state.total_rows))
        return props

    def _row_actions(self, ctx: Build) -> Any:
        if self.row_actions is None:
            return None

        from lex.lex_app.reflex.ui.pages import vocab

        at = ctx.at("row_actions", "row")

        def draw(row: Any) -> list:
            """A row's actions as RFDS's ``row_actions`` takes them: at most two small buttons in the row, the
            rest -- and every dangerous one -- in the row's "More actions" menu, a danger entry there."""
            items = self.row_actions(row)
            items = items if isinstance(items, (list, tuple)) else [items]
            inline: list = []
            menu: list = []
            for item in items:
                dangerous = isinstance(item, vocab.Button) and item.tone == "danger"
                if dangerous or len(inline) == ROW_INLINE:
                    menu.append(item.menu_entry(at))
                else:
                    inline.append(item.build(at))
            return [*inline, *menu]

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
        from lex.lex_app.reflex.ui.pages import rows_table

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
        if self.export or self.totals or self.totals_of_all:
            what = "export=" if self.export else "totals="
            raise ctx.fail(f"a model table cannot export or total (`{what}`): it reads one page at a time.",
                           f"Read every row in a `@ui.rows` handler and table that: `@ui.rows def "
                           f"{model._meta.model_name}s(self): return self.api.all({model.__name__}, filters={{...}})`, "
                           f"then `ui.table(State.{model._meta.model_name}s, columns=[...], {what}...)`.")
        linked = [c.key for c in cols if c.open is not None]
        if linked:
            raise ctx.fail(f"column `{linked[0]}` has `open=`, which only a `@ui.rows` table's rows can carry.",
                           "A model's field is data and never becomes a link. Read the rows in a `@ui.rows` "
                           "handler that builds each address with `ui.address(...)`.")
        dated: dict[str, str] = {}
        for name in self.filters:
            field = known.get(name)
            kind = field.get_internal_type() if field is not None else ""
            if kind in ("DateField", "DateTimeField"):        # a range of days, read as the API's lookups
                dated[name] = f"{name}__date" if kind == "DateTimeField" else name
                continue
            if field is not None and not (field.choices or kind == "BooleanField"):
                raise ctx.fail(f"`filters=` names {name}, whose values are not a fixed set.",
                               "A model table filters on fields with choices, yes/no fields, or dates (a range of "
                               "days); search the others.")
        # By the name the author wrote: a foreign key's spec shows the related record's name (`<fk>__short_description`).
        base = {c.key: dict(spec) for c, spec in zip(cols, data.lex_columns(model, [c.key for c in cols]))}
        self._check(ctx, cols, first_kind=base.get(cols[0].key, {}).get("kind") if cols else None)
        specs = self._specs(ctx, cols, base)
        key = model._meta.pk.name
        props: dict[str, Any] = {"row_key": key, "row_fields": (key,)}     # the key stays in each row, shown or not
        if self.open is not None:
            if isinstance(self.open, str):
                raise ctx.fail("`open=` names a field, which only a `@ui.rows` table's rows can carry.",
                               "Give a page: `open=fund`.")
            props["row_href"] = links.row_href(ctx, self.open, key)
        if self.row_link is not None:
            props["row_href"] = self.row_link
        empty = self._empty()
        if empty is not None:
            props["empty"] = empty
        actions = self._row_actions(ctx)
        if actions is not None:
            props["row_actions"] = actions
        texts = [c.key for c in cols if c.kind in (None, "text", "name", "code") and c.show is None
                 and known[c.key].get_internal_type() in ("CharField", "TextField") and not known[c.key].choices]
        if self.search and not texts:
            raise ctx.fail("`search=True`, and the table has no text column to search.",
                           "Add a text column, or leave search out.")
        # A shown column (the browser never searches a hidden one); the model's own before lex-app's created_by.
        inherited = data._framework_fields(model)
        texts = [name for name in texts if name not in inherited] + [name for name in texts if name in inherited]
        search_field = texts[0] if self.search else ""
        used: set[str] = {key}
        for c in cols:
            if c.show is not None:
                used |= _record(c.show)[1]
        if self.row_actions is not None:
            used |= _record(self.row_actions)[1]
        state = data.table_state(model, columns=specs, search_field=search_field, filters=self.where,
                                 page_size=self.page_size,
                                 row_fields=tuple(sorted(used)),
                                 choice_fields=tuple(name for name in self.filters if name not in dated),
                                 scope_fields=tuple(dict(self.where or {})), ordering=self.ordering,
                                 not_stored=True, date_lookups=dated, requires=tuple(ctx.requires or ()))
        rows_table._CACHE_BY_TABLE[self] = state
        rows_table.shown_on(ctx.route, state)
        options = {}
        for name in self.filters:
            if name in dated:
                continue
            field = known[name]
            pairs = field.choices or [(True, "Yes"), (False, "No")]
            options[name] = [{"value": rows_table.ALL, "label": "All"},
                             *({"value": str(v).lower() if isinstance(v, bool) else str(v), "label": str(label)}
                               for v, label in pairs if not isinstance(label, (list, tuple)))]
        toolbar = self._toolbar(ctx, state, cols, specs, choices=options, dated=set(dated))
        if toolbar is not None:
            props["toolbar"] = toolbar
        props.update(self._paging(state))
        caption, shown_caption = _caption(ctx, str(model._meta.verbose_name_plural).capitalize())
        table = rfds.data_table(state=state, columns=specs, caption=caption,
                                **{k: v for k, v in props.items() if k != "row_fields"})
        if not shown_caption:
            _caption_unseen(table)
        # Keyed by its where= values: when one of them changes (another record of the same page), the
        # table is a new one to React, so it appears again and reads with the new values.
        values = [v for v in dict(self.where or {}).values() if isinstance(v, rx.Var)]
        extra: dict[str, Any] = {}
        if values:
            joined = values[0].to(str)
            for v in values[1:]:
                joined = joined + "|" + v.to(str)
            extra = {"key": joined}
        return rx.box(table, restorer(state), headers_level(), on_mount=state.open(dict(self.where or {})), width="100%",
                      **extra)

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
        if key != "id" and "id" in [c.key for c in cols]:
            raise ctx.fail("a column is called `id`, and the table's key is another field.",
                           "Rename the column's field, or leave `key=` out so `id` is the key.")
        needed = tuple([c.key for c in cols] + [c.currency for c in cols if c.currency and not _CODE.fullmatch(c.currency)])
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
        kinds_by_key = {c.key: c.kind for c in cols}
        totals_spec = tuple({**spec, "aggregate": "sum" if spec["field"] in dict(self.totals or {}) else "none"}
                            for spec in specs) if self.totals else ()
        state = rows_table.state_for(
            self.source, columns=specs, row_fields=sorted(fields),
            key_field=key, needed=needed, open_fields=tuple(open_fields), row_open=row_open,
            tags_fields=tuple(c.key for c in cols if c.kind == "tags"),
            choice_fields=tuple(name for name in self.filters if kinds_by_key.get(name) not in _DATE_KINDS),
            date_fields=tuple(name for name in self.filters if kinds_by_key.get(name) in _DATE_KINDS),
            search_fields=tuple(c.key for c in cols if c.kind in ("text", None, "status", "tag", "name", "code")
                                and c.show is None),
            totals_spec=totals_spec, totals_currency=money[0].currency if money and self.totals else "",
            totals_of_all=bool(self.totals_of_all),
            page_size=self.page_size,
            export_columns=_export_columns(cols),
            export_kinds={label: _EXPORT_KIND[kinds_by_key[field]] for field, label in _export_columns(cols)
                          if kinds_by_key.get(field) in _EXPORT_KIND},
            query_param="q" if (self.search and ctx.page_search and ctx.route) else "",
            searchable=bool(self.search), exportable=bool(self.export),
            entity=self.source.fn.__name__.replace("_", " "), requires=tuple(ctx.requires or ()))
        rows_table._CACHE_BY_TABLE[self] = state
        if not ctx.under_load:          # under a load the table appears again after it, and reads then
            rows_table.on_route(ctx.route, state)
        rows_table.shown_on(ctx.route, state)
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
        toolbar = self._toolbar(ctx, state, cols, specs, choices=True)
        if toolbar is not None:
            props["toolbar"] = toolbar
        props.update(self._paging(state))
        caption, shown_caption = _caption(ctx, say(_handler_words(self.source)))
        table = rfds.data_table(state=state, columns=specs, caption=caption, **props)
        if not shown_caption:
            _caption_unseen(table)
        from lex.lex_app.reflex.ui.pages.kinds import table_anchor

        return rx.box(table, restorer(state), headers_level(), on_mount=state.open, on_unmount=state.close,
                      width="100%", id=table_anchor(self.source))          # ui.scroll_to(State.rows) brings it into view


#: RFDS sets a header cell's sort button inline, on the cell's text line; a number column reverses its button and
#: isolates its digits, which lifts that line - its label stood 6 px above the text columns' labels.
HEADERS_LEVEL = "th .rfds-sort-button { vertical-align: bottom; }"


def headers_level() -> rx.Component:
    """The rule that sets every header label on one line, carried by the table itself so it holds in any app."""
    import reflex as rx

    return rx.el.style(HEADERS_LEVEL)


def restorer(state: Any) -> rx.Component:
    """What makes a table read its rows again when the stored state left them out (``RowsNotStored``).

    Reflex sends the whole state again after a reconnect (``hydrate``) -- with the rows the store left
    out -- and mounts nothing anew, so a mount cannot tell. Reflex's ``is_hydrated`` turns off when the
    page is hydrated and on once its loads are done: each time it turns on, this sends the table's
    ``restore``, on every page that shows the table -- a ``@ui.page``, a ``@ui.dashboard`` or ``ui.embed``
    in a raw page alike. ``restore`` reads only when the rows were left out.
    """
    return _Rehydrated.create(on_hydrated=state.restore)


class _Rehydrated(Fragment):
    """Sends ``on_hydrated`` each time the page has been hydrated: its first load, and every reconnect."""

    on_hydrated: EventHandler[no_args_event_spec] = field(doc="The page has been hydrated (again).")

    @classmethod
    def create(cls, **props: Any) -> Any:
        from reflex_base.components.memoize_helpers import get_memoized_event_triggers

        made = super().create(**props)
        memo = get_memoized_event_triggers(made)
        if memo:
            made.event_triggers = {**made.event_triggers, **memo}
        return made

    def _exclude_props(self) -> list[str]:
        return [*super()._exclude_props(), *self.event_triggers.keys()]

    def add_hooks(self) -> list:
        trigger = self.event_triggers.get("on_hydrated")
        if trigger is None:
            return []
        hydrated = rx.State.is_hydrated
        return [Var(f"useEffect(() => {{ if (!{hydrated}) return; ({Var.create(trigger)})(); }}, [{hydrated}]);",
                    _var_data=VarData.merge(hydrated._get_all_var_data(),
                                            VarData(position=Hooks.HookPosition.POST_TRIGGER)))]


def _export_split(state: Any) -> rx.Component:
    """The table's Export: RFDS's split button - "Export" (CSV) and the menu of every format - as one control.

    Built from the same RFDS parts, words and marks as ``rfds.export_menu`` (an attached ``button_group``,
    ``data-slot="rfds-export-menu"``), with one difference: the menu's half is the same secondary button
    as "Export". RFDS's own leaves it a bare ghost icon pressed against the filled "Export", which reads
    as two buttons overlapping rather than one.
    """
    from rfds.components.actions import button, button_group, icon_button, menu, menu_item
    from rfds.i18n import t

    names = {"csv": t("exports.kind.csv", "CSV"), "xlsx": t("exports.kind.xlsx", "Excel (XLSX)")}
    busy = state.exporting
    trigger = icon_button("chevron_down", label=t("exports.more_formats", "More export formats"), intent="secondary",
                          disabled=busy, tooltip=False, class_name="rfds-split-button-trigger")
    group = button_group(
        button(t("exports.export", "Export"), icon="export", loading=busy, disabled=busy,
               on_click=state.export("csv")),
        menu(trigger, *(menu_item(label, on_select=state.export(kind)) for kind, label in names.items()), align="end"),
        attached=True, class_name="rfds-split-button rfds-export-menu")
    group.custom_attrs["data-slot"] = "rfds-export-menu"
    return group


def _caption(ctx: Build, made_up: Any) -> tuple[Any, bool]:
    """The words RFDS's caption names the table by, and whether the caption is shown.

    RFDS's table must have a caption: it names the table and its scroll region for a screen reader. It
    is the title of the table's section or page, or the name ``ui.embed`` was given. Shown, it is the
    only heading the table has; where a heading on the page already says it (``ctx.headed``) it is kept
    for screen readers only, never shown twice. Where the author wrote no words for the table,
    ``made_up`` names it -- the model's plural name, or the ``@ui.rows`` handler's -- and is never shown.
    """
    if isinstance(ctx.caption, rx.Var) or ctx.caption:
        return say(ctx.caption), not ctx.headed
    return made_up, False


def _caption_unseen(table: rx.Component) -> None:
    """``table``'s caption (and its narrow card view's) for screen readers only: RFDS's ``rfds-sr-only``.

    RFDS's own patterns caption a table under a heading that way. ``rfds.data_table`` takes no word for
    it, so its caption is found by its class; when RFDS builds it otherwise this stops with a sentence,
    never silently showing the caption twice.
    """
    found: list = []

    def walk(node: Any) -> None:
        names = getattr(node, "class_name", None)
        if isinstance(names, str) and {"rfds-data-table-caption", "rfds-dt-cards-caption"} & set(names.split()):
            node.class_name = f"{names} rfds-sr-only"
            found.append(node)
        for child in getattr(node, "children", None) or ():
            walk(child)

    walk(table)
    if len(found) != 2:
        raise RuntimeError(f"lex.ui: RFDS's data table is not built as lex.ui reads it ({len(found)} captions found, "
                           "2 expected), so its caption cannot be kept for screen readers only. lex.ui must be "
                           "updated for this version of RFDS.")


def _handler_words(handler: Any) -> str:
    """What a ``@ui.rows`` handler's rows are, in words: its state's name and its own (``FundState.holdings``:
    "Fund holdings"; a handler called ``rows`` is its state's: ``Commitments.rows``, "Commitments")."""
    state = re.sub(r"State$", "", handler.state.__name__) or handler.state.__name__
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", state).split()
    if handler.fn.__name__ != "rows":
        words += handler.fn.__name__.split("_")
    return " ".join(words).lower().capitalize()


def _export_columns(cols: list[Column]) -> tuple[tuple[str, str], ...]:
    """The exported columns: each column's field and label, and a money column's currency field."""
    out: list[tuple[str, str]] = []
    for c in cols:
        out.append((c.key, c.label or c.key.replace("_", " ").capitalize()))
        if c.currency and not _CODE.fullmatch(c.currency) and c.currency not in [k for k, _ in out] \
                and c.currency not in [col.key for col in cols]:
            out.append((c.currency, "Currency"))
    return tuple(out)


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
        tones, words = dict(c.tones), dict(c.words)

        def status(row: Any) -> rx.Component:
            word = row[field].to(str)
            badge = rx.match(word, *[(w, rfds.lozenge(say(words.get(w, w)), tone=t)) for w, t in tones.items()],
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
          row_actions: Callable[[Any], Any] | None = None, empty: Any = None, visible: Any = None,
          page_size: int = 25, choose_columns: bool = False, totals_of_all: bool = False) -> Table:
    """Rows from a model, or from a ``@ui.rows`` handler, paged on the server and read as the user.

    Where the rows come from:
      ``ui.table(Holding, where={"fund": FundState.id}, ...)`` -- a model, read through lex-app's
      API. ``where=`` chooses which rows to show; who may see them is decided by the model's
      ``permission_list`` / ``permission_read``, never by ``where=``.
      ``ui.table(FundState.holdings, ...)`` -- a ``@ui.rows`` handler's list of dicts.

    What the user can do:
      ``search=True`` a search box; ``filters=["kind", "created"]`` a filter per column - a dropdown of
      its values, or a range of days for a ``date`` or ``datetime`` column; ``export=True``
      CSV and Excel of every matching row; ``open=`` a page (the row's key goes into its one
      argument, a real link), or the name of a field holding each row's ``ui.address`` (the row is
      a button that opens it); ``row_actions=lambda row: [ui.button(...)]``; ``choose_columns=True``
      RFDS's "Columns" menu, where the user hides, orders and sizes the columns (a width typed in;
      the first column always shows and stays first; kept until the tab is closed).

    What lex-app does by itself:
      25 rows a page (``page_size=50`` another number, up to 200, RFDS's limit for a table in the
      browser: ``page_size=200`` for every row of a file in one scroll), a pager with a choice of
      rows per page only while the rows do not fit one page, sorting by column, loading rows, the error in place, ``empty=`` when there are no
      rows, "No rows match" with a way to clear the search; ``totals={"amount": "sum"}`` adds a
      totals row over every matching row (``totals_of_all=True``: over every row, whatever the
      search and filters leave), one per currency; while a search or filter is on, its label says
      which ("Total of matching rows", "Total of all rows").

    ``columns`` are field names or ``ui.column(...)``s; the first names the row and is plain text.
    ``key`` names the field that tells rows apart (a model: its primary key; rows: ``"id"``). Only
    the fields the table uses reach the browser. A model table is shown only to users who may read
    the model; ``visible=True`` shows it to everyone, and the rows are still read as the user.
    """
    if isinstance(columns, str):
        raise TypeError(f"ui.table(columns=) takes a list of names: columns=[{columns!r}]")
    if isinstance(filters, str):
        raise TypeError(f"ui.table(filters=) takes a list of column names: filters=[{filters!r}]")
    if totals is not None and not isinstance(totals, dict):
        raise TypeError("ui.table(totals=) takes {column: \"sum\"}: totals={\"amount\": \"sum\"}")
    if where is not None and not isinstance(where, dict):
        raise TypeError("ui.table(where=) takes {field: value}: where={\"fund\": FundState.id}")
    if row_actions is not None and not callable(row_actions):
        raise TypeError("ui.table(row_actions=) takes a function of the row: "
                        "row_actions=lambda row: [ui.button(\"Approve\", State.approve, row[\"id\"])]")
    if key is not None and not (isinstance(key, str) and _FIELD.fullmatch(key)):
        raise ValueError(f"ui.table(key=) names the field that tells rows apart; got {key!r}")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= MAX_PAGE:
        raise ValueError(f"ui.table(page_size=) takes a number of rows from 1 to {MAX_PAGE}; got {page_size!r}. For "
                         f"every row on one page write page_size={MAX_PAGE}, RFDS's limit for a table in the browser.")
    if not isinstance(totals_of_all, bool):
        raise TypeError(f"ui.table(totals_of_all=) takes True or False; got {totals_of_all!r}. "
                        "`totals_of_all=True` sums every row, whatever the search and filters leave.")
    cols = tuple(columns) if isinstance(columns, (list, tuple)) else (columns,)
    return Table(source, cols, open, empty, visible, key, where, bool(search), tuple(filters), bool(export),
                 totals, row_actions, page_size, bool(choose_columns), totals_of_all)
