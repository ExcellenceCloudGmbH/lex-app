"""Embeddable lex-app widgets for Streamlit pages.

One host iframe per page renders every widget the block declares, so widget
count costs manifest entries rather than React runtimes.
"""

from lex.lex_app.streamlit.widgets.host import WidgetPage, lex_widgets
from lex.lex_app.streamlit.widgets.spec import (
    MANIFEST_VERSION,
    WidgetSpecError,
    build_manifest,
    calculation_spec,
)

__all__ = [
    "MANIFEST_VERSION",
    "WidgetPage",
    "WidgetSpecError",
    "build_manifest",
    "calculation_spec",
    "lex_widgets",
]
