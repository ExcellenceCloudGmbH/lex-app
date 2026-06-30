# Phase 3 — Data Presentation (value formatting + FK chips + FK hover card) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render grid values with backend-declared number/currency/percent formatting, render foreign-key cells as compact chips using the `<fk>_label` the backend already emits, and replace the old horizontal-table FK tooltip with a vertical label→value hover card that uses the target's `preview` serializer when available.

**Architecture:** Phase 1 (backend) already exposes everything we need in `/fields/` metadata (`format`, `fk_label_field`, `fk_preview`) and serialized records (`<fk>_label` siblings). Phase 3 is **frontend-only**: extend the TS field types, add a pure `formatValue` util driven by `Intl.NumberFormat`, hook a `valueFormatter` into the AG-Grid column builder, render FK chips from `<fk>_label`, and build a new `ForeignKeyHoverCard` that fetches `?serializer=preview` (falling back to the first N readable default-serializer fields). All changes are additive/like-for-like and must keep filter/sort/edit/group/SSRM working and render correctly in dark mode.

**Tech Stack:** React 18, React-Admin, AG-Grid Enterprise 33.0.4, MUI v5, RTK Query, Vitest + @testing-library/react. Repo: `/home/syscall/LUND_IT/process-admin-general-client`.

**Conventions for every task:**
- Run type-check with: `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types` (a single pre-existing `TS1149` casing error on `ErrorMessages.test.ts` is expected — ignore only that one).
- Run tests with: `NPM_MARMELAB_TOKEN=dummy yarn test --run <pattern>`.
- Commit by name (never `git add -A`).
- Keep the navy/teal palette and dark-mode support.

---

## File Structure

**Create:**
- `src/utils/formatValue.ts` — pure formatter: `FormatSpec` → a function `(value) => string`. No React, no AG-Grid imports. Easily unit-tested.
- `src/utils/__test__/formatValue.test.ts` — unit tests for the formatter.
- `src/components/CustomTooltips/ForeignKeyHoverCard/ForeignKeyHoverCard.tsx` — the new vertical label→value FK hover card.
- `src/components/CustomTooltips/ForeignKeyHoverCard/__test__/ForeignKeyHoverCard.test.tsx` — tests for the card.

**Modify:**
- `src/api/endpoints/model-info.ts` — add `format`, `fk_label_field`, `fk_preview` to the field types + a `FormatSpec` type.
- `src/components/model-components/list/CustomDatagrid.tsx` — build a `valueFormatter` per column from `fieldInfo.format`; give FK columns an FK-aware `valueFormatter` (export/group fallback) using `<fk>_label`.
- `src/components/model-components/list/FieldView.tsx` — render FK cells as a chip using `<fk>_label`; swap `ForeignKeyTooltip` for `ForeignKeyHoverCard`.
- `src/components/model-components/list/__test__/CustomDatagrid.test.tsx` — assert `valueFormatter` behavior.
- `src/components/model-components/list/__test__/FieldView.test.tsx` — assert FK chip uses `<fk>_label`.

---

### Task 1: Extend frontend field-metadata TS types

**Files:**
- Modify: `src/api/endpoints/model-info.ts:1-36`

- [ ] **Step 1: Add the `FormatSpec` type and the three new field keys**

In `src/api/endpoints/model-info.ts`, add a `FormatSpec` type above `GeneralFieldInfo`, then extend the interfaces:

```typescript
/**
 * Per-field display formatting declared by the backend
 * (`lex_field_formats` → `/fields/` `format` key). Drives the grid
 * valueFormatter and the FK hover-card value rendering.
 */
export interface FormatSpec {
  format: 'number' | 'currency' | 'percentage'
  /** ISO 4217 code, e.g. "EUR". Only meaningful when format === 'currency'. */
  currency?: string
  /** Fixed fraction digits. Defaults applied by the formatter when omitted. */
  decimals?: number
  /** Toggle thousands grouping. Defaults to true. */
  useGrouping?: boolean
}

export interface GeneralFieldInfo {
  name: string
  readable_name: string
  type: string
  editable?: boolean
  required?: boolean
  default_value?: unknown
  model?: string
  dashboard_show_file_name?: boolean
  fk_id?: string
  choices?: any[]
  /** Backend display format spec (Phase 1: lex_field_formats). */
  format?: FormatSpec
}

export type ForeignKeyFieldInfo = GeneralFieldInfo & {
  target: string
  limit_choices_to?: Record<string, unknown>
  /** Target model's human-label field name (Phase 1: lex_fk_label_field). */
  fk_label_field?: string | null
  /** True when the target registers a `preview` api_serializer. */
  fk_preview?: boolean
}
```

