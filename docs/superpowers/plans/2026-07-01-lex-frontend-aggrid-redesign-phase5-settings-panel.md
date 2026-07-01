# Phase 5 — Settings Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the density-only gear menu with a proper Settings panel scoped to the current grid: Density + Display toggles (status bar, wrap header text, row-index column) + per-column format overrides with Reset — all persisted in the existing per-resource saved views (localStorage).

**Architecture:** A new MUI `Popover`-based `TableSettingsPanel` replaces `TableSettingsMenu`. CustomList holds two new pieces of view-scoped state (`displaySettings`, `columnFormatOverrides`), threads them through its inline saved-view state machine (`restoreView` / `handleUpdateView` / `handleCreateView`), and passes them to `CustomDatagrid`, which maps them onto AG-Grid options (`statusBar`, `wrapHeaderText`, a leading row-index column) and layers format overrides on top of the Phase-3 backend `FormatSpec` when building `valueFormatter`s.

**Tech Stack:** React 18, React-Admin, AG-Grid Enterprise, MUI v5, Vitest + Testing Library.

**Repo:** `/home/syscall/LUND_IT/process-admin-general-client` (branch `lex-app-v2-pac-latest`).

**Commands:**
- Tests: `NPM_MARMELAB_TOKEN=dummy yarn test --run <pattern>`
- Type-check: `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types` (only pre-existing TS1149 casing error allowed)

---

## Shared types

`DisplaySettings` and format-override shapes live in one place so panel, CustomList, and CustomDatagrid agree.

**File:** Create `src/components/model-components/list/tableSettingsTypes.ts`

```typescript
import type { FormatSpec } from '../../../api/endpoints/model-info'

export interface DisplaySettings {
  /** AG-Grid status bar with totals/selection aggregation panels. */
  statusBar: boolean
  /** Wrap long header text onto multiple lines (default true, matches current). */
  wrapHeaders: boolean
  /** Show a leading 1-based row-index column. */
  rowIndex: boolean
}

export const DEFAULT_DISPLAY_SETTINGS: DisplaySettings = {
  statusBar: false,
  wrapHeaders: true,
  rowIndex: false,
}

/** Per-column, per-view user format override keyed by field name. */
export type ColumnFormatOverrides = Record<string, FormatSpec>
```

---

## File Structure

- **Create** `src/components/model-components/list/tableSettingsTypes.ts` — shared types + defaults.
- **Create** `src/components/model-components/list/TableSettingsPanel.tsx` — the new Popover panel (density + display toggles + format overrides). Replaces `TableSettingsMenu`.
- **Create** `src/components/model-components/list/__test__/TableSettingsPanel.test.tsx` — panel behavior tests.
- **Modify** `src/components/model-components/list/CustomList.tsx` — new state, persistence wiring, pass props to panel + datagrid.
- **Modify** `src/components/model-components/list/CustomDatagrid.tsx` — accept + apply `displaySettings` and `columnFormatOverrides`.
- **Delete** `src/components/model-components/list/TableSettingsMenu.tsx` — superseded (its density logic folds into the panel).

---

## Phase 5a — Panel shell + Density + Display toggles

### Task 1: Shared types

**Files:**
- Create: `src/components/model-components/list/tableSettingsTypes.ts`

- [ ] **Step 1: Write the types file** (content exactly as in "Shared types" above).

- [ ] **Step 2: Type-check**

Run: `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types`
Expected: only the pre-existing TS1149 `ErrorMessages.test.ts` casing error.

- [ ] **Step 3: Commit**

```bash
git add src/components/model-components/list/tableSettingsTypes.ts
git commit -m "feat(frontend-redesign): shared table-settings types (phase 5)"
```

### Task 2: TableSettingsPanel component (density + display toggles)

