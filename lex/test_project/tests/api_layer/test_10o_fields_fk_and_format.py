"""'/fields/' metadata: FK display hint, preview availability, format spec.

Intent: the React column builder reads /fields/ to decide how to render each
column. FK columns must advertise (a) which target field is the human label
and (b) whether the target has a curated 'preview' serializer for the hover
card; every column must be able to carry a display 'format' spec. A
regression silently reverts FK chips to raw ids or drops currency/percent
formatting. None of this needs a DB.
Cluster 10o — scenarios 10.70–10.74. Type: U.
Covers: lex/api/views/model_info/Fields.py.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10o_fields_fk_and_format.py -v
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.db.models import FloatField, ForeignKey
from django.test import SimpleTestCase
from rest_framework import serializers as drf_serializers

from lex.api.views.model_info.Fields import (
    Fields,
    _target_has_preview_serializer,
    create_field_info,
)

pytestmark = pytest.mark.api_layer


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #


def _fake_fk_field(target):
    """Build a minimal ForeignKey-like mock pointing at *target*."""
    f = MagicMock(spec=ForeignKey)
    f.name = "fund"
    f.verbose_name = "fund"
    f.editable = True
    f.null = False
    f.primary_key = False
    f.get_default = MagicMock(return_value=None)
    f.remote_field = SimpleNamespace(model=target, limit_choices_to=None)
    return f


# --------------------------------------------------------------------- #
# Cluster 10o-A — FK display hints in create_field_info
# --------------------------------------------------------------------- #


class TestCluster10o_FkDisplayHints(SimpleTestCase):
    """Cluster 10o: FK field info exposes fk_label_field + fk_preview (scenarios 10.70–10.72)."""

    # 10.70 -----------------------------------------------------------
    def test_10_70_fk_exposes_declared_label_field_and_preview_true(self) -> None:
        """Target with lex_fk_label_field + api_serializers['preview'] → both hints present.

        Pin the primary happy-path: when a FK target model has declared a
        human-readable label field and registers a 'preview' serializer, both
        facts must surface in the /fields/ response so the frontend can
        (a) show the label in the chip and (b) enable the hover card.
        A regression that drops these keys silently reverts FK chips to raw
        PK integers — the main UX problem the redesign fixes.
        """
        target = SimpleNamespace(
            _meta=SimpleNamespace(model_name="fund"),
            lex_fk_label_field="name",
            api_serializers={"preview": object()},
        )
        info = create_field_info(_fake_fk_field(target))
        self.assertEqual(
            info["fk_label_field"],
            "name",
            "lex_fk_label_field must surface as fk_label_field in /fields/ response",
        )
        self.assertTrue(
            info["fk_preview"],
            "A target with a 'preview' entry in api_serializers must set fk_preview=True",
        )
        self.assertEqual(
            info["target"],
            "fund",
            "Existing 'target' key must still be present after adding FK hints",
        )

    # 10.71 -----------------------------------------------------------
    def test_10_71_fk_defaults_when_target_has_no_hints(self) -> None:
        """Bare FK target (no hints) → fk_label_field is None, fk_preview is False.

        Pin the "opt-in is truly optional" contract: a model that declares
        neither lex_fk_label_field nor api_serializers must produce exactly
        fk_label_field=None and fk_preview=False. Drift here would either
        KeyError or silently enable the hover card for every FK.
        """
        target = SimpleNamespace(_meta=SimpleNamespace(model_name="fund"))
        info = create_field_info(_fake_fk_field(target))
        self.assertIsNone(
            info["fk_label_field"],
            "A target without lex_fk_label_field must yield fk_label_field=None",
        )
        self.assertFalse(
            info["fk_preview"],
            "A target without api_serializers must yield fk_preview=False",
        )

    # 10.72 -----------------------------------------------------------
    def test_10_72_preview_helper_reads_api_serializers_correctly(self) -> None:
        """_target_has_preview_serializer: True only when 'preview' key is present.

        Pin the three branches of the helper:
        * a model whose api_serializers dict contains 'preview' → True,
        * a model whose dict lacks 'preview' → False,
        * a model with no api_serializers attribute at all → False.
        Drift in any branch silently enables or disables hover cards globally.
        """
        with_preview = SimpleNamespace(api_serializers={"preview": object()})
        without_preview = SimpleNamespace(api_serializers={"default": object()})
        no_registry = SimpleNamespace()

        self.assertTrue(
            _target_has_preview_serializer(with_preview),
            "api_serializers with 'preview' key must return True",
        )
        self.assertFalse(
            _target_has_preview_serializer(without_preview),
            "api_serializers without 'preview' key must return False",
        )
        self.assertFalse(
            _target_has_preview_serializer(no_registry),
            "Missing api_serializers attribute must return False",
        )


# --------------------------------------------------------------------- #
# Cluster 10o-B — per-field format spec in Fields.get
# --------------------------------------------------------------------- #


class TestCluster10o_FieldFormatSpec(SimpleTestCase):
    """Cluster 10o: model.lex_field_formats surfaces as info['format'] (scenarios 10.73–10.74)."""

    def _request(self, serializer_name="default"):
        req = MagicMock()
        req.query_params = {"serializer": serializer_name} if serializer_name else {}
        return req

    def _container(self, model, serializer):
        """Build a minimal model container that satisfies Fields.get's interface."""
        return SimpleNamespace(
            model_class=model,
            serializers_map={"default": lambda: serializer},
            get_serializers_map=lambda: {"default": lambda: serializer},
        )

    # 10.73 -----------------------------------------------------------
    def test_10_73_format_attached_from_model_declaration(self) -> None:
        """Column listed in lex_field_formats carries info['format'] verbatim.

        Pin that when a model declares lex_field_formats = {"revenue": {...}},
        the revenue column's /fields/ entry carries info["format"] equal to
        that declared dict — not a copy, not coerced, verbatim. A regression
        here means the frontend falls back to no formatting for currency /
        percent columns and the numbers render without context.
        """
        revenue = MagicMock(spec=FloatField)
        revenue.name = "revenue"
        revenue.verbose_name = "revenue"
        revenue.editable = True
        revenue.null = True
        revenue.primary_key = False
        revenue.get_default = MagicMock(return_value=None)

        fmt = {"format": "currency", "currency": "EUR", "decimals": 2}
        model = SimpleNamespace(
            _meta=SimpleNamespace(
                pk=SimpleNamespace(name="id"),
                get_field=lambda src: revenue if src == "revenue" else (_ for _ in ()).throw(Exception()),
            ),
            lex_field_formats={"revenue": fmt},
        )
        serializer = SimpleNamespace(
            fields={"revenue": SimpleNamespace(source="revenue")},
            Meta=SimpleNamespace(),
        )
        resp = Fields().get(
            self._request(),
            model_container=self._container(model, serializer),
        )
        revenue_info = next(f for f in resp.data["fields"] if f["name"] == "revenue")
        self.assertEqual(
            revenue_info["format"],
            fmt,
            "Declared lex_field_formats entry must surface verbatim under info['format']",
        )

    # 10.74 -----------------------------------------------------------
    def test_10_74_no_format_key_when_column_undeclared(self) -> None:
        """Column absent from lex_field_formats must NOT carry a 'format' key.

        Pin the non-breaking invariant: columns not listed in lex_field_formats
        must produce an info dict without any 'format' key. An accidental
        empty-dict or None insertion would break the frontend's strict
        'format' in info guard and apply a default formatting to columns
        that should remain plain.
        """
        amount = MagicMock(spec=FloatField)
        amount.name = "amount"
        amount.verbose_name = "amount"
        amount.editable = True
        amount.null = True
        amount.primary_key = False
        amount.get_default = MagicMock(return_value=None)

        model = SimpleNamespace(
            _meta=SimpleNamespace(
                pk=SimpleNamespace(name="id"),
                get_field=lambda src: amount,
            ),
            lex_field_formats={},
        )
        serializer = SimpleNamespace(
            fields={"amount": SimpleNamespace(source="amount")},
            Meta=SimpleNamespace(),
        )
        resp = Fields().get(
            self._request(),
            model_container=self._container(model, serializer),
        )
        amount_info = next(f for f in resp.data["fields"] if f["name"] == "amount")
        self.assertNotIn(
            "format",
            amount_info,
            "Columns absent from lex_field_formats must omit the 'format' key entirely",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
