"""Public surface for the built-in Streamlit widgets.

``lex.lex_app.streamlit.embed`` stays importable and keeps exporting the same
objects: it is a documented path and dashboards are already written against it.
This package only adds a shorter one alongside it.
"""

from lex.lex_app.streamlit.calculation import lex_calculation
from lex.lex_app.streamlit.embed import Flow, lex_view

__all__ = ["Flow", "lex_calculation", "lex_view"]
