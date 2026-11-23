"""A handler's status: marks on the state, the in-process registry of jobs, and the view helpers."""

from __future__ import annotations

import dataclasses
import threading
import uuid
from typing import Any

#: This backend process. A run started by a process that is gone is cleared (see :func:`sweep`).
BOOT = uuid.uuid4().hex


def key_of(handler: Any) -> str:
    """The key a handler's status is kept under: ``State.handler``."""
    fn = getattr(handler, "fn", handler)
    return str(getattr(fn, "__ui_key__", None) or fn.__qualname__)


def arg_key(args: tuple, kwargs: dict) -> str:
    """The first argument as text ('' without arguments): which row a run is for."""
    values = [*args, *kwargs.values()]
    return str(values[0]) if values else ""


# -- marks on the state (call while holding the user's lock) ---------------------------


def mark_started(s: Any, key: str, arg: str) -> None:
    running = dict(s.ui_running)
    running[key] = [*running.get(key, []), arg]
    s.ui_running = running
    failed = dict(s.ui_failed)
    failed.pop(key, None)
    s.ui_failed = failed
    s._ui_boots = {**dict(s._ui_boots), f"{key}|{arg}": BOOT}


def mark_progress(s: Any, key: str, report: Any) -> None:
    s.ui_progress = {**dict(s.ui_progress), key: float(report.fraction)}
    s.ui_progress_message = {**dict(s.ui_progress_message), key: str(report.message)}


def mark_finished(s: Any, key: str, arg: str, error: str = "") -> None:
    running = dict(s.ui_running)
    left = list(running.get(key, []))
    if arg in left:
        left.remove(arg)
    if left:
        running[key] = left
    else:
        running.pop(key, None)
        progress = dict(s.ui_progress)
        progress.pop(key, None)
        s.ui_progress = progress
        messages = dict(s.ui_progress_message)
        messages.pop(key, None)
        s.ui_progress_message = messages
    s.ui_running = running
    if error:
        s.ui_failed = {**dict(s.ui_failed), key: error}
    boots = dict(s._ui_boots)
    boots.pop(f"{key}|{arg}", None)
    s._ui_boots = boots


# -- the registry of jobs in this process ----------------------------------------------


@dataclasses.dataclass
class Run:
    cancel: bool = False


_ACTIVE: dict[tuple[str, str, str], Run] = {}
_LOCK = threading.Lock()


def claim(token: str, key: str, arg: str) -> Run | None:
    """Claim the run of ``key`` for ``arg`` in one tab; ``None`` when it is already running."""
    with _LOCK:
        if (token, key, arg) in _ACTIVE:
            return None
        run = _ACTIVE[(token, key, arg)] = Run()
        return run


def release(token: str, key: str, arg: str) -> None:
    with _LOCK:
        _ACTIVE.pop((token, key, arg), None)


def request_cancel(token: str, key: str) -> int:
    """Ask every run of ``key`` in one tab to stop at its next yield; returns how many."""
    with _LOCK:
        found = [run for (tab, name, _), run in _ACTIVE.items() if tab == token and name == key]
    for run in found:
        run.cancel = True
    return len(found)


# -- view helpers ----------------------------------------------------------------------


def _as_text(arg: Any) -> Any:
    from reflex.vars.base import Var

    if isinstance(arg, Var):
        return Var(_js_expr=f"String({arg!s})", _var_data=arg._get_all_var_data()).to(str)
    return str(arg)


def running(handler: Any, arg: Any = None) -> Any:
    """In a view: whether ``handler`` is running -- for the call with ``arg``, when given."""
    from lex.lex_app.reflex.ui.state import State

    active = State.ui_running.get(key_of(handler), [])
    return active.length() > 0 if arg is None else active.contains(_as_text(arg))


def failed(handler: Any) -> Any:
    """In a view: the failure message of ``handler``'s last run, or ''."""
    from lex.lex_app.reflex.ui.state import State

    return State.ui_failed.get(key_of(handler), "")


def progress_of(handler: Any) -> dict[str, Any]:
    """In a view: ``{"fraction": ..., "message": ...}`` as last yielded by ``handler``."""
    from lex.lex_app.reflex.ui.state import State

    key = key_of(handler)
    return {"fraction": State.ui_progress.get(key, 0.0), "message": State.ui_progress_message.get(key, "")}
