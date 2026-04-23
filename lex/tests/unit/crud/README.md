# CRUD & Filter Tests — `lex.tests.unit.crud`

> **Story:** *"The API supports tree-based FK filters, primary-key list filters,
> and string-search filters — all driven by query parameters the frontend sends.
> These pure-function tests verify the filter builders produce correct Django ORM
> lookups without touching the database."*

## What Lives Here (2 files)

| File | Covers |
|------|--------|
| `test_generic_filters.py` | `GenericFilters` — all five filter backends (`UserReadRestrictionFilterBackend`, `ForeignKeyFilterBackend`, `PrimaryKeyListFilterBackend`, `StringFilterBackend`), plus `create_filter_queries_from_tree_paths` recursive tree→ORM builder |
| `test_helpers.py` | `api.utils.helpers` — utility functions for request parsing, pagination helpers, queryset annotation |

## How to Run

```bash
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate
lex test lex.tests.unit.crud
```
