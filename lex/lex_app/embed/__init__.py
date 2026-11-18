"""lex-app's embed contract, shared by every dashboard framework that frames lex-app.

:mod:`lex.lex_app.embed.core` -- ``lex_view``'s URL, ``Flow``, filters;
:mod:`lex.lex_app.embed.widgets` -- the widget host's manifest. Neither imports
Streamlit or Reflex: ``lex.lex_app.streamlit`` and ``lex.lex_app.reflex`` render
frames over them.
"""

from lex.lex_app.embed.core import (
    EVENT_FLAGS,
    FLOW_WIRE_VERSION,
    MAX_URL_FILTERS_TOKEN,
    STAY,
    EmbedRequest,
    Flow,
    FlowError,
    Ref,
    build_lex_view_url,
    encode_filters_token,
    normalise_filters,
    origin_of,
    ref,
)
from lex.lex_app.embed.widgets import (
    MANIFEST_VERSION,
    WidgetSpecError,
    build_manifest,
    calculation_log_spec,
    calculation_spec,
)

__all__ = [
    "EVENT_FLAGS",
    "FLOW_WIRE_VERSION",
    "MANIFEST_VERSION",
    "MAX_URL_FILTERS_TOKEN",
    "STAY",
    "EmbedRequest",
    "Flow",
    "FlowError",
    "Ref",
    "WidgetSpecError",
    "build_lex_view_url",
    "build_manifest",
    "calculation_log_spec",
    "calculation_spec",
    "encode_filters_token",
    "normalise_filters",
    "origin_of",
    "ref",
]
