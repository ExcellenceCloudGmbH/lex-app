"""
Cluster 7h — ``CalculatedModelMixin._dispatch_model_processing`` Celery branch.

Sub-cluster 7g closed the synchronous side of the ``create()`` pipeline
(``CELERY_ACTIVE=False`` is what CI runs under). The **Celery-dispatch
branch** of :meth:`CalculatedModelMixin._dispatch_model_processing`
(lines ~1606-1665) was still uncovered because it only fires when
``CELERY_ACTIVE=true`` AND the model's ``calculate`` is a real Celery
task with a ``.delay`` attribute.

This sub-cluster drives that branch by:

1. Setting ``CELERY_ACTIVE=true`` for the test body.
2. Attaching a stub ``.delay`` attribute to ``CombinatorialCalc.calculate``
   so ``hasattr(cls.calculate, 'delay')`` returns ``True``.
3. Patching :meth:`CeleryTaskDispatcher.dispatch_calculation_groups` so
   no broker is required — we observe the dispatcher call instead.

Scenario numbering continues from 7.30 (last 7g scenario).
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, CombinatorialCalc


class TestCluster07h_CeleryDispatch(E2ETestCase):
    """Celery-branch dispatch of the ``create()`` pipeline."""

    e2e_models = ALL_MODELS

    def setUp(self) -> None:
        super().setUp()
        CombinatorialCalc._region_keys = ["US", "EU"]
        CombinatorialCalc._category_keys = ["A", "B"]
        CombinatorialCalc.fail_for_region = None

    # -- 7.31 ----------------------------------------------------------
    def test_7_31_create_routes_to_celery_dispatcher_when_active(self) -> None:
        """Scenario 7.31: with ``CELERY_ACTIVE=true`` and a
        ``.delay``-capable ``calculate``, ``create()`` routes through
        :meth:`CeleryTaskDispatcher.dispatch_calculation_groups`
        instead of the synchronous ``calc_and_save_sync`` path.

        Covers the async branch of ``_dispatch_model_processing``:
        the ``celery_active and not inline_inside_worker`` gate, the
        ``ModelClusterManager.flatten_clusters_to_groups`` call, and
        the dispatcher invocation itself — lines ~1606-1665 of
        ``CalculatedModelMixin.py`` that 7g did not reach.
        """
        stub_delay_called = []

        def _stub_delay(*args, **kwargs):  # pragma: no cover — not invoked
            stub_delay_called.append((args, kwargs))

        # Attach ``.delay`` so the ``hasattr(cls.calculate, 'delay')``
        # predicate fires. We cannot rebind ``calculate`` itself (the
        # dispatcher needs the bound calculate() method to still work
        # on each instance when the sync fallback hits it), so we just
        # decorate it with a ``.delay`` attribute.
        original_calculate = CombinatorialCalc.calculate
        original_calculate.delay = _stub_delay

        with patch.dict(os.environ, {"CELERY_ACTIVE": "true"}, clear=False), \
                patch(
                    "lex.core.tasks.CeleryTaskDispatcher.CeleryTaskDispatcher"
                    ".dispatch_calculation_groups",
                ) as mock_dispatcher:
            try:
                CombinatorialCalc.create()
            finally:
                try:
                    del original_calculate.delay
                except AttributeError:
                    pass

        # The async dispatcher was called. If the sync ``calc_and_save_sync``
        # branch had fired instead, dispatch_calculation_groups would be
        # untouched.
        self.assertTrue(
            mock_dispatcher.called,
            "CeleryTaskDispatcher.dispatch_calculation_groups was not "
            "invoked — _dispatch_model_processing stayed on the sync "
            "branch even though CELERY_ACTIVE=true and calculate.delay "
            "is present. That means the async gate is broken.",
        )

        # First positional arg is ``processing_groups`` — flattened
        # clusters. Must be a non-empty list-of-lists of
        # ``CombinatorialCalc`` instances. 2 regions × 2 categories
        # with ``parallelizable_fields=["region"]`` → 2 groups.
        pos_args, _kwargs = mock_dispatcher.call_args
        processing_groups = pos_args[0]
        self.assertIsInstance(processing_groups, list)
        self.assertGreater(
            len(processing_groups), 0,
            "dispatch_calculation_groups called with an empty groups "
            "list — clustering produced nothing despite 4 combinations",
        )
        total = sum(len(g) for g in processing_groups)
        self.assertEqual(
            total, 4,
            f"Expected 4 models (2 regions × 2 categories) flattened "
            f"across groups; dispatcher saw {total}",
        )

        # Because the dispatcher was mocked, NO rows were actually
        # saved — the test asserts the routing, not the calculation.
        # This also proves we did NOT silently fall back to the sync
        # path (which would have persisted rows).
        self.assertEqual(
            CombinatorialCalc.objects.count(), 0,
            "Rows were persisted — the test fell through to the sync "
            "path instead of the mocked Celery dispatcher",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

