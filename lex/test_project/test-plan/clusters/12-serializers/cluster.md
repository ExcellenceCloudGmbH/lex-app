## 12. Serializer Contract

**What it tests:** the **JSON shape** the REST API hands the frontend
and external integrations. Cluster 2 tests that HTTP verbs work;
cluster 4 tests that permission predicates fire; this cluster tests
the **payload the customer actually sees and sends**. One bad
serializer change (decimal precision lost, datetime stripped of
timezone, `lex_reserved_scopes` key removed, FK rendered as id
instead of `{id: ...}`) silently breaks the entire UI — without
tripping any existing cluster.

**Why it matters:** the `LexSerializer` / `RestApiModelSerializer`
layer is the single translation boundary between the Django ORM and
the outside world. It owns field visibility (`permission_read`
filtering), type round-tripping (Decimal / DateTime / Date / FK),
framework-managed fields (`id_field`, `short_description`,
`lex_reserved_scopes`), and the history/meta-history unwrap. Every
one of those is a customer-visible contract that today has no
dedicated coverage — the existing serializer unit tests
(`lex/tests/unit/serialization/`) exercise helpers in isolation, not
the end-to-end JSON contract.

**Why cluster 12:** everything it depends on (CRUD over HTTP,
permission predicates, history rows) is already green in clusters
2 / 4 / 5. The serializer is the thin translation layer on top.

**Models needed** (dedicated to this cluster — the existing
cluster-2 models are too shallow; we need a model with one of every
"interesting" field type):

- `WideItem` — `LexModel` carrying one field per type:
  `DecimalField(max_digits=12, decimal_places=4)`, `DateTimeField`,
  `DateField`, `TimeField`, `UUIDField`, `TextField`,
  `CharField(choices=...)`, `JSONField`, `BooleanField`, and
  `ForeignKey` to `RelatedItem`.
- `RelatedItem` — tiny FK target (`name`, `code`).
- `ProtectedWideItem` — same shape as `WideItem` but with a
  non-trivial `permission_read` that returns
  `PermissionResult.allow_fields({"name", "amount"})` for non-admin.
  Proves field-level filtering survives round-trip.

### 12a. Read contract — field visibility & framework-managed fields

| # | Scenario | What We Assert |
|---|----------|----------------|
| 12.1 | GET detail returns all framework-managed keys | Response JSON contains `id`, `id_field`, `short_description`, `lex_reserved_scopes` alongside every model field |
| 12.2 | `short_description` == `str(instance)` | The custom `__str__` of `WideItem` is what the UI receives — no stale cache, no default `Model object (1)` |
| 12.3 | `lex_reserved_scopes` shape | Keys are exactly `{"edit", "delete", "export"}`; `edit` is a **sorted list of field names**; `delete` / `export` are `bool` |
| 12.4 | `permission_read` → `allow_fields({"a", "b"})` | GET detail response contains ONLY `a`, `b`, and the framework-managed keys — every other model field is stripped |
| 12.5 | `permission_read` denies entirely | List endpoint omits the record completely (`FilteredListSerializer` drops empty dicts); detail endpoint returns `{}` or 404-equivalent |
| 12.6 | History-row GET unwraps to main model | Field visibility on a history row matches the main model's `permission_read` |
| 12.7 | MetaHistory scopes are fixed | `lex_reserved_scopes` on a MetaHistorical instance is `{"edit": [], "delete": False, "export": False}` regardless of caller |
| 12.8 | `lex_reserved_scopes.edit` reflects `permission_edit` | When `permission_edit` returns `allow_fields({"x"})`, `edit == ["x"]` — no more, no less |

### 12b. Type round-trip — what goes in comes back out

