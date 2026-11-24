"""``ui.private``: a state field whose value stays in this server process's memory, for its user only.

A backend field (``_name``) stays on the server but is part of the state, and Reflex writes the whole
state to Redis (or to disk in development): personal data there outlives the page and has no idle
limit. A private field holds its value here instead, in this process's memory, keyed by the browser
tab, the state and the field, and bound to the signed-in user who wrote it. The state itself holds
only a random stamp per field (``_ui_private``), so a reader can tell "nothing was kept" from "what
was kept is gone".

A value is gone after its idle time (no read and no write), after its maximum age (since it was
written), when the tab opens a page again (unless ``keep=True``), when another process or another
user reads the state, and when the process restarts or keeps too many values. Reading a gone value
raises :class:`Gone`, a refusal the user is shown in plain words; it never reads as stale data or
as another user's.
"""

from __future__ import annotations

import collections
import copy
import dataclasses
import os
import threading
import time
import uuid
from typing import Any

from lex.lex_app.reflex.ui import results

#: How many private values this process keeps at most; the least recently used goes first.
_SETTING = ("LEX_UI_PRIVATE_VALUES", 5000)


def _now() -> float:
    return time.monotonic()


def capacity() -> int:
    name, default = _SETTING
    try:
        wanted = int(os.getenv(name) or default)
    except ValueError:
        wanted = default
    return max(1, wanted)


class _Gone:
    """What a private field holds, in a copy of the state, once its value is gone."""

    _instance: "_Gone | None" = None

    def __new__(cls) -> "_Gone":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "ui.testing.GONE"

    def __copy__(self) -> "_Gone":
        return self

    def __deepcopy__(self, memo: Any) -> "_Gone":
        return self

    def __reduce__(self) -> str:
        return "GONE"


GONE = _Gone()


class Gone(results.Refuse):
    """A private field was read after its value was gone: the user is told so, and to start again."""

    def __init__(self) -> None:
        super().__init__(results.message(
            "lex.ui.private.gone", "This is no longer available: it is kept only for a while. Start again."))


@dataclasses.dataclass
class _Entry:
    owner: str
    stamp: str
    value: Any
    written: float
    used: float
    field: "Private"


_STORE: "collections.OrderedDict[tuple[str, str, str], _Entry]" = collections.OrderedDict()
_LOCK = threading.Lock()


class Private:
    """The descriptor ``ui.private()`` puts on a state: reads and writes go to this process's memory."""

    def __init__(self, idle_minutes: float, max_hours: float, keep: bool):
        self.idle_minutes = idle_minutes
        self.max_hours = max_hours
        self.keep = keep
        self.name = ""
        self.owner: Any = None

    def __set_name__(self, owner: Any, name: str) -> None:
        from lex.lex_app.reflex.ui.state import State

        if not issubclass(owner, State):
            raise TypeError(f"{owner.__name__}.{name}: ui.private() is a field of a ui.State; "
                            f"write `class {owner.__name__}(ui.State)`.")
        if not name.startswith("_") or name.startswith("__"):
            raise TypeError(f"{owner.__name__}.{name}: a private field's name starts with one '_', which keeps "
                            f"it out of the browser: `_{name.lstrip('_')}: dict = ui.private()`.")
        annotations = owner.__dict__.get("__annotations__") or {}
        if name not in annotations:
            raise TypeError(f"{owner.__name__}.{name}: write the field's type, as for every field: "
                            f"`{name}: dict = ui.private()`. Its value is empty of that type until something "
                            f"is kept.")
        self.name, self.owner = name, owner

    def empty(self) -> Any:
        """The value when nothing is kept: the empty value of the field's type (``{}``, ``[]``, ``""``, 0, None)."""
        from reflex_base.utils.types import get_default_value_for_type

        try:
            hint = self.owner._get_type_hints()[self.name]
            return copy.deepcopy(get_default_value_for_type(hint))
        except Exception:  # noqa: BLE001 - an unresolvable type: nothing
            return None

    def __get__(self, state: Any, cls: Any = None) -> Any:
        if state is None:
            return self
        value = peek(state, self)
        if value is GONE:
            raise Gone()
        return value

    def __set__(self, state: Any, value: Any) -> None:
        if isinstance(value, Private):     # Reflex sets every field's declared default when a state is made
            return
        put(state, self, value)


