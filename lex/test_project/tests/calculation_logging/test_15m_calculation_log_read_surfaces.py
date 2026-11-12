"""Cluster 15m — the calculation-log table is readable again.

Scenarios 15.56 – 15.59.

Reported with LEX-1555: opening a failed run's log and switching to the
**Details** tab threw the reader out of the drawer and onto the CalculationLog
table page, where every cell read ``ERR``.

Both halves are one 500. ``LexSerializer.to_representation`` asks
``type(instance).can_read`` before it asks whether the model has one, and
``CalculationLog`` is a plain ``django.db.models.Model`` — it has no
``can_read``. Every read of the table through ``model_entries`` therefore
raised ``AttributeError``:

* the detail endpoint, which react-admin's ``<Show>`` calls for the Details
  tab — its ``onError`` notifies and redirects to the resource's list, which
  is the "it sends me to the table" half;
* the grid endpoint, which AG Grid's server-side row model calls for that
  list — a failed block renders its cells as ``ERR``, which is the other half;
* ``many``, which the log tree calls for each node's own log text.

The two branches below this one guard themselves with ``has_can_read`` /
``has_permission_read``; this one did not, so it raised before they could run.
15.59 is the guard the other way: the remembered decision is still consulted
for a model that *does* declare ``can_read``, so the fix costs the per-request
memo nothing.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from django.contrib.contenttypes.models import ContentType
from rest_framework import status

from lex.audit_logging.models.CalculationLog import CalculationLog

from . import _CalcLogTestCase
from .models import LogRootCalc

pytestmark = pytest.mark.calculation_logging


def _ag(**overrides):
    request = {
        "startRow": 0,
        "endRow": 100,
        "rowGroupCols": [],
        "groupKeys": [],
        "pivotCols": [],
        "pivotMode": False,
        "valueCols": [],
        "sortModel": [],
        "filterModel": {},
    }
    request.update(overrides)
    return request


class TestCluster15m_CalculationLogReadSurfaces(_CalcLogTestCase):
    """Every way the frontend reads a CalculationLog row, as a plain user.

    ``E2ETestCase`` logs in as ``e2e_user``, who is **not** a Django
    superuser — which is what every Keycloak/OIDC user is, and the population
    the regression was reported from.
    """

    def setUp(self):
        super().setUp()
        LogRootCalc.objects.bulk_create(
            [LogRootCalc(name="15m-root", child_mode="log_only", units_csv="")]
        )
        self.row = LogRootCalc.objects.get(name="15m-root")
        self.calculation_id = f"logrootcalc_{self.row.pk}_update_{uuid4().hex}"
        self.log = CalculationLog.objects.create(
            calculationId=self.calculation_id,
            calculation_log="Start: the step that ran before the failure",
            content_type=ContentType.objects.get_for_model(LogRootCalc),
            object_id=self.row.pk,
        )

    # ── 15.56 ────────────────────────────────────────────────────────────
    def test_15_56_the_detail_endpoint_serves_one_log(self):
        """Scenario 15.56: the regression, at the endpoint ``<Show>`` calls.

        The Details tab of the log tree mounts react-admin's ``<Show>`` on
        ``calculationlog``. A failing ``getOne`` is what redirects the reader
        to the table; nothing about the Details tab itself was wrong.
        """
        response = self.client.get(
            self.url_detail("calculationlog", self.log.pk, calc_id="undefined"),
            {"serializer": "default"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["id"], self.log.pk)
        self.assertEqual(response.data["calculationId"], self.calculation_id)
        self.assertIn("the step that ran before the failure", response.data["calculation_log"])

    # ── 15.57 ────────────────────────────────────────────────────────────
    def test_15_57_the_grid_endpoint_serves_the_table(self):
        """Scenario 15.57: the ``ERR`` half.

        AG Grid's server-side row model renders a block it could not load as
        ``ERR`` in every cell, so a 500 here and an empty table are
        indistinguishable to the reader.
        """
        response = self.client.post(
            self.url_list("calculationlog"),
            data={"request": _ag()},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        rows = {row["id"]: row for row in response.data["rowData"]}
        self.assertIn(self.log.pk, rows)
        self.assertEqual(rows[self.log.pk]["calculationId"], self.calculation_id)

    # ── 15.58 ────────────────────────────────────────────────────────────
    def test_15_58_many_serves_the_log_of_each_tree_node(self):
        """Scenario 15.58: what the log tree itself reads.

        ``CalculationLogTree`` takes the shape of the run from its own view and
        then asks ``many`` for each node's ``calculation_log``. With this
        failing, a run's log rendered as a tree of empty sections — present,
        titled and silent — which reads as "this run logged nothing".
        """
        response = self.client.get(
            self.url_many("calculationlog"),
            {"ids": [self.log.pk], "serializer": "default"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual([row["id"] for row in response.data], [self.log.pk])
        self.assertIn(
            "the step that ran before the failure",
            response.data[0]["calculation_log"],
        )

    # ── 15.59 ────────────────────────────────────────────────────────────
    def test_15_59_a_model_with_can_read_still_uses_the_remembered_decision(self):
        """Scenario 15.59: the guard against fixing this by deleting the branch.

        BUG-011: the list filter has already asked ``permission_read`` for every
        row it returns, and the serializer must not ask again once per row. The
        capability check added in front of that lookup must therefore not stop a
        ``LexModel`` from reaching it — so a decision remembered for this request
        is still what the serializer serves.
        """
        from lex.core.models.LexModel import PermissionResult

        recorded = []
        import lex.api.security as security

        real_recalled = security.recalled

        def spy(request, kind, instance):
            result = real_recalled(request, kind, instance)
            if type(instance) is LogRootCalc and kind == "read":
                recorded.append(result)
                # Answer for this row so the serializer's own branch is
                # observable: only `name` survives into the payload.
                return PermissionResult.allow_fields({"id", "name"}, "15.59")
            return result

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(security, "recalled", spy)
            response = self.client.get(
                self.url_detail("logrootcalc", self.row.pk),
                {"serializer": "default"},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(recorded, "the serializer never consulted the remembered decision")
        self.assertEqual(response.data.get("name"), "15m-root")
        self.assertNotIn("units_csv", response.data)

    # ── 15.60 ────────────────────────────────────────────────────────────
    def test_15_60_the_log_drawer_finds_the_run_that_failed_and_its_traceback(self):
        """Scenario 15.60: the one query the log drawer asks, end to end.

        ``useLastAuditedRun`` does not read the row's own fields — a model that
        declares neither ``calculation_error_message`` nor ``error_message``
        keeps no traceback on the row, and the newest run that left a *log* can
        be an earlier, successful one. It asks the audit trail instead, for the
        newest entry whose **terminal** status is the one the row ended with:

            calculation_id__startswith=<model>_<pk>_
            status_records.status=failure, ordered -id, perPage 1

        Both filters matter. Without the trailing underscore record 1 matches
        record 11's runs; without the status filter a later ordinary edit of the
        same row, audited under the same prefix, hides the failure. The answer
        must carry ``error_traceback`` for a plain user — without it the drawer
        says "No failure details are available for this run" over a run that
        recorded one (LEX-1555, batch 6u).
        """
        from lex.audit_logging.models.AuditLog import AuditLog
        from lex.audit_logging.models.AuditLogStatus import AuditLogStatus

        trace = "Traceback (most recent call last):\n  ZeroDivisionError: division by zero"
        failed = AuditLog.objects.create(
            author="15m",
            resource="logrootcalc",
            action="update",
            calculation_id=self.calculation_id,
            object_id=self.row.pk,
        )
        AuditLogStatus.objects.create(audit_log=failed, status="failure", error_traceback=trace)
        # A later ordinary edit of the same row, audited under the same prefix.
        edited = AuditLog.objects.create(
            author="15m",
            resource="logrootcalc",
            action="update",
            calculation_id=f"logrootcalc_{self.row.pk}_update_{uuid4().hex}",
            object_id=self.row.pk,
        )
        AuditLogStatus.objects.create(audit_log=edited, status="success")

        response = self.client.get(
            self.url_list("auditlog"),
            {
                "page": 1,
                "perPage": 1,
                "ordering": "-id",
                "calculation_id__startswith": f"logrootcalc_{self.row.pk}_",
                "status_records.status": "failure",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["calculation_id"], self.calculation_id)
        self.assertEqual(results[0]["error_traceback"], trace)
