"""
Cluster 12h: ``?serializer=<name>`` query-param contract on list/detail endpoints.

Intent: ``ModelEntryProviderMixin.get_serializer_class`` resolves the
serializer to use for a request from ``?serializer=<name>`` against the
model container's ``serializers_map``. The contract this batch pins:

* An **unknown name** is a *client* error — the user's request is malformed —
  and must surface as **HTTP 400** with a body that includes the bad name and
  the keys the request *could* have used. Returning 500 (the historical
  ``APIException`` default) would mis-classify a user typo as a server fault
  and the React error toast would say "something went wrong" with no
  diagnostic for the developer.

* When ``?serializer=`` is **omitted**, the default serializer wins — the
  legacy zero-arg behaviour every existing client relies on.

* When ``?serializer=<valid>`` names a registered alternate, **that** serializer
  shapes the response — narrower field set, different keys, whatever the
  alternate declares. This is the surface the Streamlit ``lex_view(...,
  serializer=...)`` kwarg in the lex_view callbacks contract
  (``docs/features/access-and-ui/lex_view callbacks.md``) depends on.

The 400-not-500 status is the contract that lex_view's bidirectional component
relies on: a typo in the dashboard's ``serializer="…"`` argument must produce
the standard React 4xx error toast (visible to the developer), not a 5xx
"server error" that masks the cause.

Cluster 12h — scenarios 12.36–12.39. Type: E (APITestCase via E2ETestCase).
Covers: lex/api/views/model_entries/mixins/ModelEntryProviderMixin.py
        (``get_serializer_class`` — the ``?serializer=`` resolution branch).
Run: python -m lex pytest lex/test_project/tests/serializers/test_12h_serializer_query_param_contract.py -v
"""

from __future__ import annotations

import pytest
from rest_framework import serializers as drf_serializers, status

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, WIDE, WideItem

pytestmark = pytest.mark.serializers


class _CompactWideSerializer(drf_serializers.ModelSerializer):
    """Narrow alternate that returns only ``id`` + ``name``.

    Defined at module level so the class identity is stable across
    test runs and so ``WideItem.api_serializers`` can point at the
    same class in repeated suite runs.
    """

    class Meta:
        model = WideItem
        fields = ["id", "name"]


