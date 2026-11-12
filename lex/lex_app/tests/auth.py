"""Sign a test client in the way lex sees a real sign-in (lex-app 2.5, django-oauth2-codeflow 3).

Since lex-app 2.5 a request's identity comes from its OIDC sign-in, ``request.oidc``: the
user's Keycloak client roles from the access token (``resource_access``), the profile from
the ID token. Two things that worked in tests before 2.5 no longer do:

* ``client.force_login(user)`` alone gives a signed-in Django user with **no** client roles;
* hand-made session keys (``session["oidc_userinfo"]``, ``session["oidc_access_token"]``,
  ``session["oidc_expires_at"]``) are not read any more.

And one thing is new: a write (POST, PUT, PATCH, DELETE) of a signed-in user asks Keycloak for
the user's UMA permissions *before* the view runs. A test has no Keycloak that knows its
tokens -- a real one would refuse them (401), an unreachable one makes lex answer 503 -- so
the answer must be stubbed.

:func:`sign_in` does all of it for one test::

    from rest_framework.test import APIClient
    from lex.lex_app.tests.auth import sign_in

    class FundAdminTest(TestCase):
        def test_fund_admin_may_edit(self):
            client = APIClient()
            sign_in(self, client, user, client_roles=["fund-admin"])
            response = client.patch(url, data, format="json")

The building blocks are public too: :func:`oidc_test_settings`, :func:`create_sign_in` and
:func:`stub_keycloak_uma`.
"""
from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping, Optional, Sequence
from unittest import mock

#: The django-oauth2-codeflow backend a sign-in goes through. A project without Keycloak
#: configured does not list it in ``AUTHENTICATION_BACKENDS``.
OIDC_BACKEND = "oauth2_authcodeflow.auth.AuthenticationBackend"

#: Where lex asks Keycloak for a user's UMA permissions; :func:`stub_keycloak_uma` patches it.
UMA_FETCH = "lex.api.keycloak.uma_client.fetch_uma_permissions"

#: Access-token lifetime of a test sign-in, in seconds. Long enough that the session middleware
#: never tries to refresh it during a test: there is no provider to refresh it from.
TEST_ACCESS_TOKEN_SECONDS = 3600


def oidc_test_settings() -> dict:
    """Settings under which a test client can hold an OIDC sign-in without a provider.

    The sign-in backend must be in ``AUTHENTICATION_BACKENDS``, and the tokens need a client
    id to be issued for. Use with ``django.test.override_settings(**oidc_test_settings())``.
    """
    from django.conf import settings

    backends = list(settings.AUTHENTICATION_BACKENDS)
    if OIDC_BACKEND not in backends:
        backends.insert(0, OIDC_BACKEND)
    return {
        "AUTHENTICATION_BACKENDS": backends,
        "OIDC_RP_CLIENT_ID": getattr(settings, "OIDC_RP_CLIENT_ID", None) or "lex-test",
    }


def create_sign_in(client: Any, user: Any, **kwargs: Any):
    """Sign ``user`` in on ``client`` as a browser is after Keycloak: an OIDC session.

    ``client`` is a Django or DRF test client, or an ``HttpRequest``. Keyword arguments go to
    ``oauth2_authcodeflow.testing.create_oidc_session`` (``client_roles``, ``claims``,
    ``access_expires_in``, ``refresh``, ...); the access token lasts
    :data:`TEST_ACCESS_TOKEN_SECONDS` unless ``access_expires_in`` says otherwise. The
    settings of :func:`oidc_test_settings` must be in effect. Returns the ``OIDCSession`` row.
    """
    from oauth2_authcodeflow.testing import create_oidc_session

    kwargs.setdefault("access_expires_in", TEST_ACCESS_TOKEN_SECONDS)
    return create_oidc_session(client, user, **kwargs)


def _reset_uma_cache() -> None:
    from lex.api.middleware.keycloak_permissions import reset_uma_cache

    reset_uma_cache()


