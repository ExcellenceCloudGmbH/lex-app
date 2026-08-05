# Streamlit calculation widgets

> **Two entry points.** `lex_calculation()` embeds the lex-app frontend's own
> record view — the fields shown and their formatting are the product's, chosen
> with a named `serializer`. `lex_calculation_streamlit()` renders the trigger
> natively and is what the rest of this document describes; it is the one for a
> dashboard of many tiles, and the only one that returns a value the page can
> branch on.
>
> | | `lex_calculation()` (embed) | `lex_calculation_streamlit()` (native) |
> | --- | --- | --- |
> | Shows the record's fields | **yes**, from the serializer | no — trigger and status only |
> | Cost per tile | a full React app, its own WebSockets and auth handshake | a dictionary lookup |
> | Sizing | fixed pixel height, scrolls inside | flows with the page |
> | Returns to Python | nothing (a frame is a separate context) | the status envelope |
> | Auth | session cookie, `SameSite=None` cross-site | bearer token |
>
> `lex_calculation(model, pk, *, serializer=None, view="show"|"edit"|"list", height=420, ...)`
> is URL construction over `lex_view` and nothing else — see
> `lex/lex_app/streamlit/calculation_embed.py`.


> **Status:** Internal-only feature doc. `docs/features/` is mirror-owned from
> `lex-app-docs` (see [`docs/.docs-sync.yml`](../.docs-sync.yml)), so the
> customer-facing reference for this widget ships from upstream — add a
> "Calculation widget" section to `content/features/dashboards/` in
> `lex-app-docs` once this lands on `lex-app-v2`. Same arrangement as
> [`cancel-button-stub.md`](cancel-button-stub.md).
>
> **Source of truth:**
> [`lex/lex_app/streamlit/calculation.py`](../../lex/lex_app/streamlit/calculation.py)
> (the native widget),
> [`lex/lex_app/streamlit/calculation_embed.py`](../../lex/lex_app/streamlit/calculation_embed.py)
> (the embed),
> [`lex/lex_app/streamlit/_status_poller.py`](../../lex/lex_app/streamlit/_status_poller.py)
> (the polling thread),
> [`lex/lex_app/streamlit/_client.py`](../../lex/lex_app/streamlit/_client.py)
> (the HTTP client),
> [`lex/api/views/calculations/CalculationStatus.py`](../../lex/api/views/calculations/CalculationStatus.py)
> (the status endpoint).
>
> **Design:**
> [`docs/superpowers/specs/2026-08-04-streamlit-calculation-widget-design.md`](../superpowers/specs/2026-08-04-streamlit-calculation-widget-design.md).
> This document describes the code as committed; the widget now implements the
> design's error-handling table in full.

## What it is for

A report dashboard usually needs one thing from a calculation: *run this record,
and tell me how it went.* Until now the only way to offer that from Streamlit was
`lex_view("quarter")` — embedding the whole React table view in an iframe, with
its toolbar and its grid, to deliver one button and one status line.

`lex_calculation_streamlit()` is that button and that status line, natively:

```python
import streamlit as st
from lex.lex_app.streamlit import lex_calculation_streamlit

lex_calculation_streamlit("quarter", pk=42)
```

No iframe, no table, no second UI to keep in sync. The widget renders a
**Calculate** button, a coloured status badge, a last-run line, and — while the
calculation is running — refreshes itself until it finishes.

If you want the record's **fields** as well as the trigger, use
`lex_calculation()` instead — it embeds the frontend's own record view and takes
a named `serializer` to choose which fields appear.

`lex.lex_app.streamlit` is the import path for all of them, exporting `lex_view`,
`Flow`, `lex_calculation` and `lex_calculation_streamlit`. The older
`lex.lex_app.streamlit.embed` path keeps working unchanged, so dashboards
already written against it need no edits.

### What it is not

It is not a record editor, a picker, or a list view. It acts on **one record you
already identified**, because Streamlit's own primitives (`st.selectbox`,
`st.session_state`) are better at selection than anything shipped here would be.
Pair them:

```python
quarter_id = st.selectbox("Quarter", options=load_quarter_ids())
lex_calculation_streamlit("quarter", pk=quarter_id)
```

## API

```python
lex_calculation_streamlit(
    model,                    # str, positional — the model container name, e.g. "quarter"
    pk,                       # positional — the record's primary key
    *,
    label="Calculate",        # str  — button text
    show_status=True,         # bool — the coloured status badge
    show_last_run=True,       # bool — the "Last run: … (took 38s)" caption
    show_error=True,          # bool — inline error box carrying the calculation's own message
    show_log=False,           # bool — live log tail; the only flag with a real cost
    poll_interval=2.0,        # float — seconds between backend reads while a run is in progress
    key=None,                 # str | None — explicit state namespace
    sync_page=False,          # bool — re-run the whole page when this record's status changes
) -> dict | None
```

| Argument | Default | Meaning |
| --- | --- | --- |
| `model` | *required* | The model container name as it appears in the REST API — the same string you would pass to `lex_view()`. |
| `pk` | *required* | Primary key of the one record this widget acts on. |
| `label` | `"Calculate"` | Text on the button. |
| `show_status` | `True` | Renders the status badge beside the button, coloured from `lex.lex_app.design_system` (LEX success is teal `#14B4B4`, not green). Set `False` for a bare button. |
| `show_last_run` | `True` | Renders one caption describing the most recent run — either `Never run`, `Last run: <timestamp>`, or `Last run: <timestamp> (took 38s)`. |
| `show_error` | `True` | When the record is in `ERROR` and the model carries a `calculation_error_message` (or `error_message`), renders it in an error box. Set `False` if your dashboard surfaces errors its own way. |
| `show_log` | `False` | Renders the tail of the running calculation's log. **This is the only argument that costs anything** — see [Cost](#cost-show_logtrue-is-the-only-expensive-argument). |
| `poll_interval` | `2.0` | Seconds between *backend reads* while a calculation is in progress. Ignored otherwise: a settled record is not read at all. This is **not** how often the tile redraws — the tile keeps itself current on its own, at half this interval, bounded to 0.5–2s, and that redraw issues no request. Two tiles on one record are read at the shorter of the two intervals. |
| `key` | `None` | State namespace. Defaults to `lex_calc_<model>_<pk>`, which already keeps two widgets watching two different records independent. You only need `key` to render **the same record twice on one page** — without it Streamlit rejects the second button as a duplicate widget ID. |
| `sync_page` | `False` | Re-run the whole page whenever this record's status changes. Needed only when code **outside** the widget branches on its return value — see [Keeping the rest of the page in step](#keeping-the-rest-of-the-page-in-step). Off by default because a page run greys every element on the page until it is redrawn. |

### Return value

The latest status envelope, or `None`.

```python
{
    "status": "SUCCESS",                      # str  — the record's is_calculated value
    "error": None,                            # str | None — the calculation's own error message
    "started_at": "2026-08-04T12:04:11+00:00",# str | None — ISO 8601
    "finished_at": "2026-08-04T12:04:49+00:00",# str | None
    "duration_seconds": 38.0,                 # float | None
}
```

`None` means *there is no status to branch on yet*: the session has no token, or
the status read failed (record missing, unreadable, backend unreachable). The
widget has already rendered an explanation in all of those cases — a `None`
return is not something you need to report.

