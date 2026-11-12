"""The Streamlit token carries the caller's own Keycloak permissions.

Intent
------
``StreamlitTokenView`` (lex/authentication/views/token_views.py) mints the short-lived
token a Streamlit dashboard uses and embeds the caller's UMA permissions in it. Before
lex-app 2.5 the view imported ``KeycloakManager`` relative to its own package, where no
such module exists; the ImportError was swallowed and every Streamlit token carried no
permissions at all. It now uses ``lex.api.views.authentication.KeycloakManager``, whose
``get_uma_permissions`` sends the token on its own request (cluster 4x), and turns a
``None`` answer into an empty list.

Cluster 4aa -- scenario 4.163. Type: U.
Covers: lex/authentication/views/token_views.py (StreamlitTokenView._get_user_permissions).
Run: python -m lex pytest lex/test_project/tests/permissions/test_4aa_streamlit_token_permissions.py -v
"""

from __future__ import annotations

from unittest import mock

from django.test import RequestFactory, SimpleTestCase

from lex.authentication.views.token_views import StreamlitTokenView

_MANAGER = "lex.api.views.authentication.KeycloakManager.KeycloakManager"


class TestCluster04aa_StreamlitTokenPermissions(SimpleTestCase):
    """4.163: the permissions in a Streamlit token are the caller's, from Keycloak."""

    def _permissions(self, **headers):
        request = RequestFactory().post("/api/streamlit/token/", **headers)
        return StreamlitTokenView()._get_user_permissions(request)

    # -- 4.163 -------------------------------------------------------------
    def test_4_163_streamlit_token_asks_keycloak_with_the_callers_own_token(self) -> None:
        """
        Scenario 4.163: the view asks KeycloakManager with the caller's bearer token and
        returns its answer; None and a missing token both give an empty list.
        """
        granted = [{"rsname": "Expense", "scopes": ["read"]}]
        with mock.patch(_MANAGER) as manager_cls:
            manager_cls.return_value.get_uma_permissions.return_value = granted
            self.assertEqual(self._permissions(HTTP_AUTHORIZATION="Bearer tok-alice"), granted)
            manager_cls.return_value.get_uma_permissions.assert_called_once_with("tok-alice")

            manager_cls.return_value.get_uma_permissions.reset_mock()
            manager_cls.return_value.get_uma_permissions.return_value = None
            self.assertEqual(self._permissions(HTTP_AUTHORIZATION="Bearer tok-bob"), [])

            manager_cls.return_value.get_uma_permissions.reset_mock()
            self.assertEqual(self._permissions(), [])
            manager_cls.return_value.get_uma_permissions.assert_not_called()
