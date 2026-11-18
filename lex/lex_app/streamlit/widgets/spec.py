"""Widget specs and the manifest -- moved to :mod:`lex.lex_app.embed.widgets`.

The Reflex dashboards post the same manifest to lex-app's widget host, so the
spec lives with the rest of the embed contract, free of Streamlit. Every name
stays importable from here.
"""

from lex.lex_app.embed.widgets import *  # noqa: F401,F403
from lex.lex_app.embed.widgets import (  # noqa: F401
    CALCULATION_VARIANTS,
    KNOWN_OPTIONS,
    KNOWN_TYPES,
    MANIFEST_VERSION,
    PK,
    WidgetSpecError,
    build_manifest,
    calculation_log_spec,
    calculation_spec,
)