| # | Scenario | What We Assert |
|---|----------|----------------|
| 12.9 | `DecimalField` preserves precision | POST `"1234.5678"` → GET returns `"1234.5678"` (string, not float). No silent truncation to 2 dp |
| 12.10 | `DateTimeField` round-trip keeps timezone | POST a UTC ISO-8601 string → GET returns a tz-aware ISO-8601 string with the same instant |
| 12.11 | `DateField` uses `YYYY-MM-DD` | POST `"2026-04-21"` → GET returns `"2026-04-21"` — not a datetime, not a Unix timestamp |
| 12.12 | `UUIDField` is a string | POST/GET value is an RFC 4122 string, not a Python `UUID` repr |
| 12.13 | Nullable `ForeignKey` unset | GET returns `null` (not `0`, not `""`, not a stub dict) |
| 12.14 | `ForeignKey` set | GET returns either the FK id or a `{"id": ..., "short_description": ...}` dict per documented contract (whichever the framework's chosen shape is — test locks it in) |
| 12.15 | PATCH accepts FK as `{"id": X}` dict | `_parse_value_for_field` extracts the id; FK resolves to the target row |
| 12.16 | PATCH rejects invalid `choices` | Response 400 with field-level error; DB unchanged |
| 12.17 | `TextField` preserves unicode & newlines | Multi-line unicode string survives POST → GET byte-for-byte |
| 12.18 | `JSONField` preserves structure | Nested dict/list round-trips with key order preserved (for dicts) / element order preserved (for lists) |
| 12.19 | Unknown field in PATCH payload ignored | Response 200, unknown key silently dropped, known fields applied (mirrors 2.5 but asserts at serializer level) |

### 12c. List & Many read contract

| # | Scenario | What We Assert |
|---|----------|----------------|
| 12.20 | `FilteredListSerializer` drops records that serialize to `{}` | List with 3 rows, one denied by `permission_read`, returns 2 rows — not 3 with one empty dict |
| 12.21 | List response row shape matches detail | Every row in a list response has the same framework-managed keys as the detail endpoint |
| 12.22 | `/many/` GET selected rows match list shape | Read-only Many endpoint returns exactly the selected ids and the same framework-managed row keys as list/detail |

### 12d. AuditLog payload filtering

| # | Scenario | What We Assert |
|---|----------|----------------|
| 12.23 | AuditLog payload with FK the caller cannot read | That FK key is stripped from `payload`; rest of payload survives |
| 12.24 | AuditLog payload: unreadable fields pruned from `updates` | `payload.updates` contains only fields the caller is permitted to read on the target model |
| 12.25 | AuditLog payload when target model denies entirely | `payload` becomes `{}` (or only the pinned `id` / `short_description` keys) |

### 12e. Serializer factory contract

| # | Scenario | What We Assert |
|---|----------|----------------|
| 12.26 | `model2serializer` always injects internal fields | Every auto-generated serializer has `id_field`, `short_description`, `lex_reserved_scopes` in its `Meta.fields` |
| 12.27 | `_wrap_custom_serializer` preserves user fields + adds internals | A model's `api_serializers` entry keeps its declared `Meta.fields` AND gets the framework internals appended |
| 12.28 | Serializer is cached per model | Two calls to `get_serializer_map_for_model` return the same class object (not rebuilt per request) |
| 12.32 | Source model default override exposes configured framework alias | `api_serializers["default"]` remains the developer serializer, while the auto-generated serializer is additionally addressable under the configured alias (e.g. `framework_default`) |
| 12.33 | History table inherits framework alias from source model | A `Historical*` model with no own `api_serializers` follows the source model's alias decision through `instance_type`; it does not copy unrelated source serializers such as `detail` |
| 12.34 | Meta-history table walks the full `instance_type` chain | `MetaHistorical* → Historical* → Source` still exposes the auto-generated serializer under the configured alias |
| 12.35 | `_wrap_custom_serializer` preserves `Meta.hide_actions_column` | Serializer-level list UI metadata survives wrapping so tables can suppress Lex's default Show/Edit/Delete column |

**What is explicitly NOT tested here:**

- ❌ **DB-side correctness.** If `DecimalField` precision is wrong in Postgres, that's a model-layer issue, not the serializer. Cluster 2 covers the DB round-trip.
- ❌ **Permission logic.** Cluster 4 owns `permission_read` / `permission_edit` semantics. Here we only assert the serializer *honors* the result.
- ❌ **History chaining.** Cluster 5. Here we only assert a history row, when serialized, produces the right JSON shape.

---

### 12g. Datetime timezone ambiguity (BUG-025) ✅ (xfail strict)

**What it tests:** the timezone-unambiguity contract of every REST-serialized datetime. On `USE_TZ=False` deployment targets (`default`, `GCP`) the framework stores naive UTC (`lex_datetime_now`, `auto_now_add`) and DRF serializes it with no `Z`/offset, so browsers parse the string as local time and every timestamp renders shifted by the viewer's UTC offset (customer: "edited at 13:43, shows 11:43").

**Why a regression matters:** `edited_at` and calculation-log times are audit-relevant, customer-facing values; a silent 2-hour shift undermines trust in the whole audit trail.

**Scenario range:** 12.36 – 12.38. **Test file:** `lex/test_project/tests/serializers/test_12g_datetime_tz_ambiguity.py`. **Type:** E. **Status:** ✅ Complete — all three `xfail(strict=True)` until BUG-025 is fixed. Scenarios: 12.36 `edited_at` carries a timezone designator; 12.37 `created_at` likewise; 12.38 `CalculationLog.timestamp` likewise.
