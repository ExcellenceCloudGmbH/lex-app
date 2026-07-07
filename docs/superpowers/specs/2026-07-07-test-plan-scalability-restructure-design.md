# Test-Plan Scalability Restructure — Design

> **Status:** Approved 2026-07-07 (supersedes the draft in `docs/_drafts/`)
> **Scope:** `lex/test_project/test-plan/` (backend) + every consumer of its layout.
> **Why now:** the frontend test-plan (draft: `2026-07-07-frontend-test-plan-design-draft.md`)
> will clone this paradigm; we fix the layout once, here, before cloning it.

---

## 1. Problems, Measured

### 1.1 Files too big for agents

| File | Size | Growth law |
|---|---|---|
| `progress/session-log.md` | 232 KB | +1 paragraph-sized table row per PR, forever |
| `test-clusters.md` | 212 KB | +1 section per sub-cluster, forever |
| `test-writing-plan.md` | 67 KB | +1 batch block per batch, forever |
| `progress/dashboard.md` | 20 KB | +1 row per (sub-)cluster, forever |

The `lex-testing` skill (Step 1) and `copilot_assemble_prompt.py` both instruct agents to
read `test-clusters.md` + `test-writing-plan.md` **before allocating** — a ~280 KB
(~70k-token) read to answer "what is the next free letter in cluster 7?".

### 1.2 Concurrent PRs corrupt the plan

Every test PR must edit the same four files — `copilot_validate_pr_shape.py` *enforces*
it. With ≥2 in-flight PRs:

- `session-log.md`: bottom-append → guaranteed conflict, or clean merge with duplicate
  session numbers (the counter is a shared mutable maximum).
- `test-writing-plan.md` / `test-clusters.md`: both PRs compute "next free letter / next
  scenario ID" from the same stale snapshot → **silent double-allocation** that merges
  cleanly.
- `dashboard.md`: hand-maintained aggregate → drifts on every bad conflict resolution.

### 1.3 The compounding factor: every fact has four homes

One batch is currently written up **four times** — a `test-clusters.md` section, a
`test-writing-plan.md` block, a `dashboard.md` row, and a `session-log.md` row. That
quadruples growth (1.1), quadruples the write surface (1.2), and makes the copies
disagree over time. Any fix that shards the files but keeps the four-fold writing
re-creates the problem inside the shards.

---

## 2. Design Principles

1. **Single home per fact.** Batch detail (scenario table, files covered, test classes,
   fixtures) lives **only** in the owning cluster's `batches.md`. Scenario intent lives
   **only** in `cluster.md`. Machine state lives **only** in `allocation.yaml`. Session
   fragments and generated aggregates *reference*; they never restate.
2. **One writer-domain per PR.** A PR touching cluster 7 writes only (a) *new* files and
   (b) files under `clusters/07-*/`. Two PRs can conflict **only** when they touch the
   same cluster — exactly the case where a loud git conflict is correct (it is the
   double-allocation detector).
3. **Appends become new files, never new rows.** Session log follows the
   changelog-fragment pattern (towncrier/news.d): one small file per session. Adding a
   file never conflicts with adding another file.
4. **Aggregates are generated, never hand-edited.** Dashboard and the index cluster
   table are built by a script from `allocation.yaml` files; a CI freshness check makes
   drift impossible by construction.
5. **Bounded context per task.** An agent allocating into one cluster reads `index.md`
   + `testing-philosophy.md` + `progress/conventions.md` + one cluster directory —
   ~25–40 KB **regardless of suite growth**.

---

## 3. Target Layout

