# The calculation log, reachable after the run

**Date:** 2026-09-23 (amended the same day and on 2026-09-24 — see *Amendments*)
**Status:** approved
**Touches:** `lex-app` (a list annotation — no migration), `process-admin-general-client`
(status cell, a side-drawer shell, the log drawer)

## Amendments

The first version of this spec was approved and then checked line by line against the source while
the implementation plan was written. Five things it said were wrong. Two were decided by the user;
the other three are corrections of fact. A sixth was found later, by the final review of the built
branch: §3 assumed the drawer's cell survives the end of a run, and it does not. A seventh was found
by the user on a real table: a failed run leaves no log, and its trace lives in the audit trail.
An eighth was the user's call after using it: a popup, not a drawer.

1. **Which run a row opens** *(decided by the user)*. The first version resolved "the latest run a
   record appears in" through the `GenericForeignKey`. The frontend already has a tested resolver,
   `useResolvedCalculationId`, that finds "the newest run *started from* this record" by the
   `calculationId` prefix `<model>_<pk>_`. Two rules would let one row open different runs in the
   table and the widget. This spec now uses the existing rule.
2. **No migration.** That rule is served by the index `calculationId` already has. The first
   version's `(content_type, object_id, -timestamp)` index is not needed.
3. **The ERROR case** *(decided by the user)*. The first version claimed a failed run's log
   already carries its `Error:` lines. It does not — nothing in the framework writes one on failure.
   `update_calculation_status` only *broadcasts* the stack trace over the websocket; the one
   persisted copy is `calculation_error_message` (or `error_message`), written only when the model
   declares that field. That field — the traceback the request asked for — was deferred on the false
   premise. It is now in scope.
4. **The cell is two cells in the grid.** `CalculateFunctionality` renders twice there:
   `variant='status'` in the Calculation column (pill only) and `variant='action'` in the Actions
   column (play only). Today's only door, the spinner, lives in the *Actions* column.
5. **The running body already exists.** `CalculationLogStream` is body-only by design and shares the
   cell's socket through the same registry. The drawer hosts it rather than wiring a socket itself.
6. **The drawer cannot live in a cell** *(found by the final review)*. The grid remounts itself when
   a run finishes, and again on every create, update and delete of the resource: each of those fires
   the grid's refresh event, and the grid's React key carries a counter that event bumps. Anything a
   cell renders is destroyed with it, so a drawer opened from the cell closed at the very moment it
   exists for — and whenever any other row of the model finished. The drawer is now hosted once, in
   the layout, outside every grid; the log button opens it through a small store, and the host
   refreshes its record the way `CalculationWidget` does. The same review found the resolver ranking
   a stale id above the run it had just watched: when a run ends, the live entry is removed before
   the row is refetched, and for that moment the row still names its previous run. The resolver now
   ranks an id it watched go live above the record and the query, because a live id is the run its
   record is part of right now — its own, or a cascading parent's. The `'IN_PROGRESS'` placeholder the
   status socket stores, when the server does not say which run is live, names no run, but it still
   keys the live stream (the log socket listens per record), so it is handed on while live and never
   kept past the run. The re-review of that fix added one more rule: a reopened drawer starts from the
   row its button handed over, not from a record cached by an earlier open.
7. **A failed run leaves no log, and the audit trail keeps its trace** *(found by the user, on a real
   table)*. The log button showed on one of two ERROR rows, and neither showed a stack trace. A
   calculation that fails inside its transaction (`is_atomic`) is rolled back with every log line it
   wrote, because `CalculationLog` persists on commit. So "the row has a log" was false for exactly the
   rows that failed, and the one failed row with a door opened an *earlier*, successful run's log under
   today's failure. A model with no `calculation_error_message` / `error_message` field keeps no
   traceback on the row either. The audit trail is written outside that transaction:
   `ensure_terminal_calculation_audit` gives each run's newest `AuditLog` its terminal status and, on
   failure, the stack trace. So the door now shows on every row that has run, and a drawer on a failed,
   aborted or cancelled row asks the audit trail for the newest run from this row that ended that way
   (filtering on the terminal status, so a later plain edit cannot hide it). It shows that run's trace
   when the row has no field, and that run's log — or, when it left none, says so. *(From the PR review:)*
   the log such a run wrote before it failed is no longer lost. The live cache is not transactional
   and holds the run's whole log until the run ends, so both failure paths write it back as the run's
   log (`CalculationLog.keep_rolled_back_log`) just before they purge the cache.
