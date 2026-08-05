"""Public surface for the built-in Streamlit widgets.

Two ways to put one calculation on a Streamlit page, and the choice is a real
one rather than a legacy split:

* :func:`lex_calculation` embeds the lex-app frontend's own record view, so the
  fields shown and the way they are formatted are the product's, and stay the
  product's, with no second copy to maintain. Pass a named ``serializer`` to
  choose which fields appear.
* :func:`lex_calculation_streamlit` renders the trigger natively -- no iframe,
  no React runtime per tile, and it returns the status envelope so the rest of
  the page can branch on it. This is the one for a dashboard of many tiles.

``lex.lex_app.streamlit.embed`` stays importable and keeps exporting the same
objects: it is a documented path and dashboards are already written against it.
This package only adds shorter ones alongside it.
"""

from lex.lex_app.streamlit.calculation import lex_calculation_streamlit
from lex.lex_app.streamlit.calculation_embed import lex_calculation
from lex.lex_app.streamlit.embed import Flow, lex_view

__all__ = [
    "Flow",
    "lex_calculation",
    "lex_calculation_streamlit",
    "lex_view",
]
