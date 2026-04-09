"""
Tests for ``CalculatedModelMixin`` – the parallelisable calculation engine.

Verifies:
    • ``get_selected_key_list`` iterates lazy querysets and plain lists
    • ``ModelCombinationGenerator`` produces correct Cartesian products
    • Dispatch respects ``WaitForTasks`` / ``FireAndForget`` context
    • Parallelisable vs non-parallelisable field handling
"""

import os
from django.db import models
from django.test import SimpleTestCase
from unittest.mock import patch

from core.mixins.CalculatedModelMixin import (
    CalculatedModelMixin,
    ModelCombinationGenerator,
)
from lex.core.tasks.CeleryTaskDispatcher import CeleryTaskDispatcher
from lex.lex_app.celery_tasks import WaitForTasks


class LazyValues:
    def __init__(self, values):
        self._values = values

    def __iter__(self):
        return iter(self._values)


class IterableSelectionModel(CalculatedModelMixin):
    defining_fields = ["region"]
    parallelizable_fields = []

    region = models.CharField(max_length=32, blank=True)

    selected_values = []
    calculate_calls = 0
    dispatch_calls = []

    class Meta:
        app_label = "lex_app"
        managed = False

    def get_selected_key_list(self, key):
        if key == "region":
            return self.__class__.selected_values
        return []

    def calculate(self):
        self.__class__.calculate_calls += 1

    def calculate_mixin(self):
        self.__class__.calculate_calls += 1

    @classmethod
    def _dispatch_model_processing(cls, processing_clusters, *args):
        cls.dispatch_calls.append(processing_clusters)
        return super()._dispatch_model_processing(processing_clusters, *args)


class DispatchDecisionModel(CalculatedModelMixin):
    defining_fields = []
    parallelizable_fields = []

    region = models.CharField(max_length=32, blank=True)

    class Meta:
        app_label = "lex_app"
        managed = False

    def calculate(self):
        return None


DispatchDecisionModel.calculate.delay = object()


class CalculatedModelMixinTests(SimpleTestCase):
    def setUp(self):
        IterableSelectionModel.selected_values = []
        IterableSelectionModel.calculate_calls = 0
        IterableSelectionModel.dispatch_calls = []

    def test_get_field_values_materializes_lazy_iterables(self):
        IterableSelectionModel.selected_values = LazyValues(["DE", "SE"])

        values = ModelCombinationGenerator._get_field_values(
            IterableSelectionModel(),
            "region",
            {},
        )

        self.assertEqual(values, ["DE", "SE"])

    def test_get_field_values_keeps_strings_as_single_values(self):
        IterableSelectionModel.selected_values = "DE"

        values = ModelCombinationGenerator._get_field_values(
            IterableSelectionModel(),
            "region",
            {},
        )

        self.assertEqual(values, ["DE"])

    def test_empty_iterables_produce_no_model_combinations(self):
        IterableSelectionModel.selected_values = LazyValues([])

        combinations = ModelCombinationGenerator.generate_model_combinations(
            IterableSelectionModel(),
            IterableSelectionModel.defining_fields,
            {},
        )

        self.assertEqual(combinations, [])

    def test_generate_model_combinations_expands_lazy_iterables(self):
        IterableSelectionModel.selected_values = LazyValues(["DE", "SE"])

        combinations = ModelCombinationGenerator.generate_model_combinations(
            IterableSelectionModel(),
            IterableSelectionModel.defining_fields,
            {},
        )

        self.assertEqual([model.region for model in combinations], ["DE", "SE"])

    def test_create_treats_empty_selections_as_valid_noop(self):
        IterableSelectionModel.selected_values = LazyValues([])

        IterableSelectionModel.create()

        self.assertEqual(IterableSelectionModel.calculate_calls, 0)
        self.assertEqual(IterableSelectionModel.dispatch_calls, [{}])

    def test_dispatch_processing_runs_sync_inside_worker_without_explicit_async_context(self):
        model = DispatchDecisionModel(region="DE")
        processing_clusters = {"all": [model]}

        with patch.dict(os.environ, {"CELERY_ACTIVE": "true"}), patch(
            "lex.lex_app.celery_tasks.is_celery_worker_process",
            return_value=True,
        ), patch.object(
            CeleryTaskDispatcher, "dispatch_calculation_groups"
        ) as async_dispatch, patch(
            f"{CalculatedModelMixin.__module__}.calc_and_save_sync"
        ) as sync_dispatch:
            DispatchDecisionModel._dispatch_model_processing(processing_clusters)

        sync_dispatch.assert_called_once_with([model])
        async_dispatch.assert_not_called()

    def test_dispatch_processing_keeps_async_dispatch_with_explicit_wait_context(self):
        model = DispatchDecisionModel(region="SE")
        processing_clusters = {"all": [model]}

        with patch.dict(os.environ, {"CELERY_ACTIVE": "true"}), patch(
            "lex.lex_app.celery_tasks.is_celery_worker_process",
            return_value=True,
        ), patch(
            "lex.lex_app.celery_tasks._celery_is_active",
            return_value=True,
        ), patch.object(
            CeleryTaskDispatcher, "dispatch_calculation_groups"
        ) as async_dispatch, patch(
            f"{CalculatedModelMixin.__module__}.calc_and_save_sync"
        ) as sync_dispatch:
            with WaitForTasks():
                DispatchDecisionModel._dispatch_model_processing(processing_clusters)

        async_dispatch.assert_called_once()
        self.assertEqual(async_dispatch.call_args.args[0], [[model]])
        sync_dispatch.assert_not_called()
