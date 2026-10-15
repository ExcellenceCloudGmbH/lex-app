"""A column's values with counts, under every filter except the column's own.

Intent: a value-list filter must offer what the grid would show — the same rows
(``as_of`` and read permissions included) under the other active filters — with a
count per value and a readable label. A column's own filter is left out, so a
ticked value can be unticked and more added; a ticked value the other filters
removed stays listed at 0. A regression offers values that lead to empty grids,
hides values the user needs, or shows ids where names belong.

Fix round 1 (14.52–14.63): the first pass could show a value the grid itself
withholds -- a foreign key's name resolved from a row the user cannot read, a
credential-shaped column exposed behind a custom serializer, a column type the
design never meant to get a value list, or a per-row ``can_read`` override
silently skipped because the database path was taken anyway. These scenarios
pin the design spec's §1 (which column types get a value list), §3.7 / §8 (a
value list never shows what the grid hides) and the blank-folding rule.
Cluster 14g — scenarios 14.42–14.63. Type: E.
Covers: lex/api/views/model_entries/FilterValues.py, lex/api/utils/filterable_fields.py,
lex/api/serializers/base_serializers.py (readable_field_names), lex/api/views/model_entries/List.py
(blank folding on plain text columns), lex/process_admin/sites/process_admin_site.py (route).
Run: python -m lex pytest lex/test_project/tests/queries/test_14g_filter_values_endpoint.py -v
"""

from __future__ import annotations

import time
import urllib.parse
from datetime import date
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework import serializers as drf_serializers
from rest_framework import status

from lex.api.views.model_entries.FilterValues import _bounded_limit, uses_default_read_permission
from lex.core.models.LexModel import LexModel
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    ITEM,
    MASKED_ITEM,
    OPEN_ITEM,
    QUERY_STATUS_ACTIVE,
    QUERY_STATUS_ARCHIVED,
    QUERY_STATUS_DRAFT,
    QueryCategory,
    QueryItem,
    QueryMaskedItem,
    QueryOpenItem,
)

pytestmark = pytest.mark.queries


