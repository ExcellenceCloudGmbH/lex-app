# Cluster Allocation — Conventions, Backlog, Pending Decisions

> Absorbed from `test-writing-plan.md` (retired). Per-cluster batch
> history lives in each `NN-<slug>/batches.md`.

# Test-Writing Plan — COMPLETE bucket (May 2026)

> **Source:** §4 of [cleanup-and-coverage-plan.md](cleanup-and-coverage-plan.md) — supervisor's ~95-file COMPLETE list.
> **Goal:** turn every file in that list into one or more sub-cluster batches with concrete scenario IDs, test-class targets, fixtures, and an execution order.
> **Naming:** new sub-clusters extend existing clusters with the next free letter (e.g. cluster 6 already has 6a–6f → next is **6g**). Scenario IDs continue from the cluster's current max. Cluster numbers themselves are **never renumbered** — that would invalidate report/progress tracking.
> **Test types:** **U** = `SimpleTestCase` (no DB), **I** = `TestCase` (per-test transaction), **E** = REST through `APIClient`.
> **Back to:** [Index](index.md) · [Testing Philosophy](../testing-philosophy.md) · [Cleanup plan](cleanup-and-coverage-plan.md)

---

## Conventions for this plan

1. **One batch = one sub-cluster = one PR.** Keeps reviews bounded and lets coverage gates ratchet up cleanly.
2. **Files needing a usage check first** (`WebSocketNotifier`, `CalculationLogConsumer`, `UserAPIView` vs `user_api`) are **not slotted** until the supervisor decision lands. Each is parked in §6 "Pending decisions".
3. **Files already covered by an in-flight Tier-A cluster** in the coverage forecast (`ModelExport.py` → 13f, `List.py` → 14f, `base_serializers.py` → 12g, `celery_tasks.py` → 8j, `LexModel.py` → 3b/4i existing, `CalculatedModelMixin.py` → 7h) are **referenced, not re-slotted**. They keep their forecasted home.
4. Every batch lists: scenario range, files covered, test classes, fixtures, file path, est. tests, est. coverage gain, prerequisite PRs.
5. **Files don't always map 1:1 to a single batch** — e.g. `LexLogger.py` shows up in cluster 6 (audit fill) and again in the bug-§1 fix PR. That's deliberate.

---

## 5. LATER bucket (deferred — keep in backlog)