- [ ] **Step 2: Type-check**

Run: `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types`
Expected: only the pre-existing `TS1149` casing error; no new errors.

- [ ] **Step 3: Commit**

```bash
git add src/api/endpoints/model-info.ts
git commit -m "feat(frontend-redesign): add format/fk_label_field/fk_preview to field types (phase 3)"
```

---

### Task 2: Pure `formatValue` utility (TDD)

**Files:**
- Create: `src/utils/formatValue.ts`
- Test: `src/utils/__test__/formatValue.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `src/utils/__test__/formatValue.test.ts`:

```typescript
import { describe, it, expect } from 'vitest'
import { buildValueFormatter } from '../formatValue'

describe('buildValueFormatter', () => {
  it('formats currency with the given code and decimals', () => {
    const fmt = buildValueFormatter({ format: 'currency', currency: 'EUR', decimals: 2 })
    // en-US locale renders EUR as "€1,234.50"
    expect(fmt(1234.5)).toBe('€1,234.50')
  })

  it('formats percentage from a ratio (0.125 -> 12.5%)', () => {
    const fmt = buildValueFormatter({ format: 'percentage', decimals: 1 })
    expect(fmt(0.125)).toBe('12.5%')
  })

  it('formats plain number with grouping and decimals', () => {
    const fmt = buildValueFormatter({ format: 'number', decimals: 2 })
    expect(fmt(1234567.891)).toBe('1,234,567.89')
  })

  it('honors useGrouping=false', () => {
    const fmt = buildValueFormatter({ format: 'number', decimals: 0, useGrouping: false })
    expect(fmt(1234567)).toBe('1234567')
  })

  it('returns empty string for null/undefined', () => {
    const fmt = buildValueFormatter({ format: 'number', decimals: 2 })
    expect(fmt(null)).toBe('')
    expect(fmt(undefined)).toBe('')
  })

  it('passes through non-numeric values unchanged', () => {
    const fmt = buildValueFormatter({ format: 'number', decimals: 2 })
    expect(fmt('n/a')).toBe('n/a')
  })

  it('parses numeric strings', () => {
    const fmt = buildValueFormatter({ format: 'number', decimals: 1 })
    expect(fmt('42')).toBe('42.0')
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run formatValue`
Expected: FAIL — `buildValueFormatter` not found.

- [ ] **Step 3: Implement the formatter**

Create `src/utils/formatValue.ts`:

```typescript
import type { FormatSpec } from '../api/endpoints/model-info'

/** App display locale. Kept centralized so all formatting stays consistent. */
const DISPLAY_LOCALE = 'en-US'

/**
 * Build a `(value) => string` formatter from a backend FormatSpec using
 * Intl.NumberFormat. Null/undefined → "". Non-numeric, non-parseable values
 * pass through untouched so we never hide unexpected content. Percentages
 * treat the stored value as a ratio (0.125 → "12.5%").
 */
export function buildValueFormatter(spec: FormatSpec): (value: unknown) => string {
  const decimals = spec.decimals
  const useGrouping = spec.useGrouping !== false

  const options: Intl.NumberFormatOptions = { useGrouping }
  if (decimals !== undefined) {
    options.minimumFractionDigits = decimals
    options.maximumFractionDigits = decimals
  }
  if (spec.format === 'currency') {
    options.style = 'currency'
    options.currency = spec.currency || 'EUR'
  } else if (spec.format === 'percentage') {
    options.style = 'percent'
  }

  const nf = new Intl.NumberFormat(DISPLAY_LOCALE, options)

  return (value: unknown): string => {
    if (value === null || value === undefined || value === '') return ''
    const num = typeof value === 'number' ? value : Number(value)
    if (typeof value !== 'number' && Number.isNaN(num)) return String(value)
    if (Number.isNaN(num)) return ''
    return nf.format(num)
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run formatValue`
Expected: PASS (7 tests).

- [ ] **Step 5: Type-check**

Run: `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types`
Expected: only the pre-existing `TS1149` error.

- [ ] **Step 6: Commit**

```bash
git add src/utils/formatValue.ts src/utils/__test__/formatValue.test.ts
git commit -m "feat(frontend-redesign): add Intl-based value formatter util (phase 3)"
```

---

### Task 3: Wire `valueFormatter` into the AG-Grid column builder

**Files:**
- Modify: `src/components/model-components/list/CustomDatagrid.tsx` (the `columnDefs` useMemo, ~2400-2570)
- Test: `src/components/model-components/list/__test__/CustomDatagrid.test.tsx`

**Context:** The per-field column object in `columnDefs` currently has no `valueFormatter`. Add one driven by `fieldInfo.format`. For FK columns, add a label fallback formatter so grouping/export show the human label (`<fk>_label`) rather than the bare PK; the cell still renders via `cellRenderer`, and the underlying value stays the PK (non-breaking).

- [ ] **Step 1: Write the failing tests**

Add to `src/components/model-components/list/__test__/CustomDatagrid.test.tsx` (follow the file's existing pattern of capturing the props passed to `BareDatagridAGClient`; reuse the existing `capturedProps`/column-def helper already in that file):

```typescript
it('attaches a currency valueFormatter from field.format', () => {
  // fields fixture includes: { name: 'revenue', type: 'float',
  //   format: { format: 'currency', currency: 'EUR', decimals: 2 } }
  renderDatagrid({ fields: fieldsWithCurrency })
  const col = getColumnDef('revenue')
  expect(typeof col.valueFormatter).toBe('function')
  expect(col.valueFormatter({ value: 1234.5 })).toBe('€1,234.50')
})

it('does not attach a valueFormatter when field has no format spec', () => {
  renderDatagrid({ fields: fieldsPlain })
  const col = getColumnDef('name')
  expect(col.valueFormatter).toBeUndefined()
})

it('FK column valueFormatter prefers the <fk>_label sibling', () => {
  // fields fixture includes: { name: 'fund', type: 'foreign_key',
  //   target: 'fund', fk_label_field: 'label' }
  renderDatagrid({ fields: fieldsWithFk })
  const col = getColumnDef('fund')
  expect(col.valueFormatter({ value: 3, data: { fund: 3, fund_label: 'Alpha' } })).toBe('Alpha')
  // Falls back to the raw value when no label sibling present
  expect(col.valueFormatter({ value: 7, data: { fund: 7 } })).toBe('7')
})
```

> If the test file lacks `getColumnDef`/`renderDatagrid` helpers or the fixtures, add small local helpers mirroring the existing capture pattern and define `fieldsWithCurrency`, `fieldsPlain`, `fieldsWithFk` at the top of the relevant `describe` block. Match the existing fixtures' field shape exactly.

- [ ] **Step 2: Run tests to verify they fail**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run CustomDatagrid`
Expected: FAIL — `valueFormatter` is undefined.

- [ ] **Step 3: Implement the column `valueFormatter`**

At the top of `CustomDatagrid.tsx`, add the import:

```typescript
import { buildValueFormatter } from '../../../utils/formatValue'
```

Inside the `.map((fieldInfo: any) => { ... })` that builds each column def, compute a formatter before the `return {`:

```typescript
// Phase 3: backend-declared number/currency/percent formatting.
const numberFormatter = fieldInfo.format
  ? buildValueFormatter(fieldInfo.format)
  : undefined

// FK columns: show the human label (<fk>_label sibling) for
// grouping/export/copy while the cell value stays the PK.
const fkValueFormatter =
  fieldInfo.type === 'foreign_key'
    ? (params: any): string => {
        const label = params?.data?.[`${fieldInfo.name}_label`]
        if (label !== undefined && label !== null && label !== '') return String(label)
        return params?.value === null || params?.value === undefined
          ? ''
          : String(params.value)
      }
    : undefined

const valueFormatter = numberFormatter
  ? (params: any): string => numberFormatter(params?.value)
  : fkValueFormatter
```

Then add `valueFormatter` to the returned column object (only set it when defined to keep non-formatted columns clean):

```typescript
return {
  // ...existing keys...
  ...(valueFormatter ? { valueFormatter } : {}),
  // ...existing keys...
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run CustomDatagrid`
Expected: the 3 new tests PASS; only the documented pre-existing CustomDatagrid failures (embed/non-embed/hide-toolbar height, debounced column-resize event) remain.

- [ ] **Step 5: Type-check**

Run: `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types`
Expected: only the pre-existing `TS1149` error.

- [ ] **Step 6: Commit**

```bash
git add src/components/model-components/list/CustomDatagrid.tsx \
        src/components/model-components/list/__test__/CustomDatagrid.test.tsx
git commit -m "feat(frontend-redesign): per-column value formatter + FK label fallback (phase 3)"
```

---

### Task 4: FK cell chip using `<fk>_label`

**Files:**
- Modify: `src/components/model-components/list/FieldView.tsx` (`ForeignKeyReferenceContent`, ~68-114, and the `case 'foreign_key'` ~275-286)
- Test: `src/components/model-components/list/__test__/FieldView.test.tsx`

**Context:** Today the FK cell renders the related record's `short_description` via `ForeignKeyTooltip` wrapped in an Open-record button. Phase 3 renders a compact **chip** (a small dot + the `<fk>_label` text) for the FK value. The label comes from the *row* record's `<fk>_label` sibling, so no extra fetch is needed for the cell itself (the hover card in Task 5 does the fetch).

- [ ] **Step 1: Write the failing test**

Add to `src/components/model-components/list/__test__/FieldView.test.tsx` (mirror the file's existing render/record-context setup):

```typescript
it('renders FK cell as a chip showing the <fk>_label sibling', () => {
  renderFieldView({
    fieldInfo: { name: 'fund', type: 'foreign_key', target: 'fund', fk_label_field: 'label' },
    record: { id: 1, fund: 3, fund_label: 'Alpha Fund' },
  })
  expect(screen.getByText('Alpha Fund')).toBeInTheDocument()
})

it('falls back to the FK id when no label sibling is present', () => {
  renderFieldView({
    fieldInfo: { name: 'fund', type: 'foreign_key', target: 'fund', fk_label_field: 'label' },
    record: { id: 1, fund: 3 },
  })
  expect(screen.getByText('3')).toBeInTheDocument()
})
```

> If `renderFieldView` does not exist, add a small wrapper that renders `FieldView` inside the same providers the other tests in this file use (RecordContext etc.). Match the existing test setup exactly.

- [ ] **Step 2: Run test to verify it fails**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run FieldView`
Expected: FAIL — the label text is not found (cell still uses `short_description`/ReferenceField).

- [ ] **Step 3: Implement the chip**

Add imports at the top of `FieldView.tsx` if not present:

```typescript
import { Chip } from '@mui/material'
```

Add a small presentational chip component near `ForeignKeyReferenceContent`:

```typescript
/**
 * Compact FK chip: a teal dot + the human label (the row's `<fk>_label`
 * sibling). The underlying cell value remains the PK. Hover is handled by
 * ForeignKeyHoverCard (see FK case below).
 */
const ForeignKeyChip = ({ label }: { label: string }): JSX.Element => (
  <Chip
    size='small'
    variant='outlined'
    label={label}
    sx={{
      maxWidth: '100%',
      height: 22,
      borderColor: 'divider',
      '& .MuiChip-label': { px: 0.75, fontSize: 12, fontWeight: 500 },
      '&::before': {
        content: '""',
        display: 'inline-block',
        width: 6,
        height: 6,
        borderRadius: '50%',
        backgroundColor: '#14B4B4',
        ml: 0.75,
        flex: '0 0 auto',
      },
    }}
  />
)
```

Update the `case 'foreign_key'` branch to read the label sibling from the record and render the chip + hover card. Use the row record (via `useRecordContext` already available in this component path) to read `record[`${fieldInfo.name}_label`]`:

```typescript
case 'foreign_key': {
  assertIsForeignKeyFieldInfo(fieldInfo)
  const fkLabel = record?.[`${fieldInfo.name}_label`]
  const fkValue = record?.[fieldInfo.name]
  const display =
    fkLabel !== undefined && fkLabel !== null && fkLabel !== ''
      ? String(fkLabel)
      : fkValue === undefined || fkValue === null
        ? ''
        : String(fkValue)
  if (display === '') return null
  return (
    <ForeignKeyHoverCard record={record} referenceModel={fieldInfo.target} fieldInfo={fieldInfo}>
      <ForeignKeyChip label={display} />
    </ForeignKeyHoverCard>
  )
}
```

> Note: `ForeignKeyHoverCard` is created in Task 5. Until Task 5 lands, temporarily wrap the chip without the hover card (render `<ForeignKeyChip label={display} />` directly) so this task compiles and tests pass; Task 5 swaps in the card. Choose ONE: if doing tasks in order, render the bare chip here and add the wrapper in Task 6.

For task-ordering safety, in THIS task render the bare chip:

```typescript
  if (display === '') return null
  return <ForeignKeyChip label={display} />
```

- [ ] **Step 4: Run test to verify it passes**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run FieldView`
Expected: the 2 new tests PASS.

- [ ] **Step 5: Type-check**

Run: `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types`
Expected: only the pre-existing `TS1149` error.

- [ ] **Step 6: Commit**

```bash
git add src/components/model-components/list/FieldView.tsx \
        src/components/model-components/list/__test__/FieldView.test.tsx
git commit -m "feat(frontend-redesign): render FK cells as label chips from <fk>_label (phase 3)"
```

---

### Task 5: New `ForeignKeyHoverCard` (vertical label→value card)

**Files:**
- Create: `src/components/CustomTooltips/ForeignKeyHoverCard/ForeignKeyHoverCard.tsx`
- Test: `src/components/CustomTooltips/ForeignKeyHoverCard/__test__/ForeignKeyHoverCard.test.tsx`

**Context:** Replaces the horizontal-table `ForeignKeyTooltip` with a compact vertical card. Title = FK label; a "kind" line names the target model; values are right-aligned label→value rows. When `fk_preview` is true, fetch `/api/<target>/<id>?serializer=preview` via `useGetOne(target, { id, meta: { serializer: 'preview' } })` (same pattern as the existing tooltip's `useGetOne`). Fallback: first N readable fields from the default record. Footer: **Open record ↗** and **Filter by this value**. No Copy id. Hover stays interactive.

- [ ] **Step 1: Write the failing tests**

Create `src/components/CustomTooltips/ForeignKeyHoverCard/__test__/ForeignKeyHoverCard.test.tsx`:

```typescript
import React from 'react'
import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'

const mockUseGetOne = vi.fn()
const mockRedirect = vi.fn()
vi.mock('react-admin', () => ({
  useGetOne: (...args: any[]) => mockUseGetOne(...args),
  useRedirect: () => mockRedirect,
}))

import { ForeignKeyHoverCard } from '../ForeignKeyHoverCard'

const renderCard = (props: any) =>
  render(
    <ForeignKeyHoverCard
      record={{ id: 1, fund: 3, fund_label: 'Alpha Fund' }}
      referenceModel='fund'
      fieldInfo={{ name: 'fund', type: 'foreign_key', target: 'fund', fk_preview: true }}
      forceOpen
      {...props}
    >
      <span>child</span>
    </ForeignKeyHoverCard>,
  )

describe('ForeignKeyHoverCard', () => {
  it('renders the child trigger', () => {
    mockUseGetOne.mockReturnValue({ data: undefined, isLoading: true })
    renderCard({})
    expect(screen.getByText('child')).toBeInTheDocument()
  })

  it('shows preview fields as label/value rows when loaded', () => {
    mockUseGetOne.mockReturnValue({
      data: { id: 3, label: 'Alpha Fund', currency: 'EUR' },
      isLoading: false,
    })
    renderCard({})
    expect(screen.getByText('Alpha Fund')).toBeInTheDocument()
    expect(screen.getByText('EUR')).toBeInTheDocument()
  })

  it('renders Open record and Filter actions', () => {
    mockUseGetOne.mockReturnValue({ data: { id: 3, label: 'Alpha Fund' }, isLoading: false })
    renderCard({})
    expect(screen.getByRole('button', { name: /open record/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /filter by this value/i })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run ForeignKeyHoverCard`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `ForeignKeyHoverCard`**

Create `src/components/CustomTooltips/ForeignKeyHoverCard/ForeignKeyHoverCard.tsx`:

```typescript
import React from 'react'
import {
  Box,
  Tooltip,
  Card,
  Typography,
  Button,
  Divider,
  CircularProgress,
} from '@mui/material'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import FilterAltIcon from '@mui/icons-material/FilterAlt'
import { useGetOne, useRedirect } from 'react-admin'
import type { ForeignKeyFieldInfo } from '../../../api/endpoints/model-info'

interface ForeignKeyHoverCardProps {
  record: Record<string, any>
  referenceModel: string
  fieldInfo: ForeignKeyFieldInfo
  /** Test-only: render the card content without hovering. */
  forceOpen?: boolean
  /** Optional: filter the grid to rows sharing this FK value. */
  onFilterByValue?: (field: string, value: unknown) => void
  children: React.ReactElement
}

const MAX_FALLBACK_FIELDS = 6
const HIDDEN_KEYS = new Set(['id', 'ra_id'])

/** Title-case a snake/camel key for a human label. */
const humanize = (key: string): string =>
  key
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/\b\w/g, (c) => c.toUpperCase())

export const ForeignKeyHoverCard = ({
  record,
  referenceModel,
  fieldInfo,
  forceOpen = false,
  onFilterByValue,
  children,
}: ForeignKeyHoverCardProps): JSX.Element => {
  const [open, setOpen] = React.useState(forceOpen)
  const redirect = useRedirect()
  const fkValue = record?.[fieldInfo.name]
  const fkLabel = record?.[`${fieldInfo.name}_label`]
  const usePreview = fieldInfo.fk_preview === true

  const { data, isLoading } = useGetOne(
    referenceModel,
    {
      id: fkValue,
      ...(usePreview ? { meta: { serializer: 'preview' } } : {}),
    },
    {
      enabled: (open || forceOpen) && fkValue !== undefined && fkValue !== null,
      staleTime: 5 * 60 * 1000,
      gcTime: 15 * 60 * 1000,
      refetchOnWindowFocus: false,
    },
  )

  const rows: Array<{ key: string; label: string; value: string }> = React.useMemo(() => {
    if (!data) return []
    return Object.entries(data)
      .filter(([k, v]) => !HIDDEN_KEYS.has(k) && !k.endsWith('_label') && v !== null && v !== '')
      .slice(0, MAX_FALLBACK_FIELDS)
      .map(([k, v]) => ({ key: k, label: humanize(k), value: String(v) }))
  }, [data])

  const handleOpenRecord = (e: React.MouseEvent): void => {
    e.stopPropagation()
    redirect('show', referenceModel, fkValue)
  }
  const handleFilter = (e: React.MouseEvent): void => {
    e.stopPropagation()
    onFilterByValue?.(fieldInfo.name, fkValue)
  }

  const content = (
    <Card
      elevation={0}
      sx={{
        minWidth: 220,
        maxWidth: 320,
        p: 1.25,
        bgcolor: 'background.paper',
        border: '1px solid',
        borderColor: 'divider',
        borderRadius: '10px',
      }}
    >
      <Typography sx={{ fontWeight: 600, fontSize: 13, color: 'text.primary' }}>
        {fkLabel !== undefined && fkLabel !== null ? String(fkLabel) : String(fkValue ?? '')}
      </Typography>
      <Typography sx={{ fontSize: 11, color: 'text.secondary', mb: 0.75 }}>
        {humanize(referenceModel)}
      </Typography>
      <Divider sx={{ mb: 0.75 }} />

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 1 }}>
          <CircularProgress size={16} />
        </Box>
      ) : (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
          {rows.map((r) => (
            <Box
              key={r.key}
              sx={{ display: 'flex', justifyContent: 'space-between', gap: 1.5 }}
            >
              <Typography sx={{ fontSize: 12, color: 'text.secondary' }}>{r.label}</Typography>
              <Typography
                sx={{
                  fontSize: 12,
                  color: 'text.primary',
                  fontVariantNumeric: 'tabular-nums',
                  textAlign: 'right',
                }}
              >
                {r.value}
              </Typography>
            </Box>
          ))}
        </Box>
      )}

      <Divider sx={{ my: 0.75 }} />
      <Box sx={{ display: 'flex', gap: 0.5, justifyContent: 'flex-end' }}>
        <Button
          size='small'
          startIcon={<OpenInNewIcon fontSize='small' />}
          onClick={handleOpenRecord}
          sx={{ textTransform: 'none', fontSize: 12, color: '#0d9e9e' }}
        >
          Open record
        </Button>
        {onFilterByValue && (
          <Button
            size='small'
            startIcon={<FilterAltIcon fontSize='small' />}
            onClick={handleFilter}
            sx={{ textTransform: 'none', fontSize: 12, color: 'text.secondary' }}
          >
            Filter by this value
          </Button>
        )}
      </Box>
    </Card>
  )

  // Test path: render content inline so assertions don't depend on hover portals.
  if (forceOpen) {
    return (
      <>
        {children}
        {content}
      </>
    )
  }

  return (
    <Tooltip
      open={open}
      onOpen={() => setOpen(true)}
      onClose={() => setOpen(false)}
      title={content}
      placement='right-start'
      componentsProps={{
        tooltip: { sx: { bgcolor: 'transparent', p: 0, maxWidth: 'none' } },
      }}
      enterDelay={250}
      leaveDelay={120}
      disableInteractive={false}
    >
      {children}
    </Tooltip>
  )
}

export default ForeignKeyHoverCard
```

> The `forceOpen` Filter test passes `onFilterByValue` implicitly? No — the test does not pass `onFilterByValue`, yet asserts the Filter button exists. **Adjust:** always render the Filter button, but disable it (and no-op) when `onFilterByValue` is absent. Replace the `{onFilterByValue && (...)}` block with an always-rendered button whose `onClick` calls `onFilterByValue?.(...)` and is `disabled={!onFilterByValue}`. Keep the accessible name "Filter by this value".

Apply that adjustment so the test's `getByRole('button', { name: /filter by this value/i })` resolves.

- [ ] **Step 4: Run tests to verify they pass**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run ForeignKeyHoverCard`
Expected: PASS (3 tests).

- [ ] **Step 5: Type-check**

Run: `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types`
Expected: only the pre-existing `TS1149` error.

- [ ] **Step 6: Commit**

```bash
git add src/components/CustomTooltips/ForeignKeyHoverCard/ForeignKeyHoverCard.tsx \
        src/components/CustomTooltips/ForeignKeyHoverCard/__test__/ForeignKeyHoverCard.test.tsx
git commit -m "feat(frontend-redesign): vertical FK hover card with preview serializer (phase 3)"
```

---

### Task 6: Wire the hover card into the FK chip and retire the old tooltip path

**Files:**
- Modify: `src/components/model-components/list/FieldView.tsx` (`case 'foreign_key'`)
- Test: `src/components/model-components/list/__test__/FieldView.test.tsx`

**Context:** Task 4 rendered a bare chip. Now wrap it with `ForeignKeyHoverCard`. Pass an `onFilterByValue` if FieldView already has grid-filter access in scope; if not, omit it (the card renders the disabled Filter button). Leave `ForeignKeyTooltip.tsx` in place (it may still be used by audit-log views); only the grid FK cell switches to the new card.

- [ ] **Step 1: Update the FK case to use the hover card**

Add the import at the top of `FieldView.tsx`:

```typescript
import { ForeignKeyHoverCard } from '../../CustomTooltips/ForeignKeyHoverCard/ForeignKeyHoverCard'
```

Replace the bare-chip return from Task 4 with the wrapped version:

```typescript
  if (display === '') return null
  return (
    <ForeignKeyHoverCard record={record} referenceModel={fieldInfo.target} fieldInfo={fieldInfo}>
      <Box component='span' sx={{ display: 'inline-flex', maxWidth: '100%' }}>
        <ForeignKeyChip label={display} />
      </Box>
    </ForeignKeyHoverCard>
  )
```

> `ForeignKeyHoverCard` requires a single React element child that can hold a ref (MUI Tooltip). Wrapping the chip in a `Box component='span'` provides a ref-able element.

- [ ] **Step 2: Update the FieldView FK test for the hover wrapper**

The Task 4 tests still assert the label/id text is present — they should still pass since the chip is still rendered. Add a mock for `react-admin`'s `useGetOne`/`useRedirect` if the test file doesn't already mock react-admin (the hover card imports them). Add to the existing `vi.mock('react-admin', ...)` in this file:

```typescript
useGetOne: () => ({ data: undefined, isLoading: false }),
useRedirect: () => () => {},
```

(If react-admin is not yet mocked in this test file, add a `vi.mock('react-admin', () => ({ ... }))` that includes whatever symbols FieldView imports plus these two.)

- [ ] **Step 3: Run tests to verify they pass**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run FieldView`
Expected: PASS (the FK chip tests still find the label/id; no new failures).

- [ ] **Step 4: Type-check**

Run: `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types`
Expected: only the pre-existing `TS1149` error.

- [ ] **Step 5: Full relevant test sweep**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run formatValue CustomDatagrid FieldView ForeignKeyHoverCard CustomList`
Expected: all new tests PASS; only the previously documented pre-existing failures remain (CustomDatagrid height ×3 + column-resize event ×1, CustomList `onPotentialLateContent` ×1).

- [ ] **Step 6: Commit**

```bash
git add src/components/model-components/list/FieldView.tsx \
        src/components/model-components/list/__test__/FieldView.test.tsx
git commit -m "feat(frontend-redesign): use FK hover card in grid FK cells (phase 3)"
```

---

## Out of scope for Phase 3 (do NOT implement here)

- Per-view **format override editor** / Settings panel (that is Phase 5; the formatter already supports overrides being layered later).
- Reserved-name "preview" validation in view-save inputs (covered by Phase 1/Phase 5 work; only relevant when the Settings/view-save UI lands).
- Calculate-in-actions, status pills, calc-log drawer, history version columns (Phase 4).
- Sidebar redesign (Phase 6).

## Self-Review notes

- **Spec coverage:** §4.3 value formatting → Tasks 2,3. §4.2 FK chip → Task 4. §4.2 FK hover card (preview serializer, fallback first-N, Open record + Filter, no Copy id, interactive) → Tasks 5,6. Non-breaking FK (value stays PK) → Tasks 3,4,5 keep `record[field]` as PK; label is a sibling.
- **Type consistency:** `FormatSpec` defined in Task 1 is consumed by `buildValueFormatter` (Task 2) and `fieldInfo.format` (Task 3). `ForeignKeyFieldInfo.fk_preview`/`fk_label_field` (Task 1) consumed in Tasks 3-5. `buildValueFormatter` name used identically in Tasks 2 and 3.
- **Placeholder scan:** none — all steps carry concrete code. The one conditional ("bare chip in Task 4, wrap in Task 6") is explicit to keep tasks independently compilable.
- **Risk:** AG-Grid Tooltip vs MUI Tooltip — the FK cell uses MUI `Tooltip` (interactive) inside the cell renderer, consistent with the existing `ForeignKeyTooltip` approach, so no AG-Grid tooltip wiring is needed.