**Files:**
- Create: `src/components/model-components/list/TableSettingsPanel.tsx`
- Test: `src/components/model-components/list/__test__/TableSettingsPanel.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { TableSettingsPanel } from '../TableSettingsPanel'
import { DEFAULT_DISPLAY_SETTINGS } from '../tableSettingsTypes'

const baseProps = {
  currentRowHeight: 25 as string | number,
  onRowHeightChange: vi.fn(),
  displaySettings: DEFAULT_DISPLAY_SETTINGS,
  onDisplaySettingsChange: vi.fn(),
  formattableColumns: [] as Array<{ name: string; label: string }>,
  columnFormatOverrides: {},
  onColumnFormatOverridesChange: vi.fn(),
}

const openPanel = async () => {
  const user = userEvent.setup()
  await user.click(screen.getByLabelText('Settings'))
  return user
}

describe('TableSettingsPanel', () => {
  it('opens the panel and shows the density options', async () => {
    render(<TableSettingsPanel {...baseProps} />)
    await openPanel()
    expect(screen.getByText('Compact')).toBeInTheDocument()
    expect(screen.getByText('Standard')).toBeInTheDocument()
    expect(screen.getByText('Comfortable')).toBeInTheDocument()
  })

  it('reports a density change with the row height', async () => {
    const onRowHeightChange = vi.fn()
    render(<TableSettingsPanel {...baseProps} onRowHeightChange={onRowHeightChange} />)
    const user = await openPanel()
    await user.click(screen.getByText('Comfortable'))
    expect(onRowHeightChange).toHaveBeenCalledWith(200)
  })

  it('toggles a display setting and reports the new object', async () => {
    const onDisplaySettingsChange = vi.fn()
    render(
      <TableSettingsPanel {...baseProps} onDisplaySettingsChange={onDisplaySettingsChange} />,
    )
    const user = await openPanel()
    await user.click(screen.getByLabelText('Show status bar'))
    expect(onDisplaySettingsChange).toHaveBeenCalledWith(
      expect.objectContaining({ statusBar: true }),
    )
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run TableSettingsPanel`
Expected: FAIL — module `../TableSettingsPanel` not found.

- [ ] **Step 3: Write the panel implementation**

