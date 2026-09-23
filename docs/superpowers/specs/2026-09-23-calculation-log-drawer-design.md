# The calculation log, reachable after the run

**Date:** 2026-09-23
**Status:** approved design, awaiting spec review
**Touches:** `lex-app` (list annotation, one index migration), `process-admin-general-client`
(status cell, a side-drawer shell, the log drawer)

## The problem, as reported

> "When the calculation is IN_PROGRESS, we can already access the logs that are streaming, and
> once they are done, we lose the logs and we would have to do a lot of steps to get them."

That sentence is literally true, and it is true for two separate reasons — one in the frontend
and one in the lifecycle of the data.

**The only door to the log is the spinner.** `CalculateFunctionality` renders the status cell,
and after the pill it renders exactly one of two things:

```tsx
{!showButton ? null : shouldShowSpinner && !suppressLogViewer ? (
  <Tooltip title='See calculation logs'>…spinner, clickable…</Tooltip>   // only while running
) : (
  <IconButton><PlayArrowIcon /></IconButton>                              // otherwise: run
)}
```

The spinner opens the live log. When the run ends the spinner is replaced by the play button, and
the door goes with it.

**The live log is deliberately destroyed at completion.** While a calculation runs, its log is
served from cache: `InitCalculationLogs` reads `CacheManager` keyed by record and calculation id,
and the websocket streams on top. When the *root* calculation finishes, `CalculationModel` purges
that cache:

```python
if is_root:
    cleanup_result = CacheManager.cleanup_calculation(calculation_id=calc_id)
```

So even if the door had stayed, the endpoint behind it returns `{"logs": ""}` from that moment.

The durable copy is not lost. It is in the `CalculationLog` table, linked to the record by a
`GenericForeignKey` (`content_type`, `object_id`) and grouped into runs by `calculationId`. What is
missing is any path from a calculation row to it: the row carries `is_calculated` and nothing else,
so today reaching a finished log means going to the audit log table, finding the entry, and
following it to `/calculation_log_tree`.

## What already exists, and is reused

Two things this design does **not** need to build, both verified in the source:

- **A durable read path.** `CalculationLogTree` reads the `calculationlog` resource — the table,
  not the cache — through `useGetTree('calculationlog', { filter: { calculation_id } })`. It is
  what the audit log's "Calculation Log" button opens today.
- **An embeddable log view.** The same component accepts `calculationId`, `height` and `embedded`
  props, and `EmbedWidgetHost` already hosts it. It was built to live inside something else. It is
  also the richer of the two log views: sections, collapse, copy, export.

So the gap is narrow and specific: **a calculation row does not know its own `calculation_id`**,
and nothing in the table offers a door to it.

## The design

### 1. The status cell

The pill is unchanged. Around it:

- **A log button** (`ArticleIcon`) between the pill and the play button, rendered when the row has
  a log. Tinted `error.main` when the status is ERROR, so the cell itself says the door leads
  somewhere bad. When there is no log the icon is absent but **its slot is reserved** — otherwise the
  play button shifts left on `NOT_CALCULATED` rows and the column stops reading as a column.
- **The play button becomes permanent.** Always rendered, disabled while running. Today it is
  mutually exclusive with the spinner.
- **The `ReactLoading` spinner is removed.** It repeated IN_PROGRESS — the pill already carries a
  `CircularProgress` for that state — and it was the only door, which is the whole bug.

One log button, not two. A failed run's traceback is how its log *ends*, not a separate artefact
stored elsewhere; two icons would be two doors to one room, and a user would have to choose between
them before knowing what they are looking for.

### 2. Behaviour per status

| Status | Log button | The drawer shows |
|---|---|---|
| `IN_PROGRESS` | shown | the **live stream**, as the spinner's dialog does today, over the socket the cell already holds |
| `SUCCESS` | shown | the finished log, from the table |
| `ERROR` | shown, **red** | the finished log, **led by its last `Error:` entry** |
| `ABORTED` | shown | the log up to where it stopped, headed by a note that the worker died and the framework gave up |
| `CANCELLED` | shown | the log up to the cancel, headed *stopped by a person* — not dressed as an error |
| `NOT_CALCULATED` | slot reserved, empty | — never run, so nothing to open; the only control is run |

**ERROR needs no new field.** `CalculationLog` stores messages as `"Severity: Message"` —
`ERROR = "Error: "` is one of the prefixes, and the module docstring states the format is
load-bearing. A failed run's log therefore already contains its failure lines, and the drawer can
lead with the last one. `calculation_error_message` / `error_message` — written by
`CalculationModel` only when a subclass happens to define the field — is not required. Surfacing it
where it exists is a possible later enhancement, deliberately out of scope here.

**Where the headline is computed.** `CalculationLogTree` already fetches the run's rows itself, so
it — not the drawer — renders the heading, from a new optional `status` prop. For ERROR it scans the
data it already holds for the last `Error:`-prefixed message in document order; for ABORTED and
CANCELLED it renders the fixed heading. The drawer passes the status and nothing else. A second
fetch of the same rows purely to find one line is the thing this avoids.

**ABORTED has no traceback, by construction.** It is set at startup recovery after a row is found
stuck in `IN_PROGRESS`; nothing raised, so there is nothing to show. Saying so beats an empty
panel, which reads as broken.

**The button's presence follows the data, not the status.** A row shows a log button when the
annotation below says a log exists, whatever its status. A `SUCCESS` row whose log was cleaned
away shows none, rather than a door into an empty room.

### 3. The drawer

`FormDrawer` is split rather than reused whole. Its docstring says it was extracted so a second
drawer would not duplicate its chrome — but most of what it carries is a react-admin `sx`
cascade that only makes sense around an Edit or Create card.

