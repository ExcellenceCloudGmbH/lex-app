"""
Cluster 12i: Foreign-key display names in the read contract.

Intent (BUG-F-003): a foreign key is emitted as its raw primary-key id
(``related: 79``). That id is what filtering and editing need, so it must stay
— but a customer looking at a grid sees a bare number where they expect a name.
The serializer's job is to *also* hand the frontend the human-readable label so
the grid can show "Alpha Fund" instead of "79", WITHOUT the frontend having to
issue a second round-trip per cell.

The contract this pins:

* every serialized row carries an additive companion key
  ``<fk>__short_description`` alongside the raw ``<fk>`` id;
* its value is ``str(related)`` — the model author's ``__str__`` /
  ``short_description``, which is the documented customization point (so a
  project that wants a different label just overrides ``__str__``);
* the raw id is left **unchanged** (nothing that filters/edits on the id breaks);
* a null FK yields a null companion (present, ``None``) so the row shape is
  stable row-to-row;
* the whole page's names are resolved in ONE query per FK field — no N+1.

This mirrors, on the read path, the resolution ``ModelExport`` already performs
for exported files (``_apply_foreign_key_display_names``): same ``pk__in`` batch,
same ``str(obj)`` source of truth. It is the frontend twin of F9.6 / F3 (FK
columns showing display names).

Golden Rule: we assert the customer-visible outcome (the name is available and
correct, the id still works), not the serializer's internal field wiring.

Scenario numbering: 12.42–12.45.

Run:
    lex test lex.test_project.tests.serializers.test_12i_fk_display_names \
        --verbosity=2 --noinput --keepdb
"""

from __future__ import annotations

import unittest

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, RELATED, WIDE, RelatedItem, WideItem

import pytest

pytestmark = pytest.mark.serializers

# The companion key the frontend reads to render the FK's label.
FK_FIELD = "related"
FK_DISPLAY_KEY = "related__short_description"


def _relateditem_query_count(captured: CaptureQueriesContext) -> int:
    """Number of captured SQL statements that touch the RelatedItem table.

    RelatedItem is only ever loaded to resolve the FK display name, so this
    count is exactly the FK-resolution query count for the request.
    """
    return sum(
        1 for q in captured.captured_queries if "relateditem" in q["sql"].lower()
    )


