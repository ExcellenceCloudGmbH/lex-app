# The lex.ui action and state layer for Reflex dashboards — design

**Date:** 2026-11-23
**Status:** Design approved section by section in conversation; implemented on `feat/reflex-ui-actions`, and this spec describes what was built.
**Lands as:** a new PR stacked on lex-app #908 (`feat/reflex-lex-look`), branch `feat/reflex-ui-actions`.
**Depends on:** #907 (plain Reflex, sign-in through lex-app) for the signed-in user and token; #903 for `LexApi`.

---

## 1. Summary

Consultants write Reflex event handlers as plain synchronous Python functions on a `ui.State`, decorated
with `@ui.load`, `@ui.action` or `@ui.job`. lex-app supplies everything a handler otherwise writes by
hand: running the body off the event loop, the signed-in user, lex-app's API as that user, permission
checks, busy and progress status, discarding half-finished changes on failure, and showing failures to
the user. The layer is imported as `from lex import ui`.

This is the first of four pieces that together make Reflex dashboards readable and uniform for
consultants and for AI-assisted development (section 3).

---

## 2. Why: what a real project showed

The requirements came from a review of a large customer project whose Reflex code was written with AI
assistance on RFDS. Consultants cannot follow it. The goals stated for this work:

1. lex-app wraps Reflex in foundational components of its own.
2. The code a project writes shrinks as far as possible while Reflex's power stays available.
3. The engineering a handler needs goes away — `await (await lex_api(self)).update(...)` was the named example.
4. The design system defines everything: how things look, where they go, and how (hyperlinks, cards, placement).
5. lex-app functionality such as the calculation model gets a ready-made Reflex page, with choices about how to use it.

### 2.1 What the project contains

| Measure | Value |
| --- | --- |
| Reflex code | about 119,000 lines in 185 files |
| State files | 75 files, 44,000 lines |
| Logic files | 25 files, 37,000 lines |
| View files | 85 files, 38,000 lines |
| Event handlers | 873, of which 222 are background handlers (in 40 files) |
| lex-app Reflex helpers used | `run_orm` only (52 files); the project predates `lex_page`, `lex_table`, `lex_api` |
| Own stylesheet | 2,120 lines, plus 721 inline `style=` / `class_name=` uses |
| Hand-written `rxconfig.py` | 127 lines, plus its own app module |

### 2.2 The finding that set the order of work

The unreadable part is the event handlers, not the component calls. Every handler writes the same
plumbing by hand:

| Hand-written plumbing | Uses | Files |
| --- | --- | --- |
| `async with self` (background-task locking) | 698 | 46 |
| `except Exception` blocks | 595 | 95 |
| Incident reference and logging | 410 | 43 |
| `translate(...)` for error messages | 408 | 52 |
| Running blocking code off the loop (`asyncio.to_thread`, `run_orm`, a project thread pool) | 400 | — |
| `get_state(...)` to reach another state | 281 | 62 |
| Reading who is signed in | 179 | 51 |
| Busy flags claimed and released | 144 | 28 |
| `async for` over the state's own generators | 91 | — |
| "Did the user leave the page meanwhile" counters | 89 | 12 |

One "open this item" button takes about 70 lines of handler: claim a busy flag, read the session, run
in a worker thread, check the user is still on the page, map errors to a toast with an incident
reference, release the flag.

Three further findings:

- **The design system has leaked into project code.** A theme cannot prevent a 2,120-line project
  stylesheet and 721 inline styles; predefined page templates and components with no styling
  arguments can. That is pieces 2 and 3 (section 3).
- **Real async needs are rare** (section 5.3).
- **The project cannot benchmark the calculation page**: it has no `CalculationModel` subclass.

Two caveats recorded so expectations are right: the 37,000 lines of business logic are not reduced by
any wrapper, and part of the unreadability is comment style (paragraph-long docstrings full of rule and
slice identifiers), which needs a writing convention and is not addressed here.

The calculation page (piece 4 below) therefore has to be driven by a project that has calculations.

---

## 3. The four pieces, and why this one is first

| Order | Piece | What it is |
| --- | --- | --- |
| 1 | **Action and state layer (this spec)** | `ui.State` and the three decorators |
| 2 | Page templates and foundational components | lex-app's own page, card, link, table, form components with predefined placement |
| 3 | Design rules as checks | Rules that fail a build when a page departs from the design |
| 4 | The calculation page | A ready-made page wrapping the calculation model, to be brainstormed separately |

The action layer is first because it removes the most code and because the components depend on it:
a lex button or table needs to know what "loading", "failed" and "not permitted" mean, and this layer
defines those.

---

## 4. Decisions