```tsx
import React, { useState } from 'react'
import {
  Box,
  Divider,
  FormControlLabel,
  IconButton,
  ListSubheader,
  MenuItem,
  Popover,
  Switch,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material'
import SettingsIcon from '@mui/icons-material/Settings'
import DensitySmallIcon from '@mui/icons-material/DensitySmall'
import DensityMediumIcon from '@mui/icons-material/DensityMedium'
import DensityLargeIcon from '@mui/icons-material/DensityLarge'
import type { FormatSpec } from '../../../api/endpoints/model-info'
import type { ColumnFormatOverrides, DisplaySettings } from './tableSettingsTypes'
import { ColumnFormatEditor } from './ColumnFormatEditor'

export interface FormattableColumn {
  name: string
  label: string
  backendFormat?: FormatSpec
}

interface TableSettingsPanelProps {
  currentRowHeight: string | number
  onRowHeightChange: (h: string | number) => void
  displaySettings: DisplaySettings
  onDisplaySettingsChange: (next: DisplaySettings) => void
  formattableColumns: FormattableColumn[]
  columnFormatOverrides: ColumnFormatOverrides
  onColumnFormatOverridesChange: (next: ColumnFormatOverrides) => void
}

const DENSITIES: Array<{ h: number; label: string; Icon: typeof DensitySmallIcon }> = [
  { h: 25, label: 'Compact', Icon: DensitySmallIcon },
  { h: 100, label: 'Standard', Icon: DensityMediumIcon },
  { h: 200, label: 'Comfortable', Icon: DensityLargeIcon },
]

export const TableSettingsPanel = ({
  currentRowHeight,
  onRowHeightChange,
  displaySettings,
  onDisplaySettingsChange,
  formattableColumns,
  columnFormatOverrides,
  onColumnFormatOverridesChange,
}: TableSettingsPanelProps): JSX.Element => {
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null)
  const open = Boolean(anchorEl)
  const currentDensity = Number(currentRowHeight) || 25

  const setToggle = (key: keyof DisplaySettings) => (_: unknown, checked: boolean) =>
    onDisplaySettingsChange({ ...displaySettings, [key]: checked })

  return (
    <>
      <Tooltip title='Settings'>
        <IconButton
          aria-label='Settings'
          aria-haspopup='true'
          aria-expanded={open ? 'true' : undefined}
          size='small'
          color='inherit'
          onClick={(e) => setAnchorEl(e.currentTarget)}
        >
          <SettingsIcon />
        </IconButton>
      </Tooltip>
      <Popover
        open={open}
        anchorEl={anchorEl}
        onClose={() => setAnchorEl(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        slotProps={{ paper: { sx: { width: 320, maxHeight: 520, p: 1.5 } } }}
      >
        <ListSubheader disableSticky sx={{ px: 0, lineHeight: '28px' }}>
          Density
        </ListSubheader>
        <ToggleButtonGroup
          size='small'
          exclusive
          fullWidth
          value={currentDensity}
          onChange={(_, val) => {
            if (val != null) onRowHeightChange(val)
          }}
        >
          {DENSITIES.map(({ h, label, Icon }) => (
            <ToggleButton key={h} value={h} sx={{ textTransform: 'none', gap: 0.5 }}>
              <Icon fontSize='small' />
              {label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>

        <Divider sx={{ my: 1.5 }} />

        <ListSubheader disableSticky sx={{ px: 0, lineHeight: '28px' }}>
          Display
        </ListSubheader>
        <Box sx={{ display: 'flex', flexDirection: 'column' }}>
          <FormControlLabel
            control={
              <Switch
                size='small'
                checked={displaySettings.statusBar}
                onChange={setToggle('statusBar')}
                inputProps={{ 'aria-label': 'Show status bar' }}
              />
            }
            label='Show status bar'
          />
          <FormControlLabel
            control={
              <Switch
                size='small'
                checked={displaySettings.wrapHeaders}
                onChange={setToggle('wrapHeaders')}
                inputProps={{ 'aria-label': 'Wrap header text' }}
              />
            }
            label='Wrap header text'
          />
          <FormControlLabel
            control={
              <Switch
                size='small'
                checked={displaySettings.rowIndex}
                onChange={setToggle('rowIndex')}
                inputProps={{ 'aria-label': 'Show row-index column' }}
              />
            }
            label='Show row-index column'
          />
        </Box>

        {formattableColumns.length > 0 && (
          <>
            <Divider sx={{ my: 1.5 }} />
            <ColumnFormatEditor
              columns={formattableColumns}
              overrides={columnFormatOverrides}
              onChange={onColumnFormatOverridesChange}
            />
          </>
        )}
      </Popover>
    </>
  )
}

export default TableSettingsPanel
```

Note: `ColumnFormatEditor` is created in Task 6 (Phase 5b). For 5a, temporarily stub it so the panel compiles and tests for density/display pass — create a minimal placeholder `ColumnFormatEditor.tsx` that renders `null`, replaced in Task 6. (The panel guards it behind `formattableColumns.length > 0`, and 5a tests pass `formattableColumns: []`, so the stub is never rendered in 5a tests.)

Minimal stub for Task 2 (`src/components/model-components/list/ColumnFormatEditor.tsx`):

```tsx
import React from 'react'
import type { ColumnFormatOverrides } from './tableSettingsTypes'
import type { FormattableColumn } from './TableSettingsPanel'

export const ColumnFormatEditor = (_props: {
  columns: FormattableColumn[]
  overrides: ColumnFormatOverrides
  onChange: (next: ColumnFormatOverrides) => void
}): JSX.Element | null => null

export default ColumnFormatEditor
```

Circular import note: `ColumnFormatEditor` imports `FormattableColumn` (a type) from `TableSettingsPanel`, and `TableSettingsPanel` imports `ColumnFormatEditor` (a value). Type-only import (`import type`) does not create a runtime cycle, so this is safe.

