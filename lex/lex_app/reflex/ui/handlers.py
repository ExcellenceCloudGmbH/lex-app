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
import uuid
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


def load_id(handler: Any) -> str:
    """A load's module-qualified id, as the page bookkeeping counts it (two states may share a name)."""
    fn = getattr(handler, "fn", handler)
    body = getattr(fn, "__ui_fn__", fn)
    return f"{body.__module__}.{body.__qualname__}"


@dataclasses.dataclass(frozen=True)
class PageLoads:
    """What the opener starts for one page: its required loads, its optional ones, its permission."""

    required: tuple = ()
    optional: tuple = ()
    requires: str = ""


#: Route -> the page's loads and permission, filled by ``lex_page`` on the server. The opener
#: receives only the route from the browser and looks everything else up here.
_PAGES: dict[str, PageLoads] = {}


def register_page(route: str, required: Any, optional: Any, requires: str) -> None:
    """Record which loads ``route`` starts, and the permission its user must hold for them to start."""
    _PAGES[route] = PageLoads(tuple(required), tuple(optional), requires or "")


def page_of(route: str) -> PageLoads | None:
    """The loads registered for ``route``; ``None`` for an unknown or empty route."""
    return _PAGES.get(route) if route else None


def page_loads(on_load: Any) -> tuple[list, list, list]:
    """Split a page's ``on_load`` into (required loads, optional loads, other handlers)."""
    items = [] if on_load is None else list(on_load) if isinstance(on_load, (list, tuple)) else [on_load]
    required: list = []
    optional: list = []
    others: list = []
    for item in items:
        fn = getattr(item, "fn", None)
        if getattr(fn, "__ui_kind__", "") != "load":
            others.append(item)
            continue
        (optional if fn.__ui_options__.get("optional") else required).append(item)
    return required, optional, others


def _location(state: Any) -> str:
    """The browser's page for ``state``'s tab: the path with its query string."""
    url = workcopy.raw(state).router.url
    path = str(getattr(url, "path", "") or "")
    query = str(getattr(url, "query", "") or "")
    return f"{path}?{query}" if query else path


def _return_path(location: str) -> str:
    """Where "Try again" goes: the page the load started on, never the error page itself."""
    from lex.lex_app.reflex.ui.state import ERROR_ROUTE

    if not location or location.split("?", 1)[0].rstrip("/") == ERROR_ROUTE:
        return "/"
    return location


