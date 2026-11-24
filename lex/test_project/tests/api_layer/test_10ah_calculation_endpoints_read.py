"""Live calculation logs and running calculations, only for records the caller may read.

Intent
------
A calculation's log and its run state are data of the record it runs on. Two
endpoints handed them to every signed-in user, whatever the record's
``permission_read`` said:

* ``GET api/init-calculation-logs?calculation_record=<model>_<pk>&calculation_id=<id>``
  (``InitCalculationLogs``) - the lines a calculation has logged so far, the
  backfill the log panel shows before the WebSocket stream takes over;
* ``GET api/active-calculations`` (``ActiveCalculations``) - every running
  calculation: the record's display name, its model and primary key, and the
  calculation id, which is all the first endpoint needs.

The calculation-log tree and its PDFs already show only logs of records the
caller may read (4.103-4.104, ``LEX_SEC_INTERNAL``). These two endpoints follow
the same rule: a caller sees the log and the run of a record it may read, a
superuser sees every one. A refused log answers ``{"logs": null}``, exactly what
a record with no log answers, so the panel shows nothing and learns nothing,
and the frontend, which already copes with a missing log, keeps working. The
Instance Controller asks ``api/active-calculations`` with the instance's own key
(``LEX_API_KEY``) before a release, to warn the operator; that key still sees
every run. ``LEX_SEC_INTERNAL=false`` (or ``LEX_LEGACY_SECURITY=true``) restores
the open behaviour.

Cluster 10ah - scenarios 10.198-10.201. Type: E.
Covers: lex/api/views/calculations/InitCalculationLogs.py,
lex/api/views/calculations/ActiveCalculations.py,
lex/api/security.py (sees_every_calculation, calculation_record_readable),
lex/core/security_flags.py (the INTERNAL flag these endpoints now follow).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10ah_calculation_endpoints_read.py -v
"""

from __future__ import annotations

import os
from unittest import mock

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from lex.audit_logging.utils.CacheManager import CacheManager
from lex.core.calculation_start import calculation_record_id
from lex.core.models.CalculationModel import CalculationModel
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import GUARDED_CALC_MODELS, ApiLayerGuardedCalc

pytestmark = pytest.mark.api_layer

INSTANCE_KEY = "cluster-10ah-instance-key"


