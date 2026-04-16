# AG Grid & Data Export Tests — `lex.tests.unit.grid`

> **Story:** *"The frontend renders data in AG Grid — with server-side
> filtering, sorting, grouping, pivoting, and per-user row-level read
> restrictions. The backend must translate AG Grid requests into Django
> querysets and apply permission masks before any data leaves the server."*

## What Lives Here (7 files, 158 tests)

| File | Tests | Covers |
|------|------:|--------|
| `test_ag_grid_list_utilities.py` | 74 | AG Grid utility functions — `parse_filter_model`, `parse_sort_model`, datetime parsing, `build_queryset_filters`, reserved-param/lookup constants |
| `test_ag_grid_server_side.py` | 8 | Integration-style AG Grid tests — column filtering, row grouping/aggregation, pivot mode, sort-model translation, datetime query-param filters |
| `test_model_export_ag_grid.py` | 8 | AG Grid export — pivot DataFrame layout, grouping column order, AG selection filters with group-key paths, nested group expansion, FK-value label refresh, per-object field-level permission masking |
| `test_model_export_utilities.py` | 42 | Export utility functions — bool parsing, per-object field-permission resolution (new API / legacy / fallback), AG request normalisation, base64 ID decoding, group-key path extraction, column-layout application |
| `test_pk_list_filter_backend.py` | 8 | `PKListFilterBackend` — query-param filtering for Many/One views and base64-encoded PK support |
| `test_user_read_filter_backend.py` | 4 | `UserReadFilterBackend` — default-permission fast-path, resource-ID filtering, none-queryset on no-read, custom-permission model exclusion |
| `test_user_read_restriction_filter.py` | 14 | Extended per-row read enforcement — AuditLogStatus/CalculationLog bypass, default-permission target resolution, UMA-based queryset filtering (global / scoped / absent read permissions) |

## Key Concepts Tested

- **Filter translation** — AG Grid `filterModel` JSON → Django `Q` objects
- **Sort translation** — AG Grid `sortModel` → Django `.order_by()` args
- **Grouping & pivot** — row-group aggregation and pivot-column generation
- **Export pipeline** — AG Grid selection → DataFrame → XLSX/CSV with permission masking
- **Row-level security** — `UserReadFilterBackend` restricts querysets per user's UMA permissions
- **PK filtering** — base64-encoded PK lists for multi-select operations

## How to Run

```bash
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate
lex test lex.tests.unit.grid               # all 158 tests
lex test lex.tests.unit.grid.test_ag_grid_list_utilities  # 74 tests
```
