"""``ui.testing``: run a lex.ui handler in a unit test, without a browser."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Any

from lex.lex_app.reflex.ui import context, handlers, pools, results


class _NoApi:
    """What ``self.api`` is in a test that passed no ``api=``."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(
            f"the handler used self.api.{name}(), and ui.testing.run() was given no api=. "
            "Pass a stand-in object with the methods the handler calls."
        )


def _grants(grants: Any) -> list[dict]:
    """``[(Model, "read"), "app.Model:scope"]`` as Keycloak's permission list."""
    out = []
    for grant in grants or ():
        if isinstance(grant, str):
            resource, _, scope = grant.rpartition(":")
        else:
            target, scope = grant
            from lex.lex_app.reflex.auth import _resource_name

            resource = _resource_name(target)
        out.append({"rsname": resource, "scopes": [scope]})
    return out


def _router_data(url: str) -> dict:
    """What a browser on ``url`` sends with its events: the path, the full path, the query."""
    from urllib.parse import parse_qsl, urlsplit

    parts = urlsplit(url)
    return {"pathname": parts.path or "/", "asPath": url, "query": dict(parse_qsl(parts.query))}


class _Sink:
    """Stands in for the browser socket: records every update with its time."""

    def __init__(self) -> None:
        self.updates: list[dict] = []
        self.times: list[float] = []
        self._start = time.perf_counter()

    async def emit_update(self, update: Any, token: str, **_: Any) -> None:
        delta = getattr(update, "delta", None)
        if not delta:
            return
        merged: dict[str, Any] = {}
        for state_values in delta.values():
            for name, value in state_values.items():
                merged[name.removesuffix("_rx_state_")] = value
        self.updates.append(merged)
        self.times.append(time.perf_counter() - self._start)


class _NoBrowserApp:
    """The App Reflex looks up after each event to update other tabs of a shared state; there are none."""

    event_namespace = None


def _load_app_states() -> None:
    """Import the states an app has defined before its first event.

    ``rfds.toast`` is loaded lazily and brings in RFDS's shared feed, which defines Reflex's
    shared states. Loaded halfway through a test, they would register in that harness's fork
    only, after the user's state tree was built without them, and every later event would fail.
    ``lex.lex_app.reflex.data`` (a failure's classification imports it) defines a state whose
    computed vars follow the router, so it must exist before a test moves the browser.
    """
    import rfds

    import lex.lex_app.reflex.data  # noqa: F401 - defines LexRecordState

    rfds.toast  # noqa: B018 - the lazy import is the point


def _registry_with_app() -> Any:
    """Reflex's registry of states and handlers, with a stand-in App (a test has no real one)."""
    from reflex_base.registry import RegistrationContext

    _load_app_states()
    current = RegistrationContext.ensure_context()
    if current._app is not None:
        return current
    fork = current.fork()
    if current._config is not None:
        fork._set_config(current._config)
    fork._set_app(_NoBrowserApp())
    return fork