- [ ] **Step 4: Run test to verify it passes**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run TableSettingsPanel`
Expected: PASS (3 tests).

- [ ] **Step 5: Type-check**

Run: `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types`
Expected: only pre-existing TS1149.

- [ ] **Step 6: Commit**

```bash
git add src/components/model-components/list/TableSettingsPanel.tsx src/components/model-components/list/ColumnFormatEditor.tsx src/components/model-components/list/__test__/TableSettingsPanel.test.tsx
git commit -m "feat(frontend-redesign): settings panel shell with density + display toggles (phase 5a)"
```

### Task 3: Wire display + density state into CustomList persistence

**Files:**
- Modify: `src/components/model-components/list/CustomList.tsx`

- [ ] **Step 1: Add state** (near `currentRowHeight`, `CustomList.tsx:116`)

```tsx
import {
  DEFAULT_DISPLAY_SETTINGS,
  type ColumnFormatOverrides,
  type DisplaySettings,
} from './tableSettingsTypes'
// ...
const [displaySettings, setDisplaySettings] = useState<DisplaySettings>(DEFAULT_DISPLAY_SETTINGS)
const [columnFormatOverrides, setColumnFormatOverrides] = useState<ColumnFormatOverrides>({})
```

- [ ] **Step 2: Read persisted settings in `restoreView`** (inside `restoreView`, after `setCurrentRowHeight(savedRowHeight)` — the CustomList copy near `CustomList.tsx:~330`, mirroring the `savedRowHeight` handling)

```tsx
setDisplaySettings({
  ...DEFAULT_DISPLAY_SETTINGS,
  ...(viewSettings?.displaySettings ?? {}),
})
setColumnFormatOverrides(viewSettings?.columnFormatOverrides ?? {})
```

- [ ] **Step 3: Persist in `handleUpdateView`** (`CustomList.tsx:688`) — add two keys to the captured view object:

```tsx
        rowHeight: currentRowHeight,
        pivotMode: currentPivotMode,
        pivotAggregationState: currentPivotAggregationState,
        serializer: serializerToUse,
        displaySettings,
        columnFormatOverrides,
```

- [ ] **Step 4: Persist in `handleCreateView`** (`CustomList.tsx:749`) — add the same two keys to that captured object.

- [ ] **Step 5: Add change handlers that mark the view dirty** (near `handleRowHeightSelect`, `CustomList.tsx:762`)

```tsx
const handleDisplaySettingsChange = (next: DisplaySettings) => {
  setDisplaySettings(next)
  setIsViewModified(true)
}

const handleColumnFormatOverridesChange = (next: ColumnFormatOverrides) => {
  setColumnFormatOverrides(next)
  setIsViewModified(true)
}
```

- [ ] **Step 6: Swap the toolbar component** (`CustomList.tsx:1154-1157`) — replace `<TableSettingsMenu .../>` with:

```tsx
                    <TableSettingsPanel
                      currentRowHeight={currentRowHeight}
                      onRowHeightChange={handleRowHeightSelect}
                      displaySettings={displaySettings}
                      onDisplaySettingsChange={handleDisplaySettingsChange}
                      formattableColumns={formattableColumns}
                      columnFormatOverrides={columnFormatOverrides}
                      onColumnFormatOverridesChange={handleColumnFormatOverridesChange}
                    />
```

Update the import at the top of CustomList from `TableSettingsMenu` to `TableSettingsPanel`. For 5a, pass `formattableColumns={[]}` (a `const formattableColumns = [] as FormattableColumn[]` placeholder near the other derived values); Task 5 replaces it with the real derived list.

- [ ] **Step 7: Pass settings to CustomDatagrid** (`CustomList.tsx:1170-1192`) — add two props to `<CustomDatagrid ...>`:

```tsx
                  displaySettings={displaySettings}
                  columnFormatOverrides={columnFormatOverrides}
```

- [ ] **Step 8: Type-check**

Run: `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types`
Expected: only pre-existing TS1149. (CustomDatagrid props added in Task 4 — if this runs before Task 4, expect a props error; do Task 4 before type-checking, or land 3+4 together.)

### Task 4: Apply display settings in CustomDatagrid

**Files:**
- Modify: `src/components/model-components/list/CustomDatagrid.tsx`

- [ ] **Step 1: Extend props** (`CustomDatagridProps`, `CustomDatagrid.tsx:394`)

```tsx
  displaySettings?: import('./tableSettingsTypes').DisplaySettings
  columnFormatOverrides?: import('./tableSettingsTypes').ColumnFormatOverrides
