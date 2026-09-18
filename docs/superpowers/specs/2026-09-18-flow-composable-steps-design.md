# Composable Flow steps

**Date:** 2026-09-18
**Status:** approved, implementing
**Touches:** `lex-app` (`lex/lex_app/streamlit/embed.py`), `process-admin-general-client`
(`src/utils/useEmbedContext.ts`, `src/components/model-components/forms/EditForm.tsx`)

## The problem

`Flow` today is a lookup table, not a sequence. Each key is `"<resource>/<operation>"`
and each value is where to go next, resolved once per save in
`useEmbedContext.getCreateRedirect` / `getUpdateRedirect`. There is no cursor anywhere
in the system, which costs three things an author actually wants:

- **Order.** A chain that visits the same resource and operation twice collides on one
  key. `create t1 → create t2 → create t1` cannot be written down.
- **Loops.** "Keep opening create forms until I stop" has no expression. `STAY` gets
  close but only ever repeats one step, and only because it is a sentinel that means
  "do not navigate" rather than a position that did not move.
- **References.** A step cannot say "edit the record step 4 made". Only `{id}` exists,
  and it always means the record that was just saved.

The target shape, in the author's words:

    Table1_create → Table2_create → Table3(id=3)_update → Table4_create
      → Table4(that id)_update → table view

with `loop` and `loop_last` as alternative endings, and without hand-writing
`"resource/operation"` keys — those stay as the low-level escape hatch.

## The API

`Flow` remains a `dict` subclass. `Flow({"t1/create": "/t2"})`, `after_create`,
`after_update` and `after_save` keep working byte-for-byte; the builder appends to an
ordered `_steps` list alongside the mapping.

```python
flow = (
    Flow()
    .create("table1")
    .create("table2")
    .update("table3", id=3)      # literal id, known when writing
    .create("table4")
    .update("table4")            # no id → the record the previous step produced
    .table("table4")             # terminal
)

Flow().create("investor").loop_last()                # infinite create forms
Flow().create("investor").create("vehicle").loop()   # cycle the pair forever
Flow().create("investor", as_="inv").create("vehicle").update("investor", id=ref("inv"))
```

| Method | Kind | Meaning |
|---|---|---|
| `create(resource, *, as_=None)` | step | open `/<resource>/create` |
| `update(resource, id=None, *, as_=None)` | step | open `/<resource>/<id>` |
| `goto(path)` | step | any route; `{id}` / `{resource}` still interpolate |
| `table(resource=None)` | terminal | `/<resource>`, defaults to the last step's resource |
| `show(resource=None, id=None)` | terminal | `/<resource>/<id>/show` |
| `loop()` | terminal | restart the chain from step 0 |
| `loop_last()` | terminal | repeat the final step forever |

Exactly one terminal per flow. **Omitting it is legal** and means
`table(<last resource>)`, which is what the current code already falls back to
(`withEmbedParams('/' + resource)`), so an un-terminated flow behaves as flows do today.

`as_=` names the step that produces an id rather than naming it from a separate call,
so the binding sits on the line that creates it.

## Wire format

`lex_flow` gains a version and carries a program:

```json
{"v": 2,
 "steps": [
   {"op": "create", "res": "table1"},
   {"op": "create", "res": "table2"},
   {"op": "update", "res": "table3", "id": 3},
   {"op": "create", "res": "table4", "as": "t4"},
   {"op": "update", "res": "table4"},
   {"op": "goto",   "path": "/anything?x=1"}
 ],
 "end": {"kind": "table", "res": "table4"}}
```

A step's `id` has exactly three forms: **absent** (the previous step's record), a
**literal**, or `{"ref": "<name>"}`. `end.kind` is one of `table`, `show`, `loop`,
`loop_last`, `goto`.

Two companion params carry live state, both riding the existing `withEmbedParams`
propagation:

- `lex_step=N` — the cursor.
- `lex_bind={"inv":42}` — named ids, emitted only once something is named.

Serialisation picks the format by content: a non-empty `_steps` emits v2; otherwise the
bare v1 map exactly as today. The escape hatch costs nothing.

Routes follow react-admin v5, which is what the frontend declares: `/<r>`,
`/<r>/create`, `/<r>/<id>`, `/<r>/<id>/show`. Generating them is itself a reason to
prefer the builder — an author hand-writing `/<r>/<id>/edit` is depending on a path
shape react-admin changed between majors.

## Browser runtime

On a successful save at cursor `N`:

1. **bind** — if `steps[N]` carries `as`, record `bindings[name] = savedId`.
2. **look ahead** — `next = steps[N + 1]`.
3. **resolve** — build `next`'s route, taking its id from its literal, its `ref`, or
   the id just saved.
4. **write** — the target carries `lex_step=N+1` and the updated `lex_bind`.

Past the last step, the terminal fires:

