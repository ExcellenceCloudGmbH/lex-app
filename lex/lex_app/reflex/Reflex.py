import os

from lex.core.models.HTMLReport import HTMLReport
from lex import hosts as lex_hosts


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
        #
        # Not REFLEX_URL directly: once a customer-owned domain is attached the
        # dashboards answer on more than one hostname, and framing the canonical
        # one inside a shell on the customer's domain makes this a third-party
        # iframe -- whose cookies Safari blocks, so the dashboard can never
        # hold the session it signs in with. `same_site_url` picks the hostname
        # sharing a registrable domain with the one this request arrived on,
        # and falls back to REFLEX_URL when there is none.
        base = lex_hosts.same_site_url("reflex")
        return f"""<iframe
              src="{base}/?lex_embed=1"
              style="width:100%;border:none;height:100%"
            ></iframe>"""


def reflex_enabled() -> bool:
    """``IS_REFLEX_ENABLED``, read the way ``IS_STREAMLIT_ENABLED`` is."""
    return os.getenv("IS_REFLEX_ENABLED") == "true"