```

and destructure them in the component signature (`CustomDatagrid.tsx:594`) with defaults:

```tsx
  displaySettings,
  columnFormatOverrides,
```

- [ ] **Step 2: wrapHeaders toggle** — change `defaultColDef` (`CustomDatagrid.tsx:2659`) `wrapHeaderText: true` to `wrapHeaderText: displaySettings?.wrapHeaders ?? true`, and add `displaySettings?.wrapHeaders` to the `defaultColDef` useMemo deps (`CustomDatagrid.tsx:2665`).

- [ ] **Step 3: status bar** — add a memoized `statusBar` config and pass it to `<BareDatagridAGClient>` (near `rowHeight={numericHeight}`, `CustomDatagrid.tsx:2805`):

```tsx
        statusBar={
          displaySettings?.statusBar
            ? {
                statusPanels: [
                  { statusPanel: 'agTotalAndFilteredRowCountComponent', align: 'left' },
                  { statusPanel: 'agSelectedRowCountComponent', align: 'center' },
                  { statusPanel: 'agAggregationComponent', align: 'right' },
                ],
              }
            : undefined
        }
```

- [ ] **Step 4: row-index column** — prepend a row-index column to `columnDefs` when enabled. At the top of the `columnDefs` useMemo return array (`CustomDatagrid.tsx:2401`), spread in:

```tsx
      ...(displaySettings?.rowIndex
        ? [
            {
              colId: '__row_index__',
              headerName: '#',
              width: 64,
              pinned: 'left' as const,
              sortable: false,
              filter: false,
              resizable: false,
              editable: false,
              suppressMovable: true,
              lockPosition: true,
              enableRowGroup: false,
              enablePivot: false,
              enableValue: false,
              valueGetter: (p: any) =>
                p?.node && typeof p.node.rowIndex === 'number' ? p.node.rowIndex + 1 : '',
            },
          ]
        : []),
```

and add `displaySettings?.rowIndex` to the `columnDefs` useMemo deps (`CustomDatagrid.tsx:2634`).

- [ ] **Step 5: Type-check + run existing datagrid tests**

Run: `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types`
Then: `NPM_MARMELAB_TOKEN=dummy yarn test --run CustomDatagrid CustomList`
Expected: type-check only pre-existing TS1149; tests — same set of pre-existing failures as before this work (CustomDatagrid height ×3 + column-resize/column-events ×1; CustomList `onPotentialLateContent` ×1), no new failures.

- [ ] **Step 6: Commit**

```bash
git add src/components/model-components/list/CustomList.tsx src/components/model-components/list/CustomDatagrid.tsx
git commit -m "feat(frontend-redesign): persist + apply density/display settings via settings panel (phase 5a)"
```

- [ ] **Step 7: Delete the superseded menu**

```bash
git rm src/components/model-components/list/TableSettingsMenu.tsx
```

Verify nothing else imports it: `grep -rn TableSettingsMenu src` should return no matches. If `CustomShow` (record-level tabs) also uses it, migrate that usage to `TableSettingsPanel` with `formattableColumns={[]}` and its own display state, or leave a thin re-export — decide during implementation. Then commit:

```bash
git commit -m "chore(frontend-redesign): remove density-only settings menu (superseded by panel)"
```

---

## Phase 5b — Column format overrides

### Task 5: Derive formattable columns in CustomList

**Files:**
- Modify: `src/components/model-components/list/CustomList.tsx`

The panel needs the list of numeric-formattable columns with their backend `FormatSpec` defaults. The authoritative field metadata (`fields` with `format`, `type`, `readable_name`) is fetched inside CustomDatagrid today, not CustomList. CustomList already has the model-fields query available via the same endpoint CustomDatagrid uses.

- [ ] **Step 1: Find the fields source.** During implementation, confirm the hook CustomDatagrid uses to load `/fields/` (search `CustomDatagrid.tsx` for the fields query hook, e.g. a `useGet…Fields`/RTK query keyed by `activeResource`). Reuse that same hook in CustomList so both read the same cache.

- [ ] **Step 2: Build the derived list** (near the other derived values, after `activeModel`, `CustomList.tsx:~174`)

```tsx
const formattableColumns: FormattableColumn[] = useMemo(
  () =>
    (fields ?? [])
      .filter(
        (f: any) =>
          !String(f.name).startsWith('lex_reserved') &&
          (f.format != null ||
            f.type === 'integer' ||
            f.type === 'float' ||
            f.type === 'decimal' ||
            f.type === 'number'),
      )
      .map((f: any) => ({
        name: f.name as string,
        label: (f.readable_name as string) ?? (f.name as string),
        backendFormat: f.format,
      })),
  [fields],
)
```

Replace the Task-3 `formattableColumns={[]}` placeholder with this real list. Add `FormattableColumn` to the imports from `TableSettingsPanel`.

- [ ] **Step 3: Type-check**

Run: `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types`
Expected: only pre-existing TS1149.

### Task 6: ColumnFormatEditor component

**Files:**
- Modify: `src/components/model-components/list/ColumnFormatEditor.tsx` (replace the Task-2 stub)
- Test: `src/components/model-components/list/__test__/ColumnFormatEditor.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { ColumnFormatEditor } from '../ColumnFormatEditor'