| # | Decision | Chosen | Rejected, and why |
| --- | --- | --- | --- |
| U1 | How closed the lex layer is | **Layered.** lex components, templates and actions are the default; raw Reflex or RFDS is allowed only in a marked custom region that the checks list. | *Closed* (raw Reflex fails CI): every missing component becomes a lex-app change request. *Open* (helpers only): what produced the reviewed project. |
| U2 | Which piece first | **Action and state layer.** | *Components first*: each component would invent its own loading and error handling. *Calculation page first*: it would be built twice. |
| U3 | How a handler body is written | **Plain synchronous Python**, run off the event loop by lex-app. | *Async with helpers*: every handler still carries `async`/`await`. *Both*: two styles in one codebase. |
| U4 | The default data path in a handler | **lex-app's API as the user.** The ORM needs a declared permission. | *ORM everywhere, permissions by convention*: relies on memory. *API only*: aggregates the API has no endpoint for become impossible. |
| U5 | How the layer is built | **A state base class plus decorators.** | *A page class fusing state, route and view*: a new abstraction above Reflex, awkward for shared state; can be added later on top of this. *Declarative pages*: the reviewed project's pages are custom workflows. |
| U6 | The import name | **`from lex import ui`.** | Typing `lex.lex_app.reflex` everywhere is part of the noise being removed. |
| U7 | Handler kinds | **Three: `load`, `action`, `job`**, split by duration. | One decorator: Reflex's lock makes short and long work behave differently for the user (section 5). |
| U8 | A failed handler's changes | **Discarded back to what the browser was last shown.** | Reflex's own behaviour (partial changes stay in the state). |
| U9 | Repeat clicks | **A job ignores a repeat while it runs. A short action cannot** (section 5.3); the lex button will disable itself in the browser. | `once=True` on actions, as first discussed: not implementable inside Reflex. |

---

## 5. Evidence: why synchronous handlers hold up

Read from Reflex 0.9.13's source and confirmed with a throwaway probe that pushed real events through
Reflex's own event processor (its real lock and browser updates). The probe is not part of the
codebase.

### 5.1 Facts about Reflex

1. The browser receives state changes only when a handler yields or finishes. This holds for every
   handler, async included, so it is not a cost of synchronous bodies.
2. Stock Reflex runs a synchronous handler directly on the event loop. While it runs, no other user is
   served.
3. A normal ("foreground") handler holds that user's state lock for its whole duration. The same
   user's other events wait. With the Redis state manager, required for two or more workers, the lock
   expires after 10 seconds by default and the handler fails with `LockExpiredError`; Reflex logs a
   warning after 1 second. Its error message recommends a background handler for long work.
4. A background handler holds the lock only inside `async with self` blocks and sends changes when
   each block exits.
5. Reflex takes the user's lock before it starts any handler, including a background one, and before
   event middleware runs.

Fact 3 on Redis is from source reading only: no Redis was available for the probe.

### 5.2 Probe results

| Test | Result |
| --- | --- |
| Short action changing live state from a worker thread: assignment, list append, dict and nested changes | All applied and sent to the browser, including a computed value depending on the list |
| `yield` in the middle of a short action | First update sent immediately, second 0.4 s later |
| Event loop while a body sleeps 0.5 s | lex-style action: the loop ticked 53 times. Stock Reflex synchronous handler: 4 times. |
| Same user clicks again during a 0.5 s action | Waited the full 0.5 s; another user's click took 0.05 s |
| Reading another state from inside the worker thread | Works |
| 30 users at once, 200 list appends each | Every list ended at exactly 200 |
| Long job while the user keeps working | The user's click returned immediately |
| Long job reading a field the user changed mid-job | Old value until its next `yield`, then the new one |
| The user's change to a field the job did not touch | Survived the merge |
| Job and user change the same field | The job's value won |
| An action that sets a field, then raises | The field kept the new value (stock Reflex behaves the same) |
| Job clicked three times rapidly, then once later | Ran once, ignored two, ran again later |
| Lock-holding action clicked three times rapidly | Ran twice in a row; the repeat could not be detected |

### 5.3 What follows

- Short work can run as a foreground handler with the body in a worker thread on the live state. It
  behaves exactly like a Reflex handler, without blocking other users.
- Long work must not hold the lock. Its body works on a copy of the state, merged at each `yield` and
  at the end. The reviewed project already does this by hand (read under the lock, work outside it,
  write back, check the user is still there).
- A repeat click on a lock-holding action cannot be dropped server-side: the second event is not
  examined until the first has finished (fact 5). A job can ignore repeats because it does not hold
  the lock.
- Real async needs are rare. In the reviewed project: no `asyncio.gather`, no async HTTP client;
  14 polling loops; and 6 chunked-upload handlers, which Reflex requires to be async background
  handlers.

---

## 6. Design

### 6.1 Scope and shape

