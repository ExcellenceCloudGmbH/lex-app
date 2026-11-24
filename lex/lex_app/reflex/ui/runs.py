"""A handler's status: marks on the state, the in-process registry of jobs, and the view helpers."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import os
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
    """The first argument as text ('' without arguments): which row a run is for, for the view."""
    values = [*args, *kwargs.values()]
    return str(values[0]) if values else ""


def call_key(fn: Any, args: tuple, kwargs: dict) -> str:
    """Every argument of one call as stable text: what makes two runs of a job the same run.

    The arguments are bound to ``fn``'s signature first, so ``export(2025, 1)`` and
    ``export(year=2025, month=1)`` are the same call; keyword order never matters.
    """
    try:
        bound = inspect.signature(fn).bind(None, *args, **kwargs)
        bound.apply_defaults()
        values = list(bound.arguments.items())[1:]          # without self
    except (TypeError, ValueError):                         # the body will fail on these arguments anyway
        values = [*enumerate(args), *sorted(kwargs.items(), key=lambda item: str(item[0]))]
    return repr(values)


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
    if arg not in left:                     # another run with this key and argument keeps the record
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


def loaded(handler: Any) -> Any:
    """In a view: whether the load ``handler`` has finished since the page opened."""
    from lex.lex_app.reflex.ui.state import State

    return State.ui_loaded.get(key_of(handler), False)


def failed(handler: Any) -> Any:
    """In a view: the failure message of ``handler``'s last run, or ''."""
    from lex.lex_app.reflex.ui.state import State

    return State.ui_failed.get(key_of(handler), "")


def progress_of(handler: Any) -> dict[str, Any]:
    """In a view: ``{"fraction": ..., "message": ...}`` as last yielded by ``handler``."""
    from lex.lex_app.reflex.ui.state import State

    key = key_of(handler)
    return {"fraction": State.ui_progress.get(key, 0.0), "message": State.ui_progress_message.get(key, "")}


def cancel(handler: Any) -> Any:
    """In a view: the event a Cancel button sends for ``handler``."""
    from lex.lex_app.reflex.ui.state import State

    return State.ui_cancel(key_of(handler))


# -- runs whose process is gone ---------------------------------------------------------

HEARTBEAT_SECONDS = 10
HEARTBEAT_TTL_SECONDS = 30
#: Longest a Redis call may take: a sweep runs under the user's lock, a beat must not stall.
REDIS_TIMEOUT_SECONDS = 2

_CLIENT: Any = None


def _redis() -> Any:
    """This process's Redis client, built on first use; ``None`` only when Redis is not configured.

    Raises when the client cannot be built: callers treat that as "unknown", not as "no Redis".
    """
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    from reflex.utils import prerequisites

    client = prerequisites.get_redis()
    if client is not None:
        _CLIENT = client
    return client


def _renew_after_fork() -> None:
    """A forked worker is another process: its own id, and not its parent's Redis connection."""
    global BOOT, _CLIENT
    BOOT = uuid.uuid4().hex
    _CLIENT = None


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_renew_after_fork)


def _heartbeat_key(boot: str) -> str:
    return f"lex:ui:boot:{boot}"


async def alive(boot: str) -> bool:
    """Whether the backend process ``boot`` is still running."""
    if boot == BOOT:
        return True
    try:
        redis = _redis()
        if redis is None:
            return False  # one process holds every tab: another boot id is a process that ended
        return bool(await asyncio.wait_for(redis.exists(_heartbeat_key(boot)), REDIS_TIMEOUT_SECONDS))
    except Exception:  # noqa: BLE001 - Redis unreachable or slow: do not clear what may still run
        return True


async def sweep(s: Any, alive: Any = None) -> int:
    """Clear the marks of runs whose process is gone; returns how many. Call under the lock.

    One time budget for the whole sweep and one question per process; marks left unanswered stay
    for the next page open.
    """
    check = alive or globals()["alive"]
    boots = dict(s._ui_boots)
    gone: dict[str, bool] = {}

    async def ask() -> None:
        for boot in dict.fromkeys(boots.values()):
            if boot == BOOT:
                continue
            try:
                gone[boot] = not await check(boot)
            except Exception:  # noqa: BLE001 - unknown: leave the marks
                gone[boot] = False

    try:
        await asyncio.wait_for(ask(), REDIS_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        pass
    cleared = 0
    for entry, boot in boots.items():
        if gone.get(boot):
            key, _, arg = entry.partition("|")
            mark_finished(s, key, arg)
            while arg in dict(s.ui_running).get(key, []):   # every run under this record
                mark_finished(s, key, arg)
            cleared += 1
    return cleared


async def _close(client: Any) -> None:
    closer = getattr(client, "aclose", None) or getattr(client, "close", None)
    if closer is None:
        return
    result = closer()
    if inspect.isawaitable(result):
        await asyncio.wait_for(result, REDIS_TIMEOUT_SECONDS)


@contextlib.asynccontextmanager
async def heartbeat(app: Any):
    """A lifespan task: while the backend runs, keep this process's heartbeat in Redis.

    The same clock drops the ``ui.private`` values that are past their idle time or maximum age.
    """
    global _CLIENT

    async def beat() -> None:
        from lex.lex_app.reflex.ui import private

        while True:
            with contextlib.suppress(Exception):
                private.sweep()         # private values past their idle time or age leave memory
            try:
                redis = _redis()
                if redis is not None:
                    await asyncio.wait_for(
                        redis.set(_heartbeat_key(BOOT), "1", ex=HEARTBEAT_TTL_SECONDS), REDIS_TIMEOUT_SECONDS
                    )
            except Exception:  # noqa: BLE001 - a missed beat only delays a sweep
                pass
            await asyncio.sleep(HEARTBEAT_SECONDS)

    task = asyncio.ensure_future(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        try:
            client = _redis()
            if client is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(client.delete(_heartbeat_key(BOOT)), REDIS_TIMEOUT_SECONDS)
                with contextlib.suppress(Exception):
                    await _close(client)
        except Exception:  # noqa: BLE001 - nothing to clean up
            pass
        _CLIENT = None
