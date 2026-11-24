"""``ui.frame``: how lex-app's frame around every page behaves in one project.

lex-app draws the frame -- the header, the navigation, the breadcrumbs, the no-access notice --
and a project configures it with a few generic parts, once, in its dashboard module beside its
``@ui.page``\\ s. It is not a frame of the project's own: nothing here draws.
"""

from __future__ import annotations

import dataclasses
import sys
from typing import Any


@dataclasses.dataclass(frozen=True)
class Frame:
    """What ``ui.frame(...)`` configured; every part optional."""

    nav: tuple[str, ...] = ()
    module: str = ""


#: The project's frame, set by :func:`frame`; ``None`` until then (lex-app's defaults).
_FRAME: Frame | None = None


def current() -> Frame:
    """The project's frame, or lex-app's defaults when the project configured none."""
    return _FRAME or Frame()


def frame(*, nav: Any = ()) -> None:
    """Configure lex-app's frame for this project: call it once, in the dashboard module.

    ``nav`` is the navigation's areas in the order they are listed: the same words a page names
    its area with (``@ui.page(..., nav="Orders")``). A page whose area is not in the list stops the
    start. Without it, areas come in the order their first page is declared::

        ui.frame(nav=["Orders", "Reports", "Tools"])

    The product name in the header is ``LEX_DASHBOARD_TITLE`` and its badge ``LEX_ENVIRONMENT``: a
    deployment's values. The look is lex-app's. Called again from the same module (a reload), the
    new configuration replaces the old; from another module it is refused.
    """
    global _FRAME
    module = str(sys._getframe(1).f_globals.get("__name__", ""))
    if _FRAME is not None and _FRAME.module != module:
        raise ValueError(f"ui.frame() is already configured, in module {_FRAME.module!r}. A project configures "
                         f"lex-app's frame once, in its dashboard module; move this call ({module!r}) there.")
    _FRAME = Frame(nav=_areas(nav), module=module)


def _areas(nav: Any) -> tuple[str, ...]:
    if isinstance(nav, str):
        raise TypeError(f"ui.frame(nav=) takes the areas as a list, in their order: nav=[{nav!r}].".replace("'", '"'))
    try:
        areas = tuple(nav)
    except TypeError:
        raise TypeError(f"ui.frame(nav=) takes the areas as a list of names, such as nav=[\"Orders\", \"Reports\"]; "
                        f"got {nav!r}.") from None
    for area in areas:
        if not isinstance(area, str) or not area.strip():
            raise TypeError(f"ui.frame(nav=) takes area names, the words pages give as nav=; got {area!r}.")
    twice = sorted({area for area in areas if areas.count(area) > 1})
    if twice:
        raise ValueError(f"ui.frame(nav=) lists {twice[0]!r} twice; list each area once.")
    return areas


def target(where: Any, *, argument: str, mail: bool = False) -> str:
    """The address ``where`` leads to: a page (``@ui.page`` or ``ui.reflex_page``) without route
    arguments, or an outside address; refused with what to write."""
    from lex.lex_app.reflex.ui.pages import links

    info = links.page_info(where)
    if info is not None:
        if info.arguments:
            raise ValueError(f"ui.frame({argument}=) leads to page `{info.name}` ({info.route}), which needs "
                             f"{', '.join(info.arguments)}. Name a page without route arguments.")
        return info.route
    schemes = ("https://", "mailto:") if mail else ("https://",)
    if isinstance(where, str) and where.startswith(schemes) and len(where) > len("mailto:"):
        return where
    also = " or a mailto: address" if mail else ""
    raise ValueError(f"ui.frame({argument}=) takes a page -- a function declared with @ui.page, or "
                     f"ui.reflex_page(\"/route\") -- or an outside address starting with https://{also}; got "
                     f"{where!r}. A route is not written as text.")


__all__ = ["Frame", "current", "frame", "target"]

