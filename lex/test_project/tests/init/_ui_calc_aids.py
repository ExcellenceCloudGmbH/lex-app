"""Aids for the lex.ui calculation page tests (batches 1ef-1ei).

A calculation page reads and writes through lex-app's API as the signed-in user. These aids run
the page's handlers in ``ui.testing.Harness`` with ``self.api`` being lex.ui's own API client
(``context.Api`` and ``LexApi``) whose HTTP requests go to lex-app's real views through the test's
signed-in ``APIClient``: permissions, validation, audit and the calculation's start are lex-app's,
nothing is stubbed between the page and the views.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from urllib.parse import urlencode, urlsplit

from lex.lex_app.reflex.ui import context, testing, workcopy


class ViewsClient:
    """An httpx-shaped async client for ``LexApi`` that answers from lex-app's views, as ``client`` signed in."""

    def __init__(self, client: Any):
        self.client = client
        self.calls: list[tuple[str, str]] = []

    async def request(self, method: str, url: str, *, params: Any = None, json: Any = None,  # noqa: A002
                      headers: Any = None) -> Any:
        parts = urlsplit(url)
        query = urlencode(list(params or []))
        path = parts.path + (f"?{query}" if query else "")
        self.calls.append((method, parts.path))
        body = "" if json is None else _dumps(json)

        def call() -> Any:
            from django.db import close_old_connections

            try:
                return self.client.generic(method, path, data=body, content_type="application/json")
            finally:
                close_old_connections()

        return await asyncio.to_thread(call)


def _dumps(value: Any) -> str:
    return json.dumps(value)


class Session:
    """A harness whose ``self.api`` is lex.ui's API client over lex-app's views (see the module)."""

    def __init__(self, client: Any, *, user: str = "e2e_user", grants: Any = (), url: str = "/reports/regions"):
        self.views = ViewsClient(client)
        self.harness = testing.Harness(user=user, grants=grants)
        self.url = url
        self._user = user
        self._grants = testing._grants(grants)

    async def __aenter__(self) -> "Session":
        await self.harness.__aenter__()
        views, user, grants = self.views, self._user, self._grants
        harness = self.harness

        async def resolve(state: Any) -> context.Session:
            async def token() -> str:
                return "test-token"

            async def permissions() -> list:
                return list(grants)

            return context.Session(user=context.User(id=user, name=user, username=user),
                                   params=context._params_of(workcopy.raw(state)), token=token, permissions=permissions,
                                   api=None, api_client=views, tab=harness.token)

        self._previous = context.set_resolver(resolve)
        await self.harness.open_tab(self.url)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        context.set_resolver(self._previous)
        await self.harness.__aexit__(*exc)

    async def visit(self, url: str) -> None:
        """The browser opens ``url`` on this page: the opener runs, and with it the page's load."""
        from lex.lex_app.reflex.ui.state import State

        from lex.lex_app.reflex.ui import handlers, runs

        await self.harness.navigate(url)
        await (await self.harness.send(State, "ui_open", page=urlsplit(url).path))
        entry = handlers.page_of(urlsplit(url).path)
        keys = [runs.key_of(load) for load in (entry.required if entry else ())]

        async def loaded() -> bool:
            seen = await self.harness.fields(State)
            return seen["ui_error"] != {} or all(seen["ui_loaded"].get(key) or seen["ui_failed"].get(key)
                                                 for key in keys)

        await self.until(loaded, f"the page's load never finished on {url}")

    async def until(self, condition: Any, failure: str, seconds: float = 10) -> None:
        deadline = time.monotonic() + seconds
        while True:
            found = condition()
            if asyncio.iscoroutine(found):
                found = await found
            if found:
                return
            if time.monotonic() > deadline:
                raise AssertionError(failure)
            await asyncio.sleep(0.02)


def settled(record: Any, seconds: float = 10) -> str:
    """The status ``record`` lands in once its run leaves IN_PROGRESS."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        record.refresh_from_db()
        if record.is_calculated not in ("IN_PROGRESS", "NOT_CALCULATED"):
            return record.is_calculated
        time.sleep(0.02)
    raise AssertionError(f"{record} never settled; is_calculated={record.is_calculated!r}")
