"""A form's values on the server: what each kind of field accepts, and which handler receives which fields.

A form is submitted once, to one handler. Before that handler's body runs, lex-app checks what the
browser sent against the fields the page declared -- required, and the field's kind -- and turns
each value into a Python value. A value that does not pass never reaches the body: its message is
kept for the field (``ui_invalid``) and the handler ends there.
"""

from __future__ import annotations

import dataclasses
import datetime
import inspect
from decimal import Decimal, InvalidOperation
from typing import Any

from lex.lex_app.reflex.ui import results, runs

#: What a field can be: the words ``ui.fact`` takes for showing a value.
KINDS = ("text", "number", "money", "percent", "date", "datetime", "year", "bool", "choice", "choices")

#: The name of the handler argument that receives a form's values.
VALUES = "values"

_YES = (True, "on", "true", "True", "1", "yes")


@dataclasses.dataclass(frozen=True)
class Expect:
    """One field as the server checks it."""

    key: str
    kind: str = "text"
    required: bool = False
    options: tuple | None = None        # a fixed choice's values (one or several)
    options_field: str = ""             # options from the state: the field of the handler's state holding them


@dataclasses.dataclass(frozen=True)
class Receives:
    """What a handler receives as its ``values``: the fields, and for a wizard which step holds which."""

    fields: tuple = ()
    steps: tuple = ()                   # per step, the keys of its fields; empty for a form without steps
    where: str = ""                     # the page and slot that declared it, for a message


#: Handler id (module and qualified name) -> what it receives. Filled when a page is built.
_RECEIVES: dict[str, Receives] = {}


def ident(handler: Any) -> str:
    """A handler's module-qualified id: two states in different modules may share a name."""
    fn = getattr(handler, "fn", handler)
    body = getattr(fn, "__ui_fn__", fn)
    return f"{body.__module__}.{body.__qualname__}"


def register(handler: Any, receives: Receives) -> None:
    """Record that ``handler`` receives these fields. A handler is the submit of one set of fields."""
    name = ident(handler)
    known = _RECEIVES.get(name)
    if known is not None and (known.fields, known.steps) != (receives.fields, receives.steps):
        raise ValueError(f"`{runs.key_of(handler)}` already receives the fields of {known.where}, which are not "
                         f"the same as these. A handler receives one set of fields: give this form or button a "
                         f"handler of its own.")
    _RECEIVES[name] = receives


def of(fn: Any) -> Receives | None:
    """What the handler whose body is ``fn`` receives; ``None`` when it is no form's or dialog's handler."""
    return _RECEIVES.get(f"{fn.__module__}.{fn.__qualname__}")


def _say(key: str, default: str) -> str:
    return results.message(f"lex.ui.field.{key}", default)


def _number(raw: Any) -> Decimal | None:
    if isinstance(raw, bool):
        return None
    try:
        number = raw if isinstance(raw, Decimal) else Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _zone() -> datetime.tzinfo:
    """The app's time zone: a date and time typed without one is meant in it."""
    try:
        from zoneinfo import ZoneInfo

        from lex.lex_app.reflex.design import format_settings

        return ZoneInfo(format_settings()["timezone"])
    except Exception:  # noqa: BLE001 - no setting, or an unknown zone
        return datetime.timezone.utc


def _several(expect: Expect, raw: Any, options: Any) -> tuple[list, str]:
    """Several of the options: a list of texts in the order of the options, each once; ``[]`` for none.

    A form sends one chosen value alone as a text, and an unticked box group as ``False``.
    """
    if raw is None or raw is False or (isinstance(raw, str) and not raw.strip()):
        chosen: list = []
    elif isinstance(raw, str):
        chosen = [raw]
    elif isinstance(raw, (list, tuple)) and all(isinstance(one, str) for one in raw):
        chosen = list(raw)
    else:
        return [], _say("options", "Choose from the options.")
    allowed = expect.options if expect.options is not None else options
    if allowed is not None:
        allowed = [str(one) for one in allowed]
        if any(one not in allowed for one in chosen):
            return [], _say("options", "Choose from the options.")
        chosen = [one for one in allowed if one in chosen]
    chosen = list(dict.fromkeys(chosen))
    if expect.required and not chosen:
        return [], _say("several", "Choose at least one.")
    return chosen, ""