| Name | Purpose |
| --- | --- |
| `ui.State` | Base class of every page state |
| `@ui.load` | Runs when a page opens |
| `@ui.action` | A short handler, under about a second |
| `@ui.job` | Long work with progress |
| `@ui.custom` | Marks a raw Reflex handler (the custom region) |
| `ui.goto`, `ui.toast`, `ui.download` | What a handler returns to make the browser act |
| `ui.progress` | What a job yields to report progress |
| `ui.Refuse`, `ui.ApiError` | Exceptions |
| `ui.read`, `ui.create`, `ui.edit`, `ui.delete`, `ui.permission`, `ui.signed_in` | Values for `requires=` |
| `ui.running`, `ui.failed`, `ui.progress_of`, `ui.cancel` | For views: status of a handler, and the cancel event |
| `ui.testing` | Test helper for projects: `run`, and the `Harness` it is built on (section 6.6) |

Beside the layer, `lex.lex_app.reflex.record_model(name)` gives the project model a record
dashboard's `?model=` names (case-insensitively), or `None`; the reference example's record
dashboard uses it in its load.

`lex/ui.py` is a thin module that re-exports from `lex/lex_app/reflex/`, where the code lives. It must
only be imported in the Reflex process (it defines Reflex states), like `lex.lex_app.reflex` today.

Coexistence:

- `ui.State` is a real Reflex state, not a mixin (a class whose only state base is a mixin is itself a
  mixin in Reflex; `LexRecordState` hit this). Each project state is a substate of it.
- Existing states, `@rx.event` handlers and every current `lex_*` helper keep working unchanged. A
  project migrates one page at a time.
- The async helpers (`lex_api`, `run_orm`, `current_permissions`, `current_access_token`) stay for
  custom regions; the layer calls them internally.
- Views are unchanged: they read state fields as Reflex vars.

### 6.2 `ui.State`: what is always on `self`

A handler body is a plain function running in a worker thread. Whatever needs async work (the
signed-in user, the token, the grants) is resolved by lex-app, on the event loop, so the body awaits
nothing.

| On `self` | What it gives |
| --- | --- |
| `self.user` | The signed-in user: `name`, `username`, `email`, `id` |
| `self.api` | lex-app's API as that user (section 6.3) |
| `self.params` | The page's route and query values: `self.params.section`, `self.params.get("w")` |
| `self.can(Model, "edit")` | Whether Keycloak grants the user that scope on the model (model-wide grants only, as `has_permission` today) |
| `self.state(Other)` | Another state of the same user, to read or change. In an action it is the live state; in a load or job it is a working copy merged like `self`. |

These five names are reserved. A `ui.State` subclass that declares a field (with or without a
default), a computed var or a method named `user`, `api`, `params`, `can` or `state` is refused
with a `TypeError` when the class is defined. A page route whose dynamic argument has one of these
names (`/users/[user]`) is refused too: Reflex makes a route argument a field of every state, which
it reads before the helper, so `self.user` would be the route's text in every handler. `lex_page`
refuses such a route with a `ValueError`, and `create_app`, once the project's pages are declared,
refuses any page route that has one, including a page added with plain `rx.page`. `lex reflex
check` reports the field and method case as an error without starting the app (6.7). Each message
names the state or the route, the name, and says to rename it. Outside a handler body the helpers
raise a `RuntimeError` saying where they work.

### 6.3 Data access

**The API as the user is the default.**

| Call | Returns |
| --- | --- |
| `self.api.list(Model, filters=None, search=None, ordering=None, page=1, per_page=25, fields=None)` | Plain rows that can be assigned to a state field, with `.count` for paging |
| `self.api.get(Model, pk)` | One row |
| `self.api.create(Model, data)` | The created row |
| `self.api.update(Model, pk, data)` | The updated row |
| `self.api.delete(Model, pk)` | Nothing |

`Model` is a model class or a resource name. Hidden fields, row rules, validation, audit and "edited
by" apply, because the call goes through lex-app's API with the user's token. The token is fetched for
each call, so a long job never uses an expired one. A refusal raises `ui.ApiError` with `.status`,
`.denied` and `.detail`; uncaught, it is shown to the user (section 6.5). It wraps the existing
`LexApi`.

The body runs in a worker thread, so `self.api` and the other helpers that need async work hand their
call to the event loop and wait for it. When that wait times out, the call on the event loop is
cancelled too, so a write that outlived its handler is not duplicated by a retry.

**The ORM needs a declared permission.** Inside a handler without `requires=`, lex-app blocks database
queries on the worker thread for the duration of the body (Django's `execute_wrapper` on every
configured connection). The first query raises an error that names the handler and says to use
`self.api` or to declare what the handler requires.

With `requires=`, the permission is checked before the body runs and the block is lifted:

```python
@ui.action(requires=ui.read(Payment))
def load_totals(self):
    self.totals = list(Payment.objects.values("year").annotate(total=Sum("amount")))
```