def load(fn: Callable[..., Any] | None = None, *, requires: Any = None, optional: bool = False,
         keep: tuple[str, ...] = ()) -> Any:
    """Fill the page when it opens, written as a plain function.

    Pass it to ``lex_page(on_load=...)``; lex-app starts it after sign-in, when the user holds
    the page's permission. That permission only decides whether the load is *started*: what the
    load may read is enforced by its own ``requires=`` and by lex-app's API. The first load of a
    state in a visit resets the state's own fields (except that load's ``keep``). A required
    load that fails shows the error page and ends the visit; one with ``optional=True`` fails
    only its own region (read ``ui.failed(State.handler)``). A result or a failure that arrives
    after the user left is discarded.

    A page without ``lex_page`` (a dashboard) starts its load with a component's
    ``on_mount=State.handler`` instead. Reflex sends mount events before the page's ``on_load``
    (and, on a navigation, with the address of the page the browser left), so such a load belongs
    to its component, not to the page view:

    - it is replaced by a newer run of the same load, and by nothing else: the same load mounted
      twice, even with different arguments, keeps only the newer run's data;
    - it resets its state's own fields (except ``keep``) every time it starts, so loads started on
      mount that share one state reset it each time either starts -- a state filled by several
      loads declares them on the page (``lex_page(on_load=[...])``) or uses one load;
    - it reports a failure in place (``ui.failed(State.handler)``), whatever ``optional=`` says:
      the error page is only for loads the page declared;
    - it starts no optional load of the page.
    """

    def decorate(fn: Callable[..., Any]) -> Any:
        _check_plain(fn, "load")
        key = fn.__qualname__
        ident = f"{fn.__module__}.{fn.__qualname__}"
        requirements = guard.normalise(requires)
        kept = tuple(keep)

        @functools.wraps(fn)
        async def wrapper(self, *args: Any, **kwargs: Any):
            from lex.lex_app.reflex.ui.state import ERROR_ROUTE

            arg = runs.arg_key(args, kwargs)
            visit: int | None = None
            where = ""                                    # the page the load started on (opener-started only)
            run_token = uuid.uuid4().hex                  # this run; the latest run of each load is kept
            by_opener = False                             # started by the page's opener, not on mount
            started = False
            succeeded = False                             # the body ran to its end: no error, no cancellation
            outcome: Outcome | None = None
            follow: list = []                             # what to send once the run is wound up

            def is_current() -> bool:
                """Whether this run's results still belong on the page. Call inside ``async with self``."""
                if visit is None:
                    return False
                if by_opener:
                    return self.ui_visit == visit
                # On mount: tied to its component, not to the page view. The visit may change after it
                # started (the opener arrives later), and on a navigation the router it started with is
                # the page the browser left; only a newer run of the same load makes it stale.
                return self._ui_load_runs.get(ident) == run_token

            try:
                try:
                    async with self:                      # who is acting, a clean state, the first copy
                        visit = self.ui_visit
                        where = _location(self)
                        # Opener-started if the opener listed this load, else started on mount. One known
                        # mix-up: a load both declared on the page and mounted in a component can have its
                        # mount run take the opener's listing; the data still lands, at the cost of a
                        # second run.
                        listed = list(self._ui_started)
                        by_opener = ident in listed
                        if by_opener:
                            listed.remove(ident)
                            self._ui_started = listed
                        self._ui_load_runs = {**dict(self._ui_load_runs), ident: run_token}
                        own = type(workcopy.raw(self))
                        name = own.get_full_name()
                        done = list(self._ui_reset)
                        if not by_opener:                 # on mount: every run starts from a clean state
                            workcopy.reset(self, keep=kept)
                        elif name not in done:            # once per visit: a sibling load's data stays
                            workcopy.reset(self, keep=kept)
                            self._ui_reset = [*done, name]
                        session = await context.resolve(self)
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
                                current = is_current()
                                if current:
                                    await _merge(inv, self)
                                    if report is not None:
                                        runs.mark_progress(self, key, report)
                                elif isinstance(step, _Step):
                                    step.stop = True      # stale (the user left, or a newer run): the rest is discarded
                            if current:
                                for event in results.to_events(found):
                                    yield event
                    succeeded = True
                except Exception as exc:  # noqa: BLE001 - the body has finished here; reported below
                    outcome = _classify(key, exc)         # logged even when the user has moved on
            finally:                                      # the one place a run is wound up; also on cancellation
                async with self:
                    current = is_current()
                    message = outcome.message if outcome is not None and current else ""
                    if started:
                        runs.mark_finished(self, key, arg, error=message)
                    elif message:
                        self.ui_failed = {**dict(self.ui_failed), key: message}
                    # Started on mount, or optional: the failure stays in place, in ui_failed.
                    if current and outcome is not None and not optional and by_opener:
                        if _location(self) == where:      # the browser is still on the page it failed for
                            self.ui_error = {"kind": "denied" if outcome.kind == "denied" else "error",
                                             "title": outcome.title, "message": outcome.message,
                                             "path": _return_path(where)}
                            self.ui_visit += 1            # the visit is over: its later outcomes are stale
                            follow = [results.Goto(ERROR_ROUTE, True)]
                    elif current and succeeded and not optional and by_opener:
                        pending = [other for other in self._ui_pending_loads if other != ident]
                        self._ui_pending_loads = pending
                        if not pending and self._ui_optional_loads:
                            entry = page_of(self._ui_page)
                            waiting = set(self._ui_optional_loads)
                            follow = [h for h in (entry.optional if entry else ()) if load_id(h) in waiting]
                            self._ui_optional_loads = []
                            self._ui_started = [*self._ui_started, *(load_id(h) for h in follow)]
            for item in follow:                           # not reached when cancelled: nothing is shown
                if isinstance(item, results.Goto):
                    for event in results.to_events([item]):
                        yield event
                else:
                    yield item

        _mark(wrapper, fn, "load", requires=requirements, optional=optional, keep=kept)
        return rx.event(wrapper, background=True)

    return decorate(fn) if fn is not None else decorate


def custom(fn: Callable[..., Any] | None = None, **event_kwargs: Any) -> Any:
    """A raw Reflex handler on a ``ui.State``: the custom region, marked so reviews can see it.

    It behaves exactly like ``@rx.event`` (``background=True`` and the rest are passed on).
    ``lex reflex check`` lists every one; an unmarked ``@rx.event`` on a ``ui.State`` fails it.
    """

    def decorate(fn: Callable[..., Any]) -> Any:
        _mark(fn, fn, "custom", **event_kwargs)
        return rx.event(fn, **event_kwargs) if event_kwargs else rx.event(fn)

    return decorate(fn) if fn is not None else decorate
