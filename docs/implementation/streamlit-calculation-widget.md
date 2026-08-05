# `lex_calculation()` — Streamlit Calculation Widget

> **Status:** Internal-only feature doc. `docs/features/` is mirror-owned from
> `lex-app-docs` (see [`docs/.docs-sync.yml`](../.docs-sync.yml)), so the
> customer-facing reference for this widget ships from upstream — add a
> "Calculation widget" section to `content/features/dashboards/` in
> `lex-app-docs` once this lands on `lex-app-v2`. Same arrangement as
> [`cancel-button-stub.md`](cancel-button-stub.md).
>
> **Source of truth:**
> [`lex/lex_app/streamlit/calculation.py`](../../lex/lex_app/streamlit/calculation.py)
> (the widget),
> [`lex/lex_app/streamlit/_client.py`](../../lex/lex_app/streamlit/_client.py)
> (the HTTP client),
> [`lex/api/views/calculations/CalculationStatus.py`](../../lex/api/views/calculations/CalculationStatus.py)
> (the status endpoint).
>
> **Design:**
> [`docs/superpowers/specs/2026-08-04-streamlit-calculation-widget-design.md`](../superpowers/specs/2026-08-04-streamlit-calculation-widget-design.md).
> Where this document and the design disagree, this one describes the code as
> committed — see [Known divergence from the design](#known-divergence-from-the-design).

## What it is for

A report dashboard usually needs one thing from a calculation: *run this record,
and tell me how it went.* Until now the only way to offer that from Streamlit was
`lex_view("quarter")` — embedding the whole React table view in an iframe, with
its toolbar and its grid, to deliver one button and one status line.

`lex_calculation()` is that button and that status line, natively:

```python
import streamlit as st
from lex.lex_app.streamlit import lex_calculation

lex_calculation("quarter", pk=42)
```

No iframe, no table, no second UI to keep in sync. The widget renders a
**Calculate** button, a coloured status badge, a last-run line, and — while the
calculation is running — refreshes itself until it finishes.

`lex.lex_app.streamlit` is now the import path for both widgets, exporting
`lex_view`, `Flow` and `lex_calculation`. The older
`lex.lex_app.streamlit.embed` path keeps working unchanged, so dashboards
already written against it need no edits.

### What it is not

It is not a record editor, a picker, or a list view. It acts on **one record you
already identified**, because Streamlit's own primitives (`st.selectbox`,
`st.session_state`) are better at selection than anything shipped here would be.
Pair them:

```python
quarter_id = st.selectbox("Quarter", options=load_quarter_ids())
lex_calculation("quarter", pk=quarter_id)
```

## API

```python
lex_calculation(
    model,                    # str, positional — the model container name, e.g. "quarter"
    pk,                       # positional — the record's primary key
    *,
    label="Calculate",        # str  — button text
    show_status=True,         # bool — the coloured status badge
    show_last_run=True,       # bool — the "Last run: … (took 38s)" caption
    show_error=True,          # bool — inline error box carrying the calculation's own message
    show_log=False,           # bool — live log tail; the only flag with a real cost
    poll_interval=2.0,        # float — seconds between polls while a run is in progress
    key=None,                 # str | None — explicit state namespace
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
| `poll_interval` | `2.0` | Seconds between status reads *while a calculation is in progress*. Ignored otherwise: a settled record is not polled at all. |
| `key` | `None` | State namespace. Defaults to `lex_calc_<model>_<pk>`, which already keeps two widgets watching two different records independent. You only need `key` to render **the same record twice on one page** — without it Streamlit rejects the second button as a duplicate widget ID, and either widget's status would decide the other's poll interval. |

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

Note the log is **rendered but not returned**: `log` and `log_truncated` are not
keys of the return value.

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

**A user who may not run the calculation is told after pressing the button.** The
button is disabled *while a calculation is in progress*, and at no other time —
it is not pre-emptively disabled for want of permission. Pressing it issues the
trigger, the backend refuses with 403, and the widget renders **"You don't have
permission to run this"** in an error box beside the button. Nothing is raised
and nothing else on the page is affected. See
[Known divergence from the design](#known-divergence-from-the-design).

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

The stopping is not free of subtlety, which is why it is pinned by scenarios
1.228–1.230 and 1.245–1.246. `st.fragment` reads `run_every` only when the
fragment is *declared*, and only a full script run declares one — so both
starting and stopping the poll require a full rerun, and the widget issues one
only when the interval actually changes. Rebuilding on every poll would rerun
your whole dashboard every two seconds, which is worse than the polling.

Two consequences for you as an author:

- **Your script is not re-executed by a poll.** Only the widget's fragment
  reruns, so a heavy dataframe above the widget does not reload every two
  seconds. This is the entire reason polling is viable here.
- **A rerun does happen when the state changes** — when a running record is first
  discovered, and when a run finishes. Code above the widget runs again at those
  two moments, so keep expensive loads behind `st.cache_data` as usual.

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
  "log": ["Loading positions…", "Valuing 1,204 rows…"],
  "log_truncated": true
}
```

`log` and `log_truncated` are present **only** with `include_log=true`. Timings
come from `CalculationLog`, scoped to the newest `calculationId` — the record
carries no timestamp of its own, and a window spanning every run it ever had
would report days for a run that took seconds.

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

## Known divergence from the design

The design
([`2026-08-04-streamlit-calculation-widget-design.md`](../superpowers/specs/2026-08-04-streamlit-calculation-widget-design.md),
"Error handling") specifies *"No calculate permission (403 on PATCH) → Button
**disabled** with reason"*, on the reasoning that a hidden button reads as a
broken dashboard.

**The code as committed does not do this.** `lex_calculation()` disables the
button only while the record is `IN_PROGRESS`; a user without run permission sees
an enabled button and learns of the refusal after pressing it. Nothing breaks —
the refusal is caught and rendered as "You don't have permission to run this" —
but the reason arrives one click later than designed, and it arrives every time.

Implementing the designed behaviour needs something the status endpoint does not
currently return: whether *this caller* may trigger the calculation. Read
permission alone cannot answer it. Closing the gap means adding that field to the
status envelope (cluster 10o) and a disabled-with-reason branch in the widget
(cluster 1ab), which is a change to both halves and out of scope for the batches
as landed.

## Tests

| Batch | Scenarios | Covers |
| --- | --- | --- |
| [1ab](../../lex/test_project/test-plan/clusters/01-init/batches.md) | 1.223 – 1.246 (24 pass) | The widget and its HTTP client — poll lifecycle, presentation, trigger transport, every failure path, and the gates on colour drift and Django imports. |
| [10o](../../lex/test_project/test-plan/clusters/10-api_layer/batches.md) | 10.72 – 10.81 (10 pass) | The status endpoint — the six statuses, read-permission indistinguishability, log bounding and truncation, run scoping. |

## Follow-ups

- [ ] Open a docs PR in `lex-app-docs` adding the customer-facing widget page
      under `content/features/dashboards/`, and cross-link it from the Streamlit
      dashboards section. Remove this file's "what the user sees" half once that
      ships, keeping the internal contract notes here.
- [ ] Decide whether to close the disabled-button divergence above, or amend the
      design to match the code. It should not stay a silent difference.
- [ ] `_client.py` is deliberately action-agnostic — it is the piece a future
      `lex_action()` would reuse unchanged. If a second widget is wanted,
      generalising should be "add a widget module", not "refactor the first one".
