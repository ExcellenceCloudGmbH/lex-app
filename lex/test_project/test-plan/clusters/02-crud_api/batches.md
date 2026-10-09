## Cluster 2 — CRUD via REST API (existing 2a–2e)

### Batch 2f — Model-entry mixins & serialisers

| Property | Value |
| --- | --- |
| Scenario range | 2.40 – 2.55 |
| Type | I + E |
| Files covered | `mixins/ModelEntryProviderMixin.py`, `mixins/DestroyOneWithPayloadMixin.py`, `mixins/PermissionAwareSerializerMixin.py` |
| Test file | `lex/test_project/tests/crud_api/test_2f_model_entry_mixins.py` |
| Test classes | `TestModelClassResolution` (URL kwarg → ContentType → model), `TestDestroyOneReturnsPayload` (DELETE returns the deleted instance), `TestPermissionAwareSerializerStripsFields` (per-user field masking) |
| Fixtures | `ProtectedItem` (already exists in `permissions/models.py`); add `OwnedItem` if not present |
| Est. tests | ~14 |
| Coverage gain | +0.7 % |
| Prereqs | none |

### Batch 2g — One / Many / List + filter backends

| Property | Value |
| --- | --- |
| Scenario range | 2.56 – 2.78 |
| Type | E |
| Files covered | `views/model_entries/One.py`, `Many.py`, **partial** `List.py` (rest is in 14f), `filter_backends.py`, `api/filters/GenericFilters.py` |
| Test file | `lex/test_project/tests/crud_api/test_2g_one_many_filters.py` |
| Test classes | `TestOneEndpoint` (GET/PUT/PATCH/DELETE happy + 404), `TestManyEndpoint` (paginated GET, bulk POST), `TestListBasicShape` (defer AG-Grid specifics to 14f), `TestFilterBackendQueryParser`, `TestGenericFilterClasses` |
| Fixtures | `SimpleItem`, `TrackedItem` |
| Est. tests | ~22 |
| Coverage gain | +1.2 % |
| Prereqs | 2f |

### Batch 2h — Structure / fields / lex-API endpoints

| Property | Value |
| --- | --- |
| Scenario range | 2.79 – 2.92 |
| Type | E |
| Files covered | `views/ModelStructureObtainView.py`, `views/model_info/Fields.py`, `views/lex_api/LexAPI.py`, `api/utils/helpers.py`, `api/utils/Context.py`, `api/utils/api_key_requests.py` |
| Test file | `lex/test_project/tests/crud_api/test_2h_structure_and_lex_api.py` |
| Test classes | `TestModelStructureObtain`, `TestFieldsEndpoint` (per-model field metadata), `TestLexApiDispatcher`, `TestApiUtilHelpers` (U), `TestRequestContextObject` (U), `TestApiKeyAuthenticatedRequest` |
| Fixtures | API-key fixture (already in cluster 4) |
| Est. tests | ~15 |
| Coverage gain | +0.6 % |
| Prereqs | 2f |

### Batch 2i — Cancel-calculation REST endpoint (Session 67 — June 1)

| Property | Value |
| --- | --- |
| Scenario range | 2.93 – 2.96 |
| Type | E |
| Files covered | `lex/api/views/model_entries/One.py` (the new `cancel=true` short-circuit branch in `OneModelEntry.update`) |
| Test file | `lex/test_project/tests/crud_api/test_2i_cancel_endpoint.py` |
| Test classes | `TestCluster02i_CancelCalculationEndpoint` (PATCH with body `{"cancel":"true"}` → 202 on cancellable IN_PROGRESS, 409 on terminal state, 409 with `reason=sync_calculation_not_cancellable` when no Celery task_id, sibling fields ignored) |
| Fixtures | `AtomicCalc` (from cluster 7); patches `CalculationModel._revoke_celery_task` so no broker is needed |
| Est. tests | 4 |
| Coverage gain | +0.1 % |
| Prereqs | none |
| Status | ✅ Complete (Session 67 — 4 scenarios; passes locally for pure-logic, DB-needing scenarios require a CI-configured test DB) |

