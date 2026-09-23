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
