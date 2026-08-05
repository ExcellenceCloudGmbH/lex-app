## 10. API Layer

**What it tests:** REST API endpoints — One (create/read/update/delete), List, Many, History — including serializer behavior, filter backends, and the API-specific calculation trigger path.

**Why last:** The API is the external interface. It wraps everything above (CRUD, permissions, history, calculations) into HTTP endpoints. If the layers below work, the API layer is mostly wiring.

**Models needed:**
- `SimpleItem` (reused)
- `AtomicCalc` (reused)

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 10.1 | POST creates record | 201, record in DB, response includes `id` |
| 10.2 | GET retrieves record | 200, correct field values |
| 10.3 | PATCH updates fields | 200, only specified fields changed |
| 10.4 | DELETE removes record | 204/200, record gone |
| 10.5 | GET list returns all records | 200, correct count |
| 10.6 | GET history returns history rows | 200, ordered by `history_date` |
| 10.7 | Many endpoint bulk delete | DELETE with repeated `ids` query params removes selected records only |
| 10.9 | Invalid data returns 400 | Validation errors in response |
| 10.10 | Unauthenticated request handled | 401 or redirect |

### 10m. Calculation-log tree pagination + N+1 fix ✅

**What it tests:** `CalculationLogTreeView` (the endpoint that backfills the calculation-log tree when the panel opens). Before this change the view ran `CalculationLog.objects.all()` (or every row for one calc) and the serializer issued one child query per node (N+1) — loading the whole table into memory in one shot, a second compounding offender behind the backend OOM. The fix paginates with `limit`/`offset` (default 1000, max 5000), resolves every page's child ids in a single query via a prefetched `children_map`, and checks `parent_log_id` instead of lazy-loading the parent FK per node.

**Why a regression matters:** the unbounded query + N+1 is the read-path twin of the 6p buffer leak — together they were the calculation-log OOM. Pagination caps how much a single request can materialize; the `assertNumQueries` gate is what stops the N+1 from silently creeping back.

**Scenario range:** 10.61 – 10.66. **Test file:** `lex/test_project/tests/api_layer/test_10m_calculation_log_tree.py`. **Type:** I. **Status:** ✅ Complete (Session 77 — June 8). Scenarios: 10.61 default limit bounds the page; 10.62 offset walks the dataset; 10.63 child ids resolved per parent; 10.64 `isRoot` present only for parentless rows (stripped on children); 10.65 the integration surface — `assertNumQueries(3)` across 25 parents+children proves the N+1 is gone; 10.66 `calculation_id` filter scopes the rows.

---

### 10o. Read-only calculation-status endpoint ✅

**What it tests:** `CalculationStatus` — `GET /api/model_entries/<model:model_container>/<int:pk>/calculation-status`, the narrow read the Streamlit `lex_calculation()` widget (batch 1ab) polls every couple of seconds. It returns only what the widget renders: the record's `is_calculated` status, its error message, and the start/end/duration of its most recent run, reconstructed from `CalculationLog` because the record itself carries no timestamp — since PR #675 a calculation-owned save deliberately does not stamp `edited_at`. A bounded log tail is available behind `?include_log=true`.

