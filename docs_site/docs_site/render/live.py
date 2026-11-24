"""Running a page's python fences: setup (exec) and demos (demo, demo-only), in a module per page."""

import ast
import re
import sys
import types
from typing import Any

import reflex as rx

from lex import ui
from lex.lex_app.reflex.ui.pages.kinds import Kind
from lex.lex_app.reflex.ui.pages.parts import Part


class PageModule:
    """The module a page's code runs in: ``docs_site.pages.<slug>``, one per page, kept for the process.

    A page can be rendered more than once (when the app compiles, and again in a test); the module
    already in ``sys.modules`` is reused, so its states are never defined twice under one name.
    """

    def __init__(self, slug: str):
        from docs_site.sandbox.reset import page_module

        self.slug = slug
        name = page_module("/" + slug)
        module = sys.modules.get(name)
        if module is None:
            module = types.ModuleType(name)
            module.__dict__["__builtins__"] = __builtins__
            sys.modules[name] = module
        self.module = module
        self.namespace = module.__dict__

    def run(self, code: str) -> None:
        exec(compile(code, f"<{self.slug}>", "exec"), self.namespace)

    def value(self, code: str) -> Any:
        tree = ast.parse(code, f"<{self.slug}>")
        if not tree.body or not isinstance(tree.body[-1], ast.Expr):
            raise ValueError(f"{self.slug}: a demo's last line must be an expression - the thing to show")
        last = ast.Expression(tree.body.pop().value)
        exec(compile(tree, f"<{self.slug}>", "exec"), self.namespace)
        return eval(compile(last, f"<{self.slug}>", "eval"), self.namespace)


def to_component(value: Any, name: str) -> rx.Component:
    if isinstance(value, rx.Component):
        return value
    # The demo is named the way a reader's own page would be, because lex.ui shows the name as a
    # table's caption: a page kind after its title, a bare part not at all. ``name`` (the page and
    # the demo's number) only goes into an error, so a broken demo still says where it is.
    try:
        if isinstance(value, Kind):
            title = value.title if isinstance(value.title, str) else ""
            def page() -> Any:
                return value
            page.__name__ = page.__qualname__ = re.sub(r"\W+", "_", title.lower()).strip("_") or "demo"
            return ui.dashboard(page)()
        if isinstance(value, Part):
            return ui.embed(value)
    except Exception as error:
        error.add_note(f"in the docs demo {name}")
        raise
    raise TypeError(f"{name}: a demo shows a component, a lex.ui page kind (ui.record, ui.list) or a part "
                    f"(ui.table, ui.section); got {type(value).__name__}")


def demo_card(code: str | None, component: rx.Component) -> rx.Component:
    from docs_site.sandbox import reset
    from docs_site.sandbox.role import switch

    shown = [rx.hstack(reset.button(), rx.spacer(),
                       rx.text("Viewing as", size="1", color_scheme="gray"), switch(),
                       align="center", width="100%", flex_wrap="wrap", row_gap="8px"),
             rx.box(component, class_name="docs-demo-frame", width="100%",
                    border="1px solid var(--gray-a6)", border_radius="var(--radius-3)",
                    padding="16px", background="var(--gray-a2)")]
    if code is not None:
        shown.insert(0, rx.code_block(code.rstrip("\n"), language="python", can_copy=True))
    return rx.vstack(*shown, spacing="3", align="stretch", class_name="docs-demo")
