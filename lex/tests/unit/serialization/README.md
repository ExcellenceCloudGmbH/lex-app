# Serialization Tests — `lex.tests.unit.serialization`

> **Story:** *"Every model instance must serialize correctly — field visibility
> controlled by permissions, FK relations filtered, and the serializer must
> route to the right variant (read vs write vs list) depending on the operation."*

## What Lives Here (4 files)

| File | Covers |
|------|--------|
| `test_base_serializers.py` | `LexSerializer` — field mapping, nested serializer construction, `lex_reserved_scopes` computation, shadow instance building, FK filtering |
| `test_permission_aware_serializer.py` | `PermissionAwareSerializerMixin` — field-level write-permission checks, change detection, camelCase→snake_case translation, `run_validation` pre-check |
| `test_serializer_helpers.py` | Serializer utility functions — field type coercion, default value resolution, related-field introspection |
| `test_serializer_parse_value.py` | `parse_value` — type-aware parsing for filter/search params (dates, booleans, decimals, UUIDs, etc.) |

## How to Run

```bash
source /path/to/your-project/.venv/bin/activate  # the host project where lex-app is installed editable
lex test lex.tests.unit.serialization
```
