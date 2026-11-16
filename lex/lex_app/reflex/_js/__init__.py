"""lex-app's own browser code for Reflex dashboards, shipped as Reflex assets.

Every file here is exposed with ``rx.asset(<bare filename>, shared=True)`` from
this module only: Reflex links shared assets next to the module that declares
them, so files that import each other must be declared from one place. The
declarations run on first use -- at compile time, never on import -- because
``rx.asset`` reads the Reflex configuration, which does not exist yet while
``rxconfig.py`` loads.

Use :func:`asset_import` for a component's ``library`` and :func:`asset_url`
for a script URL. ``lex_rfds.css`` is not an asset: it is inlined into every
page's head (:func:`lex.lex_app.reflex.design.lex_head_components`).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

HERE = Path(__file__).parent

#: Every asset and what it carries.
FILES: dict[str, str] = {
    "lex_host.jsx": "the bridge to lex-app's shell: theme following, sign-in notices",
    "lex_frame.jsx": "lex-app's pages (lex_view) and controls (lex_widgets) framed in a dashboard",
}


@cache
def link_all() -> dict[str, str]:
    """``rx.asset(shared=True)`` for every declared file; ``{filename: AssetPathStr}``."""
    import reflex as rx

    linked = {}
    for name in FILES:
        if not (HERE / name).exists():
            raise FileNotFoundError(f"lex.lex_app.reflex._js: declared asset {name!r} is missing")
        linked[name] = rx.asset(name, shared=True)
    return linked


def asset_import(name: str) -> str:
    """The module path (``$/public/external/...``) a component's ``library`` imports."""
    if name not in FILES:
        raise KeyError(f"lex.lex_app.reflex._js: {name!r} is not declared in FILES")
    return link_all()[name].importable_path


def asset_url(name: str) -> str:
    """The URL a declared file is served at."""
    if name not in FILES:
        raise KeyError(f"lex.lex_app.reflex._js: {name!r} is not declared in FILES")
    return str(link_all()[name])
