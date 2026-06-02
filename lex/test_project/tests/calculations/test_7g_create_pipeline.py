"""
Cluster 7g — ``CalculatedModelMixin.create()`` pipeline (end-to-end).

Baseline coverage for ``lex/core/mixins/CalculatedModelMixin.py`` was
**33.74%** after 7a–7f. The remaining 369 missing statements live
almost entirely in the four-step orchestrator invoked by
``Model.create(**overrides)``:

1. ``_generate_model_combinations``            (1346-1401)
2. ``_prepare_models_for_processing``          (1403-1494)
3. ``_create_processing_clusters``             (1497-1576)
4. ``_dispatch_model_processing`` (sync)       (1579-1713)
5. ``calc_and_save_sync``                      (843-971)
6. ``delete_models_with_same_defining_fields`` (1715-1807)

This sub-cluster drives the whole pipeline through **one** integration
entrypoint per scenario (``CombinatorialCalc.create(...)``) rather than
unit-testing each helper in isolation — a single ``create()`` call
exercises all six sections.

Running synchronously (``CELERY_ACTIVE=False`` in CI) is the documented
fallback path and matches the environment every other Cluster 7
scenario runs under.

Scenario numbering continues from 7.24 (last 7f scenario).
"""

from __future__ import annotations

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, CombinatorialCalc

import pytest

pytestmark = pytest.mark.calculations


class TestCluster07g_CreatePipeline(E2ETestCase):
    """End-to-end ``create()`` pipeline on a non-atomic combinatorial model."""

    e2e_models = ALL_MODELS

    def setUp(self) -> None:  # noqa: D401
        super().setUp()
        # Reset class-level toggles between tests — these are mutable
        # class attributes so one test's failure-mode leaks to the next.
        CombinatorialCalc._region_keys = ["US", "EU"]
        CombinatorialCalc._category_keys = ["A", "B"]
        CombinatorialCalc.fail_for_region = None

    # -- 7.25 ----------------------------------------------------------
    def test_7_25_create_with_overrides_runs_cartesian(self) -> None:
        """
        Scenario 7.25: ``Model.create(region=[...], category=[...])``
        expands to a cartesian product, persists one row per combo, and
        every row gets its ``name`` set by ``calculate()``.
        """
        CombinatorialCalc.create(
            region=["US", "EU", "APAC"],
            category=["A", "B"],
        )

        rows = list(CombinatorialCalc.objects.all().order_by("region", "category"))
        self.assertEqual(len(rows), 6, "3 regions × 2 categories = 6 rows")
        self.assertEqual(
            {(r.region, r.category) for r in rows},
            {("US", "A"), ("US", "B"), ("EU", "A"), ("EU", "B"),
             ("APAC", "A"), ("APAC", "B")},
        )
        for r in rows:
            self.assertEqual(r.name, f"{r.region}-{r.category}")

    # -- 7.26 ----------------------------------------------------------
    def test_7_26_create_without_overrides_uses_get_selected_key_list(self) -> None:
        """
        Scenario 7.26: ``Model.create()`` with no kwargs falls back to
        each field's ``get_selected_key_list`` to enumerate values.
        """
        CombinatorialCalc.create()

        rows = list(CombinatorialCalc.objects.all())
        self.assertEqual(len(rows), 4, "default keys: 2 regions × 2 categories")
        self.assertEqual(
            {(r.region, r.category) for r in rows},
            {("US", "A"), ("US", "B"), ("EU", "A"), ("EU", "B")},
        )

    # -- 7.27 ----------------------------------------------------------
    def test_7_27_partial_failure_processes_rest_and_skips_failed(self) -> None:
        """
        Scenario 7.27: ``calculate()`` raises for one region. The sync
        dispatcher fails fast on the first error — ``calc_and_save_sync``
        raises ``CalculatedModelError`` and aborts the remaining models.

        Exercises the ``CalculatedModelError`` raise path inside
        ``calc_and_save_sync`` when ``calculate()`` fails.
        """
        from lex.core.exceptions import CalculatedModelError

        CombinatorialCalc.fail_for_region = "US"

        with self.assertRaises(CalculatedModelError) as ctx:
            CombinatorialCalc.create()

        self.assertIn("CombinatorialCalc failing on purpose", str(ctx.exception))

    # -- 7.28 ----------------------------------------------------------
    def test_7_28_create_is_idempotent_on_rerun(self) -> None:
        """
        Scenario 7.28: re-running ``Model.create()`` with the same keys
        does not duplicate rows —
        ``delete_models_with_same_defining_fields`` detects the existing
        row, returns it, and the subsequent save updates in place.

        Touches the ``object_count == 1`` branch at lines 1766-1769.
        """
        CombinatorialCalc.create()
        first_pks = sorted(CombinatorialCalc.objects.values_list("pk", flat=True))
        self.assertEqual(len(first_pks), 4)

        CombinatorialCalc.create()
        second_pks = sorted(CombinatorialCalc.objects.values_list("pk", flat=True))
        self.assertEqual(first_pks, second_pks)

    # -- 7.29 ----------------------------------------------------------
    def test_7_29_create_with_empty_key_list_prunes_branch(self) -> None:
        """
        Scenario 7.29: a defining field whose ``get_selected_key_list``
        returns ``[]`` prunes the whole combinatorial branch — no rows
        get created and no error raised.
        """
        CombinatorialCalc._region_keys = []
        CombinatorialCalc.create()
        self.assertEqual(
            CombinatorialCalc.objects.count(), 0,
            "empty defining-field key list should produce zero rows",
        )

    # -- 7.30 ----------------------------------------------------------
    def test_7_30_delete_duplicates_resets_pk_when_none_found(self) -> None:
        """
        Scenario 7.30: direct call to
        ``delete_models_with_same_defining_fields`` on an un-saved
        instance carrying a stale pk — it returns ``self`` and resets
        the pk to ``None`` so the caller can INSERT.

        Touches the ``object_count == 0`` branch at lines 1770-1777.
        """
        inst = CombinatorialCalc(region="CA", category="Z")
        inst.pk = 99999
        result = inst.delete_models_with_same_defining_fields()
        self.assertIs(result, inst)
        self.assertIsNone(inst.pk)