Covered in [cleanup-and-coverage-plan.md §5](cleanup-and-coverage-plan.md#5-later-deferred-but-tracked). When picked up, slot as:

| File | Suggested home |
| --- | --- |
| `core/middleware/embed_token_auth.py` | new **4m** |
| `core/middleware/embed_xframe.py` | **4m** (same batch) |
| `api/filters/FilterTreeNode.py` | **2g extension** |
| `api/utils/temporal.py` | **5e extension** |
| `lex_app/fast_health.py` | **1e extension** |
| `runtime_config.py` | **1e extension** |

---

## 6. Pending decisions blocking specific batches

| # | Question | Blocks |
| --- | --- | --- |
| 1 | `audit_logging/utils/WebSocketNotifier.py` — used anywhere? If no → DELETE. If yes → add a class to **6i**. | 6i final scope |
| 2 | `api/views/authentication/UserAPIView.py` vs `authentication/views/user_api.py` — which is live? Delete the other. | **4l** can be opened |
| 3 | `api/consumers/CalculationLogConsumer.py` — still used? If no → DELETE. If yes → add to **9b** as a 5th consumer. | 9b final scope |
| 4 | `process_admin_site.py` route cascade (cleanup §2d) — supervisor confirmation to drop `api/widget_structure`, `api/logs`, `streamlit-token`, `user_permissions`, `CreateOrUpdate` routes. | **13c** |

### 6a. Process Admin batches (planned "Cluster 13" block — never opened)

> **Migration note (2026-07-07):** `test-writing-plan.md` carried a
> `## Cluster 13 — Process Admin (new — opens here)` block. Cluster **13** in the
> cluster catalogue is **Export Endpoint** (`13-exports/`), not Process Admin — the
> block reused the number 13 for a separate, never-opened area. No
> `lex/test_project/tests/process_admin/` directory exists, so none of these batches
> were ever written. The batch spec is preserved here (verbatim from the retired
> writing-plan) as a pending decision: if Process Admin work is picked up it needs a
> real cluster number of its own, not 13. Until then it does **not** live in
> `13-exports/batches.md`.

#### Batch 13a (proposed) — Container, collection, model registration

| Property | Value |
| --- | --- |
| Scenario range | 13.1 – 13.18 |
| Type | U + I |
| Files covered | `process_admin/models/ModelContainer.py`, `models/ModelCollection.py`, `models/ModelProcessAdmin.py`, `models/utils.py`, `utils/model_registration.py` |
| Test file | `lex/test_project/tests/process_admin/test_container_and_registration.py` |
| Test classes | `TestModelContainerResolution`, `TestModelCollectionStructure`, `TestModelProcessAdminRegistration`, `TestProcessAdminUtils` (U), `TestModelRegistrationFlow` |
| Fixtures | reuses existing test_project models — they're already registered |
| Est. tests | ~20 |
| Coverage gain | +1.2 % |
| Prereqs | none |

#### Batch 13b (proposed) — Structure builder & relation views

| Property | Value |
| --- | --- |
| Scenario range | 13.19 – 13.30 |
| Type | U + E |
| Files covered | `process_admin/utils/model_structure.py` (gap fill — partial coverage exists), `utils/model_structure_builder.py`, `views/model_relation_views.py` |
| Test file | `lex/test_project/tests/process_admin/test_structure_and_relations.py` |
| Test classes | `TestModelStructureNormalisation` (covers the dict-vs-list `_normalize_model_list` fix), `TestModelStructureBuilder`, `TestModelRelationEndpoints` |
| Fixtures | `model_structure.yaml` test fixture (dict + list variants) |
| Est. tests | ~14 |
| Coverage gain | +0.7 % |
| Prereqs | 13a |

#### Batch 13c (proposed) — `process_admin_site.py` *(blocked on §2d cascade)*

Defer until PR-5 in the cleanup plan lands. Once routes are pruned, write 6–8 tests covering:
- Site instantiation
- URL conf assembly (the surviving routes)
- `get_urls()` ordering
- Auth gating on the admin entrypoint

---

## 7. Suggested execution order (PR-by-PR)

Numbering continues from the cleanup-and-coverage-plan §6 list (PR-1 … PR-5 already defined there).

| PR | Batches | Why this order |
| --- | --- | --- |
| PR-6  | 1o ✅ / 1p ✅ / 1q | Foundation. Unblocks meaningful coverage measurement on init/config code that everything else imports. **1o + 1p landed Sessions 53–54.** |
| PR-7  | 6g, 6h, 6i | Largest single coverage win and unblocks 10g. Bug-§1 regression test lands here. |
| PR-8  | 7k ✅ / ~~7l~~ (rolled back) / 7m | Calculation-edge code. Independent of cluster 6. **7k landed Session 55** — exceptions / restrictions / XLSX spotter. **7l rolled back upstream** — recalc-queue surface no longer ships; spec kept commented out for re-activation. PR-8 continues with **7m** (calculation signals + active-state store) as the next batch. |
| PR-9  | 2f, 2g, 2h | CRUD surface — depends on nothing new but benefits from the audit fixtures already added by PR-7. |
| PR-10 | 4j, 4k | Permissions middleware + views. |
| PR-11 | 5e, 5f | Bitemporal services + history endpoint. |
| PR-12 | 8h, 8i | Celery dispatch & app config. |
| PR-13 | 9b | WebSocket consumers (minus blocked one). |
| PR-14 | 10g, 10h | Calculation + file/SharePoint endpoints. |
| PR-15 | 13a, 13b | Process Admin (without the routed-site batch). |
| PR-16 | 4l, 13c, plus any "blocked" batch unblocked by §6 decisions | Sweep-up. |

Each PR raises `COVERAGE_FAIL_UNDER` by **its own forecasted gain, rounded down**, never up by more than the actual measured gain. Threshold goes one direction — up.

---

## 8. Coverage forecast (delta on top of cleanup-plan §7)

| Batch | Tests | Δ coverage |
| --- | --- | --- |
| 1d + 1e + 1f | ~42 | +1.3 % |
| 6g + 6h + 6i | ~60 | +3.1 % |
| 7i + 7j + 7k | ~44 | +2.4 % |
| 2f + 2g + 2h | ~51 | +2.5 % |
| 4j + 4k | ~21 | +1.1 % |
| 5e + 5f | ~28 | +1.8 % |
| 8h + 8i | ~17 | +0.9 % |
| 9b | ~14 | +0.7 % |
| 10g + 10h | ~29 | +1.7 % |
| 13a + 13b | ~34 | +1.9 % |
| **Subtotal (this plan)** | **~340** | **+17.4 %** |

Combined with cleanup-plan §7 (EXCLUDE + safe deletes + Tier-A clusters), realistic landing range is **62 % → 78–82 %** by end of PR-16, slightly above the cleanup-plan forecast because the per-file batches catch corner-case branches the Tier-A clusters miss.

---

## 9. Rules every batch must follow

Same as cluster-doc Golden Rule. Reproduced here to keep this doc self-contained:

1. Test customer-visible behaviour, not internal calls.
2. Real DB models — no `_make_model_stub`.
3. Mock only true external boundaries (Keycloak HTTP, Celery broker, channel layer, S3, SharePoint).
4. `patch.dict("os.environ", ...)` for `CELERY_ACTIVE` / DB-target switches — never rely on `.env` leakage in CI.
5. Module + class docstrings on every test file. They are the living documentation.
6. Bare `except:` is banned — always `except Exception:` (or a specific subclass).
7. If a test exposes a real bug → `@unittest.expectedFailure` with a tracker entry, **don't weaken it.**

---

> **Runner note (May 2026):** this suite runs under `python -m lex pytest`.
> New batches add `pytestmark = pytest.mark.<cluster_slug>` to each test
> module. See [`progress/conventions.md` §How to Run Tests](progress/conventions.md#how-to-run-tests)
> for the runner commands.