class _KeycloakUmaStub:
    """A patcher answering every UMA lookup with fixed permissions (see :func:`stub_keycloak_uma`)."""

    def __init__(self, permissions: Iterable[Mapping[str, Any]]) -> None:
        self.permissions = [dict(permission) for permission in permissions]
        self._patcher = mock.patch(UMA_FETCH, side_effect=self._answer)
        self.mock: Optional[mock.MagicMock] = None

    def _answer(self, access_token: str, *args: Any, **kwargs: Any) -> list:
        return copy.deepcopy(self.permissions)

    def start(self) -> mock.MagicMock:
        _reset_uma_cache()
        self.mock = self._patcher.start()
        return self.mock

    def stop(self) -> None:
        try:
            self._patcher.stop()
        finally:
            _reset_uma_cache()

    def __enter__(self) -> mock.MagicMock:
        return self.start()

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()


def stub_keycloak_uma(permissions: Iterable[Mapping[str, Any]] = ()) -> _KeycloakUmaStub:
    """Answer every Keycloak UMA lookup with ``permissions`` instead of asking Keycloak.

    ``permissions`` is Keycloak's shape, ``[{"rsname": ..., "resource_set_id": ..., "scopes":
    [...]}, ...]``; the default ``()`` grants nothing, like a user without UMA permissions.
    Use it as a context manager (``with stub_keycloak_uma(...) as fetch:``) or ``start()`` /
    ``stop()`` it; the started mock records the calls (``fetch.call_args``). lex's UMA cache
    is cleared on start and stop, so no answer outlives the stub.
    """
    return _KeycloakUmaStub(permissions)


def _in_effect(holder: Any) -> bool:
    """Whether the settings layer ``holder`` is still part of the settings in force."""
    from django.conf import UserSettingsHolder, settings

    layer = settings._wrapped
    while isinstance(layer, UserSettingsHolder):
        if layer is holder:
            return True
        layer = layer.default_settings
    return False


def _override_for_test(test_case: Any, overrides: Any) -> None:
    """Enable ``overrides`` until ``test_case``'s cleanups run, without leaking anything.

    A test method decorated with ``@override_settings`` (or a ``with self.settings(...)``
    block) entered before this call ends *before* the cleanups run, and takes every
    override enabled inside it along. Disabling these overrides afterwards would put back
    the settings they found -- the method's own -- for every later test. So: undo them
    only while they are still in force; otherwise just tell the ``setting_changed``
    receivers (the sign-in library caches its settings) that the values are back.
    """
    from django.conf import settings
    from django.test.signals import setting_changed

    overrides.enable()
    holder = settings._wrapped

    def undo() -> None:
        if _in_effect(holder):
            overrides.disable()
            return
        for key in overrides.options:
            setting_changed.send_robust(
                sender=settings._wrapped.__class__,
                setting=key,
                value=getattr(settings, key, None),
                enter=False,
            )

    test_case.addCleanup(undo)


def sign_in(
    test_case: Any,
    client: Any,
    user: Any,
    *,
    client_roles: Sequence[str] = (),
    uma_permissions: Iterable[Mapping[str, Any]] = (),
    **kwargs: Any,
):
    """Sign ``user`` in on ``client`` with these Keycloak client roles, for the rest of ``test_case``.

    * The sign-in backend is made available (:func:`oidc_test_settings`).
    * Keycloak's UMA answer is stubbed with ``uma_permissions`` (default: nothing granted).
    * The client holds an OIDC sign-in whose access token carries ``client_roles``.

    ``test_case`` is the running ``unittest.TestCase`` (Django's or lex's
    ``ProcessAdminTestCase``): the settings and the stub are undone through its
    ``addCleanup``, also when the test method has its own ``@override_settings``.
    Further keyword arguments go to :func:`create_sign_in`. Returns the ``OIDCSession``
    row.
    """
    from django.test import override_settings

    _override_for_test(test_case, override_settings(**oidc_test_settings()))
    stub = stub_keycloak_uma(uma_permissions)
    stub.start()
    test_case.addCleanup(stub.stop)
    return create_sign_in(client, user, client_roles=list(client_roles), **kwargs)


__all__ = [
    "OIDC_BACKEND",
    "TEST_ACCESS_TOKEN_SECONDS",
    "UMA_FETCH",
    "create_sign_in",
    "oidc_test_settings",
    "sign_in",
    "stub_keycloak_uma",
]
