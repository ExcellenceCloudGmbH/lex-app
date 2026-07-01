# Phase 4a — Calculation Status Pills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the plain text calculation-status display (`IN_PROGRESS` / `Yes` / `-`) in the grid with an inline, color-and-icon **status pill** (Calculating / Calculated / Error / Not calculated), dark-mode aware.

**Architecture:** A pure classifier (`classifyCalculationStatus`) maps the overloaded `is_calculated` value plus the live `isRunning` flag to one of four states. A presentational `CalculationStatusPill` renders an MUI `Chip` with the right label, color, and icon. `CalculateFunctionality`'s render swaps its two `<p>` text nodes for the pill. No change to calculation triggering, websockets, Redux, or permissions.

**Tech Stack:** React 18, MUI v5 (`Chip`, `CircularProgress`, icons), Vitest + Testing Library.

**Repo:** `/home/syscall/LUND_IT/process-admin-general-client` (branch `lex-app-v2-pac-latest`).

**Commands:** type-check `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types` (only the pre-existing `TS1149` casing error is allowed); tests `NPM_MARMELAB_TOKEN=dummy yarn test --run <pattern>`.

---

## Scope

**In scope:** the status pill component + classifier, and wiring it into `CalculateFunctionality`'s status display.

**Out of scope (later phases):** moving the Calculate button into the actions column (Phase 4a-part2), the live right-side calc-log drawer (Phase 4c), and all History-mode work — version columns + per-row timeline drawer (Phase 4b).

**Non-negotiables:** dark-mode must stay working; keep the navy/teal palette (`TEAL #14B4B4`, `NAVY #283C50`); the pill must not change any calculation behavior — it only replaces the status *display*.

## File Structure

- Create `src/components/model-components/CalculateFunctionality/calculationStatus.ts` — pure classifier `classifyCalculationStatus(value, isRunning) => CalculationStatusKind`. One responsibility: normalize the overloaded status.
- Create `src/components/model-components/CalculateFunctionality/CalculationStatusPill.tsx` — presentational pill. Depends only on the classifier + MUI.
- Create tests alongside each.
- Modify `src/components/model-components/CalculateFunctionality/CalculateFunctionality.tsx` — swap the two `<p>` nodes (line ~322) for `<CalculationStatusPill />`.

---

### Task 1: Pure status classifier

**Files:**
- Create: `src/components/model-components/CalculateFunctionality/calculationStatus.ts`
- Test: `src/components/model-components/CalculateFunctionality/__test__/calculationStatus.test.ts`

**Context:** `record.is_calculated` is overloaded — it can be a boolean (`true`/`false`), a boolean-ish string (`"true"`/`"false"`/`"Yes"`/`"No"`), a status string (`"IN_PROGRESS"`, `"SUCCESS"`, `"ERROR"`, `"CANCELLED"`, `"NOT_CALCULATED"`), or empty (`null`/`""`). The live-running signal comes from `CalculateFunctionality`'s existing `isRunning` boolean (local optimistic click + Redux calc entry + `is_calculated === 'IN_PROGRESS'`). The classifier collapses all of that into four kinds.

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, it, expect } from 'vitest'
import { classifyCalculationStatus } from '../calculationStatus'

