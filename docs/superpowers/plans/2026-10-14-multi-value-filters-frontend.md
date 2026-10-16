# Multi-value filters — frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In the grid and every embed:
- value-list filters (AG Set, and Multi = condition + list) whose lists narrow under the other
  filters, on exactly the columns the backend marks `value_list: true`;
- a filter bar over the same filter state;
- saved views that migrate old filter shapes;
- embeds that take filters from the host, report filter changes, and update without reloading.

**Architecture:** AG Grid's filter model is the only filter state.
- Small, separately tested modules decide each column's filter from the backend's `value_list`
  flag (`columnFilters`), fetch value lists (`filterValuesClient`), configure Set filters
  (`setFilterParams`), read and write selections (`setSelection`), migrate saved models
  (`filterModelMigration`), resolve bar columns (`filterBarColumns`) and translate host filters
  (`embedFilters`).
- `CustomDatagrid` and `CustomList` only wire them.

**Tech Stack:** React 18, react-admin 5.7, AG Grid Enterprise 33.2.1 (`SetFilterModule`,
`MultiFilterModule`), MUI, Vitest + Testing Library, Playwright (E tier).

**Spec:** `lex-app` repo, `docs/superpowers/specs/2026-10-14-multi-value-filters-and-cross-filtering-design.md`
(read its *Amendments* first: 1–12).

**Depends on:** the backend branch `feat/multi-value-filters` (worktree
`/home/syscall/Documents/lex/.claude/worktrees/lex_multi_filter`): the backend plan
(`docs/superpowers/plans/2026-10-14-multi-value-filters-backend.md`, Tasks 1–5: the `filter_values`
endpoint, the Set/Multi translation, `list_ui.filter_bar`, `lex_view(filters=, filter_bar=, on_filter=)`)
plus the follow-ups that implement spec Amendments 7–12:
- every field of `GET api/model_info/<model>/fields` carries `value_list: true|false`, decided by
  the resolver `filter_values` uses, through the serializer the list renders with;
- no value list for `TextField` columns without choices, opted-out models (`lex_value_lists = False`,
  e.g. `AuditLog`), write-only, renamed-source, credential-shaped and custom-rendered fields;
