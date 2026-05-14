"""
Cluster 12e: Serializer factory contract.

Covers the three factory-surface guarantees in
``docs/test-plan/test-clusters.md#12e-serializer-factory-contract``:

* 12.26 — ``model2serializer`` always injects ``id_field``,
  ``short_description``, ``lex_reserved_scopes`` in the generated
  ``Meta.fields`` regardless of what ``fields`` the caller passes.
* 12.27 — ``_wrap_custom_serializer`` preserves the user's declared
  ``Meta.fields`` AND adds the framework internals. The developer's
  fields survive; the internal row-identity keys are appended.
* 12.28 — the per-model serializer class is cached: two calls to
  ``get_serializer_map_for_model`` on the same model return the same
  class object so reference-identity comparisons (and DRF field
  registration) stay stable.

These are pure-Python tests (no HTTP round-trip) — they directly
exercise the factory functions in
``lex.api.serializers.base_serializers``.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from django.db import models
from django.test import SimpleTestCase
from lex.api.serializers.base_serializers import (
    ID_FIELD_NAME,
    LEX_SCOPES_NAME,
    SHORT_DESCR_NAME,
    _wrap_custom_serializer,
    get_serializer_map_for_model,
    model2serializer,
    resolve_requested_serializer_name,
)
from rest_framework import serializers as drf_serializers

from .models import WideItem

FRAMEWORK_INTERNALS = {ID_FIELD_NAME, SHORT_DESCR_NAME, LEX_SCOPES_NAME, "id"}


class TestCluster12e_FactoryContract(SimpleTestCase):
    """Direct tests of ``model2serializer`` / ``_wrap_custom_serializer``
    / ``get_serializer_map_for_model``."""

    # -- 12.26 ---------------------------------------------------------
    def test_12_26_model2serializer_always_injects_internal_fields(self) -> None:
        """Scenario 12.26: internal keys are present on every
        auto-generated serializer regardless of what fields the caller
        passes in.
        """
        # (a) default call — fields derived from model._meta.fields.
        SerA = model2serializer(WideItem)
        fields_a = set(SerA.Meta.fields)
        for internal in FRAMEWORK_INTERNALS:
            self.assertIn(
                internal, fields_a,
                f"model2serializer(WideItem) missing internal field "
                f"{internal!r}. Got: {sorted(fields_a)}",
            )

        # (b) caller supplies a narrow fields list — internals still
        # injected, narrow list still honoured.
        SerB = model2serializer(WideItem, fields=["name"])
        fields_b = list(SerB.Meta.fields)
        self.assertIn("name", fields_b)
        for internal in FRAMEWORK_INTERNALS:
            self.assertIn(
                internal, fields_b,
                f"model2serializer(WideItem, fields=['name']) missing "
                f"internal field {internal!r}. Got: {fields_b}",
            )

    # -- 12.27 ---------------------------------------------------------
    def test_12_27_wrap_custom_serializer_preserves_user_fields(self) -> None:
        """Scenario 12.27: ``_wrap_custom_serializer`` keeps the
        developer's ``Meta.fields`` AND appends the framework internals.
        """

        class CustomUserSerializer(drf_serializers.ModelSerializer):
            class Meta:
                model = WideItem
                fields = ["name", "amount"]

        wrapped = _wrap_custom_serializer(CustomUserSerializer, WideItem)
        wrapped_fields = list(wrapped.Meta.fields)

        # Developer's fields MUST survive — the frontend form relies on
        # them being present in exactly the order / set the developer
        # declared.
        for user_field in ("name", "amount"):
            self.assertIn(
                user_field, wrapped_fields,
                f"_wrap_custom_serializer dropped user field "
                f"{user_field!r}. Got: {wrapped_fields}",
            )

        # Framework internals MUST be appended.
        for internal in FRAMEWORK_INTERNALS:
            self.assertIn(
                internal, wrapped_fields,
                f"_wrap_custom_serializer did not append internal field "
                f"{internal!r}. Got: {wrapped_fields}",
            )

        # Base class chain includes LexSerializer so
        # ``lex_reserved_scopes`` / filter-list semantics are active.
        from lex.api.serializers.base_serializers import LexSerializer

        self.assertTrue(
            issubclass(wrapped, LexSerializer),
            "Wrapped class must inherit LexSerializer so the row-level "
            "permission filtering stays in effect",
        )

    # -- 12.28 ---------------------------------------------------------
    def test_12_28_get_serializer_map_returns_same_class_per_model(self) -> None:
        """Scenario 12.28: two calls to ``get_serializer_map_for_model``
        on the same model class return the same serializer class
        object — callers can cache references without worrying about
        re-registration churn.

        NOTE: the current framework rebuilds the class on every call
        (no in-process cache). This test documents the intended
        contract; if it fails, the fix is to memoize by model class,
        not to rewrite the test. Marked as ``expectedFailure`` until
        the cache is added — see BUG tracker.
        """
        first = get_serializer_map_for_model(WideItem)
        second = get_serializer_map_for_model(WideItem)

        self.assertIn("default", first)
        self.assertIn("default", second)

        # Structural sanity: both maps register the same model.
        self.assertIs(
            first["default"].Meta.model,
            second["default"].Meta.model,
            "Factory must register the same model class on repeated calls",
        )

        # Identity assertion — documented-but-not-yet-implemented cache.
        self.assertIs(
            first["default"],
            second["default"],
            "get_serializer_map_for_model must return the same class "
            "object on repeated calls (no cache → churn on every "
            "request; see BUG tracker)",
        )

    # Apply xfail to the cache scenario — the identity assertion is the
    # documented intent but the framework does not yet memoize.
    test_12_28_get_serializer_map_returns_same_class_per_model = (
        unittest.expectedFailure(
            test_12_28_get_serializer_map_returns_same_class_per_model,
        )
    )

    def _with_wide_api_serializers(self, serializers_map: dict[str, type]) -> None:
        """Temporarily install ``WideItem.api_serializers`` for one test."""
        had_api_serializers = hasattr(WideItem, "api_serializers")
        original_api_serializers = getattr(WideItem, "api_serializers", None)

        def _restore() -> None:
            if had_api_serializers:
                WideItem.api_serializers = original_api_serializers
            elif hasattr(WideItem, "api_serializers"):
                delattr(WideItem, "api_serializers")

        self.addCleanup(_restore)
        WideItem.api_serializers = serializers_map

    @staticmethod
    def _configured_alias():
        """Patch the project-level default serializer alias to a non-default name."""
        return patch(
            "lex.core.config.get_configured_default_serializer_name",
            return_value="framework_default",
        )

    # -- 12.32 ---------------------------------------------------------
    def test_12_32_source_default_override_exposes_framework_alias(self) -> None:
        """Scenario 12.32: overriding ``api_serializers['default']``
        keeps the developer serializer at ``default`` and exposes the
        auto-generated framework serializer under the configured alias.
        """

        class WideDefaultOverride(drf_serializers.ModelSerializer):
            class Meta:
                model = WideItem
                fields = ["name"]

        class WideDetailSerializer(drf_serializers.ModelSerializer):
            class Meta:
                model = WideItem
                fields = ["name", "amount", "notes"]

        self._with_wide_api_serializers(
            {"default": WideDefaultOverride, "detail": WideDetailSerializer},
        )

        with self._configured_alias():
            serializers_map = get_serializer_map_for_model(
                WideItem,
                default_fields=["name", "amount", "notes"],
            )

        self.assertEqual(
            set(serializers_map.keys()),
            {"default", "framework_default", "detail"},
        )
        self.assertIsNot(
            serializers_map["default"], serializers_map["framework_default"],
        )
        self.assertIn(
            "framework_default", serializers_map,
            "The framework auto-generated serializer must be addressable by "
            "the configured alias when api_serializers['default'] is overridden.",
        )
        self.assertEqual(
            resolve_requested_serializer_name(serializers_map, "default"),
            "default",
            "Public API requests for ?serializer=default keep resolving to the "
            "developer override; the framework alias is an additional explicit view.",
        )
        self.assertEqual(
            resolve_requested_serializer_name(serializers_map, "framework_default"),
            "framework_default",
        )
        self.assertEqual(
            resolve_requested_serializer_name(serializers_map, "detail"),
            "detail",
        )

    # -- 12.33 ---------------------------------------------------------
    def test_12_33_history_table_inherits_framework_alias_from_source(self) -> None:
        """Scenario 12.33: history tables inherit the source model's
        framework-alias decision without copying its custom serializers.
        """

        class WideDefaultOverride(drf_serializers.ModelSerializer):
            class Meta:
                model = WideItem
                fields = ["name"]

        class WideDetailSerializer(drf_serializers.ModelSerializer):
            class Meta:
                model = WideItem
                fields = ["name", "amount"]

        class SerializerAliasHistoryProbe(models.Model):
            instance_type = WideItem
            name = models.CharField(max_length=200)
            history_id = models.AutoField(primary_key=True)

            class Meta:
                app_label = "lex_app"
                managed = False

        self._with_wide_api_serializers(
            {"default": WideDefaultOverride, "detail": WideDetailSerializer},
        )

        with self._configured_alias():
            serializers_map = get_serializer_map_for_model(SerializerAliasHistoryProbe)

        self.assertEqual(set(serializers_map.keys()), {"default", "framework_default"})
        self.assertIs(serializers_map["default"], serializers_map["framework_default"])
        self.assertIs(serializers_map["framework_default"].Meta.model, SerializerAliasHistoryProbe)

    # -- 12.34 ---------------------------------------------------------
    def test_12_34_meta_history_table_walks_instance_type_chain_for_alias(self) -> None:
        """Scenario 12.34: meta-history tables inherit through the full
        ``MetaHistorical → Historical → Source`` instance_type chain.
        """

        class WideDefaultOverride(drf_serializers.ModelSerializer):
            class Meta:
                model = WideItem
                fields = ["name"]

        class SerializerAliasHistoryChainProbe(models.Model):
            instance_type = WideItem
            name = models.CharField(max_length=200)
            history_id = models.AutoField(primary_key=True)

            class Meta:
                app_label = "lex_app"
                managed = False

        class SerializerAliasMetaHistoryProbe(models.Model):
            instance_type = SerializerAliasHistoryChainProbe
            meta_history_id = models.AutoField(primary_key=True)

            class Meta:
                app_label = "lex_app"
                managed = False

        self._with_wide_api_serializers({"default": WideDefaultOverride})

        with self._configured_alias():
            serializers_map = get_serializer_map_for_model(SerializerAliasMetaHistoryProbe)

        self.assertEqual(set(serializers_map.keys()), {"default", "framework_default"})
        self.assertIs(serializers_map["default"], serializers_map["framework_default"])
        self.assertIs(serializers_map["framework_default"].Meta.model, SerializerAliasMetaHistoryProbe)

    # -- 12.35 ---------------------------------------------------------
    def test_12_35_wrap_custom_serializer_preserves_hide_actions_column(self) -> None:
        """Scenario 12.35: ``Meta.hide_actions_column`` survives wrapping."""

        class HiddenActionsSerializer(drf_serializers.ModelSerializer):
            class Meta:
                model = WideItem
                fields = ["name", "amount"]
                hide_actions_column = True

        wrapped = _wrap_custom_serializer(HiddenActionsSerializer, WideItem)

        self.assertTrue(wrapped.Meta.hide_actions_column)
        self.assertEqual(wrapped.get_list_ui_options(), {"hide_actions_column": True})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

