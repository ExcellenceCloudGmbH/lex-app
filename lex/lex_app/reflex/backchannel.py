"""Back-channel logouts reach Reflex dashboards (plan "long-lived sessions", section 6.4, item 6).

When Keycloak ends a session -- the user signed out elsewhere, an administrator ended it -- it
posts a logout token to ``/oidc/backchannel-logout``. django-oauth2-codeflow ends the matching
Django sign-ins there and sends ``oidc_backchannel_logout_received`` -- also when no Django
sign-in matched, because other holders of the same Keycloak session may exist. A Reflex dashboard
is one: it keeps its own tokens (decision D5). The receiver below records the ended Keycloak
session as ``lex:kc_sid_ended:{sid}`` for 90 days in the Redis every pod of the instance shares,
and ``LexKeycloakAuthState`` signs out the dashboards holding tokens of that session at their next
token validation instead of at their next refused refresh.

Connected by importing this module, which ``lex/lex_app/urls.py`` does: the URL configuration
that routes the back-channel endpoint is always loaded before it can be called. The module
imports nothing from Reflex.
"""

from __future__ import annotations

import logging

from django.dispatch import receiver
from oauth2_authcodeflow.signals import oidc_backchannel_logout_received

from lex.lex_app.reflex import _token_guard

logger = logging.getLogger(__name__)


@receiver(oidc_backchannel_logout_received, dispatch_uid="lex.lex_app.reflex.backchannel")
def remember_ended_keycloak_session(sender, sid=None, sub=None, matched=0, **kwargs) -> None:
    """Record that Keycloak ended session ``sid`` (``lex:kc_sid_ended:{sid}``, 90 days).

    A logout token without ``sid`` names a whole user (``sub``); it is not recorded -- those
    dashboards end at their next refresh, which Keycloak then refuses. Never raises: the library
    calls receivers best-effort, and the logout itself has happened already.
    """
    if not sid:
        logger.debug("Back-channel logout without sid: Reflex dashboards end at their next refresh.")
        return
    try:
        _token_guard.mark_sid_ended(str(sid))
    except Exception as exc:  # e.g. no key to read the settings with; the logout stands anyway
        logger.warning("Could not record the ended Keycloak session for Reflex dashboards (%s).", type(exc).__name__)


__all__ = ["remember_ended_keycloak_session"]
