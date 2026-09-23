"""Cluster 15i — a calculation row learns its latest run.

Scenarios 15.39 – 15.46.

Intent (reported 2026-09-23): "once they are done, we lose the logs and we
would have to do a lot of steps to get them." The live log is served from
cache, and the root calculation purges that cache when it completes, so a
finished run's log had no path back from the row that started it. The durable
copy is in the CalculationLog table; what was missing is the row knowing which
run to open.

The rule is the one the frontend's ``useResolvedCalculationId`` already
applies: a run started from record ``pk`` has an id beginning
``f"{model}_{pk}_"``, and the newest is the highest ``CalculationLog.id``.
Stating it twice, in two languages, is what makes one row open one run in the
table and in every widget.
"""
from __future__ import annotations

from unittest import mock
from uuid import uuid4

import pytest
from django.db import connection
from rest_framework import status

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.audit_logging.utils.latest_calculation import latest_calculation_ids

from . import _CalcLogTestCase
from .models import LogRootCalc

pytestmark = pytest.mark.calculation_logging


def _run(model_name, pk, text="step"):
    """Write one log row for a new run started from ``(model_name, pk)``.

    The id has the exact shape the frontend mints:
    ``{model}_{pk}_update_{uuid}``.
    """
    calculation_id = f"{model_name}_{pk}_update_{uuid4().hex}"
    CalculationLog.objects.create(calculationId=calculation_id, calculation_log=text)
    return calculation_id


def _row(name):
    """A LogRootCalc row, written without running a calculation.

    ``bulk_create`` skips ``save()`` and therefore the calculation hooks — the
    cluster's own idiom, because the test plan forbids ``skip_hooks=True``.
    """
    LogRootCalc.objects.bulk_create(
        [LogRootCalc(name=name, child_mode="log_only", units_csv="")]
    )
    return LogRootCalc.objects.get(name=name)


