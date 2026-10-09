"""Cluster 12l: a foreign key carries its display name on every path the UI reads.

Intent: a customer looking at a record expects a foreign key to read as the
related object's name -- its ``__str__`` -- never as a bare id. The frontend
never fetches the related record to label a cell or a detail field: it renders
the ``<fk>__short_description`` companion the serializer adds next to the raw id
(batch 12i), and shows the id when the companion is missing. So every request
the interface actually sends must carry the companion. Batch 12i pinned the plain
GET list and detail; these are the requests the screens send:

* the grid loads rows by POSTing an AG Grid request to the list endpoint;
* the History tab is the same grid on the ``historical<model>`` resource;
* the Summary tab loads the record with ``?serializer=<name>`` -- ``default``
  unless the user picks another serializer, which may be one the project
  declared in ``api_serializers``.

A regression on any of them shows the customer ids where they expect names.

Cluster 12l -- scenarios 12.54-12.57. Type: E.
Covers: lex/api/serializers/base_serializers.py (``FilteredListSerializer`` /
``LexSerializer`` FK companions, ``_wrap_custom_serializer``),
lex/api/views/model_entries/List.py (AG Grid POST),
lex/api/views/model_entries/mixins/ModelEntryProviderMixin.py (``?serializer=``).
Run: python -m lex pytest lex/test_project/tests/serializers/test_12l_fk_labels_on_every_read_path.py -v
"""

from __future__ import annotations

import pytest
from rest_framework import serializers as drf_serializers
from rest_framework import status

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, WIDE, RelatedItem, WideItem

pytestmark = pytest.mark.serializers

FK_FIELD = "related"
FK_LABEL = "related__short_description"
HISTORICAL_WIDE = f"historical{WIDE}"


def _ag_request(**overrides) -> dict:
    """The request body the grid's server-side row model sends."""
    request = {
        "startRow": 0,
        "endRow": 100,
        "rowGroupCols": [],
        "groupKeys": [],
        "pivotCols": [],
        "pivotMode": False,
        "valueCols": [],
        "sortModel": [],
        "filterModel": {},
    }
    request.update(overrides)
    return request