| `end.kind` | Behaviour |
|---|---|
| `table` / `show` / `goto` | navigate, **flow params dropped** |
| `loop` | cursor → 0, bindings cleared — a new iteration is a fresh run |
| `loop_last` | cursor unchanged, return `SELF_REDIRECT` |

`loop_last` reusing `SELF_REDIRECT` is what keeps this small. `CreateForm` already
remounts the form on that sentinel and `EditForm` already refetches; infinite create
forms is the existing `STAY` path with a cursor that does not move.

**Dropping the flow params at a terminal is load-bearing.** `withEmbedParams`
re-appends them unconditionally today. A terminal that kept them would land the user on
a table view still carrying the program, and their next create from that table would
re-enter the flow at whatever the cursor said. `withEmbedParams` therefore gains an
options argument: `withEmbedParams(path, { flow: false })`.

### Liveness: the cursor's presence is the flow

`lex_flow` is snapshotted in `lexAppBridge.ts` and survives the router clearing the
query string. The cursor deliberately is **not** snapshotted — `lex_step` and `lex_bind`
match neither `SNAPSHOT_PREFIXES` nor `SNAPSHOT_EXACT`, and that exclusion is
load-bearing rather than incidental.

So when a user leaves the flow's path — cancels a form, clicks something in the app —
the program is still in the snapshot but the cursor is gone from the live URL. **An
absent cursor means the flow is over**, and resolution falls back to today's behaviour.
Reading it as "step 0" instead would silently restart the sequence from the beginning,
which is a worse failure than ending: the user would be walked through the whole chain
again with no way to tell why.

Python always emits `lex_step=0`, so absence is unambiguous.

Two properties follow for free: an iframe reload resumes correctly, because the entire
state is in the URL; and browser Back rewinds the flow, because the previous URL carries
the previous cursor.

## Error handling

**Author time**, extending the rule the module already states — "validated when written,
not when they fail to fire". Every one of these raises `FlowError` at the call that
wrote it:

| Condition | Why |
|---|---|
| `update()` with an implicit id as the first step | nothing before it produced a record |
| `update()` with an implicit id after a `goto` | a `goto` produces no record |
| `ref("x")` before any step declared `as_="x"` | references point backwards only |
| duplicate `as_` name | the second silently shadows the first |
| any step or second terminal after a terminal | the flow already ended |
| mixing `after_create(...)` with `.create(...)` | two models meaning different things; picking one silently is how a flow half-works |

**Runtime**, in the browser. Every failure degrades to today's default redirect and says
so once on the console — the existing `parseFlowParam` contract, which chose a warning
over a silent `{}` precisely because "a bad flow looked exactly like no flow":

- `v` is not a version this build understands → warn, ignore the flow.
- cursor out of range → warn, treat as flow over.
- `ref` names a binding that is not in `lex_bind` → warn, fall back to the saved id.
- an implicit id where the save returned none → warn, fall back to the resource's table.

## Compatibility

v1 flows parse unchanged and take the same code path they take today. The frontend
branches on `v`: absent means v1, `2` means the program. A v2 flow reaching an older
frontend hits the "not a version I understand" branch above and degrades to the default
redirect rather than misbehaving — which matters, because backend and frontend ship as
separate artefacts and can be a version apart.

## Prerequisite fix

`EditForm.tsx` runs `onSuccessOverride` and returns **before** checking `SELF_REDIRECT`,
where `CreateForm` checks the sentinel first and documents that ordering as deliberate:
a host that asked to stay wants the panel to stay open and clear. Today the divergence
costs `after_update(x, STAY)` inside a drawer. Under this design it costs every
`loop_last` that ends on an update, so the reorder is in scope rather than a drive-by.

## Testing

**Python** — a new batch in cluster `01-init`, scenarios from 1.342. Covers: each
builder method's serialised shape; the v1/v2 format switch including that a
builder-free `Flow` still emits byte-identical v1; every row of the author-time table
above; `lex_step=0` on the emitted URL; and the terminal defaulting.

**Frontend** — vitest alongside the existing `useEmbedContext.test.tsx`. Covers: the
advance at each step of the spec's own example chain; both loop terminals; bindings
across a `ref`; the liveness rule (program present, cursor absent → today's default);
flow params dropped at a terminal and kept mid-flow; and each runtime degradation.

These run in PAC's CI. This checkout has no `node_modules` and no `NPM_MARMELAB_TOKEN`,
so they are written but not executed locally; that is stated rather than implied.

## Out of scope

- **Branching on data** — "if the saved record has X, go to Y". Needs Python between
  steps, which means the `flow_step` event firing and a `navigate` command on the
  `lex-app-host` protocol. This design leaves room for it: both are additive.
- **Delete redirects** — still refused by `_validate`, still for the same reason.
- **The published docs.** `docs/access-and-dashboards/` is mirror-owned
  (`docs/.docs-sync.yml`) and read-only in this repo; `docs_mirror_guard.yml` rejects
  local edits. The `lex_view callbacks.md` update belongs upstream in `lex-app-docs`.