8. **A popup, not a drawer** *(decided by the user)*. After using it: "make them look like a popup
   rather than that drawer", with a failure reading like the traceback the audit log's "Show" opens.
   The log now opens in a dialog on the same Paper as that traceback popup, with a remembered
   full-screen toggle in place of the full-width one. The traceback body is one shared view, used by
   both popups, and it puts the exception on top. A failure opens on its traceback, and the log is a
   tab away. The host, store and completion behaviour of Amendment 6 are unchanged; only the shell
   changed, and with it the names (`CalculationLogPopup`, `CalculationLogPopupHost`).

## The problem, as reported

> "When the calculation is IN_PROGRESS, we can already access the logs that are streaming, and
> once they are done, we lose the logs and we would have to do a lot of steps to get them."

That sentence is literally true, for two separate reasons — one in the frontend, one in the
lifecycle of the data.

**The only door to the log is the spinner.** In the grid's Actions column, `CalculateFunctionality`
renders either a clickable spinner (only while running) or the play button:

```tsx
{!showButton ? null : shouldShowSpinner && !suppressLogViewer ? (
  <Tooltip title='See calculation logs'>…spinner, clickable…</Tooltip>   // only while running
) : (
  <IconButton><PlayArrowIcon /></IconButton>                              // otherwise: run
)}
```

When the run ends the spinner is replaced by the play button, and the door goes with it.

**The live log is deliberately destroyed at completion.** While a calculation runs its log is served
from cache: `InitCalculationLogs` reads `CacheManager`, and the websocket streams on top. When the
*root* calculation finishes, `CalculationModel` purges that cache:

```python
if is_root:
    cleanup_result = CacheManager.cleanup_calculation(calculation_id=calc_id)
```

So even if the door had stayed, the endpoint behind it returns `{"logs": ""}` from that moment.

The durable copy is not lost. It is in the `CalculationLog` table, grouped into runs by
`calculationId`. What is missing is any path from a calculation row to it: the row carries
`is_calculated` and nothing else.

## What already exists, and is reused

All verified in the source:

- **The run id convention.** The frontend mints every run's id as
  `` `${model}_${pk}_update_${uuid4()}` `` (`CalculateFunctionality`), so a run started from a row
  always begins `<model>_<pk>_`. The trailing underscore is load-bearing: without it, record 1 would
  match record 11's runs.
- **A resolver built on it.** `useResolvedCalculationId(model, pk, record?)` resolves a record's run
  from, in order: the live Redux entry, `record.calculation_id`, then a `calculationlog` query for
  the newest `calculationId__startswith: "<model>_<pk>_"` sorted by `id DESC` — and it remembers
  what it resolved across the moment a run completes.
- **A durable, embeddable log view.** `CalculationLogTree` reads the `calculationlog` table (not the
  cache) and accepts `calculationId`, `height` and `embedded` props.
- **A body-only live view.** `CalculationLogStream` renders the live stream without a Dialog, and
  acquires its socket through `acquireLogSocket` — the same reuse-or-create registry, keyed
  `<record>-<calcId>`, that `CalculateFunctionality` uses.
- **A drawer.** `FormDrawer` is the right-anchored panel the create and edit forms already use.

So the gap is narrow: **a calculation row does not carry its latest run's id**, so the grid cannot
know which rows have a log to open, and nothing in the grid offers a door to it.

## The design

### 1. The cells

**Calculation column** (`variant='status'`): the pill, unchanged, plus **a log button**
(`ArticleIcon`) at the cell's trailing edge. It renders when the row has a log (see §4) or is
running; it is tinted `error.main` on ERROR, so the cell itself says the door leads somewhere bad.
Its slot is always reserved, so doors form one straight line down the column whatever the pill's
width, and a row without a log leaves a gap rather than shifting anything.

**Actions column** (`variant='action'`): **the play button becomes permanent** — always rendered,
disabled while running. **The `ReactLoading` spinner is removed.** It repeated IN_PROGRESS, which the
pill already shows with its own `CircularProgress`, and it was the only door, which is the whole bug.

**Widgets** (`variant='full'`): pill, log button, play button, in that order, same rules.

Not rendered at all: in history view (a past version has no live log, and history rows are not
annotated), and when `suppressLogViewer` is set (the widget already shows its own stream beside the
control).

One log button, not two: a traceback is part of *what happened in this run*, and it is shown inside
the same drawer as the log rather than behind a second door.

### 2. Behaviour per status

| Status | Log button | The drawer shows |
|---|---|---|
| `IN_PROGRESS` | shown | the **live stream** |
| `SUCCESS` | shown if a log exists | the finished log |
| `ERROR` | shown if a log exists, **red** | the recorded failure first, then the log |
| `ABORTED` | shown if a log exists | a note that the worker stopped, then the log up to where it stopped |
| `CANCELLED` | shown if a log exists | *stopped by a person*, then the log up to the cancel |
| `NOT_CALCULATED` | slot reserved, empty | — |

