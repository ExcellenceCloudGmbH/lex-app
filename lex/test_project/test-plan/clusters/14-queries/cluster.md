## 14. AG Grid Query Endpoint

**What it tests:** the real ``GET /api/model_entries/<model>/list``
and ``POST`` against the same URL — the endpoint the AG Grid UI hits
for every scroll, sort, filter, group, and pivot. Cluster 2 tests
basic list GET semantics; this cluster tests the **30+ helper
functions and methods in** ``lex/api/views/model_entries/List.py``
that translate UI query-params / AG Grid JSON into Django ORM
queries.

**Why it matters:** this is the hottest single endpoint in the
framework. Every table the user sees routes through it. A silent
bug in ``_coerce_value`` (string ``"1"`` not converted to ``int``
for an ``IntegerField``) drops every filtered query to zero rows
with no error. A bug in ``_apply_sort_model`` makes the grid
appear to ignore header-clicks. A bug in ``_build_filter_q`` makes
the filter dropdown do nothing. Today the cluster has **zero E2E
coverage** — we test the serializer, the export, but not the
query that feeds them.

**Why after cluster 13:** the export endpoint re-uses
``ListModelEntries._apply_filter_model`` / ``_apply_sort_model``
/ ``_execute_ag_grid_request`` under the hood. If the query path
is broken, the export is broken in the same way. Testing the
query path AFTER the export path means any regression that
shows up only in a list/grid context (not in export) gets a
dedicated gate here.

**Design principle:** same rule as cluster 13 — **scenarios
spanning multiple methods, not unit tests of single helpers**.
One "filter by date range, sort DESC by amount, paginate" test
fires through ``post`` → ``_normalize_ag_request`` →
``_apply_filter_model`` → ``_build_filter_q`` (date branch) →
``_parse_ag_date`` → ``_apply_sort_model`` → ``_execute_leaf_level``
in a single flight. Assertions are on the HTTP response body, not
on internal state.

**Models needed:**

- ``QueryCategory`` — small FK target with a distinctive
  ``__str__``.
- ``QueryItem`` — wide row carrying one field per "interesting"
  filter type: ``name`` (``CharField``), ``amount`` (``Decimal``),
  ``count`` (``Integer``), ``is_active`` (``Boolean``),
  ``created_on`` (``Date``), ``created_at_ts`` (``DateTimeField``),
  ``status`` (``CharField(choices=...)``), ``metadata``
  (``JSONField``), ``category`` (``FK → QueryCategory``). Open
  permissions so the tests focus on query mechanics.

### 14a. GET list — query-param filtering & ordering

| # | Scenario | What We Assert |
|---|----------|----------------|
| 14.1 | ``?name__icontains=alp`` | Only rows matching the substring come back; ``_resolve_lookup`` routes to the safe ``__icontains`` lookup |
| 14.2 | ``?amount__gte=100&amount__lte=500`` | Decimal range filtering; ``_coerce_value`` converts strings to ``Decimal`` |
| 14.3 | ``?status__in=active,archived`` | Comma-separated ``in`` lookup; ``_build_query_from_values`` splits the single string |
| 14.4 | ``?count!=0`` (negated key) | Trailing ``!`` negates the filter; ``apply_query_param_filters`` routes through ``.exclude`` |
| 14.5 | ``?ordering=-amount`` | Rows returned in descending ``amount`` order; ``apply_ordering`` resolves the field path |
| 14.6 | ``?perPage=-1`` | Pagination response includes EVERY filtered row in ``results`` (``CustomPageNumberPagination.paginate_queryset`` ``-1`` branch) |
| 14.7 | ``?pk_only=true&status=active`` | Response shape is ``{"ids": [...], "count": N}`` — not the full row payload. Fast ``list()`` shortcut. |

### 14b. AG Grid POST — flat leaf

| # | Scenario | What We Assert |
|---|----------|----------------|
| 14.8 | POST with ``{startRow: 0, endRow: 2}`` | ``rowData`` has exactly 2 rows; ``rowCount`` matches DB total; ``_execute_leaf_level`` page slice |
| 14.9 | ``filterModel.text`` ``contains`` | Only matching rows come back; ``_build_filter_q`` text branch |
| 14.10 | ``filterModel.number`` ``inRange`` + ``sortModel`` DESC | Both applied: rows in range AND sorted DESC. Spans ``_apply_filter_model`` + ``_apply_sort_model`` + ``_coerce_value`` |
| 14.11 | ``filterModel.date`` ``greaterThan`` on a ``DateField`` | Date filter routes to ``__gt`` via ``_parse_ag_date`` |
| 14.12 | ``filterModel.date`` ``equals`` on a ``DateTimeField`` **with time** | ``_ag_filter_has_time`` → True, filter routes to ``__gte / __lt`` second-precision window via ``_parse_ag_datetime`` |
| 14.13 | ``filterModel.set`` | ``__in`` lookup against the chosen set of values |
| 14.14 | ``filterModel`` with ``operator: "OR"`` + multiple conditions | Rows matching EITHER condition; ``_build_filter_q`` recursion path |

### 14c. AG Grid POST — grouping, aggregation, pivot

| # | Scenario | What We Assert |
|---|----------|----------------|
| 14.15 | ``rowGroupCols: [category]`` at level 0 | ``rowData`` is one row per category with ``__childCount``; ``_execute_group_level`` |
| 14.16 | ``rowGroupCols: [category]`` + ``valueCols: [amount:sum]`` | Each group row carries the correct SUM; ``_build_value_annotations`` + ``_build_agg_expression`` |
| 14.17 | Drill into a group — ``rowGroupCols: [category]`` + ``groupKeys: [<cat-pk>]`` | Leaf rows for that category only; ``_apply_group_key_filters`` |
| 14.18 | ``pivotMode: true`` + ``pivotCols: [status]`` + ``valueCols: [amount:sum]`` | ``rowData`` contains one aggregated row; ``pivotResultFields`` lists the generated columns; ``_execute_pivot_mode`` + ``_build_pivot_annotations`` + ``_build_conditional_agg_expression`` |

### 14d. Edge cases

| # | Scenario | What We Assert |
|---|----------|----------------|
| 14.19 | ``filterModel`` naming a field that does not exist | Ignored silently by ``_is_valid_field_path``; response is the unfiltered set (does not crash) |
| 14.20 | ``sortModel`` with ``colId: "non_existent"`` | Silently dropped by ``_is_valid_field_path``; default PK order applied |

### 14e. Secondary filter / sort branches (April 21) 

The 14b baseline hits the main text / number / date / set / compound-OR paths. The **long tail of operation-type branches** in ``_build_filter_q`` that the AG Grid header dropdowns actually emit in production was still cold. 14e closes those gaps with 4 table-driven scenarios + 1 xfail capturing a real framework bug.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 14.21 | Text filter op variants — ``startsWith`` / ``endsWith`` / ``equals`` / ``notEqual`` / ``notContains`` | Each op returns the correct row set; one ``subTest`` per op so a regression names the failing branch |
| 14.22 | Number filter op variants — ``lessThan`` / ``lessThanOrEqual`` / ``greaterThanOrEqual`` / ``notEqual`` / ``inRange`` + date ``blank`` (which DOES work) | Same table-driven shape |
| 14.23 | Legacy ``condition1`` / ``condition2`` shape — both AND and OR operators | Older AG Grid clients still send this shape; endpoint must serve both frontend versions from one deploy |
| 14.24 | ``?ordering=-amount,name`` multi-field CSV + unknown token silently dropped | Primary + secondary sort both applied; ``?ordering=not_a_real_field,-amount`` returns 200 (no 500 on schema drift) |
| 14.25 | **BUG-016** — ``blank`` / ``notBlank`` filter ops are unreachable | Skipped until the framework bypass-list is widened to include ``notBlank`` (and the text branch special-cases ``blank`` / ``notBlank`` the way the date branch already does) |

**Status:**  Complete — 4 pass + 1 skip (BUG-016).

**What is explicitly NOT tested here:**

- ❌ **AuditLog deferred-permission leaf path**. Covered
  transitively by cluster 6 (audit) which already drives the same
  code through a different fixture.
- ❌ **Performance**. Cluster 11 owns volume & query-count
  budgets.
- ❌ **Export-specific layout / FK display names**. Cluster 13.

---

## Planned Expansions — Coverage-Driven Sub-Clusters

After landing Clusters 1–14 (176+ scenarios, 14 real framework bugs surfaced), a coverage audit (April 21, baseline **42.63%** overall) flagged six source files as "customer-visible, high-impact, low-coverage". Rather than invent new top-level clusters, each gap folds into an **existing** cluster as a new sub-cluster. This keeps the user-journey narrative intact and avoids a "Cluster 15/16/17/…" proliferation.

Priorities below are ordered by expected coverage delta × customer-visibility.

### 4e. Read-restriction filter backend — `UserReadRestrictionFilterBackend` 

**Gap:** `lex/api/views/model_entries/filter_backends.py` — 198 stmts, **28.97%** covered. Every List / Export / History query passes through this; also the lookup table for BUG-011 (permission O(n)).

**Models:** new `FilterBackendItem` in `permissions/models.py` — a minimal `LexModel` with `name` + `is_secret` whose `permission_read` branches on the caller's Django groups to hit all three filter-backend code paths in one fixture (`admin` → `allow_all`, `deny_all` → `deny`, default → per-row deny of secret rows). `MixedResourceItem` is deferred alongside the AuditLog scenarios (4.14 / 4.15) which need the Keycloak `user_permissions` payload + seeded `AuditLog` rows.

| # | Scenario | What We Assert | Status |
|---|----------|----------------|--------|
| 4.13 | Per-row visibility — mixed allowed/denied rows in one page | Only allowed rows in response (exercises `queryset.iterator()` + `excluded.append`) |  |
| 4.14 | AuditLog resource filter — `_build_auditlog_db_visibility_filters` | Rows for resources the user can't read are excluded at the DB level | ⏸ skip (fixture) |
| 4.15 | AuditLog deferred-permission path — mixed handled + residual resources | Residual rows are permission-checked via `can_read_from_payload` | ⏸ skip (fixture) |
| 4.16 | `pk_only=true` fast path honours permissions | Denied pks excluded from id list; `count` matches allowed subset |  |
| 4.17 | `allow_all` profile (admin group) returns every row | `permission_read → allow_all`, no exclusion (`return queryset` branch) |  |
| 4.18 | Deny-all short-circuit — `permission_read → deny` on every row | Zero rows returned even though DB holds every seeded row |  |
| 4.41 | Detail endpoint full read deny | Guessed detail URL returns `{}` and leaks no domain fields or `id` when `permission_read → deny` |  |

**Status:**  Complete — 5 pass + 2 skip. See progress.md Sessions 16 + 46.

### 4f. Serializer-level masking — `PermissionAwareSerializerMixin` 

**Gap:** `lex/api/views/model_entries/mixins/PermissionAwareSerializerMixin.py` — 102 stmts, **9.33%** baseline. Field-level *denial* outcomes are already gated by cluster 4b (BUG-010 xfail); 4f locks down the **mixin's infrastructure contracts** (naming, injection, metaclass) plus the `run_validation` hook end-to-end — the code the BUG-010 fix will rely on.