- **`SideDrawer`** — the shell: right-anchored panel, header strip, icon, title, id pill, close,
  and the new expand toggle.
- **`FormDrawer`** — becomes `SideDrawer` plus the form cascade. Its callers do not change.
- **`CalculationLogDrawer`** — `SideDrawer` hosting `CalculationLogTree` for a finished run, or the
  live stream for a running one.

**Widths.** Forms keep `min(640px, 90vw)`. The log opens wider — `min(1000px, 92vw)` — because it
carries tables, headings and code blocks laid out for the old `maxWidth="lg"` dialog. **The toggle
expands it to the full viewport width**, and the choice is remembered per viewer in `localStorage`:
someone who reads logs all day should not re-expand on every row. Storage failures (private mode,
blocked site data) fall back to the default width; they never throw.

**While running, no second socket.** The drawer is handed the socket `CalculateFunctionality`
already holds (`wsRef`), exactly as the spinner's dialog is today. That file carries explicit
comments about socket sharing; a drawer that quietly opened its own would double the connections
on a busy table and nobody would notice until it mattered.

**Run end, drawer open.** A user watching the live stream in the drawer when the run finishes
should not be left looking at a stream that will never receive another line. The drawer is rendered
by `CalculateFunctionality`, which already tracks the row's status live over its socket, and
receives that status as a prop. When the prop moves from `IN_PROGRESS` to a terminal status, the
drawer swaps its body from the stream to `CalculationLogTree` for the same `calculation_id`. It
learns of the change the way the pill does, so it cannot disagree with the pill beside it.

### 4. The data: a calculation row learns its `calculation_id`

The list endpoint annotates calculation-model rows with two reserved fields, the same way it already
annotates audit log rows with `lex_reserved_has_calculation_log`:

- `lex_reserved_calculation_id` — the `calculationId` of the latest run this record appears in;
- `lex_reserved_has_calculation_log` — whether that run has log rows.

The existing audit log annotation (`List._annotate_has_calculation_log`) is the easy version:
audit log rows already carry `calculation_id`, so it is one existence check. Calculation rows do
not, so the id is derived through the generic relation — the latest `CalculationLog.timestamp` for
`(content_type, object_id)`.

**It is one query per page, not one per row.** A `Subquery` over `CalculationLog` ordered by
`-timestamp` is annotated onto the page's queryset, so the database resolves the latest run per
record inside the main query. A per-row lookup would turn a hundred-row page into a hundred
requests.

**It needs an index, and that is a migration.** `CalculationLog` today indexes `calculationId` and,
implicitly, the `content_type` foreign key — nothing else. The generic relation's `object_id` is
not indexed, so the subquery would filter an append-only table by `content_type` alone, on every
page load of every calculation table. The migration adds
`Index(fields=["content_type", "object_id", "-timestamp"])`, which serves "latest for this record"
directly and is the index Django's own documentation recommends for a generic foreign key.

**Which run is "the latest".** A record appears in a run either as its root or as a child of
another record's calculation. The annotation takes the latest run the record appears in at all,
and the drawer opens that run's whole tree. When a record was last calculated as part of a larger
run, that larger run is the honest answer to "what happened the last time this was calculated".

**Non-integer primary keys.** `CalculationLog.object_id` is a `PositiveIntegerField`, so a model
with a UUID or string primary key cannot be linked by this relation at all. Such rows annotate as
having no log — the button is absent, the slot reserved — rather than raising.

## Error handling

- **Annotation failure** must not take the list down. If the subquery errors, the rows are served
  without the two fields and the button is absent: a missing door is recoverable, a table that will
  not load is not.
- **A log that fails to load** in the drawer shows an error state inside the drawer and a retry,
  never a blank panel — the same distinction `CalculationLogStream` draws between "nothing yet" and
  "could not fetch".
- **The run-end swap** only fires on a genuine terminal transition for the drawer's own record. A
  status update for another row must not repaint an open drawer.

## Testing

**lex-app, cluster `15-calculation_logging`:**

- **The regression, in one sentence:** after `CacheManager.cleanup_calculation` has run, the log is
  still reachable from the calculation row — the annotation yields a `calculation_id` and the
  `calculationlog` resource returns its tree.
- The annotation resolves the **latest** run when a record has several, including a run in which it
  was a child.
- The annotation is **one query** for a page, asserted with `assertNumQueries` — the property that
  stops this regressing into N+1.
- A non-integer primary key annotates as no log without raising.
- The migration adds the index, and the latest-run query uses it.

**PAC — cell in `F07-calc_status`, drawer in `F12-embed_streamlit`:**

- The six cell states, including the reserved-but-empty slot on `NOT_CALCULATED`.
- The log button follows the annotation, not the status: a `SUCCESS` row without a log shows none.
- ERROR leads with the last `Error:` entry; ABORTED and CANCELLED carry their own headings.
- The expand toggle reaches full width and persists across reloads; a throwing `localStorage`
  falls back rather than breaking the drawer.
- **Opening the drawer mid-run reuses the cell's socket** — the regression nobody would see.
- An open drawer swaps from stream to tree on its own record's terminal transition, and ignores
  every other row's.
- `FormDrawer`'s existing tests pass unchanged: the split must be invisible to its callers.

## Out of scope

- Surfacing `calculation_error_message` / `error_message`. The log's own `Error:` lines cover
  ERROR; the field exists only on subclasses that define it.
- The audit log table. Reported as already fine.
- Retiring `CalculationLogDialog` and the separate `calculation_log` / `calculation_id` columns.
  Once the status cell is the door they become redundant, but removing a column users may have
  configured into saved views is its own decision, made after this ships.
