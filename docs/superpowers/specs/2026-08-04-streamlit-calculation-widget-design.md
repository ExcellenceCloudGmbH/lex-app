# Streamlit calculation widget — design

**Date:** 2026-08-04
**Branch:** `feat/streamlit-calculation-widget` (off `lex-app-v2` @ `5595a9a2`)
**Status:** design approved, not yet planned

## Problem

Triggering a calculation from a Streamlit dashboard today means embedding a whole
React view with `lex_view("quarter")` and clicking through the table UI. For the
common case — "run this one record and tell me how it went" — that is a table,
a toolbar and an iframe to deliver one button and one status.

There is no non-iframe widget API at all: `lex/lex_app/streamlit/embed.py` exposes
only `lex_view()`, and `lex/lex_app/streamlit/__init__.py` is a single line.

## Scope

**In:** one widget — trigger a calculation on a known record, show its live status,
show the error when it fails, optionally tail its log.

**Out:** a widget *family* (record fields, pickers, list-lite). Explicitly rejected:
that path ends in reimplementing the React app in Streamlit, with two divergent
UIs to maintain.

**Designed for, not built:** generalising to "any LexModel action as a widget".
The seam is the status endpoint and `_client.py` — see [Generalisation](#generalisation-the-c-step).

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Record identity | Author supplies `pk` | Streamlit already has good selection primitives (`st.selectbox`, `session_state`). A built-in picker competes with them and needs search/pagination/permission-filtering to be useful — i.e. most of a list view |
| Widget anatomy | All three densities, argument-driven | Defaults to the card; inline and log-tail are flags |
| Liveness | `@st.fragment(run_every=2s)` | Native to Streamlit 1.58 (confirmed installed). No custom component, no WebSocket auth inside an iframe sandbox — the class of problem that produced the "refused to connect" incident |
| Trigger transport | `PATCH …?calculate=true` over HTTP, as the user | The exact path the React UI uses. One way to start a calculation, not two |
| Status transport | New dedicated read-only endpoint | Polling a full record serialization to learn one enum is wasteful on wide models, and the log tail needs its own round trip anyway |
| ORM access from Streamlit | **Rejected** | Bypasses DRF permissions, audit actor resolution, and the `_defer_calculate_hook` trigger path. A second, divergent way to start a calculation is what produced the `edited_at` bug (PR #675) |
| 403 on calculate | Disabled button with reason | A hidden button reads as a broken dashboard |
| `ABORTED` | Distinct badge + re-run nudge | Since #675 an aborted row is a *stale* state, not a failure. Collapsing it into ERROR is the confusion that made incident 1410 hard to read |

## Public API

```python
from lex.lex_app.streamlit import lex_calculation

status = lex_calculation(
    "quarter", pk=42, *,
    label="Calculate",
    show_status=True,      # badge
    show_last_run=True,    # "Last run 12:04 · took 38s"
    show_error=True,       # inline error box on failure
    show_log=False,        # live log tail — the only flag with a real cost
    poll_interval=2.0,
    key=None,
)
```

Returns the latest status envelope, or `None` before the first read. The return
value is what makes the widget composable rather than a dead end:

```python
if status and status["status"] == "SUCCESS":
    st.dataframe(load_results())
```

`lex/lex_app/streamlit/__init__.py` becomes the public surface, exporting both
`lex_view` and `lex_calculation`. The existing `lex.lex_app.streamlit.embed`
import path keeps working.

## Module layout

| Unit | Responsibility | Depends on |
| --- | --- | --- |
| `lex/lex_app/streamlit/calculation.py` | Rendering, fragment poll loop, session state | `_client`, `design_system` |
| `lex/lex_app/streamlit/_client.py` | Resolve base URL, attach the user's bearer token, GET/PATCH, map failures to typed errors | `requests` |
| `lex/api/views/calculations/CalculationStatusView.py` | Read-only status endpoint | existing permission path |

`_client.py` is separate from the widget on purpose: it is the piece a future
`lex_action()` reuses unchanged, so generalising is "add a widget module", not
"refactor the first one".

**The widget never imports Django.** It talks only to `_client`. Permissions and
audit therefore behave exactly as they do for the React UI, and the widget would
still work if Streamlit were moved out of the shared image.

### Backend URL

The Streamlit pod already receives `INSTANCE_RESOURCE_IDENTIFIER` from the dpag
configmap, so the in-cluster backend is `http://lex-backend-<id>:7000`
(`:7001` when the AI client is enabled). `_client` resolves it with the same
override precedence `embed._resolve_base_url` uses: explicit env var first,
framework convention second, localhost fallback. No new secret or chart wiring.

## Status endpoint

```
GET /api/model_entries/<model>/<pk>/calculation-status
```

```json
{
  "status": "IN_PROGRESS",
  "error": null,
  "started_at": "2026-08-04T12:04:11Z",
  "finished_at": null,
  "duration_seconds": null,
  "log": ["Loading positions…", "Valuing 1,204 rows…"],
  "log_truncated": true
}
```

- Applies **the same read permission as fetching the record**, reusing the existing
  permission path rather than a parallel check. A status response that confirms a
  record exists and errored, to someone who cannot read the record, is a real leak.
- `started_at` / `finished_at` / `log` come from `CalculationLog`. There is no
  timestamp on the record, and since #675 `edited_at` deliberately is not one.
- `log` is bounded to the last N lines; `log_truncated` reports whether more exist.
  Omitted entirely unless requested, so the default widget never pays for it.

## Data flow

```
first render ─── GET status ──▶ draw from design tokens
                                   │
click "Calculate" ── PATCH ?calculate=true ──▶ accepted / error
                                   │
                        mark widget "expecting IN_PROGRESS"
                                   │
        @st.fragment(run_every=2s) ─── GET status ──▶ redraw
                                   │
                    terminal status? ── yes ──▶ stop polling, final redraw
```

Three properties that must hold:

1. **Polling runs only while work is in flight.** `run_every` becomes `None` on a
   terminal status, so an idle dashboard with ten widgets makes zero requests.
2. **The trigger is the React path, unchanged.** Same permissions, same audit
   actor, same history, same `_defer_calculate_hook` suppression.
3. **Only the fragment reruns.** The author's script is not re-executed, so a heavy
   dataframe above the widget does not reload every two seconds. This is the whole
   reason polling is viable here.

## Styling

All colours, fonts and spacing come from `lex.lex_app.design_system` (vendored in
`5595a9a2`): `SUCCESS`, `ERROR`, `WARNING`, `MUTED`, `BORDER`, `SURFACE`,
`FONT_BODY`. No literal hex in the widget. Note `SUCCESS` is teal `#14B4B4`, not
green. CI already gates token freshness, so the widget tracks the design system
automatically.

## Error handling

| Condition | Widget behaviour |
| --- | --- |
| No calculate permission (403 on PATCH) | Button **disabled** with reason |
| No read permission (403 on status) | Muted "Not available" — must not confirm the record exists |
| Record gone (404) | "Record not found" |
| Already `IN_PROGRESS` on first render | Button disabled, Running badge, polling starts immediately |
| Backend unreachable / 5xx | Inline error; dashboard keeps rendering |
| Token expired mid-poll | One refresh via existing `streamlit_app.py` machinery, then "Session expired — reload" |
| Calculation failed | `ERROR` + `calculation_error_message` |
| `ABORTED` | Distinct badge + "this was interrupted — run again" nudge |
| `CANCELLED` | Distinct badge, no nudge |

**No failure path may raise out of the widget.** Streamlit renders top-to-bottom;
an exception kills everything below it on the page.

## Test plan

Two clusters, because the surfaces are in two domains.

### Cluster 1 — the widget (letter `ab`, scenarios 1.223+, type U)

Cluster 1 owns the Streamlit helpers (`lex_view` is tested there). The HTTP client
is faked at the boundary — no live server.

- default renders the card; each `show_*` flag adds/removes exactly its element
- polling starts only on a non-terminal status
- **polling stops on a terminal status** — the regression that would otherwise
  hammer the backend forever
- click issues `PATCH ?calculate=true` once, not once per rerun
- 403 / 404 / 5xx / unreachable each render their state and **raise nothing**
- `ABORTED` and `CANCELLED` render distinctly from `ERROR`; `ABORTED` shows the nudge
- `show_log=False` never requests log data
- no literal hex in the module — colours resolve from `design_system`

### Cluster 10 — the endpoint (letter `o`, scenarios 10.72+, type E)

Drives the real endpoint through `APIClient`.

- returns the documented shape for each of the six statuses
- **read permission enforced** — a user who cannot read the record gets the same
  response as for a missing one
- `log` bounded; `log_truncated` accurate
- `log` absent unless requested
- unknown model / unknown pk
- no N+1 on the `CalculationLog` query

Neither side mocks the other's job: the widget batch pins client behaviour, the
API batch pins the contract.

## Generalisation (the C step)

Not built now. The seam:

- `_client.py` is action-agnostic already.
- The status endpoint becomes "state of action X on record Y" — the widget's
  rendering does not change shape.
- A second widget module (`action.py`) reuses both.

Nothing in this design needs to be undone to get there.

## Out of scope

- Record pickers, field widgets, list-lite widgets
- WebSocket push (polling is sufficient at 2s for calculations measured in tens of seconds)
- Bulk calculate across many records
- Changing `lex_view` or `embed.py` internals
