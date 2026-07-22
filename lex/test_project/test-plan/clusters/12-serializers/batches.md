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

---

### Batch 12i — Foreign-key display names in the read contract ✅

| Property | Value |
| --- | --- |
| Scenario range | 12.42 – 12.45 |
| Type | E |
| Files covered | `api/serializers/base_serializers.py` (`FilteredListSerializer._batch_add_fk_display_names`, `LexSerializer._add_fk_display_names_single`, `_get_fk_display_fields`/`_resolve_fk_names` helpers) |
| Test file | `lex/test_project/tests/serializers/test_12i_fk_display_names.py` |
| Test classes | `TestCluster12i_ForeignKeyDisplayNames` |
| Fixtures | reuse cluster-12 `WideItem` (nullable FK `related` → `RelatedItem`, custom `__str__`) |
| Est. tests | 4 |
| Coverage gain | FK read-contract enrichment (list + detail parity, batch N-safety) |
| Prereqs | none |
| Status | ✅ Complete — 4 pass / 0 fail |
| Note | Resolves the **backend root cause of frontend BUG-F-003** (FK columns render as bare ids like `79`). Every serialized row now carries an **additive** companion key `<fk>__short_description` = `str(related)` (the model author's `__str__`/`short_description` — the documented customization point) alongside the untouched raw id, so filtering/editing on the id are unaffected. The list path resolves the whole page's names in **one `pk__in` query per FK** (mirrors `ModelExport._apply_foreign_key_display_names`); the detail path resolves per-instance (one query, fine) so list-row shape ⊆ detail shape (the 12c invariant holds). Null FK → null companion (stable row shape). Permission-hidden FK columns get no companion (the key is emitted only when the raw column survived visibility filtering). Frontend twin: the grid/detail render the companion instead of the raw id (F3/F9.6). |

---

### Batch 12j — Datetime round trip under the aware-UTC convention ✅

| Property | Value |
| --- | --- |
| Scenario range | 12.46 – 12.48 |
| Type | E |
| Files covered | `lex/lex_app/settings.py` (`USE_TZ=True`/`TIME_ZONE="UTC"`), `lex/api/serializers/base_serializers.py` (DateTimeField round-trip) |
| Test file | `lex/test_project/tests/serializers/test_12j_datetime_roundtrip_convention.py` |
| Test classes | `TestCluster12j_DatetimeRoundTripConvention` (12.46 summer instant → Berlin viewer sees 11:00; 12.47 year-end midnight stays Dec 31 across the winter offset; 12.48 served value is a truthful designated instant) |
| Fixtures | reuse cluster-12 `WideItem` (`created_at_ts` user DateTimeField) |
| Tests landed | **3 pass / 0 fail** |
| Coverage gain | end-to-end datetime round trip under `USE_TZ=True` (the convention the incident broke) |
| Status | ✅ Complete — live regression gates for the cutover. A client sending an explicit instant (fixed frontend `toISOString`) gets that exact moment back for any viewer zone. Complements 12g (BUG-025 designator gate). Frontend twin: `datetimeConventionRoundTrip.test.ts`. |

---
