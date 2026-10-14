# Multi-value filters — frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In the grid and every embed:
- value-list filters (AG Set, and Multi = condition + list) whose lists narrow under the other
  filters;
- a filter bar over the same filter state;
- saved views that migrate old filter shapes;
- embeds that take filters from the host, report filter changes, and update without reloading.

**Architecture:** AG Grid's filter model is the only filter state.
- Small, separately tested modules decide each column's filter (`columnFilters`), fetch value lists
  (`filterValuesClient`), configure Set filters (`setFilterParams`), read and write selections
  (`setSelection`), migrate saved models (`filterModelMigration`), resolve bar columns
  (`filterBarColumns`) and translate host filters (`embedFilters`).
- `CustomDatagrid` and `CustomList` only wire them.

**Tech Stack:** React 18, react-admin 5.7, AG Grid Enterprise 33.2.1 (`SetFilterModule`,
`MultiFilterModule`), MUI, Vitest + Testing Library, Playwright (E tier).

**Spec:** `lex-app` repo, `docs/superpowers/specs/2026-10-14-multi-value-filters-and-cross-filtering-design.md`
(read its *Amendments* first).

**Depends on:** the backend plan (`docs/superpowers/plans/2026-10-14-multi-value-filters-backend.md`),
for the `filter_values` endpoint, the Set/Multi translation and `list_ui.filter_bar`. Tasks 1–6 run
against mocks; Task 8 needs a backend with the backend plan's Tasks 1–5 applied.

## Global Constraints

- **No customer data** in any file, test, commit, PR or issue. Use the e2e project (funds,
  currencies) or invented names.
- **Repo:** `ExcellenceCloudGmbH/process-admin-general-client`. Default branch `lex-app-v2-pac-latest`,
  not `main`.
- **Worktree:** `/home/syscall/LUND_IT/process-admin-general-client/.claude/worktrees/multi_value_filters`,
  branch `feat/multi-value-filters`. Borrow the main clone's install:
  `ln -sfn /home/syscall/LUND_IT/process-admin-general-client/node_modules node_modules`.
- **Test commands:** `./node_modules/.bin/vitest run <paths>` and
  `./node_modules/.bin/tsc --noEmit -p tsconfig.json`.
  - The borrowed install has a fixed baseline: about 193 vitest failures in 24 files, and 238 tsc
    errors.
  - Compare before and after, never absolute counts.
  - `yarn` needs `NPM_MARMELAB_TOKEN` set to any value.
- **Commits and PR:**
  - Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - The PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
  - No issue IDs in the branch, title or body.
  - The user merges.
- **Test plan:** `docs/test-plan/`. Allocations checked on 2026-10-14:
  - F3 grid_read: letter `o`, F3.66–F3.80.
  - F4 grid_views: letter `e`, F4.41–F4.44.
  - F12 embed_streamlit: letter `u`, F12.104–F12.110. `t` is held by open PR #576; if it has merged,
    `u` is still the next letter.
- **Wire constants** (shared with the backend):
  - Keys: `null` (blank), `"true"` / `"false"`, ISO dates, `String(value)`.
  - Labels: `"(Blank)"`; booleans `"Yes"` / `"No"` (the server sends them).
  - Limits: `filter_values` default 200, column-menu lists 1000.
  - The host message is `{source: 'lex-app-host', type: 'filter', payload: {filters}}`.
  - The bridge event is `filter`, opted in by `emit_filter`.
  - The URL parameters are `filters` (base64url JSON) and `filter_bar` (comma list; empty = hide).

---

### Task 1: Which filter each column gets

**Files:**
- Create: `src/components/model-components/list/filters/columnFilters.ts`
- Modify: `src/api/endpoints/model-info.ts` (`GeneralFieldInfo.is_pk`, `ListUiInfo.filter_bar`)
- Modify: `src/components/model-components/list/CustomDatagrid.tsx` (register `SetFilterModule` and
  `MultiFilterModule`; colDef filters from `buildColumnFilter`; drop `getFilterForField`)
- Test: `src/components/model-components/list/filters/__test__/columnFilters.cluster.test.ts` (new)
- Modify: `src/components/model-components/list/__test__/CustomDatagrid.test.tsx` (the five filter
  expectations)

**Interfaces:**
- Produces:
  - `type ColumnFilterKind = 'set' | 'multi-text' | 'multi-number' | 'multi-date' | 'number' | 'date-time' | 'default'`
  - `columnFilterKind(fieldInfo: FieldInfo): ColumnFilterKind`
  - `hasValueList(kind: ColumnFilterKind): boolean`
  - `buildColumnFilter(kind, deps: ColumnFilterDeps): { filter?: unknown; filterParams?: Record<string, unknown> }`
  - `ColumnFilterDeps = { conditionParams, setParams, dateTimeFilter, dateTimeParams }`

- [ ] **Step 1: Write the failing test**

Create `src/components/model-components/list/filters/__test__/columnFilters.cluster.test.ts`:

```ts
/**
 * F3.66–F3.67 — every column gets the filter its values call for.
 *
 * Intent: value lists (AG's Set filter) for columns whose values are few and
 * named: choices, foreign keys, booleans. A condition filter plus a value list
 * (AG's Multi filter) for text, integers and dates, so "contains" and "pick these"
 * both work. The condition filter alone where every value differs: decimals,
 * date-times, the primary key. A regression either hides the value list a user
 * needs or offers a list of thousands of unique amounts.
 *
 * Cluster F3 (grid_read), batch 3o, scenarios F3.66–F3.67. Tier U.
 */
import { describe, expect, it } from 'vitest'
import {
  buildColumnFilter,
  columnFilterKind,
  hasValueList,
} from '../columnFilters'

const field = (type: string, extra: Record<string, unknown> = {}) =>
  ({ name: 'f', readable_name: 'F', type, ...extra }) as any

describe('F3 — value lists: which filter each column gets (batch 3o)', () => {
  it('F3_66: the kind follows the field type', () => {
    // Scenario F3.66
    // Given fields of every type the backend reports
    // When their filter kind is decided
    // Then choices, foreign keys and booleans list values; text, integers and dates
    // combine a condition with a list; decimals, date-times and the key do not list
    expect(columnFilterKind(field('string', { choices: [['a', 'A']] }))).toBe('set')
    expect(columnFilterKind(field('foreign_key', { target: 'currency' }))).toBe('set')
    expect(columnFilterKind(field('boolean'))).toBe('set')
    expect(columnFilterKind(field('string'))).toBe('multi-text')
    expect(columnFilterKind(field('int'))).toBe('multi-number')
    expect(columnFilterKind(field('int', { is_pk: true }))).toBe('number')
    expect(columnFilterKind(field('date'))).toBe('multi-date')
    expect(columnFilterKind(field('float'))).toBe('number')
    expect(columnFilterKind(field('decimal'))).toBe('number')
    expect(columnFilterKind(field('date_time'))).toBe('date-time')
    for (const type of ['file', 'image_file', 'pdf_file', 'xlsx_file', 'json', 'many_to_many']) {
      expect(columnFilterKind(field(type)), type).toBe('default')
    }
    expect(hasValueList('set') && hasValueList('multi-date')).toBe(true)
    expect(hasValueList('number') || hasValueList('date-time') || hasValueList('default')).toBe(false)
  })

  it('F3_67: each kind becomes the AG Grid filter it names', () => {
    // Scenario F3.67
    // Given the deps a column filter is built from
    // When each kind is built
    // Then Set and Multi filters carry the value-list params; a Multi lists the
    // condition first and the values second
    const deps = {
      conditionParams: { buttons: ['reset', 'apply'] },
      setParams: { values: () => undefined },
      dateTimeFilter: 'DateTimeComponent',
      dateTimeParams: { buttons: ['apply'] },
    }
    expect(buildColumnFilter('set', deps)).toEqual({ filter: 'agSetColumnFilter', filterParams: deps.setParams })
    expect(buildColumnFilter('multi-text', deps)).toEqual({
      filter: 'agMultiColumnFilter',
      filterParams: {
        filters: [
          { filter: 'agTextColumnFilter', filterParams: deps.conditionParams },
          { filter: 'agSetColumnFilter', filterParams: deps.setParams },
        ],
      },
    })
    expect((buildColumnFilter('multi-number', deps).filterParams as any).filters[0].filter).toBe('agNumberColumnFilter')
    expect((buildColumnFilter('multi-date', deps).filterParams as any).filters[0].filter).toBe('agDateColumnFilter')
    expect(buildColumnFilter('number', deps)).toEqual({ filter: 'agNumberColumnFilter', filterParams: deps.conditionParams })
    expect(buildColumnFilter('date-time', deps)).toEqual({ filter: 'DateTimeComponent', filterParams: deps.dateTimeParams })
    expect(buildColumnFilter('default', deps)).toEqual({})
  })
})
```

- [ ] **Step 2: Run it to see it fail**

Run: `./node_modules/.bin/vitest run src/components/model-components/list/filters`
Expected: fails to resolve `../columnFilters`.

- [ ] **Step 3: Implement `columnFilters.ts`**

```ts
/**
 * Which filter each grid column gets, and the AG Grid config for it.
 *
 * One table for the whole grid: value lists (AG's Set filter) for choices,
 * foreign keys and booleans; a condition filter PLUS a value list (AG's Multi
 * filter) for text, integers and dates; the condition filter alone for decimals,
 * date-times and the primary key, where every value differs. Files, JSON and
 * many-to-many keep the grid's default filter.
 */
import type { FieldInfo } from '../../../../api/endpoints/model-info'

export type ColumnFilterKind =
  | 'set'
  | 'multi-text'
  | 'multi-number'
  | 'multi-date'
  | 'number'
  | 'date-time'
  | 'default'

const INTEGER_TYPES = new Set(['int', 'integer'])
const CONTINUOUS_NUMBER_TYPES = new Set(['float', 'decimal', 'number'])
const UNLISTABLE_TYPES = new Set(['file', 'image_file', 'pdf_file', 'xlsx_file', 'json', 'many_to_many'])

export function columnFilterKind(fieldInfo: FieldInfo): ColumnFilterKind {
  const { type } = fieldInfo
  if (Array.isArray(fieldInfo.choices) && fieldInfo.choices.length > 0) return 'set'
  if (type === 'foreign_key' || type === 'boolean') return 'set'
  if (type === 'date_time') return 'date-time'
  if (CONTINUOUS_NUMBER_TYPES.has(type)) return 'number'
  if (INTEGER_TYPES.has(type)) return fieldInfo.is_pk ? 'number' : 'multi-number'
  if (type === 'date') return 'multi-date'
  if (UNLISTABLE_TYPES.has(type)) return 'default'
  return 'multi-text'
}

export const hasValueList = (kind: ColumnFilterKind): boolean =>
  kind === 'set' || kind.startsWith('multi-')

export interface ColumnFilterConfig {
  filter?: unknown
  filterParams?: Record<string, unknown>
}

export interface ColumnFilterDeps {
  /** Params for AG's provided condition filters (Apply / Reset buttons). */
  conditionParams: Record<string, unknown>
  /** The Set filter params for this column (see setFilterParams.ts). */
  setParams: Record<string, unknown>
  /** The custom date-time filter component and its params. */
  dateTimeFilter: unknown
  dateTimeParams: Record<string, unknown>
}

export function buildColumnFilter(kind: ColumnFilterKind, deps: ColumnFilterDeps): ColumnFilterConfig {
  switch (kind) {
    case 'set':
      return { filter: 'agSetColumnFilter', filterParams: deps.setParams }
    case 'multi-text':
      return multi('agTextColumnFilter', deps.conditionParams, deps.setParams)
    case 'multi-number':
      return multi('agNumberColumnFilter', deps.conditionParams, deps.setParams)
    case 'multi-date':
      return multi('agDateColumnFilter', deps.conditionParams, deps.setParams)
    case 'number':
      return { filter: 'agNumberColumnFilter', filterParams: deps.conditionParams }
    case 'date-time':
      return { filter: deps.dateTimeFilter, filterParams: deps.dateTimeParams }
    default:
      return {}
  }
}

function multi(
  conditionFilter: string,
  conditionParams: Record<string, unknown>,
  setParams: Record<string, unknown>,
): ColumnFilterConfig {
  return {
    filter: 'agMultiColumnFilter',
    filterParams: {
      filters: [
        { filter: conditionFilter, filterParams: conditionParams },
        { filter: 'agSetColumnFilter', filterParams: setParams },
      ],
    },
  }
}
```

In `src/api/endpoints/model-info.ts`: add `is_pk?: boolean` to `GeneralFieldInfo` (replacing the
commented `// is_pk: boolean`), and `filter_bar?: string[]` to `ListUiInfo`.

- [ ] **Step 4: Run it to see it pass**

Run: `./node_modules/.bin/vitest run src/components/model-components/list/filters`
Expected: 2 passed.

- [ ] **Step 5: Wire the grid (temporary empty value lists)**

In `CustomDatagrid.tsx`:
1. Add `MultiFilterModule` and `SetFilterModule` to the `ag-grid-enterprise` import and to
   `gridModules`.
2. Import `buildColumnFilter, columnFilterKind, hasValueList` from `./filters/columnFilters`.
3. Move the date comparator out of the colDef into a module-level constant, unchanged:

