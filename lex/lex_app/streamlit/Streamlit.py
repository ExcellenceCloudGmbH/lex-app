from lex.core.models.HTMLReport import HTMLReport
from lex import hosts as lex_hosts


class Streamlit(HTMLReport):
    """The project's Streamlit dashboard, framed in the lex-app frontend."""

    def get_html(self, user):
        # Not STREAMLIT_URL directly: an instance with a customer-owned domain
        # attached serves its dashboard on more than one hostname, and framing
        # the canonical one inside a shell on the customer's domain makes the
        # iframe a third-party context whose cookies Safari blocks outright --
        # so the dashboard never signs in. `same_site_url` picks the hostname
        # that shares a registrable domain with the one this request arrived
        # on, and falls back to the canonical URL when there is no such host.
        base = lex_hosts.same_site_url("streamlit")
        return f"""<iframe
              src="{base}/?embed=true"
              style="width:100%;border:none;height:100%"
            ></iframe>"""
