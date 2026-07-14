## Cluster 12 — Serializer Contract

(no batches recorded yet)

---

### Batch 12g — Datetime timezone ambiguity (BUG-025) ✅

| Property | Value |
| --- | --- |
| Scenario range | 12.36 – 12.38 |
| Type | E |
| Files covered | `core/models/LexModel.py` (`lex_datetime_now`, `edited_at`/`created_at` stamping), `audit_logging/serializers/CalculationLogSerializer.py` (`timestamp`), `lex_app/settings.py` (`USE_TZ`/`TIME_ZONE` coupling) |
| Test file | `lex/test_project/tests/serializers/test_12g_datetime_tz_ambiguity.py` |
| Test classes | `TestCluster12g_DatetimeTimezoneAmbiguity` |
| Fixtures | reuse cluster-12 `WideItem` models |
| Est. tests | 3 |
| Coverage gain | bug-documentation batch (xfail strict) |
| Prereqs | none |
| Status | ✅ Complete — 3 pass / 0 fail (BUG-025 fixed in the same change; markers dropped) |
| Note | Confirms **BUG-025** (customer tickets 2026-07-13, instance 1409: `edited_at` and log times shifted −2h). Asserts the CORRECT contract — every REST-serialized datetime ends in `Z`/`±HH:MM` — which fails on `USE_TZ=False` targets because naive-UTC values serialize as bare ISO strings that browsers parse as local time. Fixed in the same change — settings.py renders naive-UTC datetimes with an explicit `Z` on `USE_TZ=False` targets (`REST_FRAMEWORK["DATETIME_FORMAT"]`); the batch runs as a live regression gate. |

---

### Batch 12h — Clearing a FileField through the REST update path ✅

| Property | Value |
| --- | --- |
| Scenario range | 12.39 – 12.41 |
| Type | E |
| Files covered | `api/serializers/base_serializers.py` (`LexClearableFileField`, `LexClearableImageField`, `LexSerializer.serializer_field_mapping`) |
| Test file | `lex/test_project/tests/serializers/test_12h_filefield_clear.py` |
| Test classes | `TestCluster12h_FileFieldClear` |
| Fixtures | new `AttachmentItem` test model (FileField, blank=True) |
| Est. tests | 3 |
| Coverage gain | file-field write-path clear/keep/replace semantics |
| Prereqs | none |
| Status | ✅ Complete — 3 pass / 0 fail |
| Note | Customer report 2026-07-13: a file removed in the edit form reappeared after save. Multipart updates had no way to express removal (omit = keep, DRF rejects `""` as "not a file"), so the admin frontend's dropped-null payload silently kept the old file. Model file fields (incl. `PDFField`/`XLSXField` via MRO lookup) now map to clearable variants: explicit empty value → clear (`allow_blank = True` so DRF's HTML-input handling doesn't rewrite `""` into "omitted"); required (`blank=False`) files reject the clear. 12.39 empty value clears; 12.40 omit keeps; 12.41 upload replaces. Frontend twin: process-admin-general-client F9 batch 9d (provider sends the `''` marker for cleared stored files). Ship both halves together. |