```ts
const dateFilterComparator = (filterLocalDateAtMidnight: any, cellValue: any) => {
  if (!cellValue) return -1
  const cellDate = new Date(cellValue)
  if (cellDate < filterLocalDateAtMidnight) return -1
  else if (cellDate > filterLocalDateAtMidnight) return 1
  return 0
}
const DATE_CONDITION_PARAMS = { ...FILTER_APPLY_PARAMS, comparator: dateFilterComparator }
```

4. Delete `getFilterForField`. In the column definition replace the `filter: getFilterForField(fieldInfo.type),`
   line and the whole `filterParams: ['date', 'date_time'].includes(...) ? … : undefined,` expression
   with a spread computed just before the colDef object:

```ts
          const filterKind = columnFilterKind(fieldInfo)
          const columnFilter = buildColumnFilter(filterKind, {
            conditionParams: filterKind === 'multi-date' ? DATE_CONDITION_PARAMS : FILTER_APPLY_PARAMS,
            setParams: hasValueList(filterKind) ? { ...FILTER_APPLY_PARAMS } : {},
            dateTimeFilter: AGDateTimeFilter,
            dateTimeParams: DATE_CONDITION_PARAMS,
          })
```

   and inside the colDef, where `filter:` was:

```ts
            ...columnFilter,
```

5. Remove the now-unused `AGForeignKeyFilter` and `AGBooleanFilter` imports. Keep both files: their
   tests (F3.51–F3.53) still document the boolean wire shape, and removing them is a separate change.
   Say so in the PR.

`setParams` is a placeholder object for one task only. Task 2 replaces it with the real value-list
params.

- [ ] **Step 6: Update the grid's own tests**

In `src/components/model-components/list/__test__/CustomDatagrid.test.tsx`:
- Delete the `vi.mock('../AGForeignKeyFilter', …)` and `vi.mock('../AGBooleanFilter', …)` blocks.
- Change the expectations to the new kinds. Each keeps its old intent (not the text filter; dates
  keep the date filter) with its scenario comment updated to F3.66:
  - `fk_field` → `agSetColumnFilter`;
  - `bool_field` → `agSetColumnFilter`;
  - `date_field` → `agMultiColumnFilter`;
  - `dt_field` → `AGDateTimeFilter` (unchanged).

Split the numeric `it.each`:

```ts
  it.each(['float', 'decimal', 'number'])('uses agNumberColumnFilter for %s fields', (numType) => {
    renderDatagrid({ data: [{ fields: [{ name: 'qty', type: numType, readable_name: 'Qty', required: false }], id_field: 'id' }] })
    const col = capturedBareProps.columnDefs.find((c: any) => c.field === 'qty')
    expect(col.filter).toBe('agNumberColumnFilter')
  })

  it.each(['int', 'integer'])('uses the number condition plus a value list for %s fields', (numType) => {
    renderDatagrid({ data: [{ fields: [{ name: 'qty', type: numType, readable_name: 'Qty', required: false }], id_field: 'id' }] })
    const col = capturedBareProps.columnDefs.find((c: any) => c.field === 'qty')
    expect(col.filter).toBe('agMultiColumnFilter')
    expect(col.filterParams.filters[0].filter).toBe('agNumberColumnFilter')
  })
```

- [ ] **Step 7: Run the grid tests and type-check**

Run: `./node_modules/.bin/vitest run src/components/model-components/list`
Then: `./node_modules/.bin/tsc --noEmit -p tsconfig.json | grep -c "error TS"`.
Expected: the filter tests pass. The failure set and tsc count equal the baseline taken before Step 5
(record the baseline first with the same two commands).

- [ ] **Step 8: Commit**

```bash
git add src/components/model-components/list/filters src/api/endpoints/model-info.ts src/components/model-components/list/CustomDatagrid.tsx src/components/model-components/list/__test__/CustomDatagrid.test.tsx
git commit -m "feat(grid): value-list filters per column type

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Value lists from `filter_values`

**Files:**
- Modify: `src/api/endpoints/model-entries.ts` (`filterValues` route)
- Create: `src/components/model-components/list/filters/filterValuesClient.ts`
- Create: `src/components/model-components/list/filters/setSelection.ts`
- Create: `src/components/model-components/list/filters/setFilterParams.ts`
- Modify: `CustomDatagrid.tsx` (a fetcher per resource/as_of/serializer; real `setParams`)
- Test: `src/components/model-components/list/filters/__test__/valueLists.cluster.test.ts` (new)

**Interfaces:**
- Consumes: `ColumnFilterKind` (Task 1).
- Produces:
  - `FilterValueItem = { key: string | null; label: string; count: number }`
  - `FilterValuesResult = { values: FilterValueItem[]; truncated: boolean }`
  - `FilterValuesRequest = { field; filterModel; selected; search?; limit? }`
  - `FetchFilterValues = (request) => Promise<FilterValuesResult | null>`. `null` means the
    backend has no endpoint.
  - `createFilterValuesFetcher({ httpClient, resource, query? }): FetchFilterValues`
  - `DEFAULT_VALUES_LIMIT = 200`, `COLUMN_MENU_VALUES_LIMIT = 1000`
  - `toKey(value): string | null`, `selectedKeysOf(model): Array<string | null>`,
    `withSelectedKeys(current, kind, keys): Record<string, unknown> | null`,
    `hasCondition(model): boolean`
  - `createSetFilterParams(field, fetchValues, applyParams): Record<string, unknown>`,
    `renderValueWithCount(items, value): HTMLElement`, `BLANK_LABEL = '(Blank)'`

- [ ] **Step 1: Write the failing tests**

Create `src/components/model-components/list/filters/__test__/valueLists.cluster.test.ts`:

```ts
/**
 * F3.68–F3.72 — value lists come from the server, narrowed by the other filters.
 *
 * Intent: a value list must show what the grid would — the column's values under
 * every OTHER active filter, with a count each and a readable label — and keep a
 * ticked value listed even when the other filters removed it, so it can be
 * unticked. Labels are text, never HTML. A backend without the endpoint must not
 * break the column: its list is just unavailable.
 *
 * Cluster F3 (grid_read), batch 3o, scenarios F3.68–F3.72. Tier U.
 */
import { describe, expect, it, vi } from 'vitest'
import { COLUMN_MENU_VALUES_LIMIT, createFilterValuesFetcher } from '../filterValuesClient'
import { createSetFilterParams, renderValueWithCount } from '../setFilterParams'
import { hasCondition, selectedKeysOf, toKey, withSelectedKeys } from '../setSelection'

describe('F3 — value lists (batch 3o)', () => {
  it('F3_68: the client posts the documented request and reads the answer', async () => {
    // Scenario F3.68
    // Given a fetcher for one resource, as of an instant, through a serializer
    // When a column's values are fetched
    // Then it POSTs field / filterModel / selected / limit to filter_values with
    // as_of and serializer on the URL, and returns keys, labels and counts
    const httpClient = vi.fn().mockResolvedValue({
      json: { values: [{ key: 'usd', label: 'US Dollar', count: 2 }, { key: null, label: '(Blank)', count: 1 }], truncated: true },
    })
    const fetchValues = createFilterValuesFetcher({ httpClient, resource: 'fund', query: { as_of: '2026-01-01T00:00:00.000Z', serializer: 'default' } })
    const result = await fetchValues({ field: 'currency', filterModel: { strategy: { filterType: 'set', values: ['equity'] } }, selected: ['usd'] })

    const [url, options] = httpClient.mock.calls[0]
    expect(url).toBe('api/model_entries/fund/filter_values?as_of=2026-01-01T00%3A00%3A00.000Z&serializer=default')
    expect(options.method).toBe('POST')
    expect(JSON.parse(options.body)).toEqual({
      field: 'currency',
      filterModel: { strategy: { filterType: 'set', values: ['equity'] } },
      selected: ['usd'],
      limit: 200,
    })
    expect(result).toEqual({ values: [{ key: 'usd', label: 'US Dollar', count: 2 }, { key: null, label: '(Blank)', count: 1 }], truncated: true })
  })

  it('F3_69: a backend without the endpoint makes lists unavailable, once', async () => {
    // Scenario F3.69
    // Given an older backend answering 404
    // When values are fetched twice
    // Then both answers are null and only the first reaches the network
    const httpClient = vi.fn().mockRejectedValue(Object.assign(new Error('Not Found'), { status: 404 }))
    const fetchValues = createFilterValuesFetcher({ httpClient, resource: 'fund' })
    expect(await fetchValues({ field: 'currency', filterModel: {}, selected: [] })).toBeNull()
    expect(await fetchValues({ field: 'currency', filterModel: {}, selected: [] })).toBeNull()
    expect(httpClient).toHaveBeenCalledTimes(1)
  })

  it('F3_70: the column menu list asks with the grid filters and keeps the server order', async () => {
    // Scenario F3.70
    // Given a Set filter's params for one column
    // When AG Grid asks for its values
    // Then the grid's whole filter model, the column's ticked keys and the
    // column-menu limit are sent, and the keys come back in the server's order
    const fetchValues = vi.fn().mockResolvedValue({ values: [{ key: 'b', label: 'Beta', count: 1 }, { key: 'a', label: 'Alpha', count: 3 }], truncated: false })
    const params: any = createSetFilterParams('strategy', fetchValues, { buttons: ['apply'] })
    const filterModel = { strategy: { filterType: 'set', values: ['a'] }, is_active: { filterType: 'set', values: ['true'] } }
    const success = vi.fn()

    params.values({ api: { getFilterModel: () => filterModel }, success })
    await vi.waitFor(() => expect(success).toHaveBeenCalled())

    expect(fetchValues).toHaveBeenCalledWith({ field: 'strategy', filterModel, selected: ['a'], limit: COLUMN_MENU_VALUES_LIMIT })
    expect(success).toHaveBeenCalledWith(['b', 'a'])
    expect(params.suppressSorting).toBe(true)
    expect(params.refreshValuesOnOpen).toBe(true)
    expect(params.buttons).toEqual(['apply'])
    expect(params.valueFormatter({ value: 'a' })).toBe('Alpha')
    expect(params.valueFormatter({ value: null })).toBe('(Blank)')
  })

  it('F3_71: a value shows its label and count, as text', () => {
    // Scenario F3.71
    // Given a label that looks like markup
    // When the list item renders
    // Then it is text, followed by the count
    const items = new Map([['x', { key: 'x', label: '<b>Bold</b>', count: 4 }]])
    const node = renderValueWithCount(items, 'x')
    expect(node.textContent).toBe('<b>Bold</b> 4')
    expect(node.querySelector('b')).toBeNull()
  })

  it('F3_72: selections read and write the Set half of a column filter', () => {
    // Scenario F3.72
    // Given Set and Multi column models
    // When their ticked keys are read and new ones written
    // Then keys are strings or null; a Multi keeps its condition; nothing ticked is no filter
    expect(toKey(true)).toBe('true')
    expect(toKey(5)).toBe('5')
    expect(toKey(undefined)).toBeNull()
    const condition = { filterType: 'text', type: 'contains', filter: 'a' }
    const multiModel = { filterType: 'multi', filterModels: [condition, { filterType: 'set', values: ['x', null] }] }
    expect(selectedKeysOf(multiModel)).toEqual(['x', null])
    expect(selectedKeysOf({ filterType: 'set', values: [false] })).toEqual(['false'])
    expect(withSelectedKeys(multiModel, 'multi-text', ['y'])).toEqual({ filterType: 'multi', filterModels: [condition, { filterType: 'set', values: ['y'] }] })
    expect(withSelectedKeys(multiModel, 'multi-text', [])).toEqual({ filterType: 'multi', filterModels: [condition, null] })
    expect(withSelectedKeys(null, 'multi-text', [])).toBeNull()
    expect(withSelectedKeys(null, 'set', [])).toBeNull()
    expect(withSelectedKeys(null, 'set', ['a'])).toEqual({ filterType: 'set', values: ['a'] })
    expect(hasCondition(multiModel)).toBe(true)
    expect(hasCondition({ filterType: 'multi', filterModels: [null, { filterType: 'set', values: ['a'] }] })).toBe(false)
  })
})
```

- [ ] **Step 2: Run them to see them fail**

Run: `./node_modules/.bin/vitest run src/components/model-components/list/filters/__test__/valueLists.cluster.test.ts`
Expected: module resolution failures.

- [ ] **Step 3: The route**

In `src/api/endpoints/model-entries.ts`, add beside `LIST`, `ONE`, `MANY`:

```ts
const FILTER_VALUES = 'filter_values'
```

and to the default export:

```ts
  filterValues: (model: string, query?: Record<string, unknown>) =>
    createRoute({ model, kind: FILTER_VALUES, queryParams: query }),
```

`createRoute` adds `?…` only when `queryParams` is truthy. F3.68 passes a query; F3.69 passes none,
and the request URL then has no `?`.

- [ ] **Step 4: The client**

`src/components/model-components/list/filters/filterValuesClient.ts`:

```ts
/**
 * Fetch a column's value list from `POST api/model_entries/<model>/filter_values`.
 *
 * `as_of` and `serializer` ride the URL, exactly as the list POST's do. A
 * backend without the endpoint (404 / 405) makes value lists unavailable for the
 * rest of the page's life, and the fetcher stops asking.
 */
import modelEntries from '../../../../api/endpoints/model-entries'
import type { HttpClient } from '../ServerSideDatasource'

export interface FilterValueItem {
  key: string | null
  label: string
  count: number
}

export interface FilterValuesResult {
  values: FilterValueItem[]
  truncated: boolean
}