class TestCluster14g_FilterValuesEndpoint(E2ETestCase):
    """Cluster 14g: POST api/model_entries/<model>/filter_values."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        self.alpha = QueryCategory.objects.create(name="alpha")
        self.beta = QueryCategory.objects.create(name="beta")
        self.gamma = QueryCategory.objects.create(name="gamma")
        rows = [
            ("alpha-small", QUERY_STATUS_ACTIVE, True, 1, date(2026, 1, 15), self.alpha),
            ("alpha-mid", QUERY_STATUS_ARCHIVED, False, 5, date(2026, 3, 10), self.alpha),
            ("beta-mid", QUERY_STATUS_DRAFT, True, 0, date(2026, 2, 20), self.beta),
            ("beta-big", QUERY_STATUS_ACTIVE, True, 9, date(2026, 2, 20), self.beta),
            ("loose", QUERY_STATUS_ACTIVE, True, 7, None, None),
        ]
        for name, state, active, count, created_on, category in rows:
            QueryItem.objects.create(
                name=name, status=state, is_active=active, count=count,
                created_on=created_on, category=category,
            )

        # 14.53 / 14.56 — per-row can_read masking of the "category" field.
        QueryMaskedItem.objects.create(name="visible-beta", visible=True, category=self.beta)
        QueryMaskedItem.objects.create(name="hidden-gamma", visible=False, category=self.gamma)

        # 14.55 / 14.59 / 14.60 — no permission_read / can_read override at all.
        QueryOpenItem.objects.create(name="open-a", status=QUERY_STATUS_ACTIVE)
        QueryOpenItem.objects.create(name="open-b", status=QUERY_STATUS_ACTIVE)
        QueryOpenItem.objects.create(name="open-c", status=QUERY_STATUS_ARCHIVED)

    def _post(self, body: dict, query: str = "", model: str = ITEM):
        return self.client.post(self.url_filter_values(model) + query, data=body, format="json")

    def _values(self, field: str, filter_model=None, query: str = "", model: str = ITEM, **extra) -> dict:
        resp = self._post({"field": field, "filterModel": filter_model or {}, **extra}, query, model=model)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        return resp.data

    def _counts(self, field: str, filter_model=None, query: str = "", model: str = ITEM, **extra) -> dict:
        data = self._values(field, filter_model, query, model=model, **extra)
        return {item["key"]: item["count"] for item in data["values"]}

    # -- 14.42 ---------------------------------------------------------
    def test_14_42_counts_follow_the_other_filters_not_the_columns_own(self):
        """
        Scenario 14.42: narrowing
        Given: five rows across three statuses and two categories
        When: status values are asked for under a status filter, then under a category filter
        Then: the status filter is ignored for its own list; the category filter narrows it
        """
        own = {"status": {"filterType": "set", "values": [QUERY_STATUS_ARCHIVED]}}
        self.assertEqual(
            self._counts("status", own),
            {QUERY_STATUS_ACTIVE: 3, QUERY_STATUS_ARCHIVED: 1, QUERY_STATUS_DRAFT: 1},
            "a column must not narrow its own list",
        )
        other = {"category": {"filterType": "set", "values": [str(self.beta.pk)]}}
        self.assertEqual(
            self._counts("status", other),
            {QUERY_STATUS_ACTIVE: 1, QUERY_STATUS_DRAFT: 1},
            "another column's filter must narrow the list",
        )

    # -- 14.43 ---------------------------------------------------------
    def test_14_43_keys_and_labels_per_type(self):
        """
        Scenario 14.43: what a value list shows
        Given: choice, foreign-key, boolean, date and integer columns, one blank row
        When: each column's values are asked for
        Then: keys are the wire form and labels are readable
        """
        status_items = {i["key"]: i["label"] for i in self._values("status")["values"]}
        self.assertEqual(status_items[QUERY_STATUS_ACTIVE], "Active", "choices use their labels")

        categories = {i["key"]: (i["label"], i["count"]) for i in self._values("category")["values"]}
        self.assertEqual(categories[str(self.alpha.pk)], ("QCat<alpha>", 2), "a foreign key shows str(related)")
        self.assertEqual(categories[None], ("(Blank)", 1), "blank is listed with its own label")

        flags = {i["key"]: i["label"] for i in self._values("is_active")["values"]}
        self.assertEqual(flags, {"true": "Yes", "false": "No"}, "booleans are true/false keys, Yes/No labels")

        self.assertEqual(self._counts("created_on")["2026-02-20"], 2, "a date key is ISO")
        self.assertEqual(self._counts("count")["5"], 1, "an integer key is its string form")

    # -- 14.44 ---------------------------------------------------------
    def test_14_44_a_ticked_value_the_other_filters_removed_stays_at_zero(self):
        """
        Scenario 14.44: a ticked value can still be unticked
        Given: a category filter that leaves no archived row
        When: status values are asked for with "archived" ticked
        Then: archived is listed with count 0
        """
        other = {"category": {"filterType": "set", "values": [str(self.beta.pk)]}}
        counts = self._counts("status", other, selected=[QUERY_STATUS_ARCHIVED])
        self.assertEqual(counts.get(QUERY_STATUS_ARCHIVED), 0, "a ticked value must stay listed")

    # -- 14.45 ---------------------------------------------------------
    def test_14_45_search_matches_labels(self):
        """
        Scenario 14.45: searching a long list
        Given: text and foreign-key columns
        When: values are searched
        Then: text matches its values; a foreign key matches its labels
        """
        self.assertEqual(set(self._counts("name", search="MID")), {"alpha-mid", "beta-mid"}, "case-insensitive")
        self.assertEqual(set(self._counts("category", search="bet")), {str(self.beta.pk)}, "matched on str(related)")

    # -- 14.46 ---------------------------------------------------------
    def test_14_46_limit_cuts_the_list_and_says_so(self):
        """
        Scenario 14.46: a long list is cut, and a ticked value survives the cut
        Given: five distinct names
        When: two are asked for, then two with "loose" ticked
        Then: two come back with truncated=true; the ticked one is added after the cut
        """
        data = self._values("name", limit=2)
        self.assertEqual(len(data["values"]), 2, "the limit applies")
        self.assertTrue(data["truncated"], "a cut list must say so")
        ticked = {i["key"] for i in self._values("name", limit=2, selected=["loose"])["values"]}
        self.assertIn("loose", ticked, "a ticked value is never cut away")

    # -- 14.47 ---------------------------------------------------------
    def test_14_47_values_are_sorted_by_label_with_blank_last(self):
        """
        Scenario 14.47: a predictable order
        Given: two categories and one blank
        When: category values are asked for
        Then: labels ascend, case-insensitively, and blank comes last
        """
        labels = [i["label"] for i in self._values("category")["values"]]
        self.assertEqual(labels, ["QCat<alpha>", "QCat<beta>", "(Blank)"])

    # -- 14.48 ---------------------------------------------------------
    def test_14_48_only_the_lists_own_columns_can_be_listed(self):
        """
        Scenario 14.48: what is not filterable
        Given: the item model
        When: an unknown field, a JSON field, a related path or a serializer-only field is asked for
        Then: each is a 400 "is not filterable"
        """
        for field in ("nope", "metadata", "category__name", "lex_reserved_scopes", ""):
            resp = self._post({"field": field, "filterModel": {}})
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, f"{field!r}: {resp.data}")
            self.assertIn("not filterable", str(resp.data), field)

    # -- 14.49 ---------------------------------------------------------
    def test_14_49_as_of_lists_the_values_of_that_moment(self):
        """
        Scenario 14.49: time travel
        Given: a row whose status changed after a recorded instant
        When: status values are asked for as of that instant
        Then: the earlier status is counted, not the current one
        """
        time.sleep(0.05)
        moment = timezone.now()
        time.sleep(0.05)
        row = QueryItem.objects.get(name="beta-mid")
        row.status = QUERY_STATUS_ARCHIVED
        row.save()

        self.assertEqual(
            self._counts("status"),
            {QUERY_STATUS_ACTIVE: 3, QUERY_STATUS_ARCHIVED: 2},
            "now: the draft row is archived",
        )
        query = "?" + urllib.parse.urlencode({"as_of": moment.isoformat()})
        self.assertEqual(
            self._counts("status", query=query),
            {QUERY_STATUS_ACTIVE: 3, QUERY_STATUS_ARCHIVED: 1, QUERY_STATUS_DRAFT: 1},
            "as of the instant: still a draft",
        )

    # -- 14.50 ---------------------------------------------------------
    def test_14_50_a_bad_key_in_another_filter_is_a_400(self):
        """
        Scenario 14.50: the other filters are translated like the list's
        Given: an integer filter with a key that is not an integer
        When: another column's values are asked for under it
        Then: 400 naming the integer field
        """
        resp = self._post({"field": "status", "filterModel": {"count": {"filterType": "set", "values": ["x"]}}})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("count", str(resp.data))

    # -- 14.51 ---------------------------------------------------------
    def test_14_51_the_database_count_and_the_per_row_count_agree(self):
        """
        Scenario 14.51: two ways to count, one answer
        Given: a model whose read permission allows every field
        When: values are counted per row (overridden permission) and in the database
        Then: the answers are identical
        """
        other = {"is_active": {"filterType": "set", "values": ["true"]}}
        per_row = self._values("category", other)
        with patch(
            "lex.api.views.model_entries.FilterValues.uses_default_read_permission",
            return_value=True,
        ):
            in_database = self._values("category", other)
        self.assertEqual(per_row, in_database)

    # -- 14.53 ---------------------------------------------------------
    def test_14_53_a_ticked_foreign_key_never_borrows_a_hidden_rows_name(self):
        """
        Scenario 14.53: labels respect visibility, not just existence
        Given: one category a readable row carries, one only a masked row carries, one no row carries
        When: category values are asked for with all three ticked, under a filter matching no rows
        Then: all three are listed at 0; only the readable category's real name appears anywhere
        """
        nothing = {"visible": {"filterType": "set", "values": []}}
        phantom_pk = str(self.beta.pk + self.gamma.pk + 10_000)
        data = self._values(
            "category", nothing,
            selected=[str(self.beta.pk), str(self.gamma.pk), phantom_pk],
            model=MASKED_ITEM,
        )
        by_key = {item["key"]: item for item in data["values"]}
        self.assertEqual(by_key[str(self.beta.pk)]["count"], 0, "no row survives the empty filter")
        self.assertEqual(by_key[str(self.gamma.pk)]["count"], 0, "no row survives the empty filter")
        self.assertEqual(by_key[phantom_pk]["count"], 0, "no row survives the empty filter")
        self.assertEqual(
            by_key[str(self.beta.pk)]["label"], "QCat<beta>",
            "a readable row's category must be named",
        )
        self.assertEqual(
            by_key[str(self.gamma.pk)]["label"], str(self.gamma.pk),
            "only a masked row carries gamma -- its label must fall back to the key",
        )
        self.assertEqual(
            by_key[phantom_pk]["label"], phantom_pk,
            "no row carries this key at all -- its label must fall back to the key",
        )
        self.assertNotIn("QCat<gamma>", str(data), "a masked row's category name must never appear")

    # -- 14.54 ---------------------------------------------------------
    # (see TestCluster14g_SerializerDependentRefusals below -- needs a custom
    # serializer, so it shares that class's fixture with 14.52.)

    # -- 14.55 ---------------------------------------------------------
    def test_14_55_no_permission_methods_at_all_uses_the_database_path(self):
        """
        Scenario 14.55: nothing to check means nothing to scan
        Given: a model that overrides neither permission_read nor can_read
        When: its status values are asked for
        Then: the counts match a plain tally of the rows, and no warning is logged
        """
        self.assertTrue(
            uses_default_read_permission(QueryOpenItem),
            "a model with no permission methods at all must use the database path",
        )
        with patch("lex.api.views.model_entries.FilterValues.logger.warning") as mock_warning:
            counts = self._counts("status", model=OPEN_ITEM)
        mock_warning.assert_not_called()
        self.assertEqual(
            counts, {QUERY_STATUS_ACTIVE: 2, QUERY_STATUS_ARCHIVED: 1},
            "counts must match a plain tally of the rows",
        )

    # -- 14.56 ---------------------------------------------------------
    def test_14_56_can_read_masking_is_honoured_even_with_permission_read_open(self):
        """
        Scenario 14.56: can_read is still checked, even though it is not permission_read
        Given: a model whose permission_read is permissive but can_read hides category on some rows
        When: category values are asked for
        Then: the per-row path is used, and the masked row's category is not counted
        """
        self.assertFalse(
            uses_default_read_permission(QueryMaskedItem),
            "can_read is overridden, so the database path must not be used",
        )
        counts = self._counts("category", model=MASKED_ITEM)
        self.assertEqual(counts.get(str(self.beta.pk)), 1, "a visible row's category must be counted")
        self.assertNotIn(
            str(self.gamma.pk), counts,
            "a masked row's category must not be counted at all",
        )

    # -- 14.57 ---------------------------------------------------------
    def test_14_57_continuous_columns_have_no_value_list(self):
        """
        Scenario 14.57: no value list for continuous values
        Given: decimal, float and date-time columns
        When: each is asked for
        Then: each is a 400 "is not filterable"
        """
        for field in ("amount", "ratio", "created_at_ts"):
            resp = self._post({"field": field, "filterModel": {}})
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, f"{field!r}: {resp.data}")
            self.assertIn("not filterable", str(resp.data), field)

    # -- 14.58 ---------------------------------------------------------
    def test_14_58_get_and_head_are_not_allowed(self):
        """
        Scenario 14.58: the endpoint is POST-only
        Given: the filter_values URL
        When: GET and HEAD are sent
        Then: both are 405
        """
        url = self.url_filter_values(ITEM)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_405_METHOD_NOT_ALLOWED, "GET must be 405")
        self.assertEqual(self.client.head(url).status_code, status.HTTP_405_METHOD_NOT_ALLOWED, "HEAD must be 405")

    # -- 14.59 ---------------------------------------------------------
    def test_14_59_truncation_and_limit_normalisation(self):
        """
        Scenario 14.59: truncation on both counting paths, and limit normalisation
        Given: a scan limit smaller than the distinct values, a row-scan limit smaller than the rows,
               and out-of-range limit values
        Then: truncated is true in both cases; a limit above 1000 is clamped, a non-integer is 200
        """
        with override_settings(LEX_FILTER_VALUES_SCAN_LIMIT=2):
            data = self._values("name", model=OPEN_ITEM)
        self.assertTrue(data["truncated"], "a small scan limit must truncate the database path")
        self.assertLessEqual(len(data["values"]), 2, "the database path must stop at the scan limit")

        with override_settings(LEX_FILTER_VALUES_ROW_SCAN_LIMIT=2):
            data = self._values("status")  # QueryItem: naturally per-row, 5 rows > 2
        self.assertTrue(data["truncated"], "a small row-scan limit must truncate the per-row path")

        self.assertEqual(_bounded_limit(5000), 1000, "a limit above 1000 must be clamped to 1000")
        self.assertEqual(_bounded_limit("not-a-number"), 200, "a non-integer limit must default to 200")

        # End-to-end: neither extreme crashes the request.
        for bad_limit in (5000, "not-a-number"):
            resp = self._post({"field": "name", "filterModel": {}, "limit": bad_limit}, model=OPEN_ITEM)
            self.assertEqual(resp.status_code, status.HTTP_200_OK, f"limit={bad_limit!r}: {resp.data}")

    # -- 14.60 ---------------------------------------------------------
    def test_14_60_default_permission_identity_and_database_path_faceting(self):
        """
        Scenario 14.60: the default-permission identity, and faceting on the database path
        Given: a LexModel subclass that overrides neither method, and QueryOpenItem's rows
        When: status values are asked for under a name facet filter, unpatched
        Then: the identity check holds, and the facet narrows the database-path answer correctly
        """

        class _UntouchedPermissionModel(LexModel):
            class Meta:
                app_label = "lex_app"
                abstract = True

        self.assertTrue(
            uses_default_read_permission(_UntouchedPermissionModel),
            "a LexModel subclass overriding neither permission_read nor can_read must use "
            "the database path",
        )

        other = {"name": {"filterType": "set", "values": ["open-a", "open-c"]}}
        counts = self._counts("status", other, model=OPEN_ITEM)
        self.assertEqual(
            counts, {QUERY_STATUS_ACTIVE: 1, QUERY_STATUS_ARCHIVED: 1},
            "the facet filter must narrow the database-path answer",
        )

    # -- 14.61 ---------------------------------------------------------
    def test_14_61_blank_folds_empty_string_and_null_together(self):
        """
        Scenario 14.61: one blank bucket, not two
        Given: a plain text column with both a NULL row and an empty-string row
        When: its values are asked for, and the list is asked to filter on blank
        Then: filter_values reports one "(Blank)" entry with the summed count; the list's own set
              filter matches both kinds of row whichever representation ("" or null) is ticked
        """
        QueryItem.objects.filter(name="alpha-small").update(note="")
        QueryItem.objects.filter(name="alpha-mid").update(note=None)
        QueryItem.objects.filter(name="beta-mid").update(note="reviewed")
        QueryItem.objects.filter(name="beta-big").update(note="reviewed")
        QueryItem.objects.filter(name="loose").update(note="flagged")

        data = self._values("note")
        counts = {i["key"]: i["count"] for i in data["values"]}
        self.assertEqual(counts.get(None), 2, "an empty string and NULL are one blank bucket")
        labels = {i["key"]: i["label"] for i in data["values"]}
        self.assertEqual(labels[None], "(Blank)", "the blank bucket is labelled (Blank)")
        self.assertEqual(counts.get("reviewed"), 2, "a real value is counted on its own")

        for ticked in (None, ""):
            resp = self.client.post(
                self.url_list(ITEM),
                data={"filterModel": {"note": {"filterType": "set", "values": [ticked]}}},
                format="json",
            )
            self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
            names = {row["name"] for row in resp.data["rowData"]}
            self.assertEqual(
                names, {"alpha-small", "alpha-mid"},
                f"ticking {ticked!r} must match both blank rows (NULL and empty string)",
            )

    # -- 14.62 ---------------------------------------------------------
    def test_14_62_integer_columns_sort_numerically_not_lexicographically(self):
        """
        Scenario 14.62: 2 before 10
        Given: counts of 2 and 10 on an integer column
        When: its values are asked for
        Then: they come back in numeric order, not lexicographic ("10" before "2") order
        """
        QueryItem.objects.filter(name="alpha-small").update(count=2)
        QueryItem.objects.filter(name="alpha-mid").update(count=10)
        keys = [i["key"] for i in self._values("count")["values"]]
        self.assertEqual(
            keys, ["0", "2", "7", "9", "10"],
            f"integer keys must sort numerically, got {keys!r}",
        )

    # -- 14.63 ---------------------------------------------------------
    def test_14_63_selected_keys_are_normalised_capped_and_search_filtered(self):
        """
        Scenario 14.63: selected keys are treated like stored keys
        Given: a boolean column, a request selecting more than 1000 keys, and an active search
        When: values are asked for
        Then: a JSON true selects the "true" bucket; only the first 1000 selected keys are honoured;
              under a search a ticked value absent from the list is added only if its label matches
        """
        data = self._values("is_active", selected=[True])
        keys = {i["key"] for i in data["values"]}
        self.assertIn("true", keys, 'a JSON true must normalise to the "true" key, not "True"')

        over_limit = [f"nonexistent-{i}" for i in range(1005)]
        resp = self._post({"field": "name", "filterModel": {}, "selected": over_limit})
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        returned = {i["key"] for i in resp.data["values"]}
        self.assertIn("nonexistent-0", returned, "a selected key within the first 1000 must be honoured")
        self.assertNotIn(
            "nonexistent-1004", returned,
            "a selected key past the first 1000 must not be honoured",
        )

        other = {"category": {"filterType": "set", "values": [str(self.beta.pk)]}}
        data = self._values(
            "status", other, search="arch", selected=[QUERY_STATUS_DRAFT, QUERY_STATUS_ARCHIVED],
        )
        statuses = {i["key"]: i["label"] for i in data["values"]}
        self.assertNotIn(
            QUERY_STATUS_DRAFT, statuses,
            "a ticked value whose label does not match the search must not be added back",
        )
        self.assertIn(
            QUERY_STATUS_ARCHIVED, statuses,
            "a ticked value whose label matches the search must be added back",
        )
        self.assertEqual(statuses[QUERY_STATUS_ARCHIVED], "Archived")


class _QueryItemAllFieldsSerializer(drf_serializers.ModelSerializer):
    """Exposes every ``QueryItem`` column via ``fields = "__all__"``, including
    credential-shaped ones -- the exact shape LEX-702 guards against, used here
    to prove ``filter_values`` refuses them independently of the serializer.
    Also masks ``status`` behind a ``SerializerMethodField`` and renames
    ``name`` under ``renamed_name``, to exercise the serializer-field-source
    check.
    """

    status = drf_serializers.SerializerMethodField()
    renamed_name = drf_serializers.CharField(source="name", read_only=True)

    class Meta:
        model = QueryItem
        fields = "__all__"

    def get_status(self, obj):
        return f"computed:{obj.status}"


class TestCluster14g_SerializerDependentRefusals(E2ETestCase):
    """Cluster 14g: refusals that depend on what a CUSTOM serializer exposes.

    ``QueryItem.api_serializers`` is mutated for the duration of this class
    only (installed before ``super().setUp()`` rebuilds the URLconf's model
    containers, restored on teardown) so the other test class's fixtures
    never see it.
    """

    e2e_models = ALL_MODELS

    def setUp(self):
        self._had_api_serializers = hasattr(QueryItem, "api_serializers")
        self._original_api_serializers = getattr(QueryItem, "api_serializers", None)
        QueryItem.api_serializers = {"all_fields": _QueryItemAllFieldsSerializer}
        super().setUp()
        self.row = QueryItem.objects.create(name="x", api_key="super-secret-value-123")

    def tearDown(self):
        super().tearDown()
        if self._had_api_serializers:
            QueryItem.api_serializers = self._original_api_serializers
        elif hasattr(QueryItem, "api_serializers"):
            del QueryItem.api_serializers

    def _post(self, body: dict, query: str = "?serializer=all_fields"):
        return self.client.post(self.url_filter_values(ITEM) + query, data=body, format="json")

    # -- 14.52 ---------------------------------------------------------
    def test_14_52_a_credential_shaped_column_is_refused_even_behind_fields_all(self):
        """
        Scenario 14.52: the sensitive-name floor applies here too
        Given: a custom serializer that publishes every column, including api_key, via fields = "__all__"
        When: api_key values are asked for
        Then: 400 "is not filterable", and the stored secret never appears in the response
        """
        resp = self._post({"field": "api_key", "filterModel": {}})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertIn("not filterable", str(resp.data))
        self.assertNotIn("super-secret-value-123", str(resp.content), "a stored secret must never appear")

    # -- 14.54 ---------------------------------------------------------
    def test_14_54_a_serializer_field_whose_source_is_another_column_is_refused(self):
        """
        Scenario 14.54: the grid shows the serializer's field, not the raw column
        Given: a SerializerMethodField named "status" and a field "renamed_name" sourced from "name"
        When: each is asked for
        Then: both are 400 "is not filterable"; an ordinary same-name field still works
        """
        for field in ("status", "renamed_name"):
            resp = self._post({"field": field, "filterModel": {}})
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, f"{field!r}: {resp.data}")
            self.assertIn("not filterable", str(resp.data), field)

        # Positive control: an ordinary field on the same custom serializer still works.
        resp = self._post({"field": "name", "filterModel": {}})
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