- the fields-endpoint shape: a model-backed field's descriptor carries the model column's `name`,
  so a renamed-source serializer field arrives named after its source column (two descriptors
  can share a name, Task 1's rule);
- the `lex_view` component posts `{source: 'lex-app-host', version: 1, type: 'filter', …}` after
  every frame load and on reruns, and sends filter tokens over 2,000 characters by message only.

Tasks 1–6 run against mocks. Task 7 Step 3 and Task 8 need a backend at that branch's head.

## Global Constraints

- **No customer data** in any file, test, commit, PR or issue. Use the e2e project (funds,
  currencies) or invented names.
- **Repo:** `ExcellenceCloudGmbH/process-admin-general-client`. Default branch `lex-app-v2-pac-latest`,
  not `main`. Base: `c2dbaa0` (PR #576 merged).
- **Worktree:** `/home/syscall/LUND_IT/process-admin-general-client/.claude/worktrees/multi_value_filters`,
  branch `feat/multi-value-filters`. Borrow the main clone's install:
  `ln -sfn /home/syscall/LUND_IT/process-admin-general-client/node_modules node_modules`.
- **Test commands:** `./node_modules/.bin/vitest run <paths>` and
  `./node_modules/.bin/tsc --noEmit -p tsconfig.json`.
  - The borrowed install has a fixed baseline: about 193 vitest failures in 24 files, and 238 tsc
    errors.
  - Compare before and after, never absolute counts. Task 1 Step 1 records the baseline in
    `BASELINE=/home/syscall/LUND_IT/process-admin-general-client/.claude/worktrees/multi_value_filters-baseline`
    (outside the worktree, never committed).
  - tsc errors are compared without their line and column, because edited files shift them:
    `grep "error TS" <file> | sed -E 's/\([0-9]+,[0-9]+\)//' | sort`.
  - The tsconfig sets no `target`, so spreading or `for…of` over a `Set`, a `Map` or a typed array
    is error TS2802. Use `Array.from(…)` or `.forEach`.
  - `yarn` needs `NPM_MARMELAB_TOKEN` set to any value.
- **Commits and PR:**
  - Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - The PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
  - No issue IDs in the branch, title or body.
  - The user merges.
- **Test plan:** `docs/test-plan/`. Allocations checked at `c2dbaa0`:
  - F3 grid_read: letter `o`, F3.66–F3.83.
  - F4 grid_views: letter `e`, F4.41–F4.44.
  - F12 embed_streamlit: letter `u`, F12.104–F12.112 (`t` is F12.103, PR #576, merged).
- **Wire constants** (shared with the backend):
  - Keys: `null` (blank), `"true"` / `"false"`, ISO dates, `String(value)`.
  - Labels: `"(Blank)"`; booleans `"Yes"` / `"No"` (the server sends them); a foreign key is
    `str(related)`.
  - Limits: `filter_values` default 200, column-menu lists 1000.
  - The fields endpoint: every field carries `value_list: true|false`; a renamed-source field's
    descriptor carries its source column's `name`, so names can repeat. Every lookup by name goes
    through `fieldsByName` (Task 1).
  - The host message is `{source: 'lex-app-host', version: 1, type: 'filter', payload: {filters}}`,
    posted after every frame load and on reruns when it differs. The app ignores a `version`
    newer than its own `LEX_BRIDGE_VERSION` (1), and keeps a message that arrives before any list
    for the first list that mounts.
  - The bridge event is `filter`, opted in by `emit_filter`.
  - The URL parameters are `filters` (base64url JSON; left out when longer than 2,000 characters,
    which then arrive by message only) and `filter_bar` (comma list; empty = hide; the opening
    table only).

---

### Task 1: Which filter each column gets

**Files:**
- Create: `src/components/model-components/list/filters/columnFilters.ts`
- Modify: `src/api/endpoints/model-info.ts` (`GeneralFieldInfo.is_pk`, `GeneralFieldInfo.value_list`,
  `ListUiInfo.filter_bar`)
- Test: `src/components/model-components/list/filters/__test__/columnFilters.cluster.test.ts` (new)

The grid does not use this module yet; Task 2 wires it together with the value lists, so no commit
ships a Set filter without values.

**Interfaces:**
- Produces:
  - `type ColumnFilterKind = 'set' | 'multi-text' | 'multi-number' | 'multi-date' | 'text' | 'number' | 'date' | 'date-time' | 'foreign-key' | 'boolean' | 'default'`
  - `columnFilterKind(fieldInfo: FieldInfo): ColumnFilterKind`
  - `hasValueList(kind: ColumnFilterKind): boolean`
  - `fieldsByName(fields: FieldInfo[]): Map<string, FieldInfo>`
  - `buildColumnFilter(kind, deps: ColumnFilterDeps): { filter?: unknown; filterParams?: Record<string, unknown> }`
  - `ColumnFilterDeps = { conditionParams, dateParams, setParams, dateTimeFilter, foreignKeyFilter, foreignKeyParams, booleanFilter }`
  - `GeneralFieldInfo.is_pk?: boolean`, `GeneralFieldInfo.value_list?: boolean`, `ListUiInfo.filter_bar?: string[]`

**Rule — a name sent twice.** The backend's fields endpoint names a model-backed field's
descriptor after its model column. A renamed-source serializer field
(`fund_currency = PrimaryKeyRelatedField(source="currency")`) therefore arrives as a second
descriptor named `currency`. The renamed one always has `value_list: false` (the backend refuses a
field whose `source` is another column); the source column's own descriptor may have `true`. So:
1. A column's filter comes only from its own descriptor (`columnFilterKind(fieldInfo)` on the
   descriptor the column is built from), never from a descriptor found by name.
2. Every lookup by name goes through `fieldsByName(fields)`: the descriptor whose kind has a value
   list stands for the name, whatever the order; among equals, the first one does. Never
   `new Map(fields.map((f) => [f.name, f]))` (last wins: a later twin hides the list) and never
   `fields.find((f) => f.name === name)` (first wins: an earlier twin hides it).
3. In the grid, the descriptor `fieldsByName` picks owns the name as its column id, the key of the
   filter model and of everything keyed by name. A twin's column gets the id `<name>_<n>` and no
   value list. The bar, host filters and saved views write by name, so they never put a value list
   into a column whose own descriptor has none.

- [ ] **Step 1: Record the baseline**

Before any change, record what the borrowed install fails today. Later tasks and Task 8 compare
against these two files.

```bash
BASELINE=/home/syscall/LUND_IT/process-admin-general-client/.claude/worktrees/multi_value_filters-baseline
mkdir -p "$BASELINE"
./node_modules/.bin/vitest run --reporter=json --outputFile="$BASELINE/vitest-before.json" > /dev/null 2>&1 || true
./node_modules/.bin/tsc --noEmit -p tsconfig.json > "$BASELINE/tsc-before.txt" 2>&1 || true
grep -c "error TS" "$BASELINE/tsc-before.txt"
```

Expected: the tsc count is 238; `vitest-before.json` exists.

- [ ] **Step 2: Write the failing test**

Create `src/components/model-components/list/filters/__test__/columnFilters.cluster.test.ts`:

```ts
/**
 * F3.66–F3.67 — every column gets the filter its values call for.
 *
 * Intent: the backend says which columns have a value list (`value_list` on every
 * field of the fields endpoint, decided by the resolver filter_values uses). Those
 * get AG's Set filter (choices, foreign keys, booleans) or a condition filter plus
 * a value list (AG's Multi filter: text, integers, dates). Every other column --
 * long text, a model that opts out, a masked or write-only field, every column of
 * an older backend -- keeps exactly the filter it has today. A name the endpoint
 * sends twice (a renamed field is named after its source column) stands for the
 * descriptor that has the list, whatever the order. A regression either offers a
 * list the server refuses, or takes away a filter a column has now.
 *
 * Cluster F3 (grid_read), batch 3o, scenarios F3.66–F3.67. Tier U.
 */
import { describe, expect, it } from 'vitest'
import {
  buildColumnFilter,
  columnFilterKind,
  fieldsByName,
  hasValueList,
  type ColumnFilterKind,
} from '../columnFilters'

const field = (type: string, extra: Record<string, unknown> = {}) =>
  ({ name: 'f', readable_name: 'F', type, ...extra }) as any
const listed = (type: string, extra: Record<string, unknown> = {}) => field(type, { value_list: true, ...extra })
const choices = [{ id: 'a', name: 'A' }]

describe('F3 — value lists: which filter each column gets (batch 3o)', () => {
  it('F3_66: the value_list flag decides, then the field type', () => {
    // Scenario F3.66
    // Given fields as the fields endpoint reports them, with and without value_list
    // When their filter kind is decided
    // Then listed choices, foreign keys and booleans get a value list; listed text,
    // integers and dates combine a condition with one; every other column keeps
    // today's filter, and the primary key never lists its values
    expect(columnFilterKind(listed('string', { choices }))).toBe('set')
    expect(columnFilterKind(listed('foreign_key', { target: 'currency' }))).toBe('set')
    expect(columnFilterKind(listed('boolean'))).toBe('set')
    expect(columnFilterKind(listed('string'))).toBe('multi-text')
    expect(columnFilterKind(listed('int'))).toBe('multi-number')
    expect(columnFilterKind(listed('int', { is_pk: true }))).toBe('number')
    expect(columnFilterKind(listed('date'))).toBe('multi-date')
    expect(columnFilterKind(field('string'))).toBe('text')
    expect(columnFilterKind(field('string', { choices }))).toBe('text')
    expect(columnFilterKind(field('foreign_key', { target: 'currency' }))).toBe('foreign-key')
    expect(columnFilterKind(field('boolean'))).toBe('boolean')
    expect(columnFilterKind(field('int'))).toBe('number')
    expect(columnFilterKind(field('date'))).toBe('date')
    expect(columnFilterKind(field('float'))).toBe('number')
    expect(columnFilterKind(field('date_time'))).toBe('date-time')
    for (const type of ['file', 'image_file', 'pdf_file', 'xlsx_file', 'json', 'many_to_many']) {
      expect(columnFilterKind(field(type)), type).toBe('default')
    }
    const listedKinds: ColumnFilterKind[] = ['set', 'multi-text', 'multi-number', 'multi-date']
    const otherKinds: ColumnFilterKind[] = ['text', 'number', 'date', 'date-time', 'foreign-key', 'boolean', 'default']
    expect(listedKinds.every((kind) => hasValueList(kind))).toBe(true)
    expect(otherKinds.some((kind) => hasValueList(kind))).toBe(false)
  })

  it('F3_66b: a name sent twice stands for its listed descriptor, whatever the order', () => {
    // Scenario F3.66 (a name sent twice)
    // Given the fields endpoint naming a renamed field after its source column
    // When the fields are looked up by name, in either order
    // Then the descriptor with the value list stands for the name; among equals the first does
    const renamed = field('foreign_key', { name: 'currency', target: 'currency', value_list: false })
    const source = listed('foreign_key', { name: 'currency', target: 'currency' })
    expect(fieldsByName([renamed, source]).get('currency')).toBe(source)
    expect(fieldsByName([source, renamed]).get('currency')).toBe(source)
    const first = field('string', { name: 'code' })
    const second = field('string', { name: 'code' })
    expect(fieldsByName([first, second]).get('code')).toBe(first)
  })

  it('F3_67: each kind becomes the AG Grid filter it names', () => {
    // Scenario F3.67
    // Given the deps a column filter is built from
    // When each kind is built
    // Then Set and Multi filters carry the value-list params, a Multi lists the
    // condition first and the values second, and the other kinds are today's filters
    const deps = {
      conditionParams: { buttons: ['reset', 'apply'] },
      dateParams: { buttons: ['reset', 'apply'], comparator: () => 0 },
      setParams: { values: () => undefined },
      dateTimeFilter: 'DateTimeComponent',
      foreignKeyFilter: 'ForeignKeyComponent',
      foreignKeyParams: { target: 'currency' },
      booleanFilter: 'BooleanComponent',
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
    expect((buildColumnFilter('multi-date', deps).filterParams as any).filters[0])
      .toEqual({ filter: 'agDateColumnFilter', filterParams: deps.dateParams })
    expect(buildColumnFilter('text', deps)).toEqual({ filter: 'agTextColumnFilter', filterParams: deps.conditionParams })
    expect(buildColumnFilter('number', deps)).toEqual({ filter: 'agNumberColumnFilter', filterParams: deps.conditionParams })
    expect(buildColumnFilter('date', deps)).toEqual({ filter: 'agDateColumnFilter', filterParams: deps.dateParams })
    expect(buildColumnFilter('date-time', deps)).toEqual({ filter: 'DateTimeComponent', filterParams: deps.dateParams })
    expect(buildColumnFilter('foreign-key', deps)).toEqual({ filter: 'ForeignKeyComponent', filterParams: deps.foreignKeyParams })
    expect(buildColumnFilter('boolean', deps)).toEqual({ filter: 'BooleanComponent' })
    expect(buildColumnFilter('default', deps)).toEqual({})
  })
})
```

- [ ] **Step 3: Run it to see it fail**

Run: `./node_modules/.bin/vitest run src/components/model-components/list/filters`
Expected: fails to resolve `../columnFilters`.

- [ ] **Step 4: Implement `columnFilters.ts`**

```ts
/**
 * Which filter each grid column gets, and the AG Grid config for it.
 *
 * The backend decides which columns have a value list: every field of the fields
 * endpoint carries `value_list`, from the resolver filter_values uses (spec
 * Amendment 10). Where it is true: a value list (AG's Set filter) for choices,
 * foreign keys and booleans; a condition filter PLUS a value list (AG's Multi
 * filter) for text, integers and dates. Everywhere else -- long text, a model that
 * opts out, a masked or write-only field, every field of an older backend -- the
 * column keeps the filter it has today. The primary key never lists its values.
 *
 * A field name can arrive twice: the fields endpoint names a renamed-source
 * serializer field after its SOURCE column. `fieldsByName` decides which
 * descriptor a name stands for, and every lookup by name goes through it.
 */
import type { FieldInfo } from '../../../../api/endpoints/model-info'

export type ColumnFilterKind =
  | 'set'
  | 'multi-text'
  | 'multi-number'
  | 'multi-date'
  | 'text'
  | 'number'
  | 'date'
  | 'date-time'
  | 'foreign-key'
  | 'boolean'
  | 'default'

const INTEGER_TYPES = new Set(['int', 'integer'])
const CONTINUOUS_NUMBER_TYPES = new Set(['float', 'decimal', 'number'])
const UNLISTABLE_TYPES = new Set(['file', 'image_file', 'pdf_file', 'xlsx_file', 'json', 'many_to_many'])

export function columnFilterKind(fieldInfo: FieldInfo): ColumnFilterKind {
  const { type } = fieldInfo
  if (fieldInfo.value_list === true && !fieldInfo.is_pk) {
    if (Array.isArray(fieldInfo.choices) && fieldInfo.choices.length > 0) return 'set'
    if (type === 'foreign_key' || type === 'boolean') return 'set'
    if (INTEGER_TYPES.has(type)) return 'multi-number'
    if (type === 'date') return 'multi-date'
    if (type !== 'date_time' && !CONTINUOUS_NUMBER_TYPES.has(type) && !UNLISTABLE_TYPES.has(type)) return 'multi-text'
  }
  if (type === 'foreign_key') return 'foreign-key'
  if (type === 'boolean') return 'boolean'
  if (type === 'date_time') return 'date-time'
  if (type === 'date') return 'date'
  if (INTEGER_TYPES.has(type) || CONTINUOUS_NUMBER_TYPES.has(type)) return 'number'
  if (UNLISTABLE_TYPES.has(type)) return 'default'
  return 'text'
}

export const hasValueList = (kind: ColumnFilterKind): boolean =>
  kind === 'set' || kind.startsWith('multi-')

/**
 * The descriptor each field name stands for, for every lookup by name.
 *
 * The fields endpoint names a renamed-source serializer field (`source="x"`) after
 * its source column `x`, so two descriptors can share a name; the renamed one never
 * has a value list. The descriptor whose kind has a value list stands for the name,
 * whatever the order; among equals, the first one does. A map built with
 * `new Map(fields.map(...))` would let a later twin hide the value list.
 */
export function fieldsByName(fields: FieldInfo[]): Map<string, FieldInfo> {
  const byName = new Map<string, FieldInfo>()
  for (const field of fields) {
    const current = byName.get(field.name)
    if (!current || (!hasValueList(columnFilterKind(current)) && hasValueList(columnFilterKind(field)))) {
      byName.set(field.name, field)
    }
  }
  return byName
}

export interface ColumnFilterConfig {
  filter?: unknown
  filterParams?: Record<string, unknown>
}

export interface ColumnFilterDeps {
  /** Params for AG's provided condition filters (Apply / Reset buttons). */
  conditionParams: Record<string, unknown>
  /** The same, with the date comparator. */
  dateParams: Record<string, unknown>
  /** The Set filter params for this column (see setFilterParams.ts). */
  setParams: Record<string, unknown>
  /** The custom filters a column keeps when it has no value list. */
  dateTimeFilter: unknown
  foreignKeyFilter: unknown
  foreignKeyParams: Record<string, unknown>
  booleanFilter: unknown
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
      return multi('agDateColumnFilter', deps.dateParams, deps.setParams)
    case 'text':
      return { filter: 'agTextColumnFilter', filterParams: deps.conditionParams }
    case 'number':
      return { filter: 'agNumberColumnFilter', filterParams: deps.conditionParams }
    case 'date':
      return { filter: 'agDateColumnFilter', filterParams: deps.dateParams }
    case 'date-time':
      return { filter: deps.dateTimeFilter, filterParams: deps.dateParams }
    case 'foreign-key':
      return { filter: deps.foreignKeyFilter, filterParams: deps.foreignKeyParams }
    case 'boolean':
      return { filter: deps.booleanFilter }
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

In `src/api/endpoints/model-info.ts`:
- in `GeneralFieldInfo`, replace the commented `// is_pk: boolean` with `is_pk?: boolean` and add
  `value_list?: boolean`;
- add `filter_bar?: string[]` to `ListUiInfo`.

- [ ] **Step 5: Run it to see it pass**

Run: `./node_modules/.bin/vitest run src/components/model-components/list/filters`
Expected: 3 passed (F3_66, F3_66b, F3_67).

Then compare tsc with the baseline:

```bash
BASELINE=/home/syscall/LUND_IT/process-admin-general-client/.claude/worktrees/multi_value_filters-baseline
./node_modules/.bin/tsc --noEmit -p tsconfig.json 2>&1 | grep "error TS" | sed -E 's/\([0-9]+,[0-9]+\)//' | sort > "$BASELINE/tsc-now.txt"
diff <(grep "error TS" "$BASELINE/tsc-before.txt" | sed -E 's/\([0-9]+,[0-9]+\)//' | sort) "$BASELINE/tsc-now.txt"
```

Expected: no diff output.

- [ ] **Step 6: Commit**

```bash
git add src/components/model-components/list/filters src/api/endpoints/model-info.ts
git commit -m "feat(grid): decide each column's filter from the backend's value_list flag

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Value lists from `filter_values`, wired into the grid

**Files:**
- Modify: `src/api/endpoints/model-entries.ts` (`filterValues` route)
- Create: `src/components/model-components/list/filters/filterValuesClient.ts`
- Create: `src/components/model-components/list/filters/setSelection.ts`
- Create: `src/components/model-components/list/filters/setFilterParams.ts`
- Modify: `src/components/model-components/list/CustomDatagrid.tsx` (register `SetFilterModule` and
  `MultiFilterModule`; `suppressSetFilterByDefault`; colDef filters from `buildColumnFilter`; one
  column id per field name; drop `getFilterForField`; a stable fetcher per resource / as_of /
  serializer / fixed filter)
- Test: `src/components/model-components/list/filters/__test__/valueLists.cluster.test.ts` (new)
- Modify: `src/components/model-components/list/__test__/CustomDatagrid.test.tsx` (module mock,
  http-client mock, seven F3.66 cases, F3.83)

**Interfaces:**
- Consumes (Task 1, `src/components/model-components/list/filters/columnFilters.ts`):
  `ColumnFilterKind`, `columnFilterKind`, `hasValueList`, `fieldsByName`, `buildColumnFilter`
  (deps `{ conditionParams, dateParams, setParams, dateTimeFilter, foreignKeyFilter, foreignKeyParams, booleanFilter }`).
  `fieldsByName` is the one lookup by name: where the fields endpoint sends a name twice (a
  renamed-source field is named after its source column), the descriptor with the value list stands
  for it, whatever the order.
- Produces:
  - `FilterValueItem = { key: string | null; label: string; count: number }`
  - `FilterValuesResult = { values: FilterValueItem[]; truncated: boolean }`
  - `FilterValuesRequest = { field; filterModel; selected; search?; limit? }`
  - `FetchFilterValues = (request) => Promise<FilterValuesResult | null>`. `null` means the
    backend has no endpoint. A refusal rejects with an `Error` carrying the backend's message.
  - `createFilterValuesFetcher({ httpClient, resource, query? }): FetchFilterValues`
  - `DEFAULT_VALUES_LIMIT = 200`, `COLUMN_MENU_VALUES_LIMIT = 1000`
  - `toKey(value): string | null`, `selectedKeysOf(model): Array<string | null>`,
    `withSelectedKeys(current, kind, keys): Record<string, unknown> | null`,
    `hasCondition(model): boolean`
  - `intendedFilterModel(api)`, `intendedKeysOf(api, field)`, `forgetIntendedFilterModel(api)`,
    `writeFilterModel(api, model, { reloadLists? }): Promise<void>`,
    `writeColumnFilterModel(api, field, model): Promise<void>`
  - `createSetFilterParams(field, fetchValues, applyParams): Record<string, unknown>`,
    `renderValueWithCount(items, value): HTMLElement`, `BLANK_LABEL = '(Blank)'`

- [ ] **Step 1: Write the failing tests**

Create `src/components/model-components/list/filters/__test__/valueLists.cluster.test.ts`:

```ts
/**
 * F3.68–F3.72, F3.82 — value lists come from the server, narrowed by the other filters.
 *
 * Intent: a value list must show what the grid would — the column's values under
 * every OTHER active filter, with a count each and a readable label — and keep a
 * ticked value listed even when the other filters removed it, so it can be
 * unticked. Labels are text, never HTML. A backend without the endpoint must not
 * break the column: its list is just unavailable. Keys the app writes (a saved
 * view, the host's filters, the bar) reach the list AG loads, because AG keeps
 * only the keys that are in its list.
 *
 * Cluster F3 (grid_read), batch 3o, scenarios F3.68–F3.72 and F3.82. Tier U.
 */
import { describe, expect, it, vi } from 'vitest'
import { COLUMN_MENU_VALUES_LIMIT, createFilterValuesFetcher } from '../filterValuesClient'
import { createSetFilterParams, renderValueWithCount } from '../setFilterParams'
import {
  forgetIntendedFilterModel,
  hasCondition,
  selectedKeysOf,
  toKey,
  withSelectedKeys,
  writeColumnFilterModel,
} from '../setSelection'

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
    // Given an older backend answering 404, or its catch-all answering 200 with the app's HTML
    // When values are fetched
    // Then the answer is null, and after a 404 only the first request reaches the
    // network; a refusal rejects with the backend's own words
    const httpClient = vi.fn().mockRejectedValue(Object.assign(new Error('Not Found'), { status: 404 }))
    const fetchValues = createFilterValuesFetcher({ httpClient, resource: 'fund' })
    expect(await fetchValues({ field: 'currency', filterModel: {}, selected: [] })).toBeNull()
    expect(await fetchValues({ field: 'currency', filterModel: {}, selected: [] })).toBeNull()
    expect(httpClient).toHaveBeenCalledTimes(1)

    const catchAll = vi.fn().mockResolvedValue({ json: undefined })
    expect(await createFilterValuesFetcher({ httpClient: catchAll, resource: 'fund' })({ field: 'currency', filterModel: {}, selected: [] })).toBeNull()

    const refusing = vi.fn().mockRejectedValue(
      Object.assign(new Error('field: x'), {
        status: 400,
        body: { filterModel: { currency: "'x' is not a valid value for currency." } },
      }),
    )
    await expect(
      createFilterValuesFetcher({ httpClient: refusing, resource: 'fund' })({ field: 'currency', filterModel: {}, selected: [] }),
    ).rejects.toThrow("'x' is not a valid value for currency.")
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
    expect(params.caseSensitive).toBe(true)
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

  it('F3_82: keys the app writes reach the list AG loads, so AG keeps them', async () => {
    // Scenario F3.82
    // Given a Set filter whose list was loaded before, under other filters
    // When the bar writes a key into it
    // Then the list reloads with that key as `selected` before the model is set,
    // and once the grid has applied it the key is no longer forced in
    const fetchValues = vi.fn().mockResolvedValue({ values: [], truncated: false })
    const params: any = createSetFilterParams('currency', fetchValues, {})
    const calls: string[] = []
    const setChild = {
      refreshFilterValues: vi.fn(() => {
        calls.push('reload')
        params.values({ api, success: vi.fn() })
      }),
    }
    const api: any = {
      getFilterModel: () => ({}),
      getColumnFilterInstance: vi.fn().mockResolvedValue({ getChildFilterInstance: () => setChild }),
      setColumnFilterModel: vi.fn(async () => { calls.push('set') }),
      onFilterChanged: vi.fn(),
    }
    await writeColumnFilterModel(api, 'currency', { filterType: 'multi', filterModels: [null, { filterType: 'set', values: ['42'] }] })
    expect(calls).toEqual(['reload', 'set'])
    await vi.waitFor(() => expect(fetchValues).toHaveBeenCalledTimes(1))
    expect(fetchValues.mock.calls[0][0].selected).toEqual(['42'])
    forgetIntendedFilterModel(api)
    params.values({ api, success: vi.fn() })
    await vi.waitFor(() => expect(fetchValues).toHaveBeenCalledTimes(2))
    expect(fetchValues.mock.calls[1][0].selected).toEqual([])
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
 * backend without the endpoint (404 / 405, or an older backend's catch-all
 * answering 200 with the app's own HTML) makes value lists unavailable for the
 * rest of the page's life, and the fetcher stops asking. A refusal (400) is
 * rethrown with the backend's own words.
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
      if (!Array.isArray((json as { values?: unknown } | null)?.values)) {
        // Not this endpoint: an older backend's catch-all answers 200 with index.html.
        unavailable = true
        return null
      }
      return normalizeFilterValues(json)
    } catch (error) {
      const failure = error as { status?: unknown; body?: unknown; message?: string } | null
      if (failure?.status === 404 || failure?.status === 405) {
        unavailable = true
        return null
      }
      throw new Error(refusalMessage(failure?.body) ?? failure?.message ?? 'Values could not be loaded')
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

/** The backend's own words: `{field: msg}`, `{selected: msg}` or `{filterModel: {column: msg}}`. */
function refusalMessage(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null
  for (const value of Object.values(body as Record<string, unknown>)) {
    if (typeof value === 'string') return value
    if (Array.isArray(value) && typeof value[0] === 'string') return value[0]
    const nested = refusalMessage(value)
    if (nested) return nested
  }
  return null
}
```

`customFetch` rejects with a `CustomHttpError` whose `status` is the HTTP status and whose `body` is
the parsed JSON, which is what `refusalMessage` reads.

- [ ] **Step 5: Selections, and writes that keep their keys**

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

/**
 * The filter model the app last wrote to a grid, until the grid has applied it.
 *
 * AG's Set filter loads its list before it applies a model, and keeps only the
 * keys that are in that list. So keys the app writes -- a saved view, the host's
 * filters, a key ticked in the bar -- are sent as `selected` (the server always
 * lists selected keys), and an existing Set filter reloads its list before it
 * receives them. Otherwise keys past the first 1,000 values, or outside the other
 * filters a list was loaded under, are silently dropped.
 */
const intendedModels = new WeakMap<object, Record<string, unknown>>()

export function intendedFilterModel(api: any): Record<string, unknown> {
  return (api && intendedModels.get(api)) ?? api?.getFilterModel?.() ?? {}
}

export function intendedKeysOf(api: unknown, field: string): Array<string | null> {
  const model = api && typeof api === 'object' ? intendedModels.get(api) : undefined
  return selectedKeysOf(model?.[field])
}

/** Called once the grid reports the change applied (its filterChanged event). */
export function forgetIntendedFilterModel(api: any): void {
  if (api) intendedModels.delete(api)
}

/** Make the Set filters of `fields` reload their lists, which now include the intended keys. */
async function reloadValueLists(api: any, fields: string[]): Promise<void> {
  await Promise.all(
    fields.map(async (field) => {
      const filter = await api.getColumnFilterInstance?.(field)
      const setFilter = typeof filter?.getChildFilterInstance === 'function' ? filter.getChildFilterInstance(1) : filter
      setFilter?.refreshFilterValues?.()
    }),
  )
}

/** Replace the grid's filter model. `reloadLists` when Set filters may already exist. */
export async function writeFilterModel(
  api: any,
  model: Record<string, unknown> | null,
  options: { reloadLists?: boolean } = {},
): Promise<void> {
  if (!api) return
  const next = model ?? {}
  intendedModels.set(api, { ...next })
  if (options.reloadLists) {
    await reloadValueLists(api, Object.keys(next).filter((field) => selectedKeysOf(next[field]).length > 0))
  }
  api.setFilterModel(Object.keys(next).length > 0 ? next : null)
}

/** Replace one column's filter model (the filter bar) and refresh the rows. */
export async function writeColumnFilterModel(
  api: any,
  field: string,
  model: Record<string, unknown> | null,
): Promise<void> {
  if (!api) return
  intendedModels.set(api, { ...intendedFilterModel(api), [field]: model })
  if (selectedKeysOf(model).length > 0) await reloadValueLists(api, [field])
  await api.setColumnFilterModel(field, model)
  api.onFilterChanged()
}
```

`writeFilterModel` without `reloadLists` reaches `api.setFilterModel` synchronously, before any
`await`. That matters in `onBeforeServerDatasourceInit`, where the datasource attaches right after.

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
import { intendedKeysOf, selectedKeysOf, toKey } from './setSelection'

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
    // Keys are stored values: "Alpha" and "alpha" are two values to the server.
    caseSensitive: true,
    values: (params: {
      api?: { getFilterModel?: () => Record<string, unknown> }
      success: (values: Array<string | null>) => void
    }) => {
      const filterModel = params.api?.getFilterModel?.() ?? {}
      fetchValues({
        field,
        filterModel,
        // The keys the grid holds, and the keys the app is writing right now.
        selected: Array.from(
          new Set([...selectedKeysOf(filterModel[field]), ...intendedKeysOf(params.api, field)]),
        ),
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

Run `./node_modules/.bin/vitest run src/components/model-components/list/filters`.
Expected: 9 passed (3 from `columnFilters.cluster.test.ts`, 6 from `valueLists.cluster.test.ts`).

- [ ] **Step 7: Wire the grid**

First record the grid's baseline, before any change to `CustomDatagrid.tsx`:

```bash
BASELINE=/home/syscall/LUND_IT/process-admin-general-client/.claude/worktrees/multi_value_filters-baseline
./node_modules/.bin/vitest run src/components/model-components/list --reporter=json --outputFile="$BASELINE/task2-list-before.json" > /dev/null 2>&1 || true
./node_modules/.bin/tsc --noEmit -p tsconfig.json 2>&1 | grep "error TS" | sed -E 's/\([0-9]+,[0-9]+\)//' | sort > "$BASELINE/tsc-task2-before.txt"
```

Then, in `src/components/model-components/list/CustomDatagrid.tsx`:

1. Add `MultiFilterModule` and `SetFilterModule` to the `ag-grid-enterprise` import and to
   `gridModules`.
2. Imports:
   - change `import { clearServerSideDatasourceCache, createServerSideDatasource } from './ServerSideDatasource'`
     to also import `type HttpClient`;
   - `import { buildColumnFilter, columnFilterKind, fieldsByName, hasValueList } from './filters/columnFilters'`;
   - `import { createFilterValuesFetcher } from './filters/filterValuesClient'`;
   - `import { createSetFilterParams } from './filters/setFilterParams'`.
3. Move the date comparator out of the colDef into a module-level constant, unchanged, below
   `FILTER_APPLY_PARAMS`:

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

4. Directly after the `serverSideDatasource` `useMemo`, add the fetcher:

```ts
  // `useRaHttpClient` builds a new function on every render. Read it through a
  // ref, so the fetcher -- and the column definitions built from it, which AG
  // re-processes on every new identity, closing open filter popups -- stays put.
  const httpClientRef = useRef<HttpClient>(httpClient)
  httpClientRef.current = httpClient
  const fetchFilterValues = useMemo(() => {
    // The same rows as the grid: its fixed filter (a record's history), as_of
    // and serializer ride the URL exactly as they do for the list POST.
    const query = {
      ...(serverFixedFilter || {}),
      ...(asOfIso ? { as_of: asOfIso } : {}),
      ...(serializer ? { serializer } : {}),
    }
    return createFilterValuesFetcher({
      httpClient: (url, options) => httpClientRef.current(url, options),
      resource: activeResource,
      query: Object.keys(query).length > 0 ? query : undefined,
    })
  }, [activeResource, asOfIso, serializer, serverFixedFilter])
```

5. Delete `getFilterForField`. In the `columnDefs` `useMemo`:
   - right after `if (fields.length === 0) return []`, add:

```ts
    // A name the fields endpoint sends twice -- a renamed-source field is named
    // after its source column -- belongs to the descriptor fieldsByName picks.
    const nameOwners = fieldsByName(fields)
    const twinCounts = new Map<string, number>()
```

   - inside `.map((fieldInfo: any) => {`, after `const valueFormatter = …` and just before the
     colDef's `return {`, add:

```ts
          // The owner's column keeps the name as its column id: the key of the
          // filter model, of the backend's filters and of every lookup by name. A
          // twin's column takes `<name>_<n>` -- the id AG gives a second column of
          // a name anyway -- and never a value list, since the name is not its key.
          const ownsName = nameOwners.get(fieldInfo.name) === fieldInfo
          let twinColId: string | undefined
          if (!ownsName) {
            const twinNumber = (twinCounts.get(fieldInfo.name) ?? 0) + 1
            twinCounts.set(fieldInfo.name, twinNumber)
            twinColId = `${fieldInfo.name}_${twinNumber}`
          }
          const filterKind = columnFilterKind(ownsName ? fieldInfo : { ...fieldInfo, value_list: false })
          const columnFilter = buildColumnFilter(filterKind, {
            conditionParams: FILTER_APPLY_PARAMS,
            dateParams: DATE_CONDITION_PARAMS,
            setParams: hasValueList(filterKind)
              ? createSetFilterParams(fieldInfo.name, fetchFilterValues, FILTER_APPLY_PARAMS)
              : {},
            dateTimeFilter: AGDateTimeFilter,
            foreignKeyFilter: AGForeignKeyFilter,
            foreignKeyParams: {
              target: fieldInfo.target,
              referenceResource: fieldInfo.target,
              optionText: 'short_description',
              optionValue: 'id',
            },
            booleanFilter: AGBooleanFilter,
          })
```

   - in the colDef object, directly after `field: fieldInfo.name,` add
     `...(twinColId ? { colId: twinColId } : {}),`;
   - replace the `filter: getFilterForField(fieldInfo.type),` line with `...columnFilter,`;
   - delete the whole `filterParams: ['date', 'date_time'].includes(fieldInfo.type) ? … : undefined,`
     expression. Its date and foreign-key branches now live in `DATE_CONDITION_PARAMS` and
     `foreignKeyParams`.
6. Keep the `AGForeignKeyFilter` and `AGBooleanFilter` imports. They stay the filters of
   foreign-key and boolean columns without a value list.
7. Add `fetchFilterValues` to the `columnDefs` `useMemo` dependency list.
8. In the `<BareDatagridAGClient>` props, beside `modules={gridModules}`, add:

```tsx
        // Registering SetFilterModule turns `filter: true` (defaultColDef) into
        // the Set filter, which the server-side row model cannot fill without a
        // values callback. Columns without their own filter keep the text filter.
        suppressSetFilterByDefault
```

- [ ] **Step 8: The grid's own tests**

In `src/components/model-components/list/__test__/CustomDatagrid.test.tsx`:

- Keep the `vi.mock('../AGForeignKeyFilter', …)` and `vi.mock('../AGBooleanFilter', …)` blocks.
- Add `MultiFilterModule: {},` and `SetFilterModule: {},` to the `vi.mock('ag-grid-enterprise', …)`
  factory. Without them the module-level `gridModules` fails to build and the whole file fails.
- Leave every existing filter expectation as it is (the five type tests and the three
  `date_field → agDateColumnFilter` assertions in "keeps filters for grids without managed
  displaySettings", F3.44 and "opting in via columnFiltersEnabled"). Their fixtures carry no
  `value_list`, so they now pin "an older backend keeps today's filters".
- Below `const mockHttpClient = vi.fn()` add:

```ts
// Production's useRaHttpClient builds a new function on every render; a test
// that needs that sets this flag (and clears it again).
let mockFreshHttpClientPerRender = false
```

  and change the `raHttpClient` mock to:

```ts
vi.mock('../../../../api/httpClients/raHttpClient', () => ({
  default: () => (mockFreshHttpClientPerRender ? (...args: any[]) => mockHttpClient(...args) : mockHttpClient),
}))
```

- After the test `'uses AGBooleanFilter (not the text filter) for boolean fields'`, add:

```tsx
  // ── value lists (F3 batch 3o) ─────────────────────────────────────
  // The fixtures above carry no `value_list`: they pin "an older backend keeps
  // today's filters". These carry it, as the fields endpoint does now.

  it.each([
    ['fk_field', 'foreign_key', 'agSetColumnFilter'],
    ['bool_field', 'boolean', 'agSetColumnFilter'],
    ['name_field', 'string', 'agMultiColumnFilter'],
    ['qty', 'int', 'agMultiColumnFilter'],
    ['date_field', 'date', 'agMultiColumnFilter'],
  ])('F3.66: %s (%s) with a value list gets %s', (name, type, filter) => {
    // Scenario F3.66 (the grid applies the kind)
    renderDatagrid({
      data: [{ fields: [{ name, type, readable_name: name, required: false, value_list: true, target: 'currency' }], id_field: 'id' }],
    } as any)
    expect(capturedBareProps.columnDefs.find((c: any) => c.field === name).filter).toBe(filter)
  })

  it('F3.66: registering the Set filter does not change the default filter', () => {
    // Scenario F3.66 (files and JSON keep the text filter)
    renderDatagrid()
    expect(capturedBareProps.suppressSetFilterByDefault).toBe(true)
  })

  it('F3.66: a name sent twice keeps one value list, on its own column', () => {
    // Scenario F3.66 (a name sent twice)
    // Given the fields endpoint naming a renamed field after its source column, in either order
    // When the grid builds both columns
    // Then the listed descriptor's column keeps the name and its value list; the
    // twin's column takes `currency_1` and keeps its own filter
    const renamed = { name: 'currency', type: 'foreign_key', readable_name: 'Fund Currency', required: false, target: 'currency', value_list: false }
    const source = { name: 'currency', type: 'foreign_key', readable_name: 'Currency', required: false, target: 'currency', value_list: true }
    for (const fields of [[renamed, source], [source, renamed]]) {
      const view = renderDatagrid({ data: [{ fields, id_field: 'id' }] } as any)
      const byHeader = (header: string) => capturedBareProps.columnDefs.find((c: any) => c.headerName === header)
      expect(byHeader('Currency').colId).toBeUndefined()
      expect(byHeader('Currency').filter).toBe('agSetColumnFilter')
      expect(byHeader('Fund Currency').colId).toBe('currency_1')
      expect(byHeader('Fund Currency').filter).toBe('AGForeignKeyFilter')
      view.unmount()
    }
  })

  it('F3.83: a value list asks for the grid’s own rows, and a re-render keeps the columns', () => {
    // Scenario F3.83
    // Given a record's history tab (a fixed filter) and a re-render with a new http client
    // When a Set filter asks for its values
    // Then the request carries the fixed filter, and the column definitions keep their identity
    mockFreshHttpClientPerRender = true
    try {
      mockHttpClient.mockResolvedValue({ json: { values: [], truncated: false } })
      const fields = [{ name: 'fk_field', type: 'foreign_key', readable_name: 'FK', required: false, value_list: true, target: 'currency' }]
      // The same objects on both renders, as CustomShow's memoised props are.
      const props = { data: [{ fields, id_field: 'id' }], useServerSide: true, serverFixedFilter: { id: 42 } } as any
      const { rerender } = renderDatagrid(props)
      const before = capturedBareProps.columnDefs
      const col = before.find((c: any) => c.field === 'fk_field')
      col.filterParams.values({ api: { getFilterModel: () => ({}) }, success: vi.fn() })
      expect(mockHttpClient.mock.calls.at(-1)![0]).toContain('filter_values?id=42')
      rerender(
        <ThemeProvider theme={theme}>
          <CustomDatagrid {...defaultProps} {...props} />
        </ThemeProvider>,
      )
      expect(capturedBareProps.columnDefs).toBe(before)
    } finally {
      mockFreshHttpClientPerRender = false
    }
  })
```

With `mockFreshHttpClientPerRender` every render gets a new http client, as in production, so F3.83
fails if the fetcher (or anything else in the column definitions) follows the client's identity.

- [ ] **Step 9: Run tests and type-check**

Run: `./node_modules/.bin/vitest run src/components/model-components/list`, then tsc compared
without positions:

```bash
BASELINE=/home/syscall/LUND_IT/process-admin-general-client/.claude/worktrees/multi_value_filters-baseline
./node_modules/.bin/tsc --noEmit -p tsconfig.json 2>&1 | grep "error TS" | sed -E 's/\([0-9]+,[0-9]+\)//' | sort > "$BASELINE/tsc-now.txt"
diff "$BASELINE/tsc-task2-before.txt" "$BASELINE/tsc-now.txt"
```

Expected:
- `valueLists.cluster.test.ts`: 6 passed (F3.68–F3.72, F3.82);
- `CustomDatagrid.test.tsx`: the eight new tests pass (seven F3.66 cases, F3.83), and so does
  every test that passed in `task2-list-before.json`;
- the failing tests equal those of `task2-list-before.json`; the tsc diff is empty.

- [ ] **Step 10: Commit**

```bash
git add src/api/endpoints/model-entries.ts src/components/model-components/list/filters src/components/model-components/list/CustomDatagrid.tsx src/components/model-components/list/__test__/CustomDatagrid.test.tsx
git commit -m "feat(grid): value lists from filter_values on the columns the backend lists

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Saved views keep working

**Files:**
- Create: `src/utils/filterModelMigration.ts`
- Modify: `src/components/model-components/list/CustomList.tsx` (a `fieldsRef`; `restoreView`
  migrates and writes through `writeFilterModel`, and returns that write)
- Modify: `src/components/model-components/list/useGridViewState.ts` (an optional `getFields`;
  `restoreView` migrates and writes through `writeFilterModel`)
- Modify: `src/components/CustomShow/CustomShow.tsx` (passes the history tab's fields to
  `useGridViewState`)
- Test: `src/utils/__test__/filterModelMigration.cluster.test.ts` (new)

**Interfaces:**
- Consumes (Task 1, `src/components/model-components/list/filters/columnFilters.ts`):
  `columnFilterKind`, `fieldsByName`, `ColumnFilterKind` (`'set'`, `'multi-*'` have a value list;
  `'foreign-key'`, `'boolean'`, `'text'`, `'number'`, `'date'`, `'date-time'`, `'default'` do not).
  `fieldsByName` is the one lookup by name: where the fields endpoint sends a name twice (a
  renamed-source field is named after its source column), the descriptor with the value list
  stands for it, whatever the order.
- Consumes (Task 2, `src/components/model-components/list/filters/setSelection.ts`): `toKey`,
  `writeFilterModel(api, model, { reloadLists? })`. `writeFilterModel` records the model as the
  grid's intended model (Set filters then list its keys as `selected`), reloads existing Set
  filters' lists first when `reloadLists` is set, and calls `api.setFilterModel(model or null)`;
  without `reloadLists` it does so synchronously.
- Produces:
  - `migrateFilterModel(filterModel: Record<string, unknown>, fields: FieldInfo[]): Record<string, unknown>`
  - `CustomList`'s `restoreView(viewKey?, apiOverride?)` returns the promise of its filter write
    (`undefined` when there is no grid API). Task 5 waits on it.
  - `useGridViewState({ …, getFields?: () => FieldInfo[] })`

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
 * A column without a value list here keeps only the condition saved on it.
 *
 * Cluster F4 (grid_views), batch 4e, scenarios F4.41–F4.43. Tier U.
 */
import { describe, expect, it, vi } from 'vitest'
import { migrateFilterModel } from '../filterModelMigration'

const fields: any[] = [
  { name: 'currency', type: 'foreign_key', readable_name: 'Currency', value_list: true },
  { name: 'is_active', type: 'boolean', readable_name: 'Active', value_list: true },
  { name: 'strategy', type: 'string', readable_name: 'Strategy', choices: [['equity', 'Equity']], value_list: true },
  { name: 'name', type: 'string', readable_name: 'Name', value_list: true },
  { name: 'inception_date', type: 'date', readable_name: 'Inception', value_list: true },
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

  it('F4_43: a shape a column filter cannot hold is dropped with a warning', () => {
    // Scenario F4.43
    // Given a choice column saved with a "contains" text filter, and a value list
    // saved on a column that has none here
    // When it is restored
    // Then the filter (or its value half) is dropped, the rest is kept, and a
    // warning names each
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    expect(migrateFilterModel({
      strategy: { filterType: 'text', type: 'contains', filter: 'eq' },
      name: { filterType: 'multi', filterModels: [null, { filterType: 'set', values: ['x'] }] },
    }, fields)).toEqual({ name: { filterType: 'multi', filterModels: [null, { filterType: 'set', values: ['x'] }] } })
    expect(warn).toHaveBeenCalledTimes(1)
    const condition = { filterType: 'number', type: 'greaterThan', filter: 1 }
    expect(migrateFilterModel({
      nav: { filterType: 'multi', filterModels: [condition, { filterType: 'set', values: ['1'] }] },
    }, fields)).toEqual({ nav: condition })
    expect(warn).toHaveBeenCalledTimes(2)
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
 * The other way round too: a column that has no value list here (an older
 * backend, a model that opted out) keeps only the condition a view saved on it.
 */
import type { FieldInfo } from '../api/endpoints/model-info'
import {
  columnFilterKind,
  fieldsByName,
  type ColumnFilterKind,
} from '../components/model-components/list/filters/columnFilters'
import { toKey } from '../components/model-components/list/filters/setSelection'

const CONDITION_TYPES = new Set(['text', 'number', 'date'])

export function migrateFilterModel(
  filterModel: Record<string, unknown>,
  fields: FieldInfo[],
): Record<string, unknown> {
  // One descriptor per name: a renamed field's twin never hides the listed one.
  const kinds = new Map<string, ColumnFilterKind>()
  fieldsByName(fields).forEach((field, name) => kinds.set(name, columnFilterKind(field)))
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

  // A column without a value list keeps a condition only (an older backend, or a
  // model that opted out after the view was saved).
  if (node.filterType === 'multi') {
    const condition = Array.isArray(node.filterModels) ? (node.filterModels[0] ?? null) : null
    if (Array.isArray(node.filterModels) && node.filterModels[1]) {
      console.warn(`[filters] the value list saved on "${field}" is not available here and was dropped`, node)
    }
    return condition
  }
  if (kind === 'foreign-key' && node.filterType === 'set') {
    const values = Array.isArray(node.values) ? node.values.filter((value: unknown) => value !== null) : []
    if (values.length === 1) return { filterType: 'text', type: 'equals', filter: values[0] }
    console.warn(`[filters] a saved filter on "${field}" cannot be shown by its filter and was dropped`, node)
    return null
  }
  return node
}
```

A filter-model key that names no descriptor (a twin's column, `<name>_<n>`) passes through unchanged.

- [ ] **Step 3: Use it in `CustomList`'s `restoreView`**

In `src/components/model-components/list/CustomList.tsx`:

1. Imports: `import { migrateFilterModel } from '../../../utils/filterModelMigration'`,
   `import { writeFilterModel } from './filters/setSelection'`, and change
   `import { type Fields } from '../../../api/endpoints/model-info'` to
   `import { type FieldInfo, type Fields } from '../../../api/endpoints/model-info'`.
2. Directly after `const modelFieldInfo = useGetModelFieldsQuery(…)` (CustomList.tsx:263–266) — not
   next to `gridViewStateRef`, which is declared before `modelFieldInfo` exists — add:

```ts
  const fieldsRef = useRef<FieldInfo[]>([])
  fieldsRef.current = (modelFieldInfo.data?.fields ?? []) as FieldInfo[]
```

3. In `restoreView`, step "3. Filters", replace these two lines:

```ts
      const persistedFilters = sanitizeFilterModelForViewPersistence(viewSettings?.filters ?? null)
      api.setFilterModel(Object.keys(persistedFilters).length > 0 ? persistedFilters : null)
```

   with:

```ts
      const persistedFilters = migrateFilterModel(
        sanitizeFilterModelForViewPersistence(viewSettings?.filters ?? null),
        fieldsRef.current,
      )
      // A view switch (grid already up) reloads the lists the view's keys go into.
      const filtersWritten = writeFilterModel(api, persistedFilters, { reloadLists: gridReadyRef.current })
```

   `gridReadyRef` is declared after `restoreView`; it is only read when `restoreView` runs, as the
   existing `if (gridReadyRef.current)` below already does. A view switch then reloads stale lists,
   at the cost of one extra request.
4. At the end of `restoreView`'s body, after the `setTimeout(…, 300)` that releases the restore
   guard, add:

```ts
      // Settles once the view's filters are on the grid, for a caller that must
      // write after them.
      return filtersWritten
```

   Every existing caller ignores the value; the embed's host filters (a later task) wait on it.

- [ ] **Step 3b: The record page's saved views**

`useGridViewState` (CustomShow's history and audit-log tabs) reads and writes the same
`${key}_gridViews` storage, so it must migrate too.

In `src/components/model-components/list/useGridViewState.ts`:

1. Imports: `import type { FieldInfo } from '../../../api/endpoints/model-info'`,
   `import { migrateFilterModel } from '../../../utils/filterModelMigration'`,
   `import { writeFilterModel } from './filters/setSelection'`.
2. Add an optional option `getFields?: () => FieldInfo[]` (with a doc comment: "The grid's field
   descriptors, so a saved filter model migrates to its columns' filters."), destructure it, and at
   the top of the hook body read it through a ref so `restoreView` keeps its identity when a caller
   passes a new function on every render:

```ts
  const getFieldsRef = useRef(getFields)
  getFieldsRef.current = getFields
```

3. In `restoreView`, replace:

```ts
      const persistedFilters = sanitizeFilterModelForViewPersistence(viewSettings?.filters ?? null)
      api.setFilterModel(Object.keys(persistedFilters).length > 0 ? persistedFilters : null)
```

   with:

```ts
      const persistedFilters = migrateFilterModel(
        sanitizeFilterModelForViewPersistence(viewSettings?.filters ?? null),
        getFieldsRef.current?.() ?? [],
      )
      void writeFilterModel(api, persistedFilters, { reloadLists: gridReadyRef.current })
```

   Without `getFields` (the audit-log tab) the field list is empty, so every saved filter passes
   through unchanged.

In `src/components/CustomShow/CustomShow.tsx`:

1. `import type { FieldInfo } from '../../api/endpoints/model-info'`.
2. Directly above `const historyViewState = useGridViewState({` (CustomShow.tsx:495) add:

```ts
  // Filled below, once the history tab's field info is known; read at restore time.
  const historyFieldsRef = useRef<FieldInfo[]>([])
```

   and pass `getFields: () => historyFieldsRef.current,` in that hook's options.
3. Directly after the `const historicalActiveFieldInfo = useGetModelFieldsQuery(…)` call
   (CustomShow.tsx:508–518; the one keyed on `historySerializerToUse`, which feeds the history
   grid — not `historicalModelFieldInfo`), add:

```ts
  historyFieldsRef.current = (historicalActiveFieldInfo.data?.fields ?? []) as FieldInfo[]
```

Neither file has its own test. The migration is F4.41–F4.43's `migrateFilterModel`; the wiring is
checked by tsc.

- [ ] **Step 4: Run and commit**

Run: `./node_modules/.bin/vitest run src/utils/__test__/filterModelMigration.cluster.test.ts src/components/model-components/list/__test__/CustomList.test.tsx`
Then compare tsc without positions:

```bash
BASELINE=/home/syscall/LUND_IT/process-admin-general-client/.claude/worktrees/multi_value_filters-baseline
./node_modules/.bin/tsc --noEmit -p tsconfig.json 2>&1 | grep "error TS" | sed -E 's/\([0-9]+,[0-9]+\)//' | sort > "$BASELINE/tsc-now.txt"
diff <(grep "error TS" "$BASELINE/tsc-before.txt" | sed -E 's/\([0-9]+,[0-9]+\)//' | sort) "$BASELINE/tsc-now.txt"
```

Expected: 3 passed (F4.41–F4.43); the `CustomList.test.tsx` failures equal those in
`$BASELINE/vitest-before.json` (one at `c2dbaa0`: F4.29); the tsc diff is empty.

```bash
git add src/utils/filterModelMigration.ts src/utils/__test__/filterModelMigration.cluster.test.ts src/components/model-components/list/CustomList.tsx src/components/model-components/list/useGridViewState.ts src/components/CustomShow/CustomShow.tsx
git commit -m "feat(views): saved filters migrate to value lists

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The filter bar

**Files:**
- Create: `src/components/model-components/list/filters/filterBarColumns.ts`
- Create: `src/components/model-components/list/filters/FilterBar.tsx`
- Create: `src/components/model-components/list/viewSettings.ts`
- Modify: `src/components/model-components/list/CustomList.tsx` (grid API state, live filter model,
  bar columns, bar rendering, view capture, discard)
- Modify: `src/components/model-components/list/CustomDatagrid.tsx` (`getMainMenuItems` with
  "Show in filter bar"; props `filterBarColumns`, `onShowInFilterBar`)
- Modify: `src/components/model-components/list/useGridViewState.ts` (`captureCurrentState` uses
  `captureViewSettings`)
- Test: `src/components/model-components/list/filters/__test__/FilterBar.cluster.test.tsx` (new)
- Test: `src/components/model-components/list/__test__/viewSettings.cluster.test.ts` (new)
- Modify: `src/components/model-components/list/__test__/CustomDatagrid.test.tsx` (F3.78)

**Interfaces:**
- Consumes (Task 1, `./filters/columnFilters`): `columnFilterKind`, `hasValueList`, `fieldsByName`,
  `ColumnFilterKind`. `fieldsByName(fields)` is the one lookup by name: where the fields endpoint
  sends a name twice (a renamed-source field is named after its source column), the descriptor
  with the value list stands for it, whatever the order. The grid gives that descriptor's column
  the name as its column id and a twin's column `<name>_<n>`.
- Consumes (Task 2): `FetchFilterValues`, `FilterValueItem`, `DEFAULT_VALUES_LIMIT`,
  `createFilterValuesFetcher` (`./filters/filterValuesClient`); `BLANK_LABEL`
  (`./filters/setFilterParams`); `selectedKeysOf`, `withSelectedKeys`, `hasCondition`,
  `writeColumnFilterModel(api, field, model)`, `forgetIntendedFilterModel(api)`
  (`./filters/setSelection`). `writeColumnFilterModel` reloads the column's Set list with the new
  keys as `selected`, sets the column model and calls `onFilterChanged`; `forgetIntendedFilterModel`
  is called once the grid reports the write applied.
- Consumes (Task 3, `CustomList.tsx`): `fieldsRef` (the field descriptors, declared directly after
  `modelFieldInfo`).
- Produces:
  - `resolveFilterBarColumns({ embedParam: string | null, savedView: unknown, listUi: unknown, valueListFields: Set<string> }): string[]`
  - `<FilterBar columns fields filterModel fetchValues onChange onRemoveColumn />`, where
    `onChange(field: string, model: Record<string, unknown> | null)`
  - `captureViewSettings(api, context): ViewSettings`, including `filterBar` (optional in the context)
  - `CustomDatagrid` props `filterBarColumns?: string[]`, `onShowInFilterBar?: (field: string) => void`
  - in `CustomList`: `gridApi` / `setGridApi`, `gridApiRef`, `resetFilterBar` (Task 5 extends them)

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
  { name: 'strategy', type: 'string', readable_name: 'Strategy', choices: [['equity', 'Equity']], value_list: true },
  { name: 'name', type: 'string', readable_name: 'Name', value_list: true },
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

F3_76b's message is what the real client rejects with: `createFilterValuesFetcher` rethrows a
refusal with the backend's own words (`'region' is not filterable.`).

`src/components/model-components/list/__test__/viewSettings.cluster.test.ts`:

```ts
/**
 * F4.44 — a saved view remembers its filter bar.
 *
 * Intent: the bar's columns are part of a view, like its columns and filters.
 * Saving and updating a view must capture them; one function captures every
 * part of a view, so the save paths cannot drift apart.
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
import React, { type JSX, useEffect, useMemo, useRef, useState } from 'react'
import { Autocomplete, Box, Chip, IconButton, TextField, Tooltip, Typography } from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import type { FieldInfo } from '../../../../api/endpoints/model-info'
import { columnFilterKind, fieldsByName, type ColumnFilterKind } from './columnFilters'
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
  // One descriptor per name: a renamed field's twin (named after its source
  // column) never hides the descriptor that has the value list.
  const byName = useMemo(() => fieldsByName(props.fields), [props.fields])
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
  const loadSeq = useRef(0)
  const [options, setOptions] = useState<FilterValueItem[]>([])
  const [truncated, setTruncated] = useState(false)
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [, rerender] = useState(0)

  const load = async (term: string): Promise<void> => {
    // Answers can arrive out of order (an earlier search finishing last): only
    // the latest request may change what the list shows.
    const seq = ++loadSeq.current
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
      if (seq !== loadSeq.current) return
      setError((failure as { message?: string } | null)?.message || 'Values could not be loaded')
      return
    }
    if (seq !== loadSeq.current) return
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
        // A search answer omits ticked keys whose label does not match; keep
        // them as options so every chip still matches one.
        options={[...options, ...value.filter((chip) => !options.some((option) => option.key === chip.key))]}
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

`JSX` is imported from `react`, as `CustomList.tsx` does: this tsconfig has no global `JSX`
namespace (TS2503).

- [ ] **Step 4: View capture**

`src/components/model-components/list/viewSettings.ts`:

```ts
/**
 * Everything a saved view stores, captured from the grid in one place.
 *
 * Creating a view and updating one used to build this object by hand, in
 * CustomList's two save handlers and again in useGridViewState (the record
 * page's grids). The filter bar's columns are part of a view now, and one
 * capture function keeps the three paths from drifting.
 */
import { sanitizeFilterModelForViewPersistence } from '../../../utils/agGridFilterStatePersistence'
import { sanitizeColumnStateForPersistence } from '../../../utils/agGridColumnStatePersistence'

export interface ViewCaptureContext {
  rowHeight: unknown
  serializer: unknown
  displaySettings: unknown
  columnFormatOverrides: unknown
  /** The filter bar's columns. The record page's grids have no bar and leave it out. */
  filterBar?: string[]
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

In `src/components/model-components/list/useGridViewState.ts`, import `captureViewSettings` from
`./viewSettings` and replace the body of `captureCurrentState` (the hand-built `captured` object)
with:

```ts
  const captureCurrentState = useCallback(() => {
    const api = gridRef.current?.api
    if (!api) return null
    const captured: Record<string, any> = captureViewSettings(api, {
      rowHeight: currentRowHeight,
      serializer: currentSerializer,
      displaySettings: undefined,
      columnFormatOverrides: undefined,
      capturePivotAggregationState,
    })
    // The record page has no filter bar, display settings or format overrides of
    // its own. Leave those keys out (and the serializer when there is none), so
    // updating a view the list saved keeps what the list stored there.
    for (const key of Object.keys(captured)) {
      if (captured[key] === undefined) delete captured[key]
    }
    return captured
  }, [currentRowHeight, currentSerializer, gridRef])
```

`handleUpdateView` there merges the capture over the stored view (`{ ...prev[key], ...captured }`),
so a key captured as `undefined` would erase what `CustomList` saved; the loop keeps today's stored
shape.

- [ ] **Step 5: Wire the list**

In `src/components/model-components/list/CustomList.tsx` (line numbers are `c2dbaa0`'s; the
earlier tasks shift them a little). Every hook below goes **above**
`if (… isLoading) return <LoadingComponent/>` (CustomList.tsx:1060–1066); hooks after that return
break the hook order. First:
- move `const asOfIso = toAsOfIsoString(asOfValue)` from line 1089 (just after
  `const handleHistoryToggle = …`) to just after `serializerToUse` (line 262, directly before
  `const modelFieldInfo = …`), unchanged;
- add `useMemo` to the React import (line 6).

1. **State and fetcher.** Directly after the `fieldsRef` lines (which follow `modelFieldInfo`):

```ts
  // ── Filter bar ── (every hook here stays above the loading return below)
  const httpClient = useRaHttpClient()
  const [gridApi, setGridApi] = useState<any>(null)
  const gridApiRef = useRef<any>(null)
  gridApiRef.current = gridApi
  const [liveFilterModel, setLiveFilterModel] = useState<Record<string, unknown>>({})
  const [filterBarColumns, setFilterBarColumns] = useState<string[]>([])

  // One descriptor per name (fieldsByName): a renamed field's twin, named after
  // its source column, neither hides nor lends a value list.
  const valueListFields = useMemo(
    () =>
      new Set(
        Array.from(fieldsByName(fieldsRef.current).values())
          .filter((f) => hasValueList(columnFilterKind(f)))
          .map((f) => f.name),
      ),
    [modelFieldInfo.data],
  )

  // `useRaHttpClient` builds a new function on every render; read it through a
  // ref so the fetcher, and the bar's lists, keep their identity.
  const httpClientRef = useRef<HttpClient>(httpClient)
  httpClientRef.current = httpClient
  const fetchBarValues = useMemo(
    () =>
      createFilterValuesFetcher({
        httpClient: (url, options) => httpClientRef.current(url, options),
        resource: activeResource || '',
        query: {
          ...(asOfIso ? { as_of: asOfIso } : {}),
          ...(serializerToUse ? { serializer: serializerToUse } : {}),
        },
      }),
    [activeResource, asOfIso, serializerToUse],
  )
```

   `asOfIso` and `serializerToUse` are the names `CustomList` already passes to `<List>` and
   `<CustomDatagrid>`. Imports:
   - `import useRaHttpClient from '../../../api/httpClients/raHttpClient'`, as `CustomDatagrid.tsx` does;
   - change `import { clearServerSideDatasourceCache } from './ServerSideDatasource'` to also import
     `type HttpClient`;
   - `import { columnFilterKind, fieldsByName, hasValueList } from './filters/columnFilters'`;
   - `import { createFilterValuesFetcher } from './filters/filterValuesClient'`;
   - `import { resolveFilterBarColumns } from './filters/filterBarColumns'`;
   - `import FilterBar from './filters/FilterBar'`;
   - `import { captureViewSettings } from './viewSettings'`;
   - extend the `./filters/setSelection` import to
     `import { forgetIntendedFilterModel, writeColumnFilterModel, writeFilterModel } from './filters/setSelection'`.

2. **Track the grid API and its filters.**
   - In `onBeforeServerDatasourceInit`, right after `restoreView(undefined, api)`, add
     `gridApiRef.current = api` and `setGridApi(api)`. Setting the ref directly closes the window
     before the re-render, in which a message for the grid would find no API.
   - In `onFirstDataRendered`'s non-SSRM branch, between its `restoreView(undefined, event?.api)`
     and its `return`, add `gridApiRef.current = event?.api` and `setGridApi(event?.api)`.
   - Then, below the fetcher from item 1:

```ts
  useEffect(() => {
    if (!gridApi) return
    const read = (): void => setLiveFilterModel({ ...(gridApi.getFilterModel?.() ?? {}) })
    const onFilterChanged = (): void => {
      // The grid has applied what the app wrote: its keys need no forcing any more.
      forgetIntendedFilterModel(gridApi)
      read()
    }
    read()
    gridApi.addEventListener?.('filterChanged', onFilterChanged)
    return () => {
      try {
        gridApi.removeEventListener?.('filterChanged', onFilterChanged)
      } catch {
        /* a destroyed grid has no listeners left */
      }
    }
  }, [gridApi])
```

   The first `read()` does not forget: when the effect runs, the Set filters of the first-load
   write may still be loading their lists, and they need the written keys as `selected`.

3. **Resolve the bar's columns.** Below that effect:

```ts
  // Discard puts the bar back to the view's columns, as it does the filters.
  const resetFilterBar = useCallback(() => {
    setFilterBarColumns(
      resolveFilterBarColumns({
        embedParam: getEmbedParam('filter_bar'),
        savedView: activeGridViewState?.[activeActiveView]?.filterBar,
        listUi: modelFieldInfo.data?.list_ui?.filter_bar,
        valueListFields,
      }),
    )
  }, [activeActiveView, activeGridViewState, modelFieldInfo.data, valueListFields])
  useEffect(() => {
    resetFilterBar()
  }, [resetFilterBar])

  const showInFilterBar = useCallback((field: string) => {
    setFilterBarColumns((columns) => (columns.includes(field) ? columns : [...columns, field]))
    setIsViewModified(true)
  }, [])
  const removeFromFilterBar = useCallback((field: string) => {
    setFilterBarColumns((columns) => columns.filter((name) => name !== field))
    setIsViewModified(true)
  }, [])
  const writeColumnFilter = useCallback((field: string, model: Record<string, unknown> | null) => {
    void writeColumnFilterModel(gridApiRef.current, field, model)
  }, [])
```

   `activeGridViewState` (not `gridViewState`) holds the history view's views when it is shown.
   `getEmbedParam('filter_bar')` reads the embed snapshot; `filter_bar` joins that snapshot in a
   later task, and reads `null` until then.

4. **Render the bar** between the toolbar `Box` and the grid, inside the same column `Box`, directly
   before `{!showAnalyticsPanel ? (<SettlingGrid …`:

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
   - In `handleUpdateView` and `handleCreateView`, replace each hand-built view object inside
     `commitGridViewState((latest) => ({ ...latest, [key]: { … } }))` with:

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
   - In `handleDiscardChanges` (CustomList.tsx:834–836), call `resetFilterBar()` after `restoreView()`.

- [ ] **Step 6: The column menu item**

In `src/components/model-components/list/CustomDatagrid.tsx`:
- Add to `CustomDatagridProps`:

```ts
  /** The filter bar's columns; the column menu offers "Show in filter bar" for the others. */
  filterBarColumns?: string[]
  onShowInFilterBar?: (field: string) => void
```

  and destructure both.
- Directly after `const fieldsString = JSON.stringify(fields)` (it reads `fields`, declared just
  above it), add:

```ts
  // The column menu can add a column to the filter bar. The bar filters a field
  // NAME, so only the column that owns the name -- its column id is the name --
  // and whose descriptor has a value list offers it. A renamed field's twin,
  // named after its source column, does not.
  const getMainMenuItems = useCallback(
    (params: any) => {
      const items = [...(params?.defaultItems ?? [])]
      const column = params?.column
      const field = column?.getColDef?.()?.field
      const owner = typeof field === 'string' ? fieldsByName(fields).get(field) : undefined
      if (
        !onShowInFilterBar ||
        !owner ||
        column?.getColId?.() !== field ||
        !hasValueList(columnFilterKind(owner)) ||
        filterBarColumns?.includes(field)
      ) {
        return items
      }
      return [...items, 'separator', { name: 'Show in filter bar', action: () => onShowInFilterBar(field) }]
    },
    [fields, filterBarColumns, onShowInFilterBar],
  )
```

  `fieldsByName`, `columnFilterKind` and `hasValueList` are already imported from
  `./filters/columnFilters`. The grid gives the owner's column no explicit id (AG uses the field
  name) and a twin's column the id `<name>_<n>`, which is what the `getColId()` check reads.
- Pass `getMainMenuItems={getMainMenuItems}` to `<BareDatagridAGClient>`, beside
  `getContextMenuItems`; it reaches AG Grid through `...rest`.

Add to `src/components/model-components/list/__test__/CustomDatagrid.test.tsx`, after the F3.83 test:

```ts
  it('F3_78: the column menu offers "Show in filter bar" for value-list columns only', () => {
    // Scenario F3.78
    // Given a listed foreign key, its renamed twin sent first, and a date-time column
    // When each column's menu is built
    // Then only the listed column's own menu offers the bar, and choosing it names the field
    const onShowInFilterBar = vi.fn()
    renderDatagrid({
      data: [{
        fields: [
          { name: 'fk_field', type: 'foreign_key', readable_name: 'FK renamed', required: false, value_list: false, target: 'currency' },
          { name: 'fk_field', type: 'foreign_key', readable_name: 'FK', required: false, value_list: true, target: 'currency' },
          { name: 'dt_field', type: 'date_time', readable_name: 'DT', required: false },
        ],
        id_field: 'id',
      }],
      onShowInFilterBar,
      filterBarColumns: [],
    } as any)
    const menu = (field: string, colId = field) =>
      capturedBareProps.getMainMenuItems({
        defaultItems: ['columnFilter'],
        column: { getColDef: () => ({ field }), getColId: () => colId },
      })
    const fkItems = menu('fk_field')
    expect(fkItems.at(-1).name).toBe('Show in filter bar')
    fkItems.at(-1).action()
    expect(onShowInFilterBar).toHaveBeenCalledWith('fk_field')
    // The twin's column (`fk_field_1`) is not the name's column.
    expect(menu('fk_field', 'fk_field_1')).toEqual(['columnFilter'])
    expect(menu('dt_field')).toEqual(['columnFilter'])
  })
```

`renderDatagrid` takes `Partial<typeof defaultProps>`, so the new props need the `as any` the file's
other tests use; without it tsc gains an error.

- [ ] **Step 7: Run tests and type-check**

Run: `./node_modules/.bin/vitest run src/components/model-components/list`, then tsc compared
without positions:

```bash
BASELINE=/home/syscall/LUND_IT/process-admin-general-client/.claude/worktrees/multi_value_filters-baseline
./node_modules/.bin/tsc --noEmit -p tsconfig.json 2>&1 | grep "error TS" | sed -E 's/\([0-9]+,[0-9]+\)//' | sort > "$BASELINE/tsc-now.txt"
diff <(grep "error TS" "$BASELINE/tsc-before.txt" | sed -E 's/\([0-9]+,[0-9]+\)//' | sort) "$BASELINE/tsc-now.txt"
```

Expected: F3.73–F3.78 (seven tests, F3_76b included) and F4.44 pass; the failing tests equal those
of `$BASELINE/vitest-before.json` for these files; the tsc diff is empty.

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
- Modify: `src/components/model-components/list/CustomList.tsx` (first-load and early host filters
  in `onBeforeServerDatasourceInit`; host message listener; the bar parameter for the opening table
  only)
- Modify: `src/components/model-components/list/CustomDatagrid.tsx` (emit `filter` after a filtered load)
- Test: `src/utils/__test__/embedFilters.cluster.test.ts` (new)
- Test: `src/utils/__test__/lexAppBridge.test.ts` (F12.110)
- Modify: `src/components/model-components/list/__test__/CustomDatagrid.test.tsx` (F12.109b)

**Interfaces:**
- Consumes (Task 1, `src/components/model-components/list/filters/columnFilters.ts`):
  `columnFilterKind`, `hasValueList`, `fieldsByName`. `fieldsByName` is the one lookup by name:
  where the fields endpoint sends a name twice (a renamed-source field is named after its source
  column), the descriptor with the value list stands for it, whatever the order.
- Consumes (Task 2, `src/components/model-components/list/filters/setSelection.ts`): `toKey`,
  `selectedKeysOf`, `intendedFilterModel(api)` (the model the app last wrote, else
  `api.getFilterModel()`), `writeFilterModel(api, model, { reloadLists? })` (synchronous
  `setFilterModel` without `reloadLists`; with it, existing Set filters first reload their lists
  with the written keys as `selected`).
- Consumes (Task 3, `CustomList.tsx`): `fieldsRef`; `restoreView(viewKey?, apiOverride?)` returns
  the promise of its filter write.
- Consumes (Task 4, `CustomList.tsx`): `gridApi` / `setGridApi`, `gridApiRef` (set directly in
  `onBeforeServerDatasourceInit`), `resetFilterBar` (resolves the bar's columns from
  `getEmbedParam('filter_bar')`, the view and `list_ui`).
- Produces:
  - `decodeFiltersToken(token: string | null): HostFilters | null`
  - `applyHostFilters(current, hostFilters, fields): Record<string, unknown>`
  - `filtersForEvent(filterModel): Record<string, Array<string | null>>`
  - `isInitialEmbedResource(resource: string): boolean`
  - `takeInitialEmbedFilters(resource: string): HostFilters | null`, consumed once
  - `takePendingHostFilters(): HostFilters | null`, consumed once
  - `onHostFilters(handler: (filters: HostFilters) => boolean): () => void`
  - `__resetHostFiltersForTests(): void`
  - event `filter` with payload `{ resource, filters, filter_model, row_count }`

- [ ] **Step 1: Write the failing tests**

`src/utils/__test__/embedFilters.cluster.test.ts`:

```ts
/**
 * F12.104–F12.109, F12.112 — a Streamlit page sets an embed's filters and hears them change.
 *
 * Intent: lex_view(filters=...) puts the first filters in the URL as a
 * base64url token and posts later changes as a `filter` message, so the frame
 * never reloads. Named fields replace their column filters (a list → a value
 * list, null → cleared, [] → no rows); fields the user filtered stay; a column
 * without a value list is ignored with a warning. Messages count only from the
 * parent frame with the host tag and a protocol version this build knows; one
 * that arrives before any list is kept for the first list that mounts. The embed
 * reports changes as a gated `filter` event. A regression lets a page widen
 * nothing it shouldn't, but it does make linked tables silently stop following
 * each other.
 *
 * Cluster F12 (embed_streamlit), batch 12u, scenarios F12.104–F12.109 and F12.112
 * (F12.110 is in lexAppBridge.test.ts, F12.109's second half in CustomDatagrid.test.tsx,
 * F12.111 in e2e/value-lists.spec.ts). Tier U.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  __resetHostFiltersForTests,
  applyHostFilters,
  decodeFiltersToken,
  filtersForEvent,
  onHostFilters,
  takeInitialEmbedFilters,
  takePendingHostFilters,
} from '../embedFilters'
import { __resetEmbedParamCacheForTests, LEX_BRIDGE_VERSION } from '../lexAppBridge'

const fields: any[] = [
  { name: 'strategy', type: 'string', readable_name: 'Strategy', choices: [['equity', 'Equity']], value_list: true },
  { name: 'name', type: 'string', readable_name: 'Name', value_list: true },
  { name: 'nav', type: 'float', readable_name: 'NAV' },
]

const token = (value: unknown): string =>
  btoa(String.fromCharCode(...Array.from(new TextEncoder().encode(JSON.stringify(value)))))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')

afterEach(() => {
  __resetHostFiltersForTests()
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
    const handler = vi.fn(() => true)
    const stop = onHostFilters(handler)
    const post = (data: unknown, source: unknown) =>
      window.dispatchEvent(new MessageEvent('message', { data, source: source as any }))
    post({ source: 'lex-app-host', version: 1, type: 'filter', payload: { filters: { strategy: ['equity'] } } }, window.parent)
    post({ source: 'someone-else', type: 'filter', payload: { filters: { strategy: ['x'] } } }, window.parent)
    post({ source: 'lex-app-host', type: 'theme', payload: { mode: 'dark' } }, window.parent)
    post({ source: 'lex-app-host', type: 'filter', payload: { filters: ['x'] } }, window.parent)
    post({ source: 'lex-app-host', type: 'filter', payload: { filters: { strategy: ['y'] } } }, null)
    // A protocol version newer than this build's is refused, as the component refuses ours.
    post({ source: 'lex-app-host', version: LEX_BRIDGE_VERSION + 1, type: 'filter', payload: { filters: { strategy: ['z'] } } }, window.parent)
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

  it('F12_112: a host message before any list is kept for the first one', () => {
    // Scenario F12.112
    // Given the host posts right after the frame loads, before a list mounts
    // When a list subscribes, and when it cannot apply a later message yet
    // Then the early filters are handed to it once (the latest wins), and the
    // unapplied one is kept for the next list
    const post = (filters: unknown) =>
      window.dispatchEvent(new MessageEvent('message', {
        data: { source: 'lex-app-host', version: 1, type: 'filter', payload: { filters } },
        source: window.parent as any,
      }))
    post({ strategy: ['equity'] })
    post({ strategy: ['fixed_income'] })
    expect(takePendingHostFilters()).toEqual({ strategy: ['fixed_income'] })
    expect(takePendingHostFilters()).toBeNull()
    const stop = onHostFilters(() => false)
    post({ name: ['Alpha'] })
    expect(takePendingHostFilters()).toEqual({ name: ['Alpha'] })
    stop()
  })
})
```

`String.fromCharCode(...Array.from(bytes))` and not `...bytes`: spreading a `Uint8Array` is TS2802
under this tsconfig.

Add to `src/utils/__test__/lexAppBridge.test.ts`, inside its main `describe` (after the last test).
That file's `beforeEach` replaces `window.location` with a `URL` object, so the test sets its URL
the same way (`history.replaceState` would change nothing the snapshot reads):

```ts
  it('F12_110: the filter event is gated by emit_filter and the new params are snapshotted', () => {
    // Scenario F12.110
    Object.defineProperty(window, 'location', {
      writable: true,
      configurable: true,
      value: new URL('http://localhost:3000/fund?embed=true&emit_filter=true&filter_bar=strategy&filters=abc#embed'),
    })
    __resetEmbedParamCacheForTests()
    expect(isEventOptedIn('filter')).toBe(true)
    expect(getEmbedParam('filter_bar')).toBe('strategy')
    expect(getEmbedParam('filters')).toBe('abc')
  })
```

`isEventOptedIn`, `getEmbedParam` and `__resetEmbedParamCacheForTests` are already imported there.
Run both files to see them fail.

- [ ] **Step 2: The bridge**

In `src/utils/lexAppBridge.ts`:
- add `| 'filter'` to `LexBridgeEventType`;
- add `filter: 'emit_filter',` to `FLAG_MAP`;
- add `'filters', 'filter_bar'` to `SNAPSHOT_EXACT`.

- [ ] **Step 3: `embedFilters.ts`**

`src/utils/embedFilters.ts`:

```ts
/**
 * Filters a Streamlit page sets on an embedded table, and the event it hears back.
 *
 * lex_view(filters=...) sends the first filters in the URL as a base64url JSON
 * token and posts later changes as `{source: 'lex-app-host', version: 1, type:
 * 'filter'}`. Named fields replace their column filters; fields the user filtered
 * stay. The page's filters are ordinary grid filters: the server applies the same
 * read permissions to every request, so a page can never widen what a user sees.
 */
import type { FieldInfo } from '../api/endpoints/model-info'
import {
  columnFilterKind,
  fieldsByName,
  hasValueList,
} from '../components/model-components/list/filters/columnFilters'
import { selectedKeysOf, toKey } from '../components/model-components/list/filters/setSelection'
import { LEX_BRIDGE_VERSION, getEmbedParam, getInitialEmbedHref } from './lexAppBridge'

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
  // One descriptor per name: a renamed field's twin (named after its source
  // column) never hides the descriptor that has the value list.
  const byName = fieldsByName(fields)
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

/** Whether `resource` is the table the embed opened with: its first path names it. */
export function isInitialEmbedResource(resource: string): boolean {
  try {
    return new URL(getInitialEmbedHref()).pathname.split('/').filter(Boolean).includes(resource)
  } catch {
    return false
  }
}

let initialFiltersTaken = false

/** First-load filters, once per page load, for the table the embed opened with. */
export function takeInitialEmbedFilters(resource: string): HostFilters | null {
  if (initialFiltersTaken || !isInitialEmbedResource(resource)) return null
  const filters = decodeFiltersToken(getEmbedParam('filters'))
  if (filters) initialFiltersTaken = true
  return filters
}

/** The parent page's `filter` message, or null for anything else. */
function hostFiltersOf(event: MessageEvent): HostFilters | null {
  if (event.source !== window.parent) return null
  const data = event.data
  if (!data || typeof data !== 'object') return null
  if (data.source !== 'lex-app-host' || data.type !== 'filter') return null
  // The component's own rule for inbound envelopes: only a version this build knows.
  if (typeof data.version === 'number' && data.version > LEX_BRIDGE_VERSION) return null
  const filters = data.payload?.filters
  return filters && typeof filters === 'object' && !Array.isArray(filters) ? (filters as HostFilters) : null
}

let pendingHostFilters: HostFilters | null = null
let hostFiltersHandler: ((filters: HostFilters) => boolean) | null = null

// Listening starts when the app loads, before any list exists: lex_view's
// component posts the host's filters right after every frame load (spec
// Amendment 11), and a token over 2,000 characters arrives no other way. A
// message no list can apply yet is kept -- the latest wins -- for the next list
// that mounts.
if (typeof window !== 'undefined') {
  window.addEventListener('message', (event: MessageEvent) => {
    const filters = hostFiltersOf(event)
    if (!filters) return
    if (!hostFiltersHandler?.(filters)) pendingHostFilters = filters
  })
}

/** Host filters that arrived while no list could apply them; handed out once. */
export function takePendingHostFilters(): HostFilters | null {
  const filters = pendingHostFilters
  pendingHostFilters = null
  return filters
}

/**
 * Filters the parent page posts while a list is mounted. The handler returns false
 * when it cannot apply them yet (no grid API); they are then kept for
 * `takePendingHostFilters`.
 */
export function onHostFilters(handler: (filters: HostFilters) => boolean): () => void {
  hostFiltersHandler = handler
  return () => {
    if (hostFiltersHandler === handler) hostFiltersHandler = null
  }
}

export function __resetHostFiltersForTests(): void {
  initialFiltersTaken = false
  pendingHostFilters = null
  hostFiltersHandler = null
}
```

`getInitialEmbedHref()` returns the snapshot taken when the module loaded. In F12.107 the test
changes the URL afterwards, so `__resetEmbedParamCacheForTests()` must also reset `_initialHref`
(it does, see `lexAppBridge.ts`). `getInitialEmbedHref` then falls back to the current
`window.location.href`.

- [ ] **Step 4: Apply them in the list**

In `src/components/model-components/list/CustomList.tsx`:

1. Imports:

```ts
import {
  applyHostFilters,
  isInitialEmbedResource,
  onHostFilters,
  takeInitialEmbedFilters,
  takePendingHostFilters,
} from '../../../utils/embedFilters'
```

   and add `intendedFilterModel` to the existing import from `./filters/setSelection`. Change
   `const { withEmbedParams } = useEmbedContext()` (CustomList.tsx:115 at `c2dbaa0`) to
   `const { withEmbedParams, isEmbed: isEmbeddedApp } = useEmbedContext()`: it is the router-proof
   snapshot (`getEmbedParam('embed')`). Leave the `isEmbed` const further down (built from
   `searchParams`, CustomList.tsx:1031–1036 at `c2dbaa0`) where it is; it still feeds
   `<CustomDatagrid isEmbed>`.
2. Beside `gridApiRef`, add:

```ts
  // Host filters applied when this list's grid started, until its one deferred restore.
  const hostModelRef = useRef<Record<string, unknown> | null>(null)
```

3. Replace `onBeforeServerDatasourceInit` with the version below. Unchanged from today apart from
   the embed block and the deferred timer:

```ts
  const onBeforeServerDatasourceInit = useCallback(
    (api: any) => {
      restoreView(undefined, api)
      gridApiRef.current = api
      setGridApi(api)
      if (isEmbeddedApp) {
        // The URL's first-load filters, then a message that came before this list
        // (Amendment 11). Both win over the saved view, the message last.
        const initial = takeInitialEmbedFilters(activeResource || '')
        const pending = takePendingHostFilters()
        if (initial || pending) {
          let model = intendedFilterModel(api)
          if (initial) model = applyHostFilters(model, initial, fieldsRef.current)
          if (pending) model = applyHostFilters(model, pending, fieldsRef.current)
          hostModelRef.current = model
          void writeFilterModel(api, model)
        }
      }
      gridReadyRef.current = true
      skipNextViewRestoreRef.current = true

      if (deferredServerDatasourceRestoreTimeoutRef.current) {
        clearTimeout(deferredServerDatasourceRestoreTimeoutRef.current)
      }

      const activeViewSettings = gridViewStateRef.current?.[activeViewRef.current]
      const shouldRunDeferredRestore = viewNeedsPostDatasourceRestore(activeViewSettings)

      deferredServerDatasourceRestoreTimeoutRef.current = setTimeout(() => {
        // Taken first, so the host's model never outlives this one deferred pass.
        const hostModel = hostModelRef.current
        hostModelRef.current = null
        deferredServerDatasourceRestoreTimeoutRef.current = null
        // If onGridReady happened after the initial effect pass, avoid leaking this guard.
        skipNextViewRestoreRef.current = false

        if (!shouldRunDeferredRestore) return
        if (activeGridRef.current?.api !== api) return

        const restored = restoreView(undefined, api)
        // restoreView puts the saved view's filters back, and with the grid up it
        // writes them asynchronously; the host's win again once that write landed.
        if (hostModel) {
          void Promise.resolve(restored).then(() => writeFilterModel(api, hostModel, { reloadLists: true }))
        }
      }, 0)
    },
    [activeGridRef, activeResource, isEmbeddedApp, restoreView, viewNeedsPostDatasourceRestore],
  )
```

   Both writes in the embed block happen before any `await`, so the datasource attaches with the
   host's filters. The deferred restore runs only for grouped or pivot views; with the grid up,
   `restoreView` writes the view's filters after an `await`, so a synchronous host write there
   would be overwritten.

4. Directly after `onBeforeServerDatasourceInit` (above the loading return), add the listener:

```ts
  // Host `filter` messages while this list is mounted (Amendment 6). One that
  // comes before the grid exists is kept, and taken above when it starts.
  useEffect(() => {
    if (!isEmbeddedApp) return
    return onHostFilters((filters) => {
      const api = gridApiRef.current
      if (!api) return false
      // setFilterModel fires filterChanged itself; no second refresh.
      void writeFilterModel(api, applyHostFilters(intendedFilterModel(api), filters, fieldsRef.current), {
        reloadLists: true,
      })
      return true
    })
  }, [isEmbeddedApp])
```

   The merge starts from `intendedFilterModel(api)`, not `api.getFilterModel()`: while Set filters
   are still loading, `getFilterModel()` lacks them, and the merge would race with the saved view.

5. The embed's `filter_bar` is for the table it opened with (spec Amendment 6, §7). In
   `resetFilterBar`, change `embedParam: getEmbedParam('filter_bar'),` to:

```ts
        // The embed's bar is for the table it opened with; another table reached
        // inside the embed keeps its own.
        embedParam: isInitialEmbedResource(activeResource || '') ? getEmbedParam('filter_bar') : null,
```

   and add `activeResource` to its dependency list.

- [ ] **Step 5: Emit the event**

In `src/components/model-components/list/CustomDatagrid.tsx`:

1. Import `filtersForEvent` from `../../../utils/embedFilters`.
2. At module scope, below `DATE_CONDITION_PARAMS`:

```ts
/** The `filter` bridge event goes out this long after a filtered load settles… */
const FILTER_EVENT_DEBOUNCE_MS = 300
/** …or this long after the filter change when no request follows (a cached answer). */
const FILTER_EVENT_FALLBACK_MS = 2000
```

3. Directly **above** `const handleServerSideLoadingChange = useCallback(` (CustomDatagrid.tsx:891 at
   `c2dbaa0`; its dependency list will name `scheduleFilterEvent`, so that must be declared first),
   add:

```ts
  // ── The `filter` bridge event: one per filtered load, with its row count ──
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

  useEffect(() => () => {
    if (filterEventTimerRef.current) clearTimeout(filterEventTimerRef.current)
  }, [])
```

4. In `handleServerSideLoadingChange`, the not-loading path is the tail after
   `if (isLoading) { …; return }`. Append to it, after
   `setServerSideLoadCount((previous) => Math.max(0, previous - 1))`:

```ts
      if (filterEventPendingRef.current) scheduleFilterEvent(FILTER_EVENT_DEBOUNCE_MS)
```

   and add `scheduleFilterEvent` to its dependencies.
5. In `handleTotalRowCountChanged`, set `latestTotalRef.current = total` before `setTableRowCount`.
6. Pass `onFilterChanged={handleFilterChanged}` instead of `onFilterChanged={onGridStateChange}`.

In `src/components/model-components/list/__test__/CustomDatagrid.test.tsx`, make the bridge's
`emitLexEvent` a spy. The file uses the real bridge for its embed-parameter tests, so mock only that
one export, beside the other `vi.mock` blocks:

```ts
// Spy on the bridge's emitter only; the embed-parameter tests use the real snapshot.
vi.mock('../../../../utils/lexAppBridge', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../../utils/lexAppBridge')>()),
  emitLexEvent: vi.fn(),
}))
```

Add `emitLexEvent` to the file's existing import from `../../../../utils/lexAppBridge`, and import
`createServerSideDatasource` from `../ServerSideDatasource` (already mocked). `act` is already
imported from `@testing-library/react`. Then add, after the `F3_78` test (or at the end of the main
`describe` if it is not there):

```ts
  it('F12_109b: a filtered load reports one filter event with its row count', () => {
    // Scenario F12.109 (second half)
    // Given an embed that opted in to filter events
    // When a filter change is followed by a load that reports 2 rows
    // Then exactly one filter event goes out, carrying the filters and the count
    const originalLocation = window.location
    vi.useFakeTimers()
    try {
      Object.defineProperty(window, 'location', {
        writable: true,
        value: {
          ...originalLocation,
          search: '?embed=true&emit_filter=true',
          href: 'http://localhost:3000/fund?embed=true&emit_filter=true#embed',
        },
      })
      __resetEmbedParamCacheForTests()
      vi.mocked(emitLexEvent).mockClear()
      const gridRef = React.createRef<any>()
      renderDatagrid({ useServerSide: true, gridRef } as any)
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
    } finally {
      vi.useRealTimers()
      Object.defineProperty(window, 'location', { writable: true, value: originalLocation })
      __resetEmbedParamCacheForTests()
    }
  })
```

The URL is set with the file's own `Object.defineProperty(window, 'location', …)` pattern (as in
its `hide_actions` describe), and restored in the `finally` with the real timers.

- [ ] **Step 6: Run tests and type-check**

Run: `./node_modules/.bin/vitest run src/utils src/components/model-components/list`, then tsc
compared without positions:

```bash
BASELINE=/home/syscall/LUND_IT/process-admin-general-client/.claude/worktrees/multi_value_filters-baseline
./node_modules/.bin/tsc --noEmit -p tsconfig.json 2>&1 | grep "error TS" | sed -E 's/\([0-9]+,[0-9]+\)//' | sort > "$BASELINE/tsc-now.txt"
diff <(grep "error TS" "$BASELINE/tsc-before.txt" | sed -E 's/\([0-9]+,[0-9]+\)//' | sort) "$BASELINE/tsc-now.txt"
```

Expected: F12.104–F12.109, F12.112 (`embedFilters.cluster.test.ts`, 7 tests), F12.110 and F12.109b
pass; the failing tests equal those of `$BASELINE/vitest-before.json` for these directories; the
tsc diff is empty.

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
`translateFilterModel`, do the task. At `c2dbaa0` it does. Open PR #473 (stacked on #472) also
edits `ServerSideDatasource.ts`; if it has landed on the base branch, expect a merge conflict here.

"Select All Records" always takes this GET path (`CustomDatagrid.getAllSelectableIds` →
`fetchAllIds` / `fetchAll`), and so does group-row selection (`getSelectableIdsByGroupRoute`). The
grid's own rows go through the POST, whose server translator already handles Set and Multi models.

**Files:**
- Modify: `src/components/model-components/list/ServerSideDatasource.ts` (`matchesNothing`,
  `selectionVariants`, `translateFilterModel`'s Multi branch, `translateSingleFilter`'s `set` case,
  `getAllSelectableIds`, `getSelectableIdsByGroupRoute`, `getRows`)
- Test: `src/components/model-components/list/__test__/ServerSideDatasource.test.ts` (two cases)

**Interfaces:**
- Consumes: the filter-model shapes written elsewhere in this plan. A Set is
  `{filterType: 'set', values: [key | null, …]}`; a Multi is
  `{filterType: 'multi', filterModels: [condition | null, set | null]}`. The filter bar never writes
  an empty Set, but a host's `[]` does (`{filterType: 'set', values: []}`, alone or as a Multi's
  second half), and means "no rows".
- Produces: `matchesNothing(filterModel): boolean`,
  `selectionVariants(filterModel): Array<Record<string, any>>` (both exported).

- [ ] **Step 1: Write the failing tests**

Add to `src/components/model-components/list/__test__/ServerSideDatasource.test.ts`, inside
`describe('flat mode (no grouping)', …)`, after the test
`'translates date "inRange" filter to Django __date__gte / __date__lte'`. Its
`createMockHttpClient` answers POST with 404, which forces the GET fallback that
"Select All Records" also uses. Add `import { serializeGroupRoute } from '../groupSelectionUtils'`
to the file's imports.

```ts
    // F3.79–F3.80 — "Select All Records" and the GET fallback honour value lists.
    // Cluster F3 (grid_read), batch 3o. Tier U.
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
      // Given Set filters with several values, with only a blank, and with values and a blank
      // When rows are fetched through the GET fallback, and all selectable ids are asked for
      // Then several values become repeated __in keys, a blank becomes __isnull, and
      // "values or blank" is asked as two requests whose ids are joined
      expect(await getUrl({ strategy: { filterType: 'set', values: ['equity', 'fixed_income'] } })).toContain('strategy__in=equity&strategy__in=fixed_income')
      expect(await getUrl({ currency: { filterType: 'set', values: [null] } })).toContain('currency__isnull=true')
      expect(await getUrl({ name: { filterType: 'multi', filterModels: [{ filterType: 'text', type: 'contains', filter: 'a' }, { filterType: 'set', values: ['Alpha'] }] } }))
        .toMatch(/name__icontains=a.*name=Alpha|name=Alpha.*name__icontains=a/)
      const byUrl = vi.fn(async (url: string) => ({ json: { ids: url.includes('currency__isnull=true') ? [2] : [1] } }))
      const { datasource: ds } = createTestDatasource({ httpClient: byUrl as any })
      expect(await ds.getAllSelectableIds({ filterModel: { currency: { filterType: 'set', values: ['3', null] } } })).toEqual([1, 2])
    })

    it('F3_80: nothing ticked selects nothing, without asking the server', async () => {
      // Scenario F3.80
      // Given a Set filter with an empty selection
      // When all selectable ids are asked for, a group's ids are asked for, and rows are fetched
      // Then no ids and no rows come back, and no GET is sent
      const httpClient = createMockHttpClient([{ id: 1 }])
      const { datasource } = createTestDatasource({ httpClient })
      const empty = { strategy: { filterType: 'set', values: [] } }
      expect(await datasource.getAllSelectableIds({ filterModel: empty })).toEqual([])
      expect(await datasource.getSelectableIdsByGroupRoute({ filterModel: empty, rowGroupFields: ['strategy'], groupRoutes: [['equity']] }))
        .toEqual({ [serializeGroupRoute(['equity'])]: [] })
      const { params } = createGetRowsParams({ filterModel: empty })
      await datasource.getRows(params)
      expect(params.success).toHaveBeenCalledWith({ rowData: [], rowCount: 0 })
      const gets = (httpClient as ReturnType<typeof vi.fn>).mock.calls.filter((c: any[]) => !c[1]?.method || c[1].method === 'GET')
      expect(gets).toHaveLength(0)
    })
```

`params.success` is the success spy `createGetRowsParams` builds.

- [ ] **Step 2: Implement**

In `src/components/model-components/list/ServerSideDatasource.ts`:

1. Add above `translateFilterModel`:

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

/**
 * Filter models whose GET translations, joined, select what `filterModel` does.
 * A Set ticking values AND (Blank) means "values OR blank", which one set of
 * query parameters cannot say, so it becomes two models. Capped at four (two
 * such columns); past that the values half is kept and a warning says so.
 */
export function selectionVariants(filterModel: Record<string, any>): Array<Record<string, any>> {
  const isBlank = (value: unknown) => value === null || value === undefined || value === ''
  let variants: Array<Record<string, any>> = [filterModel]
  for (const [field, node] of Object.entries(filterModel ?? {})) {
    const children: any[] = node?.filterType === 'multi' && Array.isArray(node.filterModels) ? node.filterModels : []
    const setNode = node?.filterType === 'set' ? node : children.find((child) => child?.filterType === 'set')
    const values: unknown[] = Array.isArray(setNode?.values) ? setNode.values : []
    if (!values.some(isBlank) || values.every(isBlank)) continue
    if (variants.length >= 4) {
      console.warn(`[filters] Select All Records: "${field}" ticks values and (Blank); its blank rows are left out`)
      continue
    }
    const keep = (wanted: (value: unknown) => boolean) => {
      const half = { ...setNode, values: values.filter(wanted) }
      return setNode === node ? half : { ...node, filterModels: children.map((child) => (child === setNode ? half : child)) }
    }
    variants = variants.flatMap((variant) => [
      { ...variant, [field]: keep((value) => !isBlank(value)) },
      { ...variant, [field]: keep(isBlank) },
    ])
  }
  return variants
}
```

2. In `translateFilterModel`, after `if (!model) continue` and before the `condition1/condition2`
   branch:

```ts
    if (model.filterType === 'multi' && Array.isArray(model.filterModels)) {
      for (const child of model.filterModels) {
        if (child) Object.assign(params, translateSingleFilter(field, child))
      }
      continue
    }
```

3. Replace the whole `case 'set': { … }` of `translateSingleFilter` with:

```ts
    case 'set': {
      // A key is a stored value as a string; null -- or "", which AG's Set filter
      // and the backend's plain-text columns both treat as blank -- is blank.
      const isBlank = (value: unknown) => value === null || value === undefined || value === ''
      const values = Array.isArray(model.values) ? model.values : []
      const present = values.filter((value: unknown) => !isBlank(value))
      if (present.length === 0) {
        // Only blank ticked (callers stop an empty selection with matchesNothing).
        if (values.length > 0) params[`${field}__isnull`] = 'true'
        break
      }
      // "values OR blank" is no single set of query parameters: Select All Records
      // asks once per half (selectionVariants); here the values half is written.
      if (present.length === 1) params[field] = present[0]
      else params[`${field}__in`] = present // repeated keys: a value with a comma stays whole
      break
    }
```

   A single real boolean (`AGBooleanFilter`'s `{filterType: 'set', values: [false]}`) still becomes
   `field=false`. `query-string` writes the array as repeated keys
   (`strategy__in=equity&strategy__in=fixed_income`), and the backend reads repeated keys as one
   `__in` list.
4. In `getAllSelectableIds`:
   - as its first statement, `if (matchesNothing(filterModel as Record<string, any>)) return []`;
   - then `const variants = selectionVariants((filterModel ?? {}) as Record<string, any>)`;
   - delete the now-unused `const filterParams = filterModel ? translateFilterModel(…) : {}`;
   - fast path: `const ids = (await Promise.all(variants.map((variant) => fetchAllIds(ordering, translateFilterModel(variant))))).flat()`;
   - row path: `const allData: any[] = []` then
     `for (const variant of variants) allData.push(...(await fetchAll(ordering, translateFilterModel(variant))))`.
   The existing de-duplication stays.
5. In `getSelectableIdsByGroupRoute`, after the `if (rowGroupFields.length === 0 || normalizedGroupRoutes.length === 0) { return {} }`
   guard:

```ts
      if (matchesNothing(filterModel as Record<string, any>)) {
        return Object.fromEntries(normalizedGroupRoutes.map((route) => [serializeGroupRoute(route), []]))
      }
```

6. In `getRows`, immediately before `const isGrouped = rowGroupCols.length > 0`:

```ts
        if (matchesNothing(request.filterModel as Record<string, any>)) {
          if (totalRowCount !== 0) {
            totalRowCount = 0
            onTotalChanged?.(0)
          }
          params.success({ rowData: [], rowCount: 0 })
          return
        }
```

- [ ] **Step 3: Run, commit**

Run: `./node_modules/.bin/vitest run src/components/model-components/list/__test__/ServerSideDatasource.test.ts`,
then tsc compared without positions:

```bash
BASELINE=/home/syscall/LUND_IT/process-admin-general-client/.claude/worktrees/multi_value_filters-baseline
./node_modules/.bin/tsc --noEmit -p tsconfig.json 2>&1 | grep "error TS" | sed -E 's/\([0-9]+,[0-9]+\)//' | sort > "$BASELINE/tsc-now.txt"
diff <(grep "error TS" "$BASELINE/tsc-before.txt" | sed -E 's/\([0-9]+,[0-9]+\)//' | sort) "$BASELINE/tsc-now.txt"
```

Expected: every test passes (the 16 existing and F3_79, F3_80); the tsc diff is empty.

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
- Modify: `e2e/grid-filters.spec.ts` (F3.16 drives the choices column's value list)
- Modify: `e2e/e2e_project/Funds/Fund.py` (`filter_bar = ["strategy", "currency"]`)

On the e2e backend every `Fund` and `Position` column the helpers filter is model-backed, so the
fields endpoint marks it `value_list: true`: the foreign keys (`currency`, `fund`), `is_active` and
the choices column `strategy` get a Set filter; `name`, `instrument` and `inception_date` get a
Multi filter (condition first, value list second); `nav` and `market_value` keep the number filter.

- [ ] **Step 1: Helpers**

In `e2e/helpers/grid.ts`, in `openColumnFilter`, widen both `.ag-filter-wrapper, .ant-select`
selectors to `.ag-filter-wrapper, .ag-set-filter, .ant-select`. Add:

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
to say they drive the value list now (boolean labels are the server's "Yes" / "No"; a foreign key's
label is `str(related)`):

```ts
export async function applyBooleanFilter(page: Page, colId: string, value: boolean): Promise<void> {
  await applySetFilter(page, colId, [value ? 'Yes' : 'No'])
}

export async function applyForeignKeyFilter(page: Page, colId: string, optionLabel: string): Promise<void> {
  await applySetFilter(page, colId, [optionLabel])
}
```

In `e2e/grid-filters.spec.ts`, F3.16 (line 245) filters the choices column `strategy` with
`applyTextFilter(page, 'strategy', 'Equals', 'equity')`. A choices column is Set-only now: it has no
operator combobox. Replace that step with `await applySetFilter(page, 'strategy', ['Equity'])`, add
`applySetFilter` to the file's import from `./helpers/grid`, and keep the assertion.

- [ ] **Step 2: A default bar on the e2e Fund list**

In `e2e/e2e_project/Funds/Fund.py`, inside `class Fund`, after the fields:

```python
    # The list's default filter-bar columns (list_ui.filter_bar).
    filter_bar = ["strategy", "currency"]
```

The Fund list's screenshots in `e2e/docshots.spec.ts` now show the bar; the documentation images
are refreshed in the docs PR, not here. Say so in this PR's body.

- [ ] **Step 3: Run the existing filter journeys**

Prepare the backend per the e2e notes:
- a `lex` wrapper on `PATH` whose `PYTHONPATH` points at the backend worktree
  `/home/syscall/Documents/lex/.claude/worktrees/lex_multi_filter` (branch `feat/multi-value-filters`,
  which carries the `filter_values` endpoint and the fields endpoint's `value_list`);
- migrate and seed by hand;
- `LEX_E2E_KEEP_DB=1`.

Then run:
`CI=1 LEX_E2E_KEEP_DB=1 NPM_MARMELAB_TOKEN=x npx playwright test e2e/grid-filters.spec.ts e2e/grid-combined.spec.ts e2e/datagrid.spec.ts`

Expected:
- Every boolean and foreign-key scenario passes through the new helpers, and F3.16 through
  `applySetFilter`.
- Text and date scenarios pass: their combobox and input are the Multi filter's first half.

If a selector misses, read the rendered DOM in the failing trace and adjust the selector. Never
change an assertion.

- [ ] **Step 4: Commit**

```bash
git add e2e/helpers/grid.ts e2e/grid-filters.spec.ts e2e/e2e_project/Funds/Fund.py
git commit -m "test(e2e): filter helpers drive value lists

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: End-to-end journeys, the test plan, the PR

**Files:**
- Create: `e2e/value-lists.spec.ts`
- Plan: `docs/test-plan/clusters/F03-grid_read/{allocation.yaml,batches.md,cluster.md}`,
  `docs/test-plan/clusters/F04-grid_views/{allocation.yaml,batches.md,cluster.md}`,
  `docs/test-plan/clusters/F12-embed_streamlit/{allocation.yaml,batches.md,cluster.md}`; a session
  fragment in `docs/test-plan/progress/sessions/`

**Interfaces:**
- Consumes (Tasks 1–7): the bar's `data-testid='filter-bar'`, its comboboxes labelled with the
  column's readable name and its remove buttons named "Remove <Name> from the filter bar"; the e2e
  Fund list's default bar `["strategy", "currency"]`; the helpers `navigateToGrid`,
  `expectFilteredSet`, `openColumnFilter` (`e2e/helpers/grid.ts`) and `appFrame`, `bridgeByType`,
  `openEmbedHost` (`e2e/helpers/embedHost.ts`); the URL parameters `filters` / `emit_filter`; the
  host message `{source: 'lex-app-host', version: 1, type: 'filter', payload: {filters}}`; the
  bridge event `filter` with `payload.filters` and `payload.row_count`.
- Every scenario ID used in a test is in the shards below, and the reverse:
  - F3.66–F3.83 (27 tests: 26 Vitest, F3.81 Playwright);
  - F4.41–F4.44 (4 tests);
  - F12.104–F12.112 (10 tests: 9 Vitest, F12.111 Playwright).

- [ ] **Step 1: The journeys**

Seed facts: five funds.

| Strategy | Funds | Currency |
|---|---|---|
| equity | Alpha Growth, Delta Equity | USD |
| fixed_income | Beta Income, Epsilon Bond | EUR |
| multi_asset | Gamma Multi | GBP |

A foreign key's value-list label is `str(related)`, and `Currency.__str__` returns its code, so the
currency options read "USD", "EUR", "GBP".

```ts
/**
 * F3.81 (E) and F12.111 (E) — value lists narrow each other in the grid, and a
 * host page drives an embed's filters without reloading it.
 */
import { expect, test } from '@playwright/test'
import { expectFilteredSet, navigateToGrid, openColumnFilter } from './helpers/grid'
import { appFrame, bridgeByType, openEmbedHost } from './helpers/embedHost'

const token = (value: unknown): string =>
  Buffer.from(JSON.stringify(value)).toString('base64url')

test('F3.81: the bar narrows the grid and the other lists', { tag: ['@grid_read', '@F03'] }, async ({ page }) => {
  await navigateToGrid(page, 'fund')
  const bar = page.getByTestId('filter-bar')
  // By role: the label text is also inside each remove button's accessible name.
  const strategy = bar.getByRole('combobox', { name: 'Strategy', exact: true })
  await expect(strategy).toBeVisible({ timeout: 20_000 })

  await strategy.click()
  await page.getByRole('option', { name: /^Equity/ }).click()
  await page.keyboard.press('Escape')
  await expectFilteredSet(page, 'name', ['Alpha Growth Fund', 'Delta Equity Fund'])

  await bar.getByRole('combobox', { name: 'Currency', exact: true }).click()
  const options = page.getByRole('option')
  await expect(options).toHaveCount(1)
  // A foreign key is labelled with str(related); Currency.__str__ is its code.
  await expect(options.first()).toContainText('USD')
  await expect(options.first()).toContainText('2')

  await page.keyboard.press('Escape')
  // The column menu loads its list under Equity (USD only); the bar then
  // clears Equity and ticks EUR: the grid must take EUR all the same.
  await openColumnFilter(page, 'currency')
  await page.keyboard.press('Escape')
  await strategy.click()
  await page.getByRole('option', { name: /^Equity/ }).click() // untick
  await page.keyboard.press('Escape')
  await bar.getByRole('combobox', { name: 'Currency', exact: true }).click()
  await page.getByRole('option', { name: /^EUR/ }).click()
  await page.keyboard.press('Escape')
  await expectFilteredSet(page, 'name', ['Beta Income Fund', 'Epsilon Bond Fund'])
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

The second half of F3.81 proves end to end that a key written into a Set filter whose list was
loaded under other filters is kept: the bar reloads that list with the key as `selected` before it
writes the model.

Prepare the backend as for the e2e filter journeys (a `lex` wrapper on `PATH` whose `PYTHONPATH`
points at the backend worktree `/home/syscall/Documents/lex/.claude/worktrees/lex_multi_filter`;
migrate and seed by hand; `LEX_E2E_KEEP_DB=1`), then run:
`CI=1 LEX_E2E_KEEP_DB=1 NPM_MARMELAB_TOKEN=x npx playwright test e2e/value-lists.spec.ts e2e/embed.spec.ts`
Expected: both new journeys pass, and `embed.spec.ts` stays as it was (its known local failures F12.3,
F12.11 and F12.19 predate this work).

- [ ] **Step 2: Run the whole unit suite and type-check against the baseline**

```bash
BASELINE=/home/syscall/LUND_IT/process-admin-general-client/.claude/worktrees/multi_value_filters-baseline
./node_modules/.bin/vitest run --reporter=json --outputFile="$BASELINE/vitest-after.json" > /dev/null 2>&1 || true
python3 - "$BASELINE" <<'PY'
import json, sys
base = sys.argv[1]
def load(name):
    data = json.load(open(f'{base}/{name}'))
    tests = {(f['name'], a['fullName']): a['status'] for f in data['testResults'] for a in f['assertionResults']}
    files = {f['name']: f['status'] for f in data['testResults']}
    return tests, files
before, before_files = load('vitest-before.json')
after, after_files = load('vitest-after.json')
added = {k: v for k, v in after.items() if k not in before}
print('added:', len(added), '| added and not passing:', [k for k, v in added.items() if v != 'passed'])
print('changed:', [(k, before[k], after[k]) for k in before if k in after and before[k] != after[k]])
print('gone:', [k for k in before if k not in after])
print('files changed:', [(f, before_files.get(f), s) for f, s in after_files.items() if f in before_files and before_files[f] != s])
print('failing before:', sum(v == 'failed' for v in before.values()), '| failing after:', sum(v == 'failed' for v in after.values()))
PY
./node_modules/.bin/tsc --noEmit -p tsconfig.json 2>&1 | grep "error TS" | sed -E 's/\([0-9]+,[0-9]+\)//' | sort > "$BASELINE/tsc-now.txt"
diff <(grep "error TS" "$BASELINE/tsc-before.txt" | sed -E 's/\([0-9]+,[0-9]+\)//' | sort) "$BASELINE/tsc-now.txt"
```

Expected:
- `added: 39`, none of them failing; `changed`, `gone` and `files changed` empty;
- the tsc diff is empty.

The 39 Vitest tests and Step 1's two Playwright journeys are the 41 this branch adds.

- [ ] **Step 3: Test-plan shards**

**F3 (`docs/test-plan/clusters/F03-grid_read/allocation.yaml`).** Append under `letters:` and set
`max_scenario: 83`:

```yaml
  o:
    title: Value lists, the filter bar, and Select All honouring them (U/C tiers, E for F3.81)
    scenarios: F3.66-F3.83
    tier: C
    status: complete
    tests: {pass: 27, skip: 0, xfail: 0}
    test_file: "src/components/model-components/list/filters/__test__/, src/components/model-components/list/__test__/CustomDatagrid.test.tsx, src/components/model-components/list/__test__/ServerSideDatasource.test.ts, e2e/value-lists.spec.ts"
    note: >-
      Value-list filters for the grid. F3.66-F3.67: the backend's value_list decides which columns
      get a value list (Set for choices, foreign keys and booleans; Multi = condition + Set for
      text, integers and dates); every other column - long text, opted-out models, every column of
      an older backend - keeps today's filter, and the key never lists its values. A name the
      fields endpoint sends twice (a renamed field is named after its source column) keeps one
      value list, on the listed descriptor's own column, whatever the order. F3.68-F3.72: lists
      come from filter_values with the grid's other filters, keep the server order, render labels
      as text, and a backend without the endpoint (404/405, or a catch-all answering HTML) makes
      lists unavailable once. F3.73-F3.78: the filter bar (columns from embed, view, list; one
      state with the grid; Show in filter bar). F3.79-F3.80: Select All Records honours Set and
      Multi models, blanks and an empty selection, group selection included. F3.81 (E): the bar
      narrows the grid and the other lists on the e2e fund grid, and a key written into a stale
      list is kept. F3.82: keys the app writes are sent as `selected` and stale lists reload first
      (AG keeps only listed keys). F3.83 (C): the grid's lists carry its fixed filter, and the
      fetcher's identity is stable.
```

**F4 (`docs/test-plan/clusters/F04-grid_views/allocation.yaml`).** Add letter `e` under `letters:`
(between `d` and `g`) and set `max_scenario: 44`:

```yaml
  e:
    title: Saved views and value lists (U tier)
    scenarios: F4.41-F4.44
    tier: U
    status: complete
    tests: {pass: 4, skip: 0, xfail: 0}
    test_file: "src/utils/__test__/filterModelMigration.cluster.test.ts, src/components/model-components/list/__test__/viewSettings.cluster.test.ts"
    note: >-
      Saved views migrate foreign-key equals, both boolean shapes and plain conditions to value
      lists / Multi; a column without a value list here keeps only the condition saved on it;
      unexpressible shapes are dropped with a warning. The record page's views (useGridViewState)
      migrate the same way. captureViewSettings stores filterBar and is the one capture for
      CustomList's two save paths and the record page's views.
```

**F12 (`docs/test-plan/clusters/F12-embed_streamlit/allocation.yaml`).** Append under `letters:` and
set `max_scenario: 112`:

```yaml
  u:
    title: A host page sets an embed's filters and hears them change (U tier, E for F12.111)
    scenarios: F12.104-F12.112
    tier: U
    status: complete
    tests: {pass: 10, skip: 0, xfail: 0}
    files:
    - src/utils/__test__/embedFilters.cluster.test.ts
    - src/utils/__test__/lexAppBridge.test.ts
    - src/components/model-components/list/__test__/CustomDatagrid.test.tsx
    - e2e/value-lists.spec.ts
    note: >-
      lex_view filters, frontend half (the backend half is lex-app batch 1aw): the first-load
      token; host filter messages from the parent only, with the host tag and a protocol version
      this build knows; named fields replace / null clears / [] matches nothing; a host message
      that arrives before any list is kept for the first one (Amendment 11); the embed's
      filter_bar for the opening table only; the gated filter event with row_count. F12.111 drives
      a real embed without a reload.
```

Append to `docs/test-plan/clusters/F03-grid_read/batches.md`. Replace `<before>` / `<after>` with the
failing-test counts Step 2 printed:

```markdown
## Batch 3o — Value lists, the filter bar, and Select All honouring them (F3.66-F3.83)

Every column gets the filter its values call for, and a filter bar shows chosen columns' value lists
above the grid. The bar holds no state: it reads and writes the grid's filter model.

- **Covers:**
  - **F3.66-F3.67** — the backend's `value_list` decides the filter, then the field type; the AG
    config each kind becomes; a name sent twice keeps one value list, on its own column.
  - **F3.68-F3.72** — lists from `filter_values` with the grid's other filters; server order; text
    labels; an older backend makes lists unavailable once; reading and writing the Set half.
  - **F3.73-F3.78** — the bar: its columns, its controls, writing the column filter, narrowing,
    removal, and "Show in filter bar".
  - **F3.79-F3.80** — Select All Records honours Set and Multi models, blanks, and an empty selection.
  - **F3.81 (E)** — the bar narrows the fund grid and the currency list, and keeps a key written
    into a stale list.
  - **F3.82** — keys the app writes reach the list AG loads, so AG keeps them.
  - **F3.83 (C)** — a grid's value lists carry its fixed filter; a re-render keeps the column
    definitions.
- **Regression:** the full unit suite on the borrowed install fails the same tests before (<before>)
  and after (<after>); tsc's error set is unchanged.
```

Append to `docs/test-plan/clusters/F03-grid_read/cluster.md`'s scenario table:

```markdown
| F3.66 | Each column gets its filter from the backend's `value_list`, then its type: Set, Multi or today's filter; a name sent twice keeps one value list, on its own column | U/C | ✅ implemented (3o). |
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
| F3.82 | A key the app writes reaches the list AG loads, so AG keeps it | U | ✅ implemented (3o). |
| F3.83 | A grid's value lists carry its fixed filter; a re-render keeps the column definitions | C | ✅ implemented (3o). |
```

Append to `docs/test-plan/clusters/F04-grid_views/batches.md`:

```markdown
## Batch 4e — Saved views and value lists (F4.41-F4.44)

Saved views keep filter models in local storage, in the shape of the filters columns had then.

- **Covers:**
  - **F4.41** — foreign-key `equals` and both boolean shapes become Set filters.
  - **F4.42** — text, date and combined conditions become the Multi filter's first half.
  - **F4.43** — a shape a column filter cannot hold is dropped with a warning; a column without a
    value list keeps only its condition.
  - **F4.44** — a captured view carries `filterBar`; one capture function serves every save path.
- **Regression:** as batch 3o.
```

and its scenario rows (`docs/test-plan/clusters/F04-grid_views/cluster.md`):

```markdown
| F4.41 | A saved single-value filter restores as a value list | U | ✅ implemented (4e). |
| F4.42 | A saved condition restores as the Multi filter's condition half | U | ✅ implemented (4e). |
| F4.43 | A saved shape a column filter cannot hold is dropped with a warning | U | ✅ implemented (4e). |
| F4.44 | A saved view remembers its filter bar | U | ✅ implemented (4e). |
```

Append to `docs/test-plan/clusters/F12-embed_streamlit/batches.md`:

```markdown
## Batch 12u — A host page sets an embed's filters and hears them change (F12.104-F12.112)

`lex_view(filters=, filter_bar=, on_filter=)`, frontend half. The backend half is lex-app batch 1aw.

- **Covers:**
  - **F12.104** — the first-load token decodes, UTF-8 included.
  - **F12.105-F12.106** — named fields replace their filters, null clears, `[]` matches nothing,
    other fields stay.
  - **F12.107** — first-load filters apply once, to the table the embed opened.
  - **F12.108** — only the parent frame with the host tag, in a protocol version this build knows,
    can push filters.
  - **F12.109 / 109b** — the event lists value-list selections, and one filtered load sends one
    event with its row count.
  - **F12.110** — the event is gated by `emit_filter`; the new parameters are snapshotted.
  - **F12.111 (E)** — a real embed changes filters without reloading and reports them.
  - **F12.112** — a host message that arrives before any list is kept for the first list that mounts.
- **Regression:** as batch 3o.
```

and its scenario rows (`docs/test-plan/clusters/F12-embed_streamlit/cluster.md`):

```markdown
| F12.104 | The first-load filters token decodes | U | ✅ implemented (12u). |
| F12.105 | Host filters replace the fields they name, and only those | U | ✅ implemented (12u). |
| F12.106 | null clears a field; an empty list matches no rows | U | ✅ implemented (12u). |
| F12.107 | First-load filters apply once, to the table the embed opened | U | ✅ implemented (12u). |
| F12.108 | Only the parent frame with the host tag can push filters | U | ✅ implemented (12u). |
| F12.109 | The filter event lists selections; one load sends one event with its count | U | ✅ implemented (12u). |
| F12.110 | `emit_filter` gates the event; `filters` and `filter_bar` are snapshotted | U | ✅ implemented (12u). |
| F12.111 | A host page changes an embed's filters without a reload, and hears back | E | ✅ implemented (12u). |
| F12.112 | A host filter message that arrives before any list is kept for the first one | U | ✅ implemented (12u). |
```

Add the session fragment, named and dated for the day it is committed:

```bash
DAY=$(date +%F)
cat > "docs/test-plan/progress/sessions/$DAY-value-lists.md" <<EOF
---
date: $DAY
clusters: [F3, F4, F12]
tests_added: 41
suite_tally: { pass: 41, skip: 0, xfail: 0 }
---

# Value lists, the filter bar, and embed filters

Value-list filters for the grid ([3o](../../clusters/F03-grid_read/batches.md)), saved views that
migrate to them ([4e](../../clusters/F04-grid_views/batches.md)), and embeds a host page can filter
([12u](../../clusters/F12-embed_streamlit/batches.md)). The backend half is in lex-app (batches 14f,
14g, 4o, 10o, 13i, 1aw).
EOF
```

41 is Step 2's 39 Vitest tests plus Step 1's two Playwright journeys. If either step counted
differently, write the counted numbers here and in the shards' `tests:`.

- [ ] **Step 4: Push and open the PR**

```bash
git add docs/test-plan e2e/value-lists.spec.ts
git commit -m "test(value-lists): journeys and test-plan shards

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin feat/multi-value-filters
```

Open the PR against `lex-app-v2-pac-latest`, titled "feat(grid): value-list filters, a filter bar, and embed filters". The body:
- covers what changed, the dependency on the backend (`filter_values`, the Set/Multi translation,
  `list_ui.filter_bar`, the fields endpoint's `value_list`, the `lex_view` component's `filter`
  message), and the fallback when the backend is older (no `value_list`: every column keeps its
  filter; no endpoint: lists unavailable);
- says the two custom filter components stay: `AGForeignKeyFilter` and `AGBooleanFilter` remain the
  filters of foreign-key and boolean columns without a value list (older backends, opted-out
  models). Their tests are F3.51–F3.53 (`booleanFilterInterop.cluster.test.tsx`) for the boolean
  filter and `AGForeignKeyFilter.test.tsx` for the foreign key;
- says the e2e Fund list now has a default filter bar, so the Fund screenshots in
  `e2e/docshots.spec.ts` change and the documentation images are refreshed in the docs PR;
- gives the test numbers against the baseline;
- ends with the Claude Code line.
