# Multi-value filters and cross-filtering

**Date:** 2026-10-14
**Status:** draft, for review
**Touches:** `lex-app` (filter translation, a filter-values endpoint, a list option, `lex_view` and its
Streamlit component), `process-admin-general-client` (grid filters, a filter bar, the embed bridge),
`lex-app-docs` (three pages)

## The problem

A review page needs to narrow one table with several values per column at once (for example two or
three statuses), with filters that narrow each other: once a region is picked, the customer filter
only offers that region's customers. Projects build such pages by hand in Streamlit today, because
the lex-app grid cannot do either, and an embedded lex-app table cannot be told what to filter.

The goal is a native lex-app feature first, which every embed then gets for free:

1. **Value lists.** A column filter lists the column's values with a row count each; the user ticks
   any number of them.
2. **Narrowing.** Each value list reflects the other active filters.
3. **A filter bar** above the grid shows chosen columns' value lists as dropdowns.
4. **Streamlit.** `lex_view` can set an embed's filters and its bar, hears when its filters change,
   and can change them again without reloading the frame. Linking two embedded tables is then plain
   Python: read one embed's selection or filters and pass them to the other.

## What already exists

**Frontend** (`process-admin-general-client`, default branch `lex-app-v2-pac-latest`)

- The grid is AG Grid Enterprise 33.2.1 with a licence (`src/index.tsx`). The enterprise Set Filter
  and Multi Filter are installed but not registered (`CustomDatagrid.tsx`, `gridModules`).
- Every list page uses the server-side row model. Rows come from `POST api/model_entries/<model>/list`
  with the whole AG request in the body (`ServerSideDatasource.ts`).
- `getFilterForField` picks a column's filter by field type: number filter for numbers, date filter
  for dates, `AGDateTimeFilter` for date-times, `AGForeignKeyFilter` for foreign keys (single value,
  sent as `{filterType: 'text', type: 'equals'}`), `AGBooleanFilter` for booleans (sent as
  `{filterType: 'set', values: [bool]}`), and AG's text filter for everything else.
- Saved views keep the filter model in local storage (`<resource>_gridViews`) and `restoreView`
  applies it when the grid starts.
- The list ignores filters in the URL (`<List disableSyncWithLocation>` in `CustomList.tsx`).
- The embed bridge (`src/utils/lexAppBridge.ts`) sends `create`, `update`, `delete`, `select` and
  `navigate`, each gated by an `emit_*` flag in `FLAG_MAP`. An event type missing from that map is
  sent without any opt-in check. The only messages the app accepts from the host are the widget
  host's `manifest`.
- "Select All Records" fetches ids with its own GET filter translator (`translateFilterModel`),
  which drops several filter shapes. The bulk-update work is moving it to the server translator.

**Backend** (`lex-app`, `lex-app-v2`)

- `ListModelEntries._build_filter_q` (`lex/api/views/model_entries/List.py`) translates the AG
  filter model for the list, the export and the AG requests. A `set` model becomes `field__in`
  with the values unconverted. An empty list is ignored, so it matches every row; AG means "no
  rows". A blank value never matches, because SQL `IN` skips NULL. An unknown `filterType`,
  including `multi`, is dropped, which widens the result.
- `_execute_group_level` already returns a column's distinct values with a `__childCount`, over the
  filtered queryset: `qs.values(field).annotate(Count("pk"))`. It checks that the field is a real
  column, but not that the user may read that field.
- Row visibility comes from `UserReadRestrictionFilterBackend`. The default `permission_read`
  (Keycloak `read` scope) is all fields or none and is applied in the database. A model that
  overrides `permission_read` is evaluated per row in Python, and its `PermissionResult` can hide
  single fields per row; the serializer then drops those fields from the row.
- A foreign key's display name is `str(related)`, sent as the companion key
  `<field>__short_description` (`base_serializers.py`).
- List options travel to the frontend as `list_ui`, read from the serializer's `Meta` by
  `get_list_ui_options` (today only `hide_actions_column`).
- `lex_view` (`lex/lex_app/streamlit/embed.py`) builds the embed URL. It renders the
  `_lex_view_component` when any `on_*` flag is set, and a plain iframe otherwise. The component
  re-points the iframe only when the URL really changes, and already posts the host's theme into
  the frame with `{source: 'lex-app-host', type: 'theme'}`.

