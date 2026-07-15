"""Cluster 8c — the on-demand recovery-beat scale metric.

Intent: the recovery-beat pod (embedded beat that fires the dead-worker sweep)
runs on demand — KEDA polls a metric endpoint and keeps the pod at one replica
while calculation work is in flight, scaling it to zero when there is nothing to
sweep. The metric must never read zero while work still needs watching, or the
sweeper scales away from a calculation that may yet need recovering. It is the
**union** of two independent signals — the cross-process recovery registry
(Redis) and the in-process, DB-reconciled active-calculation store — so a
transient outage of one never zeroes the metric on its own.
Cluster 8c — scenarios 8.157–8.159. Type: U.
Covers: lex/api/views/calculations/RecoveryScaleMetric.py
        (active_recovery_count, RecoveryScaleMetric view).
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8c_recovery_scale_metric.py -v
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from django.test import SimpleTestCase

from lex.api.views.calculations import RecoveryScaleMetric as metric_module
from lex.api.views.calculations.RecoveryScaleMetric import (
    RecoveryScaleMetric,
    active_recovery_count,
)

pytestmark = pytest.mark.celery_async


class TestCluster08c_RecoveryScaleMetric(SimpleTestCase):
    """Cluster 8c: the count that drives on-demand recovery-beat scaling."""

    def _patch_sources(self, *, registry_ids, store_rows):
        """Patch the registry list and the active-calc store snapshot."""
        from lex.core.signals.ActiveCalculationStateStore import (
            ActiveCalculationStateStore,
        )
        from lex.lex_app.celery_recovery import registry

        return [
            mock.patch.object(registry, "list_tracked", return_value=registry_ids),
            mock.patch.object(ActiveCalculationStateStore, "snapshot", return_value=store_rows),
        ]

    def _count(self, *, registry_ids, store_rows):
        patches = self._patch_sources(registry_ids=registry_ids, store_rows=store_rows)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return active_recovery_count()

    def test_8_157_count_is_the_union_of_both_signals(self):
        """
        Scenario 8.157: the metric is max(registry, active-store), so neither
        signal alone can scale the sweeper away from work the other still sees.
        Given: registry and store reporting different amounts of work
        When: active_recovery_count() runs
        Then: the larger of the two is returned — including the case where one
              is empty and the other is not
        """
        self.assertEqual(
            self._count(registry_ids=["a", "b"], store_rows=[]), 2,
            "The registry alone must keep the sweeper up when the in-process "
            "store is empty (e.g. work dispatched from another process).",
        )
        self.assertEqual(
            self._count(registry_ids=[], store_rows=[{"r": 1}]), 1,
            "The DB-reconciled store must keep the sweeper up during a Redis "
            "blip that momentarily empties the registry read.",
        )
        self.assertEqual(
            self._count(registry_ids=["a", "b", "c"], store_rows=[{"r": 1}]), 3,
            "With both non-empty the metric is the larger — never their sum, "
            "which would over-report shared work.",
        )

    def test_8_158_zero_only_when_nothing_is_in_flight(self):
        """
        Scenario 8.158: the metric reads zero exactly when both signals are
        empty — the one condition under which the pod may scale to zero.
        Given: both the registry and the store empty
        When: active_recovery_count() runs
        Then: 0 (KEDA scales the recovery-beat Deployment to zero replicas)
        """
        self.assertEqual(
            self._count(registry_ids=[], store_rows=[]), 0,
            "Only a genuinely idle instance — nothing tracked, nothing active — "
            "may scale the sweeper to zero.",
        )

    def test_8_159_view_reports_positive_count_on_error(self):
        """
        Scenario 8.159: any failure computing the count fails safe upward.
        Given: active_recovery_count raises
        When: the endpoint is called
        Then: it responds 200 with a positive count, so a metric error keeps
              the sweeper up rather than scaling it onto work it can't see
        """
        request = mock.MagicMock()
        with mock.patch.object(
            metric_module, "active_recovery_count", side_effect=RuntimeError("boom")
        ):
            response = RecoveryScaleMetric().get(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertGreater(
            payload["count"], 0,
            "A scale-metric error must report positive so the sweeper is never "
            "scaled down onto work it failed to observe.",
        )
