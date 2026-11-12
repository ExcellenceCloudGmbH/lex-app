from urllib.parse import urlencode

from lex.core.models.HTMLReport import HTMLReport
from lex import hosts as lex_hosts


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
        # Not STREAMLIT_URL directly: an instance with a customer-owned domain
        # attached serves its dashboard on more than one hostname, and framing
        # the canonical one inside a shell on the customer's domain makes the
        # iframe a third-party context whose cookies Safari blocks outright --
        # so the dashboard never signs in. `same_site_url` picks the hostname
        # that shares a registrable domain with the one this request arrived
        # on, and falls back to the canonical URL when there is no such host.
        base = lex_hosts.same_site_url("streamlit")
        params = {"embed": "true"}

        # The sign-in's own Keycloak access token -- the same value
        # `StreamlitTokenView` hands the React shell for the same purpose. It is
        # short-lived and carries no refresh token, so what travels here is no
        # more than the proxy already accepts on the embedded path.
        token = _frame_token(request)
        if token:
            params["auth_token"] = token

        return f"""<iframe
              src="{base}/?{urlencode(params)}"
              style="width:100%;border:none;height:100%"
            ></iframe>"""


def _frame_token(request):
    """The access token to bootstrap the frame with, or ``None``.

    Read through django-oauth2-codeflow 3 (``get_access_token``, renewed first when it would
    expire within a minute), as :class:`StreamlitTokenView` does: since lex-app 2.5 a sign-in's
    tokens live encrypted in its ``OIDCSession`` row, not in session keys. Nothing is handed
    when there is no request, no OIDC sign-in (an API key, an admin login), when the sign-in
    has ended or Keycloak cannot renew it -- the frame then takes the proxy's own path -- and
    under ``LEX_STREAMLIT_AUTH=handoff``, where the proxy ignores ``auth_token`` and signs the
    dashboard in through the hand-off instead.
    """
    if request is None:
        return None
    from lex.authentication.dashboards.modes import HANDOFF, streamlit_auth_mode

    if streamlit_auth_mode() == HANDOFF:
        return None
    try:
        from oauth2_authcodeflow.tokens import get_access_token

        from lex.authentication.views.token_views import TOKEN_REFRESH_SKEW_SECONDS

        return get_access_token(
            getattr(request, "_request", request), min_ttl=TOKEN_REFRESH_SKEW_SECONDS
        ) or None
    except Exception:  # an ended sign-in or an unreachable Keycloak: frame without a token
        return None
