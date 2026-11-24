"""API reference pages, generated from lex.ui's exports and their docstrings."""

import inspect
import re

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

# A line that would open a fence or a heading if the docstring were Markdown.
_RISKY = re.compile(r"^ {0,3}(```|~~~|#)", re.MULTILINE)


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


def _docstring(doc: str) -> str:
    """Docstrings are untrusted here: a fence in one could run code or raise in the renderer.

    A docstring with no fence or heading line is shown as ordinary Markdown. Any other is shown
    verbatim inside a `markdown` fence longer than the longest backtick run it contains, so nothing
    inside it can close the fence or open a block.
    """
    if not _RISKY.search(doc):
        return doc
    longest = max((len(run) for run in re.findall(r"`+", doc)), default=0)
    fence = "`" * max(4, longest + 1)
    return f"{fence}markdown\n{doc}\n{fence}"


def page_source(group: str) -> str:
    parts = [f"---\ntitle: {TITLES[group]}\ndescription: lex.ui's {TITLES[group].lower()}, from the code.\n---\n"]
    for name in GROUPS[group]:
        obj = getattr(ui, name)
        doc = inspect.cleandoc(inspect.getdoc(obj) or "No docstring yet.")
        parts.append(f"## ui.{name}\n\n```python\n{_signature(name, obj)}\n```\n\n{_docstring(doc)}\n")
    return "\n".join(parts)