---

### Batch 2j — Instance API-key extraction and matching (Session 80 — June 18)

| Property | Value |
| --- | --- |
| Scenario range | 2.97 – 2.107 |
| Type | U |
| Files covered | `lex/api/utils/api_key_requests.py` (`get_raw_api_key`, `is_instance_api_key_request`) |
| Test file | `lex/test_project/tests/crud_api/test_2j_instance_api_key.py` |
| Test classes | `TestCluster02j_GetRawApiKey` (2.97–2.103 — KeyParser hit, header fallback, prefix strip, empty candidate, no source, DRF wrapped request, non-ApiKey header), `TestCluster02j_IsInstanceApiKeyRequest` (2.104–2.107 — match, mismatch, no env var, no key in request) |
| Fixtures | none — `SimpleTestCase` with `patch` on `KeyParser` and `patch.dict("os.environ")` |
| Tests landed | 11 pass / 0 fail |
| Coverage gain | `lex/api/utils/api_key_requests.py` `get_raw_api_key` + `is_instance_api_key_request` branches |
| Status | ✅ Complete (Session 80 — June 18) |

---

### Batch 2k — A create that starts a calculation answers straight away ✅

| Property | Value |
| --- | --- |
| Scenario range | 2.108 – 2.109 |
| Type | E |
| Files covered | `lex/api/views/model_entries/One.py` (`OneModelEntry.create`) |
| Test file | `lex/test_project/tests/crud_api/test_2k_create_starts_calculation.py` |
| Test classes | `TestCluster02k_CreateStartsCalculation` — 2.108 the POST answers 201 with `is_calculated` IN_PROGRESS while `calculate()` is held open, and the run finishes in SUCCESS once released; 2.109 a calculation model without `calculate_on_create` answers NOT_CALCULATED, as before |
| Fixtures | `OnCreateCalc`, `OnCreateOffCalc` from `calculations/models.py` (cluster 7t) |
| Tests landed | **2 pass / 0 fail** |
| Status | ✅ Complete — paired with 7t |

---

### Batch 2l — A closed record: Calculate is refused with its reason, the rest stays editable ✅

| Property | Value |
| --- | --- |
| Scenario range | 2.110 – 2.116 |
| Type | E |
| Files covered | `lex/api/views/model_entries/One.py` (`OneModelEntry.update`, `perform_update`, `_closed_after_update`), `lex/core/calculation_closing.py` |
| Test file | `lex/test_project/tests/crud_api/test_2l_calculate_closed_record.py` |
| Test classes | `TestCluster02l_CalculateClosedRecord` — 2.110 the Calculate PATCH on a closed record answers 409 with the model's reason in `detail`, the record stays SUCCESS and no run is marked in progress; 2.111 an open record still gets 202 and its run finishes; 2.112 a method answering `True` gets the default message; 2.113 a closed record's other fields stay editable, and the edit keeps its status; 2.114 closing a record through an edit keeps its status; 2.115 reopening it resets it like any edit of an open record; 2.116 an open record's edit still resets it |
| Fixtures | `ClosableCalc`, `ClosedByTrueCalc` from `calculations/models.py` (cluster 7u) |
| Tests landed | **7 pass / 0 fail** |
| Status | ✅ Complete — paired with 7u and 12m |

**Why an edit keeps a closed record's status.** Every edit through the app resets a calculation record
to NOT_CALCULATED, since its inputs may have changed. A closed record can never be recalculated, so the
reset would strand it there for good, and closing a calculated record through an edit (setting the field
its `calculation_closed_reason()` reads) would flip it to NOT_CALCULATED in the same save. The edit asks on a copy carrying the incoming values: a record that is
closed once the edit is applied keeps its status; an open one, reopened ones included, is reset as
before. Nothing else about edits changes: closing stops only the calculation.

---
