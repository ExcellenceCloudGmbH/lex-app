"""A form's values on the server: what each kind of field accepts, and which handler receives which fields.

A form is submitted once, to one handler. Before that handler's body runs, lex-app checks what the
browser sent against the fields the page declared -- required, and the field's kind -- and turns
each value into a Python value. A value that does not pass never reaches the body: its message is
kept for the field (``ui_invalid``) and the handler ends there.

A choice searched on the server (``options=`` a ``@ui.rows`` handler) is the one field that talks to
the server before the form is sent: :func:`ask` records what the user typed, in order, and
:func:`search` runs the rows handler for it without holding the tab,
keeps a page of matches for the browser (``ui_matches``) and remembers, on the server only, every
value it offered to the tab (``_ui_offered``). The form's value is checked against those.
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

#: How many matches a searched choice offers at a time: the first page of what the search found.
MATCHES = 50
#: Most values a searched choice remembers having offered to a tab; past it, only the latest search's count.
OFFERED = 1000

_YES = (True, "on", "true", "True", "1", "yes")


@dataclasses.dataclass(frozen=True)
class Expect:
    """One field as the server checks it."""

    key: str
    kind: str = "text"
    required: bool = False
    options: tuple | None = None        # a fixed choice's values (one or several)
    options_field: str = ""             # options from the state: the field of the handler's state holding them
    search: str = ""                    # searched on the server: the field's id, under which the offered values are kept


@dataclasses.dataclass(frozen=True)
class Receives:
    """What a handler receives as its ``values``: the fields, and for a wizard which step holds which."""

    fields: tuple = ()
    steps: tuple = ()                   # per step, the keys of its fields; empty for a form without steps
    where: str = ""                     # the page and slot that declared it, for a message
    dialog: bool = False                # a button's ask= dialog: an action's stays open until it succeeds


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


@dataclasses.dataclass(frozen=True)
class Search:
    """A choice searched on the server: the rows handler that searches, and the field's key in ``ui_invalid``."""

    source: Any
    invalid: str


#: Searched field id (``module.State.handler|key``) -> its search. Filled when a page is built; the
#: browser names only the id, and an id that is not here is ignored.
_SEARCHES: dict[str, Search] = {}


def searched(field: str, source: Any, invalid: str) -> str:
    """Record that the field ``field`` is searched by the rows handler ``source``.

    Returns ``""``, or the rows handler the field already searches when it is another one.
    """
    known = _SEARCHES.get(field)
    if known is not None and known.source.fn.__ui_key__ != source.fn.__ui_key__:
        return known.source.fn.__ui_key__
    _SEARCHES[field] = Search(source, invalid)
    return ""


def _offer(key: str, raw: Any, text: str) -> dict[str, tuple[Any, str]]:
    """A page of the rows ``raw`` as matches: the value as text -> (the row's own id, its label).

    ``text`` is what the user typed when the rows handler does not search itself: its words are
    then matched against the labels, folded as RFDS's browser filter folds them.
    """
    from rfds.components.inputs.combobox import fold_match

    from lex.lex_app.reflex.ui import rows

    found: dict[str, tuple[Any, str]] = {}
    for index, row in enumerate(rows.checked(key, raw)):
        missing = [name for name in ("id", "label") if row.get(name) in (None, "")]
        if missing:
            raise rows.RowsError(f"{key}: row {index} has no {' and no '.join(missing)}. The rows of a choice give "
                                 "`id`, the value the handler receives, and `label`, the words the user reads: "
                                 "`{\"id\": order.id, \"label\": order.name}`.")
        ident, label = row["id"], str(row["label"])
        if text and not fold_match(text, label):
            continue
        value = str(ident)
        if value in found:
            raise rows.RowsError(f"{key}: two rows have the id {value!r}; `id` is different on every row.")
        found[value] = (ident, label)       # the handler receives the row's own id: an int stays an int
        if len(found) == MATCHES:
            break
    return found