```
lex/test_project/test-plan/
├── index.md                        # exec summary + ⚙generated cluster table (small)
├── testing-philosophy.md           # Golden Rule + rules + red flags + journey ordering
│                                   #   (extracted from test-clusters.md preamble)
├── why-the-shift.md                # unchanged
├── expected-results.md             # unchanged
├── cleanup-and-coverage-plan.md    # unchanged
├── known-bugs.md                   # unchanged this round (see §8 Deferred)
├── clusters/
│   ├── 01-init/
│   │   ├── cluster.md              # intent + scenario definitions (prose only)
│   │   ├── batches.md              # batch/allocation history (the ONLY batch write-up)
│   │   └── allocation.yaml         # machine state (§4)
│   ├── 02-crud_api/ … 14-queries/  # dir names NN-<slug>, slug == test-folder slug
│   └── …
└── progress/
    ├── conventions.md              # + new §"Where to write what" (single-home table)
    ├── dashboard.md                # ⚙ GENERATED — do not hand-edit
    └── sessions/
        ├── README.md               # fragment format (adapted from old session-log header)
        └── 2026-06-26-cluster1v-tz-coupling.md   # one file per session
```

- The three retired monoliths (`test-clusters.md`, `test-writing-plan.md`,
  `progress/session-log.md`) are replaced by **pointer stubs** for one release, so stale
  links and in-flight PRs fail loudly with directions instead of editing a dead file.
- Cluster directory slug matches the test-tree slug 1:1
  (`clusters/07-calculations/` ↔ `lex/test_project/tests/calculations/`), so the
  mapping needs no lookup table.

### Session fragments

Filename `YYYY-MM-DD-<short-slug>.md` (batch id or branch as slug — collision-free
without coordination). The monotonic session integer is **dropped**; ordering is
date + git history, and the generator numbers fragments chronologically for display.
Same six facts as today's row, as front-matter + short prose:

```markdown
---
date: 2026-06-26
clusters: [1v]
tests_added: 5
suite_tally: "231 pass + 4 skip + 19 xfail of 254"
---
Batch 1v — TIME_ZONE↔USE_TZ coupling for django_celery_beat DatabaseScheduler.
Details: ../../clusters/01-init/batches.md#batch-1v
```

The prose is a summary + link — per principle 1 it never restates the batch table.

---

## 4. `allocation.yaml` — Machine State, One Per Cluster

```yaml
# ⚙ Single machine-readable source for allocation + the generated dashboard.
cluster: 7
slug: calculations
title: Calculation State Machine
max_scenario: 199            # highest scenario ID ever allocated; never decreases
letters:                     # never renumbered, never reused; planned batches live here too
  a:
    title: Atomic calculation happy path
    scenarios: 7.1-7.12
    status: complete         # planned | in-flight | complete | blocked | rolled-back
    tests: {pass: 12, skip: 0, xfail: 0}
    note: ""                 # terse dashboard note only (BUG refs, skip reasons)
  q:
    title: Nested fan-out dispatch
    scenarios: 7.190-7.199
    status: complete
    tests: {pass: 10, skip: 0, xfail: 0}
    note: ""
```

**How this kills silent double-allocation:** two concurrent PRs allocating in the same
cluster must both bump `max_scenario` and insert a letter — the same lines — so git
turns the race into a merge conflict instead of a clean corrupt merge. Belt-and-braces:
the PR gate re-derives scenario IDs and letters from the test files in the diff (they
are in filenames and docstrings already) and fails on any ID above the YAML claim,
duplicate ID, or reused letter.

Allocation for agents becomes mechanical: Step 3 of the skill = read `allocation.yaml`,
take `max_scenario + 1` and the next free letter. "Is this source file already slotted?"
= check `status: planned|in-flight` letters and their `batches.md` rows — one cluster's
worth of reading, not the whole plan.

---

## 5. Generated Aggregates

One stdlib-only script (same style as `docs_mirror.py`), living with the other CI
scripts so the gate and its unit tests share the established harness:

- **`.github/scripts/test_plan_aggregates.py`**
  - `build` — reads every `clusters/*/allocation.yaml`; writes `progress/dashboard.md`
    and the cluster table in `index.md` (between `<!-- generated: -->` markers).
  - `check` — rebuilds to a temp copy and fails on diff (the CI freshness gate).

Dashboard rows are derived **only** from the YAML (title, scenario range, counts,
status, note) — one source, zero parsing of prose. Agents run `build` as part of the
Definition of Done; the gate runs `check`.