## The design

### 1. One filter state

All filtering stays in AG Grid's filter model. The column menus, the filter bar, saved views,
export, "Select All Records" and the embed bridge all read and write that one model, so they cannot
disagree.

The filter each column gets:

| Field | Column filter | Value list |
|---|---|---|
| has `choices` | Set | yes, labelled with the choice labels |
| foreign key | Set (replaces the single-value `AGForeignKeyFilter`) | yes, labelled with `str(related)` |
| boolean | Set (replaces `AGBooleanFilter`) | Yes, No, (Blank) |
| text | Multi: text condition + Set | yes |
| integer | Multi: number condition + Set | yes |
| date | Multi: date condition + Set | yes |
| decimal, float | number condition, as today | no — continuous values |
| date-time | `AGDateTimeFilter`, as today | no — every value is an instant |
| file, JSON, many-to-many | none, as today | no |

Model shapes:

- Set: `{filterType: 'set', values: [key, …]}`. A key is the stored value as a string: a foreign key's
  primary key, `"true"` / `"false"`, an ISO date, a number. `null` stands for blank.
- Multi: `{filterType: 'multi', filterModels: [conditionModel | null, setModel | null]}`.

**Old saved views keep working.** `restoreView` migrates a stored model whose shape no longer fits its
column:
- a text, number or date model on a column that is now Multi becomes `{filterType: 'multi',
  filterModels: [old, null]}`;
- a foreign key's `text equals` becomes a Set with that one key;
- a boolean Set's `true` / `false` become `"true"` / `"false"`.

The backend keeps accepting the old shapes.

### 2. Backend: filter translation

`_build_filter_q` changes in three places, so the list, the export and the AG requests all honour
the new shapes. It stays the one server-side translator.

- **Set.** Each key is converted with the field's `to_python` (with the target primary key's
  `to_python` for a foreign key). `null` adds `Q(field__isnull=True)` to the `__in`. An empty list
  matches no rows (`Q(pk__in=[])`). A key that fails conversion is a 400 naming the field, not a
  silent widening.
- **Multi.** The child models are applied with AND. An empty child is skipped.
- **Unknown filter types** stay ignored for compatibility, but are logged with the field name.

### 3. Backend: the filter-values endpoint

`POST api/model_entries/<model>/filter_values`

Request:

```json
{
  "field": "region",
  "filterModel": { "status": { "filterType": "set", "values": ["open", "closed"] } },
  "selected": ["north"],
  "search": "no",
  "limit": 200,
  "as_of": null,
  "serializer": "default"
}
```

Response:

```json
{
  "values": [
    { "key": "north", "label": "North", "count": 41 },
    { "key": null, "label": "(Blank)", "count": 3 }
  ],
  "truncated": false
}
```

Behaviour:

1. **Same rows as the grid.** It starts from the queryset the list POST uses: the provider queryset
   (including `as_of`), then the read filter. It is a read, with the same permission class settings
   as the list POST.
2. **Faceting.** It applies `filterModel` without the entry for `field`, so the list shows what the
   *other* filters leave. A column cannot narrow its own value list.
3. **Counts.** It groups by `field` with `Count("pk")`, as `_execute_group_level` does.
4. **Labels.** Choices use their labels; a foreign key uses `str(related)`, resolved in one query
   per page of keys; booleans become Yes / No; `null` becomes "(Blank)".
5. **Selected values stay visible.** Every key in `selected` appears in the result, with count 0 if
   the other filters removed it, so a ticked value can still be unticked.
6. **Search and size.**
   - `search` matches labels case-insensitively. On text fields without choices it runs in the
     database (`__icontains`). On every other type it runs in Python on the labels, over at most
     `LEX_FILTER_VALUES_SCAN_LIMIT` distinct keys (default 5,000, most frequent first).
   - Results are sorted by label and cut at `limit` (default 200, maximum 1,000).
   - `truncated` says the list is not complete, and the UI then asks the user to search.
7. **Field-level read permission.**
   - The field must be a filterable field of the requested serializer. A field the serializer does
     not expose can never be listed, however the model is built.
   - With the default `permission_read`, the database aggregate is enough: rows are all-or-nothing.
   - When the model overrides `permission_read`, only rows whose `PermissionResult` allows the field
     are counted. These rows are evaluated per row in chunks, as the read filter already does, and
     capped by the scan limit. A field the user can read on no row gives an empty list, not an
     error.
   - The value list never shows a value the user could not see in the grid.
