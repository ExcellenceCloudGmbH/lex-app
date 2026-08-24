# Streamlit widget host — design

> **Date:** 2026-08-25
> **Supersedes:** PR #692 (closed 2026-08-25)
> **Linear:** LEX-126, LEX-127, LEX-586, LEX-587
> **Branches:** `feat/streamlit-widget-host` off `lex-app-v2` (backend),
> `feat/embed-widget-host` off `feat/frontend-test-plan` (frontend)

## The problem

Put a calculation on a Streamlit page: a Calculate button, its live status, and the
full calculation log — tree, method modal and PDF export — behaving exactly as it does
in the product.

## Why the previous attempt was rejected

PR #692 shipped two renderings of one concept:

- `lex_calculation()` — an iframe embedding a whole React *route* (`show`/`edit`/`list`).
- `lex_calculation_streamlit()` — native Streamlit widgets over `_status_poller.py`.

Two defects, both structural rather than cosmetic:

1. **Emulated push.** 759 lines of polling — watch/peek/lapse, snapshot generations,
   a cache keyed on `(model, pk, include_log)` — approximating what
   `CalculationLogConsumer` already delivers as a socket. It also depended on a subtle
   Streamlit detail: `run_every` is only read when a fragment is *declared*, so
   terminating the poll required a full-app rerun to rebuild the timer.
   `optimistic_status()` compounded it by showing a started state before the server
   confirmed one.
2. **A second implementation.** `calculation.py` restated in Python what the React app
   owns: the status vocabulary (`_PRESENTATIONS`, `ACTIVE_STATUSES`), presentation, and
   `last_run_caption`. Any status the backend adds needs editing in two languages.

## Constraint that shapes everything

`CalculationLogTree.tsx` (671 lines) imports `@react-admin/ra-tree` (a **paid**
enterprise package), `react-admin`'s `Show`/`SimpleShowLayout`/`useGetMany`,
`react-router-dom`, and two Redux store slices.

**The log tree only renders inside a full react-admin app.** It cannot be lifted into a
standalone bundle, so a "lightweight widget bundle" is a rewrite — the duplicate
implementation we just rejected. Verified: `useGetTree('calculationlog', …)` and
`<Show resource='calculationlog'>` take the resource **explicitly**, so no ambient
resource context is required. Only three ambient assumptions need injecting: viewport
height, the calculation id, and navigation.

## Architecture: one host, one runtime

One chrome-less PAC route, `/embed/widgets`, reads a manifest and renders the widgets it
names. Exactly **one iframe per Streamlit page** regardless of widget count — one bundle,
one JS context, one socket set, one auth handshake.

The alternative (one iframe per widget) costs N React runtimes. Measured: each embedded
lex-app document opens **4 WebSockets** plus an auth handshake, so 13 tiles is 13 runtimes
and 52 sockets. That is the inefficiency this design exists to avoid.

This works because the component shim deliberately does not re-point `src` on rerun
(re-pointing would reload the app and wipe state), so the runtime **persists across
Streamlit reruns** instead of being torn down.

### Manifest

```json
{ "version": 1,
  "widgets": [
    {"id": "w1", "type": "calculation", "model": "quarter", "pk": 42,
     "options": {"show_log": true, "serializer": "summary"}}
  ],
  "layout": {"kind": "rows"} }
```

Primary transport is the component `args` on the render event — a manifest can exceed URL
length. A `?manifest=<base64url json>` fallback is also accepted so the route can be
opened directly in a browser without Streamlit, which is the cheapest way to test it.

Unknown `type`, unknown `options` keys and unknown `layout.kind` are **rejected loudly**,
not ignored. A silently dropped widget is the failure mode that wastes an afternoon.

### Python API

```python
with lex_widgets() as page:
    status_a = page.calculation("quarter", pk=42, show_log=True)
    status_b = page.calculation("quarter", pk=43)
# host iframe renders here, once, on __exit__
```

The context manager makes the single-render boundary a language construct. Rejected
alternatives: implicit accumulation plus a required `lex_widgets_render()` (easy to
forget, fails silently); session-state accumulation across reruns (desynchronises the
moment a rerun takes a different branch).

**Accepted cost:** widgets render where the `with` block closes, not where each call sits.
Arbitrary interleaving of `st.write()` between widgets is not available.

### Return values

Opt-in per call site, matching the established `emit_*`/`on_*` convention. Default off, so
the display-only case pays nothing.

`page.calculation(..., on_status=True)` makes the widget emit a `calculation_status`
envelope through the existing `source: 'lex-app'` bridge; the shim forwards it via
`Streamlit.setComponentValue`, and the call returns the latest status envelope. Without the
flag it returns `None` and no envelope is emitted.

## Components

### Backend — `lex-app`

