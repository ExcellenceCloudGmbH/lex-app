"""The lex.ui decorators: ``load``, ``action``, ``job`` and ``custom``."""

from __future__ import annotations

import asyncio
import contextlib
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
    set ``step.stop``, which closes the generator so its ``finally`` blocks run). When the
    consumer stops early (closes this generator, fails, or is cancelled), the body is told to
    stop at its yield, and this generator returns only once the body's thread has finished, so
    the body's own cleanup never runs after the consumer has moved on.
    """
    loop = inv.loop
    queue: asyncio.Queue = asyncio.Queue()
    lock = threading.Lock()
    closed = False
    outstanding: _Step | None = None     # the step the body's thread is waiting on, if any

    def emit(value: Any) -> bool:
        nonlocal outstanding
        step = _Step(value)
        with lock:
            if closed:
                return True
            outstanding = step
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
    try:
        while True:
            item = await queue.get()
            if item is _END:
                break
            yield item
            item.done.set()
    finally:
        with lock:
            closed = True
            waiting = outstanding
        if waiting is not None and not waiting.done.is_set():   # stopped early: let the body's thread go
            waiting.stop = True
            waiting.done.set()
        if not task.done():
            try:
                await asyncio.shield(task)                      # the body's cleanup completes first
            except Exception:  # noqa: BLE001 - surfaced below on the normal path; the consumer is leaving otherwise
                pass
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
            failure: BaseException | None = None
            try:                                     # who is acting, what they may do, the state to return to
                session = await context.resolve(self)
                if await _denied(session, requirements):
                    raise _NotPermitted()
                inv = context.Invocation(handler=key, kind="action", session=session,
                                         loop=asyncio.get_running_loop(), live=self, orm=requirements is not None)
                inv.saved[id(self)] = (self, workcopy.snapshot(self))
            except Exception as exc:  # noqa: BLE001 - reported below, like a failure of the body
                failure = exc
            message = ""
            try:
                if failure is None:
                    runs.mark_started(self, key, arg)
                    yield                            # the browser sees "running" before the body starts
                    started = time.monotonic()
                    try:
                        async with contextlib.aclosing(_drive(inv, "interactive", fn, self, args, kwargs)) as steps:
                            async for step in steps:
                                found, report = results.split(step.value, handler=key)
                                if report is not None:
                                    runs.mark_progress(self, key, report)
                                for event in results.to_events(found):
                                    yield event
                                if isinstance(step, _Step):
                                    inv.saved = {ident: (state, workcopy.snapshot(state))
                                                 for ident, (state, _) in inv.saved.items()}
                                    yield            # flush what the body changed so far
                    except BaseException as exc:     # the body has finished here: aclosing closed the driver
                        for state, snap in inv.saved.values():
                            workcopy.restore(state, snap)
                        if not isinstance(exc, Exception):
                            raise                    # cancelled or closed: no toast
                        failure = exc
                    elapsed = time.monotonic() - started
                    if elapsed > SLOW_ACTION_SECONDS and _is_development():
                        logger.warning("%s took %.1f s as @ui.action; the user's other clicks waited. "
                                       "Make it a @ui.job.", key, elapsed)
                if failure is not None:
                    outcome = _classify(key, failure)
                    message = outcome.message
                    for event in results.to_events([_toast(outcome)]):
                        yield event
            finally:
                runs.mark_finished(self, key, arg, error=message)

        _mark(wrapper, fn, "action", requires=requirements)
        return rx.event(wrapper)

    return decorate(fn) if fn is not None else decorate


async def _merge(inv: context.Invocation, live: Any) -> None:
    """Write every working copy's changes to its state. Call inside ``async with live``."""
    own = type(workcopy.raw(live))
    for state_cls, draft in list(inv.drafts.items()):
        target = live if state_cls is own else await live.get_state(state_cls)
        workcopy.merge(target, draft)


def job(fn: Callable[..., Any] | None = None, *, requires: Any = None, stop_on_leave: bool = False) -> Any:
    """Long work, written as a plain function; the user keeps working while it runs.

    The body works on a copy of the state. Only what it changed is written back, at each
    ``yield`` and at the end; after a ``yield`` it sees what the user changed meanwhile. One run
    per user and argument at a time; a repeat is ignored. ``yield ui.progress(...)`` reports
    progress; a cancel, or leaving the page with ``stop_on_leave=True``, takes effect at a yield.
    """

    def decorate(fn: Callable[..., Any]) -> Any:
        _check_plain(fn, "job")
        key = fn.__qualname__
        requirements = guard.normalise(requires)

        @functools.wraps(fn)
        async def wrapper(self, *args: Any, **kwargs: Any):
            from reflex_base.event.context import EventContext

            arg = runs.arg_key(args, kwargs)
            token = EventContext.get().token
            run = runs.claim(token, key, arg)
            if run is None:
                return                                    # already running: a repeat is ignored
            started = False
            stopped = False
            message = ""
            try:
                try:
                    async with self:                      # who is acting, what they may do, the first copy
                        session = await context.resolve(self)
                        if await _denied(session, requirements):
                            raise _NotPermitted()
                        own = type(workcopy.raw(self))
                        visit = self.ui_visit
                        inv = context.Invocation(handler=key, kind="job", session=session,
                                                 loop=asyncio.get_running_loop(), live=self,
                                                 orm=requirements is not None)
                        inv.drafts[own] = workcopy.Draft(own, workcopy.snapshot(self))
                        runs.mark_started(self, key, arg)
                        started = True
                    async with contextlib.aclosing(_drive(inv, "jobs", fn, inv.drafts[own], args, kwargs)) as steps:
                        async for step in steps:
                            final = isinstance(step, _Return)
                            found, report = results.split(step.value, handler=key)
                            async with self:
                                left = self.ui_visit != visit
                                # Once stopped, what the body changed since its last merge is discarded for good.
                                discard = stopped or (run.cancel and not final) or (stop_on_leave and left)
                                if discard:
                                    if not final:
                                        step.stop = True
                                        stopped = True
                                else:
                                    await _merge(inv, self)
                                    if report is not None:
                                        runs.mark_progress(self, key, report)
                            if not discard:
                                for event in results.to_events(found):
                                    yield event
                except Exception as exc:  # noqa: BLE001 - the body has finished here; reported to the user
                    outcome = _classify(key, exc)
                    message = outcome.message
                    for event in results.to_events([_toast(outcome)]):
                        yield event
            finally:                                      # also on cancellation: nothing is left running
                try:
                    async with self:
                        if started:
                            runs.mark_finished(self, key, arg, error=message)
                        elif message:
                            self.ui_failed = {**dict(self.ui_failed), key: message}
                finally:
                    runs.release(token, key, arg)

        _mark(wrapper, fn, "job", requires=requirements, stop_on_leave=stop_on_leave)
        return rx.event(wrapper, background=True)

    return decorate(fn) if fn is not None else decorate