8. **Errors.**
   - A field that is not a filterable field of the serializer is a 400 "field is not filterable".
   - A bad `filterModel` key is a 400 naming the field.

### 4. Backend: `filter_bar` in `list_ui`

`get_list_ui_options` adds `filter_bar`: the field names a list shows in its bar by default. It reads
`Meta.filter_bar` from the serializer, falling back to a model attribute `filter_bar`, so projects
without a custom serializer can set it. Field names that are not filterable are dropped with a
warning. The default is `[]`, which means no bar.

### 5. Frontend: column filters and value lists

- Register `SetFilterModule` and `MultiFilterModule`, and give each column the filter from the table
  in §1.
- **Value lists** come from `filter_values`. AG's Set Filter `values` callback posts the grid's
  current filter model and the column's ticked keys, with `refreshValuesOnOpen: true`. Narrowing
  therefore holds every time a list opens.
  - Each item shows its label and count.
  - `valueFormatter` and `keyCreator` map keys to labels.
  - The list keeps the server's order.
- **Older backends.** If the endpoint answers 404 or 405, value lists are reported as unavailable,
  and the column keeps its condition filter.
- **"Select All Records"** must honour Set and Multi models. If the bulk-update work has already moved
  it to the server translator, §2 covers it. Otherwise `translateFilterModel` learns both shapes in
  this change.

### 6. Frontend: the filter bar

- **Where it sits:** a row between the toolbar and the grid. Each bar column is a dropdown: multiple
  choice, chips, a search box and counts, fed by `filter_values` exactly like the column menu.
- **One state:** picking or removing a value writes the column's Set model through the grid API and
  calls `onFilterChanged`. The bar re-reads the model on every `filterChanged`, so the bar and the
  column menu always show the same selection.
- **Condition filters:** on a Multi column the bar edits only the Set part. If a condition is also
  active there, the bar item shows a small "+ condition" mark.
- **Which columns:**
  1. The embed's `filter_bar` parameter, if given.
  2. Otherwise the active saved view's `filterBar`.
  3. Otherwise `list_ui.filter_bar`.

  Users add a column with **Show in filter bar** in its column menu and remove one with the chip's ×.
  Saved views store the choice as `filterBar`.
- **When it shows:** only when it has at least one column. `hide_toolbar` does not hide it, because
  on embed pages the bar is the main control.

### 7. Embedding in Streamlit

New `lex_view` parameters:

```python
lex_view(
    "invoice",
    filters={"status": ["open", "closed"], "region": "north", "customer": None},
    filter_bar=["status", "region", "customer"],
    on_filter=True,
)
```

**What the parameters mean**

- **`filters`:** field name to stored values.
  - A list gives a value list, and an empty list means "match nothing".
  - A single value means just that value.
  - `None` clears the field.
  - Fields that are not columns of the embedded list are ignored, with a console warning.
  - The host's filters replace the filters on the fields they name; filters on other fields, set by
    the user, stay.
- **`filter_bar`:** the bar's columns. `[]` hides the bar.
- **`on_filter`:** report filter changes as `filter` events.

**On the wire**

1. The first load carries the filters in the URL, as `filters=<base64url JSON>` and `filter_bar=a,b`.
   Both are added to `SNAPSHOT_EXACT`, so they survive in-app navigation. Only the list of the
   resource the embed opened with applies them: when it first mounts, after the saved view, and they
   win for the fields they name. Another table reached inside the same embed ignores them.
2. Passing `filters` selects the component path, as the `on_*` flags already do. Python hands the
   component the URL without `filters` plus the filters as their own argument.
   - On first render the component adds them to the iframe URL.
   - On later renders, if only the filters changed, it posts `{source: 'lex-app-host', type:
     'filter', payload: {filters}}` to the frame's origin instead of reloading it.
3. The app accepts a host `filter` message only from `window.parent`, with that source tag, and
   applies it like the first-load filters.