def private(*, idle_minutes: float = 60, max_hours: float = 8, keep: bool = False) -> Any:
    """A state field whose value stays in this server process's memory, for the user who wrote it.

    Declared like a backend field, with its type, and read and written like one::

        class ReportState(ui.State):
            _lines: list[dict] = ui.private()

            @ui.job
            def build(self, values):
                self._lines = invoices_of(self.user.id, values["customer"])

    Never in the browser, never in Redis or a state pickle: only here, for this tab and this user.
    It is gone after ``idle_minutes`` without a read or a write, ``max_hours`` after it was written,
    when the tab opens a page again (``keep=True``: it stays across visits, within those times), and
    whenever another server process or another user reads the state. Reading it then fails with a
    plain sentence -- "This is no longer available: it is kept only for a while. Start again." --
    shown as every refusal is (a toast, a table's message), never with old or another user's data.
    A load resets it like every field, unless ``keep=True`` and it is still kept.
    """
    for name, given in (("idle_minutes", idle_minutes), ("max_hours", max_hours)):
        if isinstance(given, bool) or not isinstance(given, (int, float)) or given <= 0:
            raise ValueError(f"ui.private({name}=) is a number above 0; got {given!r}")
    if not isinstance(keep, bool):
        raise TypeError(f"ui.private(keep=) is True or False; got {keep!r}")
    return Private(idle_minutes, max_hours, keep)


# -- what a state holds ------------------------------------------------------------------


def fields(state_cls: Any) -> dict[str, Private]:
    """The private fields a state class declares itself (and through its mixins), by name."""
    found: dict[str, Private] = {}
    mixins = getattr(state_cls, "_mixins", lambda: [])()
    for klass in (*mixins, state_cls):
        for name, value in vars(klass).items():
            if isinstance(value, Private):
                found[name] = value
    return found


def _raw(state: Any) -> Any:
    return getattr(state, "__wrapped__", state)


def _where(state: Any, field: Private) -> tuple[tuple[str, str, str], str]:
    """The store's key for ``field`` of ``state`` (tab, state, field), and the stamp's name in the state."""
    target = _raw(state)
    try:
        tab = str(target.router.session.client_token or "")
    except Exception:  # noqa: BLE001 - a state without router data comes from no tab
        tab = ""
    name = type(target).get_full_name()
    return (tab, name, field.name), f"{name}.{field.name}"


def _owner(state: Any) -> str:
    from lex.lex_app.reflex.ui import context

    return context.user_id_of(_raw(state))


def _expired(entry: _Entry, now: float) -> bool:
    return (now - entry.used > entry.field.idle_minutes * 60) or (now - entry.written > entry.field.max_hours * 3600)


def peek(state: Any, field: Private | str) -> Any:
    """The kept value, the field's empty value when nothing was kept, or :data:`GONE`. Never raises ``Gone``."""
    if isinstance(field, str):
        field = fields(type(_raw(state)))[field]
    key, path = _where(state, field)
    stamp = dict(_raw(state)._ui_private).get(path)
    if not stamp:
        return field.empty()
    now, owner = _now(), _owner(state)
    with _LOCK:
        entry = _STORE.get(key)
        if entry is not None and _expired(entry, now):
            del _STORE[key]
            entry = None
        if entry is None or entry.stamp != stamp or entry.owner != owner:
            return GONE
        entry.used = now
        _STORE.move_to_end(key)
        return entry.value


def put(state: Any, field: Private, value: Any) -> None:
    """Keep ``value`` for ``field`` of ``state``. Its empty value keeps nothing; :data:`GONE` makes it gone."""
    from lex.lex_app.reflex.ui import workcopy

    key, path = _where(state, field)
    target = _raw(state)
    stamps = dict(target._ui_private)
    owner = _owner(state)
    with _LOCK:
        _STORE.pop(key, None)
        if value is GONE:
            stamps[path] = stamps.get(path) or uuid.uuid4().hex     # stamped, and nothing kept under it
        elif not workcopy._differs(value, field.empty()):
            stamps.pop(path, None)                                  # nothing kept: not gone, just empty
        else:
            now = _now()
            stamps[path] = uuid.uuid4().hex
            _STORE[key] = _Entry(owner, stamps[path], value, now, now, field)
            while len(_STORE) > capacity():
                _STORE.popitem(last=False)
    if stamps != dict(target._ui_private):
        target._ui_private = stamps


def gone_in(state: Any) -> bool:
    """Whether a private field of ``state`` kept across visits (``keep=True``) had a value that is gone.

    A field that is not kept leaves with every visit: it does not decide whether a kept form's
    result can show again (a result that reads it says it is gone where it reads it).
    """
    return any(peek(state, field) is GONE for field in fields(type(_raw(state))).values() if field.keep)


# -- the clock and the page opener ---------------------------------------------------------


def leave(tab: str) -> int:
    """The tab opened a page: drop its values that are not kept across visits; returns how many."""
    with _LOCK:
        gone = [key for key, entry in _STORE.items() if key[0] == tab and not entry.field.keep]
        for key in gone:
            del _STORE[key]
    return len(gone)


def sweep() -> int:
    """Drop every value past its idle time or its maximum age; returns how many (the lex.ui clock)."""
    now = _now()
    with _LOCK:
        gone = [key for key, entry in _STORE.items() if _expired(entry, now)]
        for key in gone:
            del _STORE[key]
    return len(gone)


def clear() -> None:
    """Forget every value (tests)."""
    with _LOCK:
        _STORE.clear()
