"""``lex_calculation()`` -- one record, rendered by the lex-app frontend.

The React app already knows how to draw a record: which fields a serializer
exposes, how each type is formatted, what the Calculate control looks like and
what it does. This embeds that, for a single record, instead of restating any of
it in Python.

It is a thin wrapper over :func:`lex.lex_app.streamlit.embed.lex_view` -- there
is deliberately no new transport, no new permission path and no new component.
Everything below is URL construction plus defaults chosen for a single record.

**When to use the other one.** :func:`lex.lex_app.streamlit.lex_calculation_streamlit`
renders the same trigger natively in Streamlit: no iframe, no second React
runtime per tile, a Python return value the rest of the page can branch on, and
it flows with the page instead of living in a fixed-height box. It is the better
choice for a *dashboard of tiles*. This one is the better choice when you want
the record itself -- its fields, formatted the way the product formats them --
and want that to keep matching the product without anyone maintaining a second
copy.

**What an iframe costs**, so the choice is made with open eyes. Each embed is a
full document: its own React bundle, its own JS runtime, its own WebSocket
connections, its own auth handshake. Thirteen of these on a page is thirteen
React apps. The frame also authenticates by cookie rather than by bearer token,
which is why ``EmbedXFrameOptionsMiddleware`` exists -- it strips
``X-Frame-Options`` and rewrites the session and CSRF cookies to
``SameSite=None; Secure`` so the browser will send them cross-site. And it
returns nothing to Python: the page cannot branch on what the record's status
became.
"""

from __future__ import annotations

from typing import Dict, Optional, Union

from lex.lex_app.streamlit.embed import lex_view

__all__ = ["lex_calculation", "record_path"]

#: Tall enough for a handful of fields and the Calculate control without an
#: inner scrollbar. An iframe does not size itself to its content -- it is a
#: fixed box, and content taller than the box scrolls inside it -- so this is a
#: guess that authors are expected to correct per page.
DEFAULT_HEIGHT = 420

#: Which React route renders the record.
#:
#: ``show`` is the read-only detail view; ``edit`` is the form. Both are
#: registered for every lex resource (the frontend passes ``list`` *and*
#: ``show`` when it builds each ``Resource``), and both live under the same
#: ``/{model}/{pk}/...`` prefix.
VIEWS = {
    "show": "{model}/{pk}/show",
    "edit": "{model}/{pk}",
    # A list scoped to one record. The fallback worth knowing about: the
    # Calculate control is definitely present in the list -- it is an AG Grid
    # cell renderer, and it is how calculations are triggered in the product
    # today -- so if the detail view turns out not to offer one, this route
    # still does.
    "list": "{model}",
}


def record_path(model: str, pk, view: str = "show") -> str:
    """The frontend path for one record.

    Separate from the rendering so it can be asserted on without a browser, and
    so a caller who wants ``lex_view`` directly can borrow the routing.
    """
    template = VIEWS.get(view)
    if template is None:
        raise ValueError(
            f"Unknown view {view!r}. Expected one of {sorted(VIEWS)}."
        )
    return template.format(model=model, pk=pk)


def lex_calculation(
    model: str,
    pk,
    *,
    serializer: Optional[str] = None,
    view: str = "show",
    height: int = DEFAULT_HEIGHT,
    width: Union[int, str] = "100%",
    hide_toolbar: bool = True,
    hide_actions: bool = True,
    scrolling: bool = True,
    extra_params: Optional[Dict[str, str]] = None,
    base_url: Optional[str] = None,
    key: Optional[str] = None,
) -> Optional[dict]:
    """Render one record -- its fields and its Calculate control -- from lex-app.

    ::

        lex_calculation("calculatenav", pk=1, serializer="dashboard")

    ``serializer`` is the whole point of the signature. It is the same named
    serializer the REST API takes as a path segment
    (``/api/model_entries/<model>/<serializer>/one/<pk>``), so declaring one on
    the model decides which fields appear here -- name, field_1, field_2 -- with
    no frontend change and nothing listed twice. Omitted, the frontend falls
    back to the model's ``default_serializer_name``.

    ``view`` picks the route: ``"show"`` for the read-only detail view,
    ``"edit"`` for the form, ``"list"`` for the table. See :data:`VIEWS`.

    ``hide_toolbar`` and ``hide_actions`` default to ``True`` here and ``False``
    in :func:`lex_view`: a page embedding one record wants the record, not the
    surrounding navigation, and the chrome is a large fraction of a 420px box.

    Returns ``None``. The frame is a separate browsing context, so nothing it
    learns comes back to Python -- a dashboard that needs to branch on the
    record's status wants ``lex_calculation_streamlit`` instead.
    """
    return lex_view(
        record_path(model, pk, view),
        height=height,
        width=width,
        scrolling=scrolling,
        hide_toolbar=hide_toolbar,
        hide_actions=hide_actions,
        serializer=serializer,
        extra_params=extra_params,
        base_url=base_url,
        key=key,
    )
