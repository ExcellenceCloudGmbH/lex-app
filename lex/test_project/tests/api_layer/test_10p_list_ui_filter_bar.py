"""The filter bar's default columns travel with the list's other options.

Intent: a developer names the columns a list shows in its filter bar, on the
serializer's Meta or, for a list without a custom serializer, on the model. The
frontend reads them from list_ui. A name that is not a value-list column must be
dropped with a warning, never sent, so the bar cannot offer a filter the
filter_values endpoint would refuse; a filter_bar that is not a list of names
gives no bar and one warning, never a failed fields request. The fields endpoint
also says, per column, whether a value list exists, decided by the same resolver
as filter_values, so the frontend offers Set and Multi lists only where they
work.
Cluster 10p — scenarios 10.91–10.95. Type: U.
Covers: lex/api/serializers/base_serializers.py (``get_list_ui_options``,
``_resolve_filter_bar``), lex/api/views/model_info/Fields.py (``create_list_ui_info``,
``Fields.get``'s ``value_list``), lex/api/utils/filterable_fields.py (``value_list_field``).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10p_list_ui_filter_bar.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import SimpleTestCase
from rest_framework import serializers as drf_serializers

from lex.api.serializers.base_serializers import LexSerializer, get_serializer_map_for_model
from lex.api.utils.filterable_fields import value_list_field
from lex.api.views.model_entries.mixins.ModelEntryProviderMixin import UserModelSerializer
from lex.api.views.model_info.Fields import Fields, create_list_ui_info
from lex.audit_logging.models.AuditLog import AuditLog

from .models import FilterBarColumnItem, SchemaItem, ValueListItem, ValueListOptOutItem

pytestmark = pytest.mark.api_layer


class _ColumnsAsWritten(drf_serializers.ModelSerializer):
    """Publishes the credential-shaped api_key, makes count write-only, and
    shows name a second time under another field name (headline)."""

    headline = drf_serializers.CharField(source="name", read_only=True)

    class Meta:
        model = ValueListItem
        fields = ["id", "name", "headline", "count", "api_key"]
        extra_kwargs = {"count": {"write_only": True}}


def _value_list_flags(model, serializer_name: str = "default") -> list:
    """``(name, value_list)`` per descriptor of ``GET .../fields`` for ``model``, in order.

    The flag also asks the filter allow-list (LEX_SEC_FILTERS) whether the caller
    may filter by the column; the allow-list reads the model's serializers from
    its process-admin container, which this test builds itself, so the
    allow-list is pointed at the same container.
    """
    container = SimpleNamespace(
        model_class=model,
        get_serializers_map=lambda: get_serializer_map_for_model(model),
    )
    request = SimpleNamespace(query_params={"serializer": serializer_name})
    with patch("lex.api.filter_policy._container", lambda model_class: container if model_class is model else None):
        response = Fields().get(request, model_container=container)
    return [(info["name"], info["value_list"]) for info in response.data["fields"]]


class TestCluster10p_ListUiFilterBar(SimpleTestCase):
    """Cluster 10p: list_ui.filter_bar."""

    def tearDown(self):
        if "filter_bar" in SchemaItem.__dict__:
            del SchemaItem.filter_bar
        super().tearDown()

    # -- 10.91 ---------------------------------------------------------
    def test_10_91_the_serializer_meta_names_the_bar(self):
        """
        Scenario 10.91: declared on the serializer
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

    # -- 10.92 ---------------------------------------------------------
    def test_10_92_a_model_attribute_is_the_fallback(self):
        """
        Scenario 10.92: declared on the model
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

    # -- 10.93 ---------------------------------------------------------
    def test_10_93_names_that_are_not_value_list_columns_are_dropped(self):
        """
        Scenario 10.93: only columns filter_values accepts
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

    # -- 10.94 ---------------------------------------------------------
    def test_10_94_the_fields_endpoint_says_which_columns_have_a_value_list(self):
        """
        Scenario 10.94: value_list on every field descriptor
        Given: a model with a char, integer, boolean, date, foreign-key and choice column, a
               TextField, a decimal, a JSON column and a credential-shaped name; a serializer that
               publishes the credential-shaped name, makes a column write-only and shows a column
               under another field name; a model and AuditLog that opt out; auth.User, whose list
               and filter_values always render through UserModelSerializer's allowlist regardless
               of the requested serializer name
        When: GET .../fields is answered for each serializer
        Then: value_list is true exactly for the char, integer, boolean, date, foreign-key and choice
              columns; false for the TextField, decimal, JSON, credential-shaped, write-only and
              renamed fields; false for every column of a model that opts out; for auth.User every
              flag agrees with what filter_values would accept through UserModelSerializer, with
              is_active, is_staff and is_superuser false on both sides
        """
        flags = dict(_value_list_flags(ValueListItem))
        for name in ("name", "count", "active", "day", "target", "status"):
            self.assertIs(flags[name], True, f"{name} has a value list")
        for name in ("notes", "amount", "payload"):
            self.assertIs(flags[name], False, f"{name} has no value list")
        self.assertNotIn("api_key", flags, "premise: the default serializer never publishes api_key")

        with patch.object(ValueListItem, "api_serializers", {"as_written": _ColumnsAsWritten}, create=True):
            custom = _value_list_flags(ValueListItem, "as_written")
        by_name = dict(custom)
        self.assertIs(by_name["api_key"], False, "a credential-shaped name has no value list, even published")
        self.assertIs(by_name["count"], False, "a write-only field has no value list")
        # The fields endpoint names a field after its source column, so the
        # renamed headline field is the second descriptor called "name".
        self.assertEqual(
            [flag for name, flag in custom if name == "name"], [True, False],
            "name itself has a value list; the field showing it under another name does not",
        )

        for model in (ValueListOptOutItem, AuditLog):
            with self.subTest(model=model.__name__):
                opted_out = _value_list_flags(model)
                self.assertTrue(opted_out, "premise: the model has columns")
                self.assertEqual(
                    [name for name, flag in opted_out if flag], [],
                    "a model with lex_value_lists = False has no value list on any column",
                )

        with self.subTest(model="auth.User"):
            # auth.User always renders through UserModelSerializer's allowlist
            # (LEX-702), not whatever the "default" auto serializer map holds --
            # the fields endpoint must compute value_list against that same
            # choice, or the flag can say true for a column filter_values 400s.
            user_flags = dict(_value_list_flags(User))
            list_serializer = UserModelSerializer()
            for name, flag in user_flags.items():
                with self.subTest(field=name):
                    self.assertIs(
                        flag, value_list_field(list_serializer, User, name) is not None,
                        f"{name}: the fields flag disagrees with what filter_values would accept",
                    )
            for name in ("is_active", "is_staff", "is_superuser"):
                self.assertIs(
                    user_flags[name], False,
                    f"{name} is not a UserModelSerializer column and must not offer a value list",
                )

    # -- 10.95 ---------------------------------------------------------
    def test_10_95_a_filter_bar_that_is_not_a_list_gives_no_bar(self):
        """
        Scenario 10.95: filter_bar must be a list of names
        Given: filter_bar = True on the model, and a model with a column named filter_bar (so the
               model attribute is that column)
        When: list options are built
        Then: the bar is empty, with exactly one warning each, rather than a failed request
        """

        class Plain(LexSerializer):
            class Meta:
                model = SchemaItem
                fields = ["id", "name"]

        class WithColumn(LexSerializer):
            class Meta:
                model = FilterBarColumnItem
                fields = ["id", "name", "filter_bar"]

        SchemaItem.filter_bar = True
        for serializer in (Plain, WithColumn):
            with self.subTest(serializer=serializer.__name__):
                with self.assertLogs("lex.api.serializers.base_serializers", level="WARNING") as logs:
                    bar = create_list_ui_info(serializer)["filter_bar"]
                self.assertEqual(bar, [], "a filter_bar that is not a list of names gives no bar")
                self.assertEqual(len(logs.output), 1, f"exactly one warning: {logs.output!r}")