4. `filter` joins `LexBridgeEventType` and `FLAG_MAP` (`emit_filter`), so it is gated like every
   other event. Payload: `{resource, filters: {field: [keys]}, filter_model, row_count}`.
   - `filters` lists only value-list selections; `filter_model` is the whole AG model.
   - The event is sent once the grid has the new row count, debounced by 300 ms.
5. Feeding an embed's own `filter` event back into its `filters` settles after one rerun. The
   component does not post a filter that equals the one the frame already has.

**Linking two tables** needs no new API:

```python
picked = lex_view("order", on_select=True, key="orders")
ids = picked["payload"]["ids"] if picked and picked["type"] == "select" else None
lex_view("order_line", filters={"order": ids}, key="lines")
```

### 8. Security and correctness

- A value list never contains a row or a field value the user cannot read in the grid (§3.7).
- Host filters are ordinary grid filters: the server applies the same read permissions to every
  request, so a page cannot widen what a user sees.
- Host messages are accepted only from the parent frame with the `lex-app-host` tag, and their
  fields are checked against the list's filterable columns.
- A blank value filters as blank, and an empty selection filters as "no rows", consistently in the
  grid, export and "Select All Records".

## Error handling

| Case | Behaviour |
|---|---|
| `filter_values` returns 400 | The list shows the server's message; the column keeps working with its condition filter |
| `filter_values` 404 / 405 (older backend) | Value lists marked unavailable; bar hidden; condition filters unchanged |
| A filter key that cannot be converted | 400 naming the field, from both the list and `filter_values` |
| `filters` names an unknown field | Ignored with a console warning; other fields applied |
| `filter_bar` names an unknown or non-filterable field | Dropped with a warning |
| Values truncated | The list says so and offers search |

## Testing

Tests follow the test plans of both repositories (cluster tests, scenarios allocated when the plan is
written).

**`lex-app`**
- **Queries cluster:**
  - Set conversion per field type, blank, empty list and Multi, in the list, export and AG requests.
  - `filter_values` faceting: a field's own filter is excluded.
  - Counts, and labels for choices, foreign keys and booleans.
  - Selected values kept at 0, search on text and foreign keys, `limit` and `truncated`, and `as_of`.
- **Permissions cluster:**
  - Rows hidden by the read filter are never counted.
  - A field hidden per row by an overridden `permission_read` is counted only on rows that allow it,
    and a field readable on no row gives an empty list.
  - A field the serializer does not expose gets a 400, even when the model has it.
- **The cluster that owns `lex_view`:**
  - The URL and component arguments for `filters` and `filter_bar`.
  - The component posts a changed filter instead of reloading the frame, and does not post an
    unchanged one.

**`process-admin-general-client`**
- **Grid-read cluster:**
  - Each field type gets its filter from the table in §1.
  - The values callback sends the right request; labels and counts render.
  - The bar and the column menu stay in sync, both ways.
  - The migration of old saved-view models.
- **Grid-views cluster:** saved views keep `filterBar` and Set / Multi models.
- **Embed cluster:**
  - The `filters` and `filter_bar` parameters, and the host `filter` message applied without a
    reload.
  - The `filter` event is gated by `emit_filter` and carries the documented payload.
- **End to end:**
  - Narrowing across two bar filters, with counts updating.
  - An embed-host page pushing a filter into the frame and receiving the event back.

## Delivery order

1. `lex-app`: filter translation (§2), `filter_values` (§3), `list_ui.filter_bar` (§4).
2. `process-admin-general-client`: column filters and value lists (§5), the filter bar (§6), the
   bridge's `filter` event and host message (§7).
3. `lex-app`: `lex_view` parameters and the component (§7); then the frontend version pin.
4. `lex-app-docs`, three pages:
   - *Filtering and sorting:* value lists, narrowing and the bar. Also corrects two current claims:
     the foreign-key filter is single-value today, and filters wait for **Apply**.
   - *Embedding app pages:* `filters`, `filter_bar`, `on_filter` and the linking pattern.
   - *Serializers:* the `filter_bar` option.

The frontend reaches projects through the pinned `lex-app-frontend` package, so steps 1 and 2 ship in
the same `lex-app` release; §5's fallback covers a frontend paired with an older backend.

## Out of scope

- Linking tables inside lex-app itself (a page with several grids).
- Locked filters an embed's user cannot remove.
- Value lists for decimals, floats and date-times.
- Bulk update, handled separately.