export interface FilterValuesRequest {
  field: string
  filterModel: Record<string, unknown>
  selected: Array<string | null>
  search?: string
  limit?: number
}

export type FetchFilterValues = (request: FilterValuesRequest) => Promise<FilterValuesResult | null>

export const DEFAULT_VALUES_LIMIT = 200
export const COLUMN_MENU_VALUES_LIMIT = 1000

export function createFilterValuesFetcher(config: {
  httpClient: HttpClient
  resource: string
  query?: Record<string, unknown>
}): FetchFilterValues {
  let unavailable = false
  return async (request) => {
    if (unavailable) return null
    try {
      const { json } = await config.httpClient(modelEntries.filterValues(config.resource, config.query), {
        method: 'POST',
        body: JSON.stringify({ ...request, limit: request.limit ?? DEFAULT_VALUES_LIMIT }),
      })
      return normalizeFilterValues(json)
    } catch (error) {
      const status = (error as { status?: unknown } | null)?.status
      if (status === 404 || status === 405) {
        unavailable = true
        return null
      }
      throw error
    }
  }
}

export function normalizeFilterValues(json: unknown): FilterValuesResult {
  const raw = (json as { values?: unknown } | null)?.values
  const values: FilterValueItem[] = Array.isArray(raw)
    ? raw
        .filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
        .map((item) => ({
          key: item.key === null || item.key === undefined ? null : String(item.key),
          label: typeof item.label === 'string' ? item.label : String(item.key ?? '(Blank)'),
          count: typeof item.count === 'number' ? item.count : 0,
        }))
    : []
  return { values, truncated: Boolean((json as { truncated?: unknown } | null)?.truncated) }
}
```

- [ ] **Step 5: Selections**

`src/components/model-components/list/filters/setSelection.ts`:

```ts
/**
 * Reading and writing the value-list half of a column's filter model.
 *
 * A Set column's model is `{filterType: 'set', values}`. A Multi column's is
 * `{filterType: 'multi', filterModels: [condition, set]}`. The filter bar edits
 * only the Set half and leaves the condition alone. Nothing ticked is "no value
 * filter" here; the host's empty list ("no rows") is built in embedFilters.ts.
 */
import type { ColumnFilterKind } from './columnFilters'

type Node = { filterType?: string; values?: unknown; filterModels?: unknown } | null | undefined

export function toKey(value: unknown): string | null {
  if (value === null || value === undefined) return null
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return String(value)
}

function setHalf(model: unknown): { values?: unknown } | null {
  const node = model as Node
  if (!node || typeof node !== 'object') return null
  if (node.filterType === 'set') return node
  if (node.filterType === 'multi' && Array.isArray(node.filterModels)) {
    return (node.filterModels.find((child: any) => child && child.filterType === 'set') as { values?: unknown }) ?? null
  }
  return null
}

export function selectedKeysOf(model: unknown): Array<string | null> {
  const half = setHalf(model)
  return Array.isArray(half?.values) ? half!.values.map(toKey) : []
}

export function hasCondition(model: unknown): boolean {
  const node = model as Node
  return node?.filterType === 'multi' && Array.isArray(node.filterModels) && !!node.filterModels[0]
}

export function withSelectedKeys(
  current: unknown,
  kind: ColumnFilterKind,
  keys: Array<string | null>,
): Record<string, unknown> | null {
  const set = keys.length > 0 ? { filterType: 'set', values: keys } : null
  if (kind === 'set') return set
  const node = current as Node
  const condition =
    node?.filterType === 'multi' && Array.isArray(node.filterModels) ? (node.filterModels[0] ?? null) : null
  if (!condition && !set) return null
  return { filterType: 'multi', filterModels: [condition, set] }
}
```

- [ ] **Step 6: Set filter params**

`src/components/model-components/list/filters/setFilterParams.ts`:

```ts
/**
 * AG Grid Set filter params for one column, fed by filter_values.
 *
 * `refreshValuesOnOpen` re-asks every time the list opens, so it always
 * reflects the other filters as they are now. `suppressSorting` keeps the
 * server's order (labels ascending, blank last). Labels and counts are rendered
 * as text: a related record's `__str__` is user data, never markup.
 */
import { COLUMN_MENU_VALUES_LIMIT, type FetchFilterValues, type FilterValueItem } from './filterValuesClient'
import { selectedKeysOf, toKey } from './setSelection'

export const BLANK_LABEL = '(Blank)'

export function renderValueWithCount(items: Map<string | null, FilterValueItem>, value: unknown): HTMLElement {
  const key = toKey(value)
  const item = items.get(key)
  const span = document.createElement('span')
  span.textContent = item ? item.label : key === null ? BLANK_LABEL : key
  if (item) {
    const count = document.createElement('span')
    count.className = 'lex-filter-value-count'
    count.textContent = String(item.count)
    span.appendChild(document.createTextNode(' '))
    span.appendChild(count)
  }
  return span
}

export function createSetFilterParams(
  field: string,
  fetchValues: FetchFilterValues,
  applyParams: Record<string, unknown>,
): Record<string, unknown> {
  const items = new Map<string | null, FilterValueItem>()
  const labelOf = (value: unknown): string => {
    const key = toKey(value)
    return items.get(key)?.label ?? (key === null ? BLANK_LABEL : key)
  }

  return {
    ...applyParams,
    refreshValuesOnOpen: true,
    suppressSorting: true,
    values: (params: {
      api?: { getFilterModel?: () => Record<string, unknown> }
      success: (values: Array<string | null>) => void
    }) => {
      const filterModel = params.api?.getFilterModel?.() ?? {}
      fetchValues({
        field,
        filterModel,
        selected: selectedKeysOf(filterModel[field]),
        limit: COLUMN_MENU_VALUES_LIMIT,
      })
        .then((result) => {
          items.clear()
          for (const item of result?.values ?? []) items.set(item.key, item)
          params.success((result?.values ?? []).map((item) => item.key))
        })
        .catch((error: unknown) => {
          // AG's list has no place for a message; the bar shows the server's.
          console.warn(`[filters] values for ${field} could not be loaded`, error)
          params.success([])
        })
    },
    valueFormatter: (p: { value: unknown }) => labelOf(p.value),
    cellRenderer: (p: { value: unknown }) => renderValueWithCount(items, p.value),
  }
}
```

- [ ] **Step 7: Use them in the grid**

In `CustomDatagrid.tsx`:
1. Import `createFilterValuesFetcher` and `createSetFilterParams`.
2. Next to `serverSideDatasource`, add:

```ts
  const fetchFilterValues = useMemo(
    () =>
      createFilterValuesFetcher({
        httpClient,
        resource: activeResource,
        query: {
          ...(asOfIso ? { as_of: asOfIso } : {}),
          ...(serializer ? { serializer } : {}),
        },
      }),
    [activeResource, asOfIso, httpClient, serializer],
  )
```

3. In Task 1's column-filter block, replace the placeholder `setParams`:

```ts
            setParams: hasValueList(filterKind)
              ? createSetFilterParams(fieldInfo.name, fetchFilterValues, FILTER_APPLY_PARAMS)
              : {},
```

4. Add `fetchFilterValues` to the column-definitions `useMemo` dependency list.

- [ ] **Step 8: Run tests and type-check**

Run: `./node_modules/.bin/vitest run src/components/model-components/list`, then tsc.
Expected: F3.68–F3.72 pass. The failure set and tsc count equal the Task 1 baseline.

- [ ] **Step 9: Commit**

```bash
git add src/api/endpoints/model-entries.ts src/components/model-components/list/filters src/components/model-components/list/CustomDatagrid.tsx
git commit -m "feat(grid): value lists come from filter_values, narrowed by the other filters

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Saved views keep working

**Files:**
- Create: `src/utils/filterModelMigration.ts`
- Modify: `src/components/model-components/list/CustomList.tsx` (`restoreView` migrates before
  `setFilterModel`; a `fieldsRef`)
- Test: `src/utils/__test__/filterModelMigration.cluster.test.ts` (new)

**Interfaces:**
- Consumes: `columnFilterKind` (Task 1), `toKey` (Task 2).
- Produces: `migrateFilterModel(filterModel: Record<string, unknown>, fields: FieldInfo[]): Record<string, unknown>`.

- [ ] **Step 1: Write the failing tests**

```ts
/**
 * F4.41–F4.43 — a saved view written before value lists still restores.
 *
 * Intent: saved views keep filter models in local storage, in the shape of the
 * filter each column had then. A foreign key's single value, the boolean
 * filter's old shapes, and plain text, number and date conditions must come back
 * as the same filter on the column's new value-list or Multi filter — or, when a
 * shape cannot be expressed, be dropped with a warning rather than break the grid.
 *
 * Cluster F4 (grid_views), batch 4e, scenarios F4.41–F4.43. Tier U.
 */
import { describe, expect, it, vi } from 'vitest'
import { migrateFilterModel } from '../filterModelMigration'

const fields: any[] = [
  { name: 'currency', type: 'foreign_key', readable_name: 'Currency' },
  { name: 'is_active', type: 'boolean', readable_name: 'Active' },
  { name: 'strategy', type: 'string', readable_name: 'Strategy', choices: [['equity', 'Equity']] },
  { name: 'name', type: 'string', readable_name: 'Name' },
  { name: 'inception_date', type: 'date', readable_name: 'Inception' },
  { name: 'nav', type: 'float', readable_name: 'NAV' },
]

describe('F4 — saved views migrate to value lists (batch 4e)', () => {
  it('F4_41: old single-value filters become value lists', () => {
    // Scenario F4.41
    // Given a view saved with a foreign key's "equals" and both boolean shapes
    // When it is restored
    // Then each is a Set filter with the same value as its key
    expect(migrateFilterModel({ currency: { filterType: 'text', type: 'equals', filter: 3 } }, fields))
      .toEqual({ currency: { filterType: 'set', values: ['3'] } })
    expect(migrateFilterModel({ is_active: { filterType: 'set', values: [false] } }, fields))
      .toEqual({ is_active: { filterType: 'set', values: ['false'] } })
    expect(migrateFilterModel({ is_active: { filterType: 'boolean', filter: true } }, fields))
      .toEqual({ is_active: { filterType: 'set', values: ['true'] } })
  })

  it('F4_42: conditions on Multi columns become the condition half', () => {
    // Scenario F4.42
    // Given a view saved with text and date conditions, and a combined text condition
    // When it is restored
    // Then each sits in the Multi filter's first half; numbers on number columns are untouched
    const text = { filterType: 'text', type: 'contains', filter: 'alp' }
    const combined = { filterType: 'text', operator: 'OR', conditions: [text, { ...text, filter: 'bet' }] }
    const date = { filterType: 'date', type: 'greaterThan', dateFrom: '2020-01-01' }
    const nav = { filterType: 'number', type: 'greaterThan', filter: 1 }
    expect(migrateFilterModel({ name: text, inception_date: date, nav }, fields)).toEqual({
      name: { filterType: 'multi', filterModels: [text, null] },
      inception_date: { filterType: 'multi', filterModels: [date, null] },
      nav,
    })
    expect(migrateFilterModel({ name: combined }, fields)).toEqual({ name: { filterType: 'multi', filterModels: [combined, null] } })
  })

  it('F4_43: a shape a value list cannot hold is dropped with a warning', () => {
    // Scenario F4.43
    // Given a choice column saved with a "contains" text filter
    // When it is restored
    // Then the filter is dropped, the rest is kept, and a warning names it
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    expect(migrateFilterModel({
      strategy: { filterType: 'text', type: 'contains', filter: 'eq' },
      name: { filterType: 'multi', filterModels: [null, { filterType: 'set', values: ['x'] }] },
    }, fields)).toEqual({ name: { filterType: 'multi', filterModels: [null, { filterType: 'set', values: ['x'] }] } })
    expect(warn).toHaveBeenCalledTimes(1)
    warn.mockRestore()
  })
})
```

Save as `src/utils/__test__/filterModelMigration.cluster.test.ts`. Run it to see the module
resolution failure.

- [ ] **Step 2: Implement**

`src/utils/filterModelMigration.ts`:

```ts
/**
 * Bring a saved view's filter model up to the column filters the grid has now.
 *
 * Saved views store AG Grid filter models in local storage. Before value lists,
 * foreign keys used a single-value `text equals`, booleans a `set` of real
 * booleans or `{filterType: 'boolean'}`, and text, numbers and dates plain
 * conditions. Each is rewritten for its column's current filter; a shape that
 * cannot be expressed is dropped with a warning, never applied half-understood.
 */
import type { FieldInfo } from '../api/endpoints/model-info'
import { columnFilterKind, type ColumnFilterKind } from '../components/model-components/list/filters/columnFilters'
import { toKey } from '../components/model-components/list/filters/setSelection'

const CONDITION_TYPES = new Set(['text', 'number', 'date'])

export function migrateFilterModel(
  filterModel: Record<string, unknown>,
  fields: FieldInfo[],
): Record<string, unknown> {
  const kinds = new Map(fields.map((field) => [field.name, columnFilterKind(field)]))
  const migrated: Record<string, unknown> = {}
  for (const [field, model] of Object.entries(filterModel ?? {})) {
    const kind = kinds.get(field)
    const next = kind ? migrateColumn(field, model, kind) : model
    if (next !== null && next !== undefined) migrated[field] = next
  }
  return migrated
}

function migrateColumn(field: string, model: unknown, kind: ColumnFilterKind): unknown {
  if (!model || typeof model !== 'object') return model
  const node = model as Record<string, any>

  if (kind === 'set') {
    if (node.filterType === 'set') {
      return { ...node, values: Array.isArray(node.values) ? node.values.map(toKey) : [] }
    }
    const single = node.filterType === 'boolean' || (node.filterType === 'text' && node.type === 'equals')
    if (single && node.filter !== undefined && node.filter !== null && node.filter !== '') {
      return { filterType: 'set', values: [toKey(node.filter)] }
    }
    console.warn(`[filters] a saved filter on "${field}" cannot be shown as a value list and was dropped`, node)
    return null
  }

  if (kind.startsWith('multi-')) {
    if (node.filterType === 'multi') return node
    if (node.filterType === 'set') {
      return {
        filterType: 'multi',
        filterModels: [null, { ...node, values: Array.isArray(node.values) ? node.values.map(toKey) : [] }],
      }
    }
    if (CONDITION_TYPES.has(node.filterType) || Array.isArray(node.conditions)) {
      return { filterType: 'multi', filterModels: [node, null] }
    }
  }
  return node
}
```

