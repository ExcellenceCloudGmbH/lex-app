"""
Sub-cluster 10r — ``model_info`` ships the project's per-column display options.

Intent: LEX-1556. The frontend learns everything it knows about a table from
``/api/<model>/fields/?serializer=…`` before it draws a single column. A
project that wants its identifier column narrower — or gone — has nowhere to
say so unless that endpoint carries the answer, so the serializer-side
contract (cluster 12p) is only half the feature. This file is the other half:
what a frontend actually receives.

Why it matters for customers
----------------------------
A customer asked for the ID column to be narrower. The durable answer is that
the project writes it down once on its serializer and every user of that model
gets it — without each of them resizing a column and saving a view, and
without it being lost the moment someone opens a read-only System View.

Two properties are worth a scenario each:

* the declaration REACHES the frontend, keyed by field name, for both kinds of
  serializer the endpoint serves (``LexSerializer`` and a project's plain DRF
  class — the endpoint must not expose the difference);
* the endpoint keeps answering when the declaration is nonsense. This payload
  is on the critical path for rendering ANY table, so a project's typo must
  cost them that option, never the grid.

No database: ``Fields.get`` walks ``serializer.fields`` and ``model._meta``,
both hand-rolled here as the sibling 10i scenarios do.

Scenarios 10.100 – 10.103.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.test import SimpleTestCase

from lex.api.serializers import LexSerializer
from lex.api.views.model_info.Fields import Fields, create_list_ui_info

pytestmark = pytest.mark.api_layer


def _fake_model(model_name="widget", pk_name="id"):
    pk = SimpleNamespace(name=pk_name)
    return SimpleNamespace(
        _meta=SimpleNamespace(model_name=model_name, pk=pk, get_field=None)
    )


def _fake_request():
    request = MagicMock()
    request.query_params = {}
    return request


def _fake_container(serializer_class):
    container = MagicMock()
    container.get_serializers_map.return_value = {"default": serializer_class}
    return container


def _list_ui_for(serializer_class, *, model_name="widget"):
    """Drive the real endpoint and return the ``list_ui`` a frontend reads."""
    container = _fake_container(serializer_class)
    container.model_class = _fake_model(model_name)
    response = Fields().get(_fake_request(), model_container=container)
    return response.data["list_ui"]


class TestCluster10r_ListUiColumnOptions(SimpleTestCase):
    # 10.100 ----------------------------------------------------------
    def test_10_100_declared_column_options_reach_the_frontend(self):
        """What the project wrote on its serializer is what the grid is told.

        The narrow ID column the customer asked for is this value arriving:
        a cap the frontend applies to the automatic sizing pass. If the key
        is dropped in transit the grid silently goes on measuring content,
        which is the behaviour being complained about.
        """

        class WidgetSerializer(LexSerializer):
            class Meta:
                lex_column_options = {
                    "id": {"max_width": 90},
                    "internal_ref": {"hidden": True},
                }

        WidgetSerializer.fields = {}

        list_ui = _list_ui_for(WidgetSerializer)

        self.assertEqual(
            list_ui["columns"],
            {"id": {"max_width": 90}, "internal_ref": {"hidden": True}},
        )
        # The older half of the payload is untouched — one reader, one shape.
        self.assertIs(list_ui["hide_actions_column"], False)

    # 10.101 ----------------------------------------------------------
    def test_10_101_a_plain_drf_serializer_gets_the_same_contract(self):
        """A project's own non-Lex serializer declares columns the same way.

        The endpoint serves both kinds. A frontend that had to know which one
        answered would need two code paths for one payload, and the kind a
        project is most likely to hand-write is the one that would be missed.
        """

        class PlainSerializer:
            class Meta:
                hide_actions_column = True
                lex_column_options = {"id": {"max_width": 120}}

            def __init__(self):
                self.fields = {}

        list_ui = _list_ui_for(PlainSerializer)

        self.assertEqual(list_ui["columns"], {"id": {"max_width": 120}})
        self.assertIs(list_ui["hide_actions_column"], True)

    # 10.102 ----------------------------------------------------------
    def test_10_102_a_model_declaring_nothing_reports_an_empty_map(self):
        """Every response answers the key, so no reader has to guess.

        ``columns`` absent and ``columns`` empty would mean the same thing to
        a careful frontend and different things to a careless one. The
        endpoint removes the question.
        """

        class WidgetSerializer(LexSerializer):
            class Meta:
                pass

        WidgetSerializer.fields = {}

        self.assertEqual(_list_ui_for(WidgetSerializer)["columns"], {})

    # 10.103 ----------------------------------------------------------
    def test_10_103_a_malformed_declaration_never_breaks_the_endpoint(self):
        """A project's typo costs them the option, not the whole grid.

        This payload is fetched before the frontend can draw a table at all.
        An exception raised while describing a DISPLAY PREFERENCE would take
        down every list view of that model, which is catastrophically out of
        proportion to the mistake that caused it.
        """

        class BadSerializer(LexSerializer):
            class Meta:
                lex_column_options = {
                    "id": {"max_width": "ninety"},   # not a number
                    "name": "left",                   # not even a mapping
                    42: {"hidden": True},             # not a field name
                    "ref": {"hidden": True},          # …and one good one
                }

        BadSerializer.fields = {}

        list_ui = _list_ui_for(BadSerializer)

        # The response arrives, and the one usable declaration survives.
        self.assertEqual(list_ui["columns"], {"ref": {"hidden": True}})

    # 10.103 (edge) ---------------------------------------------------
    def test_10_103b_a_declaration_of_the_wrong_type_is_simply_absent(self):
        """``lex_column_options = ["id"]`` is not a mapping and says nothing."""

        class ListSerializer(LexSerializer):
            class Meta:
                lex_column_options = ["id"]

        ListSerializer.fields = {}

        self.assertEqual(_list_ui_for(ListSerializer)["columns"], {})
        # Same answer through the helper the endpoint calls, for a class that
        # is not a LexSerializer at all.
        self.assertEqual(
            create_list_ui_info(
                type("S", (), {"Meta": type("M", (), {"lex_column_options": 7})})
            )["columns"],
            {},
        )
