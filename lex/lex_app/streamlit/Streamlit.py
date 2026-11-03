import os
from urllib.parse import urlencode

from lex.core.models.HTMLReport import HTMLReport


class Streamlit(HTMLReport):
    """The project's Streamlit app, framed inside lex-app's own page.

    The frame is cross-origin: the dashboard is served from its own host, behind
    the auth proxy. A cross-origin frame cannot authenticate itself -- the proxy
    would have to redirect it to Keycloak, which Keycloak forbids via
    ``frame-ancestors 'self'`` -- so its only way in is the bootstrap
    ``auth_token`` the embedding page hands it. The proxy exchanges that token
    for a session cookie on the first request and strips it from the URL, which
    is the same handshake the React shell's own dashboard embed performs.

    Framing the dashboard *without* a token is what sent the whole browser tab
    to the dashboard host: the frame 401'd, and the proxy answers a framed 401
    by moving the top window to its own login, whose post-login landing is a
    path on that host. The user clicked a sidebar entry inside their instance
    and ended up on ``https://<instance>-streamlit.<domain>/?embed=true``.
    """

    def get_html(self, user, request=None):
        """The report's HTML: the dashboard framed with a usable credential.

        ``request`` is optional because :class:`HTMLReport` has only ever
        promised ``get_html(user)``. Without one -- an API-key caller, or any
        other path that has no browser session to speak of -- the frame is
        built exactly as before; it is only a browser that needs the token, and
        only a browser that can be thrown out of its tab without one.
        """
        base = os.getenv("STREAMLIT_URL", "http://localhost:8501")
        params = {"embed": "true"}

        # The session's own Keycloak access token -- the same value
        # `StreamlitTokenView` hands the React shell for the same purpose. It is
        # short-lived and carries no refresh token, so what travels here is no
        # more than the proxy already accepts on the embedded path.
        token = getattr(request, "session", {}).get("oidc_access_token") if request else None
        if token:
            params["auth_token"] = token

        return f"""<iframe
              src="{base}/?{urlencode(params)}"
              style="width:100%;border:none;height:100%"
            ></iframe>"""