def forget_offers(state: Any, *, visit: bool = False) -> None:
    """Drop what searched choices offered to another user of the tab; on a new visit, the matches shown.

    ``state`` is the tab's ``ui.State`` (call while holding its lock). The offers stay for the same
    user across visits: a kept form accepts a value chosen before the user left (1.935).
    """
    from lex.lex_app.reflex.ui import context

    user = context.user_id_of(state)
    if str(state._ui_offered_to or "") != user:
        if state._ui_offered or state.ui_matches:
            state._ui_offered, state.ui_matches = {}, {}
        state._ui_offered_to = user
    elif visit and state.ui_matches:
        state.ui_matches = {}


def ask(state: Any, field: str, text: Any) -> Any:
    """The user typed ``text`` into the searched field ``field`` (``""`` when it opens).

    Runs in order with the tab's other events, and at once: it records what was typed as the
    field's latest search and returns the event that runs it without holding the tab
    (:func:`search`). ``None`` for a field the server does not know.
    """
    if str(field) not in _SEARCHES:
        return None
    forget_offers(state)
    text = " ".join(str(text or "").split())[:200]
    held = dict(state._ui_searching)
    number = int((held.get(field) or [0])[0]) + 1
    state._ui_searching = {**held, field: [number, text]}
    return type(state).ui_search_run(field, number)


def _latest(state: Any, field: str, number: int) -> str | None:
    """What was typed for the search ``number`` of ``field``; ``None`` once a newer search was asked."""
    held = dict(state._ui_searching).get(field) or [0, ""]
    return str(held[1]) if int(held[0]) == int(number) else None


async def search(state: Any, field: str, number: int) -> None:
    """Run the search ``number`` of the searched field ``field`` and offer its matches.

    ``state`` is the tab's ``ui.State`` behind a background proxy: the rows handler runs as the user,
    as a table's does, on a copy taken under the user's lock, while the tab's other events go on.
    Its matches are offered only if no newer search of the same field was asked meanwhile; each
    field has its own, so two searched fields never cancel each other. A failure is shown at the
    field, never the exception's text.
    """
    from lex.lex_app.reflex.ui import rows

    found = _SEARCHES.get(str(field))
    if found is None:
        return
    source = found.source
    key = source.fn.__ui_key__
    async with state:
        text = _latest(state, field, number)
        if text is None:
            return
        owner = await state.get_state(source.state)
        try:
            draft = rows.copy_of(owner)
        except Exception as exc:  # noqa: BLE001 - reported at the field, below
            draft, failure = None, exc
        else:
            failure = None
    offered: dict = {}
    if failure is None:
        try:
            raw = await rows.read(source, owner, search=text, draft=draft)
            offered = _offer(key, raw, "" if rows.takes_search(source) else text)
        except Exception as exc:  # noqa: BLE001 - as a table: logged with a reference, never the exception's text
            failure = exc
    async with state:
        if _latest(state, field, number) is None:
            return                                  # a newer search of this field was asked: its matches count
        if failure is not None:
            state.ui_invalid = {**dict(state.ui_invalid), found.invalid: rows.describe(failure, key, "")}
            state.ui_matches = {**dict(state.ui_matches), field: []}
            return
        state.ui_matches = {**dict(state.ui_matches),
                            field: [{"value": value, "label": label} for value, (_, label) in offered.items()]}
        kept = {**dict(dict(state._ui_offered).get(field) or {}), **{value: one for value, (one, _) in offered.items()}}
        if len(kept) > OFFERED:
            kept = {value: one for value, (one, _) in offered.items()}
        state._ui_offered = {**dict(state._ui_offered), field: kept}


#: Handler key -> its state class, for the forms declared ``keep=True``: their result stays across visits.
_KEPT: dict[str, Any] = {}
#: The full names of the states a kept form's result lives in: the submit handler's, and those of the
#: ``@ui.rows`` handlers its result's tables read. Their private values stay across visits and a load
#: does not reset them.
_KEPT_STATES: set[str] = set()