**The failure headline comes from the row.** On ERROR the drawer leads with
`calculation_error_message`, then `error_message` — the framework's own priority order — when the row
carries either. That text is `f"{exception_details}\n\n{stack_trace}"`: its first line is the
headline, the whole text sits beneath it in monospace. On models that declare neither field there is
no durable traceback anywhere, and the drawer says so in plain words rather than showing nothing.

**ABORTED has no traceback, by construction.** It is set at startup recovery after a row is found
stuck in `IN_PROGRESS`; nothing raised. Saying so beats an empty panel, which reads as broken.

**CANCELLED is not dressed as an error.** A person stopped it on purpose.

### 3. The drawer

`FormDrawer` is split. Most of what it carries is a react-admin `sx` cascade that only makes sense
around an Edit or Create card.

- **`SideDrawer`** — the shell: right-anchored panel, header strip, icon, title, id pill, close, and
  an optional expand toggle.
- **`FormDrawer`** — `SideDrawer` plus the form cascade. Its props and callers do not change, and its
  existing baseline tests (F5.60–F5.63) must pass untouched.
- **`CalculationLogDrawer`** — `SideDrawer` hosting the headline, then `CalculationLogStream` while
  running or `CalculationLogTree` once finished.

**Widths.** Forms keep `min(640px, 90vw)`. The log opens at `min(1000px, 92vw)` — it carries
tables, headings and code blocks laid out for the old `maxWidth="lg"` dialog — and **the toggle
expands it to the full viewport width**. The choice is remembered per viewer in `localStorage`.
Storage failures (private mode, blocked site data) fall back to the default width and never throw.

**No second socket.** `CalculationLogStream` acquires its socket from the shared registry under the
same key the Actions column uses, so it reuses that socket when one is open and opens exactly one
when the Actions column is hidden.