| Unit | Job | Depends on |
|---|---|---|
| `streamlit/widgets/spec.py` | Build + validate one widget spec. **Pure** — no Streamlit import, no I/O. | nothing |
| `streamlit/widgets/host.py` | The `lex_widgets()` context manager; renders once on `__exit__`; returns event values. | shim, spec |
| `streamlit/_widget_host_component/` | Declared component: dependency-free shim hosting the iframe, forwarding `postMessage` both ways. | nothing (no build step) |

`spec.py` is pure so manifest validation is unit-testable without a Streamlit runtime —
that is where the fiddly cases live.

**Python makes no HTTP calls.** It resolves a base URL and builds a manifest; everything
else happens in the browser. So `_client.py` from #692 (bearer token, typed errors) is
**not** carried over after all — an earlier draft of this spec listed it, which was a
leftover from the polling design. Dropping it means the widget has exactly one auth path:
the iframe authenticates the same way `lex_view` already does, by cookie, via
`EmbedXFrameOptionsMiddleware`. A second token path would be a second thing to expire.

### Frontend — PAC

| Unit | Job |
|---|---|
| `pages/EmbedWidgetHost/EmbedWidgetHost.tsx` | Read manifest (args or URL), render layout, mount one widget per entry. Knows nothing about calculations. |
| `components/widgets/CalculationWidget.tsx` | Compose `CalculateFunctionality` + `CalculationStatusPill` + `CalculationLogTreeView` + `CalcLogMethodModal`. |

Registered in `CustomReactAdmin.tsx` alongside the existing
`<Route path='/calculation_log_tree' …>` inside `<CustomRoutes>`.

**Compose, never reimplement.** `CalculationWidget` imports the existing components as
they are. If it reformats a status or re-derives a label, we have rebuilt the rejected
duplicate in TypeScript.

### The one required refactor

`CalculationLogTree` is a page that calls `useFillViewportHeight()` and `useNavigate()`.
Split it:

```
CalculationLogTree.tsx       (page)  → viewport height, back button, routing, searchParams
  └── CalculationLogTreeView.tsx     → the tree; height, calculationId and onNavigate injected
```

The page becomes a thin caller passing what it used to compute internally, so its
behaviour is unchanged. Justified as improvement to code we are touching: 671 lines is
already too large for one file, and this is the boundary that lets both consumers exist
without a second copy.

## Data flow

**Manifest in.** Python builds specs → `host.py` serialises the manifest → shim receives it
on the Streamlit render event → sets the iframe `src` once, then posts the manifest to the
iframe → `EmbedWidgetHost` renders.

**Streaming.** The widget subscribes through the product's own `web-sockets/CalculationLogs.tsx`
against `CalculationLogConsumer`. No Python involvement; no polling. This is why streaming
is free rather than new work.

**Events out.** Widget → `postMessage({source:'lex-app', version:1, type:'calculation_status', …})`
→ shim origin-gates and dedupes by ULID (both already implemented) → `setComponentValue`
→ Python return value.

**Sizing.** The tree is tall and variable. The shim measures content and calls
`Streamlit.setFrameHeight()` on change, so the frame grows with content instead of
scrolling inside a fixed box. This also solves the modal: a frame tall enough for the tree
is tall enough for a modal over it.

**PDF export.** Downloads from a sandboxed frame require `allow-downloads` in the sandbox
attribute — the existing embed sandbox list already includes it. No new mechanism.

## Error handling

- **Bad manifest** → the host renders a visible error naming the offending widget id and
  reason. Never a blank box.
- **Unreachable backend / 401 / 403 / 5xx** → per-widget error state; other widgets on the
  page keep working. One dead widget must not blank the page.
- **No failure path raises into Streamlit.** Streamlit renders top-to-bottom; an exception
  erases every widget below it. Same rule PR #692 got right and worth keeping.
- **Origin gating stays.** The shim drops messages whose origin is not the expected embed
  origin — already implemented, must not regress.

## Testing

Per AGENTS.md directive 2, paired cluster tests land in the same change.

**Backend — cluster `01-init`** (owns Streamlit/proxy/embed surfaces; PR #723 used batch
`1ab`). Next free letter, scenarios continue from `max_scenario + 1`. Coverage:
manifest construction and validation (pure, no Streamlit), the context manager's
render-once-on-exit contract, `on_status` opt-in on and off, unknown type/option rejection.

**Frontend — cluster `F12-embed_streamlit`** (`max_scenario: 22`, letter `a` used).
Coverage: manifest from args and from the base64url URL fallback, widget mount per entry,
per-widget error isolation, `setFrameHeight` fires on content change, `calculation_status`
envelope shape and opt-in gating, and that `CalculationLogTree`'s page behaviour is
unchanged after the split.

## Out of scope

- Widget types beyond `calculation`. The manifest is versioned and typed so others can be
  added without redesign, but none are specified here.
- Restoring `lex_calculation_streamlit`. Deliberately dropped.
- Theme propagation into the host iframe — owned by the concurrent theme work
  (LEX-579); this design must not open a second theme channel.
