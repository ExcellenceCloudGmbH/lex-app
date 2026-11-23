"""The lex.ui decorators: ``load``, ``action``, ``job`` and ``custom``."""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import inspect
import logging
import os
import threading
import time
from typing import Any, AsyncIterator, Callable

import reflex as rx

from lex.lex_app.reflex.ui import context, guard, pools, results, runs, workcopy

logger = logging.getLogger(__name__)

#: An action running longer than this logs a warning in development (Reflex's own lock-warning threshold).
SLOW_ACTION_SECONDS = 1.0

#: Tests set this to a list: every failure outcome is then recorded.
OUTCOMES: list | None = None

_FALSY = {"0", "false", "no", "n", "off"}


@dataclasses.dataclass(frozen=True)
class Outcome:
    """How a handler ended, besides success: ``kind`` is refused, denied or error."""

    kind: str
    title: str
    message: str


class _NotPermitted(Exception):
    """``requires=`` was not met."""


class _Step:
    """One ``yield`` of a body, handed from its thread to the wrapper."""

    def __init__(self, value: Any):
        self.value = value
        self.stop = False
        self.done = threading.Event()


@dataclasses.dataclass
class _Return:
    value: Any


_END = object()
_STOPPED = object()


def _check_plain(fn: Callable[..., Any], decorator: str) -> None:
    if inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn):
        raise TypeError(
            f"@ui.{decorator} takes a plain function; {fn.__qualname__} is async. "
            "Write it without async/await, or mark a raw Reflex handler with @ui.custom."
        )


def _is_development() -> bool:
    try:
        from reflex.utils.exec import is_prod_mode

        return not is_prod_mode()
    except Exception:  # noqa: BLE001
        return False


def _log_text() -> bool:
    return (os.getenv("LEX_UI_LOG_ERROR_TEXT") or "true").strip().lower() not in _FALSY


def _detail_text(detail: Any) -> str:
    """lex-app's API error detail as one line."""
    if isinstance(detail, dict):
        parts = []
        for name, value in detail.items():
            text = ", ".join(str(v) for v in value) if isinstance(value, (list, tuple)) else str(value)
            parts.append(text if name in ("detail", "non_field_errors") else f"{name}: {text}")
        return " ".join(parts)
    if isinstance(detail, (list, tuple)):
        return " ".join(str(item) for item in detail)
    return str(detail or "")


def _classify(key: str, exc: BaseException) -> Outcome:
    """What to tell the user about ``exc``, and what to log."""
    from lex.lex_app.reflex.data import LexApiError

    denied = results.message("lex.ui.denied", "You are not permitted to do this.")
    if isinstance(exc, results.Refuse):
        outcome = Outcome("refused", "", exc.message)
    elif isinstance(exc, _NotPermitted) or (isinstance(exc, LexApiError) and exc.denied):
        logger.warning("lex.ui handler %s was not permitted", key)
        outcome = Outcome("denied", "", denied)
    elif isinstance(exc, LexApiError) and 400 <= exc.status < 500:
        outcome = Outcome("refused", "", _detail_text(exc.detail) or denied)
    else:
        try:
            from rfds.platform.errors import new_incident_id

            reference = new_incident_id()
        except Exception:  # noqa: BLE001
            import uuid

            reference = uuid.uuid4().hex[:8].upper()
        if _log_text():
            logger.error("lex.ui handler %s failed [%s]", key, reference, exc_info=exc)
        else:
            logger.error("lex.ui handler %s failed [%s] type=%s", key, reference, type(exc).__name__)
        outcome = Outcome(
            "error",
            results.message("lex.ui.error.title", "Something went wrong"),
            results.message("lex.ui.error.reference", "Something went wrong. Reference: {incident}", incident=reference),
        )
    if OUTCOMES is not None:
        OUTCOMES.append(outcome)
    return outcome


def _toast(outcome: Outcome) -> results.Toast:
    return results.Toast(outcome.message, "warning" if outcome.kind == "refused" else "error")


async def _denied(session: context.Session, requirements: tuple | None) -> bool:
    """Whether ``requires=`` is declared and not met."""
    if not requirements:
        return False
    if all(isinstance(item, type(guard.signed_in)) for item in requirements):
        return False
    return not guard.granted(requirements, await session.permissions())


