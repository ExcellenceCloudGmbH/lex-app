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


def demo_card(code: str | None, component: rx.Component, *, title: str | None = None) -> rx.Component:
    """A live example: a toolbar (Preview / Code on the left; "Viewing as" Editor/Viewer and Reset on
    the right) over the running example. ``code`` is None for a ``demo-only`` fence: no Code tab.

    The tabs are client-side, and both panes stay mounted (an inactive pane is only hidden), so
    switching to the code and back neither reloads the example nor loses what the reader did in it.
    """
    from docs_site.render.blocks import highlighted
    from docs_site.sandbox import reset
    from docs_site.sandbox.role import switch

    controls = rx.el.div(rx.el.span("Viewing as", class_name="docs-demo-as", custom_attrs={"aria-hidden": "true"}),
                         switch(), reset.button(), class_name="docs-demo-controls")
    preview = rx.box(component, class_name="docs-demo-frame")
    if code is None:
        bar = rx.el.div(rx.el.span("Preview", class_name="docs-demo-tab docs-demo-tab-static"), controls,
                        class_name="docs-demo-bar")
        return rx.el.section(bar, preview, class_name="docs-demo", aria_label="Live example")
    source = rx.box(highlighted(code.rstrip("\n"), "python"), class_name="docs-demo-code")
    tabs = rx.tabs.list(rx.tabs.trigger("Preview", value="preview", class_name="docs-demo-tab"),
                        rx.tabs.trigger(title or "Code", value="code", class_name="docs-demo-tab"),
                        class_name="docs-demo-tabs", aria_label="Example view")
    return rx.el.section(
        rx.tabs.root(
            rx.el.div(tabs, controls, class_name="docs-demo-bar"),
            rx.tabs.content(preview, value="preview", force_mount=True, class_name="docs-demo-pane"),
            rx.tabs.content(source, value="code", force_mount=True, class_name="docs-demo-pane"),
            default_value="preview",
        ),
        class_name="docs-demo", aria_label="Live example",
    )