**Why a regression matters:** a purpose-built endpoint is a new place for a permission to be forgotten, and this one answers questions about records by pk. A status response that confirms a record exists and errored, to a caller who may not read that record, is a real leak — and it would leak silently, since nothing about it looks like a failure. The endpoint therefore filters through the same `UserReadRestrictionFilterBackend` every list read uses rather than a hand-rolled check (read permission here is a queryset filter, not a boolean), and answers an unreadable record with the same 404 **and the same body** as a missing one. The rest is poll economics: at one request every two seconds per open widget, anything unbounded — the whole log, a COUNT over every line ever logged, a window spanning every run the record ever had — becomes load and misinformation at the same time.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 10.72 | Never-calculated record | `NOT_CALCULATED` with null timings — a fresh record is a state, not an error |
| 10.73 | Terminal states stay distinct | `ABORTED` and `CANCELLED` report verbatim, not collapsed into `ERROR` |
| 10.74 | Failed calculation | status and `calculation_error_message` arrive in the same envelope |
| 10.75 | Unknown pk | 404, not a 500 — a dashboard pinned to a deleted record must not page anyone |
| 10.76 | Unreadable record is indistinguishable from missing | an errored record the caller may not read and a nonexistent pk return byte-for-byte identical responses — same status code, same body |
| 10.77 | Log absent unless requested | the default poll returns no `log` keys at all and issues no `CalculationLog` query |
| 10.78 | Log is bounded and reports truncation | exactly the newest `LOG_TAIL_LIMIT` (50) lines, oldest first, with `log_truncated` true |
| 10.79 | A short log is not flagged | every line returns and `log_truncated` is false |
| 10.80 | Timings come from the last run only | a record run yesterday and again just now reports the 38-second window, not the span between the two |
| 10.81 | The tail covers only the latest run | a short re-run's tail is not padded out of the previous run's rows |
| 10.82 | Readable but not runnable | a caller who may read the record and may not run it gets `can_calculate: false` with the restriction's own reason — and the trigger really is refused (403) |
| 10.83 | Runnable | the same record polled by an allowed caller reports `can_calculate: true` with no reason — and the trigger really is accepted (202) |

**Scenario range:** 10.72 – 10.84. **Test file:** `lex/test_project/tests/api_layer/test_10o_calculation_status_endpoint.py`. **Type:** E. **Status:** ✅ Complete (2026-08-05) — 12 pass / 0 fail. Source: `lex/api/views/calculations/CalculationStatus.py`. Both the timings and the tail are scoped by `Subquery` to the newest `calculationId`, so they always describe the same run.

`can_calculate` (10.82–10.83) is the second permission this endpoint reports and the second place one could be got wrong — this time by *inventing* it. Read permission cannot answer whether the caller may trigger a run, so the endpoint reuses `UserPermission`, the DRF permission class `OneModelEntry` declares, evaluated against the trigger's own PATCH payload. It authorises nothing: `One.update` still authorises itself, unchanged, and the widget keeps its 403 handler for the moment the two disagree because the permission changed between the poll and the click. Both scenarios issue that real PATCH alongside the poll, since a flag that quietly disagrees with the endpoint it describes is the entire failure mode — and it is not symmetric: enabled-then-403 costs a click, disabled-when-allowed leaves nothing to press.

---


### 10p. Named serializers — choosing which fields a record shows ✅

`api_serializers` on the model class maps a name to a serializer, and `?serializer=<name>` selects between them. It is what `lex_calculation(serializer=...)` passes through: the Streamlit embed hands the name to `lex_view`, which puts it in the query string, and the React data provider forwards it here. Everything between the dashboard author and the field list is transport — which is why this is worth pinning. The chain crosses three codebases and only its two ends are visible: an author writes a name, a page shows fields. If the parameter stopped selecting anything, nothing would break; every dashboard would quietly show the default field list.

| Scenario | Claim | How |
| --- | --- | --- |
| 10.85 | Without a name, nothing is hidden | the default serializer returns every business field — the wide baseline the narrow view is measured against |
| 10.86 | A named serializer narrows the field list | `?serializer=compact` returns the three declared fields; `field_2` and `internal_note` are absent, not null |
| 10.87 | A typo fails loudly | an unknown name is refused, and the body carries both the name and the available list — falling back to the default would render a plausible page showing the wrong fields |
| 10.88 | Declaring a name does not remove the default | the map holds both, because the React table and every framework-internal lookup read `default` |

**Scenario range:** 10.85 – 10.88. **Test file:** `lex/test_project/tests/api_layer/test_10p_named_serializers.py`. **Type:** E2E. **Status:** ✅ Complete (2026-08-05) — 4 pass / 0 fail. Sources: `lex/api/views/model_entries/mixins/ModelEntryProviderMixin.py` (`get_serializer_class`), `lex/api/serializers/base_serializers.py` (`get_serializer_map_for_model`). Pairs with [batch 1ab](../01-init/batches.md), the Streamlit embed that passes the name.
