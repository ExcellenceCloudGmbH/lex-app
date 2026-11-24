"""API reference pages, generated from lex.ui's exports and their docstrings."""

import inspect

from lex import ui

GROUPS: dict[str, tuple[str, ...]] = {
    "pages": ("page", "dashboard", "reflex_page", "chrome", "install", "embed", "record", "list"),
    "parts": ("section", "table", "column", "fact", "text", "each", "status", "custom", "custom_block",
              "button", "link", "switch", "address", "message", "progress", "progress_of"),
    "state": ("State", "action", "job", "load", "rows", "running", "failed", "loaded", "cancel", "refresh"),
    "results": ("toast", "goto", "download", "Refuse", "ApiError", "PageError"),
    "permissions": ("read", "create", "edit", "delete", "signed_in", "permission"),
    "testing": ("testing",),
}
TITLES = {"pages": "Pages and page kinds", "parts": "Parts", "state": "State and handlers",
          "results": "Results and errors", "permissions": "Permissions", "testing": "Testing"}

def undocumented() -> list[str]:
    listed = {n for group in GROUPS.values() for n in group}
    return sorted(set(ui.__all__) - listed)


def _signature(name: str, obj) -> str:
    if inspect.ismodule(obj):
        return f"from lex.ui import {name}"
    try:
        return f"ui.{name}{inspect.signature(obj)}".replace("'", "")
    except (TypeError, ValueError):
        return f"ui.{name}"


def page_source(group: str) -> str:
    parts = [f"---\ntitle: {TITLES[group]}\ndescription: lex.ui's {TITLES[group].lower()}, from the code.\n---\n"]
    for name in GROUPS[group]:
        obj = getattr(ui, name)
        doc = inspect.cleandoc(inspect.getdoc(obj) or "No docstring yet.")
        parts.append(f"## ui.{name}\n\n```python\n{_signature(name, obj)}\n```\n\n{doc}\n")
    return "\n".join(parts)
