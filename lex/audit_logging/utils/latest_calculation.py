"""The newest calculation run started from each record on a page.

A calculation row carries ``is_calculated`` and nothing else, so on its own the
grid cannot tell which rows have a finished log to open. This resolves, for a
whole page of rows in one query, the ``calculationId`` of the newest run each
row started.

The rule is the one ``useResolvedCalculationId`` applies in the frontend,
stated here in Python so the table and every widget open the same run for the
same row: a run started from record ``pk`` of model ``m`` has an id beginning
``f"{m}_{pk}_"`` (the frontend mints ``{m}_{pk}_update_{uuid}``), and the newest
is the one with the highest ``CalculationLog.id``.

The trailing underscore is load-bearing: without it, record 1 would match
record 11's runs.

Served by the index ``calculationId`` already has (migration 0005). On
PostgreSQL, Django adds a ``text_pattern_ops`` companion index for an indexed
text field, and that is what serves ``LIKE 'prefix%'``.
"""
from __future__ import annotations

import logging
from functools import reduce
from operator import or_
from typing import Dict, Iterable, Tuple

from django.db.models import Max, Q

logger = logging.getLogger(__name__)


def is_calculation_model(model) -> bool:
    """Whether ``model`` is a ``CalculationModel`` subclass.

    Imported lazily: ``lex.core.models.CalculationModel`` pulls in much of the
    framework, and the serializer layer imports this module.
    """
    from lex.core.models.CalculationModel import CalculationModel

    return isinstance(model, type) and issubclass(model, CalculationModel)


def run_prefix(model_name: str, pk: object) -> str:
    """The id prefix every run started from this record carries."""
    return f"{model_name}_{pk}_"


def latest_calculation_ids(model_name: str, pks: Iterable[object]) -> Dict[str, str]:
    """Map ``str(pk)`` to the ``calculationId`` of the newest run it started.

    Records that never started a run are absent. One query whatever the number
    of records: one ``startswith`` per record, ORed, collapsed to one row per
    run before it leaves the database.
    """
    from lex.audit_logging.models.CalculationLog import CalculationLog

    prefixes = {str(pk): run_prefix(model_name, pk) for pk in pks}
    if not prefixes:
        return {}

    runs = (
        CalculationLog.objects.filter(
            reduce(or_, (Q(calculationId__startswith=p) for p in prefixes.values()))
        )
        .values("calculationId")
        .annotate(newest=Max("id"))
    )

    # Longest prefix first. For string keys containing "_", a run id can start
    # with two of the page's prefixes ("m_a_" and "m_a_b_"); the longer one is
    # the record that started it, because record "a"'s own runs begin
    # "m_a_update_". Integer keys cannot collide.
    by_length = sorted(prefixes.items(), key=lambda item: len(item[1]), reverse=True)
    best: Dict[str, Tuple[int, str]] = {}
    for run in runs:
        calculation_id = run["calculationId"]
        for pk, prefix in by_length:
            if calculation_id.startswith(prefix):
                if pk not in best or run["newest"] > best[pk][0]:
                    best[pk] = (run["newest"], calculation_id)
                break
    return {pk: calculation_id for pk, (_, calculation_id) in best.items()}


def annotate_latest_calculation(rows, model) -> None:
    """Set ``_lex_calculation_id`` and ``_has_calculation_log`` on each row.

    Never raises. A failure costs the log button, not the page: every row is
    marked as having no log and the grid still loads.
    """
    try:
        ids = latest_calculation_ids(model._meta.model_name, [row.pk for row in rows])
    except Exception:
        logger.warning(
            "Could not resolve the latest calculation runs for %s; "
            "serving the page without them.",
            model._meta.label,
            exc_info=True,
        )
        ids = {}
    for row in rows:
        calculation_id = ids.get(str(row.pk))
        row._lex_calculation_id = calculation_id
        row._has_calculation_log = calculation_id is not None