def keep(handler: Any, states: Any = ()) -> None:
    """Record that the form of ``handler`` keeps its result when the user leaves the page and comes back.

    ``states``: the other states its result lives in (the states of the rows handlers it reads).
    """
    _KEPT[runs.key_of(handler)] = handler.state
    _KEPT_STATES.update(one.get_full_name() for one in (handler.state, *states))


def kept() -> dict[str, Any]:
    """Handler key -> state class, for every form declared ``keep=True``."""
    return dict(_KEPT)


def keeps(state_cls: Any) -> bool:
    """Whether a kept form's result lives in ``state_cls`` (a state class, or its full name): its values stay
    across visits, a load keeps it."""
    return (state_cls if isinstance(state_cls, str) else state_cls.get_full_name()) in _KEPT_STATES


def gone_message() -> str:
    """What a kept form says when the result it kept is no longer there."""
    return results.message("lex.ui.form.gone", "The last result is no longer available: results are kept only "
                                               "for a while. Send the form again.")


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
    """Several of the options: a list of their own values in the order of the options, each once; ``[]`` for none.

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
        own = {str(one): one for one in allowed}            # each option's own value, by the text the browser sent
        if any(one not in own for one in chosen):
            return [], _say("options", "Choose from the options.")
        chosen = [one for text, one in own.items() if text in chosen]
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
        if expect.search:                       # only what the server offered to this tab
            allowed = options or {}
        if allowed is None:
            return value, ""
        own = dict(allowed) if expect.search else {str(one): one for one in allowed}
        if value not in own:
            return None, _say("option", "Choose one of the options.")
        return own[value], ""                   # the option's own value: an int stays an int
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
        if expect.search:
            if state is not None:
                forget_offers(state)                # offered to another user of the tab: not this one's
            held = getattr(state, "_ui_offered", None) if state is not None else None
            options = dict(dict(held or {}).get(expect.search) or {})
        values[expect.key], problem = clean(expect, given.get(expect.key), options)
        if problem:
            problems[expect.key] = problem
    return values, problems


def _set_invalid(state: Any, key: str, problems: dict) -> None:
    kept = {name: text for name, text in dict(state.ui_invalid).items() if not name.startswith(f"{key}|")}
    state.ui_invalid = {**kept, **{f"{key}|{field}": text for field, text in problems.items()}}


def receive(state: Any, key: str, fn: Any, receives: Receives, args: tuple, kwargs: dict,
            kind: str = "action") -> tuple[tuple, str, bool]:
    """Check the values a handler was sent, before its body runs. Call while holding the user's lock.

    Returns (the arguments for the body, with the values checked and typed; the run's first other
    argument as text, for the view; whether the body runs). The body does not run while a field has
    a problem -- the messages are kept for the fields -- or while a wizard has steps left: its
    Continue button sends the same form, and only the last step's reaches the body. The form's last
    refusal and result leave: they were for other input. A dialog that starts a job closes now, so
    the user keeps working; one that runs an action stays open until it succeeds (:func:`done`).
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
    state.ui_refused = {name: one for name, one in dict(state.ui_refused).items() if name != key}
    if go and kind == "job" and state.ui_asking:                # the dialog that asked closes
        state.ui_asking, state.ui_asking_args = "", []
    return (*others, values), arg, go


def refused(state: Any, key: str, title: str, message: str) -> None:
    """The handler refused what its form sent: kept for the form, shown above its submit button."""
    state.ui_refused = {**dict(state.ui_refused), key: {"title": str(title or ""), "message": str(message)}}


def done(state: Any, key: str, *, close: bool = False) -> None:
    """The handler's body finished: what the form shows after a successful submit appears.

    ``close``: the handler is an action whose dialog asked; it closes now that the action succeeded.
    """
    state.ui_done = {**dict(state.ui_done), key: True}
    if close and state.ui_asking:
        state.ui_asking, state.ui_asking_args = "", []