const columns = [
  { name: 'amount', label: 'Amount', backendFormat: { format: 'currency' as const, currency: 'EUR' } },
  { name: 'ratio', label: 'Ratio' },
]

describe('ColumnFormatEditor', () => {
  it('lists each formattable column', () => {
    render(<ColumnFormatEditor columns={columns} overrides={{}} onChange={vi.fn()} />)
    expect(screen.getByText('Amount')).toBeInTheDocument()
    expect(screen.getByText('Ratio')).toBeInTheDocument()
  })

  it('sets an override when a format is chosen', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<ColumnFormatEditor columns={columns} overrides={{}} onChange={onChange} />)
    await user.click(screen.getByLabelText('Format for Ratio'))
    await user.click(screen.getByRole('option', { name: 'Percentage' }))
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ ratio: expect.objectContaining({ format: 'percentage' }) }),
    )
  })

  it('reset removes all overrides', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(
      <ColumnFormatEditor
        columns={columns}
        overrides={{ ratio: { format: 'percentage' } }}
        onChange={onChange}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Reset to backend defaults' }))
    expect(onChange).toHaveBeenCalledWith({})
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run ColumnFormatEditor`
Expected: FAIL — stub renders null, no "Amount" text.

- [ ] **Step 3: Implement the editor**

```tsx
import React from 'react'
import {
  Box,
  Button,
  ListSubheader,
  MenuItem,
  Select,
  Typography,
} from '@mui/material'
import type { FormatSpec } from '../../../api/endpoints/model-info'
import type { ColumnFormatOverrides } from './tableSettingsTypes'
import type { FormattableColumn } from './TableSettingsPanel'

interface ColumnFormatEditorProps {
  columns: FormattableColumn[]
  overrides: ColumnFormatOverrides
  onChange: (next: ColumnFormatOverrides) => void
}

const FORMAT_OPTIONS: Array<{ value: '' | FormatSpec['format']; label: string }> = [
  { value: '', label: 'Default' },
  { value: 'number', label: 'Number' },
  { value: 'currency', label: 'Currency (EUR)' },
  { value: 'percentage', label: 'Percentage' },
]

export const ColumnFormatEditor = ({
  columns,
  overrides,
  onChange,
}: ColumnFormatEditorProps): JSX.Element => {
  const setFormat = (name: string, value: '' | FormatSpec['format']) => {
    const next = { ...overrides }
    if (value === '') {
      delete next[name]
    } else if (value === 'currency') {
      next[name] = { format: 'currency', currency: 'EUR' }
    } else {
      next[name] = { format: value }
    }
    onChange(next)
  }

  return (
    <Box>
      <ListSubheader disableSticky sx={{ px: 0, lineHeight: '28px' }}>
        Column formats
      </ListSubheader>
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, maxHeight: 200, overflowY: 'auto' }}>
        {columns.map((col) => (
          <Box key={col.name} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography variant='body2' sx={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {col.label}
            </Typography>
            <Select
              size='small'
              value={overrides[col.name]?.format ?? ''}
              onChange={(e) => setFormat(col.name, e.target.value as '' | FormatSpec['format'])}
              displayEmpty
              inputProps={{ 'aria-label': `Format for ${col.label}` }}
              sx={{ minWidth: 140 }}
            >
              {FORMAT_OPTIONS.map((opt) => (
                <MenuItem key={opt.value || 'default'} value={opt.value}>
                  {opt.label}
                </MenuItem>
              ))}
            </Select>
          </Box>
        ))}
      </Box>
      <Button
        size='small'
        sx={{ mt: 1, textTransform: 'none' }}
        onClick={() => onChange({})}
        disabled={Object.keys(overrides).length === 0}
      >
        Reset to backend defaults
      </Button>
    </Box>
  )
}

export default ColumnFormatEditor
```

- [ ] **Step 4: Run test to verify it passes**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run ColumnFormatEditor TableSettingsPanel`
Expected: PASS.

- [ ] **Step 5: Type-check**

Run: `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types`
Expected: only pre-existing TS1149.

- [ ] **Step 6: Commit**

```bash
git add src/components/model-components/list/ColumnFormatEditor.tsx src/components/model-components/list/__test__/ColumnFormatEditor.test.tsx src/components/model-components/list/CustomList.tsx
git commit -m "feat(frontend-redesign): per-column format override editor + reset (phase 5b)"
```

### Task 7: Layer overrides onto the grid valueFormatter

**Files:**
- Modify: `src/components/model-components/list/CustomDatagrid.tsx`

- [ ] **Step 1: Prefer the override spec** — in the `columnDefs` build (`CustomDatagrid.tsx:2417`), change:

```tsx
          const effectiveFormat = columnFormatOverrides?.[fieldInfo.name] ?? fieldInfo.format
          const numberFormatter = effectiveFormat ? buildValueFormatter(effectiveFormat) : undefined
```

- [ ] **Step 2: Add dependency** — add `columnFormatOverrides` to the `columnDefs` useMemo deps (`CustomDatagrid.tsx:2634`).

- [ ] **Step 3: Type-check + tests**

Run: `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types`
Then: `NPM_MARMELAB_TOKEN=dummy yarn test --run CustomDatagrid CustomList TableSettingsPanel ColumnFormatEditor`
Expected: type-check only pre-existing TS1149; only the documented pre-existing test failures, no new ones.

- [ ] **Step 4: Commit**

```bash
git add src/components/model-components/list/CustomDatagrid.tsx
git commit -m "feat(frontend-redesign): apply per-view column format overrides in grid (phase 5b)"
```

---

## Self-Review

- **Spec coverage (4.6):** Density ✓ (Task 2 + 3), Display toggles status bar/wrap headers/row-index ✓ (Task 2 + 4), Column format overrides + Reset ✓ (Task 6 + 7), persisted in saved views ✓ (Task 3, keys added to `handleUpdateView`/`handleCreateView`, read in `restoreView`).
- **Type consistency:** `DisplaySettings`/`ColumnFormatOverrides` defined once in `tableSettingsTypes.ts`; `FormattableColumn` exported from `TableSettingsPanel` and imported as a type by `ColumnFormatEditor` and `CustomList`. `FormatSpec` reused from `model-info.ts`. Panel prop names (`onDisplaySettingsChange`, `onColumnFormatOverridesChange`) match CustomList handlers.
- **Open implementation decisions (resolve in-flight, not blockers):** (a) exact fields-query hook name to reuse in CustomList (Task 5 Step 1); (b) whether `CustomShow` also consumed `TableSettingsMenu` and needs migration (Task 4 Step 7); (c) confirm AG-Grid status-panel component IDs against the installed enterprise version.
