"""The filter bar's default columns travel with the list's other options.

Intent: a developer names the columns a list shows in its filter bar, on the
serializer's Meta or, for a list without a custom serializer, on the model. The
frontend reads them from list_ui. A name that is not a value-list column must be
dropped with a warning, never sent, so the bar cannot offer a filter the
filter_values endpoint would refuse.
Cluster 10o — scenarios 10.83–10.85. Type: U.
Covers: lex/api/serializers/base_serializers.py (``get_list_ui_options``,
``_resolve_filter_bar``), lex/api/views/model_info/Fields.py (``create_list_ui_info``).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10o_list_ui_filter_bar.py -v
"""

from __future__ import annotations

import pytest
from django.test import SimpleTestCase

from lex.api.serializers.base_serializers import LexSerializer
from lex.api.views.model_info.Fields import create_list_ui_info

from .models import SchemaItem

pytestmark = pytest.mark.api_layer


class TestCluster10o_ListUiFilterBar(SimpleTestCase):
    """Cluster 10o: list_ui.filter_bar."""

    def tearDown(self):
        if "filter_bar" in SchemaItem.__dict__:
            del SchemaItem.filter_bar
        super().tearDown()

    # -- 10.83 ---------------------------------------------------------
    def test_10_83_the_serializer_meta_names_the_bar(self):
        """
        Scenario 10.83: declared on the serializer
        Given: a serializer whose Meta sets filter_bar
        When: its list options are built
        Then: list_ui.filter_bar carries those columns, in order
        """

        class WithBar(LexSerializer):
            class Meta:
                model = SchemaItem
                fields = ["id", "name", "active"]
                filter_bar = ["active", "name"]

        self.assertEqual(create_list_ui_info(WithBar)["filter_bar"], ["active", "name"])

    # -- 10.84 ---------------------------------------------------------
    def test_10_84_a_model_attribute_is_the_fallback(self):
        """
        Scenario 10.84: declared on the model
        Given: no filter_bar on the serializer, filter_bar on the model
        When: list options are built
        Then: the model's columns are used; with neither, the bar is empty
        """

        class Plain(LexSerializer):
            class Meta:
                model = SchemaItem
                fields = ["id", "name", "day"]

        self.assertEqual(create_list_ui_info(Plain)["filter_bar"], [], "no declaration, no bar")
        SchemaItem.filter_bar = ["day"]
        self.assertEqual(create_list_ui_info(Plain)["filter_bar"], ["day"])

    # -- 10.85 ---------------------------------------------------------
    def test_10_85_names_that_are_not_value_list_columns_are_dropped(self):
        """
        Scenario 10.85: only columns filter_values accepts
        Given: filter_bar naming an unknown field, a JSON field, a related path and a real column twice
        When: list options are built
        Then: only the real column is kept, once, and each drop is logged
        """

        class Messy(LexSerializer):
            class Meta:
                model = SchemaItem
                fields = ["id", "name", "payload"]
                filter_bar = ["nope", "payload", "name", "fk__label", "name"]

        with self.assertLogs("lex.api.serializers.base_serializers", level="WARNING") as logs:
            bar = create_list_ui_info(Messy)["filter_bar"]
        self.assertEqual(bar, ["name"])
        self.assertEqual(len(logs.output), 3, f"one warning per dropped name: {logs.output!r}")