| `requires=` value | Meaning |
| --- | --- |
| `ui.read(M)`, `ui.create(M)`, `ui.edit(M)`, `ui.delete(M)` | Keycloak grants that scope on the model |
| `ui.permission(model_or_resource, scope)` | Any other scope or a named resource |
| `ui.signed_in` | Any signed-in user may run the handler's queries; for data with no model permission |
| A list | All must hold |

An empty `requires=` (`[]` or `()`) is refused with a `ValueError` when the handler is declared. The
ORM needs a *declared* permission, so declaring none is a mistake, not the same as leaving
`requires=` out.

When a handler writes through the ORM, lex-app sets the operation context's acting user for that
thread, so `created_by` and `edited_by` name the signed-in user instead of the fallback actor.

This is enforcement at run time, in development and production: an unguarded query cannot run on
the handler's thread. A thread the body starts itself is outside the layer, and the block does not
follow it there. It surfaces the first time the handler runs, not when the app is built; `lex reflex check` flags the
obvious cases earlier (section 6.7). In conversation this was first described as "fails the build";
detecting ORM use by reading code is not reliable, so the run-time block replaces that.

### 6.4 The three decorators

All three take a plain (non-`async`) function; an `async def` under them raises a `TypeError` naming
the handler when the class is created.

| | `@ui.load` | `@ui.action` | `@ui.job` |
| --- | --- | --- | --- |
| For | Filling the page when it opens | A click that finishes in under about a second | Work that takes longer |
| Reflex mechanism | Background handler | Foreground handler | Background handler |
| The user's other clicks meanwhile | Keep working | Wait until it finishes | Keep working |
| What `self` is | A working copy | The live state | A working copy |
| When the browser updates | At each `yield` and at the end | At each `yield` and at the end | At each `yield` and at the end |
| A repeat while it runs | Declared on the page: a newer visit replaces it. Started on mount: a newer run replaces it | Queued, runs afterwards | Ignored |
| The user leaves the page | Declared on the page: result discarded. Started on mount: kept | — | Keeps running; stops with `stop_on_leave=True` |
| Options | `requires=`, `optional=`, `keep=` | `requires=` | `requires=`, `stop_on_leave=` |
| Thread pool | Interactive | Interactive | Jobs |

**A visit** is one opening of a page in one browser tab. `lex_page` starts a new visit on every page
open; jobs, and the loads the page declares, compare the visit they started in with the current
one. A load started on a component's `on_mount` is not tied to the visit (below).

**`@ui.load`**

- Bound to a page with `lex_page(..., on_load=State.open)`, which accepts one handler or a list.
  `lex_page` does not list the load in Reflex's `on_load` itself: Reflex starts background handlers
  at once, ahead of the sign-in and grant events, so `lex_page` appends an opener event that starts
  the page's loads after them.
- A dashboard has no `lex_page`, so it starts its load with a component's `on_mount`. Reflex
  delivers mount events before the page's `on_load` events, and on a navigation with the previous
  page's address, so such a load is tied to its component, not to the page view: it is dropped only
  when a newer run of the same load has started in the tab, it resets its state each time it
  starts, and its failure is shown in place (`ui.failed`), never on the error page, whatever
  `optional=` says. It takes no part in the page's required and optional bookkeeping. Loads that
  fill one state together are therefore declared on the page, or written as one load.
- The opener carries only the page's route. The page's loads and the permission it needs are looked
  up on the server, in a registry `lex_page` fills; an unknown route starts no loads. The opener
  starts the loads only when the user holds the page's permission. A load is still an ordinary event
  the browser can send, so the page's permission decides only whether the opener *starts* it: what a
  load may read is enforced by its own `requires=` and by the API acting as the user.
- A load declared on a page takes no arguments: it reads the page's route and query values from
  `self.params`. `lex_page(on_load=State.open(arg))` is refused with a `TypeError`. A load started
  on mount reads query parameters, not route arguments: after a navigation in the browser, its
  event carries the previous page's route arguments (section 7).
- For the loads the page declares, a state is reset to its class defaults once per visit, by the
  first page-declared load on that state in the visit, so a revisit never shows the previous visit's data. A
  load started on mount resets its state every time it starts. `keep=("filters",)` exempts fields;
  `keep=` is taken from that first load. Several loads on one state therefore add to each other
  within a visit, and a revisit starts clean.
- A load the page declares without `optional=True` must succeed for the page to show (section 6.5). It counts as
  finished only when its body ran to the end; a cancelled one stays pending. Loads marked
  `optional=True` fill secondary regions: they start once every required load has finished, run
  independently of each other, and a failure affects only their region.
- If the user has left the page by the time a load the page declared merges, its result is
  discarded.
- A load sees only its own state's fields, as a job does (below).