`status` is whatever the badge is showing, which for the one render that handles
a click is `IN_PROGRESS` before any status read has confirmed it (see [What a
page costs](#what-a-page-costs)). That is on purpose: a dashboard branching on
the return value should never render finished results underneath a badge that
says the record is running.

Note the log and the calculate permission are **rendered but not returned**:
`log`, `log_truncated`, `can_calculate` and `calculate_denied_reason` are not
keys of the return value. The widget has already acted on the last two — the
button is drawn from them — and a dashboard branching on the record's *status*
should not have to step around fields describing its own button.

## Worked example

```python
import streamlit as st
from lex.lex_app.streamlit import lex_calculation

st.title("Quarterly close")

quarter_id = st.selectbox("Quarter", options=load_quarter_ids())

status = lex_calculation(
    "quarter",
    pk=quarter_id,
    label="Run close",
    show_log=True,          # this one is watched closely, so pay for the log
    poll_interval=1.0,
)

# `status` is None until the first successful read — guard before subscripting.
if status is None:
    st.info("Waiting for this quarter's status.")

elif status["status"] == "SUCCESS":
    st.dataframe(load_results(quarter_id))
    # duration_seconds is None for a record whose run left no log rows.
    if status["duration_seconds"] is not None:
        st.caption(f"Computed in {status['duration_seconds']:.1f}s")

elif status["status"] == "IN_PROGRESS":
    st.info("Close is running — this page updates itself.")

elif status["status"] == "ERROR":
    # The widget already showed the message (show_error defaults to True);
    # this is where the dashboard decides what else to hide.
    st.warning("Results are stale — the last run failed.")

elif status["status"] == "ABORTED":
    st.warning("The last run was interrupted. Run it again for fresh numbers.")
```

Branching on the return value is what makes the widget composable rather than a
dead end — the expensive part of the page (`load_results`) never runs against
numbers that were never computed.

## The four states

The widget renders whatever `is_calculated` the backend reports. Four states
matter to a reader:

| State | Backend status | Badge | What it means to a user |
| --- | --- | --- | --- |
| **Idle** | `NOT_CALCULATED` | "Not calculated", muted | This record has never been run. There are no results yet — press the button. Timings are absent, so the caption reads `Never run` rather than a zeroed duration, because "took 0s" would claim a run that never happened. |
| **Running** | `IN_PROGRESS` | "Running", amber | Work is in flight. **The button is disabled** for as long as this lasts, so a record cannot be double-triggered. The widget refreshes itself every `poll_interval` seconds and will change state on its own — the reader does not reload anything. |
| **Success** | `SUCCESS` | "Success", teal | The last run finished cleanly. `duration_seconds` is populated, and the last-run caption reports it. Polling has stopped. |
| **Error** | `ERROR` | "Error", red | The last run failed. With `show_error=True` (the default) the calculation's own message is rendered underneath, so the reader sees *what* failed, not just *that* it did. Polling has stopped; nothing will change until someone runs it again. |

Two further states exist and are deliberately **not** collapsed into `ERROR`:

- **`ABORTED`** — badge "Interrupted", amber, plus the caption *"This run was
  interrupted — run it again."* An aborted row is interrupted work, not a
  failure: the row was left `IN_PROGRESS` by a restart and swept. Rendering it as
  an error is what made incident 1410 hard to read — people went looking for a
  failure that had never happened. "Run it again" is the only available action
  and it is the correct one.
- **`CANCELLED`** — badge "Cancelled", muted, no nudge. The user's own doing, so
  it is not a fault and needs no prompting.

A status the widget has never heard of renders **its own name** in muted styling
rather than nothing, so a state added to `CalculationModel` before this table
catches up looks unfamiliar instead of invisible.

### States that are not the record's

Three messages describe the *read*, not the calculation. They render in place of
the badge, and the widget returns `None`:

| Condition | Rendered |
| --- | --- |
| Record missing **or** not readable by this user (404) | `Record not found` |
| The action was refused (403 on the status read) | `Not available` |
| Backend unreachable, 5xx, or any other failure | `Status unavailable` |
| No access token in the Streamlit session | `Session expired — reload the page to sign in again.` (and no backend call is made at all) |

**No failure path raises.** Streamlit renders a page top-to-bottom, so an
exception escaping a widget erases every widget below it: the page would not
report a problem, it would silently lose its bottom half. Every failure —
refused, missing, broken, unreachable — is a state the widget can draw.

## Permissions

Everything the widget does travels over HTTP as the **signed-in user**, using the
bearer token the Streamlit host keeps in `session_state["access_token"]`. The
widget imports no Django model, deliberately: an in-process ORM call would skip
the record's read permission, resolve the wrong audit actor, and miss the
`_defer_calculate_hook` trigger path, all in one step. Read permission, audit
trail and history are therefore exactly what they would be for the same action
taken in the React UI. There is no environment-variable fallback to the
instance's API key — that key is a machine-to-machine secret resolving to a
technical user, and using it would detach the run from the person who asked for
it.

Two behaviours are worth stating plainly.

**A record you may not read is reported exactly as a record that does not
exist.** The status endpoint filters the record through
`UserReadRestrictionFilterBackend` — the same backend that guards every list
read — and answers an unreadable record with the *same 404 status and the same
response body* as a genuinely missing one. This is not an oversight: a
distinguishable response would itself confirm that the record exists, and leak
its calculation state, to someone not allowed to know either. The widget renders
`Record not found` for both, and cannot tell them apart. So can you not, from a
dashboard: if a reader reports "record not found" for a record you know exists,
the question to ask is about their permissions, not the data.

**A user who may not run the calculation is told before pressing the button.**
The status envelope carries `can_calculate` — the backend's own answer to *may
this caller trigger a run on this record* — and the widget draws the button
disabled when it is `false`, with `calculate_denied_reason` rendered beside it. A
button that looks usable and is not is worse than one that explains itself, and
the point of this widget is that the reader does not have to go somewhere else to
find out what happened.

Nothing about that moves a permission decision into the client. The endpoint does
not invent a rule: it instantiates
[`UserPermission`](../../lex/api/views/permissions/UserPermission.py) — the very
DRF permission class `OneModelEntry` declares — and evaluates it against the same
`PATCH` body the button would send, so its answer is produced by the code that
enforces the trigger rather than by a second implementation that could drift.
Concretely: `has_permission()` for the model-level rule
(`modification_restriction.can_modify_in_general`), then `has_object_permission()`
for the record-level one (`can_be_modified`, which receives the `{"calculate":
"true"}` payload). The `reason` is the framework's own denial message, which
already lists the restriction's `violations` — text the model author wrote for a
user to read, and text this caller would receive in the 403 body anyway. Nothing
is disclosed that pressing the button would not disclose.

**The button is still disabled while a calculation is in progress**, for the
separate reason that a record must not be triggered twice; the two conditions are
independent and both hold.

**The 403 path remains.** The envelope is at most one poll old and the permission
behind it can change between the poll and the click — and only the `PATCH` is
actually being authorised. A refusal that arrives late is still caught and still
rendered as **"You don't have permission to run this"** in an error box beside
the button. Belt and braces: losing that branch would turn a stale envelope into
a button that silently does nothing.

Two directions of error are not symmetric, and the implementation is biased
accordingly. A button left enabled for someone who may not run the record costs
one click and one message, which the 403 path above already handles. A button
disabled
for someone who *may* run it is a dead end with nothing left to press. So the
widget treats **only** an explicit `can_calculate: false` as a refusal (a missing
key — an endpoint older than the flag — leaves the button enabled), and the
endpoint reports the button as enabled if evaluating the permission raises at
all, leaving the decision to the trigger itself.

## Polling self-terminates

While a record is `IN_PROGRESS`, the widget re-reads its status every
`poll_interval` seconds. On **any** terminal status — `SUCCESS`, `ERROR`,
`ABORTED`, `CANCELLED`, `NOT_CALCULATED` — and on any failed read, the poll
interval becomes `None` and the timer is torn down.

This matters more than it sounds. Nothing about a settled record will change
again until somebody acts on it, so a widget that kept asking would be permanent
backend load: invisible, because nothing is broken; multiplied by every widget on
the page, and again by every browser tab anyone left open over a weekend. A
dashboard showing ten finished calculations makes **zero** requests after its
first render.

The reads happen on a background thread the session owns (see [What a page
costs](#what-a-page-costs)), so "stopping" means that thread stops asking. It
starts again by itself when you press Calculate. Pinned by scenarios 1.228–1.229
and 1.256.

Two consequences for you as an author:

- **Your script is never re-executed by a poll.** Only the widget's own tile
  redraws, so a heavy dataframe above the widget does not reload every two
  seconds — and the redraw itself issues no request, so it costs a dictionary
  lookup.
- **Nothing outside the widget updates on its own.** That is the trade for the
  above; see [Keeping the rest of the page in
  step](#keeping-the-rest-of-the-page-in-step).

## What a page costs

The widget is built for pages with a lot of these on them — a status tile per
line of a report — so the cost of *one* is not the interesting number. Measured
in a browser on thirteen tiles over six records, against a backend answering in
300 ms, which is the page this section exists because of:

| | Then | Now |
| --- | --- | --- |
| Time to render the whole page | seconds | **17 ms** |
| Reads on the render thread | 13 | **0** |
| Click → badge reads "Running" | ~7 s | **103 ms** |
| Full script runs caused by a click | 14 | **0** |
| Page greyed (share of wall clock) | seconds at a time | **0.8 %**, in ~20 ms flickers |

One rule produces all of it, and scenarios 1.251–1.266 hold each half:

> **The render path performs no I/O and reruns nothing but itself.**

**No I/O.** Streamlit runs a page top-to-bottom on one thread. A tile that reads
its own status *inside* the render does not load slowly — it holds up every tile
below it, in series, before any of them exist. All reads and all triggers now
belong to `StatusPoller` (`lex/lex_app/streamlit/_status_poller.py`), a daemon
thread the session owns. `lex_calculation()` registers what it wants watched and
reads whatever answer is already in hand; both are dictionary lookups. The
thread reads up to four records at once, keeps one pooled HTTPS connection per
thread, and stops asking about a record the moment it settles.

The click follows the same rule. `One.update` re-reads the record, clears
terminal state, saves it, registers the calculation and broadcasts it before it
answers — doing that inside the click handler is exactly why the button used to
feel dead. The trigger is queued and the render finishes immediately.

**No page reruns.** `run_every` is read only when a fragment is *declared*, and
only a full script run declares one — so any design that adapts the poll timer
to the record's status has to rerun the page to change it. `st.rerun()` raises,
so that rerun took every widget still to be drawn with it; they never rendered,
found their own running records next time round, and each asked for a rerun of
its own. **One click cost fourteen script runs and 104 reads.** The timer is now
declared unconditionally and never rebuilt. Holding it costs a redraw of a
dictionary lookup once a second; rebuilding it costs a page.

**A click renders its own answer.** Pressing Calculate used to leave the badge
reading "Not calculated" until a re-read agreed, so the honest reading was that
the button had not worked. The widget renders **Running** the moment the click is
accepted. This is not a guess: `One.update` sets `is_calculated = IN_PROGRESS`
and saves it *before* it answers, so the record really is running by then. If the
trigger is refused, the optimism is taken back and the refusal is shown instead.
The next real read supersedes it either way, so it can never become a second
opinion about the record.

### The poller is per session, and that is a permission boundary

The poller lives in `st.session_state` — **not** `st.cache_resource`, and not a
module-level singleton. That is deliberate and it is not a preference.
`st.cache_resource` is shared by every session the server is running; the poller
holds status envelopes *and a bearer token*. The status endpoint answers an
unreadable record with the same 404 as a missing one, specifically so that its
existence is not disclosed — and a shared poller would hand over exactly the
answer that machinery exists to withhold, as well as making one user's
credential reachable from another user's request. `session_state` is already
scoped to one signed-in user, so the boundary comes for free. Pinned by 1.261.

The polling thread never touches `st` at all (pinned by 1.260): a thread with no
script run context that calls into Streamlit either raises or writes into
whichever session happens to be current. The token is handed in from the render
thread; results come back through plain dictionaries under a lock. The thread
also stops itself once the session stops rendering, because a closed tab gives
no teardown signal a background thread can see.

### What happens when reads fail

A status read is not cheap. Every request through `KeycloakPermissionsMiddleware`
makes **two uncached network calls to Keycloak** (`get_uma_permissions` and
`oidc.userinfo`), so a poll is a real round trip and, under load, one that
sometimes does not come back. Three rules keep that from reading as a broken
page:

**A failure is retried, unless it is an answer.** 403 and 404 *are* answers
about the record and will not change on their own, so they stop the watch.
Everything else — a timeout, a 502, a 401 against a token the host is about to
rotate — says nothing about the record, so it is retried on a backoff doubling
from 1s to 30s. Treating every failure as final is what left one dropped
connection showing "Status unavailable" until the reader reloaded the page.

**A failure never erases a confirmed status.** If a read fails and the record
last read `SUCCESS`, the badge still says `SUCCESS` and a separate caption says
`Reconnecting…`. The calculation did not stop being finished; our contact with
the backend lapsed, which is a much smaller thing to say — and putting "Status
unavailable" next to a status the reader can see is both alarming and untrue.
The exception is a 404 arriving after a good read: the record has gone, so the
old status goes with it.

**A tile that is redrawing keeps its poller alive.** Liveness used to be
refreshed only on a full script run, and the whole design is that a settled page
never has one — so a calculation running longer than 90 seconds outlived the
thread watching it and froze on "Running". `peek()` refreshes it now, and every
redraw calls `peek()`.

### The first moments of a page

A tile appears with the page and before anything is known about its record, so
it renders `—` — a placeholder holding the space the status will occupy, not the
word "Checking", which thirteen tiles all saying at once reads as a page that
cannot reach its backend.

How long that lasts is one backend round trip, not several: watches registered
during a single script run are held for 30 ms so they leave in one parallel
batch (up to 8 at a time). Without that, the poller wakes on the first tile's
watch, reads that record alone, and only then discovers the rest — two rounds of
latency to fill a page.

### Pressing the button

The click is acknowledged in the same render that handled it, with `Starting…`
rather than `Running`: the reader pressed a button and is owed an answer to
*that*. It also fails better — a refusal arriving after `Starting…` is a story,
and after `Running` it is a contradiction.

The trigger is dispatched on its own thread, not handed to the polling loop.
That loop spends most of its time inside a read pass, and a trigger queued
behind one reached the backend a second or more after the click — so the
calculation genuinely started late while the badge already said it had started.
`request_trigger()` returns in about a millisecond; the PATCH leaves within
~15 ms.

### Keeping the rest of the page in step

The tile updates itself in place. Code *outside* it does not — Streamlit only
re-evaluates your script on a page run, and the whole point of the above is that
the widget stops causing them. So this:

```python
status = lex_calculation_streamlit("quarter", pk=42)
if status and status["status"] == "SUCCESS":
    st.dataframe(load_results())
```

shows the dataframe as of the last page run. The widget always re-runs the page
**once**, when the first read for a record lands, so the pattern is correct on a
freshly opened page rather than only after the reader happens to click
something. Beyond that, pass `sync_page=True` to keep the page following one
record:

```python
status = lex_calculation_streamlit("quarter", pk=42, sync_page=True)
```

Use it for the one tile whose result the page is built around, not for all of
them: each page run greys every element until it is redrawn, and a dashboard of
tiles should not flicker because one of them finished.

## Cost: `show_log=True` is the only expensive argument

Every other flag toggles something the widget already has in hand. `show_log` is
different: it adds `?include_log=true` to each poll, and only then does the
endpoint query `CalculationLog` at all. With the flag off, the log keys are
absent from the response entirely — not empty — so the ordinary two-second poll
never touches the log table.

When it is on, the tail is bounded to the **50 most recent lines of the newest
run**, oldest first, and the widget adds *"Showing the most recent lines only."*
when there were more. The bound is the point: an unbounded tail would turn a
2-second poll on a long calculation into a large response, repeated forever.

Turn it on for the one calculation somebody is actually watching. Leave it off
for the status tiles.

## Configuration

None, in the ordinary case. The client resolves the backend in this order:

1. `LEX_API_URL` — explicit override, for a deployment that does not serve the
   API from the frontend host;
2. `REACT_APP_URL` / `LEX_FRONTEND_URL` — the origin `lex_view()` already needs.
   Django serves the React bundle *and* `/api/…` from one host, so a dashboard
   that can embed a `lex_view` can reach the API with no extra configuration, and
   the two cannot be pointed at different hosts by accident;
3. `http://localhost:8000`, for local development.

Backend calls time out after 10 seconds. This runs inside a page render, so a
hung request would freeze the whole dashboard.

## HTTP contract

```
GET /api/model_entries/<model:model_container>/<int:pk>/calculation-status
GET /api/model_entries/<model:model_container>/<int:pk>/calculation-status?include_log=true
```

```json
{
  "status": "IN_PROGRESS",
  "error": null,
  "started_at": "2026-08-04T12:04:11+00:00",
  "finished_at": null,
  "duration_seconds": null,
  "can_calculate": true,
  "calculate_denied_reason": null,
  "log": ["Loading positions…", "Valuing 1,204 rows…"],
  "log_truncated": true
}
```

`log` and `log_truncated` are present **only** with `include_log=true`. Timings
come from `CalculationLog`, scoped to the newest `calculationId` — the record
carries no timestamp of its own, and a window spanning every run it ever had
would report days for a run that took seconds.

`can_calculate` is what the trigger below would answer for this caller, computed
by running `One.update`'s own permission class against the trigger's own payload
(see [Permissions](#permissions)). `calculate_denied_reason` is a string only
when `can_calculate` is `false`, and `null` otherwise.

The trigger is the React UI's own call, unchanged:

```
PATCH /api/model_entries/<model>/default/one/<pk>
Content-Type: application/json

{ "calculate": "true" }
```

`calculate` travels in the **body**. `One.update` reads it from `request.data`
and never looks at the query string, so a flag parked in the URL is dropped and
the PATCH degrades into an empty partial update — no calculation, no error, a
button that appears to do nothing.

## Tests

| Batch | Scenarios | Covers |
| --- | --- | --- |
| [1ab](../../lex/test_project/test-plan/clusters/01-init/batches.md) | 1.223 – 1.262 (40 pass) | The widget and its HTTP client — poll lifecycle, presentation, trigger transport, the disabled-with-reason button and its 403 backstop, every failure path, and the gates on colour drift and Django imports. 1.251–1.262 cover what a *page* of widgets costs: shared reads, the per-session cache boundary, the optimistic click, and the rerun budget — the last three measured against the real Streamlit runtime with `AppTest`, because "how many times does the script run" is a claim about Streamlit rather than about this code. |
| [10o](../../lex/test_project/test-plan/clusters/10-api_layer/batches.md) | 10.72 – 10.83 (12 pass) | The status endpoint — the six statuses, read-permission indistinguishability, `can_calculate` pinned against what `One.update` really enforces, log bounding and truncation, run scoping. |

## Follow-ups

- [ ] Open a docs PR in `lex-app-docs` adding the customer-facing widget page
      under `content/features/dashboards/`, and cross-link it from the Streamlit
      dashboards section. Remove this file's "what the user sees" half once that
      ships, keeping the internal contract notes here.
- [ ] `UserPermission.has_object_permission` builds its denial message from the
      wrong arguments (`get_permission_denied_message(obj, user, violations)`
      where the signature is `(access_type, requested_unit, violations)`), so a
      record-level refusal reads as *"You do not have general &lt;record&gt;-access
      to the requested &lt;user&gt;."* — in the 403 body of every modify endpoint,
      not just here. `_calculate_permission` falls back to a plain sentence
      rather than surfacing it; fixing the message itself changes a
      user-visible string across the API and wants its own change.
- [ ] `_client.py` is deliberately action-agnostic — it is the piece a future
      `lex_action()` would reuse unchanged. If a second widget is wanted,
      generalising should be "add a widget module", not "refactor the first one".