class TestCluster12i_ForeignKeyDisplayNames(E2ETestCase):
    """GET list / detail — foreign keys carry a human-readable companion."""

    e2e_models = ALL_MODELS

    # -- 12.42 ---------------------------------------------------------
    def test_12_42_list_row_carries_fk_display_name(self) -> None:
        """Scenario 12.42: each list row exposes ``<fk>__short_description``
        equal to ``str(related)`` for its FK, alongside the raw id.

        This is the fix's headline: the grid can render the FK's name from the
        list payload alone, no per-cell fetch.
        """
        fund = RelatedItem.objects.create(name="Alpha Fund")
        WideItem.objects.create(name="row-A", related=fund)

        resp = self.list_get(WIDE)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = self.extract_results(resp.data)

        row = next(r for r in rows if r.get("name") == "row-A")
        self.assertIn(
            FK_DISPLAY_KEY, row,
            f"FK display companion missing from list row. Keys: {sorted(row)}",
        )
        self.assertEqual(
            row[FK_DISPLAY_KEY], str(fund),
            "FK companion must equal str(related) — the model's display label.",
        )

    # -- 12.43 ---------------------------------------------------------
    def test_12_43_display_name_honors_custom_str(self) -> None:
        """Scenario 12.43: the label comes from the related model's ``__str__``
        (the customization point), not from the id or the field name.

        ``RelatedItem.__str__`` returns ``self.name``; a project overriding
        ``__str__`` is exactly how a customer changes what the grid shows.
        """
        fund = RelatedItem.objects.create(name="Distinctive-Label-42")
        WideItem.objects.create(name="row-str", related=fund)

        resp = self.list_get(WIDE)
        rows = self.extract_results(resp.data)
        row = next(r for r in rows if r.get("name") == "row-str")

        self.assertEqual(row[FK_DISPLAY_KEY], "Distinctive-Label-42")
        # And it is genuinely the __str__ output, not the repr / id / pk-string.
        self.assertEqual(row[FK_DISPLAY_KEY], str(fund))
        self.assertNotEqual(row[FK_DISPLAY_KEY], str(fund.pk))

    # -- 12.44 ---------------------------------------------------------
    def test_12_44_raw_id_preserved_and_detail_matches_list(self) -> None:
        """Scenario 12.44: the raw FK id is untouched (filtering/editing keep
        working) and the detail shape carries the same companion as the list
        (no list-vs-detail contract drift — the 12c invariant).
        """
        fund = RelatedItem.objects.create(name="Beta Fund")
        item = WideItem.objects.create(name="row-id", related=fund)

        # List: raw id preserved + companion present.
        list_resp = self.list_get(WIDE)
        list_row = next(
            r for r in self.extract_results(list_resp.data) if r.get("name") == "row-id"
        )
        self.assertEqual(
            list_row[FK_FIELD], fund.pk,
            "Raw FK id must be preserved unchanged for filtering/editing.",
        )
        self.assertEqual(list_row[FK_DISPLAY_KEY], str(fund))

        # Detail: same two keys, same values (list row shape ⊆ detail shape).
        detail_resp = self.client.get(self.url_detail(WIDE, item.pk))
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_resp.data[FK_FIELD], fund.pk)
        self.assertEqual(detail_resp.data[FK_DISPLAY_KEY], str(fund))
        self.assertLessEqual(
            set(list_row.keys()), set(detail_resp.data.keys()),
            "List row carries a key absent from detail — contract drift.",
        )

    # -- 12.45 ---------------------------------------------------------
    def test_12_45_null_fk_and_no_n_plus_one(self) -> None:
        """Scenario 12.45: a null FK yields a null companion (present, no crash),
        and display-name resolution does NOT scale with the row count.

        N-safety is the reason the batch lives on the list serializer: a naive
        per-row ``str(self.related)`` would issue one RelatedItem query per row,
        so the query count against the FK target would grow with distinct FKs.
        We prove it doesn't by measuring a small page and a larger page (each row
        a DISTINCT RelatedItem) and asserting the FK-target query count is equal
        — the batch adds a fixed cost regardless of N.
        """
        # Null-FK row first: companion must be present and None (stable shape).
        WideItem.objects.create(name="row-null", related=None)
        for i in range(2):
            fund = RelatedItem.objects.create(name=f"Fund-{i}")
            WideItem.objects.create(name=f"row-{i}", related=fund)

        with CaptureQueriesContext(connection) as small:
            resp_small = self.list_get(WIDE)
        self.assertEqual(resp_small.status_code, status.HTTP_200_OK)
        rows_small = self.extract_results(resp_small.data)
        count_small = _relateditem_query_count(small)

        # Null FK → companion present, value None.
        null_row = next(r for r in rows_small if r.get("name") == "row-null")
        self.assertIn(FK_DISPLAY_KEY, null_row, "Companion key must exist even for a null FK.")
        self.assertIsNone(null_row[FK_DISPLAY_KEY])
        self.assertIsNone(null_row[FK_FIELD])

        # Grow the page: 6 more rows, each a DISTINCT RelatedItem.
        for i in range(2, 8):
            fund = RelatedItem.objects.create(name=f"Fund-{i}")
            WideItem.objects.create(name=f"row-{i}", related=fund)

        with CaptureQueriesContext(connection) as large:
            resp_large = self.list_get(WIDE)
        self.assertEqual(resp_large.status_code, status.HTTP_200_OK)
        rows_large = self.extract_results(resp_large.data)
        count_large = _relateditem_query_count(large)

        # Every populated row resolved correctly on the larger page.
        for i in range(8):
            row = next(r for r in rows_large if r.get("name") == f"row-{i}")
            self.assertEqual(row[FK_DISPLAY_KEY], f"Fund-{i}")

        # The FK-target query count did NOT grow with 4× the distinct FKs — the
        # resolution is batched, not per-row.
        self.assertEqual(
            count_small, count_large,
            f"FK display-name resolution scales with row count "
            f"(3 rows → {count_small} queries, 9 rows → {count_large}); "
            f"it must be a fixed per-page cost.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
