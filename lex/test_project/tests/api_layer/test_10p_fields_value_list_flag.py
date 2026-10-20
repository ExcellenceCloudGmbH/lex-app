"""The fields endpoint says which columns have a value list, and offers no filter bar.

Intent: every field descriptor of GET api/model_info/<model>/fields carries
value_list, decided by the same resolver as filter_values for the serializer the
list renders through, so the frontend offers Set and Multi lists only where they
work. The grid's own column filters are the only filter UI (spec Amendment 18):
list_ui carries no filter_bar, even for a model or serializer that still
declares one, so no client is told to draw a bar.
Cluster 10p — scenarios 10.91–10.92. Type: U.
Covers: lex/api/views/model_info/Fields.py (``Fields.get``'s ``value_list`` and
``list_ui``, ``create_list_ui_info``), lex/api/serializers/base_serializers.py
(``get_list_ui_options``), lex/api/utils/filterable_fields.py (``value_list_field``).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10p_fields_value_list_flag.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import SimpleTestCase
from rest_framework import serializers as drf_serializers

from lex.api.serializers.base_serializers import get_serializer_map_for_model
from lex.api.utils.filterable_fields import value_list_field
from lex.api.views.model_entries.mixins.ModelEntryProviderMixin import UserModelSerializer
from lex.api.views.model_info.Fields import Fields
from lex.audit_logging.models.AuditLog import AuditLog

from .models import ValueListItem, ValueListOptOutItem

pytestmark = pytest.mark.api_layer


class _ColumnsAsWritten(drf_serializers.ModelSerializer):
    """Publishes the credential-shaped api_key, makes count write-only, and
    shows name a second time under another field name (headline)."""

    headline = drf_serializers.CharField(source="name", read_only=True)

    class Meta:
        model = ValueListItem
        fields = ["id", "name", "headline", "count", "api_key"]
        extra_kwargs = {"count": {"write_only": True}}


class _DeclaresFilterBar(drf_serializers.ModelSerializer):
    """A project serializer that still names filter-bar columns in its Meta."""

    class Meta:
        model = ValueListItem
        fields = ["id", "name", "status", "active"]
        filter_bar = ["status", "active"]


def _get_fields(model, serializer_name: str = "default"):
    """The response of ``GET .../fields?serializer=<serializer_name>`` for ``model``.

    The value_list flag also asks the filter allow-list (LEX_SEC_FILTERS) whether
    the caller may filter by the column; the allow-list reads the model's
    serializers from its process-admin container, which this test builds itself,
    so the allow-list is pointed at the same container.
    """
    container = SimpleNamespace(
        model_class=model,
        get_serializers_map=lambda: get_serializer_map_for_model(model),
    )
    request = SimpleNamespace(query_params={"serializer": serializer_name})
    with patch("lex.api.filter_policy._container", lambda model_class: container if model_class is model else None):
        return Fields().get(request, model_container=container)


def _value_list_flags(model, serializer_name: str = "default") -> list:
    """``(name, value_list)`` per descriptor of ``GET .../fields`` for ``model``, in order."""
    response = _get_fields(model, serializer_name)
    return [(info["name"], info["value_list"]) for info in response.data["fields"]]


class TestCluster10p_FieldsValueListFlag(SimpleTestCase):
    """Cluster 10p: the fields endpoint's value_list flag, and no filter bar in list_ui."""

    # -- 10.91 ---------------------------------------------------------
    def test_10_91_the_fields_endpoint_says_which_columns_have_a_value_list(self):
        """
        Scenario 10.91: value_list on every field descriptor
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

    # -- 10.92 ---------------------------------------------------------
    def test_10_92_list_ui_has_no_filter_bar(self):
        """
        Scenario 10.92: the grid's own column filters are the only filter UI (Amendment 18).
        Given: a model and serializer that still declare filter_bar
        When: the fields endpoint is read
        Then: list_ui carries no filter_bar key, and every field still carries value_list
        """
        with patch.object(ValueListItem, "filter_bar", ["name", "count"], create=True), patch.object(
            ValueListItem, "api_serializers", {"with_bar": _DeclaresFilterBar}, create=True,
        ):
            # "default" is the generated serializer, so the model's filter_bar is the
            # only declaration; "with_bar" declares Meta.filter_bar as well.
            for serializer_name in ("default", "with_bar"):
                with self.subTest(serializer=serializer_name):
                    resp = _get_fields(ValueListItem, serializer_name)
                    self.assertEqual(resp.status_code, 200)
                    list_ui = resp.data["list_ui"]
                    self.assertNotIn("filter_bar", list_ui, "list_ui must not offer a filter bar")
                    self.assertTrue(resp.data["fields"], "premise: the serializer has fields")
                    self.assertTrue(
                        all("value_list" in f for f in resp.data["fields"]),
                        "every field still says whether it has a value list",
                    )