def _ag(**overrides):
    req = {
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
    req.update(overrides)
    return req


class TestCluster15i_TheRunRule(_CalcLogTestCase):
    """Which run belongs to which row — the rule, independent of the grid."""

    def test_15_39_the_newest_run_by_id_wins(self):
        """Scenario 15.39: a record calculated twice opens its second run.

        Newest by ``id``, not ``timestamp`` — the frontend resolver sorts by
        ``id DESC``, and two rules would open two different runs.
        """
        _run("logrootcalc", 7)
        newest = _run("logrootcalc", 7)

        self.assertEqual(latest_calculation_ids("logrootcalc", [7]), {"7": newest})

    def test_15_40_record_1_never_resolves_record_11s_runs(self):
        """Scenario 15.40: the trailing underscore is load-bearing.

        Without it, ``logrootcalc_1`` is a prefix of ``logrootcalc_11_…`` and
        record 1 would open a run it never started.
        """
        eleven = _run("logrootcalc", 11)

        self.assertEqual(latest_calculation_ids("logrootcalc", [1]), {})
        self.assertEqual(latest_calculation_ids("logrootcalc", [1, 11]), {"11": eleven})

    def test_15_41_a_run_belongs_to_the_longest_matching_prefix(self):
        """Scenario 15.41: string keys containing ``_``.

        ``m_a_b_update_…`` starts with both ``m_a_`` and ``m_a_b_``. It can only
        have been started by record ``a_b``: record ``a``'s own runs begin
        ``m_a_update_``. Matched shortest-first, record ``a`` would steal it.
        """
        short = _run("m", "a")
        longer = _run("m", "a_b")

        self.assertEqual(
            latest_calculation_ids("m", ["a", "a_b"]),
            {"a": short, "a_b": longer},
        )

    def test_15_42_a_whole_page_is_one_query(self):
        """Scenario 15.42: never one query per grid row.

        Fifty records with two runs each resolve in a single query. This is the
        property that stops the annotation turning a page load into N+1.
        """
        for pk in range(1, 51):
            _run("logrootcalc", pk)
            _run("logrootcalc", pk)

        with self.assertNumQueries(1):
            result = latest_calculation_ids("logrootcalc", range(1, 51))

        self.assertEqual(len(result), 50)


class TestCluster15i_TheSerializer(_CalcLogTestCase):
    """Only calculation models grow the two fields."""

    def test_15_43_only_calculation_models_carry_the_run_fields(self):
        """Scenario 15.43: declared where they mean something, nowhere else.

        Every other model's rows would otherwise carry two permanent nulls — a
        payload cost on every list in the application, for nothing.
        """
        from lex.api.serializers.base_serializers import model2serializer

        calculation_fields = model2serializer(LogRootCalc)().fields
        other_fields = model2serializer(CalculationLog)().fields

        self.assertIn("lex_reserved_calculation_id", calculation_fields)
        self.assertIn("lex_reserved_has_calculation_log", calculation_fields)
        self.assertNotIn("lex_reserved_calculation_id", other_fields)
        self.assertNotIn("lex_reserved_has_calculation_log", other_fields)

    def test_15_43_the_audit_log_keeps_its_own_answer(self):
        """Scenario 15.43 (second half): the new fields leave the audit log alone.

        AuditLogDefaultSerializer answers ``lex_reserved_has_calculation_log``
        itself, falling back to a query when the list view has not annotated
        the row. ``_wrap_custom_serializer`` builds ``(LexSerializer,
        custom_cls)``, so a same-named getter on LexSerializer would come
        first in the MRO, and every unannotated audit row — a detail view,
        an embed — would silently answer False.
        """
        from lex.api.serializers.base_serializers import _wrap_custom_serializer
        from lex.audit_logging.serializers.AuditLogSerializer import (
            AuditLogDefaultSerializer,
        )

        calculation_id = _run("logrootcalc", 1)
        audit_row = AuditLog.objects.create(
            calculation_id=calculation_id,
            resource="logrootcalc",
            action="calculate",
            author="cluster-15-tests",
        )

        serializer = _wrap_custom_serializer(AuditLogDefaultSerializer, AuditLog)()

        self.assertIs(serializer.get_lex_reserved_has_calculation_log(audit_row), True)


class TestCluster15i_TheGrid(_CalcLogTestCase):
    """What the grid endpoint actually returns — the contract the frontend reads."""

    def _post(self, **overrides):
        return self.client.post(
            self.url_list("logrootcalc"),
            data={"request": _ag(**overrides)},
            format="json",
        )

    def _row_data(self, resp, pk):
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        return {row["id"]: row for row in resp.data["rowData"]}[pk]

    def test_15_44_a_finished_run_is_still_reachable_after_its_cache_is_gone(self):
        """Scenario 15.44: the regression, in one sentence.

        Completion purges the calculation's cache — which is what the live
        endpoint reads, and why the reported log "disappeared". The row must
        still name its run afterwards, and the run's rows must still exist.
        """
        from lex.audit_logging.utils.CacheManager import CacheManager

        row = _row("reported")
        calculation_id = _run("logrootcalc", row.pk, text="the log the user lost")
        CacheManager.cleanup_calculation(calculation_id=calculation_id)

        data = self._row_data(self._post(), row.pk)

        self.assertEqual(data["lex_reserved_calculation_id"], calculation_id)
        self.assertIs(data["lex_reserved_has_calculation_log"], True)
        self.assertTrue(CalculationLog.objects.filter(calculationId=calculation_id).exists())

    def test_15_44_a_row_that_never_ran_says_so(self):
        """Scenario 15.44 (second half): null and false, not absent."""
        row = _row("never-ran")

        data = self._row_data(self._post(), row.pk)

        self.assertIsNone(data["lex_reserved_calculation_id"])
        self.assertIs(data["lex_reserved_has_calculation_log"], False)

    def test_15_45_a_resolution_failure_still_serves_the_page(self):
        """Scenario 15.45: a missing door is recoverable; a grid that won't load is not."""
        row = _row("still-loads")
        _run("logrootcalc", row.pk)

        with mock.patch(
            "lex.audit_logging.utils.latest_calculation.latest_calculation_ids",
            side_effect=RuntimeError("database unavailable"),
        ):
            data = self._row_data(self._post(), row.pk)

        self.assertIsNone(data["lex_reserved_calculation_id"])
        self.assertIs(data["lex_reserved_has_calculation_log"], False)

    def test_15_46_the_prefix_lookup_is_served_by_an_index(self):
        """Scenario 15.46: the guard against someone removing ``db_index``.

        ``calculationId`` is ``db_index=True``; on PostgreSQL Django adds a
        ``text_pattern_ops`` companion, which is what makes ``LIKE 'prefix%'``
        an index probe. Without it, every page load of every calculation table
        scans an append-only log table.
        """
        if connection.vendor != "postgresql":
            self.skipTest("the pattern-ops companion index is PostgreSQL-specific")

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename = %s",
                [CalculationLog._meta.db_table],
            )
            definitions = [row[0] for row in cursor.fetchall()]

        self.assertTrue(
            any('"calculationId"' in d and "pattern_ops" in d for d in definitions),
            f"no pattern-ops index on calculationId; indexes: {definitions}",
        )
