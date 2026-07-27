# LEX Frontend Test Plan — Design Draft (v0.1)

> **Status:** DRAFT — for review, not yet approved
> **Date:** 2026-07-07
> **Audience:** Engineering leadership, QA supervisors, agents (local Claude + cloud Copilot)
> **Companion to:** the backend test-plan at `lex/test_project/test-plan/` — this document
> deliberately mirrors its structure, vocabulary, and workflows so one paradigm governs both stacks.

---

## 1. Where We Are (and Why It Rhymes With April)

The frontend today is where the backend was before the April shift:

| Signal | Backend (pre-shift) | Frontend (today) |
|---|---|---|
| Green tests | 2,000+ | ~1,200 across 186 files |
| Coverage gate | 60% lines | 76% lines |
| Test target | Internal methods | Component internals, indexed **by file name** |
| Organizing principle | None (file-per-module) | None (file-per-component) |
| Derives expectations from | The implementation | The implementation |
| Scenario traceability | None | None |
| Agent workflow | None | None |
| Known-bugs ledger | None | None |

The 76% gate is real and worth keeping — but it measures *lines executed*, not *customer
journeys protected*. A test like `CustomDatagrid.test.tsx` (62 tests) verifies that the component
renders what the component renders. If the grid silently mistranslates a filter into the wrong
Django lookup, or a permission flag stops hiding the Delete button, line coverage stays green.

**The proposal: apply the backend's Golden-Rule cluster paradigm to the frontend**, reusing the
same document set, allocation workflow, known-bugs ledger, session log, and (in phase 2) the same
cloud Copilot test-bot — adapted to what "public interface" means in a browser.

---

## 2. The Golden Rule, Translated for the Frontend

> **Test what the UI is trying to let the customer accomplish — not what the current
> component tree happens to render.**
>
> The component source is an incomplete story. Tests derive their expectations from
> **documented intent** (published docs in `lex-app-docs`, the redesign phase specs in
> `docs/superpowers/plans/`) and **reasonable user expectations** — never from reading the
> component and asserting back what it already does.
>
> If the UI does the wrong thing, the test must fail. That failure is the test doing its job.

**What "public interface" means here.** On the backend it's the ORM and REST API. On the
frontend it is **what the user can see and do**: rendered text, enabled/disabled controls,
navigation results, downloaded files, and the HTTP requests the app sends to the backend.
A scenario is only "covered" when a test drives it the way a real user reaches it —
through rendered UI, not by calling a helper function underneath it.

**Red-flag list (mirrors backend conventions.md):**
- Asserting on internal component state, Redux internals, or prop plumbing → implementation-coupled.
- Mocking the module under test, or mocking `fetch` for a flow whose whole point is the request shape.
- Snapshot tests of DOM trees (they assert "whatever it currently renders").
- Deriving the expected filter/sort/query translation by reading the datasource code instead of
  the backend's documented query contract (backend clusters 12–14 define that contract).

---

## 3. Three Tiers, One Spine

| Letter | Tier | Runner | What belongs here |
|---|---|---|---|
| **U** | Unit | Vitest (node/jsdom) | Pure logic: query translation, state persistence helpers, slices, formatters. No DOM rendering needed. **Prefer when the behaviour is genuinely pure.** |
| **C** | Component | Vitest + RTL + MSW v2 | A component + its provider tree, real `fetch` intercepted by MSW. Wiring: loading/error branches, permission-gated rendering, form field behaviour. |
| **E** | End-to-end | Playwright + **real Django backend** (`e2e/e2e_project`) | The cluster spine. Full user journeys in Chromium against a live lex backend: grid interactions, CRUD round-trips, WebSocket status, exports, auth. |

**Why E is the spine and not MSW.** Three reasons, all learned the hard way:

1. **AG Grid cannot be tested in jsdom** (no layout engine, no virtualisation, no scroll). AG Grid's
   own team says use E2E. ~90% of user time is in the grid.
2. **MSW fixtures drift.** A fixture asserts what the backend *used to* return. The backend already
   has clusters 12–14 defending the serializer/query/export contract from its side; the only way
   the frontend proves it speaks the same contract is to talk to the real thing.
3. **It mirrors the backend paradigm exactly.** `e2e/e2e_project` is the frontend's `test_project`:
   a dedicated, versioned lex project with its own models, seeds, and settings, mimicking real
   customer structure. It already exists (Fund/Position/Currency models, seed command, `run-e2e.sh`,
   CI wiring via `frontend_build.yml` in the lex repo). We grow it per-cluster.

