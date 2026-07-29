# REST API Tests — `lex.tests.unit.api`

> **Story:** *"A frontend developer hits the REST API to list, create, update,
> and delete model entries — and the API must return the right structure,
> enforce permissions, and serialise every field correctly."*

## What Lives Here (28 files)

| File | Tests | Covers |
|------|------:|--------|
| `test_one_model_entry.py` | 22 | Single-entry CRUD — GET/PUT/PATCH/DELETE with permission checks |
| `test_many_model_entries.py` | 18 | List/bulk endpoints — pagination, ordering, filtering |
| `test_destroy_one_with_payload.py` | 8 | DELETE with request body — soft-delete metadata, audit payload |
| `test_model_entry_provider_mixin.py` | 12 | `ModelEntryProviderMixin` — queryset resolution, serializer selection |
| `test_base_serializer_helpers.py` | 14 | Serializer utility methods — field mapping, nested serializer construction |
| `test_serializer_map_behavior.py` | 10 | Dynamic serializer selection — read vs write vs list variants |
| `test_xlsx_field.py` | 6 | XLSX field serialization — upload, download, content-type negotiation |
| `test_history_endpoint.py` | 8 | Bitemporal history API — as-of queries, history record listing |
| `test_model_container.py` | 12 | `ModelContainer` — model registry, lazy resolution, missing-model errors |
| `test_model_collection_structure.py` | 16 | `ModelCollectionStructure` — full YAML/JSON schema generation |
| `test_model_structure_yaml.py` | 10 | YAML-specific model structure — field definitions, relationship mapping |
| `test_model_structure_permissions.py` | 14 | Permission annotations in model structure — field-level, model-level |
| `test_model_permissions_view.py` | 12 | `/permissions/` endpoint — effective permissions for current user |
| `test_model_structure_builder_merge.py` | 8 | Structure builder merge logic — widget + styling + permission overlays |
| `test_model_structure_types.py` | 10 | Type mapping — Python field types → frontend widget types |
| `test_model_utils.py` | 18 | Model utility functions — field introspection, related-model discovery |
| `test_model_registration.py` | 14 | Auto-registration — models discovered and registered at startup |
| `test_constants.py` | 15 | API constants — status codes, content types, header names |
| `test_api_helpers.py` | — | API request/response helper utilities |
| `test_api_helpers_advanced.py` | — | Advanced API helper edge cases |
| `test_api_key_requests.py` | — | API key–based request authentication |
| `test_destroy_mixin.py` | — | Destroy mixin — soft-delete, cascade behaviour |
| `test_filter_backends.py` | — | DRF filter backend integration |
| `test_filter_tree_node.py` | — | Filter tree node parsing and traversal |
| `test_model_converter.py` | — | URL converter for model names |
| `test_non_atomic_view_transactions.py` | — | Non-atomic view transaction handling |
| `test_pagination.py` | — | Pagination configuration and limits |
| `test_view_utils.py` | — | View utility helpers |

## Key Concepts Tested

- **CRUD lifecycle** — every REST verb (GET/POST/PUT/PATCH/DELETE) with correct status codes
- **Serializer routing** — different serializer for list vs detail vs write operations
- **Model structure** — auto-generated JSON/YAML schema consumed by the frontend
- **Permission overlay** — model-level and field-level permissions woven into the structure
- **Registration** — models are auto-discovered and registered without explicit configuration

## How to Run

```bash
source /path/to/your-project/.venv/bin/activate  # the host project where lex-app is installed editable
lex test lex.tests.unit.api                # all tests
lex test lex.tests.unit.api.test_one_model_entry  # 22 tests
```