def clean(expect: Expect, raw: Any, options: Any = None) -> tuple[Any, str]:
    """``raw`` (what the browser sent for one field) as (the Python value, the problem or '').

    Nothing entered is ``""`` for text, ``False`` for a yes/no, ``[]`` for several choices and
    ``None`` for every other kind. A value that is already of its kind (a test passing a
    ``Decimal``) is kept.
    """
    kind = expect.kind
    if kind == "choices":
        return _several(expect, raw, options)
    if kind == "bool":
        value = raw in _YES
        return value, (_say("tick", "Tick this box to continue.") if expect.required and not value else "")
    if isinstance(raw, (list, tuple)):          # a name sent twice: the last one counts
        raw = raw[-1] if raw else ""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        empty = "" if kind == "text" else None
        if not expect.required:
            return empty, ""
        return empty, (_say("choose", "Choose one.") if kind == "choice" else _say("required", "Enter a value."))
    if kind == "text":
        return str(raw).strip(), ""
    if kind == "choice":
        value = str(raw)
        allowed = expect.options if expect.options is not None else options
        if allowed is not None and value not in {str(one) for one in allowed}:
            return None, _say("option", "Choose one of the options.")
        return value, ""
    if kind in ("number", "money", "percent"):
        number = _number(raw)
        if number is None:
            return None, _say("number", "Enter a number, such as 1250.50.")
        if kind == "number" and number == number.to_integral_value():
            return int(number), ""
        return number, ""
    if kind == "year":
        text = str(raw).strip()
        if not (text.isascii() and text.isdigit() and len(text) == 4):
            return None, _say("year", "Enter a year with four digits, such as 2026.")
        return int(text), ""
    if kind == "date":
        if isinstance(raw, datetime.date) and not isinstance(raw, datetime.datetime):
            return raw, ""
        try:
            return datetime.date.fromisoformat(str(raw).strip()), ""
        except ValueError:
            return None, _say("date", "Enter a date.")
    if kind == "datetime":
        try:
            moment = raw if isinstance(raw, datetime.datetime) else datetime.datetime.fromisoformat(str(raw).strip())
        except ValueError:
            return None, _say("datetime", "Enter a date and a time.")
        return (moment if moment.tzinfo is not None else moment.replace(tzinfo=_zone())), ""
    raise ValueError(f"unknown field kind {kind!r}")


def check(receives: Receives, raw: Any, state: Any = None) -> tuple[dict, dict]:
    """Every field of ``receives`` read from ``raw``: (the values by key, the problems by key)."""
    given = raw if isinstance(raw, dict) else {}
    values: dict[str, Any] = {}
    problems: dict[str, str] = {}
    for expect in receives.fields:
        options = getattr(state, expect.options_field, None) if expect.options_field and state is not None else None
        values[expect.key], problem = clean(expect, given.get(expect.key), options)
        if problem:
            problems[expect.key] = problem
    return values, problems


def _set_invalid(state: Any, key: str, problems: dict) -> None:
    kept = {name: text for name, text in dict(state.ui_invalid).items() if not name.startswith(f"{key}|")}
    state.ui_invalid = {**kept, **{f"{key}|{field}": text for field, text in problems.items()}}


def receive(state: Any, key: str, fn: Any, receives: Receives, args: tuple, kwargs: dict) -> tuple[tuple, str, bool]:
    """Check the values a handler was sent, before its body runs. Call while holding the user's lock.

    Returns (the arguments for the body, with the values checked and typed; the run's first other
    argument as text, for the view; whether the body runs). The body does not run while a field has
    a problem -- the messages are kept for the fields -- or while a wizard has steps left: its
    Continue button sends the same form, and only the last step's reaches the body.
    """
    bound = inspect.signature(fn).bind(None, *args, **kwargs)
    others = tuple(value for name, value in list(bound.arguments.items())[1:] if name != VALUES)
    arg = runs.arg_key(others, {})
    values, problems = check(receives, bound.arguments.get(VALUES), state)
    go = not problems
    if receives.steps:
        last = len(receives.steps) - 1
        step = min(max(int(dict(state.ui_step).get(key, 0)), 0), last)
        wrong = [index for index, keys in enumerate(receives.steps) if any(name in problems for name in keys)]
        if wrong and wrong[0] <= step:
            reached = {name for keys in receives.steps[:step + 1] for name in keys}
            problems = {name: text for name, text in problems.items() if name in reached}
            step, go = wrong[0], False
        elif step < last:                   # this step is fine: the next one, whose fields are not judged yet
            problems, step, go = {}, step + 1, False
        state.ui_step = {**dict(state.ui_step), key: step}
    _set_invalid(state, key, problems)
    state.ui_done = {**dict(state.ui_done), key: False}         # the last result leaves: it was for other input
    if go:
        if state.ui_asking:                                     # the dialog that asked closes
            state.ui_asking, state.ui_asking_args = "", []
    return (*others, values), arg, go


def done(state: Any, key: str) -> None:
    """The handler's body finished: what the form shows after a successful submit appears."""
    state.ui_done = {**dict(state.ui_done), key: True}