**Tier selection rule (strict):** a behaviour surface is covered at the tier where a real user
reaches it. Grid sorting is an E surface — a U test of the sort-translation helper is *supporting*
coverage, never a substitute. The allocation step (§7) records the tier per scenario, so an audit
can ask "which journeys are only defended by mocks?" and get an answer.

MSW (C-tier) is kept for what it is good at: exhaustive branch coverage of error/edge rendering
(401/403/500/timeout/empty), which would be slow and awkward to provoke through a real backend.

---

## 4. Cluster Map — Ordered by the User's Day

Frontend clusters are numbered **F1–F12** (the `F` prefix avoids collision with backend
clusters 1–14, so a PR or bug can reference either unambiguously). Ordering follows the
customer journey: get in → find your data → read it → change it → process it → trust it.

| # | Cluster | Slug | What It Tests | Key Risk It Covers |
|---|---------|------|---------------|-------------------|
| F1 | Boot & Auth | `boot_auth` | Login via Keycloak/OIDC, session gate, redirect-loop guard, unauthorized page, logout | Nobody can log in; auth loops; silent lockout |
| F2 | Shell & Navigation | `shell_nav` | Sidebar model tree, breadcrumbs, global search, routing, home/launchpad, embed detection | User can't find their models; broken deep links |
| F3 | Grid — Read Path | `grid_read` | List rendering via SSRM against real backend: pagination, sorting, filtering (text/number/date/FK), grouping, aggregation, pivot | Silent query mistranslation — wrong rows, empty grids, wrong order (frontend twin of backend cluster 14) |
| F4 | Grid — Views & State | `grid_views` | Column state, saved views/presets, density, filter persistence, URL-driven column config | User's configured workspace evaporates or corrupts |
| F5 | CRUD Forms | `crud_forms` | Create/edit forms for every field type, backend validation errors surfaced, delete + confirmation, bulk actions | Data corruption from the UI; validation bypass; lost input (frontend twin of backend clusters 2–3) |
| F6 | Permission-Aware UI | `permissions_ui` | Buttons/fields/rows hidden or disabled per `can_create`/`can_edit`/scopes; masked fields never rendered | Data leaks; users acting beyond their rights (twin of backend cluster 4) |
| F7 | Calculations & Live Status | `calc_status` | Calculate button states, status pills, WebSocket-driven updates, monitoring drawer, calc logs, abort | Phantom spinners; stale status; user re-triggers a running calc (twin of backend clusters 7–9) |
| F8 | History & Bitemporal UI | `history_ui` | As-of control, history timeline, version drawer, effective/valid-time views, diff view | Compliance story invisible or wrong in the UI (twin of backend cluster 5) |
| F9 | Exports & Files | `exports_files` | Excel/PDF export round-trip (real file, real columns, FK display names), file upload/download fields, SharePoint/GCS | "Export to Excel" lies to the customer (twin of backend cluster 13) |
| F10 | Process Flows | `process_flows` | Multi-step wizards, step validation, process history sidebar | Customer workflow stuck mid-process |
| F11 | Errors & Resilience | `errors_resilience` | Notifications, traceback modal, backend-down grace period, server-not-ready, error boundaries, retry | Errors swallowed; white screens; users lose work silently |
| F12 | Embedding & Integrations | `embed_streamlit` | Embed mode, Streamlit iframe auth/lifecycle | Embedded deployments break unnoticed |

Each cluster gets the full backend-style breakdown in `test-clusters.md`: intent, behaviour
surfaces, scenario tables with IDs (`F3.1`, `F3.2`, …), and the docs it derives intent from.

---

## 5. Repository Layout

The plan and the tests live **in the frontend repo** (`process-admin-general-client`), because
that is where agents write frontend tests and where the CI that gates them runs. Cross-links
from the lex repo's test-plan index make the two plans one navigable system.

> **Layout note (2026-07-07):** the plan adopts the sharded layout from
> `docs/superpowers/specs/2026-07-07-test-plan-scalability-restructure-design.md`
> from day one — per-cluster directories with `cluster.md` + `batches.md` +
> `allocation.yaml`, session fragments instead of an append-only log, and a
> generated dashboard. No monoliths are ever created here.

