"""Cluster 2m: an API key in the ``?api_key=`` query parameter.

Intent: Some external systems can only be configured with a callback URL — no
headers, no Basic auth (the Deutsche Post MultiConnect / Postident DataConnect
backend callback is one). Lex must accept a ``djangorestframework-api-key`` key
from the ``api_key`` query parameter when the request carries no API-key header,
so such a system can call the generic model-entry endpoints. The whole path must
accept it: the OIDC login middleware (``ApiKeyAwareLoginRequiredMiddleware``),
the view's ``HasAPIKey`` permission, and the API-key identity that stamps
``created_by`` with the API-key audit actor. A key in a header always wins over the query parameter, and a
wrong or empty query key is refused like a missing key.

Cluster 2m — scenarios 2.117–2.121. Type: E (real ``APIKey`` rows, real
middleware, real REST create through ``OneModelEntry``).
Covers: ``lex/api/utils/api_key_requests.py`` — ``get_raw_api_key``,
``LexKeyParser``, ``HasAPIKey``; ``lex/api/views/model_entries/One.py``.
Run: python -m lex pytest lex/test_project/tests/crud_api/test_2m_api_key_query_param.py -v
"""

from __future__ import annotations

from urllib.parse import urlencode

import pytest

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, SIMPLE, SimpleItem

pytestmark = pytest.mark.crud_api


class TestCluster02m_ApiKeyQueryParam(E2ETestCase):
    """An API key in ``?api_key=`` authenticates a model-entry create."""

    e2e_models = ALL_MODELS

    def setUp(self) -> None:
        super().setUp()
        # Creates a real APIKey row; drop the header it installs on the client
        # so each test decides where the key travels.
        self.query_key = self.authenticate_as_api_key(name="Callback Key")
        self.client.credentials()

    def _create(self, name: str, query: dict | None = None, **headers):
        url = self.url_create(SIMPLE)
        if query is not None:
            url = f"{url}?{urlencode(query)}"
        return self.client.post(url, data={"name": name, "value": 1}, format="json", **headers)

    # -- 2.117 ---------------------------------------------------------
    def test_2_117_query_key_creates_entry_as_api_key_caller(self) -> None:
        """
        Scenario 2.117: a valid key in ``?api_key=`` alone authenticates a create.
        Given: a registered API key and no API-key header, no session.
        When: the caller POSTs to ``model_entries/<model>/<calcId>/one?api_key=<key>``.
        Then: the entry is created and stamped with the API-key audit actor.
        """
        resp = self._create("query-2-117", {"api_key": self.query_key})

        self.assertIn(resp.status_code, (200, 201), getattr(resp, "data", resp.content))
        item = SimpleItem.objects.get(name="query-2-117")
        self.assertEqual(item.created_by, SimpleItem.API_KEY_AUDIT_ACTOR)

    # -- 2.118 ---------------------------------------------------------
    def test_2_118_header_wins_so_a_wrong_header_key_is_not_rescued(self) -> None:
        """
        Scenario 2.118: the query parameter is not consulted when a header carries a key.
        Given: a key that does not exist in the ``Api-Key`` header.
        When: ``?api_key=`` carries a valid key.
        Then: the request is refused (401/403) and nothing is created.
        """
        resp = self._create("header-2-118", {"api_key": self.query_key}, HTTP_API_KEY="not-a-key")

        self.assertIn(resp.status_code, (401, 403), getattr(resp, "data", resp.content))
        self.assertFalse(SimpleItem.objects.filter(name="header-2-118").exists())

    # -- 2.119 ---------------------------------------------------------
    def test_2_119_header_key_wins_over_a_wrong_query_key(self) -> None:
        """
        Scenario 2.119: a valid header key is not spoiled by a wrong query key.
        Given: the valid key in the ``Api-Key`` header.
        When: ``?api_key=`` carries a key that does not exist.
        Then: the entry is created by the API-key caller.
        """
        resp = self._create("header-2-119", {"api_key": "not-a-key"}, HTTP_API_KEY=self.query_key)

        self.assertIn(resp.status_code, (200, 201), getattr(resp, "data", resp.content))
        item = SimpleItem.objects.get(name="header-2-119")
        self.assertEqual(item.created_by, SimpleItem.API_KEY_AUDIT_ACTOR)

    # -- 2.120 ---------------------------------------------------------
    def test_2_120_wrong_query_key_is_refused(self) -> None:
        """
        Scenario 2.120: a key that does not exist in ``?api_key=`` is refused.
        Given: no API-key header and no session.
        When: the caller POSTs with ``?api_key=not-a-key``.
        Then: the request is refused (401/403) and nothing is created.
        """
        resp = self._create("wrong-2-120", {"api_key": "not-a-key"})

        self.assertIn(resp.status_code, (401, 403), getattr(resp, "data", resp.content))
        self.assertFalse(SimpleItem.objects.filter(name="wrong-2-120").exists())

    # -- 2.121 ---------------------------------------------------------
    def test_2_121_empty_query_key_is_refused(self) -> None:
        """
        Scenario 2.121: an empty or blank ``?api_key=`` counts as no key.
        Given: no API-key header and no session.
        When: the caller POSTs with ``?api_key=`` and with ``?api_key=%20``.
        Then: both requests are refused (401/403) and nothing is created.
        """
        for value in ("", " "):
            with self.subTest(value=value):
                resp = self._create("empty-2-121", {"api_key": value})
                self.assertIn(resp.status_code, (401, 403), getattr(resp, "data", resp.content))
        self.assertFalse(SimpleItem.objects.filter(name="empty-2-121").exists())

