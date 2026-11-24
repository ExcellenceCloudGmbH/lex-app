"""A calculation's page at run time: the state ``ui.calculation`` makes, and what its handlers do.

Every read and write goes through lex-app's API as the signed-in user (``self.api``): the record is
created and its run started as the user, and the run is followed by reading the record until its
status settles. The page never talks to the process that runs the calculation, so it follows a run
the same way whether lex-app runs it in its own process or on a Celery worker.
Design: ``docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`` (6.19).
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import itertools
import time
from typing import Any
from urllib.parse import quote, urlsplit

from lex.lex_app.reflex.ui import handlers, results
from lex.lex_app.reflex.ui.state import State

#: How long the page waits between two readings of a running calculation.
POLL_SECONDS = 1.0

IN_PROGRESS, SUCCESS, ERROR = "IN_PROGRESS", "SUCCESS", "ERROR"
CANCELLED, ABORTED, NOT_CALCULATED = "CANCELLED", "ABORTED", "NOT_CALCULATED"

#: The query parameter that names a run on the calculation's page.
RUN = "run"


@dataclasses.dataclass(frozen=True)
class Plan:
    """What one calculation page's state needs to know: the model, and the keys its form writes."""

    model: Any
    keys: tuple[str, ...]


def payload(values: dict) -> dict:
    """The form's values in the shapes lex-app's API takes: a decimal as text, a day or an instant as ISO."""
    out: dict = {}
    for name, value in values.items():
        if isinstance(value, decimal.Decimal):
            value = str(value)
        elif isinstance(value, (datetime.date, datetime.datetime)):
            value = value.isoformat()
        out[name] = value
    return out


def error_words(text: Any) -> str:
    """A calculation's error in its own words: what lex-app stored, without the traceback after it."""
    found = str(text or "").strip()
    if not found:
        return ""
    head = found.split("\n\nTraceback", 1)[0].split("\n\n", 1)[0].strip()
    return head or found.splitlines()[0].strip()


def newest_line(log: list) -> str:
    """The last line a run logged, as a sentence: Markdown's marks at its start taken off."""
    for entry in reversed(list(log or ())):
        for line in reversed(str(entry).splitlines()):
            line = line.strip().lstrip("#>-*").strip()
            if line:
                return line
    return ""


def _here(state: Any) -> str:
    """The path of the page the browser is on, without its query."""
    return urlsplit(handlers._location(state)).path or "/"


def _fill(state: Any, plan: Plan, record: dict) -> None:
    """The run's view fields from the record as lex-app's API answered it."""
    state.run = {name: record.get(name) for name in record if not str(name).startswith("lex_reserved")}
    state.run_status = str(record.get("is_calculated") or NOT_CALCULATED)
    state.run_name = str(record.get("short_description") or "")
    message = record.get("calculation_error_message", record.get("error_message", ""))
    state.run_error = error_words(message)


def _read(state: Any, plan: Plan, key: Any) -> dict:
    """The run ``key``, read as the user; a key that is no run is refused in words."""
    from lex.lex_app.reflex.data import LexApiError

    try:
        return state.api.get(plan.model, key)
    except LexApiError as exc:
        if exc.status == 404:
            raise results.Refuse(results.message("lex.ui.calculation.missing", "There is no run {run}.",
                                                 run=key)) from None
        raise


def make_state(plan: Plan, counter: Any) -> type:
    """A ``ui.State`` for one calculation page, with its handlers: ``calculate``, ``open``, ``follow``."""
    import reflex as rx  # noqa: F401 - Reflex builds the class's vars from its annotations

    name = f"Calculation_{plan.model._meta.model_name}_{next(counter)}"
    pk = plan.model._meta.pk.name

    def calculate(self, values):
        record = self.api.create(plan.model, payload(values))
        key = record.get(pk)
        if record.get("is_calculated") != IN_PROGRESS:          # a calculate_on_create model started on its create
            self.api.calculate(plan.model, key)
        return results.goto(f"{_here(self)}?{RUN}={quote(str(key), safe='')}")

    def open(self):  # noqa: A001 - the page's load, named as every page's
        key = self.params.get(RUN)
        if not key:
            return
        record = _read(self, plan, key)
        self.run_id = str(record.get(pk, key))
        _fill(self, plan, record)
        if self.run_status != IN_PROGRESS:
            self.run_log = list(self.api.log(plan.model, self.run_id))

    def follow(self):
        while True:
            record = _read(self, plan, self.run_id)
            _fill(self, plan, record)
            if self.run_status != IN_PROGRESS:
                break
            note = newest_line(self.api.log(plan.model, self.run_id))
            yield results.progress(0, note or results.message("lex.ui.calculation.running", "Running"))
            time.sleep(POLL_SECONDS)
        self.run_log = list(self.api.log(plan.model, self.run_id))

    namespace: dict[str, Any] = {
        "__module__": __name__,
        "__qualname__": name,
        "__annotations__": {"run_id": str, "run": dict[str, Any], "run_status": str, "run_name": str,
                            "run_error": str, "run_log": list[str]},
        "run_id": "", "run": {}, "run_status": "", "run_name": "", "run_error": "", "run_log": [],
    }
    made = {"calculate": (handlers.action, calculate), "open": (handlers.load, open),
            "follow": (lambda fn: handlers.job(fn, stop_on_leave=True), follow)}
    for handler, (decorate, fn) in made.items():
        fn.__name__ = handler
        fn.__qualname__ = f"{name}.{handler}"
        fn.__module__ = __name__
        namespace[handler] = decorate(fn)
    cls = type(name, (State,), namespace)
    # Reachable by name, as a class written in this module would be: Reflex pickles state, and pickle
    # finds a class by its module and name. Every worker builds the same pages in the same order.
    globals()[name] = cls
    cls.__ui_plan__ = plan
    return cls


_STATES: dict[tuple, type] = {}
_COUNTER = itertools.count(1)


def state_for(plan: Plan, identity: tuple) -> type:
    """The state of the calculation page ``identity`` names (its model, title and fields); one per page."""
    key = (plan.model._meta.label_lower, plan.keys, identity)
    found = _STATES.get(key)
    if found is None:
        found = _STATES[key] = make_state(plan, _COUNTER)
    return found
