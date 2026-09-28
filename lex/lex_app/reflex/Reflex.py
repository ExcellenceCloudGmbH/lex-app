import os

from lex.core.models.HTMLReport import HTMLReport


class Reflex(HTMLReport):
    """The project's Reflex dashboards, as a page of the lex-app frontend.

    Registered -- and placed in the sidebar -- only when ``IS_REFLEX_ENABLED``
    is ``true``: a project that never runs ``lex reflex`` should not show an
    entry that frames nothing.
    """

    def get_html(self, user):
        # 8502 is `lex reflex`'s default frontend port (lex/bin/lex.py). `lex_embed=1`
        # is how lex-app says it is framing a dashboard in its own chrome: the page
        # then leaves out the signed-in user and sign-out that chrome already shows.
        base = os.getenv("REFLEX_URL", "http://localhost:8502").rstrip("/")
        return f"""<iframe
              src="{base}/?lex_embed=1"
              style="width:100%;border:none;height:100%"
            ></iframe>"""


def reflex_enabled() -> bool:
    """``IS_REFLEX_ENABLED``, read the way ``IS_STREAMLIT_ENABLED`` is."""
    return os.getenv("IS_REFLEX_ENABLED") == "true"
