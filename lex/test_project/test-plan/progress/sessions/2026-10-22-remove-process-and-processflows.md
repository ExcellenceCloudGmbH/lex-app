---
date: 2026-10-22
clusters: [1, 4, 10]
tests_added: "7 new scenarios (1.406-1.409, 10.97-10.99); 4.90 retired with the endpoint it pinned; 4.105, 4.108 and 10.83 narrowed to the write paths that still exist"
suite_tally: "init or permissions or api_layer: 847 pass / 15 fail / 16 skip. Clusters 4 and 10 are clean; all 15 failures are pre-existing and reproduce identically on the pre-change tree (reflex and streamlit are not installed locally and the frontend bundle is not built). The five reflex-importing modules are skipped outright for the same reason. Gates: coverage pass, doc-imports pass, docs-mirror pass, .github/scripts 619 pass."
---

# Batches 1ax, 10q — the Process and ProcessFlow features removed

The supervisor asked for **everything related to Process and ProcessFlows** to go. Read as the two
named features, not the `process_admin` app: `ProcessAdminSite`, `ModelProcessAdmin` and the model
registry are the framework's routing and registration core — `lex/lex_app/urls.py` mounts the site
as the entire REST API — so they stay, under their own names.

**What was removed.** `lex/core/models/Process.py` (the abstract workflow model) and
`lex/api/views/process_flow/` (the `CreateOrUpdate` step view) are deleted. With them went the
registry and both routes: `processAdminSite.processes` / `registerProcess` /
`get_process_structure_func`, `ModelRegistration._register_process_model` and its branch in
`register_models`, the `ProcessStructure` view in both the modules that carried a copy
(`lex/process_admin/views/model_relation_views.py`, `lex/api/views/ModelStructureObtainView.py`),
the `api/process/<name>` and `api/run_step/<model>/<pk>` routes, the `"Process"` node type in
`lex/process_admin/models/utils.py`, and the `Process` entries in `lex/core/models.__all__` and
`ModelRegistration._RESERVED_CLASS_NAMES`. `lex_context_repo.md` lost its section 22 and the
sections after it were renumbered. `HTMLReport` — the sibling feature built on the same machinery —
was deliberately left alone, which is what the new scenarios check alongside each absence.

Because `Process` left `_RESERVED_CLASS_NAMES`, a project may now call its own model `Process`;
1.408 pins that, and that the names the framework still owns keep raising.

This settles the `CreateOrUpdate` half of pending decision 4 in
[clusters/README.md](../../clusters/README.md) and the matching row in
[cleanup-and-coverage-plan.md](../../cleanup-and-coverage-plan.md) §2d: both had the route flagged
as live and awaiting supervisor confirmation to drop. The other four routes in that cascade
(`api/widget_structure`, `api/logs`, `streamlit-token`, `user_permissions`) are untouched and still
await confirmation — 10.99 pins that they survived.

**Surfaces and where they landed.** The removal is observable in two places, so it is two batches.
The *model* side — import, registry, registration branch, reserved name, structure node type — is
bootstrap, so [1ax](../../clusters/01-init/batches.md) (1.406–1.409, I). The *route* side is the
URL table, so [10q](../../clusters/10-api_layer/batches.md) (10.97–10.99, U), which walks the
project's own URLconf for every `(name, path)` it serves rather than trusting a single `reverse`.
10.99 is the half that matters most for a cascade of this shape: the deleted routes lived in the one
url list the entire frontend is served from, so every neighbouring endpoint is checked to still
resolve.

**Allocation moved on merge.** The route batch was written as **10p / 10.91–10.94**. While this
branch was open, #840 ("File-download endpoints write an audit entry") landed on `lex-app-v2` and
took cluster-10 letter `p` with 10.91–10.96. Merging the base surfaced the collision, so this batch
re-took the next free pair: **10q**. Letters are never renumbered — this one moved before it ever
landed. The merge also reverted four cluster-10 shards to the base copy, with the 10o edits
re-applied on top.

**Existing scenarios that drove `run_step`.** `run_step` was a second write path into every model,
carrying its own copy of the permission checks, so three hardening scenarios probed it. 4.90 pinned
it *alone* and is therefore **retired** — the id is not reused, and 10.97 now pins the endpoint's
absence instead. 4.105 creates its `auth.User` through the CRUD endpoint, 4.108 dropped the
`run_step` entry from its answers dict, and 10.83 dropped the `run_step` probe from `_failures()`;
all three keep every other write path they covered, so cluster 4p and 10o land the same counts as
before and 4o drops 21 → 20.

**Scope note.** An intermediate version of this change also renamed the admin backbone off the
`Process` name (`lex/process_admin/` → `lex/model_admin/`, `ProcessAdminSite` → `LexAdminSite`,
`ProcessAdminTestCase` → `LexAppTestCase`, and the URL namespace). That rename was **reverted in
full** at the supervisor's direction: it touched ~100 files, broke a documented public test base for
downstream projects, and was not what "remove the Process and ProcessFlow features" asked for. The
backbone keeps its names. What remains here is the feature removal alone, and
`lex/lex_app/settings.py` is byte-identical to the base.

**Run:** `python -m lex pytest` over the two new files — **7 pass / 0 fail** (1ax 4, 10q 3).

Over `init`, `permissions` and `api_layer` — **847 pass / 15 fail** — and every one of those 15
fails identically on the pre-change tree (checked by stashing the change and re-running the same
files): `reflex` and `streamlit` are not installed on this machine and the frontend bundle is not
built. None of it touches the Process removal, but it does mean this machine cannot green the whole
suite — CI is the authority for the clusters that need those dependencies.

**Checked against the tree before the change** (stash the source, keep the tests): **1.406, 1.407,
1.408, 10.97 and 10.98 all fail** — they are real gates, not tautologies. 1.409 and 10.99 pass on
both trees by design: they pin what *survived* (the `Model` / `HTMLReport` / `Folder` node types,
the ten neighbouring routes), so they are forward regression gates against the type or a route
coming back, not before/after discriminators.