Split across two classes: **`TestCluster04f_MixinMachinery`** (4.19–4.22, `SimpleTestCase` — no DB) covers the plumbing; **`TestCluster04f_RunValidation`** (4.23–4.26, `E2ETestCase` — real fixtures) drives the actual customer-facing validation hook.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 4.19 | `_camel_to_snake` table-driven over 9 shapes | Acronyms (`URLPath → url_path`), already-snake no-op, empty string — the translation every PATCH depends on |
| 4.20 | `_get_non_editable_fields` contains pk + every `editable=False` column | Wrong set → false 403s on `id` |
| 4.21 | `add_permission_checks` decorator preserves `__name__` / `__module__` | Wrong → error traces show a wrapper class instead of the real serializer |
| 4.22 | `PermissionAwareSerializerMetaclass` auto-injects the mixin on LexModel-backed serializers | Plain Django-model serializers untouched |
| 4.23 | `run_validation` — change detection | PATCH with same value as stored on a denied field passes validation (the frontend's "send the whole form back" pattern must not false-403) |
| 4.24 | `run_validation` — changed denied field raises `PermissionDenied` | Message names the field; non-superuser denied, superuser allowed |
| 4.25 | `lexReservedMeta` key bypasses the check | Real `public_name` change still goes through normally |
| 4.26 | `run_validation` — create path | Regular user POSTing `ProtectedItem` gets `PermissionDenied` (model name in the message); admin passes |

**Status:**  Complete — 8 pass / 0 fail. See progress.md Sessions 17 + 19.

### 4i. `LexModel` permission helper convenience methods ✅

**Gap:** `lex/core/models/LexModel.py` ships seven public **shorthand helpers** customers compose inside their own `permission_read` / `permission_edit` overrides — `allow_all_if_superuser`, `allow_all_if_in_groups`, `allow_fields_if_owner`, `keycloak_fallback`, `allow_all_except_sensitive`, `allow_public_fields`, `allow_basic_fields` — plus six **legacy `can_*(request)` adapters** (`can_read` / `can_edit` / `can_export` / `can_create` / `can_delete` / `can_list`) for back-compat with pre-`PermissionResult` customer code. Cluster 4a–4h test the *outcome* of permission overrides through HTTP, but never directly pin the helpers' input/output contract. A drift in any helper silently weakens every customer model that composes it: returning `False` instead of `None` from an "intermediate" helper breaks the documented `or`-chain pattern; returning the wrong field set from `allow_public_fields` leaks PII.

**Why a sub-cluster of 4 (Permissions):** these helpers compose with the same `UserContext` / `PermissionResult` building blocks 4a–4h test against. They are not their own feature — they are convenience shortcuts inside the existing permission API.

**Scenario numbering** runs **4.27 – 4.34** in the free band between 4f's last (4.26) and 4h's first (4.40).

**Models needed:**
- `ProtectedItem` (reused) — every helper that doesn't need an FK runs against this lightweight fixture.
- `FieldLevelItem` (reused) — drives `can_read`'s `Set[str]` collapse from a real `permission_read` override that returns `allow_fields(...)`.
- `OwnedItem` (new) — adds an `owner` FK to `auth.User` so `allow_fields_if_owner`'s ownership check is exercised against a real ORM lookup.

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 4.27 | `allow_all_if_superuser` | Superuser → `allow_all` with documented reason; non-superuser → `None` so the caller falls through; custom `reason` propagates |
| 4.28 | `allow_all_if_in_groups` | Bare-string argument is normalised to a one-element set; any overlap of user's groups with the required set allows; no overlap returns `None`; default reason mentions the matched groups |
| 4.29 | `allow_fields_if_owner` | Owner + explicit `fields=` → `allow_fields(...)`; owner + `excluded_fields=` → `allow_all_except(...)`; owner + neither → `allow_all`; non-owner returns `None`; unauthenticated short-circuits *before* the FK lookup; `owner_field` pointing at a missing attribute returns `None` (never raises) |
| 4.30 | `keycloak_fallback` is the **terminal** helper | Scope present → `allow_all`; scope missing → `deny` (not `None` — terminal helpers never return `None`); unrelated scope (e.g. `write` for a `read` check) does not satisfy |
| 4.31 | `allow_all_except_sensitive` | No-arg call uses the documented PII default set (`password`, `ssn`, `credit_card`, `bank_account`, …); explicit `sensitive_fields=` *replaces* the default rather than extending it |
| 4.32 | `allow_public_fields` / `allow_basic_fields` | Returns the documented allowlist (`{id, name, title, description, created_at, edited_at, updated_at}` for `public`; `{id, name, email, created_at}` for `basic`) — locks the customer-facing constant against accidental drift |
| 4.33 | Helper composition contract | Every "intermediate" helper returns `None` (not `False`, not a denied `PermissionResult`) when inapplicable, so the documented `allow_X() or allow_Y() or keycloak_fallback()` one-liner short-circuits at the first match. The `or`-chain is exercised end-to-end and the short-circuit is asserted. |
| 4.34 | Legacy `can_*(request)` adapters | Field-returning adapters (`can_read` / `can_edit` / `can_export`) collapse a `PermissionResult` to a `Set[str]` of allowed field names; boolean adapters (`can_create` / `can_delete` / `can_list`) return the predicate's `bool` directly. Drives both a regular user and a superuser through `FieldLevelItem` / `ProtectedItem`. |

**Status:** ✅ Complete — 26 pass / 0 fail. Covers `lex/test_project/tests/permissions/test_4i_permission_helpers.py`.

### 12f. Serializer write paths — M2M & nested FK 

**Gap:** `lex/api/serializers/base_serializers.py` still had ~108 missing stmts on the write side — the M2M and FK-nested branches. 12f closes them with three end-to-end scenarios driving real POST/PATCH against the One endpoints.

**Models:** `TagItem` + `TaggableItem` (M2M `tags` + nullable FK `primary_tag`) in `serializers/models.py`.

> **Scenario numbers:** the originally-planned 12.26–12.28 slots were reassigned to 12e factory-contract scenarios (canonical per cluster 12). 12f was renumbered to **12.29–12.31** in the April 23 session.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 12.29 | POST creates M2M through rows atomically | Through-table read back via ORM (not trusting the serializer echo) |
| 12.30 | PATCH with a different tag set **replaces** (not merges) | Guards the frontend's deselect UX from silently regressing |
| 12.31 | Nullable FK lifecycle | Attach-on-create → rewire via PATCH → detach to NULL |

**Status:**  Complete — 3 pass / 0 fail. See progress.md Sessions 18 + 29.

### 10e. Schema introspection — `create_field_info` + structure-tree pruning 

**Gap:** `ModelStructureObtainView.py` (102 stmts, 21.54% baseline) and `model_info/Fields.py` (67 stmts, 22.35% baseline) drive every frontend form + nav menu. A drift here renders the wrong widget for a field, or leaks denied models into the nav.

**Models:** new `SchemaFKTarget`, `SchemaItem` (one field per interesting type), `SchemaHiddenItem` (`permission_list → False`) in `api_layer/models.py`.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 10.11 | Django field → API-type mapping | `name → string`, `amount → int`, `ratio → float`, `active → boolean`, `day → date`, `when → date_time`, `payload → json`, `target → foreign_key`. Table-driven — a new type trips a named `subTest` failure |
| 10.12 | `editable` / `required` / `default_value` / `is_pk` flags | AutoField pk is `is_pk=True` + `editable=False`; field with `default=` is `required=False`; **surfaces BUG-015** — `CharField` without explicit default reports `required=False` because Django's `get_default()` returns `""` |
| 10.13 | FK metadata exposes `target` | `target == related_model._meta.model_name` — frontend uses it to fetch dropdown values |
| 10.14 | `delete_restricted_nodes_from_model_structure` prunes denied models | Folders that only contained denied models are collapsed; nav must not show empty folders |

**Status:**  Complete — 4 pass / 0 fail (+ BUG-015 surfaced, Open). See progress.md Sessions 18 + 20.

### 10f. Global search — `Search.py` 

**Gap:** 28 stmts, baseline **34.21%**. Small surface, user-facing — the nav-bar search box hits this endpoint.

**Models:** reuses `SchemaItem` from 10e (a varied-field model — `SearchVector` indexes the `name` CharField).

Shipped 4 scenarios. The view depends only on `self.model_collection` + `self.kwargs['query']`, so the tests build a `SimpleNamespace(all_containers=[...])` stand-in and drive `Search.get` directly — no URL wiring required. `UserPermission` is patched open; the exclusion-list contract is asserted independently.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 10.15 | Query matches a text field on a registered model | Response is `{data, total}`; hit has `id` / `model` / `content.description` / `url` (routing) |
| 10.15b | Zero matches returns the documented `"No match found"` sentinel string | Frontend branches on response type — drift would silently break the search box |
| 10.16 | Container whose `id` is in `EXCLUDED_MODELS` (`user`, `permission`, …) short-circuits *before* the query runs | No PII leak through global search even if system rows contain the query term |
| 10.16b | `EXCLUDED_TYPES` still contains every non-text field type (`FloatField`, `BooleanField`, `IntegerField`, `FileField`, `ForeignKey`) | Regression gate — if a non-text type slips out, `SearchVector` 500s at runtime |

**Status:**  Complete — 4 pass / 0 fail.

### 5.11 — History fallback-snapshot path 

**Gap (April 25):** `History.py` lines 180–201 — the per-field manual-serialization branch inside `_get_snapshot` that fires when a model-container has no registered `serializers_map['default']`. The existing 5c scenarios never hit it because every test-project LexModel ships a default serializer.

**Shape:** `SimpleTestCase` that drives `_get_snapshot` directly with a synthetic history record (a dynamically-built class whose `_meta.fields` yields `.name`-carrying fakes). Covers five branches in one scenario with named sub-assertions so regressions surface the exact drift.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 5.11 | Fallback snapshot contract | **(a)** `CONTROL_FIELDS` (`history_id` / `valid_from` / `sys_from` / `meta_task_status` / …) filtered out even when populated — frontend must not see system columns inside the business payload; **(b)** `datetime` → `isoformat()`; **(c)** `date` → `isoformat()`; **(d)** non-primitive object coerced via `str()` so DRF's JSON encoder doesn't blow up; **(e)** primitives (`int` / `bool` / `None`) + containers (`list` / `dict`) pass through unchanged |

**Status:**  Complete — 1 pass / 0 fail.

### 9.7 – 9.10 — Bitemporal suppression guards 

**Gap (April 25):** the three `ContextVar`-backed suppression guards (`suppress_main_table_sync`, `suppress_history_valid_to_chaining`, `suppress_meta_sys_to_chaining`) are consulted by every handler in the file — early-return at lines 118, 274, and the Level-2 meta-chaining guard. A drift in their lifecycle is how the BUG-011 chaining bottleneck compounds (leaked True → recursion; cross-contaminating state → wrong handler skipped).

Direct handler coverage of lines 170–340 would need a full history fixture (already exercised happy-path by 5a/5b/5c). This sub-cluster locks down the **suppression primitives** those handlers lean on.

**Shape:** `SimpleTestCase` — pure Python, no DB, no models. Runs in 1ms.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 9.7 | Guard lifecycle (before / inside / after) for all three guards | `ContextVar` defaults False; flips True on enter; resets False on exit. A leaked True across request boundaries silently skips bitemporal maintenance. |
| 9.8 | Nested suspension stacks and unwinds | Inner `with` exit does **not** deactivate the outer context — this is what the handlers' internal `with suppress_*(): record.save(...)` depends on to avoid unbounded recursion |
| 9.9 | Three guards are independent | Suspending `main_table_sync` must not suspend `valid_to_chaining` or `meta_sys_to_chaining` — the handlers rely on asymmetric combinations |
| 9.10 | Suspension is thread-local | Background thread sees `False` even while the parent thread holds a suspension — guarantees Celery-worker parallel requests don't silently share suspension state |

**Status:**  Complete — 4 pass / 0 fail.

### 9d — `ActiveCalculationStateStore` full surface (coverage-driven — May 12)

**Gap (May 12):** `lex/core/signals/ActiveCalculationStateStore.py` baseline **27.03%** (131 stmts, 86 missed). Two tests exist (9a/9b) but only exercise the store transitively through the `update_calculation_status` signal — the public accessors, the DB-validated `snapshot()` reconciliation path that the WebSocket consumer calls on every reconnect, the startup `validate_and_prune()` sweep, and the private model-resolution helpers (`_resolve_model_and_pk` / `_split_record_id` / `_find_model_by_name`) were all dark.

**Why it matters:** this store is the single source of truth that lets a re-connecting browser tab pick up the spinner mid-calculation. The previous DatabaseCache implementation lost entries written inside `transaction.atomic()` because the ASGI consumer ran on a different DB connection — the bug whose fix this whole file exists to protect. Anything that breaks `snapshot()` (stale entries leaking through, live entries disappearing) directly regresses the customer-visible "did my calculation crash or am I just disconnected?" UX.

**Shape:** `SimpleTestCase` — pure Python, no DB. Models are unmanaged (`Meta.managed = False`); DB-touching paths are MagicMock-driven via `patch.object(ActiveCalculationStateStore, '_resolve_model_and_pk', …)`. 24 tests run in 0.009s.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 9.11 | `mark_in_progress` with empty `record_id` is a no-op | Early-return guard; store stays empty (line 56) |
| 9.12 | `mark_in_progress` persists full payload | All 5 fields land verbatim; `int` pk normalised to `str` so JSON-serializable downstream |
| 9.13 | Optional fields default to `''` not `None` | `record` falls back to `record_id`; `calculation_id` / `model_label` / `record_pk` blank-string defaults — downstream consumers iterate values directly so `None` would force `or ""` everywhere |
| 9.14 | `clear('')` is a no-op | Symmetric early-return guard (line 71); existing entry untouched |
| 9.15 | `clear` removes entry and is idempotent | Second `clear` of same id silent — no `KeyError`, no log spam |
| 9.16 | `clear_all` empties every entry | Startup-only sweep; works regardless of size |
| 9.17 | `mark_in_progress` overwrites existing entry | Re-marking same id replaces prior entry (re-fire after ABORTED reset) |
| 9.18 | `get_calculation_id` returns string when set | Live entry → calc id string |
| 9.19 | `get_calculation_id` returns None for missing or blank | Both dark branches: dict.get default + `isinstance(...) and calculation_id` truthiness guard (line 88) |
| 9.20 | `get_entry('')` returns `{}` | Symmetric defensive guard (line 94) |
| 9.21 | `get_entry` returns a defensive copy | Mutating result must NOT affect store; pins `dict(entry)` copy (line 99) — regression to bare `return entry` would let snapshot consumers mutate under the lock |
| 9.22 | `_split_record_id` parses `model_pk` on rightmost `_` | Handles model names containing underscores (`my_calc_model_7` → `("my_calc_model", "7")`) |
| 9.23 | `_split_record_id` rejects malformed input | Empty / no underscore / blank halves all → `(None, None)` |
| 9.24 | `_find_model_by_name` walks app registry | Returns `CalculationModel` subclass when match exists; `None` for unknown names |
| 9.25 | `_resolve_model_and_pk` prefers explicit `model_label` | `app_label.ModelName` resolves via `apps.get_model` before `record_id` parsing |
| 9.26 | `_resolve_model_and_pk` falls back to `record_id` parsing | No `model_label` → split + walk app registry |
| 9.27 | `_resolve_model_and_pk` rejects non-`CalculationModel` classes | Without this guard `snapshot()` would call `.objects.filter(...)` on arbitrary models and could leak unrelated state into the WebSocket payload |
| 9.27b | `_resolve_model_and_pk` returns `(None, None)` on full resolution failure | Empty entry early-out + `apps.get_model` raise → registry-walk fallback that also fails — no exception bubbles |
| 9.28 | `snapshot()` empty-store fast path | No entries → `[]` returned without DB hit |
| 9.28b | `snapshot()` returns live entries and prunes stale | Live IN_PROGRESS pass through; terminal-state entries dropped from BOTH the payload and the store — the WebSocket reconciliation contract |
| 9.28c | `snapshot()` keeps entry on DB exception | Defensive: better a possibly-stale spinner than a silently-dropped live calculation on a DB blip |
| 9.28d | `snapshot()` skips DB validation when resolver returns `(None, None)` | Unresolvable entry passes through unchecked; pins the `if model_class is not None and record_pk is not None` guard |
| 9.28e | `validate_and_prune()` keeps only IN_PROGRESS rows | Empty-store fast-path safe; stale (terminal state) / gone (instance None) / unresolvable all dropped |
| 9.28f | `validate_and_prune()` drops entry on DB exception | Documented behavioural difference from `snapshot()` — startup sweep is conservative-rebuild ("only keep what we can positively confirm"); a regression that adds "keep on exception" would have to revisit this test |

**Status:**  Complete — 24 pass / 0 fail / 0.009s. Coverage: 27.03% → ~95%+ (whole file minus 1-2 unreachable defensive branches).

### 9e — Generic CRUD mutation broadcast (live list refresh — June 3)

**Gap (June 3):** the framework broadcast *calculation* state changes over WebSocket (frontend listens, refreshes the AG Grid), but ordinary CRUD on a **non-`CalculationModel`** record emitted **no** WebSocket traffic at all. A list view open in another tab, iframe, or window silently went stale — the customer had to press "Refresh" to see a newly created/updated/deleted row. New surfaces: a generic `model_data_update`-group `record_mutation` broadcast emitted from every REST mutation entry point, and the consumer that fans it out.

**Covers:** `lex/core/signals/ModelMutationSignal.py` (new — `broadcast_model_mutation` defers a group send via `transaction.on_commit`), `lex/api/consumers/ModelDataUpdateConsumer.py` (new — joins `model_data_update`, forwards verbatim), `lex/lex_app/routing.py` (route `ws/model_data_update`), `lex/api/views/model_entries/One.py` (create/update/destroy; skips the generic broadcast when `calculate=true`), `lex/api/views/model_entries/Many.py` (bulk patch/delete).

**Shape:** U (helper message-shape) + I (`TestCase` — on_commit deferral, empty-name no-op) + U (`SimpleTestCase` consumer) + E (`E2ETestCase` — real REST CRUD end-to-end; `TransactionTestCase` so commits fire `on_commit`).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 9.29 | `build_model_mutation_message` envelope | type `record_mutation`; payload carries `model_name`/`action`/`record_id` so the frontend can match the open resource |
| 9.30 | Empty `model_name` is a no-op | defensive guard — nothing ever reaches the channel layer |
| 9.31 | Broadcast deferred until commit | 0 sends before commit, exactly 1 to `model_data_update` after — emitting early would let a client refresh and miss the just-written row |
| 9.32 | Consumer joins group + forwards payload | `connect` adds to `model_data_update`; `record_mutation` forwards JSON to the socket verbatim |
| 9.33 | POST create emits `created` | real REST create broadcasts a `record_mutation` for the model |
| 9.34 | PATCH update emits `updated` | real REST update broadcasts |
| 9.35 | DELETE emits `deleted` | real REST delete broadcasts |
| 9.36 | Bulk DELETE (`Many` endpoint) emits `deleted` | bulk path broadcasts too |

**Status:** Complete — 8 pass / 0 fail locally (Postgres test DB available).

### 9f — Core health/calculation/log WebSocket consumers ✅

**Gap:** PR #615 touched the core WebSocket consumer files that the frontend uses
for public backend health, calculation notifications, and live calculation logs.
These are long-lived sockets, so regressions leak active consumer references,
stop shutdown cleanup, or silently break the JSON envelopes frontend listeners
dispatch on.

**Covers:** `lex/api/consumers/BackendHealthConsumer.py`,
`lex/api/consumers/CalculationsConsumer.py`,
`lex/api/consumers/CalculationLogConsumer.py`.

**Shape:** U (`SimpleTestCase`) with mocked channel layer and socket boundary.
No broker, Redis, or database required.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 9.37 | Backend health socket accepts and returns Healthy payload | `connect()` accepts and tracks the consumer; `receive()` sends `{"status": "Healthy :)"}` |
| 9.38 | Backend health disconnect untracks connection | disconnect removes the consumer from the process-global active set |
| 9.39 | Calculations socket joins group and forwards events | joins `calculations`; forwards `calculation_id` events and `calculation_notification` payloads in the frontend contract shape |
| 9.40 | Calculations disconnect leaves group | discards `test-channel` from `calculations` and untracks the consumer |
| 9.41 | Calculation-log socket groups by record prefix and streams logs | `calculationId="record-..."` joins group `record`; sends `calculation_log_real_time` envelope with `logs` |
| 9.42 | Shutdown `disconnect_all` closes tracked consumers | all three classes call `disconnect(None)` on a snapshot of active consumers |

**Scenario range:** 9.37 – 9.42. **Test file:** `lex/test_project/tests/signals_ws/test_9f_core_consumers.py`. **Type:** U. **Status:** ✅ Complete (Session 81 — June 18). `CalculationLogConsumer.py` is no longer parked because PR #615 wires it in `authenticated_websocket_urlpatterns()`.

### 7g — `CalculatedModel.create()` pipeline (end-to-end) 

**Gap (April 25):** `CalculatedModelMixin.py` baseline **33.74%** after 7a–7f. The remaining 369 missing statements were concentrated in the four-step orchestrator invoked by `Model.create(**overrides)`:

1. `_generate_model_combinations` (1346-1401)
2. `_prepare_models_for_processing` (1403-1494)
3. `_create_processing_clusters` (1497-1576)
4. `_dispatch_model_processing` — sync branch (1579-1713)
5. `calc_and_save_sync` (843-971)
6. `delete_models_with_same_defining_fields` (1715-1807)

**Model:** new `CombinatorialCalc` — a non-atomic `CalculatedModelMixin` subclass with `defining_fields = ["region", "category"]` and `parallelizable_fields = ["region"]`. A single `create()` call walks every one of the six sections above.

**Shape:** `E2ETestCase` — runs under `CELERY_ACTIVE=False` (the documented sync fallback); same environment every other Cluster 7 scenario uses.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 7.25 | `Model.create(region=[...], category=[...])` cartesian expansion | 3×2 = 6 rows persisted, each with `name` set by `calculate()`; exercises combination generator → prepare → cluster → dispatch → `calc_and_save_sync` |
| 7.26 | `Model.create()` with no kwargs falls back to `get_selected_key_list` | Default 2×2 = 4 rows, one per `(region, category)` combo |
| 7.27 | Partial failure — `calculate()` raises for one region | `calc_and_save_sync` catches + accumulates the error and keeps going; failed rows are NOT saved; successful rows persist; "processed_count > 0" warning branch fires |
| 7.28 | `Model.create()` is idempotent on rerun | `delete_models_with_same_defining_fields` detects existing rows; pk set unchanged between runs |
| 7.29 | Empty `get_selected_key_list` return prunes the whole branch | Zero rows, no error — exercises the `if not field_values: continue` + early-break |
| 7.30 | `delete_models_with_same_defining_fields` on un-saved instance | Returns `self` and resets a stale pk to `None` so caller can INSERT |

**Status:**  Complete — 6 pass / 0 fail. Drove `CalculatedModelMixin.py` from **33.74% → 64.75%** (+31 pts, +170 lines covered).

### 1i. Initial-data upload — full journey end-to-end 

**Gap (April 24):** `InitialDataAuditLogger` (148 stmts, **12.64%** baseline) + `ProcessAdminTestCase` seed walker (`replace_tagged_parameters`, `get_test_data_from_path`, seed dispatcher). Never driven end-to-end by Cluster 1c — 1c only asserts post-`Init` database state, not the intermediate audit trail or the JSON-walker contracts.

**Intent** (per `docs/lex_topics/16-initial-data-upload.md`): seed files declare production-shaped data in JSON; on server start if every referenced model is empty, the framework walks the JSON top-down and applies `create` / `update` / `delete` actions in declaration order. `tag:` prefixes resolve to in-memory objects created earlier; `datetime:` strings parse through `dateutil`; `{"subprocess": path}` entries flatten recursively. Every operation emits an `AuditLog` + `AuditLogStatus(pending)` pair; `mark_operation_success/failure` advances the status; `finalize_batch` sweeps lingering pendings so the compliance view is always consistent.

**Models:** reuses `SimpleItem` from `crud_api/models.py` — a minimal LexModel with `name` + `value`. **Fixtures** (all new in `tests/fixtures/`): `seed_parent.json` (2 subprocess refs), `seed_child_01.json` (1 create), `seed_child_02.json` (2 creates), `test_seed_journey.json` (2 creates + 1 update + 1 delete).

**Shape:** five test classes — two `SimpleTestCase` (pure unit, no DB) + three `E2ETestCase` (real `AuditLog` / `AuditLogStatus` / `SimpleItem`). `ProcessAdminTestCase.setUp` is driven via a `runTest = lambda: None` sub-class and a monkey-patched `get_test_data`; `apps.get_app_config` is stubbed so the harness sees `SimpleItem` without registering the test project as an installed app.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 1.51a | `tag:foo` resolves to previously-stored in-memory object | FK-by-reference mechanism — literals without a known prefix pass through unchanged |
| 1.51b | `datetime:YYYY-MM-DD` parses via `dateutil.parser` | Seed dates ship as strings; models receive real `datetime` |
| 1.52 | Recursive subprocess flattening preserves declared + internal order | Parent-before-child FK resolution depends on this ordering |
| 1.54 | `log_object_creation` writes AuditLog + pending AuditLogStatus | `author = "system (initial_data_upload)"`, `_audit_tag` in payload for trace-back |
| 1.54b | `_logged_ids` tracks every log in declaration order | `finalize_batch` sweep is scoped by this list — must not touch earlier sessions |
| 1.55 | `log_object_update` embeds instance pk in payload | Compliance view links audit row → DB row |
| 1.56 | `log_object_deletion` snapshots the instance via `generic_instance_payload` | Deleted row's fields survive in the audit trail after the main row is gone |
| 1.57 | `mark_operation_success` / `mark_operation_failure` advance status; idempotent | Retry must not duplicate status rows |
| 1.57b | `mark_operation_*` on `None` audit log is a no-op | The loader passes whatever `log_object_creation` returned — can be `None` |
| 1.58a | `finalize_batch()` clean run resolves lingering pending → success | Summary reports `pending_resolved` count (signal that a handler forgot to mark) |
| 1.58b | `finalize_batch(failure_error=…)` collapses pending → failure with error string | Outer-driver exception path — audit trail closes out even for aborted runs |
| 1.59 | Full create/update/delete drive with audit (env-gated) | DB has correct surviving row; 4 AuditLog rows in `[create, create, update, delete]` order; all statuses `success` |
| 1.59b | Full journey without audit still lands DB state | Audit is observability — disabling it must not regress data transitions |
| 1.60 | Crash path — `finalize_batch(failure_error=…)` after 3 pending ops | All 3 swept to `failure` with error string; `pending_resolved == 3` |

**Status:**  Complete — 13 pass / 0 fail / 1 env-gated skip (1.59). Targets ~140 lines in `InitialDataAuditLogger.py` + ~40 in `ProcessAdminTestCase`.

### 8j. Celery task bodies — `load_data` / `calc_and_save` / `activate_history_version` 

**Gap (April 24):** 8g–8i intentionally kept Celery tests broker-free so the normal suite stays deterministic: patched `.delay`, eager mode, and direct task-body invocation cover the Lex framework logic without requiring Redis. That leaves one environment-level risk unpinned: a real Celery producer must be able to publish to Redis, a worker must consume from Redis, and the result backend must return the payload.

**Shape:** two opt-in scenarios in `test_8k_redis_broker_integration.py`. The first is a `SimpleTestCase` JSON-safe smoke task that switches the Celery app to a Redis broker/result backend, starts an in-process Celery worker with `celery.contrib.testing.worker.start_worker`, publishes to a unique queue, and calls `AsyncResult.get()` through `allow_join_result()`. The second is an `E2ETestCase` using a real `CalculationModel` fixture and `WaitForTasks`; it dispatches the decorated bound `calculate()` method over Redis, blocks on the returned `AsyncResult`, and asserts `CallbackTask.on_success` persists `SUCCESS` plus reaches the terminal audit seam. Both producers pass an explicit temporary Redis connection so Celery app producer-pool caching cannot leak a previous broker URL into the example.

**Environment gate:** skipped by default. To run it, set `LEX_RUN_REDIS_CELERY_TESTS=true`; optionally set `LEX_CELERY_REDIS_TEST_URL` (default `redis://127.0.0.1:6379/15`). This keeps CI/laptops without Redis green while still giving DevOps a one-command real-broker check.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 8.45 | Producer → Redis broker → in-process Celery worker → Redis result backend | Task is received and executed by the worker, result round-trips with a correlation id, and `task_always_eager` is false so this is not the eager-mode path. |
| 8.46 | `CalculationModel` + `WaitForTasks` over Redis broker | A persisted `CeleryCalc` in `IN_PROGRESS` dispatches via the real `EnhancedBoundTaskMethod`/`WaitForTasks` path to Redis, the worker runs the decorated `calculate()` task, `WaitForTasks` drains the `AsyncResult`, `CallbackTask.on_success` flips the row to `SUCCESS`, and terminal audit is invoked. |

**Status:**  Complete — 2 broker-backed passes when Redis is available; env-gated skips otherwise. The reusable `celery_redis_broker_example.yml` workflow runs the examples with PostgreSQL + Redis services and is called by `pip_publish.yml` before PyPI publishing.

### 1j. Keycloak client safety pre-flight — mocked 

**Gap (April 25):** `lex init` mutates the configured Keycloak client's resources / policies / permissions. Without a pre-flight gate, an operator who points the framework at a STANDARD or production client by accident silently rewrites authorization config — the very accident the controller's `is_confidential` + `client_type="DEVELOPMENT"` invariants exist to prevent on the create side.

**Shape:** `TestCase` with mocked `kc_manager` (same `_make_sync_manager()` pattern as 1e / 1g — bypass `__init__`, stub `kc_manager.admin`).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 1.71 | Confidential + localhost redirect passes | Returns the rep; `admin.get_client` called once with `client_uuid` |
| 1.72 | `http://localhost:8000/*` is a localhost redirect | Port number doesn't matter — only the parsed hostname does |
| 1.73 | Only-localhost (no prod URI) is still a valid dev client | DEV-only test fixtures are not penalized |
| 1.74 | `publicClient=true` raises | Message names the client + 'confidential'; mentions `publicClient=true` |
| 1.75 | Missing `publicClient` flag is treated as unsafe | Never assume confidential by default |
| 1.76 | Prod-only `redirectUris` raises | Message names DEVELOPMENT + localhost + offending URI + clientId |
| 1.77 | Empty `redirectUris` raises with `<empty>` sentinel | Operator can tell the list was blank vs. populated-but-wrong |
| 1.78 | Missing `redirectUris` field raises | Malformed rep is rejected, not assumed-empty |
| 1.79 | `redirectUris` set to a string raises | Type check is real, not duck-typed |
| 1.80 | `localhost.example.com` does NOT match | Proves we use parsed `.hostname`, not substring |
| 1.81 | `_redirect_uris_indicate_development` accepts every dev shape | Case-insensitive host matching across http/https + ports |
| 1.82 | Helper rejects production / 127.0.0.1 / look-alike hosts | Loopback IP is NOT 'dev' — controller only emits literal `localhost` |
| 1.83 | Helper skips non-string entries (None, int, dict) | Mixed list with one valid localhost entry still passes |
| 1.84 | `KEYCLOAK_DEV_REDIRECT_HOST == "localhost"` is pinned | Any change is deliberate (touches both halves of the contract) |
| 1.86 | Empty `client_uuid` raises BEFORE any HTTP call | `admin.get_client.assert_not_called()` |
| 1.87 | `admin.get_client` raising → `CommandError` wraps + chains | `__cause__` is the original exception |
| 1.88 | Non-dict response shape raises | Defensive against SDK contract drift |
| 1.89 | Failing pre-flight aborts `init` BEFORE `process_model_changes` | `mgr.process_model_changes.assert_not_called()` |
| 1.90 | `--skip-client-preflight` short-circuits pre-flight | `admin.get_client.assert_not_called()`; stdout names the flag; sync still runs |

**Status:**  Complete — 20 pass / 0 fail in 0.007s. See progress.md Session 38.

### 1k. Keycloak client safety pre-flight — REAL Keycloak integration 

**Companion to 1j.** Drives `verify_client_is_safe_for_init` against a **live** Keycloak server using credentials from repo secrets / `os.environ` or the gitignored `lex/test_project/tests/init/.env` file — no mocks, no canned responses, every assertion bottoms out in an HTTP round-trip. Only a live server actually proves: (a) `KeycloakManager` initialization works end-to-end with the configured token endpoints; (b) the admin REST API actually accepts the response shape we parse; (c) the configured client's `publicClient` + `redirectUris` round-trip verbatim across the SDK boundary.

**Gating:** TWO levels — (a) `LEX_RUN_KEYCLOAK_INTEGRATION=1` must be set to enable; (b) the configured client must satisfy the 1j pre-flight (confidential + DEVELOPMENT) so the integration tests cannot be turned on against a production client by accident. Both gates fail-closed — the tests skip rather than error when the env is incomplete.

**Shape:** `TestCase` with the real `KeycloakManager` SDK; 4 read-only scenarios covering happy-path verification, live representation shape, env-var round trip through dotenv/repo-secret injection, and the pinned localhost dev-host constant.

**Status:**  Complete — 4 env-gated integration tests, all skip cleanly without live Keycloak, all pass against the configured dev tenant when integration env is wired. See progress.md Session 38.

### 1l. `lex init` full pipeline — REAL Keycloak integration 

**Companion to 1b (mocked `lex init` end-to-end) and 1f (Keycloak drift coverage with stubbed manager).** Drives the **same code path the real `lex init` command runs** against a live Keycloak server. Mocked tests cover *contract*; live tests prove three things only a real server can: (1) the Keycloak admin REST API actually accepts the payloads `KeycloakSyncManager` builds (schema drift on Keycloak's side fails here before production); (2) end-to-end timing works (token refresh, multi-call sequences, no race against the authz-import endpoint); (3) `last_authz_import_error` round-trips to `None` on success — what `Command.handle` actually checks.

**Gating:** TWO levels — (a) `LEX_KEYCLOAK_INTEGRATION=1` must be set to enable; (b) the configured client must satisfy the 1j pre-flight (confidential + DEVELOPMENT) so the integration tests cannot be turned on against a production client by accident. Both gates fail-closed — the tests skip rather than error when the env is incomplete.

**Shape:** `E2ETestCase` with real `KeycloakManager` + real `KeycloakSyncManager`; 7 scenarios driving `Command.handle` end-to-end across happy-path full sync, `--dry-run` no-op, drift recovery, idempotent rerun, snapshot/restore round-trip, `--skip-client-preflight` against real client, and `last_authz_import_error → None` assertion.

**Status:**  Complete — 7 env-gated integration tests, all skip cleanly without live Keycloak, all pass against the configured dev tenant when integration env is wired. See progress.md Session 38.

### 1m. `lex` CLI ↔ PyCharm `.run.xml` cross-file contract 

**Gap (April 25):** `generate_pycharm_configs.py` writes 16 `.run/*.run.xml` files an operator can click in PyCharm; each invokes the `lex` CLI with a specific subcommand. 1a covered three of those files (Init / Start / Streamlit); per-builder helpers in `lex/tests/unit/cli/test_lex_cli.py` validate Celery / Flower / MCP individually; nothing asserted "every PyCharm-clickable subcommand actually resolves through the CLI". A rename in either file would silently break a PyCharm action — caught only by a developer trying to use it.

**Shape:** `SimpleTestCase` only — fast, in-process (Click's `CliRunner`, no subprocess), Django bootstrap once per class for the dynamic-command lookup. Scaffolds `.run/` into a per-test `TemporaryDirectory` so the live project's `.run/` is never touched.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 1.102 | Generated `.run.xml` set parity | Exactly the 16 expected files, no orphans, no missing — canary against "removed Test_Audit but constant still has it" drift |
| 1.103 | Every `.run.xml` `SCRIPT_NAME` is `lex` | A copy-paste of a different binary cannot bypass the CLI's env handling / Django bootstrap |
| 1.104 | First-token of every `.run.xml` `PARAMETERS` resolves | Either explicit `@lex.command(...)` Click handler OR registered Django management command — the cross-file contract that nothing else asserts |
| 1.105 | Explicit Click registry pinned | `celery` / `celery-workers` / `flower` / `streamlit` / `start` / `setup` / `setup-with-ai` / `ai-update` / `ai-faq` — removing one is what would silently break a `.run.xml` |
| 1.106 | `_SKIP_BOOTSTRAP_COMMANDS` is a subset of explicit registry | Otherwise listed names silently fall through to dynamic forwarding without `django.setup()` |
| 1.107 | `lex --help` exits 0 and names every explicit command | Click group itself wired correctly |
| 1.108 | `lex <cmd> --help` exits 0 for every explicit handler | Catches decorator typos / signature regressions that `--help` surfaces but real runs would mask |
| 1.109 | Every Django-side subcommand referenced by a `.run.xml` is registered | `init` / `migrate` / `makemigrations` / `flush` / `test` / `create_db` resolve through Django's command loader — otherwise dynamic forwarding produces a less-helpful error |

**Status:**  Complete — 8 pass / 0 fail in 0.020s. See progress.md Session 40.

### 5g. History `valid_to` chaining contract  — implemented

**Gap (May 5):** Cluster 5.4 documents the contract "`valid_to` of row N = `valid_from` of row N+1" but the implemented test only asserts ascending `history_id`. The chaining is the very thing that makes the bitemporal timeline contiguous (latest row carries `valid_to=NULL`); a regression here would silently produce gaps or overlaps in the timeline that no other test sees.

**Models:** reuses `HistSimpleItem` from Cluster 5.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 5.61 | Three saves chain `valid_to → valid_from` end-to-end | For an ordered list of 3 history rows, `rows[0].valid_to == rows[1].valid_from`, `rows[1].valid_to == rows[2].valid_from`, `rows[2].valid_to is None` (latest row open-ended) |
| 5.61b | Delete closes the chain | After `delete()`, the `-` row's `valid_from` matches the previous row's `valid_to`; the `-` row's `valid_to` is `None` |

**Status:**  Implemented (Session 51). 5.61 + 5.61b both pass. See progress.md Session 51.

### 5h. History suppression toolkit (per-instance, per-save, bulk, model-level, calculation-level)  — implemented

**Gap (May 5):** `docs/features/tracking/history.md` + `bitemporal history.md` document **five** distinct suppression toggles. Cluster 5.5 covers exactly one (`skip_history_when_saving`). The remaining four — each customer-facing — are dark.

**Models:** reuses `HistSimpleItem`; one new `UntrackedItem` model with `skip_history_when_saving = True` baked in via `untracked_models` (deferred).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 5.62 | `obj.save_without_historical_record()` | Single save with no history row appended; subsequent normal `.save()` resumes history (proves it's a single-save toggle, not a sticky flag) |
| 5.63 | `obj.untrack()` followed by `obj.save()` then `obj.track()` then `obj.save()` | First save produces no history row, second save produces a `~` row — proves the toggle is sticky between calls and `track()` re-enables |
| 5.64 | `Model.objects.bulk_create(objs, skip_history=True)` | N rows persisted, 0 history rows; subsequent `.save()` on one of those rows then produces a `~` history row (catching the case where `bulk_create` would otherwise leave the instance permanently untracked) |
| 5.65 | `bulk_create` without `skip_history` | Documented bulk-path behaviour: per-row history rows ARE created (this is the "make sure the default still works" gate) |
| 5.66 | `with suspend_bitemporal(): obj.save()` | Inside the block: zero L1 rows, zero L2 rows, exactly 1 raw INSERT/UPDATE; outside the block: full bitemporal chain runs again. Pins the documented "1 query inside, normal cost outside" contract from `bitemporal history.md` |
| 5.67 | `untracked_models` declared in `model_structure.yaml` | No `Historical*` table generated for the model; `model.history` manager raises / returns no rows. ⏸ deferred — needs a fresh test project with `model_structure.yaml`-loaded config to avoid mutating the live test_project model registry |

**Status:**  Implemented (Session 51). 5.62–5.65 pass; 5.66 (`suspend_bitemporal()` CM) tracked as `@expectedFailure` — docs reference it but only the lower-level guards (covered by 9.7–9.10) are exposed today; 5.67 deferred (fixture-shaped). See progress.md Session 51.

### 5i. History API contract — response shape + `as_of` time-travel  — implemented

**Gap (May 5):** Cluster 5.9 only asserts `200 OK + ≥3 rows`. The documented JSON contract from `bitemporal history.md` is much wider, and the `?as_of=...` system-time time-travel branch (the entire reason MetaHistory exists) is uncovered. A silent contract drift here would break the History tab UI without tripping any existing test.

**Models:** reuses `HistSimpleItem`; new helper `UserHistItem` with a `history_user` FK so the actor is observable on the response.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 5.71 | `GET /history/` response shape | Each row carries `history_id`, `valid_from`, `valid_to`, `history_type`, `user` (`{id, email, name}` or `null`), `snapshot` (full field map), `system_history` (list of L2 records) — exactly the keys documented in `bitemporal history.md` |
| 5.72 | `get_queryset_as_of(Model, t)` — valid time | Returns history rows where `valid_from <= t AND (valid_to > t OR valid_to IS NULL)`; pre-`t` and post-`t` rows are filtered out |
| 5.73 | `get_queryset_as_of(HistoryModel, t)` — system time | Auto-detects history-model class, returns L2 meta rows with `sys_from <= t AND (sys_to > t OR sys_to IS NULL)` — answers "what did the system *believe* was true at t" |
| 5.74 | `GET /history/?as_of=2026-02-01T00:00:00Z` | Endpoint returns the L2 snapshot at that system time (the As-Of UI control's contract). Asserts the rows match the `get_queryset_as_of(HistoryModel, t)` set from 5.73 |

**Status:**  Implemented (Session 51). 5.71/5.72 pass; 5.73/5.74 (system-time `as_of` + `?as_of=...` REST branch) auto-skip on missing L2 fixture (covered at the unit level by `lex.tests.unit.api.test_history_endpoint` + `lex.tests.unit.infra.test_bitemporal_service`). See progress.md Session 51.

### 5j. History snapshot completeness + `history_user` actor  — implemented

**Gap (May 5):** Docs guarantee each L1 row carries every field's value at that moment. No test asserts this — only counts and types. Same for `history_user`: docs say "ForeignKey(User) — Who made the change" but the API path actor is not pinned.

**Models:** `HistSimpleItem` (existing) + `UserHistItem` (small `LexModel` with one tracked field, used to inspect `history_user` after API saves).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 5.75 | After update, the new history row carries every model field's value | For a 4-field model, the `~` row has all 4 field values matching the post-update state; the prior `+` row has all 4 matching the pre-update state — proves the snapshot is full, not a diff |
| 5.76 | API-driven save stamps `history_user` to the authenticated user | POST + PATCH via `force_login`'d user → `item.history.first().history_user_id == user.pk` (or `history_user.email` matches). The `history_change_reason` field is `None` by default — also pinned so a default change is caught |

**Status:**  Implemented (Session 51). 5.75 (full snapshot, not a diff) + 5.76 (`history_user` actor stamping on the API path) both pass. See progress.md Session 51.

### 5k. MetaHistory positive contract  — implemented

**Gap (May 5):** 9.7–9.10 cover the suppression *primitives* (ContextVars), but no test asserts that a save() actually *creates* a MetaHistorical row, that `sys_from`/`sys_to` chain, or that an `history_object` FK points back to L1. The full bitemporal signal chain documented in `bitemporal history.md` "How the Signal Chain Works" is therefore not gated.

**Models:** reuses `HistSimpleItem` from Cluster 5.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 5.81 | Single save → exactly 1 L2 row | After `obj.save()` (create), `MetaHistoricalHistSimpleItem.objects.count() == 1`; the row's `history_object_id == obj.history.first().history_id`; `sys_to is None`; `meta_history_type == "+"` |
| 5.82 | Three saves chain `sys_to → sys_from` | Identical contract to 5.61 but on the L2 table — proves `chain_sys_to` runs |
| 5.83 | Retroactive `valid_from` correction (the docs example) | (a) save with default `valid_from=now`, (b) save again with `valid_from=earlier_date` — L1 has 2 rows with the new row chained into the timeline; L2 has 2 rows with `sys_from` reflecting the *clock time* of each correction (NOT the customer-supplied `valid_from`) |
| 5.84 | `meta_task_status` defaults to `NONE` for direct saves | Scheduled bitemporal activations bump it to `SCHEDULED → ACTIVE` (closing the read side of the contract `activate_history_version` writes against — see 8j scenario 8.43) |

**Status:**  Implemented (Session 51). 5.81/5.82/5.84 pass; 5.83 (retroactive `valid_from` correction) tracked as `@expectedFailure` — documented intent the framework does not yet accept on user-supplied saves. Companion to 8.43 — closes the producer side of the activation contract that the worker side already pins.

### 6d. Audit-log payload + GenericForeignKey contract  — implemented

**Gap (May 5):** 6.1/6.2/6.3 are thin: count + status only, with one `assertIn("value", payload)` for update. The Audit Log Tab UI's documented columns (`date`, `author`, `resource`, `action`, expandable JSON `payload`, link to record via `content_type` + `object_id`, `calculation_id` link to calc log) are mostly unpinned. **Critical:** without `content_type` + `object_id`, the per-record Audit Log Tab cannot find rows, but no test catches a regression.

**Models:** reuses `AuditSimpleItem` from Cluster 6.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 6.41 | Create audit row → `content_type` + `object_id` populated | After `POST /api/<model>/`, `audit_log.content_type == ContentType.objects.get_for_model(AuditSimpleItem)` and `audit_log.object_id == created_pk`. The `calculatable_object` GFK resolves back to the row. Without these, the Audit Log Tab UI cannot list operations affecting a specific record |
| 6.42 | Create audit payload carries the *full* request body + the post-save `id` | `payload == {"name": ..., "value": ..., "id": <created_pk>}` — the documented "full data + id-after-save" shape (line 227–231 of `AuditLogMixin.py`) |
| 6.43 | Update audit payload is *refreshed to final state* on success | `audit_log.payload` after PATCH equals the *full GET-shape* serialized representation including every field, not just the patched ones (the line 260 `payload = self.get_serializer(instance).data` contract). This is what makes audit logs reconstructable into "what the row looked like after this change" |
| 6.44 | Delete audit payload preserves the deleted record's pre-delete state | After DELETE, `audit_log.payload` carries every field's value at the moment of deletion + `id`. Docs: "you can always inspect what was removed" — currently no test asserts this |
| 6.45 | Failed `pre_validation` on POST → failure audit row | `pre_validation` raises → response 400/500, `AuditLogStatus.status == 'failure'`, `error_traceback` contains the exception class name and message, no DB row created. Replaces the previously-skipped 6.4 — reachable today through validation hooks (`PreValidatedItem`-style fixture), no middleware needed |
| 6.46 | Failure audit traceback round-trips through `resolve_exception_traceback` | Multi-line traceback string preserved — operators need full diagnostic info, not just the exception message |
| 6.47 | Atomic-block failure queues a replacement audit row | When `perform_create` fails inside an atomic block (`transaction.get_connection().in_atomic_block`), the in-flight failure status row rolls back with the request, and `_pending_failed_audit_logs` carries the queued replacement so the request-level fallback can persist it. Pins the line 238–246 branch |
| 6.48 | Pending state observable mid-flight | A `perform_create` paused in the serializer save (e.g. via a `pre_save` signal that captures status mid-call) sees `AuditLogStatus.status == 'pending'`. Documents the documented  → / lifecycle from the Audit Log Tab |

**Status:**  Implemented (Session 51). 6.41–6.46 pass live — including 6.45/6.46 which were planned as `@expectedFailure` but the framework already writes the failure audit row through the validation-hook path, so they stand as live regression gates. 6.47/6.48 auto-skip on missing fixture (atomic-block reentrancy + mid-flight pending observation). See progress.md Session 51.

### 6e. Bulk audit logging — `BulkAuditLogMixin`  — implemented

**Gap (May 5):** Docs explicitly say "a bulk update of 100 records creates 100 audit log entries". Cluster 2e's bulk DELETE scenarios (2.23/2.24/2.25) never assert the audit row count. `BulkAuditLogMixin` (167 stmts) is dark.

**Models:** reuses `AuditSimpleItem`.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 6.51 | `DELETE /many/?ids=1,2,3` → 3 audit rows | One audit row per deleted record, all with `action='delete'`, payload carrying the deleted row's pre-delete state, status `success` |
| 6.52 | Bulk delete with one denied / failing row | The deletable rows produce success audit entries, the failing row produces a failure entry — the partial-success contract |
| 6.53 | Bulk delete preserves per-row `content_type` + `object_id` | Each audit row's GFK points back to its own pre-delete instance — Audit Log Tab on each individual record still works after the bulk op |

**Status:**  Implemented (Session 51). 6.51 passes; 6.52 (audit row count under `bulk_create`) auto-skips on missing fixture. See progress.md Session 51.

### 6f. Audit-log resilience — deadlock retries + ContentType cache healing  — implemented

**Gap (May 5):** Docs "Resilience" section calls out two contracts. Both unpinned.

**Models:** reuses `AuditSimpleItem`.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 6.61 | Save raises `OperationalError(pgcode='40P01')` once, then succeeds | Retried automatically up to `MAX_UPDATE_RETRIES` (3); final state is success; backoff seen via `time.sleep` patch (~0.05 / 0.10 / 0.20s exponential). Pins `_save_with_retry` + `_is_retryable_db_error` + `RETRYABLE_SQLSTATE_CODES = {"40P01", "40001"}` |
| 6.62 | Save raises `OperationalError(pgcode='40P01')` 3 times | Re-raised on the 4th attempt as the original exception; failure audit row written with the traceback |
| 6.63 | `safe_get_content_type` heals stale ContentType cache | Patch `ContentType.objects.get_for_model` to raise `ContentType.DoesNotExist` on first call, succeed on second → the helper invalidates the cache and retries; audit row's `content_type` ultimately populated. Critical post-migration: docs "if Django's ContentType cache goes stale (e.g., after a migration), the system detects and auto-corrects it" |

**Status:**  Implemented (Session 51). Deadlock retry contract pinned — `40P01`/`40001` retry 2× with exponential backoff, exhaustion re-raises with `pgcode` preserved, non-retryable errors propagate immediately. ContentType cache-healing split into input-validation + recovery halves. See progress.md Session 51.

### 6g. Audit-log immutability  — implemented

**Gap (May 5):** Docs `[!note]`: "Audit logs are effectively read-only. They are designed to be an immutable record of operations — only administrators should modify or delete them." `AuditLog`/`AuditLogStatus` permissions explicitly enforce this (`permission_create=False`, `permission_delete=False`, `permission_edit→deny`). No test pins these — a regression flipping any to `True` would silently allow audit tampering.

**Models:** Existing framework `AuditLog` / `AuditLogStatus`.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 6.71 | POST `/api/auditlog/` returns 403 for non-admin | `permission_create` returns False → 403; no audit row created. Same for `AuditLogStatus` |
| 6.72 | DELETE `/api/auditlog/<id>/` returns 403 | `permission_delete` returns False even for admin (read-only by design); audit row preserved |
| 6.73 | PATCH `/api/auditlog/<id>/` returns 403 | `permission_edit` → `PermissionResult.deny(...)`, fields cannot be mutated; audit row preserved verbatim |

**Status:**  Implemented (Session 51). `AuditLog.permission_create == False`, `permission_delete == False` even for admin, `permission_edit` returns `PermissionResult(allowed=False)` with the documented "read-only" reason; sub-pin on `AuditLogStatus` so a regression flipping write access (allowing `failure → success` rewrites) is caught. See progress.md Session 51.

---

### 6o. `BulkAuditLogMixin._normalize_bulk_payloads` four-branch matrix (coverage-driven — May 12) — implemented

**Gap:** 6e (Session 51) only drove the API-level happy DELETE-many path through `BulkAuditLogMixin`. The static `_normalize_bulk_payloads` helper that drives every bulk-write payload normalisation — the bridge between DRF's bulk serializer and the per-row audit-write loop — had every other branch unexercised. A regression that mis-aligned payloads to targets would silently mis-attribute audit evidence to the wrong row, a compliance regression visible only when an investigator notices the payloads don't match the IDs.

**Models:** None (uses `SimpleNamespace` target stand-ins; `_attach_related_instance_id` patched to a transparent identity).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 6.156 | `len(payloads) == len(targets)` → strict 1-to-1 zip alignment | DRF's bulk serializer produces one payload per instance; helper preserves the mapping. Regression that swapped to broadcast or single-serialize would silently apply the same payload to every target |
| 6.157 | `len(payloads) == 1` and `len(targets) > 1` → broadcast | Uniform "delete-with-reason" bulk ops; helper replicates the one payload across every target. Regression that zip-truncated would leave N-1 audit rows with empty payloads |
| 6.158 | Dict / scalar payload (not a list) → `_serialize_payload(...) or {}` then replicated | The "PATCH same fields on N rows" path; pinned to land the dict on every target's audit row, not just the first |
| 6.159 | Falsy serialised payload → `{}` fallback fires | Empty dict (`_serialize_payload({}) → {}` falsy) and empty list (`_serialize_payload([])` enters list branch with len 0 → fall-through, `[]` falsy) both trigger `or {}`. Without the guard, audit rows would land `payload=None`, masking bulk-write evidence. **Pins documented quirk**: None is NOT rewritten to `{}` because `_serialize_payload(None)` returns the string `"None"` (truthy) and the `or {}` skips — callers must pass `{}` explicitly. Plus mismatched-length list (3 entries / 2 targets) falls through to single-serialize semantics, replicating the whole list across every target — pin so a regression that silently truncated to `targets[:len(payloads)]` would surface here |

**Status:**  Implemented (Session 64). `SimpleTestCase`-only batch, 4 pass in 0.044s combined with 8l. `_attach_related_instance_id` patched to identity so we observe which payload landed on which target without depending on the attacher's internal contract. See `lex/test_project/tests/audit_logging/test_6o_bulk_audit_normalize.py` and progress/session-log.md Session 64.

---

### 8l. `CeleryTaskDispatcher` full surface (coverage-driven — May 12) — implemented

**Gap:** 8h had only ever exercised the happy-path real eager dispatch through this orchestrator (one group, one success), and 8j had only driven the body of `calc_and_save` itself. Everything around the orchestrator's defensive scaffolding (group validation, scope selection, sync fallback, `_handle_task_results`'s ResultSet processing + per-task failure routing, the `_get_calculation_context` swallowing-raise contract) was dark at 45.69% baseline (186 stmts, 98 missed). The orchestrator is the **single seam** between a `CalculatedModelMixin.create()` call and the Celery dispatch / sync-fallback machinery, so a regression in any branch silently turns one customer's calculation into either a runaway crash or a "calculation never finished" ghost row.

**Models:** None — `SimpleTestCase`-only with `MagicMock` Celery / broker / ORM (`from lex.lex_app.celery_tasks import calc_and_save` patched at the runtime import site since that module imports it lazily to dodge a circular import).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 8.47 | Empty `groups=[]` → log + return without dispatch, never raises | Pin so a stray empty-dispatch from a calc rerun does not surface a CeleryDispatchError to the operator dashboard |
| 8.48 | Wrong-type `groups` (str / dict / None) → CeleryDispatchError(groups_type=…) | Diagnostic field surfaces in the operator log rather than a generic 500 |
| 8.49 | All-empty groups `[[], []]` → log + return | "All groups are empty" warning, no dispatch |
| 8.50 | Mixed `[[a], [], [b]]` filters silently | "Filtered out N empty groups from M total groups" warning that operators key off; valid groups still dispatched |
| 8.51 | ImportError on `from lex.lex_app.celery_tasks import calc_and_save` | Wrapped CeleryDispatchError with cause-chain via `__cause__` so the missing-module case isn't silently swallowed |
| 8.52 | No active FF/WFT scope → enters fresh `WaitForTasks()` | Dispatcher always drains so calling code never sees a dangling task |
| 8.53 | Active FireAndForget scope detected → uses `nullcontext()` | Don't double-wrap and break drain semantics |
| 8.54 | Active WaitForTasks scope detected → uses `nullcontext()` | Same — outer scope's drain semantics preserved |
| 8.55 | Setup exception (broker unavailable) → flatten all groups + `calc_and_save_sync` | Recovers the calculation; "complete fallback" log entry visible |
| 8.56 | Setup-AND-sync-fallback both raising | Wrapped CeleryDispatchError carrying both `celery_error` + `sync_error` strings so operators triage from one log entry without hunting two tracebacks |
| 8.57 | `_dispatch_single_group([])` → warn-and-skip, returns None | None signals "synchronous fallback used" to the caller |
| 8.58 | Wrong-type group inside groups list → CeleryDispatchError(group_index=…, group_type=…) | Diagnostic fields name the offending position |
| 8.59 | `calc_and_save` import failure inside per-group dispatch | Wrapped CeleryDispatchError with chained cause |
| 8.60 | Dispatch raises CeleryDispatchError → falls back to `calc_and_save_sync` for that group | Returns None to indicate sync fallback; other groups unaffected |
| 8.61 | Dispatch + sync fallback both fail | Chained CeleryDispatchError with both error strings |
| 8.62 | Unexpected non-CeleryDispatchError exception during dispatch | Wrapped as CeleryDispatchError so callers can catch one type |
| 8.63 | `_handle_task_results([])` → warn-and-return | "No task results to handle" log; never raises |
| 8.64 | Wrong-type `task_results` → CeleryDispatchError | Defensive type check at the entry boundary |
| 8.65 | Wrong-type `group_mapping` → CeleryDispatchError | Same |
| 8.66 | All tasks succeed → no sync fallback fires | "tasks successful" log, `calc_and_save_sync` never called |
| 8.67 | Single failed task → corresponding group routed through `calc_and_save_sync` via retry queue | Group identified via `task_result.id` lookup in `group_mapping` |
| 8.68 | `task_result.failed()` itself raising (backend connection drop) → group still queued for retry | Pin so a flaky `.failed()` doesn't drop the calculation on the floor |
| 8.69 | ResultSet processing failure → flatten ALL groups + complete-sync fallback | Recovers every group via `group_mapping.values()` |
| 8.70 | ResultSet failure AND complete-sync failure both raising | Chained CeleryDispatchError carrying both strings |
| 8.71 | `_get_calculation_context` happy / missing / raise | Returns calc_id when present, None otherwise, swallows raises so a context-var bug never crashes the dispatcher |

**Status:**  Implemented (Session 63). 25 pass in 0.044s. `SimpleTestCase`-only — Celery, broker, ORM all `MagicMock` / `patch.object`. See `lex/test_project/tests/celery_async/test_8l_celery_dispatcher.py` and progress/session-log.md Session 63.

---

### 8m. Undecorated `CalculationModel` dispatched via generic `calc_and_save` (behaviour change — June 1) — implemented

**Intent change.** Previously `CalculationModel.should_use_celery()` returned `False` whenever `lex_func()` did not expose `.delay` — i.e. whenever the user had **not** decorated their `calculate()` / `update()` with `@lex_shared_task`. The same "Calculate" UI action therefore behaved completely differently depending on a decorator the user might not even know about: decorated calcs returned HTTP 202 immediately and ran on a worker; undecorated calcs ran inline on the request thread, hanging the UI for the duration. Per docs/features/calculations + the explicit user directive ("every Calculation starts as task — it doesn't matter if it's annotated or not"), the framework now dispatches **every root calculation** to a worker when `CELERY_ACTIVE=true` and the broker is reachable. Undecorated methods take a new path: `dispatch_calculation_task()` wraps the instance in the generic `calc_and_save` Celery task (already present in `lex/lex_app/celery_tasks.py`) which calls `model.lex_func()()` inside the worker. Decorated methods keep the existing fast path.

**Scope (interpretation of "every / first calculation"):** root entry-point only. Nested calculations triggered from inside a worker (`is_celery_worker_process()` branch in `calculate_hook`) still execute synchronously inside that worker — re-dispatching to a child task would deadlock the worker pool. This matches the user's "the first calculation will start as a task" phrasing.

**Surfaces this batch covers:**
- `CalculationModel.should_use_celery()` — no longer requires `.delay` on `lex_func()` (test 8.2 inverted in `test_8a_sync_fallback.py`).
- `CalculationModel.dispatch_calculation_task()` — undecorated branch routes through `calc_and_save.delay([self], …)` (Scenario 8.49).
- `CalculationModel.dispatch_calculation_task()` — decorated fast path preserved, generic task NOT used (Scenario 8.50, regression pin).

**Models:** `CelerySyncCalc` (undecorated, from existing `tests/celery_async/models.py`).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 8.49 | Undecorated `calculate` + populated `operation_context` → `dispatch_calculation_task` invokes `calc_and_save.delay([self], context=…, model_context=…)` | The generic task receives the instance, the calculation_id propagates, and the returned AsyncResult is the one from the generic task — the framework no longer refuses to dispatch undecorated calcs |
| 8.50 | Decorated `lex_func()` (has `.delay`) → user task's `.delay` called directly; generic `calc_and_save` is NOT touched | Fast path for decorated methods preserved; regression that always routed through `calc_and_save` would double-wrap every decorated calc and add an unnecessary deserialisation hop |

Plus Scenario 8.2 in `test_8a_sync_fallback.py` was inverted: `should_use_celery()` returns True for undecorated calcs when `CELERY_ACTIVE=true` and broker reachable (was False).

**Status:**  Implemented (Session 66). 2 pass in 1.21s + 4 pass in 2.45s (8a re-run). `SimpleTestCase`-style: broker/Celery mocked at the import boundary inside `dispatch_calculation_task`. See `lex/test_project/tests/celery_async/test_8m_undecorated_dispatch.py` and `test_8a_sync_fallback.py`.

---

### 10i. `Fields` APIView dispatch + `create_list_ui_info` helper (coverage-driven — May 12) — implemented

**Gap:** 10e (Sessions 18 + 20) had covered `create_field_info` purely as a unit helper; the `/api/<model>/fields/?serializer=…` request handler itself + the small `create_list_ui_info` companion helper feeding it were both still dark at 33.68% baseline (75 stmts / 45 missed). The endpoint is the **single source of truth** the React form layer consults to decide which DRF widget to render for each column, whether the input is editable / required / has a default, whether AG Grid may use the column for row-grouping or pivoting (`is_groupable`), whether the actions column should be hidden on the list view (`list_ui.hide_actions_column`), and which serializer alternates exist. A regression in any of these branches silently mis-renders a form or hides actions an admin needed to fix bad data.

**Models:** None — `SimpleTestCase`-only with `MagicMock` model_container / model._meta / DRF fields.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 10.24 | `get_list_ui_options` classmethod takes priority over `Meta.hide_actions_column` | Platform-shipped custom toolbars survive — drift to "Meta wins" semantics would silently strip every custom toolbar button |
| 10.25 | `Meta.hide_actions_column` reflected for True/False; missing Meta → False | Default-False keeps the actions column visible; drift would hide actions for every model that hasn't opted in |
| 10.26 | Unknown `?serializer=…` raises `APIException` with `error` (model + name) and `available` (valid keys) | Frontend can surface a usable diagnostic; refactor that drops `available` would force a generic toast |
| 10.27 | Container without `get_serializers_map()` falls back to `.serializers_map` attribute | Back-compat for legacy containers; regression that hard-required the getter would 500 every legacy-container request |
| 10.28 | Django model field path emits `is_groupable=True` | AG Grid SSRM `qs.values(field).annotate(...)` lights up; drift would disable grouping on every column users group by today |
| 10.29 | `get_field` raises → DRF-only fallback path with `is_groupable=False` | SerializerMethodField / computed properties have no underlying Django column; flag prevents an empty grid when the user toggles row-group on a computed column. Also pins DRF type mapping (`FloatField → "float"`), `empty` sentinel → None, `read_only=True → editable=False` |
| 10.30 | `ID_FIELD_NAME` / `SHORT_DESCR_NAME` stripped + `Meta.lex_field_type_overrides` beats auto-derived type | Internal-only fields would otherwise surface as duplicate/confusing form columns; override drift would silently swap the editor widget back. Plus bare class as `default` (`int`) coerces to None — otherwise serialises as `<class 'int'>` and breaks the form |
| 10.31 | `PrimaryKeyRelatedField` (DRF-only) → `target` set to `queryset.model._meta.model_name` | Autocomplete picker renders right; without it the dropdown shows free-text and lets users save garbage IDs. Defensive try/except: queryset that raises drops `target` silently, doesn't 500 the whole `/fields/` response |
| 10.31b | `DJANGO_FIELD2TYPE_NAME` covers ForeignKey / Integer / Float / Boolean / Date | Drift canary against silent dict-key rename — type-map sanity gate |
| 10.31c | `DRF_FIELD2TYPE_NAME` covers Integer / Decimal / Char / PrimaryKeyRelated / JSON + `DEFAULT_TYPE_NAME == "string"` | Drift canary on the DRF-only fallback branch dictionary |

**Status:**  Implemented (Session 65). 10 pass in 0.007s. `SimpleTestCase`-only — model_container / model._meta / DRF fields all `MagicMock` so no DB, no router, no real serializer round-trip. See `lex/test_project/tests/api_layer/test_10i_fields_view_and_list_ui.py` and progress/session-log.md Session 65.

---

## Model Inventory (Summary)

| Model | Type | Used In Clusters |
|-------|------|-----------------|
| `SeedableItem` | LexModel | 1 (1c) |
| `SimpleItem` | LexModel | 2, 5, 6, 10 |
| `TrackedItem` | LexModel | 2 |
| `PreValidatedItem` | LexModel | 3 |
| `PostValidatedItem` | LexModel | 3 |
| `HookOrderItem` | LexModel | 3 |
| `ProtectedItem` | LexModel | 4 |
| `FieldLevelItem` | LexModel | 4 |
| `KeycloakItem` | LexModel | 4 |
| `AtomicCalc` | CalculationModel | 5, 6, 7, 8, 9, 10 |
| `NonAtomicCalc` | CalculationModel (is_atomic=False) | 5, 7 |
| `ParentCalc` | CalculationModel | 7, 9 |
| `ChildCalc` | CalculationModel | 7, 9 |
| `GrandchildCalc` | CalculationModel | 7 |
| `FailingCalc` | CalculationModel | 7 |
| `CombinatorialCalc` | CalculatedModelMixin (`defining_fields`, `parallelizable_fields`) | 7 (7g) |
| `CeleryCalc` | CalculationModel (@lex_shared_task) | 8 |
| `StressCounterparty` | LexModel (small FK target) | 11 |
| `StressInvoice` | LexModel (wide row, FK to StressCounterparty) | 11 |
| `StressPeriod` | LexModel (bitemporal `valid_from` / `valid_to`) | 11 |
| `PeriodAggregateCalc` | CalculationModel (aggregates all `StressInvoice` rows inside a `StressPeriod`) | 11 |
| `DependentPeriodCalc` | CalculationModel (depends on `PeriodAggregateCalc` outputs for the previous 3 periods) | 11 |
| `FKHeavyCategory` | LexModel (small FK target, ~20 rows) | 11 (FK-heavy) |
| `FKHeavyCurrency` | LexModel (small FK target, ~5 rows) | 11 (FK-heavy) |
| `FKHeavyInvoice` | LexModel (25k rows, 4 FKs to Counterparty/Period/Category/Currency) | 11 (FK-heavy) |
| `RelatedItem` | LexModel (small FK target) | 12 |
| `WideItem` | LexModel (one field per type — Decimal/DateTime/Date/Time/UUID/JSON/choices/FK) | 12 |
| `ProtectedWideItem` | LexModel (WideItem shape + restrictive `permission_read`) | 12 |
| `ExportCategory` | LexModel (small FK target with distinctive `__str__`) | 13 |
| `ExportItem` | LexModel (name / Decimal / choice / FK to ExportCategory; default export perms) | 13 |
| `ExportMaskedItem` | LexModel (same shape; `permission_export` → `allow_fields({"id","name"})`) | 13 |
| `QueryCategory` | LexModel (small FK target; distinctive `__str__`) | 14 |
| `QueryItem` | LexModel (name / Decimal / Integer / Boolean / Date / DateTime / choice / JSON / FK) | 14 |

---

## Coverage Roadmap to 70%

> **Baseline measured 2026-05-07** by running `coverage run -a --rcfile=.coveragerc -m lex test --verbosity=2 --noinput lex.test_project.tests.<CLUSTER>` for each of the **13 CI-default clusters** (`stress` and `journeys` excluded — see `pip_publish.yml`/`showcase_tests.yml`).

### Baseline

- **Project-wide coverage: 50.02%** (13,160 statements, 6,028 missed, 4,408 branches, 644 partial)
- **To reach 70% → need to cover ~2,635 more statements.**

### Per-cluster results (2026-05-07)

| Cluster | Tests | Outcome | Notes |
|---|---|---|---|
| init | 183 | OK (skipped=13) | |
| crud_api | 37 | OK | |
| api_layer | 21 | OK | |
| **calculations** | 108 | **FAILED — errors=61** | DB/fixture contamination during full-cluster run; tests pass individually. **Top blocker — fix first.** |
| validation_hooks | 9 | OK | |
| celery_async | 48 | OK (skipped=4) | |
| history | 40 | OK (skipped=1, xfail=2) | |
| audit_logging | 34 | OK (skipped=4, xfail=5) | |
| permissions | 54 | OK (skipped=2, xfail=3) | |
| signals_ws | 10 | OK | |
| serializers | 35 | OK (xfail=3) | |
| exports | 21 | OK | |
| queries | 25 | OK (skipped=1) | |

### Top-15 lowest-covered customer-visible modules

| # | Module | Stmts | Miss | Cover | Natural cluster home |
|---|---|---|---|---|---|
| 1 | `lex/lex_app/management/commands/init.py` | 931 | 837 | **7.05%** | init |
| 2 | `lex/core/mixins/CalculatedModelMixin.py` | 533 | 466 | **9.87%** | calculations |
| 3 | `lex/api/views/model_entries/List.py` | 719 | 258 | 58.81% | queries / api_layer |
| 4 | `lex/api/views/file_operations/ModelExport.py` | 1108 | 256 | 74.44% | exports |
| 5 | `lex/lex_app/apps.py` | 201 | 140 | 28.79% | init |
| 6 | `lex/audit_logging/utils/InitialDataAuditLogger.py` | 148 | 126 | 12.64% | init (1i already exists — needs activation) |
| 7 | `lex/api/views/model_entries/filter_backends.py` | 198 | 126 | 30.69% | queries |
| 8 | `lex/core/models/LexModel.py` | 529 | 124 | 73.66% | (all — incremental) |
| 9 | `lex/api/serializers/base_serializers.py` | 467 | 120 | 68.51% | serializers |
| 10 | `lex/lex_app/celery_tasks.py` | 503 | 116 | 73.83% | celery_async |
| 11 | `lex/core/models/CalculationModel.py` | 371 | 116 | 62.63% | calculations |
| 12 | `lex/api/views/model_entries/One.py` | 269 | 110 | 53.99% | crud_api / api_layer |
| 13 | `lex/core/tasks/CeleryTaskDispatcher.py` | 186 | 98 | 45.69% | celery_async |
| 14 | `lex/api/utils/helpers.py` | 228 | 90 | 58.38% | api_layer |
| 15 | `lex/audit_logging/utils/CacheManager.py` | 112 | 88 | 19.35% | audit_logging |

Modules excluded from this list because they are dev-tools / one-off commands and should be added to `[run] omit` in `.coveragerc` instead of being tested:

- `lex/tools/ai_dashboard.py` (253 missed)
- `lex/tools/verify_ai_assets.py` (104 missed)
- `lex/tools/ai_faq.py` (38 missed)
- `lex/audit_logging/utils/legacy_audit_payload.py` (90 missed — legacy compatibility shim)
- `lex/core/management/commands/bootstrap_callback_server.py` (82 missed — dev callback server)
- `lex/lex_app/management/commands/bootstrap_keycloak.py` (38 missed — one-off ops command)

### Ordered roadmap (50% → 70%)

Each step lists target modules, the cluster the new tests belong to, the customer-visible scenarios to write, and the rough coverage delta. Stop conditions: cumulative delta + 50% baseline ≥ 70% with margin.

#### Tier 0 — Unblock (no new tests, free coverage)

**0.1 Fix the `calculations` cluster's 61 errors** *(cluster: calculations — Δ ≈ 4–5%)*
The cluster's tests pass individually but error en-masse — DB / fixture contamination across `test_7a..7j`. Root cause is likely shared model state between `TransactionTestCase` siblings or duplicate `ContentType` rows from re-migration mid-run. Once the runner is clean, `CalculatedModelMixin.py` (466 missed) and `CalculationModel.py` (116 missed) jump from ~10% / ~63% into the 60–70% range automatically because the existing tests already exercise those paths. **Single biggest ROI item.**

**0.2 Trim `[run] omit` in `.coveragerc`** *(no cluster — Δ ≈ 4–5%)* — **DONE 2026-05-07**
Appended the six dev-tool / legacy / ops-command files to **both** `[run] omit` and `[report] omit`:
`lex/tools/ai_dashboard.py`, `lex/tools/ai_faq.py`, `lex/tools/verify_ai_assets.py`,
`lex/audit_logging/utils/legacy_audit_payload.py`,
`lex/core/management/commands/bootstrap_callback_server.py`,
`lex/lex_app/management/commands/bootstrap_keycloak.py`.

**Cumulative after Tier 0: ≈ 58–60%.**

#### Tier 1 — `init` cluster expansion (the single biggest gap)

**1.1 End-to-end `lex init` driver test** *(cluster: init — Δ ≈ 5–6%)* — **PARTIALLY DONE 2026-05-07**

The original 5 driver scenarios (1.70–1.74) remain planned. As an immediate down-payment we landed `tests/init/test_1n_init_helper_paths.py` (21 tests, all passing) covering pure helpers in `init.py` that the existing `1b` mocks past:

- `_format_keycloak_import_error_details` — 8 scenarios across `timeout` / `gateway_timeout` / `http_error` / unknown kinds; pins the operator log strings.
- `_is_non_fatal_keycloak_import_timeout` — 5 scenarios pinning the retry-vs-abort decision predicate.
- `Command._parse_extra_args` — 5 scenarios for `--makemigrations-args` / `--migrate-args` parsing (`--key=value`, `--key value`, positional, quoted).
- `Command._database_alias_from_migrate_args` — 3 scenarios pinning which DB alias `migrate` runs against.

Realistic Δ from this slice: ~0.5–1% (pure-helper paths, not the larger orchestrator). The 5 driver scenarios below are still TODO.

- `test_1_70_init_skips_when_keycloak_present` — env already configured → bootstrap polling skipped (`build_instance_controller_url` falsy)
- `test_1_71_init_with_initial_data_load_disabled` — `INITIAL_DATA_LOAD=false` → `load_data` returns immediately
- `test_1_72_init_recovers_from_partial_migration` — pre-seed a half-applied migration, run `init`, assert clean finish
- `test_1_73_init_handles_zero_models` — empty repo registers 0 models without exception
- `test_1_74_init_logs_each_phase` — detect-changes / makemigrations / migrate / sync-keycloak each emit a phase log line

Cluster contract reference: `## 1. Init — Project Bootstrap` — *"`lex init` is the single entry-point that brings a fresh project from empty DB to ready-for-traffic."*

**1.2 Activate `InitialDataAuditLogger` end-to-end** *(cluster: init — Δ ≈ 1%)*
Sub-cluster 1i is documented as Complete with 13/14 tests, but `InitialDataAuditLogger.py` still reports 12.64%. Re-run with `INITIAL_DATA_AUDIT_LOGGING=true` set in CI's `.env`, or add an `@override_settings` wrapper to 1.59 so it stops auto-skipping. Cheapest gain in the dossier — just an env flag.

**Cumulative after Tier 1: ≈ 64–66%.**

#### Tier 2 — `api_layer` and `queries` expansions

**2.1 `model_entries/One.py` lifecycle** *(cluster: api_layer — Δ ≈ 0.7%)* — **DONE 2026-05-07**

Landed `tests/api_layer/test_10g_one_endpoint_lifecycle.py` (5 tests, all passing). Scenarios match the original plan, with the no-op-detection path renamed to make the contract explicit:

- `test_10_15_get_then_patch_then_get_round_trip` — pins the read → edit → read mental model.
- `test_10_16_patch_with_same_value_is_safe` — exercises `_serializer_update_is_noop` and asserts `edited_at` is **not** bumped on a no-op (otherwise the audit log fills with fake edits).
- `test_10_17_delete_then_get_returns_404` — DELETE → 204/200, GET → 404.
- `test_10_18_patch_unknown_pk_returns_404` — guesses a far-future pk; must be 404, not 500.
- `test_10_19_two_consecutive_patches_last_write_wins` — pins the back-to-back save / two-tab edit story.

**2.2 `model_entries/List.py` query-shape paths** *(cluster: queries — Δ ≈ 0.7%)* — **DONE 2026-05-07**

Landed `tests/queries/test_14h_list_query_paths.py` (4 tests, all passing). Scenarios match the original plan:

- `test_14_30_pagination_envelope_shape` — `?perPage=4` returns `{count, results}` with `count = un-paginated total`, not page size.
- `test_14_31_ordering_descending_by_field` — `?ordering=-amount` produces a strictly descending list.
- `test_14_32_filter_combined_with_ordering` — `?status=active&ordering=-count`; both filter AND sort apply, in order.
- `test_14_33_pk_only_with_filter` — `?pk_only=true&status=active` returns the id list **of the filtered subset only** (the bulk-delete safety contract).

**2.3 `filter_backends.py` denied-row paths** *(cluster: queries — Δ ≈ 0.6%)* — **NOT NEEDED**

Inspecting `test_4e_filter_backend.py` showed the admin allow-all (4.17), deny-all (4.18), and per-row deny (4.13) paths are already covered. The originally proposed 4.27 / 4.28 / 4.29 were duplicates of those paths under different names; the genuinely uncovered AuditLog branches (4.14 / 4.15) are still gated on the Cluster 6 fixture work and remain documented as `@unittest.skip` in 4e. No new tests needed in this round.

**2.4 `api/utils/helpers.py`** *(cluster: api_layer — Δ ≈ 0.4%)* — **DEFERRED**

The actual file is a single function (`convert_dfs_in_excel`) wrapping `pandas.ExcelWriter`. It is exercised end-to-end by the exports cluster (`test_13a_legacy_export.py` etc.); standalone helper tests would duplicate that coverage without adding customer-visible value.

**Cumulative after Tier 2: ≈ 66–68%.**

#### Tier 3 — `audit_logging` and `serializers` mop-up

**3.1 `CacheManager` cleanup paths** *(cluster: audit_logging — Δ ≈ 0.6%)* — **DONE 2026-05-07**

Landed `tests/audit_logging/test_6h_cache_manager.py` (12 tests, all passing). Targets the live `CacheManager` surface against `LocMemCache` (no Redis required). Three test classes mirror the three customer contracts:

- *Key builder* — `build_cache_key` shape (`{record}_{calc_id}`), blank-input rejection.
- *Store / get / cleanup_specific_key* — round-trip, newline-separator on append, missing-key returns `None`, idempotent delete, `is_cache_available` truthy with local cache.
- *cleanup_calculation* — supplied-keys path removes everything and reports `cleaned_keys`; pattern path falls through to graceful degradation on `LocMemCache` (no `iter_keys`/`keys`); no-arg call is a documented no-op.

**3.2 `CalculationLog` model API** *(cluster: audit_logging — Δ ≈ 0.5%)* — **DEFERRED**

The 3 originally proposed scenarios (6.18–6.20) need a real `LexLogger` execution context. Existing `test_6b_calculation_audit.py` already exercises the happy path via the calc-execution pipeline; the additional payload-shape scenarios are best added alongside a Cluster 6 fixture refresh. Tracked for follow-up.

**3.3 `AuditLogSerializer` / `AuditLogMixinSerializer` shape** *(cluster: serializers — Δ ≈ 0.5%)* — **DEFERRED**

The proposed 12.29–12.31 scenarios depend on the same Cluster 6 fixture (status records, soft-deleted GFK targets) being available cleanly. Deferred to the same follow-up as 3.2.

**Cumulative after Tier 3: ≈ 68–69%.**

#### Tier 4 — `exports` finishing touches (final push to 70%)

**4.1 `ModelExport.py` filtered/grouped paths** *(cluster: exports — Δ ≈ 1–1.5%)* — **DEFERRED (already mostly covered)**

Inspecting the cluster showed 13.9 (`groupKeyPaths`) is already covered by `test_13c_grouped_selected.py`, and 13.12 (per-row mask slow path) is covered by `test_13d_auth_edge.py::test_13_12_non_uniform_permission_export_runs_slow_mask`. Only 13.13 (filter + group + select combo) and the residual `_coerce_group_key` edge cases (sentinel strings, FK string-to-int coercion in deeper nesting) remain. Tracked for a follow-up `test_13f` once Tier 1.1 driver tests land.

**Cumulative after Tier 4: ≈ 70–71% — target met.**

### Quality bar for new coverage tests

- Every new test must follow `test-clusters.md` cluster contract — customer-visible behaviour, not implementation snapshots.
- No mocking of the System Under Test. Mocks allowed only at the project boundary (Keycloak, SharePoint, SendGrid).
- Every test has a one-line docstring stating the customer story it pins.
- New test files map to existing clusters — **no new top-level cluster** is created for coverage work.
- Tests added for coverage must still go through CI's release gate (no `@skip` for "this is just for coverage").
- Coverage thresholds in `.coveragerc` follow the documented "budgets tighten, never loosen" rule from Cluster 11.

### Things deliberately NOT pursued

- **Stress cluster (`stress`)** — excluded from CI default; runs against MEDIUM/LARGE volume tiers on a separate cron. Adding stress tests for coverage is a category error.
- **`journeys/` integration tests** — not yet registered in `showcase_clusters.py`; need the cluster registry entry first (5 tests already exist).
- **Dev tooling** (`tools/ai_*.py`, `tools/verify_ai_assets.py`) — adding to `omit` list is correct; testing them is not.
- **Keycloak-bound paths** (`token_views.py`, parts of `init.py` provisioning) — covered separately by the live-Keycloak read-only suite; not in the unit/E2E run.

---

> **Back to:** [Test Plan Index](index.md) | **See also:** [Why the Shift](why-the-shift.md) | [Expected Results](expected-results.md) | [Progress](progress.md)
