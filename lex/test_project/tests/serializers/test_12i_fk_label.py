"""Foreign-key label resolution + model display hints.

Intent: FK cells must be able to show a human label (the target model's
declared ``lex_fk_label_field``) instead of a bare PK, while the FK value
itself stays the PK. A regression here means FK chips render blank or fall
back to opaque ids, which is exactly the UX the redesign removes.
Cluster 12i — scenarios 12.40–12.46. Type: U.
Covers: lex/api/serializers/base_serializers.py (resolve_fk_label,
        RestApiModelSerializerTemplate._inject_fk_labels, to_representation),
        lex/core/models/LexModel.py (lex_fk_label_field, lex_field_formats).
Run: python -m lex pytest lex/test_project/tests/serializers/test_12i_fk_label.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.db.models import ForeignKey, IntegerField
from django.test import SimpleTestCase

from lex.api.serializers.base_serializers import (
    RestApiModelSerializerTemplate,
    resolve_fk_label,
)

pytestmark = pytest.mark.serializers


# ─── helpers ────────────────────────────────────────────────────────────────

def _fk_field(name):
    """Build a MagicMock that satisfies isinstance(…, ForeignKey)."""
    f = MagicMock(spec=ForeignKey)
    f.name = name
    return f


def _plain_field(name):
    """Build a MagicMock that is NOT a ForeignKey."""
    f = MagicMock(spec=IntegerField)
    f.name = name
    return f


# ─── Cluster 12i — Task 1: resolve_fk_label ─────────────────────────────────

class TestCluster12i_ResolveFkLabel(SimpleTestCase):
    """Cluster 12i: resolve_fk_label honours lex_fk_label_field, else str()."""

    def test_none_returns_none(self):
        """Scenario 12.40: a null relation resolves to None (no label)."""
        self.assertIsNone(
            resolve_fk_label(None),
            "Scenario 12.40: None relation must yield no label",
        )

    def test_falls_back_to_str_without_hint(self):
        """Scenario 12.41: with no lex_fk_label_field, label == str(obj)."""

        class Target:
            def __str__(self):
                return "STR-FORM"

        self.assertEqual(
            resolve_fk_label(Target()),
            "STR-FORM",
            "Scenario 12.41: without a hint the label must fall back to str(obj)",
        )

    def test_uses_declared_label_field(self):
        """Scenario 12.42: lex_fk_label_field selects the label column."""

        class Target:
            lex_fk_label_field = "name"
            name = "Fund Alpha"

            def __str__(self):
                return "wrong"

        self.assertEqual(
            resolve_fk_label(Target()),
            "Fund Alpha",
            "Scenario 12.42: declared label field must win over __str__",
        )

    def test_blank_label_field_value_falls_back_to_str(self):
        """Scenario 12.43: a None value on the label field falls back to str(obj)."""

        class Target:
            lex_fk_label_field = "name"
            name = None

            def __str__(self):
                return "STR-FALLBACK"

        self.assertEqual(
            resolve_fk_label(Target()),
            "STR-FALLBACK",
            "Scenario 12.43: a null label-field value must fall back to str(obj)",
        )


# ─── Cluster 12i — Task 2: _inject_fk_labels ────────────────────────────────

class TestCluster12i_InjectFkLabels(SimpleTestCase):
    """Cluster 12i: _inject_fk_labels adds '<fk>_label' next to each FK PK."""

    def _meta_with(self, fields):
        meta = MagicMock()
        meta.concrete_fields = fields
        return meta

    def test_label_added_for_present_fk(self):
        """Scenario 12.44: a 'fund_label' sibling appears next to 'fund' PK."""
        related = SimpleNamespace(__str__=lambda self=None: "Fund Alpha")
        instance = SimpleNamespace(fund=related)
        # Bind the unbound helper to a lightweight object carrying a model meta.
        carrier = SimpleNamespace(
            Meta=SimpleNamespace(
                model=SimpleNamespace(
                    _meta=self._meta_with([_fk_field("fund"), _plain_field("amount")])
                )
            )
        )
        rep = {"fund": 7, "amount": 100}
        RestApiModelSerializerTemplate._inject_fk_labels(carrier, rep, instance)
        self.assertEqual(
            rep["fund"],
            7,
            "Scenario 12.44a: FK value must stay the PK (non-breaking)",
        )
        self.assertEqual(
            rep["fund_label"],
            "Fund Alpha",
            "Scenario 12.44b: label sibling must be added",
        )
        self.assertNotIn(
            "amount_label",
            rep,
            "Scenario 12.44c: non-FK columns get no label",
        )

    def test_no_label_when_fk_absent_from_representation(self):
        """Scenario 12.45: a filtered-out FK gets no label (respects visibility)."""
        instance = SimpleNamespace(fund=SimpleNamespace())
        carrier = SimpleNamespace(
            Meta=SimpleNamespace(
                model=SimpleNamespace(
                    _meta=self._meta_with([_fk_field("fund")])
                )
            )
        )
        rep = {"amount": 100}  # 'fund' was removed by permission filtering
        RestApiModelSerializerTemplate._inject_fk_labels(carrier, rep, instance)
        self.assertNotIn(
            "fund_label",
            rep,
            "Scenario 12.45: no label for a hidden FK column",
        )

    def test_null_relation_label_is_none(self):
        """Scenario 12.46: a null FK yields fund_label == None, PK stays None."""
        instance = SimpleNamespace(fund=None)
        carrier = SimpleNamespace(
            Meta=SimpleNamespace(
                model=SimpleNamespace(
                    _meta=self._meta_with([_fk_field("fund")])
                )
            )
        )
        rep = {"fund": None}
        RestApiModelSerializerTemplate._inject_fk_labels(carrier, rep, instance)
        self.assertIsNone(
            rep["fund_label"],
            "Scenario 12.46: null relation must yield null label",
        )