---

## 6. Consumer Updates (same migration PR)

| Consumer | Change |
|---|---|
| `lex-testing` skill | Step 1 reads `index.md` + `testing-philosophy.md` + target `clusters/NN-<slug>/`; Step 3 allocates from `allocation.yaml`; Step 7 DoD = cluster-shard edits + new session fragment + `test_plan_aggregates.py build`. Drop instructions to read the monoliths. |
| `.github/instructions/testing.instructions.md` | Same path/workflow updates. |
| `copilot_assemble_prompt.py` | Prompt points Copilot at the **target cluster directory** (+ `testing-philosophy.md`) instead of the two monoliths → smaller, better-focused cloud prompts. Required-deliverables list rewritten to the new DoD. |
| `copilot_validate_pr_shape.py` | New checks: (1) test-file changes under `lex/test_project/tests/<slug>/` require edits under `clusters/NN-<slug>/`; (2) ≥1 **added** file under `progress/sessions/`; (3) allocation consistency (§4: IDs ≤ `max_scenario`, no duplicate IDs repo-wide, no reused letters, filename letter present in YAML); (4) `test_plan_aggregates.py check` passes. Existing bug-repro checks (known-bugs row, xfail-strip probe) unchanged. Drop the must-touch-monolith checks. |
| Cross-links | `index.md`, `progress.md`, `conventions.md`, `docs/ci-cd/copilot-test-bot.md`, pointer stubs. |

---

## 7. Migration — One PR, Mechanical and Verified

1. **Split script** `.github/scripts/test_plan_split.py` (committed for audit, deleted
   after the release that retires the stubs):
   - `test-clusters.md` → cut at `## N.` headings → `clusters/NN-<slug>/cluster.md`;
     preamble (philosophy, rules, red flags, ordering) → `testing-philosophy.md`.
   - `test-writing-plan.md` → cut at `## Cluster N` → `batches.md` per cluster.
   - `session-log.md` → one dated fragment per row (slug derived from the row's batch
     references; session number preserved inside the fragment body for history).
   - `allocation.yaml` seeded from the batch tables + dashboard rows.
2. **Fact-preservation audit (the gate for the migration itself):** the script extracts
   the sets of {scenario IDs, letters, BUG references, session dates} from the old files
   and the new tree and asserts equality; the extracted-facts diff is attached to the PR.
3. Run `test_plan_aggregates.py build`; commit generated dashboard + index table.
4. Add pointer stubs; update all §6 consumers; update `progress/conventions.md` with the
   "Where to write what" single-home table.
5. **Operational notes:** land at a quiet moment; in-flight Copilot PRs fail the new
   shape gate with a clear message and are re-dispatched (cheap by design). The
   `docs/.docs-sync.yml` manifest is unaffected (test-plan paths are not mirror-owned) —
   verified during implementation.

---

## 8. Deferred (explicit non-goals this round)

- **`known-bugs.md` fragmenting** — 44 lines today and gate-integrated; apply the
  fragment pattern when it measurably hurts, not before.
- **Per-cluster size budgets** — sharding gives headroom; add a soft gate warning
  (e.g. 64 KB per `cluster.md`) only when a cluster approaches it.
- **Rewriting historical prose** — migration moves text; it does not edit it.

## 9. Alternatives Rejected

| Approach | Why rejected |
|---|---|
| Full machine-readable source (everything YAML, all .md generated) | Intent is prose; forcing it into YAML kills the living-documentation property; far larger migration. |
| Rotation/archiving only (quarterly session logs, archive completed batches) | Shrinks files but every PR still edits the same hot files — contention and double-allocation remain. |

## 10. What the Frontend Inherits

The frontend test-plan adopts this layout from day one: `docs/test-plan/clusters/
F01-boot_auth/…`, session fragments, `allocation.yaml` per F-cluster, the same
aggregates script and gate checks in the frontend repo. Its draft's §5 is updated to
reference this spec.