- [ ] **Step 3: Use it in `restoreView`**

In `CustomList.tsx`:
1. Import `migrateFilterModel`.
2. Next to `gridViewStateRef`, add:

```ts
  const fieldsRef = useRef<FieldInfo[]>([])
  fieldsRef.current = (modelFieldInfo.data?.fields ?? []) as FieldInfo[]
```

   (import `type FieldInfo` from `../../../api/endpoints/model-info`).

3. In `restoreView`, step "3. Filters", change:

```ts
      const persistedFilters = sanitizeFilterModelForViewPersistence(viewSettings?.filters ?? null)
```

   to:

```ts
      const persistedFilters = migrateFilterModel(
        sanitizeFilterModelForViewPersistence(viewSettings?.filters ?? null),
        fieldsRef.current,
      )
```

- [ ] **Step 4: Run and commit**

Run: `./node_modules/.bin/vitest run src/utils/__test__/filterModelMigration.cluster.test.ts src/components/model-components/list/__test__/CustomList.test.tsx`
Expected: F4.41–F4.43 pass; CustomList failures equal the baseline.

```bash
git add src/utils/filterModelMigration.ts src/utils/__test__/filterModelMigration.cluster.test.ts src/components/model-components/list/CustomList.tsx
git commit -m "feat(views): saved filters migrate to value lists

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The filter bar

**Files:**
- Create: `src/components/model-components/list/filters/filterBarColumns.ts`
- Create: `src/components/model-components/list/filters/FilterBar.tsx`
- Create: `src/components/model-components/list/viewSettings.ts`
- Modify: `CustomList.tsx` (grid API state, live filter model, bar columns, bar rendering, view
  capture)
- Modify: `CustomDatagrid.tsx` (`getMainMenuItems` with "Show in filter bar"; props
  `filterBarColumns`, `onShowInFilterBar`)
- Test: `src/components/model-components/list/filters/__test__/FilterBar.cluster.test.tsx` (new)
- Test: `src/components/model-components/list/__test__/viewSettings.cluster.test.ts` (new)

**Interfaces:**
- Consumes: `columnFilterKind`, `hasValueList` (Task 1); `FetchFilterValues`,
  `DEFAULT_VALUES_LIMIT`, `selectedKeysOf`, `withSelectedKeys`, `hasCondition` (Task 2).
- Produces:
  - `resolveFilterBarColumns({ embedParam: string | null, savedView: unknown, listUi: unknown, valueListFields: Set<string> }): string[]`
  - `<FilterBar columns fields filterModel fetchValues onChange onRemoveColumn />`, where
    `onChange(field: string, model: Record<string, unknown> | null)`
  - `captureViewSettings(api, context): ViewSettings`, including `filterBar`

- [ ] **Step 1: Write the failing tests**

`src/components/model-components/list/filters/__test__/FilterBar.cluster.test.tsx`:

```tsx
/**
 * F3.73–F3.77 — the filter bar is a second view of the same filters.
 *
 * Intent: the bar shows chosen columns' value lists above the grid. Its columns
 * come from the embed, else the saved view, else the list's default. Ticking a
 * value writes the column's Set filter (keeping a Multi column's condition), so
 * the column header, saved views and export see the same state; opening a list
 * asks with the grid's other filters, so it narrows. A column outside the bar is
 * one menu click away. A regression makes the bar and the grid disagree.
 *
 * Cluster F3 (grid_read), batch 3o, scenarios F3.73–F3.77. Tier C.
 */
import React from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import FilterBar from '../FilterBar'
import { resolveFilterBarColumns } from '../filterBarColumns'

const fields: any[] = [
  { name: 'strategy', type: 'string', readable_name: 'Strategy', choices: [['equity', 'Equity']] },
  { name: 'name', type: 'string', readable_name: 'Name' },
]

const values = {
  strategy: { values: [{ key: 'equity', label: 'Equity', count: 2 }, { key: 'fixed_income', label: 'Fixed Income', count: 2 }], truncated: false },
  name: { values: [{ key: 'Alpha', label: 'Alpha', count: 1 }], truncated: true },
}

const renderBar = (props: Record<string, unknown> = {}) => {
  const fetchValues = vi.fn(async (req: any) => (values as any)[req.field])
  const onChange = vi.fn()
  const onRemoveColumn = vi.fn()
  render(
    <FilterBar
      columns={['strategy', 'name']}
      fields={fields}
      filterModel={{}}
      fetchValues={fetchValues}
      onChange={onChange}
      onRemoveColumn={onRemoveColumn}
      {...props}
    />,
  )
  return { fetchValues, onChange, onRemoveColumn }
}

describe('F3 — the filter bar (batch 3o)', () => {
  it('F3_73: the bar columns come from the embed, the view, then the list', () => {
    // Scenario F3.73
    // Given an embed parameter, a saved view and a list default
    // When the bar's columns are resolved
    // Then the first present source wins, an empty embed parameter hides the bar,
    // and names that are not value-list columns are dropped
    const valueListFields = new Set(['strategy', 'currency'])
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    expect(resolveFilterBarColumns({ embedParam: 'currency,strategy', savedView: ['strategy'], listUi: ['strategy'], valueListFields })).toEqual(['currency', 'strategy'])
    expect(resolveFilterBarColumns({ embedParam: '', savedView: ['strategy'], listUi: ['strategy'], valueListFields })).toEqual([])
    expect(resolveFilterBarColumns({ embedParam: null, savedView: ['currency'], listUi: ['strategy'], valueListFields })).toEqual(['currency'])
    expect(resolveFilterBarColumns({ embedParam: null, savedView: undefined, listUi: ['strategy', 'nav', 'strategy'], valueListFields })).toEqual(['strategy'])
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('nav'))
    warn.mockRestore()
  })

  it('F3_74: one control per column, ticked values shown by label', async () => {
    // Scenario F3.74
    // Given a bar over strategy and name, with "equity" ticked
    // When it renders
    // Then both controls show, and the chip reads "Equity", not the key
    renderBar({ filterModel: { strategy: { filterType: 'set', values: ['equity'] } } })
    expect(screen.getByLabelText('Strategy')).toBeInTheDocument()
    expect(screen.getByLabelText('Name')).toBeInTheDocument()
    expect(await screen.findByText('Equity')).toBeInTheDocument()
  })

  it('F3_75: ticking writes the column filter, keeping a condition', async () => {
    // Scenario F3.75
    // Given a Multi column with a text condition
    // When a value is ticked in the bar
    // Then onChange gets the Multi model with the condition kept and the value as its Set half
    const condition = { filterType: 'text', type: 'contains', filter: 'a' }
    const { onChange } = renderBar({ filterModel: { name: { filterType: 'multi', filterModels: [condition, null] } } })
    fireEvent.mouseDown(screen.getByLabelText('Name'))
    fireEvent.click(await screen.findByRole('option', { name: /Alpha/ }))
    expect(onChange).toHaveBeenCalledWith('name', { filterType: 'multi', filterModels: [condition, { filterType: 'set', values: ['Alpha'] }] })
  })

  it('F3_76: opening a list asks with the other filters and shows counts', async () => {
    // Scenario F3.76
    // Given a filter on another column
    // When the strategy list opens
    // Then the request carries the whole filter model, options show their counts,
    // and a cut list tells the user to search
    const filterModel = { is_active: { filterType: 'set', values: ['true'] } }
    const { fetchValues } = renderBar({ filterModel })
    fireEvent.mouseDown(screen.getByLabelText('Strategy'))
    const option = await screen.findByRole('option', { name: /Fixed Income/ })
    expect(within(option).getByText('2')).toBeInTheDocument()
    await waitFor(() => expect(fetchValues).toHaveBeenCalledWith(expect.objectContaining({ field: 'strategy', filterModel })))
    fireEvent.mouseDown(screen.getByLabelText('Name'))
    expect(await screen.findByText(/type to search/i)).toBeInTheDocument()
  })

  it('F3_76b: a refused list shows the server message', async () => {
    // Scenario F3.76 (second half)
    // Given a server that refuses the list
    // When the dropdown opens
    // Then its message is shown under the control
    const fetchValues = vi.fn().mockRejectedValue(new Error("'region' is not filterable."))
    render(
      <FilterBar columns={['strategy']} fields={fields} filterModel={{}} fetchValues={fetchValues} onChange={vi.fn()} onRemoveColumn={vi.fn()} />,
    )
    fireEvent.mouseDown(screen.getByLabelText('Strategy'))
    expect(await screen.findByText("'region' is not filterable.")).toBeInTheDocument()
  })

  it('F3_77: a column leaves the bar without losing its filter', () => {
    // Scenario F3.77
    // Given a bar column
    // When its remove button is pressed
    // Then onRemoveColumn is called and onChange is not
    const { onChange, onRemoveColumn } = renderBar()
    fireEvent.click(screen.getByRole('button', { name: 'Remove Strategy from the filter bar' }))
    expect(onRemoveColumn).toHaveBeenCalledWith('strategy')
    expect(onChange).not.toHaveBeenCalled()
  })
})
```

`src/components/model-components/list/__test__/viewSettings.cluster.test.ts`:

```ts
/**
 * F4.44 — a saved view remembers its filter bar.
 *
 * Intent: the bar's columns are part of a view, like its columns and filters.
 * Saving and updating a view must capture them; one function captures every
 * part of a view, so the two save paths cannot drift apart.
 *
 * Cluster F4 (grid_views), batch 4e, scenario F4.44. Tier U.
 */
import { describe, expect, it } from 'vitest'
import { captureViewSettings } from '../viewSettings'

describe('F4 — view settings (batch 4e)', () => {
  it('F4_44: the captured view carries the bar columns with the rest', () => {
    // Scenario F4.44
    // Given a grid with a filter, a column state and two bar columns
    // When its view is captured
    // Then filters, columns and filterBar are all present
    const api = {
      getColumnState: () => [{ colId: 'name', width: 120 }],
      getFilterModel: () => ({ strategy: { filterType: 'set', values: ['equity'] } }),
      isPivotMode: () => false,
    }
    const settings = captureViewSettings(api, {
      rowHeight: 25,
      serializer: 'default',
      displaySettings: undefined,
      columnFormatOverrides: undefined,
      filterBar: ['strategy', 'currency'],
      capturePivotAggregationState: () => ({ rowGroupColumns: [], pivotColumns: [], valueColumns: [] }),
    })
    expect(settings.filterBar).toEqual(['strategy', 'currency'])
    expect(settings.filters).toEqual({ strategy: { filterType: 'set', values: ['equity'] } })
    expect(settings.columns).toEqual([{ colId: 'name', width: 120 }])
  })
})
```

Run both to see them fail.

- [ ] **Step 2: Bar columns**

`src/components/model-components/list/filters/filterBarColumns.ts`:

```ts
/**
 * Which columns the filter bar shows: the embed's `filter_bar` parameter if the
 * host sent one (an empty value hides the bar), else the active saved view's
 * choice, else the list's default from the backend. Names that are not
 * value-list columns of this list are dropped with a warning.
 */
