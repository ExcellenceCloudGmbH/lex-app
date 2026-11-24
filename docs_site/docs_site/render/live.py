"""Running a page's python fences: setup (exec) and demos (demo, demo-only), in a module per page."""

import ast
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
        self.slug = slug
        name = "docs_site.pages." + slug.replace("/", "_").replace("-", "_")
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
    if isinstance(value, Kind):
        def page() -> Any:
            return value
        page.__name__ = page.__qualname__ = name.replace("/", "_").replace("-", "_")
        return ui.dashboard(page)()
    if isinstance(value, Part):
        return ui.embed(value, name=name)
    raise TypeError(f"{name}: a demo shows a component, a lex.ui page kind (ui.record, ui.list) or a part "
                    f"(ui.table, ui.section); got {type(value).__name__}")


def demo_card(code: str | None, component: rx.Component) -> rx.Component:
    from docs_site.sandbox.role import switch

    shown = [rx.hstack(rx.text("Viewing as", size="1", color_scheme="gray"), switch(),
                       justify="end", width="100%"),
             rx.box(component, class_name="docs-demo-frame", width="100%")]
    if code is not None:
        shown.insert(0, rx.code_block(code.rstrip("\n"), language="python", can_copy=True))
    return rx.vstack(*shown, spacing="3", align="stretch", class_name="docs-demo")
