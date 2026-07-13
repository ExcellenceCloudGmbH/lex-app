---
date: 2026-07-13
clusters: [12h]
tests_added: "3 (12.39–12.41) + source in 2 files (base_serializers + test model)"
suite_tally: "12h 3 pass / 0 fail; regression: serializers+exports+crud_api+api_layer = 193 pass / 1 xfail / 0 fail"
---

**Batch 12h landed — clearable file fields (customer report 2026-07-13:
removing a file from a FileField and saving brings it back).** Root cause is a
two-sided gap: the admin frontend drops a cleared FileInput's `null` from the
multipart body, and even if it hadn't, DRF gives multipart clients no way to
express file removal (omit = keep, `""` = "not a file" validation error) — so
removal was impossible by construction. Fix: `LexSerializer` now maps model
file fields (including `PDFField`/`XLSXField` subclasses via DRF's MRO lookup)
to `LexClearableFileField`/`LexClearableImageField`, which accept an explicit
empty value as "clear the stored file" (`allow_blank = True` keeps DRF's
HTML-input handling from rewriting `""` into "omitted"); required
(`blank=False`) files reject the clear like a missing required field. Omit
still keeps, upload still replaces (12.40/12.41 pin both). Frontend twin:
process-admin-general-client F9 batch 9d sends the `''` marker for fields
whose previous value was a stored file URL — ship both halves together.
See [batch 12h](../../clusters/12-serializers/batches.md).
