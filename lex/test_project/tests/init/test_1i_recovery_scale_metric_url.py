"""Cluster 1i — URL routing for the recovery-beat scale metric endpoint.

Intent
------
``lex/lex_app/urls.py`` registers the named route ``recovery-scale-metric``
so that KEDA's ``metrics-api`` trigger can poll
``GET /api/recovery-scale-metric`` on the framework's HTTP interface.
A regression that renames or removes the route would silently break the
on-demand scale signal: KEDA would fail to poll the endpoint, treat the
metric as unavailable, and either keep the recovery-beat pod up forever
(depending on KEDA's missing-metric policy) or scale it down onto work
it can no longer observe.

The URL must resolve both ways:
* **forward** — ``reverse("recovery-scale-metric")`` returns the path
  ``/api/recovery-scale-metric``.
* **reverse** — ``resolve("/api/recovery-scale-metric")`` maps to the
  ``RecoveryScaleMetric`` view class, confirming the registration wired
  the right handler.

Cluster 1i — scenarios 1.195–1.196. Type: U.
Covers: ``lex/lex_app/urls.py`` (``recovery-scale-metric`` route entry).
Run: python -m lex pytest lex/test_project/tests/init/test_1i_recovery_scale_metric_url.py -v
"""

from __future__ import annotations

import pytest
from django.test import SimpleTestCase
from django.urls import resolve, reverse, NoReverseMatch

pytestmark = pytest.mark.init


class TestCluster01i_RecoveryScaleMetricUrl(SimpleTestCase):
    """Cluster 1i: URL registration of the recovery-beat scale metric route."""

    def test_1_195_recovery_scale_metric_route_reverses(self):
        """
        Scenario 1.195: ``reverse("recovery-scale-metric")`` resolves.
        Given: Django URL conf loaded with the framework routes
        When:  ``reverse("recovery-scale-metric")`` is called
        Then:  it returns a path containing ``/api/recovery-scale-metric``
               (confirms the named route is present — a rename would raise
               ``NoReverseMatch`` and silently break the KEDA metrics-api
               trigger that relies on this name)
        """
        url = reverse("recovery-scale-metric")
        self.assertIn(
            "/api/recovery-scale-metric",
            url,
            f"reverse('recovery-scale-metric') must contain "
            f"/api/recovery-scale-metric — KEDA polls this named route; "
            f"got {url!r}",
        )

    def test_1_196_recovery_scale_metric_path_resolves_to_correct_view(self):
        """
        Scenario 1.196: ``/api/recovery-scale-metric`` resolves to
        ``RecoveryScaleMetric``.
        Given: Django URL conf loaded with the framework routes
        When:  ``resolve("/api/recovery-scale-metric")`` is called
        Then:  the resolved view's class is ``RecoveryScaleMetric``
               (confirms the route is wired to the right handler — swapping
               in a different view would respond with the wrong schema and
               break KEDA's count-based scaling decision)
        """
        from lex.api.views.calculations.RecoveryScaleMetric import RecoveryScaleMetric

        match = resolve("/api/recovery-scale-metric")
        # DRF ``APIView.as_view()`` stores ``cls`` on the wrapped function.
        view_cls = getattr(match.func, "cls", None) or getattr(match.func, "view_class", None)
        self.assertIs(
            view_cls,
            RecoveryScaleMetric,
            f"/api/recovery-scale-metric must resolve to RecoveryScaleMetric; "
            f"got {view_cls!r}",
        )