**`@ui.action`**

- The body runs in a worker thread on the live state while Reflex holds the user's lock.
- In development, an action that runs longer than one second logs a warning that it should be a job
  (one second is Reflex's own lock-warning threshold).
- Repeat clicks queue and run in order (section 5.3). Protection against a double click is the
  button's job: the lex button will disable itself in the browser the moment it is clicked
  (components spec). A handler that must never run twice checks its own precondition, or is written
  as a job.
- A body cannot run another handler of its state. Reflex turns every public method into an event
  handler, and calling one from a body only builds it. In an action, calling another action
  (`self.reload()`) fails the action with an error that says so; calling a load or a job from an
  action does nothing and raises nothing, because Reflex hands back a stand-in for a background
  handler. In a load or a job, reaching a handler through `self` raises. `lex reflex check` reports
  every `self.<handler>(...)` in a state's methods (6.7), so the check is what catches all of them.
  Work that two handlers share goes into a method whose name starts with `_` (`self._reload()`),
  which both call.

**`@ui.job`**

- At the start it copies the state's fields. The body works on the copy.
- A job sees its own state's fields; fields of a parent or another state are reached with
  `self.state(Other)`. Reading a field the state inherits from a parent through `self` raises an
  `AttributeError` that says to use `self.state(Parent)`.
- Every field of a state used by a job or load must be deep-copyable. A lock, a client or an open
  file in such a state is refused with a `TypeError` naming the state and the field.
- At each `yield` and at the end, lex-app takes the lock briefly, writes back only the fields the job
  changed since the last merge (assignments and in-place changes, found by comparing with the copy
  taken at that merge), then refreshes the copy, so the job now sees what the user changed meanwhile.
  Values are compared with `==`; where that does not give a plain yes or no (data frames, arrays),
  their pickled forms are compared instead.
- When the job and the user changed the same field, the job's value wins.
- A `yield` is also where a cancel takes effect: the generator is closed, so `try`/`finally` cleanup
  runs, and changes since the previous `yield` are discarded. A job without a `yield` cannot be
  cancelled.
- `yield ui.progress(0.4, "Reading files")` records a fraction and a message for a standard progress
  display. A job may also yield `ui.toast(...)`.
- One run per tab, per job, per set of arguments at a time; a repeat while it runs is ignored. The
  arguments are compared as a whole: `export(2025, 2)` runs beside `export(2025, 1)`.
- `stop_on_leave=True` stops a job when the page's opener starts a new visit in the tab. A page
  added with plain `rx.page` has no opener, so leaving it does not stop the job.
- Jobs run on their own thread pool; when it is full a new job waits its turn, and does not fail. Jobs run inside the
  Reflex backend process, so a restart ends them. Work that must survive a restart belongs on Celery,
  as lex-app already says for calculations; the job API is shaped so a Celery-backed variant can be
  added later.

The rule to teach: in a job, `yield` whenever you want to show progress or see the user's latest
input.

**Thread pools.** Two per backend process: an interactive pool for actions and loads
(`LEX_UI_ACTION_THREADS`, default 16) and a job pool (`LEX_UI_JOB_THREADS`, default 8). Each call
retires stale database connections before and after, as `run_orm` does. Neither uses `run_orm`'s
single shared thread, on which one slow call stalls every user.

**Status without hand-written flags.** The decorators keep each handler's status in the state, so a
view can ask:

| In a view | Is |
| --- | --- |
| `ui.running(State.handler)` | True while it runs |
| `ui.running(State.handler, row["id"])` | True only for the calls whose first argument is that value, compared as text |
| `ui.failed(State.handler)` | The failure message of its last run, or empty; a run refused for lack of permission records its message like any other failed run |
| `ui.progress_of(State.handler)` | The fraction and message last yielded |
| `ui.cancel(State.handler)` | The event a Cancel button sends |

For an action, the "running" status is sent to the browser before the body starts.

**Runs whose process is gone.** Each running mark records the backend process that set it, and every
page open clears the marks whose process is gone, so a crashed worker cannot leave a handler
"running" forever. With Redis, a process is alive while its heartbeat key exists: it is written every
10 seconds, expires after 30 and is removed on a clean stop. Without Redis, one process holds every
tab, so any other process id is gone. The Redis calls made for this are bounded at 2 seconds and fail
open: a doubt never clears a run. The whole sweep has one 2-second budget. A forked worker gets its
own process id.

`ui.progress_of` was added while writing this spec; `ui.running` and `ui.failed` were discussed.

**What a handler returns.** Nothing, or one or more of `ui.goto(path)`, `ui.toast(message, tone=...)`
and `ui.download(...)`. A generator returns them with `return`.

### 6.5 Failures

One rule for all three decorators: **what the browser has already been shown stays; whatever the
handler changed after that is discarded.** For an action this means restoring the fields to their
values at the start, or at its last `yield`.

| Kind | How it happens | What the user sees | What is logged |
| --- | --- | --- | --- |
| Refusal | The handler raises `ui.Refuse("This request is closed.")`, or lex-app's API rejects a write (validation) | That message | Nothing; it is expected |
| Not permitted | `requires=` is not met (the body never runs), or the API denies access | The standard "not permitted" message | A warning naming the handler and the user's id (never the name or e-mail) |
| Unexpected error | Any other exception | A generic message with a reference | The full traceback under that reference |

| Decorator | Where the message appears |
| --- | --- |
| `@ui.load` declared on a page | The page `/ui-error`, with the reference and a "Try again" that reopens the page; "not permitted" shows the same page as an access-denied message |
| `@ui.load(optional=True)` declared on a page | Inside that region only, through `ui.failed` |
| `@ui.load` started on mount | In place, through `ui.failed` |
| `@ui.action` | A toast |
| `@ui.job` | A toast, and `ui.failed` for showing it in place |

A handler therefore needs no `try`/`except` for the normal cases:

```python
@ui.action
def approve(self, request_id: int):
    if self.closed:
        raise ui.Refuse("This request is closed.")
    self.api.update(Request, request_id, {"status": "approved"})   # a validation error shows itself
```

Details:

- **The error page of a required load.** The page's path, with its query string, is noted when the
  load starts; "Try again" returns there, never to the error page itself and never to another site
  (only a path with one leading `/` and no backslash is kept; anything else returns to `/`). The
  first required failure ends the visit, so later outcomes of that visit are ignored. A failure that
  arrives after the browser has moved to another page shows no error page and sends nobody anywhere;
  it is logged, and its message is still recorded for `ui.failed`.
- **Who is acting is settled before the user's lock is taken.** A load or a job resolves the
  session and checks `requires=` first; inside its `async with self` blocks nothing raises. (Under
  Reflex's Redis state manager a block that raises is not stored, while the browser may already
  have seen its changes.) Its wind-up, which clears the "running" mark and records the failure, runs
  to its end even when Reflex cancels the handler meanwhile (as it cancels the previous page's loads
  at every navigation); the cancellation takes effect after it.
- **lex-app's API refusals** are shown in their own words: the body's `detail` alone when it has a
  text one, otherwise its fields as `name: message` pairs, without the program-facing `code`,
  `error` and `traceback`.
- **Database writes are not rolled back.** The discard covers the page's state. A handler that needs
  all-or-nothing database writes uses Django's `transaction.atomic()`. Handlers are not wrapped in a
  transaction by default, because a job would hold one open for minutes.
- **Logging can be limited to the error's type** with `LEX_UI_LOG_ERROR_TEXT=false`, for projects
  whose compliance rules keep exception text out of logs. The default logs the full traceback. The
  browser sees neither.
- The standard messages go through RFDS's translation, so they follow the app's UI language.

### 6.6 Tests and proof

**lex-app's own tests** go in new batches of the init cluster and drive handlers through Reflex's
in-process event processor, so they exercise the real lock and the real browser updates:

- Actions: changes from the worker thread, `yield` flushing, discard on failure, the slow-action warning.
- Loads: reset and `keep=`, a result discarded after the user left, an optional load failing alone.
- Jobs: the merge, the user's fields surviving, the same-field rule, repeats ignored (and a call with
  other arguments not ignored), cancel, `stop_on_leave`, the pool queueing when full, a cancellation
  during the wind-up, `requires=` not met.