describe('classifyCalculationStatus', () => {
  it('returns calculating when isRunning is true regardless of value', () => {
    expect(classifyCalculationStatus('SUCCESS', true)).toBe('calculating')
    expect(classifyCalculationStatus(null, true)).toBe('calculating')
  })

  it('returns calculating for IN_PROGRESS value', () => {
    expect(classifyCalculationStatus('IN_PROGRESS', false)).toBe('calculating')
    expect(classifyCalculationStatus('in_progress', false)).toBe('calculating')
  })

  it('returns success for success-ish values', () => {
    expect(classifyCalculationStatus('SUCCESS', false)).toBe('success')
    expect(classifyCalculationStatus(true, false)).toBe('success')
    expect(classifyCalculationStatus('true', false)).toBe('success')
    expect(classifyCalculationStatus('Yes', false)).toBe('success')
  })

  it('returns error for error-ish values', () => {
    expect(classifyCalculationStatus('ERROR', false)).toBe('error')
    expect(classifyCalculationStatus('failed', false)).toBe('error')
    expect(classifyCalculationStatus('CANCELLED', false)).toBe('error')
  })

  it('returns idle for not-calculated / falsey / empty values', () => {
    expect(classifyCalculationStatus(false, false)).toBe('idle')
    expect(classifyCalculationStatus('No', false)).toBe('idle')
    expect(classifyCalculationStatus('NOT_CALCULATED', false)).toBe('idle')
    expect(classifyCalculationStatus(null, false)).toBe('idle')
    expect(classifyCalculationStatus('', false)).toBe('idle')
    expect(classifyCalculationStatus(undefined, false)).toBe('idle')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run calculationStatus`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```typescript
export type CalculationStatusKind = 'calculating' | 'success' | 'error' | 'idle'

const SUCCESS_TOKENS = new Set(['success', 'true', 'yes', 'completed', 'done', 'ok'])
const ERROR_TOKENS = new Set(['error', 'failed', 'failure', 'cancelled', 'canceled', 'aborted'])

/**
 * Collapse the overloaded `is_calculated` value plus the live-running flag
 * into a single display state. `isRunning` always wins.
 */
export function classifyCalculationStatus(
  value: unknown,
  isRunning: boolean,
): CalculationStatusKind {
  if (isRunning) return 'calculating'

  const normalized = typeof value === 'string' ? value.trim().toLowerCase() : value

  if (normalized === 'in_progress') return 'calculating'
  if (value === true || (typeof normalized === 'string' && SUCCESS_TOKENS.has(normalized))) {
    return 'success'
  }
  if (typeof normalized === 'string' && ERROR_TOKENS.has(normalized)) return 'error'
  return 'idle'
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run calculationStatus`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/components/model-components/CalculateFunctionality/calculationStatus.ts \
        src/components/model-components/CalculateFunctionality/__test__/calculationStatus.test.ts
git commit -m "feat(frontend-redesign): pure calculation-status classifier (phase 4a)"
```

---

### Task 2: `CalculationStatusPill` presentational component

**Files:**
- Create: `src/components/model-components/CalculateFunctionality/CalculationStatusPill.tsx`
- Test: `src/components/model-components/CalculateFunctionality/__test__/CalculationStatusPill.test.tsx`

**Context:** Renders an MUI `Chip` (`size='small'`) driven by the classifier. Colors use theme-aware `sx` so dark mode stays legible. Calculating shows a small `CircularProgress`; success a check icon; error an error icon; idle a muted dot. `compact` prop shrinks it for compact grid density. Accessible: each state has a stable `aria-label` and visible text.

- [ ] **Step 1: Write the failing test**

```typescript
import React from 'react'
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { CalculationStatusPill } from '../CalculationStatusPill'

describe('CalculationStatusPill', () => {
  it('renders Calculating with a progress indicator when running', () => {
    render(<CalculationStatusPill value={null} isRunning />)
    expect(screen.getByText('Calculating')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toBeInTheDocument()
  })

  it('renders Calculated for success values', () => {
    render(<CalculationStatusPill value='SUCCESS' isRunning={false} />)
    expect(screen.getByText('Calculated')).toBeInTheDocument()
  })

  it('renders Error for error values', () => {
    render(<CalculationStatusPill value='ERROR' isRunning={false} />)
    expect(screen.getByText('Error')).toBeInTheDocument()
  })

  it('renders Not calculated for idle values', () => {
    render(<CalculationStatusPill value={false} isRunning={false} />)
    expect(screen.getByText('Not calculated')).toBeInTheDocument()
  })

  it('exposes an accessible label matching the state', () => {
    render(<CalculationStatusPill value='ERROR' isRunning={false} />)
    expect(screen.getByLabelText('Calculation status: Error')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run CalculationStatusPill`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```typescript
import React from 'react'
import { Chip, CircularProgress } from '@mui/material'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import ErrorIcon from '@mui/icons-material/Error'
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked'
import { classifyCalculationStatus, type CalculationStatusKind } from './calculationStatus'

interface CalculationStatusPillProps {
  value: unknown
  isRunning: boolean
  compact?: boolean
}

const TEAL = '#14B4B4'

interface PillStyle {
  label: string
  icon: React.ReactNode
  fg: string
  bg: string
  border: string
}

const buildStyles = (compact: boolean): Record<CalculationStatusKind, PillStyle> => {
  const iconSize = compact ? 12 : 14
  return {
    calculating: {
      label: 'Calculating',
      icon: <CircularProgress size={iconSize} thickness={6} sx={{ color: TEAL }} />,
      fg: TEAL,
      bg: 'rgba(20, 180, 180, 0.10)',
      border: 'rgba(20, 180, 180, 0.45)',
    },
    success: {
      label: 'Calculated',
      icon: <CheckCircleIcon sx={{ fontSize: iconSize }} />,
      fg: '#2e7d32',
      bg: 'rgba(46, 125, 50, 0.10)',
      border: 'rgba(46, 125, 50, 0.45)',
    },
    error: {
      label: 'Error',
      icon: <ErrorIcon sx={{ fontSize: iconSize }} />,
      fg: '#d32f2f',
      bg: 'rgba(211, 47, 47, 0.10)',
      border: 'rgba(211, 47, 47, 0.45)',
    },
    idle: {
      label: 'Not calculated',
      icon: <RadioButtonUncheckedIcon sx={{ fontSize: iconSize }} />,
      fg: 'text.secondary',
      bg: 'transparent',
      border: 'divider',
    },
  }
}

export function CalculationStatusPill({
  value,
  isRunning,
  compact = false,
}: CalculationStatusPillProps): JSX.Element {
  const kind = classifyCalculationStatus(value, isRunning)
  const style = buildStyles(compact)[kind]

  return (
    <Chip
      size='small'
      variant='outlined'
      icon={<span style={{ display: 'inline-flex', marginLeft: 6 }}>{style.icon}</span>}
      label={style.label}
      aria-label={`Calculation status: ${style.label}`}
      sx={{
        height: compact ? 20 : 24,
        fontSize: compact ? 11 : 12,
        fontWeight: 500,
        color: style.fg,
        borderColor: style.border,
        backgroundColor: style.bg,
        '& .MuiChip-label': { px: 0.75 },
        '& .MuiChip-icon': { color: style.fg, ml: 0 },
      }}
    />
  )
}

export default CalculationStatusPill
```

- [ ] **Step 4: Run test to verify it passes**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run CalculationStatusPill`
Expected: PASS (5 tests).

- [ ] **Step 5: Type-check**

Run: `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types`
Expected: only the pre-existing `TS1149` error.

- [ ] **Step 6: Commit**

```bash
git add src/components/model-components/CalculateFunctionality/CalculationStatusPill.tsx \
        src/components/model-components/CalculateFunctionality/__test__/CalculationStatusPill.test.tsx
git commit -m "feat(frontend-redesign): calculation status pill component (phase 4a)"
```

---

### Task 3: Wire the pill into `CalculateFunctionality`

**Files:**
- Modify: `src/components/model-components/CalculateFunctionality/CalculateFunctionality.tsx` (render block ~line 320-322)
- Test: `src/components/model-components/__test__/CalculateFunctionality.test.tsx` (add one assertion)

**Context:** The render currently shows `{isRunning ? <p>IN_PROGRESS</p> : <p>{formatCalculatedValue(record.is_calculated)}</p>}`. Replace those two `<p>` nodes with the pill, passing `value={record.is_calculated}`, `isRunning={isRunning}`, and `compact={isCompactMode}`. Leave the spinner/button branch untouched — the pill is the status label that sits alongside the action control. `formatCalculatedValue` stays exported (still used elsewhere/tests).

- [ ] **Step 1: Add the import**

At the top of `CalculateFunctionality.tsx`, with the other local imports:

```typescript
import CalculationStatusPill from './CalculationStatusPill'
```

- [ ] **Step 2: Replace the status text nodes**

Replace:

```typescript
      {isRunning ? <p>IN_PROGRESS</p> : <p>{formatCalculatedValue(record.is_calculated)}</p>}
```

with:

```typescript
      <CalculationStatusPill
        value={record.is_calculated}
        isRunning={isRunning}
        compact={isCompactMode}
      />
```

- [ ] **Step 3: Add a wiring assertion to the existing test**

In `src/components/model-components/__test__/CalculateFunctionality.test.tsx`, add:

```typescript
  it('shows the Calculating pill while a calculation is running', () => {
    renderCalculateFunctionality({ isCalculated: 'IN_PROGRESS' })
    expect(screen.getByText('Calculating')).toBeInTheDocument()
  })
```

> Note: the existing test mocks `react-loading` as `calculation-spinner` and mocks `CalculationLogs` as `null`; the pill is a real MUI Chip and needs no new mock. If `screen` is not already imported in that file, add it to the `@testing-library/react` import.

- [ ] **Step 4: Run the CalculateFunctionality tests**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run CalculateFunctionality`
Expected: PASS (existing tests + the new one).

- [ ] **Step 5: Type-check**

Run: `NPM_MARMELAB_TOKEN=dummy yarn ts:check-types`
Expected: only the pre-existing `TS1149` error.

- [ ] **Step 6: Full relevant sweep**

Run: `NPM_MARMELAB_TOKEN=dummy yarn test --run calculationStatus CalculationStatusPill CalculateFunctionality FieldView`
Expected: all pass except any previously-documented pre-existing failures (none expected in these files).

- [ ] **Step 7: Commit**

```bash
git add src/components/model-components/CalculateFunctionality/CalculateFunctionality.tsx \
        src/components/model-components/__test__/CalculateFunctionality.test.tsx
git commit -m "feat(frontend-redesign): use status pill in calculate cell (phase 4a)"
```

---

## Self-Review notes

- **Spec coverage:** §4.5 "inline status pill — Calculating / Success / Error — with colors + icons, dark-mode compatible" → Tasks 1-3. The other §4.5 items (Calculate button into actions column; live drawer) are explicitly deferred and listed under Scope.
- **Type consistency:** `CalculationStatusKind` defined in Task 1 is imported by Task 2. `classifyCalculationStatus(value, isRunning)` signature identical across Tasks 1-2. Pill props (`value`, `isRunning`, `compact`) identical in Tasks 2-3.
- **Placeholder scan:** none — every step carries concrete code.
- **Risk:** `is_calculated` may be `true`/`false` booleans on non-CalculationModel rows; the classifier maps `false`→idle ("Not calculated") and `true`→success ("Calculated"), which matches the prior `formatCalculatedValue` Yes/No intent while upgrading the presentation. If a boolean field unrelated to calculation ever routed here it would show a pill, but this component only renders inside `CalculateFunctionality`, which is only mounted for the `is_calculated` field (FieldView `case 'is_calculated'`), so blast radius is contained.
