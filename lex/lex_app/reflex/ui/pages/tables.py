"""ui.table and ui.column: rows, paged on the server."""

from __future__ import annotations

import dataclasses
import re
from typing import Any

import reflex as rx

from lex.lex_app.reflex.ui.pages import links
from lex.lex_app.reflex.ui.pages.parts import Build, Part, say


#: What a column's kind is called in RFDS's column spec.
_RFDS_KIND = {"text": "text", "number": "number", "money": "money", "percent": "percent", "datetime": "instant"}


@dataclasses.dataclass(frozen=True)
class Column:
    key: str
    label: str | None = None
    kind: str | None = None
    currency: str | None = None


def column(key: str, *, label: str | None = None, kind: str | None = None, currency: str | None = None) -> Column:
    """A table column that needs more than its name: its own label, or what its values are.

    ``kind`` is one of the words ``ui.fact`` takes; ``currency`` (a code such as ``"EUR"``) goes
    with ``kind="money"``.
    """
    if kind is not None and kind not in _RFDS_KIND:
        raise ValueError(f"ui.column({key!r}, kind=) must be one of {', '.join(_RFDS_KIND)}; got {kind!r}")
    if label is not None and not (isinstance(label, str) and label):
        raise ValueError(f"ui.column({key!r}, label=) takes a sentence; got {label!r}")
    if currency is not None and kind != "money":
        raise ValueError(f"ui.column({key!r}, currency=) goes with kind=\"money\"")
    if currency is not None and not (isinstance(currency, str) and re.fullmatch(r"[A-Za-z]{3}", currency)):
        raise ValueError(f"ui.column({key!r}, currency=) takes a three-letter code such as \"EUR\"; got {currency!r}")
    return Column(key, label, kind, currency)


@dataclasses.dataclass(eq=False)
class Table(Part):
    source: Any                         # a model class
    columns: tuple = ()
    open: Any = None
    empty: Any = None
    visible: Any = None

    def build(self, ctx: Build) -> rx.Component:
        # A table reads through the API as the user; without the grant it could only show an error.
        from lex.lex_app.reflex.ui import guard

        if self.visible is None and isinstance(self.source, type) and hasattr(self.source, "_meta"):
            return dataclasses.replace(self, visible=guard.read(self.source)).build(ctx)
        return super().build(ctx)

    def _render(self, ctx: Build) -> rx.Component:
        import rfds

        from lex.lex_app.reflex import data

        model = self.source
        if not (isinstance(model, type) and hasattr(model, "_meta")):
            raise ctx.fail("`ui.table` takes a model class and reads its rows from the server; it cannot show rows "
                           "you hold yourself (a list, or a state value) yet.",
                           "Pass a model: `ui.table(Fund, columns=[...])`.")
        known = {field.name for field in model._meta.concrete_fields}
        keys = [c.key if isinstance(c, Column) else str(c) for c in self.columns]
        unknown = [key for key in keys if key not in known]
        if unknown:
            raise ctx.fail(f"{model.__name__} has no column {', '.join(unknown)}.",
                           f"Its columns are: {', '.join(sorted(known))}. A many-to-many field cannot be a column.")
        overrides: dict[str, dict] = {}
        for c in self.columns:
            if not isinstance(c, Column):
                continue
            options: dict[str, Any] = {}
            if c.label is not None:
                options.update(header=c.label, header_id=c.label)      # the label is the message, as everywhere
            if c.kind is not None:
                options["kind"] = _RFDS_KIND[c.kind]
            if c.currency is not None:
                options["ccy"] = c.currency
            if options:
                overrides[c.key] = options
        key = model._meta.pk.name
        props: dict[str, Any] = {"row_key": key, "row_fields": (key,)}     # the key stays in each row, shown or not
        if self.open is not None:
            props["row_href"] = links.row_href(ctx, self.open, key)
        if self.empty is not None:
            props["empty"] = rfds.empty_state(kind="no_data", title=say(self.empty),
                                              description=say("Nothing has been added here yet."))
        return data.lex_table(model, columns=data.lex_columns(model, keys or None, **overrides), **props)


def table(source: Any, *, columns: Any = (), open: Any = None, empty: Any = None, visible: Any = None) -> Table:
    """A model's rows, paged on the server and read as the user.

    ``columns`` names the fields to show (``ui.column`` for one that needs its own label or kind);
    ``open=`` a page makes each row a link to it; ``empty=`` is the sentence shown when the model
    has no rows at all. The table is shown only to users who may read the model; ``visible=True``
    shows it to everyone, and the rows are still read as the user.
    """
    if isinstance(columns, str):
        raise TypeError(f"ui.table(columns=) takes a list of names: columns=[{columns!r}]")
    return Table(source, tuple(columns) if isinstance(columns, (list, tuple)) else (columns,), open, empty, visible)