async def _drive(inv: context.Invocation, pool: str, fn: Callable[..., Any], target: Any,
                 args: tuple, kwargs: dict) -> AsyncIterator[Any]:
    """Run the body on ONE pool thread; yield a ``_Step`` for each of its yields, then a ``_Return``.

    The body's thread waits at each yield until the consumer has handled the step (and may have
    set ``step.stop``, which closes the generator so its ``finally`` blocks run).
    """
    loop = inv.loop
    queue: asyncio.Queue = asyncio.Queue()

    def emit(value: Any) -> bool:
        step = _Step(value)
        loop.call_soon_threadsafe(queue.put_nowait, step)
        step.done.wait()
        return step.stop

    def body() -> Any:
        token = context.enter(inv)
        try:
            access = guard.acting_user(inv.session.user) if inv.orm else guard.block_queries(inv.handler)
            with access:
                out = fn(target, *args, **kwargs)
                if not inspect.isgenerator(out):
                    return out
                try:
                    while True:
                        if emit(next(out)):
                            out.close()
                            return _STOPPED
                except StopIteration as done:
                    return done.value
        finally:
            context.leave(token)

    task = asyncio.ensure_future(pools.run(pool, body))
    task.add_done_callback(lambda _: queue.put_nowait(_END))
    pending: _Step | None = None
    try:
        while True:
            item = await queue.get()
            if item is _END:
                break
            pending = item
            yield item
            pending = None
            item.done.set()
    finally:
        if pending is not None:          # the consumer stopped early: let the body's thread go
            pending.stop = True
            pending.done.set()
    value = await task
    yield _Return(None if value is _STOPPED else value)


def _mark(wrapper: Callable[..., Any], fn: Callable[..., Any], kind: str, **options: Any) -> None:
    wrapper.__ui_kind__ = kind
    wrapper.__ui_key__ = fn.__qualname__
    wrapper.__ui_fn__ = fn
    wrapper.__ui_options__ = options


def action(fn: Callable[..., Any] | None = None, *, requires: Any = None) -> Any:
    """A short handler (under about a second), written as a plain function.

    Its body runs off the event loop on the live state while Reflex holds the user's lock, so
    the same user's other clicks wait. Changes reach the browser at each ``yield`` and at the
    end. On failure, what the browser was not yet shown is discarded and the user is told.
    """

    def decorate(fn: Callable[..., Any]) -> Any:
        _check_plain(fn, "action")
        key = fn.__qualname__
        requirements = guard.normalise(requires)

        @functools.wraps(fn)
        async def wrapper(self, *args: Any, **kwargs: Any):
            arg = runs.arg_key(args, kwargs)
            session = await context.resolve(self)
            if await _denied(session, requirements):
                for event in results.to_events([_toast(_classify(key, _NotPermitted()))]):
                    yield event
                return
            inv = context.Invocation(handler=key, kind="action", session=session,
                                     loop=asyncio.get_running_loop(), live=self, orm=requirements is not None)
            failure: BaseException | None = None
            try:
                inv.saved[id(self)] = (self, workcopy.snapshot(self))
            except Exception as exc:  # noqa: BLE001 - a field that cannot be copied is reported below
                failure = exc
            if failure is None:
                runs.mark_started(self, key, arg)
                yield                                # the browser sees "running" before the body starts
                started = time.monotonic()
                try:
                    async for step in _drive(inv, "interactive", fn, self, args, kwargs):
                        found, report = results.split(step.value, handler=key)
                        if report is not None:
                            runs.mark_progress(self, key, report)
                        for event in results.to_events(found):
                            yield event
                        if isinstance(step, _Step):
                            inv.saved = {ident: (state, workcopy.snapshot(state)) for ident, (state, _) in inv.saved.items()}
                            yield                    # flush what the body changed so far
                except Exception as exc:  # noqa: BLE001 - every failure is reported to the user below
                    failure = exc
                    for state, snap in inv.saved.values():
                        workcopy.restore(state, snap)
                elapsed = time.monotonic() - started
                if elapsed > SLOW_ACTION_SECONDS and _is_development():
                    logger.warning("%s took %.1f s as @ui.action; the user's other clicks waited. "
                                   "Make it a @ui.job.", key, elapsed)
            message = ""
            if failure is not None:
                outcome = _classify(key, failure)
                message = outcome.message
                for event in results.to_events([_toast(outcome)]):
                    yield event
            runs.mark_finished(self, key, arg, error=message)

        _mark(wrapper, fn, "action", requires=requirements)
        return rx.event(wrapper)

    return decorate(fn) if fn is not None else decorate