**Which id the drawer opens.** The drawer resolves it with `useResolvedCalculationId`, passing the
annotated id in as `record.calculation_id`, so a finished row opens without a query of its own. The
order is: the live id while running; then an id the drawer watched go live; then the record's id;
then the `calculationlog` query; then whatever it resolved last. The second place is the part that
matters. When a run ends, the live Redux entry is removed before the row is refetched, and for that
moment the row still names its previous run. Ranked below the record, the id the drawer had just
watched would lose to that stale one, and the drawer would show the previous run's log in the second
this run's log became available. A live id is the run its record is part of right now — its own,
or, for a calculation cascaded from a parent, the parent's run, where its lines are logged — so
nothing the record says can be newer. Nor can the query outrank it: react-query answers from its
cache the instant the query is enabled, and a run that wrote no log row makes the query answer the
previous run. (A drawer opened after a run uses the prefix rule instead, so a cascaded child shows its
parent's run only while watched.) When the server does not say which run is live, the status socket
stores the placeholder `'IN_PROGRESS'`. It names no run, but the log socket listens per record — it
joins the group named by the part of the key before `-` — so a stream keyed on the placeholder still
carries the running calculation's lines, on the socket the Actions column opened with it. The resolver
hands it on while live and never keeps it, so a finished run cannot resolve to it.

**Run end, drawer open.** A cell cannot host the drawer. The grid remounts when a run finishes, and
on every create, update and delete of the resource, so anything a cell renders is destroyed with it.
The drawer is therefore hosted once, in the layout, outside every grid — in the embedded layout too.
The log button puts the row into a small store and the host renders the drawer for it, keyed by the
row so that switching rows starts clean. The drawer belongs to the table it was opened from, so the
host closes it when the page changes. The host fetches the record itself and shows the row the
button handed it until that fetch lands — a fetch made since the drawer opened, not a record
react-query kept from an earlier open, which for a row re-run in between would be the previous run's
status and failure. A record fetched one at a time carries no run id (only the grid's list is
annotated), so the host keeps the row's run id rather than send the resolver to the query. It
refreshes the record the way `CalculationWidget` does:
on the refresh event for its model or a global one, and when the row's live entry disappears. It
derives the status from the same inputs as the pill — the live entry and `is_calculated` — so the
two cannot disagree. When the status moves from `IN_PROGRESS` to terminal, the body swaps from the
stream to the tree for the same id, and the refreshed record brings the failure headline with it.

**No duplicate title.** `CalculationLogTree` gains one prop, `showTitle` (default `true`). The
drawer passes `false`: its own header already says what this is, and two "Calculation Log" bars
stacked on top of each other is exactly the kind of seam this work exists to remove.

**A failed load says so.** The tree reads only `{ data, isPending }` from `useGetTree` today, so a
failed fetch renders an empty tree — indistinguishable from a run that logged nothing. It gains an
error message with a retry, which is what the error-handling rule below needs from the one component
that fetches the finished log.

### 4. The data: a calculation row learns its latest run

The list endpoint annotates calculation-model rows with two reserved fields, as it already annotates
audit log rows with `lex_reserved_has_calculation_log`:

- `lex_reserved_calculation_id` — the `calculationId` of the newest run started from this record;
- `lex_reserved_has_calculation_log` — whether such a run exists.

**The rule mirrors `useResolvedCalculationId` exactly**: `calculationId` starting with
`f"{model._meta.model_name}_{pk}_"`, newest by `id`. One rule, stated in two languages, so the table
and every widget open the same run for the same row.

**One query per page.** A single `CalculationLog` query ORs one `startswith` per row on the page and
collapses to one row per run (`values("calculationId").annotate(newest=Max("id"))`); the newest run
per record is picked in Python. That returns one small row per *run*, never one per log line, and
never one query per grid row.

**Matching is by longest prefix.** For string primary keys that contain underscores, a run id can
start with two of the page's prefixes (`m_a_` and `m_a_b_`). The longest match is the record that
started it: record `a`'s own runs begin `m_a_update_`, so only record `a_b` produces `m_a_b_…`.
Integer keys cannot collide.

**Served by an existing index.** `calculationId` is `db_index=True` (migration `0005`). On
PostgreSQL, Django creates a companion `text_pattern_ops` index for an indexed text field, and that
is what serves `LIKE 'prefix%'`. No migration is part of this work.

**Where it runs.** The generic leaf path of the grid endpoint (`List._execute_leaf_level`)
materialises the page for calculation models, annotates it, then serializes it — the shape the audit
log branch directly above it already uses. The two fields are declared only on serializers of
`CalculationModel` subclasses (`model2serializer` and `_wrap_custom_serializer`) and are listed in
`LexSerializer._SYSTEM_FIELDS`, without which the visibility filter in `to_representation` would
strip them silently.

## Error handling

- **Annotation failure** must not take the list down. If the query raises, the page is served with
  the two fields as `null` / `false` and the log button is absent: a missing door is recoverable, a
  table that will not load is not.
- **A log that fails to load** in the drawer shows an error inside the drawer, never a blank panel.
- **The run-end swap** only fires on the drawer's own row. It is driven by that row's status prop,
  so another row's update cannot repaint it.

## Testing

**lex-app, cluster `15-calculation_logging`:**

- **The regression, in one sentence:** after `CacheManager.cleanup_calculation` has run, the grid
  endpoint still returns the row with its run's `lex_reserved_calculation_id`, and the log rows for
  that id are still there.
- The newest run by `id` wins when a record has several.
- The trailing underscore holds: record 1 never resolves record 11's runs.
- A whole page is annotated in one query (`assertNumQueries`).
- A record never run annotates `null` / `false`; a non-calculation model's rows carry neither field.
- An annotation failure still serves the page.
- On PostgreSQL, the `calculationId` pattern index exists — the guard against someone removing
  `db_index` and silently turning every page load into a scan.

**PAC — cells in `F07-calc_status`, drawer in `F12-embed_streamlit`:**

- The door follows the annotation or the live run, not the status; it is absent in history view and
  under `suppressLogViewer`; its slot is reserved when empty; it is red on ERROR.
- The Actions column's play button is present and disabled while running. The existing test that
  pinned the spinner is rewritten, with the correction in its name — it asserted the bug.
- The headline: ERROR with `calculation_error_message`, with only `error_message`, with neither;
  ABORTED; CANCELLED; nothing for the rest.
- The drawer shows the stream while running, the tree once finished, swaps on the row's terminal
  transition, and keeps its id across the completion gap.
- `SideDrawer` expands to full width, persists the choice, and survives a throwing `localStorage`.
- `FormDrawer`'s F5.60–F5.63 pass unchanged.
- `CalculationLogTree` with `showTitle={false}` renders no title bar; a failed fetch shows an error
  and a retry rather than an empty tree.

## Out of scope

- **Making every failed run carry its traceback in its log.** Writing the error into the run's
  `CalculationLog` would make ERROR universal, but it touches the `except` block in
  `CalculationModel` — which also handles cancellation, cache cleanup and terminal-state
  persistence — and changes what every log contains, including the tree and the PDF export. A
  follow-up of its own.
- **Views that hide the Calculation column.** Removing the spinner leaves a view showing only the
  Actions column with no door to the live log. That view has lost nothing it can't get back by
  showing the column; noted so it is a decision, not a surprise.
- The audit log table — reported as already fine.
- Retiring `CalculationLogDialog` and the separate `calculation_log` / `calculation_id` columns. They
  become redundant, but removing a column users may have in saved views is its own decision.