class Harness:
    """Reflex's own event processor, with a fake browser and a fake signed-in user.

    ``async with Harness(user="anna", grants=[(Ticket, "read")]) as h:`` then ``await (await
    h.send(State, "handler", code="X"))``.
    """

    def __init__(self, *, user: str = "tester", grants: Any = (), params: dict | None = None,
                 api: Any = None, token: str = "tab1"):
        self.token = token
        self._session_args = (user, _grants(grants), dict(params or {}), api)
        self._sink = _Sink()
        self._opened = False
        self.results: list = []
        self.outcomes: list = []

    @property
    def updates(self) -> list[dict]:
        return self._sink.updates

    @property
    def update_times(self) -> list[float]:
        return self._sink.times

    async def __aenter__(self) -> "Harness":
        from reflex_base.event.processor import BaseStateEventProcessor
        from reflex_base.registry import RegistrationContext

        user, grants, params, api = self._session_args

        async def resolve(state: Any) -> context.Session:
            async def token() -> str:
                return "test-token"

            async def permissions() -> list:
                return list(grants)

            return context.Session(user=context.User(id=user, name=user, username=user, email=f"{user}@example.test"),
                                   params=context.Params(params), token=token, permissions=permissions,
                                   api=api if api is not None else _NoApi())

        self._previous = (context.set_resolver(resolve), results.RECORDER, handlers.OUTCOMES)
        results.RECORDER = self.results
        handlers.OUTCOMES = self.outcomes
        self._registry = RegistrationContext.set(_registry_with_app())
        self._processor = BaseStateEventProcessor()
        self._running = self._processor.configure(event_namespace=self._sink)
        await self._running.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        from reflex_base.registry import RegistrationContext

        try:
            await self._running.__aexit__(*exc)
        finally:
            RegistrationContext.reset(self._registry)
            resolver, recorder, outcomes = self._previous
            context.set_resolver(resolver)
            results.RECORDER = recorder
            handlers.OUTCOMES = outcomes
            pools.shutdown()

    async def _state(self, state_cls: Any) -> Any:
        from reflex.istate.manager.token import BaseStateToken

        manager = self._processor._root_context.state_manager
        root = (await manager.get_state(BaseStateToken(ident=self.token, cls=state_cls)))._get_root_state()
        return await root.get_state(state_cls)

    async def send(self, state_cls: Any, handler: str, *, _router_data: dict | None = None,
                   **arguments: Any) -> Any:
        """Send one event, as the browser would; returns a future that resolves when it has run."""
        from reflex_base.event import Event

        return await self._processor.enqueue(self.token, Event(name=f"{state_cls.get_full_name()}.{handler}",
                                                               router_data=_router_data or {},
                                                               payload=arguments))

    async def fields(self, state_cls: Any) -> dict:
        """The state's own fields and the status fields, as they stand."""
        from lex.lex_app.reflex.ui import workcopy

        state = await self._state(state_cls)
        values = workcopy.snapshot(state)
        for name in ("ui_running", "ui_failed", "ui_progress", "ui_progress_message", "ui_visit", "ui_error"):
            values[name] = workcopy._plain(getattr(state, name))
        return values

    async def open_tab(self, url: str = "/") -> None:
        """Connect the tab on ``url`` as a browser would, and forget the whole-state sync of a new tab.

        ``run`` calls it before seeding ``fields=``: the seed is what the browser already shows,
        so the updates recorded afterwards are only what the handler sent.
        """
        import reflex as rx

        await (await self.send(rx.State, "hydrate", _router_data=_router_data(url)))
        await (await self.send(rx.State, "set_is_hydrated", _router_data=_router_data(url), value=True))
        self._opened = True
        self._sink.updates.clear()
        self._sink.times.clear()

    async def navigate(self, url: str) -> None:
        """The browser is now on ``url`` (a path, with a query string or not); no page load runs."""
        import reflex as rx

        if not self._opened:
            await self.open_tab(url)
            return
        await (await self.send(rx.State, "set_is_hydrated", _router_data=_router_data(url), value=True))

    async def mount(self, state_cls: Any, handler: str, *, url: str = "/", route: str | None = None,
                    **arguments: Any) -> Any:
        """The browser opens ``url``, where a component mounts with ``on_mount=state_cls.handler``.

        Reflex sends a component's mount events before the page's ``on_load`` events, so the
        handler's event goes first and the page's opener (``ui_open`` with ``route``, by default
        the path of ``url``) after it, once the handler has started. Returns the handler's
        future; the opener has run when this returns.
        """
        from urllib.parse import urlsplit

        from lex.lex_app.reflex.ui.state import State

        await self.navigate(url)
        future = await self.send(state_cls, handler, **arguments)
        await asyncio.sleep(0.05)                 # the mount event is handled before the opener arrives
        page = route if route is not None else (urlsplit(url).path or "/")
        await (await self.send(State, "ui_open", page=page))
        return future

    async def set_fields(self, state_cls: Any, **values: Any) -> None:
        state = await self._state(state_cls)
        for name, value in values.items():
            setattr(state, name, value)


@dataclasses.dataclass
class Result:
    """What one handler run did."""

    fields: dict
    updates: list
    update_times: list
    results: list
    outcomes: list

    @property
    def goto(self) -> str | None:
        paths = [r.path for r in self.results if isinstance(r, results.Goto)]
        return paths[-1] if paths else None

    @property
    def toasts(self) -> list:
        return [r for r in self.results if isinstance(r, results.Toast)]

    @property
    def downloads(self) -> list:
        return [r for r in self.results if isinstance(r, results.Download)]

    @property
    def refused(self) -> str:
        return next((o.message for o in self.outcomes if o.kind == "refused"), "")

    @property
    def denied(self) -> bool:
        return any(o.kind == "denied" for o in self.outcomes)

    @property
    def error(self) -> str:
        return next((o.message for o in self.outcomes if o.kind == "error"), "")

    @property
    def running(self) -> dict:
        return self.fields["ui_running"]

    @property
    def failed(self) -> dict:
        return self.fields["ui_failed"]


def run(state_cls: Any, handler: str, *, user: str = "tester", grants: Any = (), params: dict | None = None,
        fields: dict | None = None, api: Any = None, **arguments: Any) -> Result:
    """Run one handler to completion and report what it did.

    ``grants`` are ``(Model, "read")`` pairs or ``"app_label.Model:scope"`` strings. ``fields``
    seeds the state. ``api`` is the stand-in ``self.api`` returns; a handler that uses
    ``self.api`` needs one.
    """

    async def scenario() -> Result:
        async with Harness(user=user, grants=grants, params=params, api=api) as harness:
            await harness.open_tab()
            if fields:
                await harness.set_fields(state_cls, **fields)
            future = await harness.send(state_cls, handler, **arguments)
            await future             # a handler's own failure is in the outcomes; anything else is a bug
            await asyncio.sleep(0.05)
            return Result(fields=await harness.fields(state_cls), updates=list(harness.updates),
                          update_times=list(harness.update_times), results=list(harness.results),
                          outcomes=list(harness.outcomes))

    return asyncio.run(scenario())