- Data: `self.api` applying lex-app's rules, a query blocked without `requires=`, allowed with it,
  refused without the grant, audit fields naming the user.
- Failures: the three kinds and where each shows, `ui.running` / `ui.failed` / `ui.progress_of`, and a
  run whose process is gone no longer counting as running; a refusal under a state manager that,
  like Redis, does not store a block that raised.

**A test helper for projects**, exposing the same harness:

```python
result = ui.testing.run(Tickets, "open_ticket", code="X", user="anna", grants=["tickets.Ticket:read"])
assert result.goto == "/tickets/X"
```

`run(State, handler, *, user=, grants=, params=, fields=, api=, **arguments)` returns the final field
values, the updates the browser received in order, and any `goto`, toasts, downloads, refusal or
error. `grants` are `(Model, "read")` pairs or `"app_label.Model:scope"` strings. `api=` takes a
stand-in for `self.api`. A handler that uses `self.api` needs one: test tokens are not
Keycloak-signed, so the real API cannot answer in a unit test.

`run` is built on `ui.testing.Harness`, which a test uses directly when it needs more than one event:
`async with Harness(user=, grants=, params=, api=) as h`, then `h.send(State, "handler", **arguments)`
(it returns the event's future), `h.fields(State)`, `h.open_tab(url)`, `h.navigate(url)` (the browser
is now on `url`; no page load runs) and `h.mount(State, "handler", url=, sent_from=, route=)` (a
component's `on_mount` on `url`, followed by the page's opener, in Reflex's real order).

**Proof on real code.**

1. *Live.* A project already on the new stack has its Reflex example converted to `ui.State`; every
   page keeps working, with line counts before and after.
2. *Side by side.* Two files of the reviewed project (one with a load and short actions, one
   job-heavy) are ported outside that project's repository and tested with the helper against
   stand-ins for its logic functions. This shows the size and readability difference; it is not that
   project running on the layer, which needs it moved to the new stack first.

### 6.7 The custom region and the checks

A handler that needs raw Reflex is decorated `@ui.custom` in place of `@rx.event`. It behaves
identically; the marker makes it visible. Chunked uploads use it until an upload component exists.

`lex reflex check` gains:

| Finding | Result |
| --- | --- |
| A public method on a `ui.State` that is not `@ui.action`, `@ui.job`, `@ui.load` or `@ui.custom` — a raw `@rx.event` handler, or a method with no decorator, which Reflex also exposes to the browser | Fails the strict check |
| An `@ui.custom` handler | Listed for review; passes |
| `requires=ui.signed_in` | Listed for review; passes |
| A field, computed var or method of a `ui.State` named `user`, `api`, `params`, `can` or `state` (6.2) | Fails the strict check |
| `self.<handler>(...)` in a method of a `ui.State`, where `<handler>` is a lex.ui handler of that state or of a base the check follows (6.4) | Fails the strict check |
| A handler body that names a model manager (`.objects`) with no `requires=` | Warning (best effort; the run-time block is the guarantee) |

Helpers on a state are named with a leading underscore; properties, computed vars, static and class
methods are not handlers. The check reads source only. It recognises `lex.ui` and `reflex` under any
import alias, and follows inheritance across the project's files by class name, so it may
over-report on a name clash. A file it cannot analyse is itself an error. It does not see a public
attribute assigned a function (`refresh = lambda self: ...`), nor the methods a mixin brings in.

### 6.8 Migration, page by page

| Today | With the layer |
| --- | --- |
| `await run_orm(fn)` | Call `fn` in the body |
| `await (await lex_api(self)).update(...)` | `self.api.update(...)` |
| `@lex_event(auth=has_permission(M, "read"))` | `@ui.action(requires=ui.read(M))` |
| `await current_permissions(self)`, `has_permission(...)(self)` | `self.can(M, "read")` |
| `@rx.event(background=True)` with `async with self` | `@ui.job` |
| `async for _ in self.other():` | `yield from self._other()` — shared logic lives in underscore methods, because Reflex turns every public method into an event handler |
| Busy flags, stale-visit counters, `try`/`except` with a toast | Removed |

So that consultants and AI copy the new style, lex-app's reference example and the three
`lex reflex add` templates are converted to the layer in the same PR. The published docs page for the
layer is still to be written.

### 6.9 Before and after

The reviewed project's "open this item" handler takes about 70 lines today. Its shape, shortened, with
the project's names replaced:

```python
@rx.event(background=True)
async def open_ticket(self, code: str):
    events, claimed = [], False
    async with self:
        if self.opening or not self.loaded:
            return
        locale = await locale_of(self)
        try:
            args = await session_args(self)
        except Exception as error:
            self.open_error = translate(locale, "error.generic", incident=_log_incident("session", error))
            events = [toast.error(self.open_error)]
        else:
            self.opening = code
            self.open_error = ""
            claimed = True
        page = self._page_sequence
    if not claimed:
        for event in events:
            yield event
        return
    try:
        try:
            result = await run_in_worker(ticket_logic.open, code, *args)
            incident = ""
        except Exception as error:
            result, incident = None, _log_incident("open_ticket", error)
        async with self:
            if page != self._page_sequence:
                return                      # the user left meanwhile
            if result is None:
                self.open_error = translate(locale, "error.generic", incident=incident)
                events = [toast.error(self.open_error)]
            elif not result["ok"]:
                self.open_error = result["message"]
            else:
                events = [rx.redirect(f"/tickets/{result['code']}")]
    finally:
        async with self:
            if self.opening == code:
                self.opening = ""
    for event in events:
        yield event
```

With the layer, the same behaviour is:

```python
from lex import ui

class Tickets(ui.State):
    groups: list[dict] = []

    @ui.load
    def open(self):
        self.groups = self.api.list(Ticket, filters={"section": self.params.section})

    @ui.job
    def open_ticket(self, code: str):
        ticket = ticket_logic.open(code, self.user)      # the project's own business logic
        return ui.goto(f"/tickets/{ticket.code}")
```

| What the long version does by hand | Where it went |
| --- | --- |
| Busy flag claimed, checked and released | The job runs once at a time; the view reads `ui.running(Tickets.open_ticket, code)` |
| Reading the session and its failure path | `self.user`, resolved before the body runs |
| Running in a worker thread | Every body runs off the event loop |
| "The user left meanwhile" counter | The visit check built into loads and jobs |
| Catching the exception, logging an incident, building a translated toast | The standard unexpected-error path |
| A refusal in the business logic's own words | The logic raises `ui.Refuse(message)` |
| Locking around every state change | The merge at `yield` and at the end |

It is a job, not an action, because the original ignores a second press while one is running, and
only a job can do that.

### 6.10 What changed between the conversation and this spec

| Said in conversation | In this spec | Why |
| --- | --- | --- |
| `@ui.action(once=True)` drops a repeat click | Removed. A job ignores repeats; an action queues them. | Reflex takes the user's lock before it starts any handler, so a click arriving while an action runs cannot be examined until the action has finished. The probe showed three rapid clicks running twice. |
| An ORM query without a declared permission "fails the build" | Blocked at run time; flagged by the check on a best-effort basis | Detecting ORM use by reading code is not reliable. Blocking the query on the worker thread is. |
| `ui.running` and `ui.failed` | Plus `ui.progress_of` | A view needs to read what `yield ui.progress(...)` recorded. |

---

## 7. Limits and risks

| Risk | Where it stands |
| --- | --- |
| The 10-second lock expiry under Redis | Read from Reflex's source only; no Redis in the test environment, so no test runs it |
| A slow handler written as an action freezes that user's screen and fails under Redis after 10 s | The development warning at one second |
| Copying a state's fields at the start of every handler (for the discard and for working copies) | Not measured yet (below); switch to copying on first write if it costs too much |
| Thread capacity | Two sized pools; a polling loop written as a job holds a thread, so a `ui.poll` helper may follow |
| Jobs end when the backend restarts | Unchanged from today; Celery-backed jobs are a later piece |
| A repeat click on a short action runs twice | Cannot be prevented server-side (5.3). The lex button disables itself on click (components spec); handlers that must not repeat check their own precondition or are written as jobs. |
| State discard does not undo database writes | Documented; `transaction.atomic()` |
| The job wins when it and the user change the same field | Documented; a job should assign only the fields it owns |
| Every field of a state used by a job or load must be deep-copyable | A lock, a client or an open file in such a state is refused with an error naming the field (6.4) |
| A run cleared because its process is gone | It disappears without a message to the user |
| A "Try again" return path under a frontend path prefix | Untested |
| A load started on mount that finishes after the user has left | It still fills its own state and may still toast |
| Route arguments in a load started on mount | After a client-side navigation its event carries the previous page's pathname and route arguments (the query string is read live), so such a load reads query parameters. Read from Reflex's client code; not verified in a browser |
| Jobs with distinct arguments | Any signed-in user can queue any number of jobs with distinct arguments, as with any Reflex handler |
| One process record per handler and first argument | Overlapping runs started by different worker processes can clear a spinner early, or keep a dead one until that worker restarts; data is unaffected |
| The copy cost on thousands of rows | Not measured |
| An action cancelled twice | Reflex cancels a handler once; only a shutdown that outlasts its drain timeout cancels again. A second cancellation can let the discard run while the body's thread is still finishing. Jobs and loads are unaffected: their body writes a working copy and their wind-up is shielded |
| Two runs of one job whose arguments differ only in order | Keyword arguments collected by `**kwargs`, and keys inside a dictionary argument, are compared in the order given, so such a pair runs twice rather than once. Nothing is dropped |
| Two quick mounts of one load that needs a permission lookup | With a cold permission cache the older run can take the state after the newer one, so the older parameters win until the next run |

---

## 8. Not in this spec

- Components and page templates (piece 2), including the self-disabling button and the standard
  progress display that read `ui.running` and `ui.progress_of`.
- Design rules as checks (piece 3).
- The calculation page (piece 4).
- A chunked-upload component.
- Celery-backed jobs.
- A page class fusing state, route and view (decision U5).
- Moving the reviewed project to the new stack.
- A writing convention for comments in AI-written project code.

---

## 9. Acceptance

1. A handler written as a plain function under each decorator behaves as sections 6.4 and 6.5 say, shown by the tests of 6.6.
2. No handler in lex-app's reference example or scaffold templates contains `await`, `async with self`, `run_orm`, a busy flag or a `try`/`except` around a toast. RFDS's own async hooks, such as `ExportMixin._export_rows`, are excepted.
3. A database query in a handler without `requires=` raises; with it, the permission is enforced.
4. The live proof and the side-by-side proof of 6.6 are done, with line counts. (Open.)
5. Every existing Reflex test batch still passes.