def _env(**extra):
    """The process environment without any security switch, plus ``extra``."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("LEX_SEC_", "LEX_LEGACY_SECURITY"))}
    clean.update(extra)
    return mock.patch.dict(os.environ, clean, clear=True)


def _legacy():
    return _env(LEX_SEC_INTERNAL="false")


class TestCluster10ah_CalculationEndpointsRead(E2ETestCase):
    """Cluster 10ah: the live log and the active-calculations list follow ``permission_read``."""

    e2e_models = GUARDED_CALC_MODELS
    # The real store and the real log cache: these tests read back what a run wrote.
    e2e_unpatch = {"mark_in_progress", "store_message", "build_cache_key"}

    def setUp(self) -> None:
        super().setUp()
        defaults = _env()
        defaults.start()
        self.addCleanup(defaults.stop)
        ActiveCalculationStateStore.clear_all()
        self.addCleanup(ActiveCalculationStateStore.clear_all)
        self.logs_url = reverse("process_admin_rest_api:init-calculation-logs")

    def make_superuser(self) -> None:
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])

    def _running(self, name: str, *, secret: bool = False) -> tuple[str, str]:
        """Start a run on a new row the way a Calculate click registers it, with one logged line."""
        calc = ApiLayerGuardedCalc.objects.create(name=name, is_secret=secret)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save(skip_hooks=True)
        record = calculation_record_id(calc)
        calculation_id = f"{record}_update_10ah"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record,
            calculation_id=calculation_id,
            record=str(calc),
            model_name=calc._meta.object_name,
            model_label=calc._meta.label_lower,
            record_pk=calc.pk,
        )
        self._log(record, calculation_id, f"{name}: step 1 done")
        return record, calculation_id

    def _log(self, record: str, calculation_id: str, line: str) -> None:
        key = CacheManager.build_cache_key(record, calculation_id)
        self.assertTrue(CacheManager.store_message(key, line), "the log cache must accept the line")
        self.addCleanup(CacheManager.cleanup_specific_key, key)

    def _logs(self, record: str, calculation_id: str, client: APIClient | None = None):
        response = (client or self.client).get(
            self.logs_url, {"calculation_record": record, "calculation_id": calculation_id}
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        return response.json()

    def _active(self, client: APIClient | None = None, **headers):
        response = (client or self.client).get("/api/active-calculations", **headers)
        self.assertEqual(response.status_code, 200, response.content[:300])
        return response

    @staticmethod
    def _record_ids(response) -> list[str]:
        return sorted(entry["record_id"] for entry in response.json()["active_calculations"])

    # -- 10.198 --------------------------------------------------------
    def test_10_198_live_log_only_for_a_record_the_caller_may_read(self) -> None:
        """
        Scenario 10.198: init-calculation-logs answers a refused record like one with no log.
        Given: a running calculation on an open row and one on a secret row
               (permission_read refuses it), each with a logged line
        When: a signed-in user asks for each log by its record and calculation id
        Then: the open row's line comes back; the secret row's answer is
              {"logs": null}, exactly what a record with no log answers, so its
              line never leaves the server.
        """
        open_record, open_id = self._running("Open forecast")
        secret_record, secret_id = self._running("Board bonus pool", secret=True)

        self.assertEqual(
            self._logs(open_record, open_id),
            {"logs": "Open forecast: step 1 done"},
            "a readable record's log must still reach the log panel",
        )
        self.assertEqual(
            self._logs(secret_record, secret_id),
            {"logs": None},
            "the log of a record the caller may not read leaked through api/init-calculation-logs",
        )
        self.assertEqual(
            self._logs(open_record, "no-such-run"),
            {"logs": None},
            "a run with no log answers null; a refusal must look the same",
        )

    # -- 10.199 --------------------------------------------------------
    def test_10_199_live_log_superuser_legacy_and_unknown_records(self) -> None:
        """
        Scenario 10.199: who still sees every live log, and an id that names no record.
        Given: the secret row's running calculation, and a log cached under ids
               that name no record (a primary key with no row, a model that does
               not exist)
        When: the signed-in user asks with LEX_SEC_INTERNAL=false, then without
              it for the record-less logs, then as a superuser
        Then: the switch restores the open answer; without it a log that belongs
              to no readable record is null (operator data, as in the log tree);
              the superuser reads all three.
        """
        secret_record, secret_id = self._running("Board bonus pool", secret=True)
        no_row = (f"{ApiLayerGuardedCalc._meta.model_name}_987654", "run-without-row")
        no_model = ("nosuchmodel_1", "run-without-model")
        for record, calculation_id in (no_row, no_model):
            self._log(record, calculation_id, f"{record}: orphan line")

        with _legacy():
            self.assertEqual(
                self._logs(secret_record, secret_id),
                {"logs": "Board bonus pool: step 1 done"},
                "LEX_SEC_INTERNAL=false must restore the open log endpoint",
            )
        for record, calculation_id in (no_row, no_model):
            self.assertEqual(
                self._logs(record, calculation_id),
                {"logs": None},
                f"{record}: a log that names no readable record must not reach a non-superuser",
            )

        self.make_superuser()
        self.assertEqual(self._logs(secret_record, secret_id), {"logs": "Board bonus pool: step 1 done"})
        self.assertEqual(self._logs(*no_row), {"logs": f"{no_row[0]}: orphan line"})
        self.assertEqual(self._logs(*no_model), {"logs": f"{no_model[0]}: orphan line"})

    # -- 10.200 --------------------------------------------------------
    def test_10_200_active_calculations_lists_readable_records_only(self) -> None:
        """
        Scenario 10.200: active-calculations lists only runs on records the caller may read.
        Given: a running calculation on an open row and one on a secret row
        When: a signed-in user, then a project API key, GET api/active-calculations
        Then: each sees the open run only; the secret row's display name, record
              id and calculation id appear nowhere in the response.
        """
        open_record, _ = self._running("Open forecast")
        secret_record, secret_id = self._running("Board bonus pool", secret=True)

        for caller in ("signed-in user", "project API key"):
            if caller == "project API key":
                self.authenticate_as_api_key(name="Reporting job")
            with self.subTest(caller=caller):
                response = self._active()
                self.assertEqual(
                    self._record_ids(response),
                    [open_record],
                    f"{caller}: api/active-calculations listed a run on a record it may not read",
                )
                for leaked in (b"Board bonus pool", secret_record.encode(), secret_id.encode()):
                    self.assertNotIn(leaked, response.content, f"{caller}: {leaked!r} leaked")

    # -- 10.201 --------------------------------------------------------
    def test_10_201_active_calculations_superuser_instance_key_and_legacy(self) -> None:
        """
        Scenario 10.201: the operator's callers still see every run.
        Given: the open and the secret running calculations, and the instance's
               own key in LEX_API_KEY
        When: the signed-in user asks with LEX_SEC_INTERNAL=false; the Instance
              Controller asks with the instance key (the Api-Key and
              Authorization headers it sends); the user asks as a superuser
        Then: all three list both runs, so the release warning still names
              every calculation a restart would interrupt.
        """
        open_record, _ = self._running("Open forecast")
        secret_record, _ = self._running("Board bonus pool", secret=True)
        both = sorted([open_record, secret_record])

        with _legacy():
            self.assertEqual(self._record_ids(self._active()), both, "the switch must restore the full list")

        with _env(LEX_API_KEY=INSTANCE_KEY):
            controller = APIClient()
            response = self._active(
                controller,
                HTTP_API_KEY=INSTANCE_KEY,
                HTTP_AUTHORIZATION=f"Api-Key {INSTANCE_KEY}",
            )
        self.assertEqual(
            self._record_ids(response),
            both,
            "the Instance Controller's release warning must keep seeing every running calculation",
        )

        self.make_superuser()
        self.assertEqual(self._record_ids(self._active()), both, "a superuser sees every run")