```
process-admin-general-client/
├── docs/test-plan/                  # sharded layout per the restructure spec
│   ├── index.md                     # exec summary + ⚙generated cluster table
│   ├── testing-philosophy.md        # Golden Rule (frontend translation) + red flags
│   ├── known-bugs.md                # BUG-F-NNN ledger
│   ├── expected-results.md          # KPIs
│   ├── clusters/
│   │   ├── F01-boot_auth/           # cluster.md + batches.md + allocation.yaml
│   │   └── … F12-embed_streamlit/
│   └── progress/
│       ├── conventions.md           # stable rules (this section, distilled)
│       ├── dashboard.md             # ⚙ GENERATED — do not hand-edit
│       └── sessions/                # one fragment file per session/PR
├── e2e/
│   ├── e2e_project/                 # the frontend's test_project (grows per cluster)
│   └── tests/                       # one folder per cluster, named by slug
│       ├── boot_auth/       f1a_login.spec.ts, …
│       ├── grid_read/       f3a_sorting.spec.ts, f3b_filters.spec.ts, …
│       └── …
└── src/__test__/clusters/           # U + C tier cluster tests
    ├── grid_read/           f3c_filter_translation.test.ts, …
    └── …
```

**Naming convention (PR-gate enforced):** `f<N><letter>_<slug>.{spec,test}.{ts,tsx}` —
E-tier files are `.spec.ts` under `e2e/tests/<cluster_slug>/`; U/C-tier files are `.test.ts(x)`
under `src/__test__/clusters/<cluster_slug>/`. Letters allocated per cluster exactly as on the
backend: next free letter, never renumbered. Playwright tags every spec with `@<cluster_slug>`
(its equivalent of `pytestmark`), so `--grep @grid_read` runs one cluster.