class TestCluster12h_SerializerQueryParam(E2ETestCase):
    """End-to-end contract for ``?serializer=…`` on list and detail endpoints."""

    e2e_models = ALL_MODELS

    # -- helpers ------------------------------------------------------

    def _with_wide_api_serializers(self, serializers_map: dict) -> None:
        """Install ``WideItem.api_serializers`` for one test, restore after.

        Mirrors the pattern in ``test_12e_factory.py`` so the cluster
        12 fixture story stays consistent. The container's
        ``get_serializers_map()`` will rebuild its map on next call
        because the signature changes.
        """
        had = hasattr(WideItem, "api_serializers")
        original = getattr(WideItem, "api_serializers", None)

        def _restore() -> None:
            if had:
                WideItem.api_serializers = original
            elif hasattr(WideItem, "api_serializers"):
                delattr(WideItem, "api_serializers")

        self.addCleanup(_restore)
        WideItem.api_serializers = serializers_map

    # -- 12.36 --------------------------------------------------------
    def test_12_36_list_unknown_serializer_returns_400(self) -> None:
        """Scenario 12.36: GET list with ``?serializer=ghost`` → 400.

        Given:  WideItem with no registered alternates (only "default").
        When:   the client requests ``GET /api/wideitem/?serializer=ghost``.
        Then:   the response is HTTP 400 (client error, not 500), and the
                JSON body carries an ``error`` mentioning the bad name and
                an ``available`` list including ``"default"``.
        """
        WideItem.objects.create(name="row-1", amount="1")

        resp = self.list_get(WIDE, query_params={"serializer": "ghost"})

        self.assertEqual(
            resp.status_code, status.HTTP_400_BAD_REQUEST,
            f"Unknown ?serializer= must surface as 400, got "
            f"{resp.status_code}. Body: {resp.data!r}",
        )
        body = resp.data
        self.assertIn(
            "error", body,
            f"400 body must carry an 'error' key. Got: {body!r}",
        )
        self.assertIn(
            "ghost", str(body["error"]),
            f"Error message must include the offending name 'ghost' so "
            f"the developer can find the typo. Got: {body['error']!r}",
        )
        self.assertIn(
            "available", body,
            f"400 body must list 'available' serializer names so the "
            f"developer can fix the typo. Got: {body!r}",
        )
        self.assertIn(
            "default", body["available"],
            f"'default' is always a valid choice and must be in the "
            f"available list. Got: {body['available']!r}",
        )

    # -- 12.37 --------------------------------------------------------
    def test_12_37_detail_unknown_serializer_returns_400(self) -> None:
        """Scenario 12.37: GET detail with ``?serializer=ghost`` → 400.

        Given:  a single WideItem row.
        When:   the client requests ``GET /api/wideitem/<id>/?serializer=ghost``.
        Then:   HTTP 400 — the same contract as the list endpoint, because
                both go through ``ModelEntryProviderMixin.get_serializer_class``.
                A regression that handled list and detail differently would
                let the same typo produce a 400 in one place and a 500 in
                the other.
        """
        row = WideItem.objects.create(name="d-row", amount="1")

        resp = self.client.get(
            self.url_detail(WIDE, row.pk),
            data={"serializer": "ghost"},
        )

        self.assertEqual(
            resp.status_code, status.HTTP_400_BAD_REQUEST,
            f"Detail endpoint must also return 400 on unknown serializer, "
            f"got {resp.status_code}. Body: {resp.data!r}",
        )
        self.assertIn("error", resp.data)
        self.assertIn("ghost", str(resp.data["error"]))

    # -- 12.38 --------------------------------------------------------
    def test_12_38_omitted_serializer_uses_default(self) -> None:
        """Scenario 12.38: omitting ``?serializer=`` → default serializer wins.

        Given:  a WideItem row.
        When:   the client requests ``GET /api/wideitem/`` with no query param.
        Then:   200 OK, and the row carries the default serializer's full
                shape (the type-round-trip fields exercised by 12b live here).
                Regression sentinel: a refactor that mishandled the missing
                key would either 400 or hand back the wrong shape.
        """
        WideItem.objects.create(name="default-row", amount="1.5")

        resp = self.list_get(WIDE)

        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Omitted ?serializer= must use the default and return 200. "
            f"Got {resp.status_code}. Body: {resp.data!r}",
        )
        rows = self.extract_results(resp.data)
        self.assertTrue(rows, "Expected at least one row in default response")
        first = rows[0]
        # Default serializer carries the model's full set of fields.
        # We assert on a couple of distinctive default-shape keys rather
        # than the full schema (12b owns the exhaustive shape contract).
        self.assertIn(
            "amount", first,
            f"Default serializer must include domain fields like "
            f"'amount'. Row keys: {sorted(first.keys())}",
        )
        self.assertIn("id", first)

    # -- 12.39 --------------------------------------------------------
    def test_12_39_valid_alternate_serializer_shapes_response(self) -> None:
        """Scenario 12.39: ``?serializer=compact`` returns the alternate shape.

        Given:  WideItem temporarily registers a ``compact`` alternate
                that exposes only ``id`` and ``name``.
        When:   the client requests ``GET /api/wideitem/?serializer=compact``.
        Then:   200 OK; rows are shaped by the alternate (no ``amount``,
                no ``notes``, etc.). This is the surface
                ``lex_view(serializer="…")`` ultimately depends on — without
                this round-trip the Streamlit dashboard cannot ask for a
                narrower payload.
        """
        self._with_wide_api_serializers(
            {"compact": _CompactWideSerializer},
        )
        WideItem.objects.create(name="alt-row", amount="9.99", notes="hidden")

        resp = self.list_get(WIDE, query_params={"serializer": "compact"})

        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Valid alternate ?serializer= must return 200. Got "
            f"{resp.status_code}. Body: {resp.data!r}",
        )
        rows = self.extract_results(resp.data)
        self.assertTrue(rows, "Expected at least one row in alternate response")
        keys = set(rows[0].keys())
        self.assertIn("id", keys)
        self.assertIn("name", keys)
        # The alternate is *narrow*: domain fields the default exposes
        # must be absent. Pick a couple of distinctive ones.
        self.assertNotIn(
            "amount", keys,
            f"Compact alternate must not leak 'amount'. Row keys: "
            f"{sorted(keys)}. Did the request fall back to the default "
            f"serializer instead of honouring ?serializer=compact?",
        )
        self.assertNotIn(
            "notes", keys,
            f"Compact alternate must not leak 'notes'. Row keys: "
            f"{sorted(keys)}.",
        )
