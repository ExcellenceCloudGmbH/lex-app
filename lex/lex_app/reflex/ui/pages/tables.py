"""ui.table and ui.column: rows, paged on the server."""

from __future__ import annotations

import dataclasses
from typing import Any

import reflex as rx

from lex.lex_app.reflex.ui.pages import links
from lex.lex_app.reflex.ui.pages.parts import Build, Part, say


@dataclasses.dataclass(frozen=True)
class Column:
    key: str
    label: str | None = None
    kind: str | None = None


def column(key: str, *, label: str | None = None, kind: str | None = None) -> Column:
    """A table column that needs more than its name."""
    return Column(key, label, kind)


@dataclasses.dataclass(eq=False)
class Table(Part):
    source: Any                         # a model class
    columns: tuple = ()
    open: Any = None
    empty: Any = None
    visible: Any = None

    def _render(self, ctx: Build) -> rx.Component:
        import rfds

        from lex.lex_app.reflex import data

        model = self.source
        if not hasattr(model, "_meta"):
            raise ctx.fail(f"`ui.table` got {type(model).__name__}, not a model.",
                           "Pass a model class: `ui.table(Fund, columns=[...])`.")
        keys = [c.key if isinstance(c, Column) else str(c) for c in self.columns] or None
        overrides = {c.key: {k: v for k, v in (("header", c.label), ("kind", c.kind)) if v is not None}
                     for c in self.columns if isinstance(c, Column) and (c.label or c.kind)}
        props: dict[str, Any] = {}
        if self.open is not None:
            props["row_href"] = links.row_href(ctx, self.open, "id")
        if self.empty is not None:
            props["empty"] = rfds.empty_state(kind="no_data", title=say(self.empty), description="")
        return data.lex_table(model, columns=data.lex_columns(model, keys, **overrides), **props)


def table(source: Any, *, columns: Any = (), open: Any = None, empty: Any = None, visible: Any = None) -> Table:
    """A model's rows, paged on the server. ``open=`` a page makes each row a link to it."""
    return Table(source, tuple(columns), open, empty, visible)
