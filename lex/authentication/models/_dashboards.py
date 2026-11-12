"""The Django-anchored dashboard hand-off: single-use codes and the bindings they are redeemed into.

A dashboard (the Streamlit proxy) never holds Keycloak tokens of its own. It holds an opaque
handle in a cookie; the handle names a :class:`DashboardBinding`, the binding names the user's
``OIDCSession`` (django-oauth2-codeflow), and every use asks the library for a fresh access token
of that sign-in. When the sign-in ends -- Keycloak refuses the refresh, the user signs out, a
back-channel logout arrives -- every binding on it stops working at its next use, and deleting
the sign-in row deletes its bindings (``CASCADE``).

Neither model stores a secret in the clear: codes and handles are kept as SHA-256 digests, so a
database read gives nothing that can be replayed. ``aud`` says which surface a row belongs to:
``streamlit`` for dashboards, ``embed`` for the MCP embed ticket (whose sign-in row carries the
MCP client's access token and no refresh token, so its binding ends with that token).
"""
from __future__ import annotations

from django.db import models


class DashboardHandoffCode(models.Model):
    """A single-use code (valid 60 s) that turns into a :class:`DashboardBinding` once.

    Bound to the sign-in it was minted for, the audience, the origin it may be redeemed for and
    -- for the Streamlit hand-off -- the ``state`` nonce of the browser that asked for it.
    """

    code_hash = models.CharField(max_length=64, unique=True)
    session = models.ForeignKey(
        "oauth2_authcodeflow.OIDCSession",
        on_delete=models.CASCADE,
        related_name="lex_dashboard_handoff_codes",
    )
    aud = models.CharField(max_length=32)
    return_origin = models.CharField(max_length=512)
    state_hash = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    #: The binding this code was redeemed into (revoked if the code is presented again).
    binding = models.ForeignKey(
        "authentication.DashboardBinding",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        app_label = "authentication"
        verbose_name = "dashboard hand-off code"

    def __str__(self) -> str:
        state = "used" if self.used_at else "unused"
        return f"Dashboard hand-off code ({self.aud}, {state})"


class DashboardBinding(models.Model):
    """A dashboard's (or an embed's) standing on one sign-in, named by an opaque cookie handle."""

    handle_hash = models.CharField(max_length=64, unique=True)
    session = models.ForeignKey(
        "oauth2_authcodeflow.OIDCSession",
        on_delete=models.CASCADE,
        related_name="lex_dashboard_bindings",
    )
    aud = models.CharField(max_length=32)
    #: The origin (dashboard host, or the frontend for an embed) the handle cookie was set on.
    origin = models.CharField(max_length=512)
    created_at = models.DateTimeField(auto_now_add=True)
    #: When the handle cookie was last (re-)set; the cookie lives 30 days from here.
    cookie_set_at = models.DateTimeField()
    #: Embeds only: the end of the access token they were issued from. ``None`` for dashboards,
    #: which live as long as their sign-in.
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    revoke_reason = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        app_label = "authentication"
        verbose_name = "dashboard binding"

    def __str__(self) -> str:
        state = f"revoked ({self.revoke_reason})" if self.revoked_at else "active"
        return f"Dashboard binding ({self.aud}, {state})"


__all__ = ["DashboardBinding", "DashboardHandoffCode"]