export function resolveFilterBarColumns(input: {
  embedParam: string | null
  savedView: unknown
  listUi: unknown
  valueListFields: Set<string>
}): string[] {
  const raw: unknown[] =
    input.embedParam !== null
      ? input.embedParam.split(',').map((name) => name.trim()).filter(Boolean)
      : Array.isArray(input.savedView)
        ? input.savedView
        : Array.isArray(input.listUi)
          ? input.listUi
          : []
  const columns: string[] = []
  for (const name of raw) {
    if (typeof name !== 'string' || !input.valueListFields.has(name)) {
      console.warn(`[filters] filter bar: ${String(name)} is not a value-list column here; dropped`)
      continue
    }
    if (!columns.includes(name)) columns.push(name)
  }
  return columns
}
```

- [ ] **Step 3: The bar component**

`src/components/model-components/list/filters/FilterBar.tsx`:

```tsx
/**
 * The filter bar: chosen columns' value lists as dropdowns above the grid.
 *
 * It holds no filter state of its own. It reads the grid's filter model and
 * writes a column's Set half through `onChange`, so the header, saved views,
 * export and "Select All Records" always see what the bar shows. Lists are
 * fetched when opened and while searching, with the grid's whole filter model,
 * so they narrow under the other filters.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Autocomplete, Box, Chip, IconButton, TextField, Tooltip, Typography } from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import type { FieldInfo } from '../../../../api/endpoints/model-info'
import { columnFilterKind, type ColumnFilterKind } from './columnFilters'
import { DEFAULT_VALUES_LIMIT, type FetchFilterValues, type FilterValueItem } from './filterValuesClient'
import { BLANK_LABEL } from './setFilterParams'
import { hasCondition, selectedKeysOf, withSelectedKeys } from './setSelection'

const SEARCH_DEBOUNCE_MS = 250

export interface FilterBarProps {
  columns: string[]
  fields: FieldInfo[]
  filterModel: Record<string, unknown>
  fetchValues: FetchFilterValues
  onChange: (field: string, model: Record<string, unknown> | null) => void
  onRemoveColumn: (field: string) => void
}

export default function FilterBar(props: FilterBarProps): JSX.Element | null {
  const byName = useMemo(() => new Map(props.fields.map((f) => [f.name, f])), [props.fields])
  if (props.columns.length === 0) return null
  return (
    <Box
      data-testid='filter-bar'
      sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, px: 1, py: 0.75, borderBottom: 1, borderColor: 'divider' }}
    >
      {props.columns.map((field) => {
        const info = byName.get(field)
        if (!info) return null
        return (
          <FilterBarItem
            key={field}
            field={field}
            label={info.readable_name || field}
            kind={columnFilterKind(info)}
            model={props.filterModel[field]}
            filterModel={props.filterModel}
            fetchValues={props.fetchValues}
            onChange={props.onChange}
            onRemove={props.onRemoveColumn}
          />
        )
      })}
    </Box>
  )
}

interface FilterBarItemProps {
  field: string
  label: string
  kind: ColumnFilterKind
  model: unknown
  filterModel: Record<string, unknown>
  fetchValues: FetchFilterValues
  onChange: FilterBarProps['onChange']
  onRemove: (field: string) => void
}

function FilterBarItem(props: FilterBarItemProps): JSX.Element {
  const { field, label, kind, model, filterModel, fetchValues, onChange, onRemove } = props
  const selected = selectedKeysOf(model)
  const selectedSignature = JSON.stringify(selected)
  const filterSignature = JSON.stringify(filterModel)
  const labels = useRef(new Map<string | null, string>())
  const [options, setOptions] = useState<FilterValueItem[]>([])
  const [truncated, setTruncated] = useState(false)
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [, rerender] = useState(0)

  const load = async (term: string): Promise<void> => {
    let result
    try {
      result = await fetchValues({
        field,
        filterModel,
        selected,
        search: term || undefined,
        limit: DEFAULT_VALUES_LIMIT,
      })
    } catch (failure) {
      setError((failure as { message?: string } | null)?.message || 'Values could not be loaded')
      return
    }
    setError(null)
    if (!result) return
    for (const item of result.values) labels.current.set(item.key, item.label)
    setOptions(result.values)
    setTruncated(result.truncated)
    rerender((n) => n + 1)
  }

  // Resolve chip labels for values ticked elsewhere (header, saved view, host).
  useEffect(() => {
    if (selected.length > 0 && selected.some((key) => !labels.current.has(key))) void load('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [field, selectedSignature])

  // Fetch while open: on open, on search, and when the other filters change.
  useEffect(() => {
    if (!open) return
    const timer = setTimeout(() => void load(search), search ? SEARCH_DEBOUNCE_MS : 0)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, search, filterSignature])

  const labelOf = (key: string | null): string => labels.current.get(key) ?? (key === null ? BLANK_LABEL : key)
  const value: FilterValueItem[] = selected.map((key) => ({ key, label: labelOf(key), count: 0 }))

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25 }}>
      <Autocomplete<FilterValueItem, true, false, false>
        multiple
        size='small'
        sx={{ minWidth: 200, maxWidth: 360 }}
        open={open}
        onOpen={() => setOpen(true)}
        onClose={() => {
          setOpen(false)
          setSearch('')
        }}
        options={options}
        value={value}
        filterOptions={(items) => items}
        isOptionEqualToValue={(a, b) => a.key === b.key}
        getOptionLabel={(item) => item.label}
        onInputChange={(_, next, reason) => {
          if (reason === 'input') setSearch(next)
        }}
        onChange={(_, items) => onChange(field, withSelectedKeys(model, kind, items.map((item) => item.key)))}
        renderOption={(optionProps, item) => (
          <li {...optionProps} key={String(item.key)}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', width: '100%', gap: 2 }}>
              <span>{item.label}</span>
              <Typography component='span' variant='caption' color='text.secondary'>
                {item.count}
              </Typography>
            </Box>
          </li>
        )}
        renderTags={(items, getTagProps) =>
          items.map((item, index) => (
            <Chip {...getTagProps({ index })} key={String(item.key)} size='small' label={item.label} />
          ))
        }
        renderInput={(params) => (
          <TextField
            {...params}
            label={label}
            placeholder={selected.length === 0 ? 'All' : undefined}
            error={Boolean(error)}
            helperText={
              error ??
              (open && truncated
                ? 'More values — type to search'
                : hasCondition(model)
                  ? '+ condition in the column filter'
                  : undefined)
            }
          />
        )}
      />
      <Tooltip title={`Remove ${label} from the filter bar`}>
        <IconButton size='small' aria-label={`Remove ${label} from the filter bar`} onClick={() => onRemove(field)}>
          <CloseIcon fontSize='small' />
        </IconButton>
      </Tooltip>
    </Box>
  )
}
```

- [ ] **Step 4: View capture**

`src/components/model-components/list/viewSettings.ts`:

```ts
/**
 * Everything a saved view stores, captured from the grid in one place.
 *
 * Creating a view and updating one used to build this object twice, by hand.
 * The filter bar's columns are part of a view now, and one capture function
 * keeps the two paths from drifting.
 */
import { sanitizeFilterModelForViewPersistence } from '../../../utils/agGridFilterStatePersistence'
import { sanitizeColumnStateForPersistence } from '../../../utils/agGridColumnStatePersistence'

export interface ViewCaptureContext {
  rowHeight: unknown
  serializer: unknown
  displaySettings: unknown
  columnFormatOverrides: unknown
  filterBar: string[]
  capturePivotAggregationState: (api: any) => unknown
}

export function captureViewSettings(api: any, context: ViewCaptureContext) {
  const legacyColumnApi = api?.columnApi ?? api?.getColumnApi?.()
  const pivotMode =
    typeof api?.isPivotMode === 'function'
      ? api.isPivotMode()
      : typeof legacyColumnApi?.isPivotMode === 'function'
        ? legacyColumnApi.isPivotMode()
        : Boolean(api?.getGridOption?.('pivotMode'))
  return {
    columns: sanitizeColumnStateForPersistence(api.getColumnState()),
    filters: sanitizeFilterModelForViewPersistence(api.getFilterModel()),
    rowHeight: context.rowHeight,
    pivotMode,
    pivotAggregationState: context.capturePivotAggregationState(api),
    serializer: context.serializer,
    displaySettings: context.displaySettings,
    columnFormatOverrides: context.columnFormatOverrides,
    filterBar: context.filterBar,
  }
}
```

`sanitizeColumnStateForPersistence` is exported by `src/utils/agGridColumnStatePersistence.ts`,
which is where `CustomList.tsx` already imports it from.

- [ ] **Step 5: Wire the list**

In `CustomList.tsx`:

1. **State and fetcher**

```ts
  const httpClient = useRaHttpClient()
  const [gridApi, setGridApi] = useState<any>(null)
  const gridApiRef = useRef<any>(null)
  gridApiRef.current = gridApi
  const [liveFilterModel, setLiveFilterModel] = useState<Record<string, unknown>>({})
  const [filterBarColumns, setFilterBarColumns] = useState<string[]>([])

  const valueListFields = useMemo(
    () => new Set(fieldsRef.current.filter((f) => hasValueList(columnFilterKind(f))).map((f) => f.name)),
    [modelFieldInfo.data],
  )

  const fetchBarValues = useMemo(
    () =>
      createFilterValuesFetcher({
        httpClient,
        resource: activeResource || '',
        query: {
          ...(asOfIso ? { as_of: asOfIso } : {}),
          ...(serializerToUse ? { serializer: serializerToUse } : {}),
        },
      }),
    [activeResource, asOfIso, httpClient, serializerToUse],
  )
```

   `asOfIso` and `serializerToUse` are the names `CustomList` already passes to `<List>` and
   `<CustomDatagrid>`. Import `useRaHttpClient` from `../../../api/httpClients/raHttpClient`, as
   `CustomDatagrid.tsx` does, and `createFilterValuesFetcher`, `resolveFilterBarColumns`,
   `columnFilterKind`, `hasValueList`, `FilterBar` and `captureViewSettings` from their new modules.

2. **Track the grid API and its filters**

   - In `onBeforeServerDatasourceInit`, after `restoreView(undefined, api)`, add `setGridApi(api)`.
   - In `onFirstDataRendered`'s non-SSRM branch, add `setGridApi(event?.api)`.
   - Then:

```ts
  useEffect(() => {
    if (!gridApi) return
    const sync = (): void => setLiveFilterModel({ ...(gridApi.getFilterModel?.() ?? {}) })
    sync()
    gridApi.addEventListener?.('filterChanged', sync)
    return () => {
      try {
        gridApi.removeEventListener?.('filterChanged', sync)
      } catch {
        /* a destroyed grid has no listeners left */
      }
    }
  }, [gridApi])
```

3. **Resolve the bar's columns**

```ts
  const activeViewFilterBar = gridViewState?.[activeActiveView]?.filterBar
  useEffect(() => {
    setFilterBarColumns(
      resolveFilterBarColumns({
        embedParam: getEmbedParam('filter_bar'),
        savedView: activeViewFilterBar,
        listUi: modelFieldInfo.data?.list_ui?.filter_bar,
        valueListFields,
      }),
    )
  }, [activeActiveView, activeViewFilterBar, modelFieldInfo.data, valueListFields])

  const showInFilterBar = useCallback((field: string) => {
    setFilterBarColumns((columns) => (columns.includes(field) ? columns : [...columns, field]))
    setIsViewModified(true)
  }, [])
  const removeFromFilterBar = useCallback((field: string) => {
    setFilterBarColumns((columns) => columns.filter((name) => name !== field))
    setIsViewModified(true)
  }, [])
  const writeColumnFilter = useCallback((field: string, model: Record<string, unknown> | null) => {
    const api = gridApiRef.current
    if (!api) return
    Promise.resolve(api.setColumnFilterModel(field, model)).then(() => api.onFilterChanged())
  }, [])
```

4. **Render the bar** between the toolbar `Box` and the grid, inside the same column `Box`, when not
   showing the analytics panel:

```tsx
                  {!showAnalyticsPanel && (
                    <FilterBar
                      columns={filterBarColumns}
                      fields={fieldsRef.current}
                      filterModel={liveFilterModel}
                      fetchValues={fetchBarValues}
                      onChange={writeColumnFilter}
                      onRemoveColumn={removeFromFilterBar}
                    />
                  )}
```

5. **Pass the menu wiring** to `<CustomDatagrid>`:
   `filterBarColumns={filterBarColumns} onShowInFilterBar={showInFilterBar}`.

6. **Use `captureViewSettings` when saving.**
   - In `handleUpdateView` and `handleCreateView`, replace each hand-built view object with:

```ts
        captureViewSettings(api, {
          rowHeight: currentRowHeight,
          serializer: serializerToUse,
          displaySettings,
          columnFormatOverrides,
          filterBar: filterBarColumns,
          capturePivotAggregationState,
        })
```

   - Delete the local `currentColumns` / `currentFilters` / `currentPivotAggregationState` /
     `legacyColumnApi` / `currentPivotMode` variables those handlers no longer use.

- [ ] **Step 6: The column menu item**

In `CustomDatagrid.tsx`:
- Add `filterBarColumns?: string[]` and `onShowInFilterBar?: (field: string) => void` to
  `CustomDatagridProps`, and destructure them.
- Then add:

```ts
  const getMainMenuItems = useCallback(
    (params: any) => {
      const items = [...(params?.defaultItems ?? [])]
      const field = params?.column?.getColDef?.()?.field
      const fieldInfo = fields.find((f: any) => f.name === field)
      if (
        !onShowInFilterBar ||
        !fieldInfo ||
        !hasValueList(columnFilterKind(fieldInfo)) ||
        filterBarColumns?.includes(field)
      ) {
        return items
      }
      return [...items, 'separator', { name: 'Show in filter bar', action: () => onShowInFilterBar(field) }]
    },
    [fields, filterBarColumns, onShowInFilterBar],
  )
```

and pass `getMainMenuItems={getMainMenuItems}` to `<BareDatagridAGClient>`, beside
`getContextMenuItems`. Add to `CustomDatagrid.test.tsx`:

```ts
  it('F3_78: the column menu offers "Show in filter bar" for value-list columns only', () => {
    // Scenario F3.78
    const onShowInFilterBar = vi.fn()
    renderDatagrid({ onShowInFilterBar, filterBarColumns: [] })
    const menu = (field: string) =>
      capturedBareProps.getMainMenuItems({ defaultItems: ['columnFilter'], column: { getColDef: () => ({ field }) } })
    const fkItems = menu('fk_field')
    expect(fkItems.at(-1).name).toBe('Show in filter bar')
    fkItems.at(-1).action()
    expect(onShowInFilterBar).toHaveBeenCalledWith('fk_field')
    expect(menu('dt_field')).toEqual(['columnFilter'])
  })