class TestCluster12l_FkLabelsOnEveryReadPath(E2ETestCase):
    """Cluster 12l: the FK label on the requests the grid, History and Summary send."""

    e2e_models = ALL_MODELS

    def _grid_rows(self, model_name: str) -> list[dict]:
        resp = self.client.post(
            self.url_list(model_name),
            data={"request": _ag_request()},
            format="json",
        )
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"AG Grid POST to {model_name} failed: {getattr(resp, 'data', resp)}",
        )
        return resp.data.get("rowData") or []

    def _detail(self, pk: int, serializer: str) -> dict:
        resp = self.client.get(
            self.url_detail(WIDE, pk), data={"serializer": serializer},
        )
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"GET detail with serializer={serializer!r} failed: "
            f"{getattr(resp, 'data', resp)}",
        )
        return resp.data

    def _with_wide_api_serializers(self, serializers_map: dict[str, type]) -> None:
        """Declare ``WideItem.api_serializers`` for one test, as a project would."""
        had = "api_serializers" in WideItem.__dict__
        original = WideItem.__dict__.get("api_serializers")

        def _restore() -> None:
            if had:
                WideItem.api_serializers = original
            elif "api_serializers" in WideItem.__dict__:
                del WideItem.api_serializers

        self.addCleanup(_restore)
        WideItem.api_serializers = serializers_map

    # -- 12.54 ---------------------------------------------------------
    def test_12_54_grid_rows_carry_the_fk_label(self) -> None:
        """
        Scenario 12.54: the rows the grid loads label the foreign key.
        Given: a row whose FK points at "Alpha Fund"
        When: the grid POSTs its AG Grid request to the list endpoint
        Then: the row carries ``related__short_description == "Alpha Fund"``
            beside the untouched raw id
        """
        fund = RelatedItem.objects.create(name="Alpha Fund")
        WideItem.objects.create(name="grid-row", related=fund)

        row = next(r for r in self._grid_rows(WIDE) if r.get("name") == "grid-row")

        self.assertEqual(
            row.get(FK_LABEL), "Alpha Fund",
            f"the grid's row must carry the FK's display name; keys: {sorted(row)}",
        )
        self.assertEqual(
            row.get(FK_FIELD), fund.pk,
            "the raw FK id must stay unchanged for filtering and editing",
        )

    # -- 12.55 ---------------------------------------------------------
    def test_12_55_history_rows_label_each_version_fk(self) -> None:
        """
        Scenario 12.55: every row of the History tab labels the FK it held.
        Given: a record created pointing at "Fund A", then moved to "Fund B"
        When: the History tab's grid POSTs to ``historical<model>``
        Then: each version's row carries the name of the fund it pointed at --
            "Fund A" on the first version, "Fund B" on the second
        """
        fund_a = RelatedItem.objects.create(name="Fund A")
        fund_b = RelatedItem.objects.create(name="Fund B")
        item = WideItem.objects.create(name="history-row", related=fund_a)
        item.related = fund_b
        item.save()

        labels_by_fk = {
            row.get(FK_FIELD): row.get(FK_LABEL)
            for row in self._grid_rows(HISTORICAL_WIDE)
            if row.get("name") == "history-row"
        }

        self.assertEqual(
            labels_by_fk,
            {fund_a.pk: "Fund A", fund_b.pk: "Fund B"},
            "each History row must carry the display name of the FK it held",
        )

    # -- 12.56 ---------------------------------------------------------
    def test_12_56_summary_request_carries_the_fk_label(self) -> None:
        """
        Scenario 12.56: the record the Summary tab loads labels the FK.
        Given: a record whose FK points at "Gamma Fund"
        When: the Summary tab requests it with ``?serializer=default``
        Then: the payload carries ``related__short_description == "Gamma Fund"``
            beside the raw id
        """
        fund = RelatedItem.objects.create(name="Gamma Fund")
        item = WideItem.objects.create(name="summary-row", related=fund)

        data = self._detail(item.pk, "default")

        self.assertEqual(
            data.get(FK_LABEL), "Gamma Fund",
            f"the Summary's record must carry the FK's display name; keys: {sorted(data)}",
        )
        self.assertEqual(data.get(FK_FIELD), fund.pk)

    # -- 12.57 ---------------------------------------------------------
    def test_12_57_project_serializer_still_carries_the_fk_label(self) -> None:
        """
        Scenario 12.57: a serializer the project declared labels the FK too.
        Given: the model declares ``api_serializers`` -- a plain DRF
            ``ModelSerializer`` as ``default`` and another as ``detail`` -- each
            exposing the FK, and a record pointing at "Delta Fund"
        When: the Summary tab requests the record with either serializer
        Then: both payloads carry ``related__short_description == "Delta Fund"``
            -- a project's own serializer must not turn names back into ids
        """

        class PlainDefault(drf_serializers.ModelSerializer):
            class Meta:
                model = WideItem
                fields = ["name", "related"]

        class PlainDetail(drf_serializers.ModelSerializer):
            class Meta:
                model = WideItem
                fields = ["name", "amount", "related"]

        self._with_wide_api_serializers({"default": PlainDefault, "detail": PlainDetail})
        fund = RelatedItem.objects.create(name="Delta Fund")
        item = WideItem.objects.create(name="custom-row", related=fund)

        for serializer in ("default", "detail"):
            with self.subTest(serializer=serializer):
                data = self._detail(item.pk, serializer)
                self.assertEqual(
                    data.get(FK_LABEL), "Delta Fund",
                    f"serializer {serializer!r} declared by the project must still "
                    f"carry the FK's display name; keys: {sorted(data)}",
                )
                self.assertEqual(data.get(FK_FIELD), fund.pk)