**The existing 186 files stay where they are.** They keep holding the 76% coverage floor and are
explicitly labelled the *legacy suite* in the plan. A later supervisor categorisation pass
(EXCLUDE / DELETE / COMPLETE / LATER — same rubric as the backend's cleanup-and-coverage-plan)
maps each legacy file to a cluster or marks it for retirement. No big-bang migration.

---

## 6. Known Bugs — Same Ledger, Frontend Markers

When a cluster test exposes a real bug (frontend *or* backend — E-tier tests will find both):

1. Assert the **correct** behaviour, derived from docs/intent.
2. Mark the expected failure with the runner's native strict marker:
   - Playwright: `test.fail()` (fails the run if the test unexpectedly *passes* — same semantics as `xfail(strict=True)`)
   - Vitest: `it.fails()`
3. Add a `BUG-F-NNN` row to `known-bugs.md` (description, severity, cluster, test, status).
   If the root cause is in the backend, cross-file it to the backend `known-bugs.md` and link both.
4. Never soften the assertion. The marker comes off when the bug is fixed; the test becomes a
   live regression gate automatically.

---

## 7. Agent Workflow

### 7.1 Local agents — a `frontend-testing` skill

Mirror of `lex-testing`, adapted:

- **Step 0 — intent research first:** read the published docs (`lex-app-docs` feature pages), the
  redesign phase specs, and the backend contract clusters (12–14) before writing anything.
  Never derive a test by reading the component.
- **Step 1 — read the plan** (`index.md`, `test-clusters.md`, `test-writing-plan.md`, `known-bugs.md`).
- **Step 2 — enumerate behaviour surfaces** (every user-visible state, every request the UI emits,
  every error branch), map each to its F-cluster **and its tier** (U/C/E per §3's strict rule).
- **Step 3–5 — allocate letter + scenario range, confirm before scaffolding** (same template as backend).
- **Step 6 — scaffold** with the mandatory header: Intent, cluster/scenarios, tier, covers,
  run command; Playwright tag / describe naming per convention.
- **Step 7 — Definition of Done:** session-log row + dashboard bump + test-writing-plan row +
  known-bugs entries, in the **same PR**. Plan on disk must match tests on disk.
- **Coverage pairing:** any change under `src/` requires a paired cluster test in the same change
  (same rule the backend gate enforces for `lex/`).

### 7.2 Cloud agent — extend the Copilot test-bot (phase 2)

The lex repo's `copilot_test_bot.yml` / `copilot_pr_gate.yml` / `copilot_coverage_check.yml`
trio is replicated in the frontend repo:

- Same three labels (`copilot:regression`, `copilot:bug-repro`, `copilot:fix-and-test`).
- Prompt assembled at runtime from `docs/test-plan/` (the frontend plan is written to be the
  prompt source, exactly like the backend's).
- PR gate validates: file naming regex, cluster folder, plan-sync deliverables present
  (session-log row, dashboard bump), no edits to legacy suite, no softened `test.fail()` removals
  without a known-bugs status change.
- Coverage gate: source change without paired cluster test → `coverage-task` issue auto-filed
  and assigned to Copilot.

---

## 8. CI Pipeline

```
PR to frontend repo
├─ checkcode            (TS + ESLint + Prettier)          hard gate (exists)
├─ vitest --run         legacy + U/C cluster tests,
│                       coverage ≥ 76% lines, only up      hard gate (exists)
├─ e2e cluster suite    Playwright vs real backend,
│                       per-cluster jobs (--grep @slug)    soft → hard per cluster
└─ plan-sync gate       naming regex + session-log row
                        + dashboard bump on cluster PRs    hard gate (new)

Release (push-build-to-pip-package)
└─ gate-tests: all of the above hard-gated               (extend existing)

Nightly (frontend_build.yml in lex repo — exists)
└─ full E2E matrix vs backend HEAD — catches contract drift between releases
```

**Per-cluster promotion (the auditable rollout):** a cluster's E2E job starts
`continue-on-error: true`; when it has run green for 10 consecutive scheduled runs, it is
promoted to a hard gate and its dashboard row flips to gating. Promotion is a one-line
PR to the workflow — visible, reviewable, reversible.

**Flake policy:** Playwright `retries: 1` in CI; a test that needed the retry is logged; two
retry-passes in a week → quarantined with a `@flaky` tag + known-bugs row (flakes are bugs).
Quarantine without a ledger entry fails the plan-sync gate.

**New scenario-level KPI (replaces coverage-as-quality):** the dashboard tracks
*scenarios defined vs implemented vs gating* per cluster. Line coverage remains a floor
(76%, never down) but stops being the headline number.

---

## 9. Expected Results

| Metric | Today | Target |
|---|---|---|
| Customer journeys defended end-to-end (real browser + real backend) | ~0 (2 skipped specs) | All F1–F12 happy paths + top error paths |
| Contract drift detection (frontend ↔ backend) | None (MSW fixtures frozen in time) | Nightly E2E vs backend HEAD |
| Scenario traceability (PR → plan row → docs intent) | None | 100% of new tests |
| Known frontend bugs with a failing test | 0 | All (BUG-F ledger) |
| Agent-writable | No (no allocation rules) | Yes (local skill + cloud bot) |
| Line coverage | 76% (headline) | ≥76% (floor only) |

---

## 10. Rollout Phases

| Phase | Deliverable | Effort |
|---|---|---|
| **0** | This design approved; plan docs scaffolded (`docs/test-plan/` with F1–F12, conventions, empty dashboard/session-log) | small |
| **1** | `e2e/tests/` cluster tree + Playwright tags + naming convention; F1 (boot_auth) and F3 (grid_read) implemented as the proving clusters; per-cluster CI jobs (soft) | medium |
| **2** | `frontend-testing` skill; plan-sync PR gate; coverage-pairing gate | medium |
| **3** | Copilot test-bot trio replicated in the frontend repo | medium |
| **4** | Remaining clusters in journey order (one at a time, backend rule); per-cluster promotion to hard gates | ongoing |
| **5** | Legacy-suite categorisation pass (EXCLUDE/DELETE/COMPLETE/LATER) | later |

F1 and F3 go first deliberately: F1 is the cheapest full journey (proves the harness), F3 is
the highest-risk surface (proves the paradigm pays for itself — it is where the backend's
cluster 14 found silent query mistranslation from the other side).

---

## 11. Open Decisions (flagged for review)

1. **Plan location** — recommended: frontend repo (`docs/test-plan/`), cross-linked from the
   backend plan. Alternative: everything in the lex repo next to the backend plan; cost: agents
   working in the frontend repo lose the plan-beside-tests property the backend enjoys.
2. **E-spine vs MSW-first** — recommended: real-backend Playwright spine (§3). Alternative:
   MSW-first is faster in CI but cannot test the grid and re-freezes the contract-drift problem
   we're trying to kill. Middle option (recorded-fixture refresh job) noted as later hardening
   for the C tier, not a substitute for E.
3. **Cloud bot timing** — recommended: phase 3 (after the harness is proven by F1/F3), not phase 1.
4. **Legacy suite** — recommended: keep + categorise later. Alternative (delete/migrate now)
   burns weeks before the new paradigm has produced value.