```

- [ ] **Step 7: Run tests and type-check**

Run: `./node_modules/.bin/vitest run src/components/model-components/list`, then tsc.
Expected: F3.73–F3.78 and F4.44 pass. The baseline is unchanged.

- [ ] **Step 8: Commit**

```bash
git add src/components/model-components/list src/utils
git commit -m "feat(grid): a filter bar over the grid's own filters

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Embeds — host filters in, filter events out

**Files:**
- Modify: `src/utils/lexAppBridge.ts` (event type, flag, snapshot params)
- Create: `src/utils/embedFilters.ts`
- Modify: `CustomList.tsx` (first-load filters in `onBeforeServerDatasourceInit`; host message
  listener)
- Modify: `CustomDatagrid.tsx` (emit `filter` after a filtered load)
- Test: `src/utils/__test__/embedFilters.cluster.test.ts` (new)
- Test: `src/utils/__test__/lexAppBridge.test.ts` (one case for the new flag)

**Interfaces:**
- Consumes: `columnFilterKind`, `hasValueList`, `toKey`, `selectedKeysOf`.
- Produces:
  - `decodeFiltersToken(token: string | null): HostFilters | null`
  - `applyHostFilters(current, hostFilters, fields): Record<string, unknown>`
  - `filtersForEvent(filterModel): Record<string, Array<string | null>>`
  - `takeInitialEmbedFilters(resource: string): HostFilters | null`, consumed once
  - `onHostFilters(handler): () => void`
  - event `filter` with payload `{ resource, filters, filter_model, row_count }`

- [ ] **Step 1: Write the failing tests**

`src/utils/__test__/embedFilters.cluster.test.ts`:

```ts
/**
 * F12.104–F12.109 — a Streamlit page sets an embed's filters and hears them change.
 *
 * Intent: lex_view(filters=...) puts the first filters in the URL as a
 * base64url token and posts later changes as a `filter` message, so the frame
 * never reloads. Named fields replace their column filters (a list → a value
 * list, null → cleared, [] → no rows); fields the user filtered stay; a column
 * without a value list is ignored with a warning. Messages count only from the
 * parent frame with the host tag. The embed reports changes as a gated `filter`
 * event. A regression lets a page widen nothing it shouldn't, but it does make
 * linked tables silently stop following each other.
 *
 * Cluster F12 (embed_streamlit), batch 12u, scenarios F12.104–F12.109. Tier U.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  __resetInitialEmbedFiltersForTests,
  applyHostFilters,
  decodeFiltersToken,
  filtersForEvent,
  onHostFilters,
  takeInitialEmbedFilters,
} from '../embedFilters'
import { __resetEmbedParamCacheForTests } from '../lexAppBridge'

const fields: any[] = [
  { name: 'strategy', type: 'string', readable_name: 'Strategy', choices: [['equity', 'Equity']] },
  { name: 'name', type: 'string', readable_name: 'Name' },
  { name: 'nav', type: 'float', readable_name: 'NAV' },
]

const token = (value: unknown): string =>
  btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(value))))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')

afterEach(() => {
  __resetInitialEmbedFiltersForTests()
  __resetEmbedParamCacheForTests()
  window.history.replaceState({}, '', '/')
})

describe('F12 — embed filters (batch 12u)', () => {
  it('F12_104: the first-load token decodes, UTF-8 included', () => {
    // Scenario F12.104
    expect(decodeFiltersToken(token({ name: ['Zürich'], strategy: 'equity' }))).toEqual({ name: ['Zürich'], strategy: 'equity' })
    expect(decodeFiltersToken('not-base64!')).toBeNull()
    expect(decodeFiltersToken(null)).toBeNull()
  })

  it('F12_105: host filters replace the fields they name, and only those', () => {
    // Scenario F12.105
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const current = { nav: { filterType: 'number', type: 'greaterThan', filter: 1 }, strategy: { filterType: 'set', values: ['x'] } }
    expect(applyHostFilters(current, { strategy: ['equity', 'fixed_income'], name: 'Alpha', nav: [5] }, fields)).toEqual({
      nav: { filterType: 'number', type: 'greaterThan', filter: 1 },
      strategy: { filterType: 'set', values: ['equity', 'fixed_income'] },
      name: { filterType: 'multi', filterModels: [null, { filterType: 'set', values: ['Alpha'] }] },
    })
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('nav'))
    warn.mockRestore()
  })

  it('F12_106: null clears a field; an empty list means no rows', () => {
    // Scenario F12.106
    const current = { strategy: { filterType: 'set', values: ['x'] } }
    expect(applyHostFilters(current, { strategy: null }, fields)).toEqual({})
    expect(applyHostFilters({}, { strategy: [] }, fields)).toEqual({ strategy: { filterType: 'set', values: [] } })
    expect(applyHostFilters({}, { strategy: [true, 3, null] }, fields)).toEqual({ strategy: { filterType: 'set', values: ['true', '3', null] } })
  })

  it('F12_107: first-load filters apply once, to the table the embed opened', () => {
    // Scenario F12.107
    window.history.replaceState({}, '', `/fund?embed=true&filters=${token({ strategy: ['equity'] })}`)
    __resetEmbedParamCacheForTests()
    expect(takeInitialEmbedFilters('currency')).toBeNull()
    expect(takeInitialEmbedFilters('fund')).toEqual({ strategy: ['equity'] })
    expect(takeInitialEmbedFilters('fund')).toBeNull()
  })

  it('F12_108: only the parent frame with the host tag can push filters', () => {
    // Scenario F12.108
    const handler = vi.fn()
    const stop = onHostFilters(handler)
    const post = (data: unknown, source: unknown) =>
      window.dispatchEvent(new MessageEvent('message', { data, source: source as any }))
    post({ source: 'lex-app-host', type: 'filter', payload: { filters: { strategy: ['equity'] } } }, window.parent)
    post({ source: 'someone-else', type: 'filter', payload: { filters: { strategy: ['x'] } } }, window.parent)
    post({ source: 'lex-app-host', type: 'theme', payload: { mode: 'dark' } }, window.parent)
    post({ source: 'lex-app-host', type: 'filter', payload: { filters: ['x'] } }, window.parent)
    post({ source: 'lex-app-host', type: 'filter', payload: { filters: { strategy: ['y'] } } }, null)
    expect(handler).toHaveBeenCalledTimes(1)
    expect(handler).toHaveBeenCalledWith({ strategy: ['equity'] })
    stop()
  })

  it('F12_109: the event lists value-list selections per field', () => {
    // Scenario F12.109
    expect(filtersForEvent({
      strategy: { filterType: 'set', values: ['equity'] },
      name: { filterType: 'multi', filterModels: [{ filterType: 'text', type: 'contains', filter: 'a' }, { filterType: 'set', values: ['Alpha', null] }] },
      nav: { filterType: 'number', type: 'greaterThan', filter: 1 },
    })).toEqual({ strategy: ['equity'], name: ['Alpha', null] })
  })
})
```

Add to `src/utils/__test__/lexAppBridge.test.ts`, inside its main `describe`, following that file's
URL-setup pattern:

```ts
  it('F12_110: the filter event is gated by emit_filter and the new params are snapshotted', () => {
    // Scenario F12.110
    window.history.replaceState({}, '', '/fund?embed=true&emit_filter=true&filter_bar=strategy&filters=abc')
    __resetEmbedParamCacheForTests()
    expect(isEventOptedIn('filter')).toBe(true)
    expect(getEmbedParam('filter_bar')).toBe('strategy')
    expect(getEmbedParam('filters')).toBe('abc')
  })
```

Run both files to see them fail.

- [ ] **Step 2: The bridge**

In `src/utils/lexAppBridge.ts`:
- add `| 'filter'` to `LexBridgeEventType`;
- add `filter: 'emit_filter',` to `FLAG_MAP`;
- add `'filters', 'filter_bar'` to `SNAPSHOT_EXACT`.

- [ ] **Step 3: `embedFilters.ts`**

```ts
/**
 * Filters a Streamlit page sets on an embedded table, and the event it hears back.
 *
 * lex_view(filters=...) sends the first filters in the URL as a base64url JSON
 * token and posts later changes as `{source: 'lex-app-host', type: 'filter'}`.
 * Named fields replace their column filters; fields the user filtered stay. The
 * page's filters are ordinary grid filters: the server applies the same read
 * permissions to every request, so a page can never widen what a user sees.
 */
import type { FieldInfo } from '../api/endpoints/model-info'
import { columnFilterKind, hasValueList } from '../components/model-components/list/filters/columnFilters'
import { selectedKeysOf, toKey } from '../components/model-components/list/filters/setSelection'
import { getEmbedParam, getInitialEmbedHref } from './lexAppBridge'

export type HostFilters = Record<string, unknown>

export function decodeFiltersToken(token: string | null): HostFilters | null {
  if (!token) return null
  try {
    const base64 = token.replace(/-/g, '+').replace(/_/g, '/')
    const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4)
    const bytes = Uint8Array.from(atob(padded), (char) => char.charCodeAt(0))
    const parsed = JSON.parse(new TextDecoder().decode(bytes))
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null
  } catch {
    console.warn('[lex_view] filters: the first-load token could not be read; ignored')
    return null
  }
}

export function applyHostFilters(
  current: Record<string, unknown>,
  hostFilters: HostFilters,
  fields: FieldInfo[],
): Record<string, unknown> {
  const byName = new Map(fields.map((field) => [field.name, field]))
  const next: Record<string, unknown> = { ...current }
  for (const [field, value] of Object.entries(hostFilters)) {
    const info = byName.get(field)
    const kind = info ? columnFilterKind(info) : undefined
    if (!kind || !hasValueList(kind)) {
      console.warn(`[lex_view] filters: ${field} is not a value-list column of this table; ignored`)
      continue
    }
    if (value === null || value === undefined) {
      delete next[field]
      continue
    }
    const set = { filterType: 'set', values: (Array.isArray(value) ? value : [value]).map(toKey) }
    next[field] = kind === 'set' ? set : { filterType: 'multi', filterModels: [null, set] }
  }
  return next
}

export function filtersForEvent(filterModel: Record<string, unknown>): Record<string, Array<string | null>> {
  const out: Record<string, Array<string | null>> = {}
  for (const [field, model] of Object.entries(filterModel ?? {})) {
    const node = model as { filterType?: string; filterModels?: unknown[] } | null
    const isValueList =
      node?.filterType === 'set' ||
      (node?.filterType === 'multi' &&
        Array.isArray(node.filterModels) &&
        node.filterModels.some((child: any) => child?.filterType === 'set'))
    if (isValueList) out[field] = selectedKeysOf(model)
  }
  return out
}

let initialFiltersTaken = false

/** First-load filters, once per page load, for the table the embed opened with. */
export function takeInitialEmbedFilters(resource: string): HostFilters | null {
  if (initialFiltersTaken) return null
  try {
    const segments = new URL(getInitialEmbedHref()).pathname.split('/').filter(Boolean)
    if (!segments.includes(resource)) return null
  } catch {
    return null
  }
  const filters = decodeFiltersToken(getEmbedParam('filters'))
  if (filters) initialFiltersTaken = true
  return filters
}

export function __resetInitialEmbedFiltersForTests(): void {
  initialFiltersTaken = false
}

/** Filters the parent page posts later. Only the parent frame, only the host tag. */
export function onHostFilters(handler: (filters: HostFilters) => void): () => void {
  const listener = (event: MessageEvent): void => {
    if (event.source !== window.parent) return
    const data = event.data
    if (!data || typeof data !== 'object') return
    if (data.source !== 'lex-app-host' || data.type !== 'filter') return
    const filters = data.payload?.filters
    if (filters && typeof filters === 'object' && !Array.isArray(filters)) handler(filters as HostFilters)
  }
  window.addEventListener('message', listener)
  return () => window.removeEventListener('message', listener)
}
```

`getInitialEmbedHref()` returns the snapshot taken when the module loaded. In F12.107 the test
changes the URL afterwards, so `__resetEmbedParamCacheForTests()` must also reset `_initialHref`
(it does, see `lexAppBridge.ts`). `getInitialEmbedHref` then falls back to the current
`window.location.href`.

- [ ] **Step 4: Apply them in the list**

In `CustomList.tsx`:

1. Import `applyHostFilters, onHostFilters, takeInitialEmbedFilters`.
2. In `onBeforeServerDatasourceInit`, right after `restoreView(undefined, api)`:

```ts
      if (isEmbed) {
        const initial = takeInitialEmbedFilters(activeResource || '')
        if (initial) api.setFilterModel(applyHostFilters(api.getFilterModel?.() ?? {}, initial, fieldsRef.current))
      }
```

   Add `isEmbed` and `activeResource` to that callback's dependencies. `isEmbed` is computed further
   down; move its `const` above `onBeforeServerDatasourceInit`, unchanged.

3. Add the host listener:

```ts
  useEffect(() => {
    if (!isEmbed) return
    return onHostFilters((filters) => {
      const api = gridApiRef.current
      if (!api) return
      api.setFilterModel(applyHostFilters(api.getFilterModel?.() ?? {}, filters, fieldsRef.current))
      api.onFilterChanged()
    })
  }, [isEmbed])
```

- [ ] **Step 5: Emit the event**

In `CustomDatagrid.tsx`:

1. Import `filtersForEvent` from `../../../utils/embedFilters`.
2. Add, after `handleTotalRowCountChanged`:

```ts
  const FILTER_EVENT_DEBOUNCE_MS = 300
  const FILTER_EVENT_FALLBACK_MS = 2000
  const latestTotalRef = useRef<number | null>(null)
  const filterEventPendingRef = useRef(false)
  const filterEventTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const emitFilterEvent = useCallback(() => {
    const filterModel = gridRef?.current?.api?.getFilterModel?.() ?? {}
    emitLexEvent('filter', {
      resource: activeResource,
      filters: filtersForEvent(filterModel),
      filter_model: filterModel,
      row_count: latestTotalRef.current,
    })
  }, [activeResource, gridRef])

  const scheduleFilterEvent = useCallback(
    (delay: number) => {
      if (filterEventTimerRef.current) clearTimeout(filterEventTimerRef.current)
      filterEventTimerRef.current = setTimeout(() => {
        filterEventTimerRef.current = null
        filterEventPendingRef.current = false
        emitFilterEvent()
      }, delay)
    },
    [emitFilterEvent],
  )

  const handleFilterChanged = useCallback(
    (event?: any) => {
      onGridStateChange(event)
      if (!isEventOptedIn('filter')) return
      filterEventPendingRef.current = true
      // If no request follows (a cached answer), still report.
      scheduleFilterEvent(FILTER_EVENT_FALLBACK_MS)
    },
    [onGridStateChange, scheduleFilterEvent],
  )
```

3. In `handleTotalRowCountChanged`, set `latestTotalRef.current = total` before `setTableRowCount`.
4. At the end of `handleServerSideLoadingChange`'s `isLoading === false` branch, add:

```ts
      if (filterEventPendingRef.current) scheduleFilterEvent(FILTER_EVENT_DEBOUNCE_MS)
```

   and add `scheduleFilterEvent` to its dependencies.
5. Pass `onFilterChanged={handleFilterChanged}` instead of `onFilterChanged={onGridStateChange}`.
6. Clear the timer on unmount:

```ts
  useEffect(() => () => {
    if (filterEventTimerRef.current) clearTimeout(filterEventTimerRef.current)
  }, [])
```

In `CustomDatagrid.test.tsx`, make the bridge's `emitLexEvent` a spy. The file uses the real bridge
for its embed-parameter tests, so mock only that one export, beside the other `vi.mock` blocks:

```ts
vi.mock('../../../../utils/lexAppBridge', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../../utils/lexAppBridge')>()),
  emitLexEvent: vi.fn(),
}))
```

Add `emitLexEvent` to the file's existing import from `../../../../utils/lexAppBridge`. Import
`createServerSideDatasource` from `../ServerSideDatasource` (already mocked) and `act` from
`@testing-library/react`. Then add:

```ts
  it('F12_109b: a filtered load reports one filter event with its row count', () => {
    // Scenario F12.109 (second half)
    // Given an embed that opted in to filter events
    // When a filter change is followed by a load that reports 2 rows
    // Then exactly one filter event goes out, carrying the filters and the count
    vi.useFakeTimers()
    window.history.replaceState({}, '', '/fund?embed=true&emit_filter=true')
    __resetEmbedParamCacheForTests()
    vi.mocked(emitLexEvent).mockClear()
    const gridRef = React.createRef<any>()
    renderDatagrid({ useServerSide: true, gridRef })
    const config = vi.mocked(createServerSideDatasource).mock.calls.at(-1)![0] as any
    const filterModel = { strategy: { filterType: 'set', values: ['equity'] } }
    ;(gridRef as any).current = { api: { getFilterModel: () => filterModel } }

    act(() => {
      capturedBareProps.onFilterChanged({})
      config.onTotalChanged(2)
      config.onLoadingChange(true)
      config.onLoadingChange(false)
      vi.advanceTimersByTime(300)
    })

    const filterCalls = vi.mocked(emitLexEvent).mock.calls.filter(([type]) => type === 'filter')
    expect(filterCalls).toHaveLength(1)
    expect(filterCalls[0][1]).toEqual({
      resource: defaultProps.activeResource,
      filters: { strategy: ['equity'] },
      filter_model: filterModel,
      row_count: 2,
    })
    vi.useRealTimers()
    window.history.replaceState({}, '', '/')
    __resetEmbedParamCacheForTests()
  })
```

- [ ] **Step 6: Run tests and type-check**

Run: `./node_modules/.bin/vitest run src/utils src/components/model-components/list`, then tsc.
Expected: F12.104–F12.110 and F12.109b pass. The baseline is unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/utils src/components/model-components/list
git commit -m "feat(embed): a host page sets the table's filters and hears them change

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: "Select All Records" honours value lists

Skip this task if the bulk-update work has already replaced the GET-based id fetch with a POST to
the server translator. Check `getAllSelectableIds` in `ServerSideDatasource.ts`: if it calls
`translateFilterModel`, do the task.

