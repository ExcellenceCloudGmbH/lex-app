# lex.ui Action and State Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let consultants write Reflex event handlers as plain synchronous functions on a `ui.State`, under `@ui.load`, `@ui.action` and `@ui.job`, with lex-app supplying the user, the API as that user, permission checks, status, discard-on-failure and failure display.

**Architecture:** A subpackage `lex/lex_app/reflex/ui/` holds small single-purpose modules (results, pools, guard, working copy, context, runs, state, handlers, testing); `lex/ui.py` re-exports the public names. Each decorator wraps the plain function in a Reflex handler: `action` is a foreground handler whose body runs in a worker thread on the live state; `job` and `load` are background handlers whose body runs in a worker thread on a working copy that is merged under the lock at each `yield` and at the end. One thread drives the whole body; a queue hands each `yield` to the wrapper.

**Tech Stack:** Python 3.12, Reflex 0.9.13, RFDS 1.0.0, Django 6 (ORM, `execute_wrapper`), httpx (through the existing `LexApi`), pytest through `lex pytest`.

**Spec:** `docs/superpowers/specs/2026-11-23-reflex-ui-action-state-layer-design.md` — read it first; section numbers below refer to it.

## Global Constraints

- Branch `feat/reflex-ui-actions`, stacked on `feat/reflex-lex-look` (lex-app #908). The PR base is `feat/reflex-lex-look`.
- lex-app blocks force-push. Never amend or rebase pushed commits. Push over SSH: `git push git@github.com:ExcellenceCloudGmbH/lex-app.git feat/reflex-ui-actions`.
- No `reflex-enterprise`, no `rfds.enterprise` import anywhere (test 1.736 enforces it).
- Never name a customer project, its paths, its models or its functions in committed files, commit messages or PR text. Examples use `Ticket`, `Payment`, `Request`.
- Handler bodies under the three decorators are plain functions. An `async def` under them raises `TypeError` at decoration.
- Settings and defaults, verbatim from the spec: `LEX_UI_ACTION_THREADS` = 16, `LEX_UI_JOB_THREADS` = 8, slow-action warning after 1 second in development, `LEX_UI_LOG_ERROR_TEXT=false` logs only the exception type.
- Tests are cluster tests in `lex/test_project/tests/init/`, marker `init`, one batch letter per task, scenarios from 1.747. Before starting, confirm letters `cc`–`cm` and scenarios 1.747–1.785 are still free on every remote branch (see Task 12, step 1); if not, shift them and keep the mapping in this plan consistent.
- Run tests one run at a time (shared test database), from the worktree root:
  `DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd) .venv-oss/bin/python -m lex pytest <file> -v -p no:cacheprovider`
  Below this is written `RUN <file>`. After a run, delete the stray `assets/` folder Reflex leaves at the worktree root (`rm -rf assets`).
- Every test has a docstring with Scenario / Given / When / Then, and every assertion a failure message, as the existing batches do.
- Commit after each task. Commit messages end with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.

## Clarifications found while planning

These refine the spec without changing its decisions. Task 11 writes them into the spec.

1. **Loads are started by an opener.** Reflex starts background handlers immediately, ahead of earlier foreground `on_load` entries, so a `@ui.load` listed in `on_load` would run before sign-in and grants. `lex_page` therefore appends one foreground event, `State.ui_open`, which starts the page's loads after `LexAccessState.load` has run. This settles the spec's risk "a page's `on_load` running background handlers".
2. **Shared handler logic lives in underscore methods.** Reflex turns every public method of a state into an event handler. A handler calls `self._helper()`; a generator helper is used with `yield from self._helper()`.
3. **A job sees its own state's fields.** Fields of a parent or another state are reached with `self.state(Other)`.
4. **`ui.running(handler, arg)` compares the first argument as text.**
5. **`ui.testing.run` needs `api=` when the handler uses `self.api`.** Test tokens are not Keycloak-signed, so the real API cannot answer in a unit test; the helper raises a clear error if `self.api` is used without a stand-in.
6. **A failed required load shows the page `/ui-error`**, with the reference and "Try again".
7. **RFDS's own async hooks stay async.** `ExportMixin._export_rows` in the reference example is RFDS's contract, not a lex.ui handler.

## File Structure

| File | Responsibility |
| --- | --- |
| `lex/lex_app/reflex/ui/__init__.py` | Package docstring only |
| `lex/lex_app/reflex/ui/results.py` | `Refuse`; `goto`/`toast`/`download`/`progress` values; turning them into Reflex events; standard messages |
| `lex/lex_app/reflex/ui/pools.py` | The two thread pools |
| `lex/lex_app/reflex/ui/guard.py` | `requires=` values, the grant check, the query block, the acting user |
| `lex/lex_app/reflex/ui/workcopy.py` | Snapshot, restore, reset, `Draft`, merge |
| `lex/lex_app/reflex/ui/context.py` | `User`, `Params`, `Session`, `Invocation`, the `Api` facade, the resolver seam |
| `lex/lex_app/reflex/ui/runs.py` | Run status on the state, the in-process registry, liveness, `running`/`failed`/`progress_of`/`cancel` |
| `lex/lex_app/reflex/ui/state.py` | `ui.State`, the opener, the cancel event, the error page |
| `lex/lex_app/reflex/ui/handlers.py` | `load`, `action`, `job`, `custom`, the body driver, failure handling |
| `lex/lex_app/reflex/ui/testing.py` | `Harness`, `run`, `Result` |
| `lex/lex_app/reflex/ui/checks.py` | The `lex reflex check` findings |
| `lex/ui.py` | Public re-exports |
| Modify `lex/lex_app/reflex/session.py`, `auth.py` | `access_token_of(digest)`, `permissions_of(token)` |
| Modify `lex/lex_app/reflex/shell.py`, `app.py` | The opener in `lex_page` and on `/`; the error page; the heartbeat |
| Modify `lex/lex_app/reflex/scaffold.py`, `examples/_reflex_structure.py` | Templates and the example on the layer; the check |

---

### Task 1: Results and thread pools

**Files:**
- Create: `lex/lex_app/reflex/ui/__init__.py`, `lex/lex_app/reflex/ui/results.py`, `lex/lex_app/reflex/ui/pools.py`
- Test: `lex/test_project/tests/init/test_1cc_ui_results_and_pools.py`

**Interfaces:**
- Produces: `results.Refuse(message)`, `results.Goto/Toast/Download/Progress` (frozen dataclasses), `results.goto(path, *, replace=False)`, `results.toast(message, *, tone="info", description="")`, `results.download(*, url=None, filename=None, data=None)`, `results.progress(fraction, message="")`, `results.split(value, *, handler) -> (list, Progress | None)`, `results.to_events(results) -> list`, `results.RECORDER` (a `list | None` test hook), `results.message(key, default, **params) -> str`; `pools.run(kind, fn, /, *args)` awaitable with `kind` in `("interactive", "jobs")`, `pools.size(kind) -> int`, `pools.shutdown()`.

- [ ] **Step 1: Write the failing tests**

```python
"""lex.ui: what a handler returns, and the thread pools its body runs on.

Intent: a consultant's handler returns plain values -- go to a page, show a toast, hand over a
file, report progress -- and lex-app turns them into browser events; its body runs on one of two
sized thread pools, never on the event loop, so one slow handler cannot stall every user. A
regression is a handler whose result is silently dropped, or long jobs starving page loads.

Cluster 1cc -- scenarios 1.747-1.749. Type: U.
Covers: lex/lex_app/reflex/ui/results.py, lex/lex_app/reflex/ui/pools.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1cc_ui_results_and_pools.py -v
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import threading
from unittest import TestCase
from unittest.mock import patch

import pytest

from lex.lex_app.reflex.ui import pools, results

pytestmark = pytest.mark.init


class TestCluster01cc_Results(TestCase):
    """1.747-1.748: return values and their browser events."""

    # -- 1.747 ---------------------------------------------------------
    def test_1_747_a_handler_returns_plain_values_and_nothing_else(self) -> None:
        """
        Scenario 1.747: what a handler may return or yield.
        Given: the four value builders.
        When:  values are built and split as a handler's return value would be.
        Then:  goto, toast and download are results; progress is the progress report, clamped
               to 0..1; None is nothing; a wrong tone, an empty path, a download of nothing and
               any other object (a raw Reflex event) are refused with the handler's name.
        """
        out, report = results.split([results.goto("/a"), results.toast("Saved", tone="success"),
                                     results.progress(1.7, "Reading")], handler="Tickets.open")
        self.assertEqual(out, [results.Goto("/a"), results.Toast("Saved", "success")], "results keep their order")
        self.assertEqual(report, results.Progress(1.0, "Reading"), "progress is clamped")
        self.assertEqual(results.split(None, handler="h"), ([], None), "None is nothing")
        self.assertEqual(results.split(results.goto("/b", replace=True), handler="h")[0], [results.Goto("/b", True)])
        for bad in (lambda: results.toast("x", tone="loud"), lambda: results.goto(""), lambda: results.download()):
            with self.assertRaises(ValueError, msg="invalid values are refused when built"):
                bad()
        with self.assertRaises(TypeError) as raised:
            results.split(object(), handler="Tickets.open")
        self.assertIn("Tickets.open", str(raised.exception), "the error names the handler")
        self.assertEqual(results.Refuse("This request is closed.").message, "This request is closed.")

    # -- 1.748 ---------------------------------------------------------
    def test_1_748_values_become_browser_events_and_are_recorded_for_tests(self) -> None:
        """
        Scenario 1.748: results turn into Reflex events.
        Given: a goto inside the app, a goto to another site, a toast and a download.
        When:  they are converted.
        Then:  an in-app path is Reflex's redirect, another site a whole-page navigation, the
               toast RFDS's toast of that tone, the download Reflex's download; with the test
               recorder on, the values themselves are recorded in order.
        """
        with patch.object(results, "RECORDER", []) as recorded:
            events = results.to_events([results.goto("/tickets/1"), results.goto("https://example.org/x"),
                                        results.toast("Saved", tone="success", description="All good"),
                                        results.download(url="/files/a.csv", filename="a.csv")])
            self.assertEqual(len(events), 4, "one event per result")
            rendered = [str(event) for event in events]
            self.assertIn("/tickets/1", rendered[0])
            self.assertIn("window.location.assign", rendered[1], "another site is a whole-page load")
            self.assertIn("Saved", rendered[2])
            self.assertIn("a.csv", rendered[3])
            self.assertEqual([type(r).__name__ for r in recorded], ["Goto", "Goto", "Toast", "Download"])
        self.assertEqual(results.message("lex.ui.x", "Reference: {incident}", incident="AB12"), "Reference: AB12")


class TestCluster01cc_Pools(TestCase):
    """1.749: the two pools."""

    def tearDown(self) -> None:
        pools.shutdown()

    # -- 1.749 ---------------------------------------------------------
    def test_1_749_bodies_run_on_sized_pools_with_the_callers_context(self) -> None:
        """
        Scenario 1.749: where a handler body runs.
        Given: the interactive and the job pool, with their sizes set by environment.
        When:  functions are run on each.
        Then:  they run off the calling thread, on a thread named for their pool; the caller's
               context variables are visible; the sizes come from LEX_UI_ACTION_THREADS and
               LEX_UI_JOB_THREADS (16 and 8 by default, at least 1); a full pool queues.
        """
        marker = contextvars.ContextVar("marker", default="unset")

        async def scenario():
            marker.set("from-caller")
            name, seen = await pools.run("interactive", lambda: (threading.current_thread().name, marker.get()))
            job_name = await pools.run("jobs", lambda: threading.current_thread().name)
            return name, seen, job_name

        with patch.dict(os.environ, {"LEX_UI_ACTION_THREADS": "3", "LEX_UI_JOB_THREADS": "0"}):
            self.assertEqual((pools.size("interactive"), pools.size("jobs")), (3, 1), "sizes from the environment, at least 1")
            name, seen, job_name = asyncio.run(scenario())
        self.assertTrue(name.startswith("lex-ui-interactive"), name)
        self.assertTrue(job_name.startswith("lex-ui-jobs"), job_name)
        self.assertEqual(seen, "from-caller", "the body sees the caller's context variables")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LEX_UI_ACTION_THREADS", None)
            os.environ.pop("LEX_UI_JOB_THREADS", None)
            self.assertEqual((pools.size("interactive"), pools.size("jobs")), (16, 8), "the defaults")
```

- [ ] **Step 2: Run to verify it fails**

Run: `RUN lex/test_project/tests/init/test_1cc_ui_results_and_pools.py`
Expected: collection error, `ModuleNotFoundError: No module named 'lex.lex_app.reflex.ui'`.

- [ ] **Step 3: Write the package docstring**

`lex/lex_app/reflex/ui/__init__.py`:

```python
"""The lex.ui action and state layer: plain synchronous handlers for Reflex dashboards.

Import the public names from :mod:`lex.ui` (``from lex import ui``). This package holds the
implementation, one concern per module. Design:
``docs/superpowers/specs/2026-11-23-reflex-ui-action-state-layer-design.md``.
"""
```

- [ ] **Step 4: Write `results.py`**

```python
"""What a lex.ui handler returns or yields, and the refusal it may raise."""

from __future__ import annotations

import dataclasses
from typing import Any

TONES = ("info", "success", "warning", "error")

#: Tests set this to a list: :func:`to_events` then also records the values it converts.
RECORDER: list | None = None


class Refuse(Exception):
    """An expected refusal: ``message`` is shown to the user as written, and nothing is logged."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = str(message)


@dataclasses.dataclass(frozen=True)
class Goto:
    path: str
    replace: bool = False


@dataclasses.dataclass(frozen=True)
class Toast:
    message: str
    tone: str = "info"
    description: str = ""


@dataclasses.dataclass(frozen=True)
class Download:
    url: str | None = None
    filename: str | None = None
    data: Any = None


@dataclasses.dataclass(frozen=True)
class Progress:
    fraction: float
    message: str = ""


def goto(path: str, *, replace: bool = False) -> Goto:
    """Send the browser to ``path`` (a page of the dashboards, or a full URL)."""
    if not isinstance(path, str) or not path:
        raise ValueError("ui.goto() takes a path such as '/tickets/1' or a full URL")
    return Goto(path, bool(replace))


def toast(message: str, *, tone: str = "info", description: str = "") -> Toast:
    """Show a short message; ``tone`` is one of info, success, warning, error."""
    if tone not in TONES:
        raise ValueError(f"ui.toast() tone must be one of {', '.join(TONES)}; got {tone!r}")
    return Toast(str(message), tone, str(description))


def download(*, url: str | None = None, filename: str | None = None, data: Any = None) -> Download:
    """Hand the user a file: by ``url``, or ``data`` with a ``filename``."""
    if url is None and data is None:
        raise ValueError("ui.download() needs url= or data=")
    return Download(url, filename, data)


def progress(fraction: float, message: str = "") -> Progress:
    """What a job yields to report progress: a fraction from 0 to 1 and a message."""
    return Progress(min(1.0, max(0.0, float(fraction))), str(message))


def split(value: Any, *, handler: str) -> tuple[list, Progress | None]:
    """A handler's returned or yielded value as (results, progress report)."""
    items = [] if value is None else list(value) if isinstance(value, (list, tuple)) else [value]
    out: list = []
    report: Progress | None = None
    for item in items:
        if isinstance(item, Progress):
            report = item
        elif isinstance(item, (Goto, Toast, Download)):
            out.append(item)
        else:
            raise TypeError(
                f"{handler} returned {item!r}. A lex.ui handler returns ui.goto(), ui.toast() or "
                "ui.download(), and a job may yield ui.progress(). Use @ui.custom for raw Reflex events."
            )
    return out, report


def to_events(items: list) -> list:
    """The Reflex events for a handler's results, in order."""
    import reflex as rx

    if RECORDER is not None:
        RECORDER.extend(items)
    events = []
    for item in items:
        if isinstance(item, Goto):
            if "://" in item.path:
                from lex.lex_app.reflex.session import navigate

                events.append(navigate(item.path))
            else:
                events.append(rx.redirect(item.path, replace=item.replace))
        elif isinstance(item, Toast):
            import rfds

            extra = {"description": item.description} if item.description else {}
            events.append(getattr(rfds.toast, item.tone)(item.message, **extra))
        else:
            events.append(rx.download(url=item.url, filename=item.filename, data=item.data))
    return events


def message(key: str, default: str, **params: Any) -> str:
    """A standard message in the app's UI language (RFDS's translation), else ``default``."""
    try:
        from rfds.i18n import translate

        from lex.lex_app.reflex.design import format_settings

        return translate(format_settings()["ui_language"], key, default, **params)
    except Exception:  # noqa: BLE001 - a message must never fail the handler that reports a failure
        return default.format(**params) if params else default
```

- [ ] **Step 5: Write `pools.py`**

```python
"""The two thread pools lex.ui handler bodies run on: interactive (actions, loads) and jobs."""

from __future__ import annotations

import asyncio
import contextvars
import functools
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

_SETTINGS = {"interactive": ("LEX_UI_ACTION_THREADS", 16), "jobs": ("LEX_UI_JOB_THREADS", 8)}
_POOLS: dict[str, ThreadPoolExecutor] = {}
_LOCK = threading.Lock()


def size(kind: str) -> int:
    """Threads of one pool: its environment setting, else its default; never fewer than one."""
    name, default = _SETTINGS[kind]
    try:
        wanted = int(os.getenv(name) or default)
    except ValueError:
        wanted = default
    return max(1, wanted)


def pool(kind: str) -> ThreadPoolExecutor:
    with _LOCK:
        if kind not in _POOLS:
            _POOLS[kind] = ThreadPoolExecutor(max_workers=size(kind), thread_name_prefix=f"lex-ui-{kind}")
        return _POOLS[kind]


async def run(kind: str, fn: Callable[..., Any], /, *args: Any) -> Any:
    """Run ``fn`` on a pool thread, with the caller's context variables and fresh DB connections."""
    from lex.lex_app.reflex.django_orm import _with_fresh_connections

    call = functools.partial(contextvars.copy_context().run, _with_fresh_connections(fn), *args)
    return await asyncio.get_running_loop().run_in_executor(pool(kind), call)


def shutdown() -> None:
    """Stop both pools (tests)."""
    with _LOCK:
        for executor in _POOLS.values():
            executor.shutdown(wait=False, cancel_futures=True)
        _POOLS.clear()
```

- [ ] **Step 6: Run to verify it passes**

Run: `RUN lex/test_project/tests/init/test_1cc_ui_results_and_pools.py`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add lex/lex_app/reflex/ui lex/test_project/tests/init/test_1cc_ui_results_and_pools.py
git commit -m "feat(reflex): lex.ui results and thread pools

Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>"
```

---

### Task 2: The guard — `requires=`, the query block, the acting user

**Files:**
- Create: `lex/lex_app/reflex/ui/guard.py`
- Test: `lex/test_project/tests/init/test_1cd_ui_guard.py`

**Interfaces:**
- Consumes: `lex.lex_app.reflex.auth._resource_name(target) -> str`; `lex.api.utils.operation_context` (a `ContextVar[dict]`).
- Produces: `guard.Requirement(target, scope)` with `.resource`; `guard.signed_in`; `guard.read/create/edit/delete(model)`; `guard.permission(target, scope)`; `guard.normalise(requires) -> tuple | None` (`None` = nothing declared); `guard.granted(requirements, permissions: list[dict]) -> bool`; `guard.UnguardedQuery`; `guard.block_queries(handler: str)` and `guard.acting_user(user)` context managers.

- [ ] **Step 1: Write the failing tests**

```python
"""lex.ui: the guard on direct database access.

Intent: lex-app's API applies hidden fields and row rules; the ORM does not. So a handler body
may query the database only when the handler declared what it requires -- otherwise the first
query is refused with a message naming the handler. With a declared requirement, the user's
Keycloak grants are checked (model-wide grants only, as everywhere in lex-app) and ORM writes
record the signed-in user as created_by / edited_by. A regression is a dashboard that shows rows
the grid would hide, or audit fields naming nobody.

Cluster 1cd -- scenarios 1.750-1.752. Type: I.
Covers: lex/lex_app/reflex/ui/guard.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1cd_ui_guard.py -v
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import Group
from django.test import TestCase

from lex.lex_app.reflex.ui import guard

pytestmark = pytest.mark.init


class TestCluster01cd_Guard(TestCase):
    """1.750-1.752."""

    # -- 1.750 ---------------------------------------------------------
    def test_1_750_requires_values_and_the_grant_check(self) -> None:
        """
        Scenario 1.750: what requires= means.
        Given: Keycloak grants: a model-wide read on auth.Group, and an edit scoped to one record.
        When:  requirements are normalised and checked against them.
        Then:  nothing declared is None; one value or a list is a tuple; ui.read(Group) holds;
               the record-scoped edit does not open the model; a list needs all; ui.signed_in
               always holds; a named resource works like a model.
        """
        grants = [{"rsname": "auth.Group", "scopes": ["read"]},
                  {"rsname": "auth.Group", "scopes": ["edit"], "resource_set_id": "7"}]
        self.assertIsNone(guard.normalise(None), "nothing declared")
        self.assertEqual(guard.normalise(guard.read(Group)), (guard.Requirement(Group, "read"),))
        self.assertEqual(guard.read(Group).resource, "auth.Group")
        self.assertTrue(guard.granted(guard.normalise(guard.read(Group)), grants), "a model-wide grant holds")
        self.assertFalse(guard.granted(guard.normalise(guard.edit(Group)), grants), "a record-scoped grant does not")
        self.assertFalse(guard.granted(guard.normalise([guard.read(Group), guard.delete(Group)]), grants), "a list needs all")
        self.assertTrue(guard.granted(guard.normalise(guard.signed_in), []), "signed_in needs no grant")
        self.assertTrue(guard.granted(guard.normalise(guard.permission("auth.Group", "read")), grants))
        self.assertEqual({guard.create(Group).scope, guard.delete(Group).scope}, {"create", "delete"})
        with self.assertRaises(TypeError, msg="anything else is refused"):
            guard.normalise("auth.Group:read")

    # -- 1.751 ---------------------------------------------------------
    def test_1_751_queries_are_refused_inside_the_block_and_work_after_it(self) -> None:
        """
        Scenario 1.751: an unguarded handler cannot query the database.
        Given: a model with a row.
        When:  it is queried inside block_queries, then after it.
        Then:  inside, the query raises UnguardedQuery naming the handler and the two ways out
               (self.api, requires=); after the block, queries work again.
        """
        Group.objects.create(name="alpha")
        with guard.block_queries("Tickets.load_totals"):
            with self.assertRaises(guard.UnguardedQuery) as raised:
                Group.objects.count()
        text = str(raised.exception)
        for expected in ("Tickets.load_totals", "self.api", "requires="):
            self.assertIn(expected, text, f"the error must mention {expected}")
        self.assertEqual(Group.objects.count(), 1, "queries work again after the block")

    # -- 1.752 ---------------------------------------------------------
    def test_1_752_the_acting_user_is_set_for_audit_fields(self) -> None:
        """
        Scenario 1.752: ORM writes in a handler name the signed-in user.
        Given: a signed-in user with a username and an e-mail.
        When:  code runs inside acting_user.
        Then:  lex-app's operation context carries a request whose user is authenticated and
               reads as the username (the e-mail when there is none); it is restored afterwards.
        """
        from lex.api.utils import operation_context

        before = operation_context.get()
        with guard.acting_user(SimpleNamespace(username="anna", email="anna@example.test")):
            request = operation_context.get()["request_obj"]
            self.assertTrue(request.user.is_authenticated)
            self.assertEqual(str(request.user), "anna")
        with guard.acting_user(SimpleNamespace(username="", email="anna@example.test")):
            self.assertEqual(str(operation_context.get()["request_obj"].user), "anna@example.test")
        self.assertEqual(operation_context.get(), before, "the context is restored")
```

- [ ] **Step 2: Run to verify it fails**

Run: `RUN lex/test_project/tests/init/test_1cd_ui_guard.py`
Expected: `ImportError: cannot import name 'guard'`.

- [ ] **Step 3: Write `guard.py`**

```python
"""What a lex.ui handler requires, and the guard on direct database access."""

from __future__ import annotations

import contextlib
import dataclasses
import types
from typing import Any, Iterator


@dataclasses.dataclass(frozen=True)
class Requirement:
    """Keycloak grants ``scope`` on ``target`` (a model class or a resource name), model-wide."""

    target: Any
    scope: str

    @property
    def resource(self) -> str:
        from lex.lex_app.reflex.auth import _resource_name

        return _resource_name(self.target)


class _SignedIn:
    def __repr__(self) -> str:
        return "ui.signed_in"


#: ``requires=ui.signed_in``: any signed-in user may run the handler's queries.
signed_in = _SignedIn()


def permission(target: Any, scope: str) -> Requirement:
    return Requirement(target, str(scope))


def read(model: Any) -> Requirement:
    return Requirement(model, "read")


def create(model: Any) -> Requirement:
    return Requirement(model, "create")


def edit(model: Any) -> Requirement:
    return Requirement(model, "edit")


def delete(model: Any) -> Requirement:
    return Requirement(model, "delete")


def normalise(requires: Any) -> tuple | None:
    """``requires=`` as a tuple of requirements; ``None`` when the handler declared nothing."""
    if requires is None:
        return None
    items = tuple(requires) if isinstance(requires, (list, tuple)) else (requires,)
    for item in items:
        if not isinstance(item, (Requirement, _SignedIn)):
            raise TypeError(
                f"requires= takes ui.read(Model), ui.create(), ui.edit(), ui.delete(), ui.permission() "
                f"or ui.signed_in (or a list of them); got {item!r}"
            )
    return items


def granted(requirements: tuple, permissions: list[dict]) -> bool:
    """Whether Keycloak's ``permissions`` meet every requirement (model-wide grants only)."""
    for item in requirements:
        if isinstance(item, _SignedIn):
            continue
        if not any(
            isinstance(grant, dict)
            and grant.get("rsname") == item.resource
            and grant.get("resource_set_id") is None
            and item.scope in (grant.get("scopes") or ())
            for grant in permissions or ()
        ):
            return False
    return True


class UnguardedQuery(RuntimeError):
    """A lex.ui handler queried the database without declaring what it requires."""


@contextlib.contextmanager
def block_queries(handler: str) -> Iterator[None]:
    """Refuse every database query on this thread while the block is open."""
    from django.db import connections

    def refuse(execute, sql, params, many, context):
        raise UnguardedQuery(
            f"{handler} queried the database directly, which bypasses lex-app's field and row rules. "
            "Read and write through self.api, or declare what the handler requires: "
            "@ui.action(requires=ui.read(Model))."
        )

    with contextlib.ExitStack() as stack:
        for connection in connections.all():
            stack.enter_context(connection.execute_wrapper(refuse))
        yield


class _Actor:
    """The signed-in user as lex-app's audit fields read a request's user."""

    is_authenticated = True

    def __init__(self, user: Any):
        self._name = str(getattr(user, "username", "") or getattr(user, "email", "") or "")

    def __str__(self) -> str:
        return self._name


@contextlib.contextmanager
def acting_user(user: Any) -> Iterator[None]:
    """Make ``user`` the actor of ORM writes on this thread (created_by / edited_by)."""
    from lex.api.utils import operation_context

    current = operation_context.get()
    request = types.SimpleNamespace(user=_Actor(user))
    token = operation_context.set({**(current if isinstance(current, dict) else {}), "request_obj": request})
    try:
        yield
    finally:
        operation_context.reset(token)
```

- [ ] **Step 4: Run to verify it passes**

Run: `RUN lex/test_project/tests/init/test_1cd_ui_guard.py`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/reflex/ui/guard.py lex/test_project/tests/init/test_1cd_ui_guard.py
git commit -m "feat(reflex): lex.ui guard - requires values, the query block, the acting user

Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>"
```

---

### Task 3: The working copy

**Files:**
- Create: `lex/lex_app/reflex/ui/workcopy.py`
- Test: `lex/test_project/tests/init/test_1ce_ui_working_copy.py`

**Interfaces:**
- Produces: `workcopy.raw(state)`; `workcopy.fields_of(state_cls) -> tuple[str, ...]` (the class's own vars and backend vars); `workcopy.snapshot(state) -> dict`; `workcopy.restore(state, snap) -> None`; `workcopy.reset(state, keep=()) -> None`; `workcopy.Draft(state_cls, values)` with `.changed() -> dict` and `.rebase(values)`; `workcopy.merge(state, draft) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

```python
"""lex.ui: the working copy a load or job works on, and the snapshot a failed handler goes back to.

Intent: a job's body must not hold the user's lock, so it works on a copy of the state's fields;
only what it changed is written back, so what the user changed meanwhile survives. A failed
handler's changes are discarded by restoring a snapshot. A regression is a job overwriting the
user's input with stale values, or half-finished changes staying on screen after an error.

Cluster 1ce -- scenarios 1.753-1.755. Type: U.
Covers: lex/lex_app/reflex/ui/workcopy.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1ce_ui_working_copy.py -v
"""

from __future__ import annotations

from typing import ClassVar
from unittest import TestCase

import pytest
import reflex as rx

from lex.lex_app.reflex.ui import workcopy

pytestmark = pytest.mark.init


class _Parent(rx.State):
    shared: int = 0


class _Tickets(_Parent):
    LIMIT: ClassVar[int] = 5
    rows: list[dict] = []
    note: str = ""
    progress: int = 0
    _cursor: int = 0

    @rx.var
    def total(self) -> int:
        return len(self.rows)

    def _double(self) -> int:
        return self.progress * 2

    @rx.event
    def refresh(self):
        pass


class _Fake:
    """Stands in for a live state: plain attributes, same class contract."""

    def __init__(self, **values):
        self.__dict__.update(values)


class TestCluster01ce_WorkingCopy(TestCase):
    """1.753-1.755."""

    def _live(self, **values):
        live = _Fake(rows=[], note="", progress=0, _cursor=0)
        live.__dict__.update(values)
        live.__class__ = type("Live", (_Fake,), {"base_vars": _Tickets.base_vars, "backend_vars": _Tickets.backend_vars,
                                                 "inherited_backend_vars": _Tickets.inherited_backend_vars,
                                                 "get_fields": _Tickets.get_fields})
        return live

    # -- 1.753 ---------------------------------------------------------
    def test_1_753_a_snapshot_holds_the_states_own_fields_and_restores_them(self) -> None:
        """
        Scenario 1.753: snapshot, restore and reset.
        Given: a state with its own fields (one of them a backend field) and an inherited one.
        When:  it is snapshotted, changed in place, restored; then reset with one field kept.
        Then:  the snapshot holds deep copies of the class's own fields only; restore puts every
               changed field back; reset sets fields to their class defaults except the kept one.
        """
        self.assertEqual(set(workcopy.fields_of(_Tickets)), {"rows", "note", "progress", "_cursor"},
                         "own fields, the backend one included, not the inherited one")
        live = self._live(rows=[{"id": 1}], note="x")
        snap = workcopy.snapshot(live)
        live.rows.append({"id": 2})
        live.note = "changed"
        self.assertEqual(snap["rows"], [{"id": 1}], "the snapshot is a deep copy")
        workcopy.restore(live, snap)
        self.assertEqual((live.rows, live.note), ([{"id": 1}], "x"), "restore puts changed fields back")
        live.progress = 9
        workcopy.reset(live, keep=("note",))
        self.assertEqual((live.rows, live.note, live.progress, live._cursor), ([], "x", 0, 0),
                         "reset to defaults, the kept field untouched")

    # -- 1.754 ---------------------------------------------------------
    def test_1_754_a_draft_reads_and_writes_fields_and_reaches_helpers(self) -> None:
        """
        Scenario 1.754: what a job body sees as self.
        Given: a draft of the state's fields.
        When:  the body reads and assigns fields, changes a list in place, reads a computed
               value, a class constant and an underscore helper, and misuses it.
        Then:  fields behave as on the state; the computed value and the helper work on the
               draft's values; assigning an undeclared field and calling another event handler
               are refused with a message that says what to do.
        """
        draft = workcopy.Draft(_Tickets, {"rows": [{"id": 1}], "note": "", "progress": 3, "_cursor": 0})
        draft.rows.append({"id": 2})
        draft.note = "typed"
        self.assertEqual((draft.total, draft._double(), draft.LIMIT), (2, 6, 5),
                         "computed values, helpers and constants work on the draft")
        self.assertEqual(set(draft.changed()), {"rows", "note"}, "in-place changes and assignments are both seen")
        with self.assertRaises(AttributeError) as undeclared:
            draft.nope = 1
        self.assertIn("declare it", str(undeclared.exception))
        with self.assertRaises(AttributeError) as handler:
            draft.refresh()
        self.assertIn("helper method", str(handler.exception))

    # -- 1.755 ---------------------------------------------------------
    def test_1_755_merge_writes_only_what_the_draft_changed(self) -> None:
        """
        Scenario 1.755: merging a job's changes.
        Given: a live state and a draft taken from it; then the user changes `note` on the
               live state while the job changes `progress` and `rows` on the draft.
        When:  the draft is merged, and merged again after both change `note`.
        Then:  the first merge writes progress and rows and leaves the user's note; the draft
               then sees the user's note; when both changed the same field, the job's value wins.
        """
        live = self._live(note="")
        draft = workcopy.Draft(_Tickets, workcopy.snapshot(live))
        live.note = "typed-by-user"
        draft.progress = 50
        draft.rows.append({"id": 1})
        self.assertEqual(sorted(workcopy.merge(live, draft)), ["progress", "rows"])
        self.assertEqual((live.note, live.progress, live.rows), ("typed-by-user", 50, [{"id": 1}]))
        self.assertEqual(draft.note, "typed-by-user", "after a merge the draft sees the user's change")
        self.assertEqual(draft.changed(), {}, "nothing is pending after a merge")
        live.note = "user-again"
        draft.note = "set-by-job"
        workcopy.merge(live, draft)
        self.assertEqual(live.note, "set-by-job", "the job wins a same-field conflict")
```

- [ ] **Step 2: Run to verify it fails**

Run: `RUN lex/test_project/tests/init/test_1ce_ui_working_copy.py`
Expected: `ImportError: cannot import name 'workcopy'`.

- [ ] **Step 3: Write `workcopy.py`**

```python
"""Copies of a state's fields: the snapshot a failed handler returns to, and a job's working copy."""

from __future__ import annotations

import copy
import inspect
import types
from typing import Any


def raw(state: Any) -> Any:
    """The state itself, behind a background handler's proxy."""
    return getattr(state, "__wrapped__", state)


def fields_of(state_cls: Any) -> tuple[str, ...]:
    """The class's own fields: its vars, and its backend vars (not the inherited ones)."""
    inherited = set(getattr(state_cls, "inherited_backend_vars", {}) or {})
    backend = [name for name in state_cls.backend_vars if name not in inherited]
    return (*state_cls.base_vars, *backend)


def _plain(value: Any) -> Any:
    """A deep copy of a field's value, without Reflex's change-tracking wrapper."""
    return copy.deepcopy(getattr(value, "__wrapped__", value))


def snapshot(state: Any) -> dict[str, Any]:
    """Deep copies of the state's own fields."""
    target = raw(state)
    return {name: _plain(getattr(target, name)) for name in fields_of(type(target))}


def restore(state: Any, snap: dict[str, Any]) -> None:
    """Set every field that differs from ``snap`` back to its snapshot value."""
    target = raw(state)
    for name, value in snap.items():
        if _plain(getattr(target, name)) != value:
            setattr(state, name, copy.deepcopy(value))


def reset(state: Any, keep: tuple[str, ...] = ()) -> None:
    """Set the state's own fields to their class defaults, except ``keep``."""
    state_cls = type(raw(state))
    declared = state_cls.get_fields()
    for name in fields_of(state_cls):
        if name in keep:
            continue
        if name in state_cls.backend_vars:
            default = copy.deepcopy(state_cls.backend_vars[name])
        else:
            default = declared[name].default_value()
        setattr(state, name, default)


class Draft:
    """What a load or job body sees as ``self``: the state's fields, as a working copy."""

    __slots__ = ("_cls", "_values", "_base")

    def __init__(self, state_cls: Any, values: dict[str, Any]):
        object.__setattr__(self, "_cls", state_cls)
        object.__setattr__(self, "_values", values)
        object.__setattr__(self, "_base", copy.deepcopy(values))

    def __getattr__(self, name: str) -> Any:
        values = object.__getattribute__(self, "_values")
        if name in values:
            return values[name]
        state_cls = object.__getattribute__(self, "_cls")
        try:
            static = inspect.getattr_static(state_cls, name)
        except AttributeError:
            raise AttributeError(f"{state_cls.__name__} has no attribute {name!r}") from None
        from reflex.event import EventHandler

        if isinstance(static, property):
            return static.fget(self)
        if isinstance(static, EventHandler):
            raise AttributeError(
                f"{state_cls.__name__}.{name} is an event handler. Put shared logic in a helper method "
                f"whose name starts with '_' and call that instead."
            )
        computed = getattr(static, "fget", None)
        if computed is not None:
            return computed(self)
        if inspect.isfunction(static):
            return types.MethodType(static, self)
        return getattr(state_cls, name)

    def __setattr__(self, name: str, value: Any) -> None:
        values = object.__getattribute__(self, "_values")
        if name not in values:
            state_cls = object.__getattribute__(self, "_cls")
            raise AttributeError(f"{state_cls.__name__} has no field {name!r}; declare it on the state to assign it")
        values[name] = value

    def changed(self) -> dict[str, Any]:
        """The fields whose value differs from the last merge (assignments and in-place changes)."""
        values = object.__getattribute__(self, "_values")
        base = object.__getattribute__(self, "_base")
        return {name: value for name, value in values.items() if value != base.get(name)}

    def rebase(self, values: dict[str, Any]) -> None:
        """Start again from ``values`` (the live state's fields after a merge)."""
        object.__setattr__(self, "_values", values)
        object.__setattr__(self, "_base", copy.deepcopy(values))


def merge(state: Any, draft: Draft) -> list[str]:
    """Write the draft's changed fields to ``state``, then refresh the draft from it."""
    changed = draft.changed()
    for name, value in changed.items():
        setattr(state, name, copy.deepcopy(value))
    draft.rebase(snapshot(state))
    return list(changed)
```

- [ ] **Step 4: Run to verify it passes**

Run: `RUN lex/test_project/tests/init/test_1ce_ui_working_copy.py`
Expected: 3 passed. If `fields_of` reports extra names, print `_Tickets.base_vars` and `_Tickets.backend_vars` and adjust the filter so only the four declared fields remain.

- [ ] **Step 5: Commit**

```bash
git add lex/lex_app/reflex/ui/workcopy.py lex/test_project/tests/init/test_1ce_ui_working_copy.py
git commit -m "feat(reflex): lex.ui working copy - snapshot, restore, reset, draft, merge

Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>"
```

---

### Task 4: The invocation context — user, params, the API facade

**Files:**
- Create: `lex/lex_app/reflex/ui/context.py`
- Modify: `lex/lex_app/reflex/session.py` (function `access_token`, about line 344), `lex/lex_app/reflex/auth.py` (function `current_permissions`, about line 104)
- Test: `lex/test_project/tests/init/test_1cf_ui_context.py`

**Interfaces:**
- Consumes: `guard.granted`, `guard.Requirement`, `workcopy.Draft`, `workcopy.snapshot`, `LexApi(token, client=...)`, `LexApiError`, `scope_params`, `wire_value`, `_default_search_field` from `lex.lex_app.reflex.data`.
- Produces: `session.access_token_of(digest) -> str` (async); `auth.permissions_of(token) -> list[dict]` (async); `context.User(id, name, username, email)`; `context.Params(values)`; `context.Session(user, params, token, permissions, api=None, api_client=None)` where `token` and `permissions` are zero-argument async callables; `context.resolve(state) -> Session` (async) and `context.set_resolver(fn) -> previous`; `context.Invocation(handler, kind, session, loop, live, orm)` with `.drafts`, `.saved`, `.on_loop(coro)`, `.can(model, scope)`, `.state_of(state_cls)`; `context.enter(inv) -> token`, `context.leave(token)`, `context.current() -> Invocation`; `context.Api(inv)` with `list/get/create/update/delete`; `context.Rows` (a `list` with `.count`).

- [ ] **Step 1: Refactor the token and permission lookups so they need no state**

In `lex/lex_app/reflex/session.py`, replace the body of `access_token` and add `access_token_of` directly above it:

```python
async def access_token_of(digest: str) -> str:
    """The access token of the sign-in behind a binding digest (see :func:`access_token`)."""
    from oauth2_authcodeflow.exceptions import OIDCSessionEnded

    from lex.lex_app.reflex.django_orm import run_orm

    known = await standing(digest) if digest else None
    if known is None or known.identity is None or known.binding_id is None:
        return ""
    with _LOCK:
        held = _TOKENS.get(known.binding_id)
    if held and held[1] - time.time() >= TOKEN_MIN_TTL:
        return held[0]
    try:
        token = await run_orm(_fresh_token, known.binding_id)
    except OIDCSessionEnded as exc:
        _remember(digest, Standing(time.monotonic(), known.binding_id, reason=f"ended:{exc}"))
        return ""
    if token:
        with _LOCK:
            _TOKENS[known.binding_id] = (token, _token_expiry(token))
    return token


async def access_token(state: Any) -> str:
    """The user's access token for lex-app's API, valid at least :data:`TOKEN_MIN_TTL` seconds.

    ``""`` when there is no sign-in, when it has ended (the binding is then forgotten), or while
    Keycloak cannot be reached and no valid token is left. The browser never sees it.
    """
    return await access_token_of(binding_digest(state))
```

In `lex/lex_app/reflex/auth.py`, replace `current_permissions` with:

```python
async def permissions_of(token: str) -> list[dict]:
    """The UMA permissions Keycloak grants the holder of ``token`` (see :func:`current_permissions`)."""
    if not token:
        return []
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _UMA_LOCK:
        known = _UMA.get(digest)
    if known is not None:
        return list(known[1])
    subject = _token_subject(token)
    fetched = await asyncio.to_thread(_fetch_uma_permissions, token)
    with _UMA_LOCK:
        if fetched is None:
            return list(_LAST_BY_SUBJECT.get(subject, []))
        if len(_UMA) >= _UMA_LIMIT:
            _UMA.clear()
        _UMA[digest] = (subject, fetched)
        _LAST_BY_SUBJECT[subject] = fetched
    return list(fetched)


async def current_permissions(state: rx.State) -> list[dict]:
    """The signed-in user's Keycloak UMA permissions.

    The Reflex counterpart of ``st.session_state.permissions``: the list Keycloak returns, one
    ``{"rsname": ..., "scopes": [...]}`` entry per resource, fetched once per access token. A
    failed lookup keeps what the same user was last granted -- a Keycloak blip should not demote
    someone mid-session -- and never lets one user's permissions stand in for another's. No
    sign-in, no permissions.
    """
    return await permissions_of(await current_access_token(state))
```

Run the existing batches that cover both: `RUN lex/test_project/tests/init/test_1bx_reflex_sign_in_handoff.py` then `RUN lex/test_project/tests/permissions/test_4n_reflex_permissions.py` (find the second file's folder with `ls lex/test_project/tests/*/test_4n_reflex_permissions.py`). Expected: all pass, unchanged counts.

- [ ] **Step 2: Write the failing tests**

```python
"""lex.ui: who is acting, the page's parameters, and lex-app's API as the user.

Intent: a handler body is plain Python on a worker thread, so everything that needs async work
is reachable synchronously through the invocation: the signed-in user, the page's parameters,
the user's grants, and lex-app's API called with the user's token -- fetched for every call, so
a long job never uses an expired one. A regression is a handler acting as nobody, or as a stale
token.

Cluster 1cf -- scenarios 1.756-1.758. Type: U.
Covers: lex/lex_app/reflex/ui/context.py; session.access_token_of; auth.permissions_of.
Run: python -m lex pytest lex/test_project/tests/init/test_1cf_ui_context.py -v
"""

from __future__ import annotations

import asyncio
import json
from unittest import TestCase

import httpx
import pytest
from django.contrib.auth.models import Group

from lex.lex_app.reflex.data import LexApiError
from lex.lex_app.reflex.ui import context, pools

pytestmark = pytest.mark.init


def _session(tokens, grants=(), client=None, **params):
    async def token():
        return tokens.pop(0) if len(tokens) > 1 else tokens[0]

    async def permissions():
        return list(grants)

    return context.Session(user=context.User(id="u1", name="Anna A", username="anna", email="anna@example.test"),
                           params=context.Params(params), token=token, permissions=permissions, api_client=client)


def _in_thread(session, body):
    """Run ``body(inv)`` on a pool thread, as a handler body runs."""
    async def scenario():
        inv = context.Invocation(handler="T.h", kind="action", session=session,
                                 loop=asyncio.get_running_loop(), live=None, orm=False)

        def call():
            token = context.enter(inv)
            try:
                return body(inv)
            finally:
                context.leave(token)

        return await pools.run("interactive", call)

    try:
        return asyncio.run(scenario())
    finally:
        pools.shutdown()


class TestCluster01cf_Context(TestCase):
    """1.756-1.758."""

    # -- 1.756 ---------------------------------------------------------
    def test_1_756_user_params_and_grants_are_reachable_from_the_body(self) -> None:
        """
        Scenario 1.756: what a body can ask without awaiting.
        Given: a session with a user, page parameters and one model-wide grant.
        When:  the body reads them through the current invocation.
        Then:  the user's four names are there; a parameter reads as an attribute or with
               get(); a missing one is an AttributeError that says so; can() answers from the
               grants; outside a handler, current() says it only works inside one.
        """
        session = _session(["tok"], grants=[{"rsname": "auth.Group", "scopes": ["read"]}], section="open")

        def body(inv):
            params = context.current().session.params
            return (inv.session.user.email, params.section, params.get("missing", "dflt"),
                    inv.can(Group, "read"), inv.can(Group, "edit"))

        self.assertEqual(_in_thread(session, body), ("anna@example.test", "open", "dflt", True, False))
        with self.assertRaises(AttributeError) as missing:
            context.Params({}).nope
        self.assertIn("no parameter", str(missing.exception))
        with self.assertRaises(RuntimeError) as outside:
            context.current()
        self.assertIn("inside a lex.ui handler", str(outside.exception))
        self.assertEqual(context.User.from_profile({"sub": "s", "name": "N", "preferred_username": "u", "email": "e"}),
                         context.User(id="s", name="N", username="u", email="e"))

    # -- 1.757 ---------------------------------------------------------
    def test_1_757_the_api_is_called_as_the_user_with_a_fresh_token_each_time(self) -> None:
        """
        Scenario 1.757: self.api.
        Given: lex-app's API behind a recording transport, and a token that changes between calls.
        When:  the body lists with filters, a search and an ordering, gets, creates, updates
               and deletes.
        Then:  each call carries the token current at that moment as a bearer token; list sends
               the filters (a list as repeated values), the search on the model's text field and
               the ordering, and returns plain rows with the total count, trimmed to the fields
               asked for; the other calls hit lex-app's routes and return the row.
        """
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, dict(request.url.params.multi_items()),
                         request.headers["authorization"], request.content))
            if request.url.path.endswith("/list"):
                return httpx.Response(200, json={"count": 7, "results": [{"id": 1, "name": "alpha", "x": 9}]})
            if request.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(200, json={"id": 1, "name": "alpha"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        session = _session(["first", "second"], client=client)

        def body(inv):
            api = context.Api(inv)
            rows = api.list(Group, filters={"name": ["a", "b"]}, search="al", ordering="-name",
                            page=2, per_page=10, fields=["id", "name"])
            got = api.get(Group, 1)
            made = api.create(Group, {"name": "beta"})
            changed = api.update(Group, 1, {"name": "gamma"})
            gone = api.delete(Group, 1)
            return rows, rows.count, got, made, changed, gone

        rows, count, got, made, changed, gone = _in_thread(session, body)
        self.assertEqual((list(rows), count), ([{"id": 1, "name": "alpha"}], 7), "plain rows, trimmed, with the count")
        self.assertEqual((got, made, changed, gone), ({"id": 1, "name": "alpha"},) * 3 + (None,))
        method, path, query, auth, _ = seen[0]
        self.assertEqual((method, auth), ("GET", "Bearer first"), "the first call uses the first token")
        self.assertTrue(path.endswith("/api/model_entries/group/list"), path)
        self.assertEqual((query["page"], query["perPage"], query["ordering"], query["name__icontains"]),
                         ("2", "10", "-name", "al"))
        self.assertEqual(seen[1][3], "Bearer second", "a later call fetches the token again")
        self.assertEqual([s[0] for s in seen], ["GET", "GET", "POST", "PATCH", "DELETE"])
        self.assertEqual(json.loads(seen[2][4]), {"name": "beta"})

    # -- 1.758 ---------------------------------------------------------
    def test_1_758_a_refusal_is_an_api_error_and_a_stand_in_replaces_the_api(self) -> None:
        """
        Scenario 1.758: refusals, no sign-in, and the test stand-in.
        Given: an API that answers 403; a session with no token; a session with a stand-in api.
        When:  the body calls the API.
        Then:  403 raises LexApiError with denied true; no token raises 401; with a stand-in,
               self.api is the stand-in itself.
        """
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403, json={"detail": "no"})))
        with self.assertRaises(LexApiError) as denied:
            _in_thread(_session(["tok"], client=client), lambda inv: context.Api(inv).get(Group, 1))
        self.assertTrue(denied.exception.denied)
        with self.assertRaises(LexApiError) as anonymous:
            _in_thread(_session([""]), lambda inv: context.Api(inv).get(Group, 1))
        self.assertEqual(anonymous.exception.status, 401)
        session = _session(["tok"])
        session.api = stand_in = object()
        self.assertIs(_in_thread(session, lambda inv: context.api_of(inv)), stand_in)
```

- [ ] **Step 3: Run to verify it fails**

Run: `RUN lex/test_project/tests/init/test_1cf_ui_context.py`
Expected: `ImportError: cannot import name 'context'`.

- [ ] **Step 4: Write `context.py`**

```python
"""One handler invocation: who is acting, the page's parameters, and lex-app's API as the user."""

from __future__ import annotations

import asyncio
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
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(LOOP_TIMEOUT_SECONDS)

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
```

- [ ] **Step 5: Run to verify it passes**

Run: `RUN lex/test_project/tests/init/test_1cf_ui_context.py`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add lex/lex_app/reflex/ui/context.py lex/lex_app/reflex/session.py lex/lex_app/reflex/auth.py lex/test_project/tests/init/test_1cf_ui_context.py
git commit -m "feat(reflex): lex.ui invocation context - user, params, grants, the API as the user

Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>"
```

---

### Task 5: `ui.State` and run status

**Files:**
- Create: `lex/lex_app/reflex/ui/runs.py`, `lex/lex_app/reflex/ui/state.py`
- Test: `lex/test_project/tests/init/test_1cg_ui_state_and_status.py`

**Interfaces:**
- Consumes: `context.current()`, `context.api_of(inv)`.
- Produces: `state.State(rx.State)` with fields `ui_running: dict[str, list[str]]`, `ui_failed: dict[str, str]`, `ui_progress: dict[str, float]`, `ui_progress_message: dict[str, str]`, `ui_visit: int`, `ui_error: dict[str, str]`, backend fields `_ui_boots: dict[str, str]`, `_ui_pending_loads: list[str]`, `_ui_optional_loads: list[str]`; properties `user`, `api`, `params`, `can`, `state`; `runs.BOOT`; `runs.key_of(handler) -> str`; `runs.arg_key(args, kwargs) -> str`; `runs.mark_started(s, key, arg)`, `runs.mark_progress(s, key, report)`, `runs.mark_finished(s, key, arg, error="")`; `runs.claim(token, key, arg) -> Run | None`, `runs.release(token, key, arg)`, `runs.request_cancel(token, key) -> int`, `runs.Run.cancel`; view helpers `runs.running(handler, arg=None)`, `runs.failed(handler)`, `runs.progress_of(handler)`.

- [ ] **Step 1: Write the failing tests**

```python
"""lex.ui: the state base class, and a handler's status without hand-written flags.

Intent: every page state inherits ui.State, which carries what a view needs to show a handler's
status -- is it running (for which row), did it fail, how far is it -- and gives a body self.user,
self.api, self.params, self.can and self.state. A regression is a spinner that never stops, or a
project writing its own busy flags again.

Cluster 1cg -- scenarios 1.759-1.761. Type: U.
Covers: lex/lex_app/reflex/ui/state.py, lex/lex_app/reflex/ui/runs.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1cg_ui_state_and_status.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase

import pytest
import reflex as rx

from lex.lex_app.reflex.ui import results, runs
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init


class _Board(State):
    rows: list[dict[str, str]] = []

    @rx.event
    def open_ticket(self, code: str):
        pass


def _status():
    return SimpleNamespace(ui_running={}, ui_failed={}, ui_progress={}, ui_progress_message={}, _ui_boots={})


class TestCluster01cg_State(TestCase):
    """1.759-1.761."""

    # -- 1.759 ---------------------------------------------------------
    def test_1_759_ui_state_adds_fields_and_helpers_but_no_event_handlers_of_its_own_names(self) -> None:
        """
        Scenario 1.759: what ui.State is.
        Given: ui.State and a project state inheriting it.
        When:  their fields and handlers are listed.
        Then:  ui.State is a real Reflex state with the status fields and the visit counter;
               user, api, params, can and state are properties, not event handlers; the
               project's state inherits the status fields and keeps only its own as its own.
        """
        for name in ("ui_running", "ui_failed", "ui_progress", "ui_progress_message", "ui_visit", "ui_error"):
            self.assertIn(name, State.base_vars, f"{name} is a field of ui.State")
            self.assertIn(name, _Board.inherited_vars, f"{name} is inherited by a project state")
        self.assertEqual(list(_Board.base_vars), ["rows"], "a project state's own fields are only its own")
        for name in ("user", "api", "params", "can", "state"):
            self.assertIsInstance(State.__dict__[name], property, f"{name} is a property")
            self.assertNotIn(name, State.event_handlers, f"{name} must not be callable from the browser")
        self.assertIn("ui_open", State.event_handlers)
        self.assertIn("ui_cancel", State.event_handlers)

    # -- 1.760 ---------------------------------------------------------
    def test_1_760_status_marks_follow_a_runs_life(self) -> None:
        """
        Scenario 1.760: started, progress, finished.
        Given: an empty status.
        When:  a handler starts for row 7, reports progress, and finishes -- once cleanly,
               once with a failure message; and a second run for row 8 overlaps.
        Then:  running lists the rows in flight; progress holds the fraction and the message;
               finishing removes the row and the progress, records the failure message, and a
               new start clears the previous failure; the registry lets one run per key and
               argument in, and a cancel request reaches every run of that handler.
        """
        s = _status()
        key = runs.key_of(_Board.open_ticket)
        self.assertEqual(key, "_Board.open_ticket")
        self.assertEqual((runs.arg_key((7,), {}), runs.arg_key((), {"code": "x"}), runs.arg_key((), {})), ("7", "x", ""))
        runs.mark_started(s, key, "7")
        runs.mark_started(s, key, "8")
        runs.mark_progress(s, key, results.progress(0.4, "Reading"))
        self.assertEqual((s.ui_running[key], s.ui_progress[key], s.ui_progress_message[key]), (["7", "8"], 0.4, "Reading"))
        self.assertEqual(s._ui_boots[f"{key}|7"], runs.BOOT, "each run records the process that started it")
        runs.mark_finished(s, key, "7", error="It failed.")
        self.assertEqual((s.ui_running[key], s.ui_failed[key]), (["8"], "It failed."))
        runs.mark_finished(s, key, "8")
        self.assertNotIn(key, s.ui_running, "nothing is running")
        self.assertNotIn(key, s.ui_progress, "progress goes with the run")
        runs.mark_started(s, key, "9")
        self.assertNotIn(key, s.ui_failed, "a new start clears the last failure")

        first = runs.claim("tab1", key, "7")
        self.assertIsNotNone(first)
        self.assertIsNone(runs.claim("tab1", key, "7"), "the same run cannot be claimed twice")
        self.assertIsNotNone(runs.claim("tab1", key, "8"), "another argument is another run")
        self.assertIsNotNone(runs.claim("tab2", key, "7"), "another tab is another run")
        self.assertEqual(runs.request_cancel("tab1", key), 2)
        self.assertTrue(first.cancel)
        for tab, arg in (("tab1", "7"), ("tab1", "8"), ("tab2", "7")):
            runs.release(tab, key, arg)
        self.assertIsNotNone(runs.claim("tab1", key, "7"), "released, it can run again")
        runs.release("tab1", key, "7")

    # -- 1.761 ---------------------------------------------------------
    def test_1_761_views_read_status_with_running_failed_and_progress_of(self) -> None:
        """
        Scenario 1.761: the view helpers.
        Given: a handler of a project state.
        When:  running, running for one row, failed and progress_of are built.
        Then:  each is a Reflex expression over ui.State's status fields under the handler's
               key, with a default when nothing was recorded; the per-row form compares the row
               as text.
        """
        key = runs.key_of(_Board.open_ticket)
        any_run = str(runs.running(_Board.open_ticket))
        one_row = str(runs.running(_Board.open_ticket, _Board.rows[0]["id"]))
        self.assertIn("ui_running", any_run)
        self.assertIn(key, any_run)
        self.assertIn("String(", one_row, "the row is compared as text")
        self.assertIn(".includes(", one_row)
        self.assertIn('.includes("7")', str(runs.running(_Board.open_ticket, 7)), "a literal is compared as text too")
        self.assertIn("ui_failed", str(runs.failed(_Board.open_ticket)))
        report = runs.progress_of(_Board.open_ticket)
        self.assertIn("ui_progress", str(report["fraction"]))
        self.assertIn("ui_progress_message", str(report["message"]))
```

- [ ] **Step 2: Run to verify it fails**

Run: `RUN lex/test_project/tests/init/test_1cg_ui_state_and_status.py`
Expected: `ImportError: cannot import name 'runs'`.

- [ ] **Step 3: Write `runs.py`**

```python
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
```

- [ ] **Step 4: Write `state.py`** (the opener and the cancel event are completed in Tasks 7 and 8; here they exist with their final signatures)

```python
"""``ui.State``: the base class of every page state."""

from __future__ import annotations

import reflex as rx

from lex.lex_app.reflex.ui import context


class State(rx.State):
    """The base class of a page state: handler status for views, and the body's helpers.

    Inside a handler body: ``self.user``, ``self.api``, ``self.params``, ``self.can(Model, scope)``
    and ``self.state(Other)``. They are properties, so the browser cannot call them.
    """

    #: Handler key -> the first argument of each run in flight ('' without arguments).
    ui_running: dict[str, list[str]] = {}
    #: Handler key -> the failure message of its last run.
    ui_failed: dict[str, str] = {}
    ui_progress: dict[str, float] = {}
    ui_progress_message: dict[str, str] = {}
    #: Counts page openings in this tab; loads and jobs compare it with the visit they started in.
    ui_visit: int = 0
    #: What ``/ui-error`` shows: kind, title, message, path.
    ui_error: dict[str, str] = {}

    _ui_boots: dict[str, str] = {}
    _ui_pending_loads: list[str] = []
    _ui_optional_loads: list[str] = []

    user = property(lambda self: context.current().session.user)
    api = property(lambda self: context.api_of(context.current()))
    params = property(lambda self: context.current().session.params)
    can = property(lambda self: context.current().can)
    state = property(lambda self: context.current().state_of)

    @rx.event
    async def ui_open(self, required: list[str], optional: list[str], requires: str):
        """A page opened: a new visit. Task 8 makes it start the page's loads."""
        self.ui_visit += 1

    @rx.event
    def ui_cancel(self, key: str):
        """A Cancel button: ask the handler's runs in this tab to stop at their next yield."""
        from reflex_base.event.context import EventContext

        from lex.lex_app.reflex.ui import runs

        runs.request_cancel(EventContext.get().token, key)
```

- [ ] **Step 5: Run to verify it passes**

Run: `RUN lex/test_project/tests/init/test_1cg_ui_state_and_status.py`
Expected: 3 passed. If 1.761 fails on `String(`, print `str(runs.running(_Board.open_ticket, _Board.rows[0]["id"]))` and adjust `_as_text` to whatever `Var` constructor keywords Reflex 0.9.13 accepts; the assertion (the row compared as text with `.includes(`) is the contract.

- [ ] **Step 6: Commit**

```bash
git add lex/lex_app/reflex/ui/runs.py lex/lex_app/reflex/ui/state.py lex/test_project/tests/init/test_1cg_ui_state_and_status.py
git commit -m "feat(reflex): ui.State and handler status - running, failed, progress

Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>"
```

---

### Task 6: `@ui.action`, failures, and the test harness

**Files:**
- Create: `lex/lex_app/reflex/ui/handlers.py`, `lex/lex_app/reflex/ui/testing.py`
- Test: `lex/test_project/tests/init/test_1ch_ui_action.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: `handlers.action(fn=None, *, requires=None)`; `handlers.SLOW_ACTION_SECONDS = 1.0`; `handlers.Outcome(kind, title, message)` with `kind` in `("refused", "denied", "error")`; `handlers.OUTCOMES` (a `list | None` test hook); `handlers._drive(inv, pool, fn, target, args, kwargs)` async generator yielding `_Step` (with `.value`, settable `.stop`) and finally `_Return` (with `.value`); `handlers._classify(key, exc) -> Outcome`; `handlers._toast(outcome) -> results.Toast`; `handlers._denied(session, requirements) -> bool` (async); `handlers._check_plain(fn, decorator)`; every wrapper carries `__ui_kind__`, `__ui_key__`, `__ui_fn__`. `testing.Harness` (async context manager) with `.send(state_cls, handler, **arguments) -> future`, `.fields(state_cls) -> dict`, `.set_fields(state_cls, **values)`, `.updates`, `.results`, `.outcomes`, `.token`; `testing.Result`; `testing.run(state_cls, handler, *, user="tester", grants=(), params=None, fields=None, api=None, **arguments) -> Result`.

- [ ] **Step 1: Write the failing tests**

```python
"""lex.ui: @ui.action -- a short handler written as a plain function.

Intent: a click that finishes in under a second is a plain function on the state. lex-app runs
its body off the event loop on the live state, flushes changes at each yield, marks it running
for the view, and on failure discards what the browser was not yet shown and tells the user: a
refusal in the handler's own words, "not permitted", or a generic message with a reference.
A regression is a slow click stalling every user, half-applied values after an error, or an
exception text reaching the browser.

Cluster 1ch -- scenarios 1.762-1.767. Type: I.
Covers: lex/lex_app/reflex/ui/handlers.py (action, _drive, _classify), lex/lex_app/reflex/ui/testing.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1ch_ui_action.py -v
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group
from django.test import TransactionTestCase

from lex.lex_app.reflex.data import LexApiError
from lex.lex_app.reflex.ui import guard, handlers, results, runs, testing
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init

SEEN: list = []


class Board(State):
    status: str = ""
    items: list[str] = []
    meta: dict[str, int] = {}
    steps: int = 0

    @handlers.action
    def approve(self, code: str):
        SEEN.append((threading.current_thread().name, self.user.username, self.params.get("section")))
        self.status = f"approved-{code}"
        self.items.append(code)
        self.meta["n"] = len(self.items)
        return results.toast("Saved", tone="success")

    @handlers.action
    def stepwise(self):
        self.steps = 1
        yield
        time.sleep(0.3)
        self.steps = 2
        return results.goto("/done")

    @handlers.action
    def breaks_after_a_yield(self):
        self.status = "shown"
        yield
        self.status = "not-shown"
        self.items.append("half")
        raise ValueError("secret detail")

    @handlers.action
    def refuses(self):
        self.status = "half"
        raise results.Refuse("This request is closed.")

    @handlers.action
    def api_denied(self):
        raise LexApiError(403, {"detail": "no"})

    @handlers.action
    def api_invalid(self):
        raise LexApiError(400, {"name": ["Too long."]})

    @handlers.action
    def counts_unguarded(self):
        self.steps = Group.objects.count()

    @handlers.action(requires=guard.read(Group))
    def counts_guarded(self):
        self.steps = Group.objects.count()

    @handlers.action
    def sleeps(self, seconds: float):
        time.sleep(seconds)


class TestCluster01ch_Action(TransactionTestCase):
    """1.762-1.767. Transactional: the body runs on another thread, with its own connection."""

    def setUp(self) -> None:
        SEEN.clear()

    # -- 1.762 ---------------------------------------------------------
    def test_1_762_a_plain_function_runs_off_the_loop_on_the_live_state(self) -> None:
        """
        Scenario 1.762: the happy path.
        Given: an action that assigns a field, changes a list and a dict in place, and returns a toast.
        When:  it is run for a signed-in user on a page with a parameter.
        Then:  the body ran on an interactive pool thread and saw the user and the parameter; the
               fields changed and reached the browser; the toast was shown; nothing is running
               afterwards and no failure is recorded; an async def under @ui.action is refused.
        """
        result = testing.run(Board, "approve", code="A1", user="anna", params={"section": "open"})
        thread, username, section = SEEN[0]
        self.assertTrue(thread.startswith("lex-ui-interactive"), thread)
        self.assertEqual((username, section), ("anna", "open"))
        self.assertEqual((result.fields["status"], result.fields["items"], result.fields["meta"]),
                         ("approved-A1", ["A1"], {"n": 1}))
        self.assertEqual(result.toasts, [results.Toast("Saved", "success")])
        self.assertIn({"status": "approved-A1"}, [{k: v for k, v in u.items() if k == "status"} for u in result.updates])
        self.assertEqual((result.refused, result.denied, result.error), ("", False, ""))
        self.assertEqual(result.running, {}, "nothing is running afterwards")
        with self.assertRaises(TypeError) as raised:
            @handlers.action
            async def bad(self):
                pass
        self.assertIn("plain function", str(raised.exception))

    # -- 1.763 ---------------------------------------------------------
    def test_1_763_running_is_shown_first_and_each_yield_flushes(self) -> None:
        """
        Scenario 1.763: what the browser sees, in order.
        Given: an action that sets a field, yields, works 0.3 s, sets it again and goes to a page.
        When:  it runs.
        Then:  the browser first learns the handler is running, then sees steps=1 before the
               body has finished, then steps=2; "running" is cleared at the end; the goto is sent.
        """
        result = testing.run(Board, "stepwise")
        key = runs.key_of(Board.stepwise)
        marks = [u["ui_running"] for u in result.updates if "ui_running" in u]
        self.assertIn({key: [""]}, marks, "the browser is told the handler is running")
        self.assertEqual(marks[-1], {}, "the last update says it is not")
        # A new tab's first update carries the whole state (steps=0), so only real steps count.
        steps = [(t, u["steps"]) for t, u in zip(result.update_times, result.updates) if u.get("steps")]
        self.assertEqual([value for _, value in steps], [1, 2])
        first_running = next(i for i, u in enumerate(result.updates) if u.get("ui_running") == {key: [""]})
        first_step = next(i for i, u in enumerate(result.updates) if u.get("steps") == 1)
        self.assertLessEqual(first_running, first_step, "running is shown no later than the body's first change")
        self.assertGreaterEqual(steps[1][0] - steps[0][0], 0.25, "steps=1 reached the browser before the body finished")
        self.assertEqual(result.goto, "/done")

    # -- 1.764 ---------------------------------------------------------
    def test_1_764_a_failure_discards_what_was_not_shown_and_hides_the_detail(self) -> None:
        """
        Scenario 1.764: an unexpected error.
        Given: an action that shows a value at a yield, changes more, then raises.
        When:  it runs, with full logging and with LEX_UI_LOG_ERROR_TEXT=false.
        Then:  the fields are back at what the yield showed; the user gets an error toast with a
               reference and without the exception's text; the failure message is recorded for
               the view; the log holds the traceback by default and only the type when limited.
        """
        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR") as logs:
            result = testing.run(Board, "breaks_after_a_yield")
        self.assertEqual((result.fields["status"], result.fields["items"]), ("shown", []),
                         "what the browser saw stays; the rest is discarded")
        self.assertTrue(result.error, "an unexpected error is reported")
        self.assertIn("Reference", result.error)
        self.assertNotIn("secret detail", result.error, "the exception text never reaches the browser")
        self.assertEqual(result.toasts[-1].tone, "error")
        self.assertEqual(result.failed[runs.key_of(Board.breaks_after_a_yield)], result.error)
        self.assertIn("secret detail", "\n".join(logs.output), "the log has the traceback")
        with patch.dict(os.environ, {"LEX_UI_LOG_ERROR_TEXT": "false"}):
            with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR") as limited:
                testing.run(Board, "breaks_after_a_yield")
        text = "\n".join(limited.output)
        self.assertIn("ValueError", text)
        self.assertNotIn("secret detail", text, "limited logging keeps the text out")

    # -- 1.765 ---------------------------------------------------------
    def test_1_765_refusals_and_api_answers_are_shown_in_their_own_words(self) -> None:
        """
        Scenario 1.765: expected endings.
        Given: actions that raise ui.Refuse, get a 403 from the API, and get a validation error.
        When:  each runs.
        Then:  the refusal's message is shown as written, as a warning, and its changes are
               discarded; the 403 is the standard "not permitted" message; the validation error
               is shown with the API's detail; none is reported as an unexpected error.
        """
        refused = testing.run(Board, "refuses")
        self.assertEqual((refused.refused, refused.fields["status"]), ("This request is closed.", ""))
        self.assertEqual((refused.toasts[-1].tone, refused.error), ("warning", ""))
        denied = testing.run(Board, "api_denied")
        self.assertTrue(denied.denied)
        self.assertIn("not permitted", denied.toasts[-1].message.lower())
        invalid = testing.run(Board, "api_invalid")
        self.assertIn("Too long.", invalid.refused, "the API's detail is the message")

    # -- 1.766 ---------------------------------------------------------
    def test_1_766_the_database_is_reachable_only_with_requires(self) -> None:
        """
        Scenario 1.766: the ORM rule, end to end.
        Given: two rows; an action that counts them without requires=, and one with requires=read.
        When:  each runs, the second with and without the grant.
        Then:  without requires= the query is refused and reported as an unexpected error whose
               log names the handler and the way out; with the grant the count is read; without
               the grant the body never runs and the user is told they are not permitted.
        """
        Group.objects.create(name="a")
        Group.objects.create(name="b")
        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR") as logs:
            unguarded = testing.run(Board, "counts_unguarded")
        self.assertTrue(unguarded.error)
        self.assertIn("requires=", "\n".join(logs.output))
        granted = testing.run(Board, "counts_guarded", grants=[(Group, "read")])
        self.assertEqual((granted.fields["steps"], granted.error, granted.denied), (2, "", False))
        refused = testing.run(Board, "counts_guarded")
        self.assertTrue(refused.denied)
        self.assertEqual(refused.fields["steps"], 0, "the body never ran")

    # -- 1.767 ---------------------------------------------------------
    def test_1_767_the_loop_stays_free_and_a_slow_action_is_called_out(self) -> None:
        """
        Scenario 1.767: one user's slow action and everyone else.
        Given: an action that sleeps.
        When:  it sleeps 0.4 s while a ticker runs on the event loop; and 1.1 s in development.
        Then:  the loop keeps ticking during the sleep; the 1.1 s run logs a warning that names
               the handler and says to make it a job.
        """
        async def scenario():
            async with testing.Harness() as harness:
                ticks = 0

                async def tick():
                    nonlocal ticks
                    while True:
                        await asyncio.sleep(0.01)
                        ticks += 1

                ticker = asyncio.create_task(tick())
                await (await harness.send(Board, "sleeps", seconds=0.4))
                ticker.cancel()
                return ticks

        self.assertGreater(asyncio.run(scenario()), 20, "the event loop was not blocked by the sleeping body")
        with patch.object(handlers, "_is_development", return_value=True):
            with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="WARNING") as logs:
                testing.run(Board, "sleeps", seconds=1.1)
        text = "\n".join(logs.output)
        self.assertIn("Board.sleeps", text)
        self.assertIn("@ui.job", text)
```

- [ ] **Step 2: Run to verify it fails**

Run: `RUN lex/test_project/tests/init/test_1ch_ui_action.py`
Expected: `ImportError: cannot import name 'handlers'`.

- [ ] **Step 3: Write `handlers.py`** (this task adds the shared parts and `action`; Tasks 7, 8 and 10 append `job`, `load` and `custom`)

```python
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
            inv.saved[id(self)] = (self, workcopy.snapshot(self))
            runs.mark_started(self, key, arg)
            yield                                    # the browser sees "running" before the body starts
            started = time.monotonic()
            failure: BaseException | None = None
            try:
                async for step in _drive(inv, "interactive", fn, self, args, kwargs):
                    found, report = results.split(step.value, handler=key)
                    if report is not None:
                        runs.mark_progress(self, key, report)
                    for event in results.to_events(found):
                        yield event
                    if isinstance(step, _Step):
                        inv.saved = {ident: (state, workcopy.snapshot(state)) for ident, (state, _) in inv.saved.items()}
                        yield                        # flush what the body changed so far
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
```

- [ ] **Step 4: Write `testing.py`**

```python
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
        self._processor = BaseStateEventProcessor()
        self._running = self._processor.configure(event_namespace=self._sink)
        await self._running.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._running.__aexit__(*exc)
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

    async def send(self, state_cls: Any, handler: str, **arguments: Any) -> Any:
        """Send one event, as the browser would; returns a future that resolves when it has run."""
        from reflex_base.event import Event

        return await self._processor.enqueue(self.token, Event(name=f"{state_cls.get_full_name()}.{handler}",
                                                               payload=arguments))

    async def fields(self, state_cls: Any) -> dict:
        """The state's own fields and the status fields, as they stand."""
        from lex.lex_app.reflex.ui import workcopy

        state = await self._state(state_cls)
        values = workcopy.snapshot(state)
        for name in ("ui_running", "ui_failed", "ui_progress", "ui_progress_message", "ui_visit", "ui_error"):
            values[name] = workcopy._plain(getattr(state, name))
        return values

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
            if fields:
                await harness.set_fields(state_cls, **fields)
            future = await harness.send(state_cls, handler, **arguments)
            try:
                await future
            except Exception:  # noqa: BLE001 - a handler's own failure is in the outcomes
                pass
            await asyncio.sleep(0.05)
            return Result(fields=await harness.fields(state_cls), updates=list(harness.updates),
                          update_times=list(harness.update_times), results=list(harness.results),
                          outcomes=list(harness.outcomes))

    return asyncio.run(scenario())
```

- [ ] **Step 5: Run to verify it passes**

Run: `RUN lex/test_project/tests/init/test_1ch_ui_action.py`
Expected: 6 passed. Known adjustment points, with the contract that must hold:
- If `handler.send` cannot find the event, print `Board.get_full_name()` and `list(Board.event_handlers)`; the event name is `<state full name>.<handler name>`.
- If updates carry the status fields under another suffix than `_rx_state_`, strip that one in `_Sink.emit_update`; the contract is that `updates` holds plain field names.
- 1.766 runs the body on a pool thread with its own database connection, which is why the class is `TransactionTestCase`.

- [ ] **Step 6: Commit**

```bash
git add lex/lex_app/reflex/ui/handlers.py lex/lex_app/reflex/ui/testing.py lex/test_project/tests/init/test_1ch_ui_action.py
git commit -m "feat(reflex): @ui.action - plain synchronous handlers, failures, the test harness

Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>"
```

---

### Task 7: `@ui.job`

**Files:**
- Modify: `lex/lex_app/reflex/ui/handlers.py` (append), `lex/lex_app/reflex/ui/runs.py` (append `cancel`)
- Test: `lex/test_project/tests/init/test_1ci_ui_job.py`

**Interfaces:**
- Consumes: `_drive`, `_classify`, `_toast`, `_denied`, `_mark`, `_check_plain`, `runs.claim/release/Run`, `workcopy.Draft/merge/snapshot`.
- Produces: `handlers.job(fn=None, *, requires=None, stop_on_leave=False)`; `handlers._merge(inv, live) -> None` (async; call inside `async with live`); `runs.cancel(handler) -> EventSpec`.

- [ ] **Step 1: Write the failing tests**

```python
"""lex.ui: @ui.job -- long work that does not block the user.

Intent: work that takes longer than a second runs as a job. Its body works on a copy of the
state; lex-app merges only what the job changed at each yield and at the end, so the user keeps
working and what they typed meanwhile survives. One run at a time per user and argument; a
cancel takes effect at the next yield; a job can stop when the user leaves the page. A regression
is a frozen screen during an export, a job overwriting the user's input, or the same export
running twice.

Cluster 1ci -- scenarios 1.768-1.773. Type: I.
Covers: lex/lex_app/reflex/ui/handlers.py (job, _merge), lex/lex_app/reflex/ui/runs.py (cancel).
Run: python -m lex pytest lex/test_project/tests/init/test_1ci_ui_job.py -v
"""

from __future__ import annotations

import asyncio
import time
from unittest import TestCase

import pytest
import reflex as rx

from lex.lex_app.reflex.ui import handlers, results, runs, testing
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init

CLEANED: list = []
RAN: list = []


class Other(State):
    count: int = 0


class Export(State):
    note: str = ""
    status: str = ""
    progress: int = 0
    seen: list[str] = []
    parts: list[str] = []

    @rx.event
    def set_note(self, value: str):
        self.note = value

    @rx.event
    def set_status(self, value: str):
        self.status = value

    @handlers.job
    def build(self):
        self.seen.append(f"start note={self.note!r}")
        self.progress = 10
        yield results.progress(0.1, "Starting")
        time.sleep(0.5)
        self.seen.append(f"before-yield note={self.note!r}")
        self.parts.append("p1")
        self.state(Other).count += 1
        yield results.progress(0.6, "Half way")
        self.seen.append(f"after-yield note={self.note!r}")
        self.progress = 100
        return results.toast("Export ready", tone="success")

    @handlers.job
    def conflicts(self):
        time.sleep(0.3)
        self.status = "set-by-job"

    @handlers.job
    def slow_for(self, code: str):
        time.sleep(0.4)
        RAN.append(code)

    @handlers.job(stop_on_leave=True)
    def stoppable(self):
        try:
            for index in range(10):
                self.progress = index
                yield
                time.sleep(0.1)
        finally:
            CLEANED.append("closed")

    @handlers.job
    def breaks(self):
        self.status = "shown"
        yield
        self.status = "not-shown"
        raise RuntimeError("boom")


class TestCluster01ci_Job(TestCase):
    """1.768-1.773."""

    def setUp(self) -> None:
        CLEANED.clear()
        RAN.clear()

    # -- 1.768 ---------------------------------------------------------
    def test_1_768_the_user_keeps_working_and_their_changes_survive(self) -> None:
        """
        Scenario 1.768: a job and the user at the same time.
        Given: a job that yields, works 0.5 s, yields and finishes; the user types a note during
               the work.
        When:  both run.
        Then:  the user's event returns at once; the job saw the old note until its next yield
               and the new one after it; at the end the job's fields and the user's note both
               stand; another state the job changed through self.state() is merged too; the
               toast is shown; the browser got progress at each yield.
        """
        async def scenario():
            async with testing.Harness() as h:
                job = await h.send(Export, "build")
                await asyncio.sleep(0.2)
                started = time.perf_counter()
                await (await h.send(Export, "set_note", value="typed-by-user"))
                waited = time.perf_counter() - started
                await job
                await asyncio.sleep(0.1)
                return waited, await h.fields(Export), await h.fields(Other), list(h.updates), list(h.results)

        waited, fields, other, updates, shown = asyncio.run(scenario())
        self.assertLess(waited, 0.2, "the user's click did not wait for the job")
        self.assertEqual(fields["seen"], ["start note=''", "before-yield note=''", "after-yield note='typed-by-user'"],
                         "the job sees the user's change after its next yield")
        self.assertEqual((fields["note"], fields["progress"], fields["parts"]), ("typed-by-user", 100, ["p1"]))
        self.assertEqual(other["count"], 1, "another state changed through self.state() is merged")
        key = runs.key_of(Export.build)
        fractions = [u["ui_progress"].get(key) for u in updates if key in u.get("ui_progress", {})]
        self.assertEqual(fractions[:2], [0.1, 0.6], "progress reached the browser at each yield")
        self.assertEqual(shown, [results.Toast("Export ready", "success")])
        self.assertEqual(fields["ui_running"], {}, "nothing is running afterwards")

    # -- 1.769 ---------------------------------------------------------
    def test_1_769_the_job_wins_when_both_change_the_same_field(self) -> None:
        """
        Scenario 1.769: a same-field conflict.
        Given: a job that sets `status` after 0.3 s; the user sets `status` meanwhile.
        When:  both run.
        Then:  the job's value stands.
        """
        async def scenario():
            async with testing.Harness() as h:
                job = await h.send(Export, "conflicts")
                await asyncio.sleep(0.1)
                await (await h.send(Export, "set_status", value="set-by-user"))
                await job
                await asyncio.sleep(0.1)
                return (await h.fields(Export))["status"]

        self.assertEqual(asyncio.run(scenario()), "set-by-job")

    # -- 1.770 ---------------------------------------------------------
    def test_1_770_one_run_at_a_time_per_user_and_argument(self) -> None:
        """
        Scenario 1.770: repeats.
        Given: a job that takes 0.4 s.
        When:  it is sent three times rapidly for "a", once for "b" meanwhile, and once more
               for "a" after it finished.
        Then:  "a" ran once, the two repeats were ignored, "b" ran beside it, and the later
               "a" ran again.
        """
        async def scenario():
            async with testing.Harness() as h:
                first = [await h.send(Export, "slow_for", code="a") for _ in range(3)]
                other = await h.send(Export, "slow_for", code="b")
                for future in (*first, other):
                    await future
                await asyncio.sleep(0.1)
                await (await h.send(Export, "slow_for", code="a"))
                await asyncio.sleep(0.1)

        asyncio.run(scenario())
        self.assertEqual(sorted(RAN), ["a", "a", "b"], "one run per argument at a time; the repeats were ignored")

    # -- 1.771 ---------------------------------------------------------
    def test_1_771_cancel_takes_effect_at_the_next_yield(self) -> None:
        """
        Scenario 1.771: Cancel.
        Given: a job that yields every 0.1 s inside try/finally.
        When:  the cancel event is sent while it runs.
        Then:  the job stops within a few steps, its finally block runs, and it is no longer
               running; ui.cancel(handler) is the event for a Cancel button.
        """
        async def scenario():
            async with testing.Harness() as h:
                job = await h.send(Export, "stoppable")
                await asyncio.sleep(0.25)
                await (await h.send(State, "ui_cancel", key=runs.key_of(Export.stoppable)))
                await job
                await asyncio.sleep(0.1)
                return await h.fields(Export)

        fields = asyncio.run(scenario())
        self.assertLess(fields["progress"], 9, "the job did not run to the end")
        self.assertEqual(CLEANED, ["closed"], "the body's finally block ran")
        self.assertEqual(fields["ui_running"], {})
        self.assertIn("ui_cancel", str(runs.cancel(Export.stoppable)))

    # -- 1.772 ---------------------------------------------------------
    def test_1_772_stop_on_leave_ends_the_job_when_the_page_changes(self) -> None:
        """
        Scenario 1.772: leaving the page.
        Given: a job with stop_on_leave=True.
        When:  the user opens another page (a new visit) while it runs.
        Then:  the job stops at its next yield and its cleanup runs.
        """
        async def scenario():
            async with testing.Harness() as h:
                job = await h.send(Export, "stoppable")
                await asyncio.sleep(0.25)
                await (await h.send(State, "ui_open", required=[], optional=[], requires=""))
                await job
                await asyncio.sleep(0.1)
                return await h.fields(Export)

        fields = asyncio.run(scenario())
        self.assertLess(fields["progress"], 9)
        self.assertEqual(CLEANED, ["closed"])

    # -- 1.773 ---------------------------------------------------------
    def test_1_773_a_failing_job_keeps_what_was_merged_and_reports(self) -> None:
        """
        Scenario 1.773: failure.
        Given: a job that yields once, changes more, then raises.
        When:  it runs.
        Then:  what was merged at the yield stays, the later change is discarded, an error
               toast is shown, the failure message is readable with ui.failed, and it is no
               longer running.
        """
        result = testing.run(Export, "breaks")
        self.assertEqual(result.fields["status"], "shown")
        self.assertTrue(result.error)
        self.assertEqual(result.toasts[-1].tone, "error")
        self.assertEqual(result.failed[runs.key_of(Export.breaks)], result.error)
        self.assertEqual(result.running, {})
```

- [ ] **Step 2: Run to verify it fails**

Run: `RUN lex/test_project/tests/init/test_1ci_ui_job.py`
Expected: `AttributeError: module 'lex.lex_app.reflex.ui.handlers' has no attribute 'job'`.

- [ ] **Step 3: Append `cancel` to `runs.py`**

```python
def cancel(handler: Any) -> Any:
    """In a view: the event a Cancel button sends for ``handler``."""
    from lex.lex_app.reflex.ui.state import State

    return State.ui_cancel(key_of(handler))
```

- [ ] **Step 4: Append `_merge` and `job` to `handlers.py`**

```python
async def _merge(inv: context.Invocation, live: Any) -> None:
    """Write every working copy's changes to its state. Call inside ``async with live``."""
    own = type(workcopy.raw(live))
    for state_cls, draft in inv.drafts.items():
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
            failure: BaseException | None = None
            started = False
            stopped = False
            try:
                async with self:
                    session = await context.resolve(self)
                    if await _denied(session, requirements):
                        failure = _NotPermitted()
                    else:
                        own = type(workcopy.raw(self))
                        visit = self.ui_visit
                        inv = context.Invocation(handler=key, kind="job", session=session,
                                                 loop=asyncio.get_running_loop(), live=self,
                                                 orm=requirements is not None)
                        inv.drafts[own] = workcopy.Draft(own, workcopy.snapshot(self))
                        runs.mark_started(self, key, arg)
                        started = True
                if failure is None:
                    try:
                        async for step in _drive(inv, "jobs", fn, inv.drafts[own], args, kwargs):
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
                    except Exception as exc:  # noqa: BLE001 - reported to the user below
                        failure = exc
                message = ""
                if failure is not None:
                    outcome = _classify(key, failure)
                    message = outcome.message
                    for event in results.to_events([_toast(outcome)]):
                        yield event
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
```

- [ ] **Step 5: Run to verify it passes**

Run: `RUN lex/test_project/tests/init/test_1ci_ui_job.py`
Expected: 6 passed. If 1.768's `Other.count` is 0, the draft of `Other` was not merged: check that `inv.state_of(Other)` stored it in `inv.drafts` and that `_merge` reaches it with `await live.get_state(Other)` inside the lock.

- [ ] **Step 6: Re-run the action batch** (shared module)

Run: `RUN lex/test_project/tests/init/test_1ch_ui_action.py`
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add lex/lex_app/reflex/ui/handlers.py lex/lex_app/reflex/ui/runs.py lex/test_project/tests/init/test_1ci_ui_job.py
git commit -m "feat(reflex): @ui.job - long work on a working copy, merged at each yield

Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>"
```

---

### Task 8: `@ui.load`, the opener, and the error page

**Files:**
- Modify: `lex/lex_app/reflex/ui/handlers.py` (append `load`, `page_loads`, `registered`), `lex/lex_app/reflex/ui/state.py` (`ui_open`, `error_page`), `lex/lex_app/reflex/shell.py` (`lex_page`), `lex/lex_app/reflex/app.py` (`create_app`)
- Test: `lex/test_project/tests/init/test_1cj_ui_load.py`

**Interfaces:**
- Consumes: Tasks 1–7; `lex.lex_app.reflex.access.lex_permission_resolver(identity) -> frozenset[str]`; `rfds.auth.current_identity(state)`.
- Produces: `handlers.load(fn=None, *, requires=None, optional=False, keep=())`; `handlers.page_loads(on_load) -> (required_names, optional_names, others)`; `handlers.registered(name) -> EventHandler`; `state.State.ui_open(required, optional, requires)` starting loads; `state.ERROR_ROUTE = "/ui-error"`; `state.error_page() -> rx.Component`.

- [ ] **Step 1: Write the failing tests**

```python
"""lex.ui: @ui.load -- filling a page when it opens.

Intent: a page's data is loaded by plain functions that start only after sign-in and the
user's grants are known. Each load first clears the state, so a revisit never shows the last
visit's data; a result that arrives after the user has left is discarded; a required load that
fails shows an error page with a reference, an optional one fails only its own region. A
regression is a page flashing another record's data, loading before sign-in, or one broken
region taking the page down.

Cluster 1cj -- scenarios 1.774-1.778. Type: I.
Covers: lex/lex_app/reflex/ui/handlers.py (load, page_loads, registered), ui/state.py (ui_open,
error_page), lex/lex_app/reflex/shell.py (lex_page), lex/lex_app/reflex/app.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1cj_ui_load.py -v
"""

from __future__ import annotations

import asyncio
import time
from unittest import TestCase
from unittest.mock import patch

import pytest
import reflex as rx

from lex.lex_app.reflex import shell
from lex.lex_app.reflex.ui import handlers, results, runs, testing
from lex.lex_app.reflex.ui import state as ui_state
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init

STARTED: list = []


class Page(State):
    rows: list[str] = []
    filters: str = ""
    region: str = ""

    @handlers.load(keep=("filters",))
    def open(self):
        STARTED.append("open")
        self.rows = [f"row-for-{self.params.get('section', '?')}"]

    @handlers.load(optional=True)
    def open_region(self):
        STARTED.append("region")
        self.region = "filled"

    @handlers.load
    def open_slowly(self):
        time.sleep(0.4)
        self.rows = ["late"]

    @handlers.load
    def open_broken(self):
        raise RuntimeError("database down")

    @handlers.load(optional=True)
    def region_broken(self):
        raise results.Refuse("No figures for this period.")

    @rx.event
    def plain(self):
        pass


class TestCluster01cj_Load(TestCase):
    """1.774-1.778."""

    def setUp(self) -> None:
        STARTED.clear()

    # -- 1.774 ---------------------------------------------------------
    def test_1_774_a_load_resets_the_state_then_fills_it(self) -> None:
        """
        Scenario 1.774: reset, keep, fill.
        Given: a state holding the last visit's rows and filters.
        When:  its load runs on a page with a parameter.
        Then:  the rows were emptied before the body ran and then filled from this visit's
               parameter; the kept field was not reset; the browser saw the empty rows first.
        """
        result = testing.run(Page, "open", params={"section": "open"}, fields={"rows": ["stale"], "filters": "mine"})
        self.assertEqual((result.fields["rows"], result.fields["filters"]), (["row-for-open"], "mine"))
        rows_seen = [u["rows"] for u in result.updates if "rows" in u]
        self.assertEqual(rows_seen[0], [], "the last visit's rows are cleared first")
        self.assertEqual(rows_seen[-1], ["row-for-open"])

    # -- 1.775 ---------------------------------------------------------
    def test_1_775_the_opener_starts_required_loads_then_optional_ones(self) -> None:
        """
        Scenario 1.775: the order of a page's loads.
        Given: a page with a required load and an optional one, registered through lex_page's
               on_load, beside a plain Reflex handler.
        When:  the on_load list is split, and the opener runs for a user who may see the page
               and for one who may not.
        Then:  the split names the required and the optional load and leaves the plain handler
               in place; the opener starts a new visit and the required load, and the optional
               load starts after the required one succeeded; for a user lacking the page's
               permission no load starts.
        """
        required, optional, others = handlers.page_loads([Page.open, Page.open_region, Page.plain])
        self.assertEqual((required, optional), ([runs.key_of(Page.open)], [runs.key_of(Page.open_region)]))
        self.assertEqual(others, [Page.plain], "a plain Reflex handler stays in on_load")
        self.assertIs(handlers.registered(required[0]), Page.open)

        async def scenario(requires):
            async with testing.Harness() as h:
                with patch.object(ui_state, "_page_permitted", return_value=requires != "missing.Permission:read"):
                    await (await h.send(State, "ui_open", required=required, optional=optional, requires=requires))
                    await asyncio.sleep(0.4)
                return await h.fields(Page)

        fields = asyncio.run(scenario(""))
        self.assertEqual(STARTED, ["open", "region"], "the optional load starts after the required one")
        self.assertEqual((fields["region"], fields["ui_visit"]), ("filled", 1))
        STARTED.clear()
        asyncio.run(scenario("missing.Permission:read"))
        self.assertEqual(STARTED, [], "no load starts for a user who may not see the page")

    # -- 1.776 ---------------------------------------------------------
    def test_1_776_a_result_that_arrives_after_the_user_left_is_discarded(self) -> None:
        """
        Scenario 1.776: leaving during a load.
        Given: a load that takes 0.4 s.
        When:  the user opens another page (a new visit) while it runs.
        Then:  the load's result is not written.
        """
        async def scenario():
            async with testing.Harness() as h:
                load = await h.send(Page, "open_slowly")
                await asyncio.sleep(0.15)
                await (await h.send(State, "ui_open", required=[], optional=[], requires=""))
                await load
                await asyncio.sleep(0.1)
                return (await h.fields(Page))["rows"]

        self.assertEqual(asyncio.run(scenario()), [], "a stale visit's result is discarded")

    # -- 1.777 ---------------------------------------------------------
    def test_1_777_a_failed_required_load_shows_the_error_page_an_optional_one_its_region(self) -> None:
        """
        Scenario 1.777: failures of loads.
        Given: a required load that raises, and an optional one that refuses.
        When:  each runs.
        Then:  the required one records a reference and the page to retry, and sends the browser
               to the error page, without the exception's text; the optional one sends nobody
               anywhere and leaves its message for ui.failed.
        """
        with self.assertLogs("lex.lex_app.reflex.ui.handlers", level="ERROR"):
            broken = testing.run(Page, "open_broken")
        self.assertEqual(broken.goto, ui_state.ERROR_ROUTE)
        error = broken.fields["ui_error"]
        self.assertEqual(error["kind"], "error")
        self.assertIn("Reference", error["message"])
        self.assertNotIn("database down", error["message"])
        region = testing.run(Page, "region_broken")
        self.assertIsNone(region.goto, "an optional load never leaves the page")
        self.assertEqual(region.failed[runs.key_of(Page.region_broken)], "No figures for this period.")

    # -- 1.778 ---------------------------------------------------------
    def test_1_778_lex_page_adds_the_opener_and_the_error_page_exists(self) -> None:
        """
        Scenario 1.778: wiring.
        Given: lex_page with a ui load, and the error page.
        When:  a page is registered and the error page is built.
        Then:  the page's on_load is the access load, any plain handler, then the opener
               carrying the load's name and the page's permission -- the load itself is not in
               on_load; the error page shows the message and a way to try again.
        """
        captured = {}

        def fake_page(**kwargs):
            captured.update(kwargs)
            return lambda fn: fn

        with patch("reflex.page", fake_page), patch.object(shell, "_NAV", []):
            shell.lex_page("/tickets", title="Tickets", requires="app.Ticket:read",
                           on_load=[Page.open, Page.plain])(lambda: rx.text("x"))
        loads = captured["on_load"]
        self.assertEqual(len(loads), 3, "access load, the plain handler, the opener")
        self.assertIs(loads[1], Page.plain)
        opener = str(loads[2])
        self.assertIn("ui_open", opener)
        self.assertIn(runs.key_of(Page.open), opener)
        self.assertIn("app.Ticket:read", opener)
        page = str(ui_state.error_page())
        self.assertIn("ui_error", page)
        self.assertIn("Try again", page)
```

- [ ] **Step 2: Run to verify it fails**

Run: `RUN lex/test_project/tests/init/test_1cj_ui_load.py`
Expected: `AttributeError: module ... has no attribute 'load'`.

- [ ] **Step 3: Append `load`, `page_loads` and `registered` to `handlers.py`**

```python
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

        @functools.wraps(fn)
        async def wrapper(self, *args: Any, **kwargs: Any):
            from lex.lex_app.reflex.ui.state import ERROR_ROUTE

            failure: BaseException | None = None
            current = True
            async with self:
                session = await context.resolve(self)
                visit = self.ui_visit
                own = type(workcopy.raw(self))
                workcopy.reset(self, keep=tuple(keep))
                if await _denied(session, requirements):
                    failure = _NotPermitted()
                else:
                    inv = context.Invocation(handler=key, kind="load", session=session,
                                             loop=asyncio.get_running_loop(), live=self,
                                             orm=requirements is not None)
                    inv.drafts[own] = workcopy.Draft(own, workcopy.snapshot(self))
                    runs.mark_started(self, key, "")
            if failure is None:
                try:
                    async for step in _drive(inv, "interactive", fn, inv.drafts[own], args, kwargs):
                        found, report = results.split(step.value, handler=key)
                        async with self:
                            current = self.ui_visit == visit
                            if current:
                                await _merge(inv, self)
                                if report is not None:
                                    runs.mark_progress(self, key, report)
                            elif isinstance(step, _Step):
                                step.stop = True
                        if current:
                            for event in results.to_events(found):
                                yield event
                except Exception as exc:  # noqa: BLE001 - reported below
                    failure = exc
            outcome = _classify(key, failure) if failure is not None else None
            start: list[str] = []
            show_error = False
            async with self:
                current = self.ui_visit == visit
                if failure is None or not isinstance(failure, _NotPermitted):
                    runs.mark_finished(self, key, "", error=outcome.message if outcome and optional else "")
                elif optional:
                    self.ui_failed = {**dict(self.ui_failed), key: outcome.message}
                if current and outcome is not None and not optional:
                    self.ui_error = {"kind": "denied" if outcome.kind == "denied" else "error",
                                     "title": outcome.title, "message": outcome.message,
                                     "path": str(getattr(workcopy.raw(self).router.url, "path", "") or "/")}
                    show_error = True
                if current and outcome is None and not optional:
                    pending = [name for name in self._ui_pending_loads if name != key]
                    self._ui_pending_loads = pending
                    if not pending and self._ui_optional_loads:
                        start = list(self._ui_optional_loads)
                        self._ui_optional_loads = []
            if show_error:
                for event in results.to_events([results.Goto(ERROR_ROUTE, True)]):
                    yield event
            for name in start:
                yield registered(name)

        _mark(wrapper, fn, "load", requires=requirements, optional=optional, keep=tuple(keep))
        return rx.event(wrapper, background=True)

    return decorate(fn) if fn is not None else decorate
```

- [ ] **Step 4: Complete `ui_open` and add the error page in `state.py`**

Replace the `ui_open` method and add the module-level names:

```python
#: The page a failed required load sends the browser to.
ERROR_ROUTE = "/ui-error"


def _page_permitted(state: Any, requires: str) -> bool:
    """Whether the signed-in user holds the page's permission string ('' = no requirement)."""
    if not requires:
        return True
    from rfds.auth import current_identity

    from lex.lex_app.reflex.access import lex_permission_resolver

    return requires in lex_permission_resolver(current_identity(state))
```

```python
    @rx.event
    async def ui_open(self, required: list[str], optional: list[str], requires: str):
        """A page opened: a new visit, then the page's loads.

        ``lex_page`` puts this last in ``on_load``, after the user's grants are loaded. Reflex
        starts background handlers at once, so the loads are started from here, not listed in
        ``on_load`` themselves. Optional loads start when the required ones have succeeded.
        """
        from lex.lex_app.reflex.ui import handlers

        self.ui_visit += 1
        self.ui_error = {}
        if not _page_permitted(self, requires):
            self._ui_pending_loads, self._ui_optional_loads = [], []
            return None
        self._ui_pending_loads = list(required)
        self._ui_optional_loads = list(optional) if required else []
        return [handlers.registered(name) for name in (required or optional)]
```

Add `from typing import Any` to the imports, and append:

```python
def error_page() -> rx.Component:
    """What ``/ui-error`` shows: why the page could not be opened, a reference, and a way back."""
    import rfds

    from lex.lex_app.reflex.shell import lex_chrome

    retry = rfds.link("Try again", href=State.ui_error.get("path", "/"))
    title = State.ui_error.get("title", "This page could not be opened")
    message = State.ui_error.get("message", "Try again.")
    return lex_chrome(rfds.page(
        rfds.page_header(title="This page could not be opened", path=ERROR_ROUTE),
        rx.cond(
            State.ui_error.get("kind", "error") == "denied",
            rfds.empty_state(kind="no_permission", title=title, description=message),
            rfds.empty_state(kind="error", title=title, description=message, primary_action=retry),
        ),
    ))
```

- [ ] **Step 5: Start the loads from `lex_page`, and register the opener and the error page in `create_app`**

In `lex/lex_app/reflex/shell.py`, inside `lex_page`'s `decorator`, replace

```python
        loads: list[Any] = [LexAccessState.load]
        if on_load is not None:
            loads.extend(on_load if isinstance(on_load, (list, tuple)) else [on_load])
```

with

```python
        from lex.lex_app.reflex.ui.handlers import page_loads
        from lex.lex_app.reflex.ui.state import State as UiState

        # lex.ui loads are background handlers, which Reflex starts at once -- before the
        # grants are loaded. So they are started by the opener, the page's last load event.
        required, optional, others = page_loads(on_load)
        loads: list[Any] = [LexAccessState.load, *others, UiState.ui_open(required, optional, requires or "")]
```

and extend the docstring's `on_load` line to: `on_load: Load events, run after the user's grants are loaded. A lex.ui load (``@ui.load``) is started after them, by the opener.`

In `lex/lex_app/reflex/app.py`, in `create_app`, change the `/` page and add the error page:

```python
    from lex.lex_app.reflex.ui.state import ERROR_ROUTE, State as UiState, error_page

    app.add_page(
        dashboard_page(load_structure()),
        route="/",
        on_load=[LexAccessState.load, LexDashboardState.resolve, UiState.ui_open([], [], "")],
    )
    app.add_page(error_page, route=ERROR_ROUTE, title="This page could not be opened", on_load=[LexAccessState.load])
```

(the `app.add_page(dashboard_page(...))` call replaces the existing one; the `/logout` page stays as it is.)

- [ ] **Step 6: Run to verify it passes**

Run: `RUN lex/test_project/tests/init/test_1cj_ui_load.py`
Expected: 5 passed.

- [ ] **Step 7: Re-run the batches that compile the app and count `on_load` entries**

Run: `RUN lex/test_project/tests/init/test_1bt_reflex_design_system.py`, then `RUN lex/test_project/tests/init/test_1aq_reflex_dashboards.py`, then `RUN lex/test_project/tests/init/test_1bw_reflex_developer_tooling.py`
Expected: all pass. If an assertion lists a page's `on_load` handlers exactly, extend its expected list with the opener (`ui_open`) and say in the test's docstring that every lex page now ends its load events with it.

- [ ] **Step 8: Commit**

```bash
git add lex/lex_app/reflex/ui lex/lex_app/reflex/shell.py lex/lex_app/reflex/app.py lex/test_project/tests/init
git commit -m "feat(reflex): @ui.load - page loads started after sign-in, reset, discard, error page

Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>"
```

---

### Task 9: Runs whose process is gone

**Files:**
- Modify: `lex/lex_app/reflex/ui/runs.py` (append), `lex/lex_app/reflex/ui/state.py` (`ui_open`), `lex/lex_app/reflex/app.py` (`create_app`)
- Test: `lex/test_project/tests/init/test_1ck_ui_stale_runs.py`

**Interfaces:**
- Produces: `runs.HEARTBEAT_SECONDS = 10`, `runs.HEARTBEAT_TTL_SECONDS = 30`; `runs.sweep(s, alive=None) -> int` (async; `alive` is an async `(boot) -> bool`, default `runs.alive`); `runs.alive(boot) -> bool` (async); `runs.heartbeat(app)` (an async context manager usable as a Reflex lifespan task).

- [ ] **Step 1: Write the failing tests**

```python
"""lex.ui: a handler whose process died is not "running" forever.

Intent: the view's spinner comes from the state. If the backend process that ran a job dies,
nobody clears the mark, and with state kept on disk or in Redis the mark survives the restart.
So each mark records the process that set it, every page open sweeps marks whose process is
gone, and with Redis each process keeps a heartbeat another worker can check. A regression is a
button that stays disabled until the user opens a new tab.

Cluster 1ck -- scenarios 1.779-1.780. Type: U.
Covers: lex/lex_app/reflex/ui/runs.py (sweep, alive, heartbeat), ui/state.py (ui_open).
Run: python -m lex pytest lex/test_project/tests/init/test_1ck_ui_stale_runs.py -v
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import pytest

from lex.lex_app.reflex.ui import runs

pytestmark = pytest.mark.init


def _status():
    return SimpleNamespace(ui_running={}, ui_failed={}, ui_progress={}, ui_progress_message={}, _ui_boots={})


class TestCluster01ck_StaleRuns(TestCase):
    """1.779-1.780."""

    # -- 1.779 ---------------------------------------------------------
    def test_1_779_a_sweep_clears_runs_of_a_process_that_is_gone(self) -> None:
        """
        Scenario 1.779: the sweep.
        Given: a state marked running by this process for one handler, and by a process that
               no longer exists for another.
        When:  the state is swept.
        Then:  the dead process's run is cleared, with its progress; this process's run stays;
               a process another worker reports alive keeps its run.
        """
        s = _status()
        runs.mark_started(s, "A.mine", "1")
        runs.mark_started(s, "B.theirs", "2")
        s._ui_boots = {**s._ui_boots, "B.theirs|2": "dead-process"}
        s.ui_progress = {"B.theirs": 0.5}

        async def nobody(boot):
            return False

        self.assertEqual(asyncio.run(runs.sweep(s, alive=nobody)), 1)
        self.assertEqual(s.ui_running, {"A.mine": ["1"]})
        self.assertNotIn("B.theirs", s.ui_progress)
        s2 = _status()
        runs.mark_started(s2, "B.theirs", "2")
        s2._ui_boots = {"B.theirs|2": "other-worker"}

        async def everybody(boot):
            return True

        self.assertEqual(asyncio.run(runs.sweep(s2, alive=everybody)), 0, "a live worker's run stays")

    # -- 1.780 ---------------------------------------------------------
    def test_1_780_liveness_is_local_without_redis_and_a_heartbeat_with_it(self) -> None:
        """
        Scenario 1.780: who is alive.
        Given: no Redis; then a Redis stand-in.
        When:  liveness is asked for this process, for another one, and the heartbeat runs.
        Then:  without Redis only this process is alive (one process holds every tab); with
               Redis a process is alive while its heartbeat key exists, and the heartbeat writes
               this process's key with an expiry.
        """
        with patch.object(runs, "_redis", return_value=None):
            self.assertEqual((asyncio.run(runs.alive(runs.BOOT)), asyncio.run(runs.alive("other"))), (True, False))

        class FakeRedis:
            def __init__(self):
                self.keys = {}

            async def set(self, key, value, ex=None):
                self.keys[key] = ex

            async def exists(self, key):
                return 1 if key in self.keys else 0

        redis = FakeRedis()

        async def scenario():
            with patch.object(runs, "_redis", return_value=redis), patch.object(runs, "HEARTBEAT_SECONDS", 0.01):
                async with runs.heartbeat(None):
                    await asyncio.sleep(0.05)
                    return await runs.alive(runs.BOOT), await runs.alive("other")

        self.assertEqual(asyncio.run(scenario()), (True, False))
        self.assertEqual(redis.keys, {f"lex:ui:boot:{runs.BOOT}": runs.HEARTBEAT_TTL_SECONDS})
```

- [ ] **Step 2: Run to verify it fails**

Run: `RUN lex/test_project/tests/init/test_1ck_ui_stale_runs.py`
Expected: `AttributeError: module ... has no attribute 'sweep'`.

- [ ] **Step 3: Append to `runs.py`**

```python
# -- runs whose process is gone ---------------------------------------------------------

HEARTBEAT_SECONDS = 10
HEARTBEAT_TTL_SECONDS = 30


def _redis() -> Any:
    """Reflex's Redis client when one is configured, else ``None``."""
    try:
        from reflex.utils import prerequisites

        return prerequisites.get_redis()
    except Exception:  # noqa: BLE001
        return None


def _heartbeat_key(boot: str) -> str:
    return f"lex:ui:boot:{boot}"


async def alive(boot: str) -> bool:
    """Whether the backend process ``boot`` is still running."""
    if boot == BOOT:
        return True
    redis = _redis()
    if redis is None:
        return False          # one process holds every tab: another boot id is a process that ended
    try:
        return bool(await redis.exists(_heartbeat_key(boot)))
    except Exception:  # noqa: BLE001 - Redis unreachable: do not clear what may still run
        return True


async def sweep(s: Any, alive: Any = None) -> int:
    """Clear the marks of runs whose process is gone; returns how many. Call under the lock."""
    check = alive or globals()["alive"]
    cleared = 0
    for entry, boot in list(dict(s._ui_boots).items()):
        if boot == BOOT or await check(boot):
            continue
        key, _, arg = entry.rpartition("|")
        mark_finished(s, key, arg)
        cleared += 1
    return cleared


import contextlib  # noqa: E402


@contextlib.asynccontextmanager
async def heartbeat(app: Any):
    """A lifespan task: while the backend runs, keep this process's heartbeat in Redis."""
    import asyncio

    async def beat() -> None:
        while True:
            redis = _redis()
            if redis is not None:
                try:
                    await redis.set(_heartbeat_key(BOOT), "1", ex=HEARTBEAT_TTL_SECONDS)
                except Exception:  # noqa: BLE001 - a missed beat only delays a sweep
                    pass
            await asyncio.sleep(HEARTBEAT_SECONDS)

    task = asyncio.ensure_future(beat())
    try:
        yield
    finally:
        task.cancel()
```

Move the `import contextlib` line to the module's import block at the top (it is shown here only to keep the snippet self-contained).

- [ ] **Step 4: Sweep on every page open, and start the heartbeat**

In `state.py`, in `ui_open`, directly after `self.ui_error = {}` add:

```python
        from lex.lex_app.reflex.ui import runs

        await runs.sweep(self)
```

In `app.py`, in `create_app`, after `devproxy.install(app)` add:

```python
    from lex.lex_app.reflex.ui import runs as ui_runs

    app.register_lifespan_task(ui_runs.heartbeat)
```

- [ ] **Step 5: Run to verify it passes**

Run: `RUN lex/test_project/tests/init/test_1ck_ui_stale_runs.py`, then `RUN lex/test_project/tests/init/test_1cj_ui_load.py`
Expected: 2 passed, then 5 passed.

- [ ] **Step 6: Commit**

```bash
git add lex/lex_app/reflex/ui/runs.py lex/lex_app/reflex/ui/state.py lex/lex_app/reflex/app.py lex/test_project/tests/init/test_1ck_ui_stale_runs.py
git commit -m "feat(reflex): lex.ui clears runs whose backend process is gone

Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>"
```

---

### Task 10: `@ui.custom` and the `lex reflex check` findings

**Files:**
- Modify: `lex/lex_app/reflex/ui/handlers.py` (append `custom`), `lex/lex_app/reflex/scaffold.py` (`run_check`)
- Create: `lex/lex_app/reflex/ui/checks.py`
- Test: `lex/test_project/tests/init/test_1cl_ui_custom_and_check.py`

**Interfaces:**
- Produces: `handlers.custom(fn=None, **event_kwargs)`; `checks.Finding(level, path, line, message)` with `level` in `("error", "warning", "note")`; `checks.find(paths: list[str]) -> list[Finding]`; `checks.report(findings) -> int` (prints; returns 1 if any error).

- [ ] **Step 1: Write the failing tests**

```python
"""lex.ui: the custom region is marked, and the check lists what leaves the default path.

Intent: raw Reflex stays possible, but only where it is marked -- @ui.custom -- so a reviewer
sees every place a page leaves lex-app's vocabulary. `lex reflex check` fails on an unmarked raw
handler on a ui.State, lists the marked ones and every requires=ui.signed_in, and warns about a
handler that names a model manager without declaring what it requires. A regression is raw
Reflex creeping back into project code unnoticed.

Cluster 1cl -- scenarios 1.781-1.782. Type: U.
Covers: lex/lex_app/reflex/ui/handlers.py (custom), lex/lex_app/reflex/ui/checks.py,
lex/lex_app/reflex/scaffold.py (run_check).
Run: python -m lex pytest lex/test_project/tests/init/test_1cl_ui_custom_and_check.py -v
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import pytest

from lex.lex_app.reflex import scaffold
from lex.lex_app.reflex.ui import checks, handlers
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init

_SOURCE = '''
import reflex as rx
from lex import ui


class Tickets(ui.State):
    rows: list[dict] = []

    @ui.action
    def approve(self, code: str):
        self.api.update("ticket", code, {"status": "approved"})

    @rx.event
    def raw(self):
        pass

    @ui.custom
    async def upload(self, files: list[rx.UploadFile]):
        pass

    @ui.action(requires=ui.signed_in)
    def anyone(self):
        pass

    @ui.job
    def totals(self):
        self.rows = list(Ticket.objects.values("id"))

    @ui.load(requires=ui.read(Ticket))
    def open(self):
        self.rows = list(Ticket.objects.values("id"))


class Plain(rx.State):
    @rx.event
    def fine(self):
        pass
'''


class TestCluster01cl_CustomAndCheck(TestCase):
    """1.781-1.782."""

    # -- 1.781 ---------------------------------------------------------
    def test_1_781_ui_custom_is_a_marked_reflex_handler(self) -> None:
        """
        Scenario 1.781: the custom region.
        Given: an async handler under @ui.custom on a ui.State.
        When:  the class is created.
        Then:  it is an ordinary Reflex event handler -- async allowed, background on request --
               that carries the custom mark.
        """
        class Uploads(State):
            @handlers.custom
            async def take(self):
                pass

            @handlers.custom(background=True)
            async def stream(self):
                pass

        self.assertEqual(Uploads.take.fn.__ui_kind__, "custom")
        self.assertFalse(Uploads.take.is_background)
        self.assertTrue(Uploads.stream.is_background)

    # -- 1.782 ---------------------------------------------------------
    def test_1_782_the_check_fails_on_unmarked_raw_handlers_and_lists_the_rest(self) -> None:
        """
        Scenario 1.782: lex reflex check.
        Given: a project file with a ui.State holding a raw @rx.event, a @ui.custom, a
               requires=ui.signed_in, a handler naming a model manager without requires=, one
               with requires=, and a plain rx.State.
        When:  the findings are collected and reported, and run_check runs over the project.
        Then:  the raw handler is an error; the custom handler and the signed_in requirement
               are notes; the unguarded manager is a warning; the guarded one and the plain
               state raise nothing; the report returns 1, and run_check returns non-zero even
               when RFDS's own checks pass.
        """
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "_reflex_structure.py"
            path.write_text(_SOURCE)
            found = checks.find([str(path)])
            by_level = {level: sorted(f.message.split(":")[0] for f in found if f.level == level)
                        for level in ("error", "warning", "note")}
            self.assertEqual(by_level["error"], ["Tickets.raw"], "an unmarked raw handler fails the check")
            self.assertEqual(by_level["warning"], ["Tickets.totals"], "a model manager without requires= is flagged")
            self.assertEqual(by_level["note"], ["Tickets.anyone", "Tickets.upload"])
            self.assertTrue(all(f.line > 0 and f.path == str(path) for f in found), "each finding has its place")
            self.assertEqual(checks.report(found), 1)
            self.assertEqual(checks.report([f for f in found if f.level != "error"]), 0, "notes and warnings pass")
            with patch("rfds.cli.main", return_value=0):
                self.assertNotEqual(scaffold.run_check(Path(folder)), 0, "the lex.ui error fails lex reflex check")
```

- [ ] **Step 2: Run to verify it fails**

Run: `RUN lex/test_project/tests/init/test_1cl_ui_custom_and_check.py`
Expected: `ImportError: cannot import name 'checks'`.

- [ ] **Step 3: Append `custom` to `handlers.py`**

```python
def custom(fn: Callable[..., Any] | None = None, **event_kwargs: Any) -> Any:
    """A raw Reflex handler on a ``ui.State``: the custom region, marked so reviews can see it.

    It behaves exactly like ``@rx.event`` (``background=True`` and the rest are passed on).
    ``lex reflex check`` lists every one; an unmarked ``@rx.event`` on a ``ui.State`` fails it.
    """

    def decorate(fn: Callable[..., Any]) -> Any:
        fn.__ui_kind__ = "custom"
        fn.__ui_key__ = fn.__qualname__
        return rx.event(fn, **event_kwargs) if event_kwargs else rx.event(fn)

    return decorate(fn) if fn is not None else decorate
```

- [ ] **Step 4: Write `checks.py`**

```python
"""What ``lex reflex check`` reports about lex.ui: handlers that leave the default path."""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

_UI_DECORATORS = {"action", "job", "load"}


@dataclasses.dataclass(frozen=True)
class Finding:
    level: str          # "error", "warning" or "note"
    path: str
    line: int
    message: str


def _files(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for item in paths:
        path = Path(item)
        out.extend(sorted(path.rglob("*.py")) if path.is_dir() else [path])
    return out


def _dotted(node: ast.AST) -> str:
    """``ui.State`` / ``rx.event`` for an attribute chain, ``State`` for a name, '' otherwise."""
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr}" if head else ""
    return node.id if isinstance(node, ast.Name) else ""


def _decorator(node: ast.AST) -> tuple[str, list[ast.keyword]]:
    """A decorator's dotted name and keywords, called or not."""
    if isinstance(node, ast.Call):
        return _dotted(node.func), list(node.keywords)
    return _dotted(node), []


def find(paths: list[str]) -> list[Finding]:
    """The lex.ui findings in the project files under ``paths``."""
    findings: list[Finding] = []
    for path in _files(paths):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        ui_states: set[str] = set()
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            bases = {_dotted(base) for base in cls.bases}
            if not (("ui.State" in bases) or (bases & ui_states)):
                continue
            ui_states.add(cls.name)
            for fn in [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                name = f"{cls.name}.{fn.name}"
                for node in fn.decorator_list:
                    dotted, keywords = _decorator(node)
                    if dotted in ("rx.event", "reflex.event"):
                        findings.append(Finding("error", str(path), fn.lineno,
                                                f"{name}: a raw @rx.event on a ui.State. Use @ui.action, @ui.job or "
                                                "@ui.load, or mark it @ui.custom."))
                    elif dotted == "ui.custom":
                        findings.append(Finding("note", str(path), fn.lineno, f"{name}: custom region (@ui.custom)."))
                    elif dotted.startswith("ui.") and dotted[3:] in _UI_DECORATORS:
                        requires = next((k.value for k in keywords if k.arg == "requires"), None)
                        if requires is None:
                            uses_manager = any(isinstance(n, ast.Attribute) and n.attr in ("objects", "_default_manager")
                                               for n in ast.walk(fn))
                            if uses_manager:
                                findings.append(Finding("warning", str(path), fn.lineno,
                                                        f"{name}: names a model manager without requires=. The query "
                                                        "will be refused at run time; use self.api or declare requires=."))
                        elif "ui.signed_in" in {_dotted(n) for n in ast.walk(requires)}:
                            findings.append(Finding("note", str(path), fn.lineno,
                                                    f"{name}: requires=ui.signed_in (any signed-in user)."))
    return findings


def report(findings: list[Finding]) -> int:
    """Print the findings; 1 when any is an error, else 0."""
    for finding in sorted(findings, key=lambda f: (f.path, f.line)):
        print(f"[{finding.level:<7}] {finding.path}:{finding.line}  {finding.message}")
    errors = sum(1 for finding in findings if finding.level == "error")
    print(f"lex.ui check: {errors} error(s), {sum(f.level == 'warning' for f in findings)} warning(s), "
          f"{sum(f.level == 'note' for f in findings)} listed for review")
    return 1 if errors else 0
```

- [ ] **Step 5: Call it from `run_check` in `scaffold.py`**

Replace `run_check` with:

```python
def run_check(project_root: Path) -> int:
    """lex.ui's findings, RFDS's ``lint --strict`` over the dashboards, then ``doctor``; 0 = clean."""
    from rfds.cli import main as rfds_main

    from lex.lex_app.reflex.ui import checks

    paths = check_paths(project_root)
    status = 0
    if paths:
        status = checks.report(checks.find(paths)) or status
        status = rfds_main(["lint", "--strict", *paths]) or status
    return rfds_main(["doctor", "--app-root", str(project_root)]) or status
```

- [ ] **Step 6: Run to verify it passes**

Run: `RUN lex/test_project/tests/init/test_1cl_ui_custom_and_check.py`, then `RUN lex/test_project/tests/init/test_1bw_reflex_developer_tooling.py`
Expected: 2 passed, then the existing batch unchanged.

- [ ] **Step 7: Commit**

```bash
git add lex/lex_app/reflex/ui/handlers.py lex/lex_app/reflex/ui/checks.py lex/lex_app/reflex/scaffold.py lex/test_project/tests/init/test_1cl_ui_custom_and_check.py
git commit -m "feat(reflex): @ui.custom and the lex.ui findings of lex reflex check

Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>"
```

---

### Task 11: The `lex.ui` namespace, the templates and the reference example

**Files:**
- Create: `lex/ui.py`
- Modify: `lex/lex_app/reflex/scaffold.py` (templates `_OBJECT_PAGE`, `_OVERVIEW`), `lex/lex_app/reflex/examples/_reflex_structure.py`, `docs/superpowers/specs/2026-11-23-reflex-ui-action-state-layer-design.md`
- Test: `lex/test_project/tests/init/test_1cm_ui_namespace_and_examples.py`

**Interfaces:**
- Produces: `lex.ui` exporting `State, load, action, job, custom, goto, toast, download, progress, Refuse, ApiError, read, create, edit, delete, permission, signed_in, running, failed, progress_of, cancel, testing`.

- [ ] **Step 1: Write the failing tests**

```python
"""lex.ui: one import for everything, and examples written in the new style.

Intent: a project writes `from lex import ui` and finds every name the layer has; lex-app's own
scaffold templates and reference example use it, because consultants and AI copy what they see.
A regression is a template that teaches the old plumbing again.

Cluster 1cm -- scenarios 1.783-1.785. Type: U.
Covers: lex/ui.py, lex/lex_app/reflex/scaffold.py (templates),
lex/lex_app/reflex/examples/_reflex_structure.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1cm_ui_namespace_and_examples.py -v
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest import TestCase

import pytest

from lex import ui
from lex.lex_app.reflex import scaffold
from lex.lex_app.reflex.data import LexApiError
from lex.lex_app.reflex.ui import checks, guard, handlers, results, runs, state, testing

pytestmark = pytest.mark.init

_EXAMPLE = Path(scaffold.__file__).parent / "examples" / "_reflex_structure.py"

#: RFDS's own async hooks (not lex.ui handlers), which keep RFDS's contract.
_RFDS_HOOKS = {"_export_rows"}


def _plumbing(source: str) -> list[str]:
    """Old-style plumbing in handler code: what the layer exists to remove."""
    found = []
    tree = ast.parse(source)
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        if fn.name in _RFDS_HOOKS:
            continue
        if isinstance(fn, ast.AsyncFunctionDef):
            found.append(f"async def {fn.name}")
        for node in ast.walk(fn):
            if isinstance(node, ast.Await):
                found.append(f"await in {fn.name}")
            if isinstance(node, ast.Name) and node.id in ("run_orm", "lex_api"):
                found.append(f"{node.id} in {fn.name}")
    return sorted(set(found))


class TestCluster01cm_Namespace(TestCase):
    """1.783-1.785."""

    # -- 1.783 ---------------------------------------------------------
    def test_1_783_from_lex_import_ui_has_every_name(self) -> None:
        """
        Scenario 1.783: the public namespace.
        Given: `from lex import ui`.
        When:  its names are compared with the implementation.
        Then:  each public name is the implementation's own object, ui.ApiError is lex-app's
               API error, and ui.testing is the test helper module.
        """
        expected = {
            "State": state.State, "load": handlers.load, "action": handlers.action, "job": handlers.job,
            "custom": handlers.custom, "goto": results.goto, "toast": results.toast, "download": results.download,
            "progress": results.progress, "Refuse": results.Refuse, "ApiError": LexApiError,
            "read": guard.read, "create": guard.create, "edit": guard.edit, "delete": guard.delete,
            "permission": guard.permission, "signed_in": guard.signed_in, "running": runs.running,
            "failed": runs.failed, "progress_of": runs.progress_of, "cancel": runs.cancel, "testing": testing,
        }
        for name, obj in expected.items():
            self.assertIs(getattr(ui, name), obj, f"ui.{name}")
        self.assertEqual(sorted(ui.__all__), sorted(expected), "nothing else is public")

    # -- 1.784 ---------------------------------------------------------
    def test_1_784_the_scaffold_templates_are_written_on_the_layer(self) -> None:
        """
        Scenario 1.784: what `lex reflex add` writes.
        Given: the three templates, rendered for a model.
        When:  their handler code is inspected and checked.
        Then:  none contains async, await, run_orm or lex_api; the object page and the overview
               use ui.State with @ui.load; the lex.ui check finds no error in them.
        """
        for block in ("list_report", "object_page", "overview"):
            _, source = scaffold.render(block, "Ticket", package="demo")
            with self.subTest(block=block):
                self.assertEqual(_plumbing(source), [], f"{block} must not teach the old plumbing")
                if block != "list_report":
                    self.assertIn("ui.State", source)
                    self.assertIn("@ui.load", source)

    # -- 1.785 ---------------------------------------------------------
    def test_1_785_the_reference_example_is_written_on_the_layer(self) -> None:
        """
        Scenario 1.785: the example consultants copy.
        Given: lex-app's reference _reflex_structure.py.
        When:  its handler code is inspected and checked.
        Then:  outside RFDS's own async export hook it contains no async, await, run_orm or
               lex_api; every state with handlers is a ui.State; the lex.ui check reports no
               error.
        """
        source = _EXAMPLE.read_text(encoding="utf-8")
        self.assertEqual(_plumbing(source), [], "the example must not teach the old plumbing")
        self.assertEqual([f.message for f in checks.find([str(_EXAMPLE)]) if f.level == "error"], [])
        self.assertIn("from lex import ui", source)
```

`scaffold.render(block, model, *, route=None, title=None, nav="Dashboards", package="your_repo")` returns `(module name, source)`; it is what `lex reflex add` calls.

- [ ] **Step 2: Run to verify it fails**

Run: `RUN lex/test_project/tests/init/test_1cm_ui_namespace_and_examples.py`
Expected: `ImportError: cannot import name 'ui' from 'lex'`.

- [ ] **Step 3: Write `lex/ui.py`**

```python
"""lex.ui: plain synchronous handlers for Reflex dashboards.

    from lex import ui

    class Tickets(ui.State):
        rows: list[dict] = []

        @ui.load
        def open(self):
            self.rows = self.api.list(Ticket)

        @ui.action
        def approve(self, ticket_id: int):
            self.api.update(Ticket, ticket_id, {"status": "approved"})

        @ui.job
        def export(self):
            for index, part in enumerate(parts(self.user)):
                yield ui.progress(index / 10, "Building")

==========================  =====================================================
``ui.State``                base class of a page state: ``self.user``, ``self.api``,
                            ``self.params``, ``self.can()``, ``self.state()``
``@ui.load``                fills the page when it opens
``@ui.action``              a short handler, under about a second
``@ui.job``                 long work with progress; the user keeps working
``@ui.custom``              a raw Reflex handler, marked for review
``ui.goto/toast/download``  what a handler returns
``ui.progress``             what a job yields
``ui.Refuse``               an expected refusal, shown in its own words
``requires=``               ``ui.read/create/edit/delete(Model)``, ``ui.permission()``,
                            ``ui.signed_in`` -- needed to query the database directly
``ui.running/failed/        a handler's status, for views; ``ui.cancel`` is the
progress_of/cancel``        event of a Cancel button
``ui.testing``              run a handler in a unit test
==========================  =====================================================

Import this module only in the Reflex process: it defines Reflex states.
"""

from lex.lex_app.reflex.data import LexApiError as ApiError
from lex.lex_app.reflex.ui import testing
from lex.lex_app.reflex.ui.guard import create, delete, edit, permission, read, signed_in
from lex.lex_app.reflex.ui.handlers import action, custom, job, load
from lex.lex_app.reflex.ui.results import Refuse, download, goto, progress, toast
from lex.lex_app.reflex.ui.runs import cancel, failed, progress_of, running
from lex.lex_app.reflex.ui.state import State

__all__ = [
    "ApiError", "Refuse", "State", "action", "cancel", "create", "custom", "delete", "download", "edit",
    "failed", "goto", "job", "load", "permission", "progress", "progress_of", "read", "running",
    "signed_in", "testing", "toast",
]
```

- [ ] **Step 4: Convert the two templates in `scaffold.py`**

Replace `_OBJECT_PAGE` with:

```python
_OBJECT_PAGE = _HEADER + '''import reflex as rx
import rfds
from django.apps import apps

from lex import ui
from lex.lex_app.reflex import lex_view
from lex.lex_app.settings import repo_name


def _model():
    return apps.get_model(repo_name, "${model}")


class ${state}(ui.State):
    """The ${model} the URL names, read through lex-app's API as the user."""

    pk: str = ""
    record: dict = {}

    @ui.load(optional=True)
    def open(self):
        self.pk = str(self.params.get("pk", ""))
        if not self.pk:
            raise ui.Refuse("This record does not exist.")
        # A record the user may not see, or that is gone, shows itself as a message in place.
        self.record = self.api.get(_model(), self.pk)


def ${func}() -> rx.Component:
    """The record dashboard. Return it from the model's hook::

        @classmethod
        def reflex_main(cls):
            from ${package}._dashboards.${module} import ${func}
            return ${func}()
    """
    problem = ui.failed(${state}.open)
    return rfds.page(
        rfds.page_header(title="${model} " + ${state}.pk, path="/${model_lower}"),
        rx.cond(
            problem != "",
            rfds.inline_message(problem, tone="warning"),
            rx.cond(
                ui.running(${state}.open),
                rfds.spinner(label="Loading"),
                # lex-app's own record page: its fields, actions and history, as the user may see them.
                lex_view(f"${model_lower}/{${state}.pk}/show", height=600),
            ),
        ),
        on_mount=${state}.open,
    )
'''
```

Replace `_OVERVIEW` with:

```python
_OVERVIEW = _HEADER + '''import rfds
from django.apps import apps

from lex import ui
from lex.lex_app.reflex import lex_page, lex_table
from lex.lex_app.settings import repo_name

ROUTE = "${route}"


def _model():
    return apps.get_model(repo_name, "${model}")


class ${state}(ui.State):
    """The overview's figures, computed in the database."""

    total: int = 0

    # Counting rows reads the database directly, so the load says what it requires.
    @ui.load(requires=ui.permission(f"{repo_name}.${model}", "read"))
    def open(self):
        self.total = _model()._default_manager.count()


@lex_page(ROUTE, title="${title}", nav="${nav}", requires=f"{repo_name}.${model}:read",
          on_load=${state}.open)
def ${func}():
    """KPIs over ${model}, then its records."""
    return rfds.page(
        rfds.page_header(title="${title}", path=ROUTE),
        rfds.grid(
            rfds.kpi_tile(label="${model} records", value=${state}.total, kind="count"),
            columns=3,
        ),
        lex_table(_model()),
    )
'''
```

- [ ] **Step 5: Convert the reference example's handlers**

In `lex/lex_app/reflex/examples/_reflex_structure.py`:

1. Add `from lex import ui` to the imports and remove `lex_api`, `run_orm`, `lex_event`, `current_permissions`, `LexApiError`, `LexRecordState` and `has_permission` from the `lex.lex_app.reflex` import if nothing else in the file uses them (search before removing each).
2. Delete `_may_read_featured`.
3. Replace `OverviewState` with:

```python
class OverviewState(ExportMixin, ui.State):
    """The overview's figures, per viewer; the counts are also its CSV export."""

    export_entity: ClassVar[str] = "row-counts"
    counts: list[dict] = []
    total: int = 0
    readable: list[str] = []

    # Counting rows reads the database directly, so the load says what it requires: here, read
    # access to the featured model. Without the grant the body never runs.
    @ui.load(requires=ui.signed_in)
    def open(self):
        """Read the counts and which models the viewer may read -- per viewer, when the page opens."""
        if not self.can(featured(), "read"):
            raise ui.Refuse("You may not read the featured model, so no figures are shown.")
        self.counts = _row_counts()
        self.total = sum(int(row["rows"]) for row in self.counts)
        self.readable = sorted(model.__name__ for model in _project_models() if self.can(model, "read"))

    async def _export_rows(self, kind: str):
        # RFDS's ExportMixin calls this hook and awaits it: it is RFDS's contract, not a lex.ui handler.
        from lex.lex_app.reflex import run_orm

        rows = await run_orm(_row_counts)
        return ["model", "rows"], rows, {"user": "", "entity": "row counts"}
```

   `ui.signed_in` with an explicit `self.can` check is used because the featured model is only known once Django has loaded the project's models, after this class is defined. Wherever the page referenced `OverviewState.load`, use `OverviewState.open`; wherever it showed `OverviewState.resources`, show `OverviewState.readable` and change its caption to "Models you may read". If `main()` triggers the load with `on_mount=OverviewState.load`, change it to `on_mount=OverviewState.open`.
4. In `InLexAppState` and `CalculationState`: change the base class to `ui.State` and each `@rx.event` to `@ui.action`.
5. Replace `EditState` with:

```python
class EditState(ui.State):
    """One field of one record, changed as the user -- lex-app validates, audits and recalculates."""

    pk: str = ""
    field_name: str = ""
    value: str = ""
    outcome: str = ""

    @ui.action
    def set_pk(self, value: str):
        self.pk = value

    @ui.action
    def set_field_name(self, value: str):
        self.field_name = value

    @ui.action
    def set_value(self, value: str):
        self.value = value

    @ui.action
    def save(self):
        if not (self.pk and self.field_name):
            raise ui.Refuse("Name a record and a field.")
        # A refusal by lex-app (not allowed, or a validation error) is shown to the user as it is.
        self.api.update(featured(), self.pk, {self.field_name: self.value})
        self.outcome = "Saved."
```

6. Replace `RecordState` and the record dashboard's body with the `ui.State` form:

```python
class RecordState(ui.State):
    """The record ``?model=&pk=`` names, read through lex-app's API as the user."""

    pk: str = ""
    record: dict = {}

    @ui.load(optional=True)
    def open(self):
        self.pk = str(self.params.get("pk", ""))
        if not self.pk:
            raise ui.Refuse("This record does not exist.")
        self.record = self.api.get(featured(), self.pk)
```

   and in the function that draws the record dashboard, replace the `rx.match(RecordState.record_state, ...)` block and `on_mount=RecordState.load` with:

```python
    problem = ui.failed(RecordState.open)
    return rx.box(
        rx.cond(
            problem != "",
            rfds.inline_message(problem, tone="warning"),
            rx.cond(
                ui.running(RecordState.open),
                rfds.spinner(label="Loading"),
                rx.vstack(
                    rx.heading("Record " + RecordState.pk, size="5"),
                    rx.text("Read through lex-app's API as you."),
                ),
            ),
        ),
        on_mount=RecordState.open,
    )
```

7. Update the module docstring's table rows that name `run_orm`, `lex_api` and `LexRecordState` to name `@ui.load`, `self.api` and `ui.State` instead.

- [ ] **Step 6: Write the clarifications into the spec**

In `docs/superpowers/specs/2026-11-23-reflex-ui-action-state-layer-design.md`:

- In section 6.4, under **`@ui.load`**, replace the first bullet with: "Bound to a page with `lex_page(..., on_load=State.open)`, which accepts one handler or a list. `lex_page` does not list the load in Reflex's `on_load` itself: Reflex starts background handlers at once, ahead of the sign-in and grant events, so `lex_page` appends an opener event that starts the page's loads after them. A load can also be started with a component's `on_mount`."
- In section 6.4, under **`@ui.job`**, add the bullet: "A job sees its own state's fields; fields of a parent or another state are reached with `self.state(Other)`."
- In section 6.8, replace the row `` `async for _ in self.other():` | `yield from self.other()` `` with `` `async for _ in self.other():` | `yield from self._other()` — shared logic lives in underscore methods, because Reflex turns every public method into an event handler ``.
- In section 6.6, replace "`api=` takes a stand-in; by default calls go to the project's real API in-process, through the client seam `LexApi` already has." with "`api=` takes a stand-in for `self.api`. A handler that uses `self.api` needs one: test tokens are not Keycloak-signed, so the real API cannot answer in a unit test."
- In section 6.5's table, replace the `@ui.load` row's text with "The page `/ui-error`, with the reference and a "Try again" that reopens the page; "not permitted" shows the same page as an access-denied message".
- In section 7, delete the row "A page's `on_load` running background handlers".
- In section 9, item 2, append: "RFDS's own async hooks, such as `ExportMixin._export_rows`, are excepted."
- In section 6.4's status table, change the `ui.running(State.handler, row["id"])` row's meaning to "True only for the call whose first argument is that value, compared as text".

- [ ] **Step 7: Run to verify it passes**

Run: `RUN lex/test_project/tests/init/test_1cm_ui_namespace_and_examples.py`
Expected: 3 passed.

- [ ] **Step 8: Re-run the batches that compile the example and the templates**

Run each, one at a time: `RUN lex/test_project/tests/init/test_1bw_reflex_developer_tooling.py`, `RUN lex/test_project/tests/init/test_1aq_reflex_dashboards.py`, `RUN lex/test_project/tests/init/test_1bt_reflex_design_system.py`, `RUN lex/test_project/tests/init/test_1by_reflex_without_enterprise.py`
Expected: all pass. Where a test asserts the old template or example text (`LexRecordState`, `run_orm`, `OverviewState.load`, the page list), update the expectation to the new text and keep the scenario's intent. In `test_1by`, add `"ui"` handling only if its probe lists modules explicitly: add `importlib.import_module("lex.ui")` to the probe so the no-Enterprise check covers the new package.

- [ ] **Step 9: Commit**

```bash
git add lex/ui.py lex/lex_app/reflex/scaffold.py lex/lex_app/reflex/examples/_reflex_structure.py docs/superpowers/specs/2026-11-23-reflex-ui-action-state-layer-design.md lex/test_project/tests/init
git commit -m "feat(reflex): the lex.ui namespace; templates and the reference example on the layer

Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>"
```

---

### Task 12: Test-plan shards, the full Reflex suite, and the pull request

**Files:**
- Modify: `lex/test_project/test-plan/clusters/01-init/allocation.yaml`, `batches.md`, `cluster.md`; `lex/test_project/test-plan/progress/dashboard.md` (generated)
- Create: `lex/test_project/test-plan/progress/sessions/<date>-1cc-1cm-lex-ui-layer.md`

- [ ] **Step 1: Confirm the letters and scenarios are still free**

```bash
git fetch -q git@github.com:ExcellenceCloudGmbH/lex-app.git '+refs/heads/*:refs/remotes/gh/*'
F=lex/test_project/test-plan/clusters/01-init/allocation.yaml
for r in $(git for-each-ref --format='%(refname)' refs/remotes/gh); do
  git show $r:$F 2>/dev/null | awk -v r=$r '/^max_scenario:/{m=$2} /^  c[c-m]:/{c=c" "$1} END{if(m>746||c) print r, m, c}'
done
```

Expected: only this branch's base (`feat/reflex-lex-look`, 746). If another branch holds a letter or a higher scenario, renumber this plan's batches and tests before continuing, and say so in the PR.

- [ ] **Step 2: Add the eleven batches to the shards**

In `allocation.yaml`: set `max_scenario: 785` and append one entry per batch, in this shape (title and note from the table below, `status: complete`, `tests.pass` = the count, `skip: 0`, `xfail: 0`):

| Letter | Title | Scenarios | Type | Tests | Test file |
| --- | --- | --- | --- | --- | --- |
| cc | lex.ui results and thread pools | 1.747-1.749 | U | 3 | `test_1cc_ui_results_and_pools.py` |
| cd | lex.ui guard: requires values, the query block, the acting user | 1.750-1.752 | I | 3 | `test_1cd_ui_guard.py` |
| ce | lex.ui working copy: snapshot, restore, draft, merge | 1.753-1.755 | U | 3 | `test_1ce_ui_working_copy.py` |
| cf | lex.ui context: user, params, grants, the API as the user | 1.756-1.758 | U | 3 | `test_1cf_ui_context.py` |
| cg | ui.State and handler status | 1.759-1.761 | U | 3 | `test_1cg_ui_state_and_status.py` |
| ch | @ui.action: plain synchronous handlers and their failures | 1.762-1.767 | I | 6 | `test_1ch_ui_action.py` |
| ci | @ui.job: long work on a working copy | 1.768-1.773 | I | 6 | `test_1ci_ui_job.py` |
| cj | @ui.load: page loads, the opener, the error page | 1.774-1.778 | I | 5 | `test_1cj_ui_load.py` |
| ck | lex.ui clears runs whose process is gone | 1.779-1.780 | U | 2 | `test_1ck_ui_stale_runs.py` |
| cl | @ui.custom and the lex.ui findings of lex reflex check | 1.781-1.782 | U | 2 | `test_1cl_ui_custom_and_check.py` |
| cm | The lex.ui namespace; templates and the reference example on the layer | 1.783-1.785 | U | 3 | `test_1cm_ui_namespace_and_examples.py` |

Each entry's `note` says, in two or three sentences: what the module does, the decision it implements (spec section), and what the tests would catch. In `batches.md`, append one block per batch in the shape of batch `1cb` (Property table: scenario range, type, files covered, test file, test classes, fixtures, tests landed, status; then the Scenario | Title | Asserts table with one row per scenario, taken from each test's docstring). In `cluster.md`, append one section per batch in the shape of `1cb` ("What it tests", "Why a regression matters", the scenario table, the closing line).

- [ ] **Step 3: Add the session fragment**

`lex/test_project/test-plan/progress/sessions/<today>-1cc-1cm-lex-ui-layer.md`:

```markdown
---
date: <today, YYYY-MM-DD>
clusters: [1]
tests_added: 39
suite_tally: "<fill from step 5: every Reflex batch, N pass / 0 fail, M subtests pass>"
---

# The lex.ui action and state layer (1cc-1cm)

Reflex handlers are written as plain synchronous functions on a `ui.State`, under `@ui.load`,
`@ui.action` and `@ui.job`; lex-app runs the body off the event loop and supplies the user, the API
as that user, permission checks, status, discard on failure and the display of failures. Batches
[1cc to 1cm](../../clusters/01-init/batches.md) cover each module. The scaffold templates and the
reference example are written on the layer. Design:
`docs/superpowers/specs/2026-11-23-reflex-ui-action-state-layer-design.md`.
```

- [ ] **Step 4: Regenerate the dashboard**

Run: `.venv-oss/bin/python .github/scripts/test_plan_aggregates.py build && .venv-oss/bin/python .github/scripts/test_plan_aggregates.py check`
Expected: `wrote ... dashboard.md`, then `OK`.

- [ ] **Step 5: Run every Reflex batch together**

```bash
T=lex/test_project/tests
DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False PROJECT_ROOT=$(pwd) \
  .venv-oss/bin/python -m lex pytest $T/init/test_1ap_reflex_cli.py $T/init/test_1aq_reflex_dashboards.py \
  $T/init/test_1b[t-z]_*.py $T/init/test_1c[a-m]_*.py \
  $(ls $T/*/test_10ag_reflex_handoff_audience.py $T/*/test_10d_reflex_search_exclusion.py $T/*/test_4n_reflex_permissions.py) \
  -p no:cacheprovider -q
rm -rf assets
```

Expected: 87 + 39 = 126 passed, 0 failed (subtests on top). Put the real numbers into the session fragment's `suite_tally`, then run step 4 again.

- [ ] **Step 6: The Redis lock expiry (spec section 7)**

If a Redis is reachable (`redis-cli ping` answers, or CI provides one), add one test to `test_1ch_ui_action.py`, scenario 1.786, that runs `Board.sleeps` for longer than a `redis_lock_expiration` set to 300 ms with `REFLEX_REDIS_URL` pointing at it and asserts the failure is reported as an unexpected error whose log mentions the lock; decorate it `@pytest.mark.skipif(not os.getenv("REFLEX_REDIS_URL"), reason="needs Redis")`, raise `max_scenario` to 786 and add the row to batch `ch`. If no Redis is reachable here or in CI, do not write an untested test: record in the PR text that the lock expiry remains read from Reflex's source.

- [ ] **Step 7: Commit and push**

```bash
git add lex/test_project/test-plan
git commit -m "test(plan): lex.ui layer batches 1cc-1cm in the test plan

Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>"
git push git@github.com:ExcellenceCloudGmbH/lex-app.git feat/reflex-ui-actions
```

- [ ] **Step 8: Open the pull request**

```bash
gh pr create --repo ExcellenceCloudGmbH/lex-app --base feat/reflex-lex-look --head feat/reflex-ui-actions \
  --title "feat(reflex): lex.ui - plain synchronous handlers for dashboards" --body-file <a file with the text below>
```

The body states: why (handler plumbing made dashboards unreadable; counts from the spec's section 2.2, without naming the project); what (`ui.State`, the three decorators, `self.api` as the default path, the run-time ORM block, discard on failure, status helpers, `@ui.custom`, the check findings, `ui.testing`); the clarifications from this plan's "Clarifications found while planning"; the tests (batches 1cc-1cm, the full Reflex tally); what is not in it (components, design checks, the calculation page, Celery jobs, uploads); and whether the Redis lock expiry was tested. It ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Write every PR reference as a full link, never a bare `#123`.

After opening it, read its status with the `ccd_pr` tools; do not poll CI.

---

### Task 13: Proof on real code, and the docs page

Nothing in this task is committed to lex-app. Customer data and project names stay on this machine.

- [ ] **Step 1: Live proof — convert a project that is already on the stack**

In the local example project that runs on this stack (its `_reflex_structure.py`), first copy the file to `.reflex_demo/_reflex_structure.before-ui.py`, then convert every state with handlers to `ui.State`: `await run_orm(fn)` becomes the body calling `fn`; `await (await lex_api(self)).update(...)` becomes `self.api.update(...)`; `@lex_event(auth=...)` becomes `requires=`; each plain `@rx.event` setter becomes `@ui.action`; loads use `@ui.load` and are passed to `lex_page(on_load=...)` or `on_mount`.

Verify, one after the other:

```bash
PYTHONPATH=.reflex_demo/shim DATABASE_DEPLOYMENT_TARGET=local venv/bin/python .reflex_demo/compile_check.py
DATABASE_DEPLOYMENT_TARGET=local venv/bin/lex reflex check
wc -l .reflex_demo/_reflex_structure.before-ui.py _reflex_structure.py
```

Expected: `COMPILED OK (CI mode: conformance = error)`; `lex.ui check: 0 error(s)` and RFDS's lint with 0 findings; a lower line count. Record both line counts and the number of `await`, `async def`, `run_orm` and `try:` occurrences before and after (`grep -c`). Then ask the user to click through the pages in their signed-in browser; do not claim the pages work until they have.

- [ ] **Step 2: Side-by-side proof — two files of the reviewed project**

In the scratchpad (never in either repository), write two ports with neutral names: one state with a load and short actions, one job-heavy state, each next to a shortened copy of the original's shape, and a test file that drives both with `ui.testing.run` and `ui.testing.Harness` against stand-ins for the project's logic functions. Run the test file with `.venv-oss/bin/python -m pytest <file> -q` from the lex-app worktree (so `lex.ui` is importable). Record lines and plumbing counts before and after.

- [ ] **Step 3: Report the two proofs**

Add a "Proof" section to the review document (the Claude Doc "lex.ui Action and State Layer Design") with the two before/after tables and what was and was not verified. Use neutral names only.

- [ ] **Step 4: The docs page**

In the `lex-app-docs` repository, on a new branch from its default branch, add a page for the layer beside the existing Reflex dashboards pages (find them with `grep -rl "lex_page" content/`): what `ui.State` and the three decorators are, the rule "under about a second, an action; longer, a job", `self.api` as the default and `requires=` for the ORM, failures, the status helpers, `@ui.custom`, `ui.testing`, and the migration table from the spec's section 6.8. Follow that repository's conventions for front matter and links, verify every code sample against the implementation, then open a PR there and link it from the lex-app PR. lex-app-docs may refuse a second push to an open PR's branch; try once, and if refused, open a follow-up PR.

---

## Self-review notes

- **Spec coverage.** 6.1 → Task 11; 6.2 → Tasks 4, 5; 6.3 → Tasks 2, 4, 6 (1.766); 6.4 → Tasks 6, 7, 8 and status in 5, 9; 6.5 → Tasks 6 (1.764, 1.765), 7 (1.773), 8 (1.777); 6.6 → every task's tests, Task 6's harness, Task 13's proofs; 6.7 → Task 10; 6.8 and the templates → Task 11; section 7's Redis row → Task 12 step 6; section 9's acceptance → Tasks 11, 12, 13.
- **Names used across tasks.** `results.split/to_events/RECORDER/message`; `pools.run/size/shutdown`; `guard.normalise/granted/block_queries/acting_user/Requirement/signed_in`; `workcopy.raw/fields_of/snapshot/restore/reset/Draft/merge/_plain`; `context.Session/Invocation/resolve/set_resolver/enter/leave/current/Api/api_of/Rows/User/Params`; `runs.BOOT/key_of/arg_key/mark_started/mark_progress/mark_finished/claim/release/request_cancel/running/failed/progress_of/cancel/sweep/alive/heartbeat`; `state.State/ERROR_ROUTE/error_page/_page_permitted`; `handlers.action/job/load/custom/page_loads/registered/_drive/_classify/_toast/_denied/_merge/_mark/_check_plain/Outcome/OUTCOMES/SLOW_ACTION_SECONDS/_is_development`; `testing.Harness/Result/run`; `checks.Finding/find/report`.
- **Known soft spots, each with its contract stated in the task:** the exact `Var` constructor for the per-row `running` expression (Task 5), and the status-field suffix in browser updates (Task 6).
