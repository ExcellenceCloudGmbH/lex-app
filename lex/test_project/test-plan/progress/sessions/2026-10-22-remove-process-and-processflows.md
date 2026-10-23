---
date: 2026-10-22
clusters: [1, 4, 10]
tests_added: "12 new scenarios (1.406-1.413, 10.91-10.94); 4.90 retired with the endpoint it pinned; 4.105, 4.108 and 10.83 narrowed to the write paths that still exist; 1o renamed its lazy-import class with the package"
suite_tally: "init or permissions or api_layer (the clusters this change edits): 841 pass / 15 fail / 16 skip. calculation_logging, run separately for the comparison: 41 pass / 9 fail. Clusters 4 and 10 are clean. All 24 failures are pre-existing and reproduce identically on the pre-change tree and on the pre-rename tree -- 15 in init (reflex and streamlit are not installed locally and the frontend bundle is not built) and 9 in calculation_logging (15b-15e). The five reflex-importing modules are skipped outright for the same reason. Gates: coverage pass, doc-imports pass, docs-mirror pass, .github/scripts 619 pass."
---

# Batches 1ax, 10p — the Process and ProcessFlow features removed, and the name with them

The supervisor asked for **everything related to Process and ProcessFlows** to go. That came in
two parts, decided in that order: first the two named *features*, then — after the supervisor
confirmed the wider reading — the *name* itself, everywhere it survived in the admin backbone.

The backbone was never the removed feature: it is the framework's routing and registration core
(the url list, `ModelCollection` / `ModelContainer`, `ModelRegistration`, `ModelStructureBuilder`,
`bitemporal_sync`), and `lex/lex_app/urls.py` mounts it as the entire REST API. Deleting it would
have deleted the product, so it was **renamed, not removed** — the machinery is byte-for-byte the
same work, only the names moved.

**What was removed.** `lex/core/models/Process.py` (the abstract workflow model) and
`lex/api/views/process_flow/` (the `CreateOrUpdate` step view) are deleted. With them went the
registry and both routes: `lexAdminSite.processes` / `registerProcess` /
`get_process_structure_func`, `ModelRegistration._register_process_model` and its branch in
`register_models`, the `ProcessStructure` view in both the modules that carried a copy
(`lex/model_admin/views/model_relation_views.py`, `lex/api/views/ModelStructureObtainView.py`),
the `api/process/<name>` and `api/run_step/<model>/<pk>` routes, the `"Process"` node type in
`lex/model_admin/models/utils.py`, and the `Process` entries in `lex/core/models.__all__` and
`ModelRegistration._RESERVED_CLASS_NAMES`. `lex_context_repo.md` lost its section 22 and the
sections after it were renumbered. `HTMLReport` — the sibling feature built on the same
machinery — was deliberately left alone, which is what the new scenarios check alongside each
absence.

This settles the `CreateOrUpdate` half of pending decision 4 in
[clusters/README.md](../../clusters/README.md) and the matching row in
[cleanup-and-coverage-plan.md](../../cleanup-and-coverage-plan.md) §2d: both had the route
flagged as live and awaiting supervisor confirmation to drop. The other four routes in that
cascade (`api/widget_structure`, `api/logs`, `streamlit-token`, `user_permissions`) are
untouched and still await confirmation — 10.93 pins that they survived.

**The rename.** `lex/process_admin/` → `lex/model_admin/`; `ProcessAdminSite` → `LexAdminSite`;
`processAdminSite` → `lexAdminSite`; `ModelProcessAdmin` → `LexModelAdmin` (file too);
`ProcessAdminConfig` → `ModelAdminConfig`; `process_admin_site.py` → `lex_admin_site.py`;
`process_admin_settings.py` → `lex_admin_settings.py`; the URL namespace
`process_admin_rest_api` → `lex_rest_api`; and the public test base
`lex/lex_app/tests/ProcessAdminTestCase.py` → `LexAppTestCase.py`. 397 replacements across 98
files, plus two dead placeholders deleted (`model_admin/models.py` and `views.py` were one-line
"will be moved here during refactoring" comments, permanently shadowed by the packages of the
same name).

**`LOGIN_REDIRECT_URL`.** The sweep rewrote `/process_admin/all` → `/model_admin/all`; it was
first reverted on the assumption that the bundled frontend owned that route, then restored when
that assumption was checked and proved wrong. The evidence: nothing in lex-app reads
`LOGIN_REDIRECT_URL` (only the new 10.94 does), `oauth2_authcodeflow` does not read it either,
and `grep` over the shipped bundle in `lex/react/build/` finds no `process_admin` string at all.
It is dead configuration, so it follows the app's new name and 10.94 pins the new value.