**Files:**
- Modify: `src/components/model-components/list/ServerSideDatasource.ts` (`translateFilterModel`,
  `translateSingleFilter`'s `set` case)
- Test: `src/components/model-components/list/__test__/ServerSideDatasource.test.ts` (two cases)

- [ ] **Step 1: Write the failing tests**

Add to `src/components/model-components/list/__test__/ServerSideDatasource.test.ts`, inside the
`describe` that holds the GET-translator tests. Its `createMockHttpClient` answers POST with 404, which
forces the GET fallback that "Select All Records" also uses:

```ts
    const getUrl = async (filterModel: Record<string, unknown>): Promise<string> => {
      const httpClient = createMockHttpClient([])
      const { datasource } = createTestDatasource({ httpClient })
      const { params } = createGetRowsParams({ filterModel })
      await datasource.getRows(params)
      const getCall = (httpClient as ReturnType<typeof vi.fn>).mock.calls.find(
        (c: any[]) => !c[1]?.method || c[1].method === 'GET',
      )
      return decodeURIComponent(getCall![0] as string)
    }

    it('F3_79: value lists reach the GET fallback, blanks included', async () => {
      // Scenario F3.79
      // Given Set filters with several values, and with only a blank
      // When rows are fetched through the GET fallback
      // Then several values become __in, and a blank becomes __isnull
      expect(await getUrl({ strategy: { filterType: 'set', values: ['equity', 'fixed_income'] } })).toContain('strategy__in=equity,fixed_income')
      expect(await getUrl({ currency: { filterType: 'set', values: [null] } })).toContain('currency__isnull=true')
      expect(await getUrl({ name: { filterType: 'multi', filterModels: [{ filterType: 'text', type: 'contains', filter: 'a' }, { filterType: 'set', values: ['Alpha'] }] } }))
        .toMatch(/name__icontains=a.*name=Alpha|name=Alpha.*name__icontains=a/)
    })

    it('F3_80: nothing ticked selects nothing, without asking the server', async () => {
      // Scenario F3.80
      // Given a Set filter with an empty selection
      // When all selectable ids are asked for, and rows are fetched
      // Then no ids and no rows come back, and no GET is sent
      const httpClient = createMockHttpClient([{ id: 1 }])
      const { datasource } = createTestDatasource({ httpClient })
      const empty = { strategy: { filterType: 'set', values: [] } }
      expect(await (datasource as any).getAllSelectableIds({ filterModel: empty })).toEqual([])
      const { params } = createGetRowsParams({ filterModel: empty })
      await datasource.getRows(params)
      expect(params.success).toHaveBeenCalledWith({ rowData: [], rowCount: 0 })
      const gets = (httpClient as ReturnType<typeof vi.fn>).mock.calls.filter((c: any[]) => !c[1]?.method || c[1].method === 'GET')
      expect(gets).toHaveLength(0)
    })
```

If `createGetRowsParams` returns the params without `success` as a spy, use the spy it does return. It
"creates minimal IServerSideGetRowsParams with success/fail spies".

- [ ] **Step 2: Implement**

In `ServerSideDatasource.ts`, add above `translateFilterModel`:

```ts
/** True when a Set filter (alone or inside a Multi) has nothing ticked: no row can match. */
export function matchesNothing(filterModel: Record<string, any> | null | undefined): boolean {
  const isEmptySet = (node: any): boolean =>
    node?.filterType === 'set' && Array.isArray(node.values) && node.values.length === 0
  return Object.values(filterModel ?? {}).some(
    (node: any) =>
      isEmptySet(node) ||
      (node?.filterType === 'multi' && Array.isArray(node.filterModels) && node.filterModels.some(isEmptySet)),
  )
}
```

In `translateFilterModel`, before the `condition1/condition2` branch:

```ts
    if (model.filterType === 'multi' && Array.isArray(model.filterModels)) {
      for (const child of model.filterModels) {
        if (child) Object.assign(params, translateSingleFilter(field, child))
      }
      continue
    }
```

Replace the body of the `set` case in `translateSingleFilter` with:

```ts
    case 'set': {
      const values = Array.isArray(model.values) ? model.values : []
      const present = values.filter((value: unknown) => value !== null && value !== undefined)
      if (present.length === 0) {
        // Only blank ticked (an empty selection never gets here: matchesNothing stops it first).
        if (values.length > 0) params[`${field}__isnull`] = 'true'
        break
      }
      // A value AND a blank cannot be written as GET parameters (an OR across lookups);
      // this fallback keeps the values. The server translator, used by the grid, has both.
      if (present.length === 1) params[field] = present[0]
      else params[`${field}__in`] = present.join(',')
      break
    }
```

In `getAllSelectableIds`, as its first statement: `if (matchesNothing(filterModel as Record<string, any>)) return []`.

In `getRows`, immediately before `const isGrouped = rowGroupCols.length > 0`:

```ts
        if (matchesNothing(request.filterModel as Record<string, any>)) {
          params.success({ rowData: [], rowCount: 0 })
          return
        }
```

- [ ] **Step 3: Run, commit**

Run: `./node_modules/.bin/vitest run src/components/model-components/list/__test__/ServerSideDatasource.test.ts`

```bash
git add src/components/model-components/list/ServerSideDatasource.ts src/components/model-components/list/__test__/ServerSideDatasource.test.ts
git commit -m "fix(selection): Select All Records honours value lists

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The e2e helpers drive value lists

**Files:**
- Modify: `e2e/helpers/grid.ts` (`openColumnFilter` waits for a Set filter body too; new
  `applySetFilter`; `applyBooleanFilter` and `applyForeignKeyFilter` drive the Set filter)
- Modify: `e2e/e2e_project/Funds/Fund.py` (`filter_bar = ["strategy", "currency"]`)

- [ ] **Step 1: Helpers**

In `openColumnFilter`, widen both `.ag-filter-wrapper, .ant-select` selectors to
`.ag-filter-wrapper, .ag-set-filter, .ant-select`. Add:

```ts
/**
 * Tick values in a column's value list (AG Set filter, alone or as the second
 * half of a Multi filter) by their labels, then apply.
 */
export async function applySetFilter(page: Page, colId: string, labels: string[]): Promise<void> {
  const popup = await openColumnFilter(page, colId)
  const setFilter = popup.locator('.ag-set-filter').last()
  await expect(setFilter.locator('.ag-set-filter-item').first()).toBeVisible({ timeout: 10_000 })
  const selectAll = setFilter.locator('.ag-set-filter-select-all input[type="checkbox"]')
  // Start from nothing ticked: AG starts a fresh Set filter with everything ticked.
  if (await selectAll.isChecked()) await selectAll.click({ force: true })
  if (await selectAll.isChecked()) await selectAll.click({ force: true })
  for (const label of labels) {
    await setFilter
      .locator('.ag-set-filter-item', { hasText: label })
      .first()
      .locator('input[type="checkbox"]')
      .check({ force: true })
  }
  await popup.getByRole('button', { name: 'Apply' }).last().click()
  await page.keyboard.press('Escape')
}
```

Replace the bodies of `applyBooleanFilter` and `applyForeignKeyFilter`, and update their doc comments
to say they drive the value list now:

```ts
export async function applyBooleanFilter(page: Page, colId: string, value: boolean): Promise<void> {
  await applySetFilter(page, colId, [value ? 'Yes' : 'No'])
}

export async function applyForeignKeyFilter(page: Page, colId: string, optionLabel: string): Promise<void> {
  await applySetFilter(page, colId, [optionLabel])
}
```

- [ ] **Step 2: A default bar on the e2e Fund list**

In `e2e/e2e_project/Funds/Fund.py`, inside `class Fund`, after the fields:

```python
    # The list's default filter-bar columns (list_ui.filter_bar).
    filter_bar = ["strategy", "currency"]
```

- [ ] **Step 3: Run the existing filter journeys**

Prepare the backend per the e2e notes:
- a `lex` wrapper on `PATH` whose `PYTHONPATH` points at the backend worktree with the backend plan
  applied;
- migrate and seed by hand;
- `LEX_E2E_KEEP_DB=1`.

Then run:
`CI=1 LEX_E2E_KEEP_DB=1 NPM_MARMELAB_TOKEN=x npx playwright test e2e/grid-filters.spec.ts e2e/grid-combined.spec.ts e2e/datagrid.spec.ts`

Expected:
- Every boolean and foreign-key scenario passes through the new helpers.
- Text and date scenarios pass: their combobox and input are the Multi filter's first half.

If a selector misses, read the rendered DOM in the failing trace and adjust the selector. Never
change an assertion.

- [ ] **Step 4: Commit**

```bash
git add e2e/helpers/grid.ts e2e/e2e_project/Funds/Fund.py
git commit -m "test(e2e): filter helpers drive value lists

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: End-to-end journeys, the test plan, the PR

**Files:**
- Create: `e2e/value-lists.spec.ts`
- Plan: `docs/test-plan/clusters/F03-grid_read/*`, `F04-grid_views/*`, `F12-embed_streamlit/*`; a
  session fragment

- [ ] **Step 1: The journeys**

Seed facts: five funds.

| Strategy | Funds | Currency |
|---|---|---|
| equity | Alpha Growth, Delta Equity | USD |
| fixed_income | Beta Income, Epsilon Bond | EUR |
| multi_asset | Gamma Multi | GBP |

```ts
/**
 * F3.81 (E) and F12.111 (E) — value lists narrow each other in the grid, and a
 * host page drives an embed's filters without reloading it.
 */
import { expect, test } from '@playwright/test'
import { expectFilteredSet, navigateToGrid } from './helpers/grid'
import { appFrame, bridgeByType, openEmbedHost } from './helpers/embedHost'

const token = (value: unknown): string =>
  Buffer.from(JSON.stringify(value)).toString('base64url')

test('F3.81: the bar narrows the grid and the other lists', { tag: ['@grid_read', '@F03'] }, async ({ page }) => {
  await navigateToGrid(page, 'fund')
  const bar = page.getByTestId('filter-bar')
  await expect(bar.getByLabel('Strategy')).toBeVisible({ timeout: 20_000 })

  await bar.getByLabel('Strategy').click()
  await page.getByRole('option', { name: /^Equity/ }).click()
  await page.keyboard.press('Escape')
  await expectFilteredSet(page, 'name', ['Alpha Growth Fund', 'Delta Equity Fund'])

  await bar.getByLabel('Currency').click()
  const options = page.getByRole('option')
  await expect(options).toHaveCount(1)
  await expect(options.first()).toContainText('US Dollar')
  await expect(options.first()).toContainText('2')
})

test('F12.111: a host page sets, then changes, an embed’s filters', { tag: ['@embed_streamlit', '@F12'] }, async ({ page }) => {
  await openEmbedHost(page, { emit_filter: 'true', filters: token({ strategy: ['equity'] }) })
  const frame = appFrame(page)
  await expect.poll(async () => (await frame.locator('.ag-center-cols-container [col-id="name"]').allInnerTexts()).map((t) => t.trim()).sort(), { timeout: 20_000 })
    .toEqual(['Alpha Growth Fund', 'Delta Equity Fund'])

  // Mark the frame; a reload would clear the mark.
  await page.evaluate(() => { ((document.querySelector('#app-frame') as HTMLIFrameElement).contentWindow as any).__marker = 1 })
  await page.evaluate(() => {
    ;(document.querySelector('#app-frame') as HTMLIFrameElement).contentWindow!.postMessage(
      { source: 'lex-app-host', version: 1, type: 'filter', payload: { filters: { strategy: ['fixed_income'] } } },
      '*',
    )
  })
  await expect.poll(async () => (await frame.locator('.ag-center-cols-container [col-id="name"]').allInnerTexts()).map((t) => t.trim()).sort(), { timeout: 20_000 })
    .toEqual(['Beta Income Fund', 'Epsilon Bond Fund'])
  expect(await page.evaluate(() => ((document.querySelector('#app-frame') as HTMLIFrameElement).contentWindow as any).__marker)).toBe(1)

  await expect.poll(async () => (await bridgeByType(page, 'filter')).at(-1)?.payload?.filters, { timeout: 10_000 })
    .toEqual({ strategy: ['fixed_income'] })
  const last = (await bridgeByType(page, 'filter')).at(-1)
  expect(last.payload.row_count).toBe(2)
})
```

Run: `CI=1 LEX_E2E_KEEP_DB=1 NPM_MARMELAB_TOKEN=x npx playwright test e2e/value-lists.spec.ts e2e/embed.spec.ts`
Expected: both new journeys pass, and `embed.spec.ts` stays as it was (its known local failures F12.3,
F12.11 and F12.19 predate this work).

- [ ] **Step 2: Run the whole unit suite and type-check against the baseline**

Run: `./node_modules/.bin/vitest run --reporter=json --outputFile=<scratch>/after.json`
- Diff each test's status with the baseline run from Task 1.
- Only additions may differ, and every one of them must pass.
- Then run tsc and compare its error set with the baseline: it must be unchanged.

- [ ] **Step 3: Test-plan shards**

**F3 (`clusters/F03-grid_read/allocation.yaml`).** Append under `letters:` and set `max_scenario: 81`:

```yaml
  o:
    title: Value lists, the filter bar, and Select All honouring them (U/C tiers, E for F3.81)
    scenarios: F3.66-F3.81
    tier: C
    status: complete
    tests: {pass: 17, skip: 0, xfail: 0}
    file: src/components/model-components/list/filters/__test__/
    note: >-
      Value-list filters for the grid. F3.66-F3.67: each field type gets its filter (Set for
      choices, foreign keys and booleans; Multi = condition + Set for text, integers and dates;
      condition only for decimals, date-times and the key). F3.68-F3.72: lists come from
      filter_values with the grid's other filters, keep the server order, render labels as text,
      and a backend without the endpoint makes lists unavailable once. F3.73-F3.78: the filter bar
      (columns from embed, view, list; one state with the grid; Show in filter bar). F3.79-F3.80:
      Select All Records honours Set and Multi models. F3.81 (E): the bar narrows the grid and the
      other lists on the e2e fund grid.
```

**F4.** Append letter `e` (F4.41–F4.44, tier U, 4 pass, file
`src/utils/__test__/filterModelMigration.cluster.test.ts`) with the note: "saved views migrate
foreign-key equals, both boolean shapes and plain conditions to value lists / Multi; unexpressible
shapes are dropped with a warning; captureViewSettings stores filterBar". Set `max_scenario: 44`.

**F12.** Append letter `u` (F12.104–F12.111, tier U with F12.111 E, 9 pass, files
`src/utils/__test__/embedFilters.cluster.test.ts` and `e2e/value-lists.spec.ts`) with the note:
"lex_view filters — first-load token, host filter messages from the parent only, named fields
replace / null clears / [] matches nothing, the gated filter event with row_count; F12.111 drives a
real embed without a reload". Set `max_scenario: 111`.

Append to `docs/test-plan/clusters/F03-grid_read/batches.md`. Fill `<before>` / `<after>` with the
Step 2 numbers:

```markdown
## Batch 3o — Value lists, the filter bar, and Select All honouring them (F3.66-F3.81)

Every column gets the filter its values call for, and a filter bar shows chosen columns' value lists
above the grid. The bar holds no state: it reads and writes the grid's filter model.

- **Covers:**
  - **F3.66-F3.67** — the filter per field type, and the AG config each kind becomes.
  - **F3.68-F3.72** — lists from `filter_values` with the grid's other filters; server order; text
    labels; an older backend makes lists unavailable once; reading and writing the Set half.
  - **F3.73-F3.78** — the bar: its columns, its controls, writing the column filter, narrowing,
    removal, and "Show in filter bar".
  - **F3.79-F3.80** — Select All Records honours Set and Multi models, and an empty selection.
  - **F3.81 (E)** — the bar narrows the fund grid and the currency list.
- **Regression:** the full unit suite on the borrowed install fails the same tests before (<before>)
  and after (<after>); tsc's error set is unchanged.
```

Append to `docs/test-plan/clusters/F03-grid_read/cluster.md`'s scenario table:

```markdown
| F3.66 | Each field type gets its filter: Set, Multi or condition only | U | ✅ implemented (3o). |
| F3.67 | Each filter kind becomes the AG Grid filter config it names | U | ✅ implemented (3o). |
| F3.68 | A value list is fetched with the documented request and read back | U | ✅ implemented (3o). |
| F3.69 | A backend without `filter_values` makes lists unavailable, asking once | U | ✅ implemented (3o). |
| F3.70 | The column menu list asks with the grid's filters and keeps the server's order | U | ✅ implemented (3o). |
| F3.71 | A value renders its label and count as text | U | ✅ implemented (3o). |
| F3.72 | Selections read and write the Set half; a Multi keeps its condition | U | ✅ implemented (3o). |
| F3.73 | The bar's columns come from the embed, the view, then the list | U | ✅ implemented (3o). |
| F3.74 | One control per bar column; ticked values shown by label | C | ✅ implemented (3o). |
| F3.75 | Ticking in the bar writes the column filter, keeping a condition | C | ✅ implemented (3o). |
| F3.76 | Opening a bar list asks with the other filters and shows counts | C | ✅ implemented (3o). |
| F3.77 | Removing a bar column keeps its filter | C | ✅ implemented (3o). |
| F3.78 | The column menu offers "Show in filter bar" for value-list columns only | C | ✅ implemented (3o). |
| F3.79 | Value lists and blanks reach the GET fallback Select All uses | U | ✅ implemented (3o). |
| F3.80 | Nothing ticked selects nothing, without a request | U | ✅ implemented (3o). |
| F3.81 | The bar narrows the fund grid and the currency list | E | ✅ implemented (3o). |
```

Append to `docs/test-plan/clusters/F04-grid_views/batches.md`:

```markdown
## Batch 4e — Saved views and value lists (F4.41-F4.44)

Saved views keep filter models in local storage, in the shape of the filters columns had then.

- **Covers:**
  - **F4.41** — foreign-key `equals` and both boolean shapes become Set filters.
  - **F4.42** — text, date and combined conditions become the Multi filter's first half.
  - **F4.43** — a shape a value list cannot hold is dropped with a warning.
  - **F4.44** — a captured view carries `filterBar`; one capture function serves both save paths.
- **Regression:** as batch 3o.
```

and its scenario rows:

```markdown
| F4.41 | A saved single-value filter restores as a value list | U | ✅ implemented (4e). |
| F4.42 | A saved condition restores as the Multi filter's condition half | U | ✅ implemented (4e). |
| F4.43 | A saved shape a value list cannot hold is dropped with a warning | U | ✅ implemented (4e). |
| F4.44 | A saved view remembers its filter bar | U | ✅ implemented (4e). |
```

Append to `docs/test-plan/clusters/F12-embed_streamlit/batches.md`:

```markdown
## Batch 12u — A host page sets an embed's filters and hears them change (F12.104-F12.111)

`lex_view(filters=, filter_bar=, on_filter=)`, frontend half. The backend half is lex-app batch 1aw.

- **Covers:**
  - **F12.104** — the first-load token decodes, UTF-8 included.
  - **F12.105-F12.106** — named fields replace their filters, null clears, `[]` matches nothing,
    other fields stay.
  - **F12.107** — first-load filters apply once, to the table the embed opened.
  - **F12.108** — only the parent frame with the host tag can push filters.
  - **F12.109 / 109b** — the event lists value-list selections, and one filtered load sends one
    event with its row count.
  - **F12.110** — the event is gated by `emit_filter`; the new parameters are snapshotted.
  - **F12.111 (E)** — a real embed changes filters without reloading and reports them.
- **Regression:** as batch 3o.
```

and its scenario rows:

```markdown
| F12.104 | The first-load filters token decodes | U | ✅ implemented (12u). |
| F12.105 | Host filters replace the fields they name, and only those | U | ✅ implemented (12u). |
| F12.106 | null clears a field; an empty list matches no rows | U | ✅ implemented (12u). |
| F12.107 | First-load filters apply once, to the table the embed opened | U | ✅ implemented (12u). |
| F12.108 | Only the parent frame with the host tag can push filters | U | ✅ implemented (12u). |
| F12.109 | The filter event lists selections; one load sends one event with its count | U | ✅ implemented (12u). |
| F12.110 | `emit_filter` gates the event; `filters` and `filter_bar` are snapshotted | U | ✅ implemented (12u). |
| F12.111 | A host page changes an embed's filters without a reload, and hears back | E | ✅ implemented (12u). |
```

Add the session fragment `docs/test-plan/progress/sessions/2026-10-14-value-lists.md`:

```markdown
---
date: 2026-10-14
clusters: [F3, F4, F12]
tests_added: 31
suite_tally: { pass: 31, skip: 0, xfail: 0 }
---

# Value lists, the filter bar, and embed filters

Value-list filters for the grid ([3o](../../clusters/F03-grid_read/batches.md)), saved views that
migrate to them ([4e](../../clusters/F04-grid_views/batches.md)), and embeds a host page can filter
([12u](../../clusters/F12-embed_streamlit/batches.md)). The backend half is in lex-app (batches 14f,
14g, 4o, 10o, 1aw).
```

Set `tests_added` and `suite_tally` to what Step 2 counted for the new tests.

- [ ] **Step 4: Push and open the PR**

```bash
git add docs/test-plan e2e/value-lists.spec.ts
git commit -m "test(value-lists): journeys and test-plan shards

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin feat/multi-value-filters
```

Open the PR against `lex-app-v2-pac-latest`, titled "feat(grid): value-list filters, a filter bar, and embed filters". The body:
- covers what changed, the dependency on the backend PR (`filter_values`, Set/Multi translation,
  `list_ui.filter_bar`), and the fallback when the backend is older;
- says the two custom filter components are no longer used by the grid and can be removed with
  their tests separately;
- gives the test numbers against the baseline;
- ends with the Claude Code line.
