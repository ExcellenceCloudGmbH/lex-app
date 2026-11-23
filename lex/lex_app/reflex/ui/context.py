"""One handler invocation: who is acting, the page's parameters, and lex-app's API as the user."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import dataclasses
from typing import Any, Awaitable, Callable, Mapping, Sequence
from urllib.parse import parse_qs, urlsplit

from lex.lex_app.reflex.ui import guard, workcopy

#: How long a body waits for the event loop to answer (an API call, a grant lookup).
LOOP_TIMEOUT_SECONDS = 60.0


@dataclasses.dataclass(frozen=True)
class User:
    """The signed-in user."""

    id: str = ""
    name: str = ""
    username: str = ""
    email: str = ""

    @classmethod
    def from_profile(cls, profile: Mapping[str, Any]) -> "User":
        return cls(id=str(profile.get("sub") or ""), name=str(profile.get("name") or ""),
                   username=str(profile.get("preferred_username") or ""), email=str(profile.get("email") or ""))


class Params:
    """The page's route and query values: ``params.section`` or ``params.get("w")``."""

    def __init__(self, values: Mapping[str, Any]):
        self._values = dict(values)

    def get(self, name: str, default: Any = None) -> Any:
        return self._values.get(name, default)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._values[name]
        except KeyError:
            raise AttributeError(f"the page has no parameter {name!r}; use params.get({name!r}) for an optional one") from None

    def __contains__(self, name: str) -> bool:
        return name in self._values


@dataclasses.dataclass
class Session:
    """Who is acting, resolved on the event loop before a handler body runs."""

    user: User
    params: Params
    token: Callable[[], Awaitable[str]]
    permissions: Callable[[], Awaitable[list]]
    #: A stand-in for ``self.api`` (tests); ``None`` = lex-app's API with the user's token.
    api: Any = None
    #: An httpx-style async client for ``LexApi`` (tests).
    api_client: Any = None


def _params_of(state: Any) -> Params:
    values: dict[str, Any] = {}
    try:
        for name, found in parse_qs(urlsplit(str(state.router.url)).query).items():
            values[name] = found[0] if len(found) == 1 else found
    except Exception:  # noqa: BLE001 - a state without router data has no parameters
        pass
    try:
        values.update(dict(state.router.page.params))
    except Exception:  # noqa: BLE001
        pass
    return Params(values)


async def _default_resolve(state: Any) -> Session:
    from lex.lex_app.reflex import auth, session

    target = workcopy.raw(state)
    digest = session.binding_digest(target)

    async def token() -> str:
        return await session.access_token_of(digest)

    async def permissions() -> list:
        return await auth.permissions_of(await token())

    return Session(user=User.from_profile(session.profile(target)), params=_params_of(target),
                   token=token, permissions=permissions)


_resolver: Callable[[Any], Awaitable[Session]] = _default_resolve


def set_resolver(fn: Callable[[Any], Awaitable[Session]] | None) -> Callable[[Any], Awaitable[Session]]:
    """Replace how a session is resolved (tests); ``None`` restores lex-app's own. Returns the previous."""
    global _resolver
    previous = _resolver
    _resolver = fn or _default_resolve
    return previous


async def resolve(state: Any) -> Session:
    return await _resolver(state)


@dataclasses.dataclass
class Invocation:
    """One run of one handler."""

    handler: str
    kind: str                       # "action", "job" or "load"
    session: Session
    loop: asyncio.AbstractEventLoop
    live: Any                       # the live state (action) or the background proxy (load, job)
    orm: bool = False
    drafts: dict = dataclasses.field(default_factory=dict)   # state class -> Draft (load, job)
    saved: dict = dataclasses.field(default_factory=dict)    # live state object id -> (state, snapshot) (action)

    def on_loop(self, coro: Awaitable[Any]) -> Any:
        """Run ``coro`` on the event loop from the body's thread and wait for it."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            if hasattr(coro, "close"):
                coro.close()
            raise RuntimeError("on_loop is for a handler body's worker thread; code already on the "
                               "event loop should await the coroutine instead")
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(LOOP_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise

    def can(self, target: Any, scope: str = "read") -> bool:
        """Whether Keycloak grants the user ``scope`` on ``target`` (model-wide)."""
        return guard.granted((guard.Requirement(target, scope),), self.on_loop(self.session.permissions()))

    def state_of(self, state_cls: Any) -> Any:
        """Another state of the same user: live in an action, a working copy in a load or job."""
        if self.kind == "action":
            other = self.on_loop(self.live.get_state(state_cls))
            self.saved.setdefault(id(other), (other, workcopy.snapshot(other)))
            return other
        if state_cls not in self.drafts:
            self.drafts[state_cls] = self.on_loop(self._draft_of(state_cls))
        return self.drafts[state_cls]

    async def _draft_of(self, state_cls: Any) -> workcopy.Draft:
        async with self.live:
            other = await self.live.get_state(state_cls)
            return workcopy.Draft(state_cls, workcopy.snapshot(other))


_CURRENT: contextvars.ContextVar[Invocation] = contextvars.ContextVar("lex_ui_invocation")


def enter(inv: Invocation) -> contextvars.Token:
    return _CURRENT.set(inv)


def leave(token: contextvars.Token) -> None:
    _CURRENT.reset(token)


def current() -> Invocation:
    try:
        return _CURRENT.get()
    except LookupError:
        raise RuntimeError("self.user, self.api, self.params, self.can and self.state only work "
                           "inside a lex.ui handler (@ui.load, @ui.action, @ui.job)") from None


class Rows(list):
    """Rows of a model, with the total ``count`` across all pages."""

    count: int = 0


class Api:
    """lex-app's API as the signed-in user, for a handler body (synchronous)."""

    def __init__(self, inv: Invocation):
        self._inv = inv

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        async def call() -> Any:
            from lex.lex_app.reflex.data import LexApi

            api = LexApi(await self._inv.session.token(), client=self._inv.session.api_client)
            return await getattr(api, method)(*args, **kwargs)

        return self._inv.on_loop(call())

    def list(self, model: Any, *, filters: Mapping[str, Any] | None = None, search: str | None = None,
             ordering: str | None = None, page: int = 1, per_page: int = 25,
             fields: Sequence[str] | None = None) -> Rows:
        """One page of a model's records, as plain rows with ``.count``."""
        from lex.lex_app.reflex.data import _default_search_field, scope_params, wire_value

        params = scope_params(filters or {})
        if search:
            field = _default_search_field(model) if not isinstance(model, str) else ""
            if not field:
                raise ValueError(f"{model!r} has no text field to search; pass filters={{'<field>__icontains': ...}}")
            params.append((f"{field}__icontains", str(search)))
        data = self._call("list", model, page=page, per_page=per_page, ordering=ordering, params=params)
        rows = Rows({name: wire_value(value) for name, value in row.items() if fields is None or name in fields}
                    for row in data["results"])
        rows.count = int(data["count"])
        return rows

    def get(self, model: Any, pk: Any) -> dict:
        return self._call("get", model, pk)

    def create(self, model: Any, data: Mapping[str, Any]) -> dict:
        return self._call("create", model, data)

    def update(self, model: Any, pk: Any, data: Mapping[str, Any]) -> dict:
        return self._call("update", model, pk, data)

    def delete(self, model: Any, pk: Any) -> None:
        return self._call("delete", model, pk)


def api_of(inv: Invocation) -> Any:
    """``self.api``: the session's stand-in when it has one, else lex-app's API."""
    return inv.session.api if inv.session.api is not None else Api(inv)