**One name genuinely cannot move: `process-admin-general-client`.** It is the frontend's actual
GitHub repository, `ExcellenceCloudGmbH/process-admin-general-client`, hard-coded in ten places
under `.github/` (`frontend_build.yml`, `prerelease_gate.yml`, `publish_release_notes.yml`,
`draft_notes_dry_run.yml`, `frontend_manifest_guard.yml`, `release_notes/changelog.py`) and named
in `requirements.txt` as the source of the compiled frontend package. Renaming the string here
would point CI at a repository that does not exist and break the frontend build; renaming the
repository is not something this repo can do. It stays, and the CI gates stay green.

**`.worktrees/multi-realm-clients/` is not part of this change.** `git worktree list` shows it
registered, checked out on `chore/realm-agnostic-keycloak-commands` (already merged) and clean;
the path is excluded via `.git/info/exclude`. Its copies of these files are that branch's working
tree, not this one's — they are in no commit of `lex-app-v2` and in no diff of this change.
Editing them would leave uncommitted edits ambushing whoever opens that worktree next. The
directory can be pruned with `git worktree remove`, which is the user's call, not this change's.

The app has no models and no migrations, so the app-label change (`process_admin` → `model_admin`)
renames no table and needs no migration.

**`ProcessAdminTestCase` is a breaking change.** Downstream projects import it. It was retired
without a compatibility alias, on purpose: an alias would keep the retired name alive, which is
the thing being removed. 1.412 pins that the old module is gone rather than quietly re-exported,
so the break is visible at import time instead of at some later surprise.

**Surfaces and where they landed.** The removal is observable in two places, so it is two
batches. The *model* side — import, registry, registration branch, reserved name, structure node
type — is bootstrap, so [1ax](../../clusters/01-init/batches.md) (1.406–1.409, I). The *route*
side is the URL table, so [10p](../../clusters/10-api_layer/batches.md) (10.91–10.93, U), which
walks the project's own URLconf for every `(name, path)` it serves rather than trusting a single
`reverse`. 10.93 is the half that matters most for a cascade of this shape: the deleted routes
lived in the one url list the entire frontend is served from, so every neighbouring endpoint is
checked to still resolve.

**Existing scenarios that drove `run_step`.** `run_step` was a second write path into every
model, carrying its own copy of the permission checks, so three hardening scenarios probed it.
4.90 pinned it *alone* and is therefore **retired** — the id is not reused, and 10.91 now pins
the endpoint's absence instead. 4.105 creates its `auth.User` through the CRUD endpoint, 4.108
dropped the `run_step` entry from its answers dict, and 10.83 dropped the `run_step` probe from
`_failures()`; all three keep every other write path they covered, so cluster 4p and 10o land
the same counts as before and 4o drops 21 → 20.

**Run:** `python -m lex pytest` over the two new files — **12 pass / 0 fail** (1ax 8, 10p 4).
1.413 is the gate for the sweep itself — it imports every module the rename rewrote and then
reads the whole `lex/` tree for the retired spellings, the feature's names included
(`ProcessStructure`, `ProcessFlow`, `process_flow`, `run_step`, `registerProcess`). Its search
strings are assembled at runtime so the file does not match itself, and exactly two files are
exempt: this one and 10p, the two whose job is to assert the absence. Everything else under
`lex/` is clean. It earned its place immediately — on its first run it failed, correctly, on 10p.

Over the four clusters this change reaches — `init`, `permissions`, `api_layer`,
`calculation_logging` — **882 pass / 24 fail** after the rename (the final `init`, `permissions`
and `api_layer` run is 841 pass / 15 fail; `calculation_logging` contributes the other 9).
The failure set is byte-identical before the feature removal, after it and after the rename, and
every one of those 24 fails identically on the pre-change tree (checked by stashing the change and re-running the same files): 15 in `init`
because `reflex` and `streamlit` are not installed on this machine and the frontend bundle is not
built, 9 in `calculation_logging` (15b–15e). **Clusters 4 and 10 are clean.** None of this is
new and none of it touches the Process removal, but it does mean this machine cannot green the
whole suite — CI is the authority for the clusters that need those dependencies.

**Checked against the tree before the change** (stash the source, keep the tests): **1.406,
1.407, 1.408, 10.91 and 10.92 all fail** — they are real gates, not tautologies. 1.409 and
10.93 pass on both trees by design: they pin what *survived* (the `Model` / `HTMLReport` /
`Folder` node types, the ten neighbouring routes), so they are forward regression gates against
the type or a route coming back, not before/after discriminators.