#: Load handlers by key, filled by :func:`page_loads`; the opener starts them by name.
_LOADS: dict[str, Any] = {}


def page_loads(on_load: Any) -> tuple[list[str], list[str], list]:
    """Split a page's ``on_load`` into (required load names, optional load names, other handlers)."""
    items = [] if on_load is None else list(on_load) if isinstance(on_load, (list, tuple)) else [on_load]
    required: list[str] = []
    optional: list[str] = []
    others: list = []
    for item in items:
        fn = getattr(item, "fn", None)
        if getattr(fn, "__ui_kind__", "") != "load":
            others.append(item)
            continue
        key = fn.__ui_key__
        _LOADS[key] = item
        (optional if fn.__ui_options__.get("optional") else required).append(key)
    return required, optional, others


def registered(name: str) -> Any:
    """The load handler registered under ``name``."""
    return _LOADS[name]


def load(fn: Callable[..., Any] | None = None, *, requires: Any = None, optional: bool = False,
         keep: tuple[str, ...] = ()) -> Any:
    """Fill the page when it opens, written as a plain function.

    Pass it to ``lex_page(on_load=...)``; lex-app starts it after sign-in and the page's
    permission check. It first resets the state's own fields (except ``keep``). A required
    load that fails shows the error page; one with ``optional=True`` fails only its own region
    (read ``ui.failed(State.handler)``). A result that arrives after the user left is discarded.
    """

    def decorate(fn: Callable[..., Any]) -> Any:
        _check_plain(fn, "load")
        key = fn.__qualname__
        requirements = guard.normalise(requires)
        kept = tuple(keep)

        @functools.wraps(fn)
        async def wrapper(self, *args: Any, **kwargs: Any):
            from lex.lex_app.reflex.ui.state import ERROR_ROUTE

            arg = runs.arg_key(args, kwargs)
            visit: int | None = None
            started = False
            outcome: Outcome | None = None
            follow: list = []                             # what to send once the run is wound up
            try:
                try:
                    async with self:                      # who is acting, a clean state, the first copy
                        visit = self.ui_visit
                        session = await context.resolve(self)
                        own = type(workcopy.raw(self))
                        workcopy.reset(self, keep=kept)
                        if await _denied(session, requirements):
                            raise _NotPermitted()
                        inv = context.Invocation(handler=key, kind="load", session=session,
                                                 loop=asyncio.get_running_loop(), live=self,
                                                 orm=requirements is not None)
                        inv.drafts[own] = workcopy.Draft(own, workcopy.snapshot(self))
                        runs.mark_started(self, key, arg)
                        started = True
                    async with contextlib.aclosing(_drive(inv, "interactive", fn, inv.drafts[own], args, kwargs)) as steps:
                        async for step in steps:
                            found, report = results.split(step.value, handler=key)
                            async with self:
                                current = self.ui_visit == visit
                                if current:
                                    await _merge(inv, self)
                                    if report is not None:
                                        runs.mark_progress(self, key, report)
                                elif isinstance(step, _Step):
                                    step.stop = True      # the user left: the rest is discarded
                            if current:
                                for event in results.to_events(found):
                                    yield event
                except Exception as exc:  # noqa: BLE001 - the body has finished here; reported below
                    outcome = _classify(key, exc)
            finally:                                      # the one place a run is wound up; also on cancellation
                async with self:
                    message = outcome.message if outcome is not None else ""
                    if started:
                        runs.mark_finished(self, key, arg, error=message)
                    elif message:
                        self.ui_failed = {**dict(self.ui_failed), key: message}
                    current = visit is not None and self.ui_visit == visit
                    if current and outcome is not None and not optional:
                        self.ui_error = {"kind": "denied" if outcome.kind == "denied" else "error",
                                         "title": outcome.title, "message": outcome.message,
                                         "path": str(getattr(workcopy.raw(self).router.url, "path", "") or "/")}
                        follow = [results.Goto(ERROR_ROUTE, True)]
                    elif current and outcome is None and started and not optional:
                        pending = [name for name in self._ui_pending_loads if name != key]
                        self._ui_pending_loads = pending
                        if not pending and self._ui_optional_loads:
                            follow = [registered(name) for name in self._ui_optional_loads]
                            self._ui_optional_loads = []
            for item in follow:                           # not reached when cancelled: nothing is shown
                if isinstance(item, results.Goto):
                    for event in results.to_events([item]):
                        yield event
                else:
                    yield item

        _mark(wrapper, fn, "load", requires=requirements, optional=optional, keep=kept)
        return rx.event(wrapper, background=True)

    return decorate(fn) if fn is not None else decorate
