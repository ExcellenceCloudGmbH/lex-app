"""The Streamlit surface of lex-app.

Everything a dashboard author needs is importable from here::

    from lex.lex_app.streamlit import lex_view, lex_calculation, lex_widgets

One rule decides which name to reach for, and it is the shape of the call rather
than anything to memorise:

* ``lex_view(path)`` embeds one lex-app **route** -- a table, a form, a detail page;
* ``lex_calculation(...)`` and its siblings embed one lex-app **control**;
* ``lex_widgets()`` embeds **several controls** into a single iframe.

A flat ``lex_*`` function is always exactly one thing. The block form is the
optimisation you reach for once a page has several, and it exists for that
reason alone.
"""

from lex.lex_app.streamlit.embed import STAY, Flow, FlowError, Ref, lex_view, ref
from lex.lex_app.streamlit.widgets import (
    WidgetPage,
    WidgetSpecError,
    lex_calculation,
    lex_calculation_log,
    lex_calculation_log_tree,
    lex_widgets,
)

__all__ = [
    "STAY",
    "Flow",
    "FlowError",
    "Ref",
    "WidgetPage",
    "WidgetSpecError",
    "lex_calculation",
    "lex_calculation_log",
    "lex_calculation_log_tree",
    "lex_view",
    "lex_widgets",
    "ref",
]
