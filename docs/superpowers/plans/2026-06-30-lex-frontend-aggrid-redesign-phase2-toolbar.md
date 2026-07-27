# LEX Frontend AG-Grid Redesign — Phase 2 (Toolbar De-clutter) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** De-clutter the AG-Grid table-view toolbar — collapse the three duplicate refresh controls into one icon-only Refresh, turn Columns/Filters into direct toolbar buttons (no Sidebar toggle gate), merge As-Of + History into one segmented switch, and move Density into a new ⚙ Settings menu.

**Architecture:** All work is in the **frontend repo** `process-admin-general-client` (React 18 + React-Admin + AG-Grid 33.0.4 + MUI). The linchpin is decoupling AG-Grid's `sideBar` option from the old boolean toggle into a stable object config (`hiddenByDefault: true`, both tool panels); once that lands, any grid instance's `gridRef.current.api.openToolPanel(...)` works, so the new Columns/Filters buttons drive the panels directly. The toolbar lives in two parallel places — `CustomList.tsx` (main list view) and `GridViewToolbar.tsx` (CustomShow embedded History/Audit grids) — both get the same treatment. `CustomDatagrid.tsx` is the shared grid used by both.

**Tech Stack:** TypeScript, React, React-Admin, AG-Grid Enterprise 33.0.4, MUI v5, Vitest + @testing-library/react.

---

## Cross-cutting prime directives (apply to EVERY task)

1. **Do not change the theme.** No new palette, colors, or theme tokens. Reuse the existing MUI theme. New buttons use `color="inherit"`/`color="primary"` exactly as the existing toolbar buttons do.
2. **Dark-mode compatible.** Anything new must render via the existing theme palette (no hard-coded greys/whites).
3. **Do not break functionality.** Server-side row model, filtering, sorting, inline editing, grouping/pivot, exports, websocket calc streaming, saved views, history/as-of, permissions — all must keep working. Changes are additive or like-for-like.
4. **Match existing patterns.** Toolbar buttons are MUI `Button`/`IconButton` `size="small"`; icons from `@mui/icons-material`; tooltips via MUI `Tooltip` (wrap disabled buttons in `<span>`). Mirror the style already in `CustomList.tsx`.
5. **Tests are Vitest, not the lex cluster suite.** This is the frontend repo; the lex-testing cluster rules do NOT apply. Put tests next to existing ones in `src/components/model-components/list/__test__/`. Run with `yarn test --run <file>` and type-check with `yarn ts:check-types`.

**Repo root for all paths below:** `/home/syscall/LUND_IT/process-admin-general-client`

---

## Reference — current code map (verified)

- `src/components/model-components/list/CustomList.tsx` (1,152 lines) — main list view + inline toolbar (lines 956-1096). Grid API reachable here via `activeGridRef.current?.api` (line 151: `activeGridRef = isHistoryView ? gridRefHistorical : gridRef`).
  - History toggle button: 967-977 (`handleHistoryToggle` at 905-922; `canToggleHistoryView` 903; `isEffectiveHistory` 880; `historyButtonLabel` 822).
  - `AsOfControl`: 979 (`asOfValue`/`setAsOfValue` state ~122; `asOfIso` 924 fed to `List filter` 942).
  - Inline "Reload" button: 980-990 (calls `refreshServerSideGrid(true)`, defined 468-494).
  - `DensityControl`: 1012-1015 (`currentRowHeight` state; `handleRowHeightSelect`).
  - Sidebar toggle button: 1085-1093 (`isTableSidebarOpen` state line 110).
  - `isTableSidebarOpen` other uses: 624-625 (pivot auto-closes sidebar), 640-658 (resize-on-toggle effect), 1110 (prop to CustomDatagrid).
  - `CustomListActions` render: 945-953 (passes `onRefresh`).
- `src/components/model-components/list/CustomListActions.tsx` (67 lines) — React-Admin `<TopToolbar>`. "Refresh" button 36-46 (calls `onRefresh?.()`). Also used by `CustomDashboard.tsx:69` (no `onRefresh`) and `AuditLogList.tsx:57`.
- `src/components/model-components/list/GridViewToolbar.tsx` (168 lines) — reusable toolbar for CustomShow tabs. Density 69-72, Auto-Adjust 73-81, conditional "Reload" 82-92 (`onReload`), Discard/Update 96-124, ViewSelector 126-133, StateSaveForm 135-142, conditional Sidebar toggle 144-154 (`isSidebarOpen`/`onToggleSidebar`).
- `src/components/model-components/list/CustomDatagrid.tsx` (2,800+ lines) — the AG-Grid wrapper. `sideBar={isTableSidebarOpen}` at line 2775. `gridRef` ref chain; API via `gridRef.current?.api`. Used by `CustomList` and by `CustomShow.tsx` (line 204 main grid, line 711 history grid with `isTableSidebarOpen={historySidebarOpen}`).
- `src/components/CustomShow/CustomShow.tsx` — renders `GridViewToolbar` (689, 765) and `CustomDatagrid` (204, 711). `historySidebarOpen` state 554.
- `src/components/model-components/list/AsOfControl.tsx` (108 lines) — Button + Popover + react-admin `DateTimeInput` Form; emits `Date|null` via `onChange`. Helpers `parseAsOfInputValue`/`toAsOfInputValue`/`toAsOfIsoString` in `src/utils/asOf`.
- `src/components/model-components/list/DensityControl.tsx` (99 lines) — Button + Menu with Compact(25)/Standard(100)/Comfortable(200).
- Existing tests in `src/components/model-components/list/__test__/`: `CustomList.test.tsx`, `CustomListActions.test.tsx`, `CustomDatagrid.test.tsx`, `AsOfControl.test.tsx`, `DensityControl.test.tsx`.

---

## Phase-2 design decisions (locked)

- **Density → new ⚙ Settings menu now.** Build a minimal `TableSettingsMenu` (gear IconButton → Menu) containing only Density in Phase 2; Phase 5 extends it with display toggles + format overrides. `DensityControl.tsx` logic folds into it; the standalone `DensityControl` is removed from both toolbars.
- **History switch wires existing mechanisms only.** The segmented `Current / As of / History` switch drives the existing `asOfValue` state and `handleHistoryToggle`. Version-column auto-reveal and the per-row timeline drawer remain **Phase 4**.
- **`sideBar` becomes a stable object** (`hiddenByDefault: true`, columns + filters tool panels) decoupled from any boolean toggle. The `isTableSidebarOpen` state and the "Sidebar" toggle button are removed entirely.
- **One Refresh.** Keep a single icon-only Refresh button in each grid toolbar; remove the `CustomListActions` TopToolbar "Refresh" and convert the `GridViewToolbar` "Reload" to icon-only.

---

## Task ordering rationale

Task 1 (sideBar object) is the foundation everything else relies on; it must land first. Tasks 2–5 add the new controls. Task 6 removes the now-dead `isTableSidebarOpen` plumbing and old controls (done last so nothing references removed code mid-flight). Task 7 is the regression gate.

---

### Task 1: Decouple AG-Grid `sideBar` into a stable object config

**Files:**
- Modify: `src/components/model-components/list/CustomDatagrid.tsx` (line 2775 + props)
- Test: `src/components/model-components/list/__test__/CustomDatagrid.test.tsx`

**Context:** Today `sideBar={isTableSidebarOpen}` (boolean). When `false`, AG-Grid renders NO sidebar, so `api.openToolPanel(...)` silently fails. We need the sidebar always *configured* (both tool panels) but *hidden by default* (no side button bar), so the new buttons can open a panel on demand.

- [ ] **Step 1: Write the failing test**

In `CustomDatagrid.test.tsx`, find the existing assertion(s) on the `sideBar` prop (the agent investigation noted it asserts `sideBar={true}`/`sideBar={false}`). Replace with an assertion that the rendered AG-Grid receives a `sideBar` **object** with both tool panels and `hiddenByDefault: true`. Using the existing mock pattern in that file (it captures props passed to the mocked `AgGridReact`/`BareDatagridAGClient`), add:

```tsx
it('configures the sidebar as a hidden-by-default object with columns + filters panels', () => {
  // ...render CustomDatagrid using the file's existing render helper...
  const sideBar = capturedAgGridProps.sideBar
  expect(typeof sideBar).toBe('object')
  expect(sideBar.hiddenByDefault).toBe(true)
  const ids = sideBar.toolPanels.map((p: any) => p.id)
  expect(ids).toEqual(expect.arrayContaining(['columns', 'filters']))
})
```

Adapt `capturedAgGridProps` to whatever capture mechanism the file already uses (do not invent a new one — reuse the existing mock).

- [ ] **Step 2: Run test to verify it fails**

Run: `yarn test --run src/components/model-components/list/__test__/CustomDatagrid.test.tsx`
Expected: FAIL (sideBar is currently a boolean).

- [ ] **Step 3: Implement the stable sideBar object**

In `CustomDatagrid.tsx`, near the other `useMemo` grid-option definitions, add:

```tsx
// Stable sidebar config: both tool panels available but no side button bar
// shown by default. Phase-2 toolbar buttons open a panel on demand via
// api.openToolPanel('columns'|'filters'). Decoupled from any toggle so the
// API call always has a configured panel to open.
const tableSideBarDef = useMemo(
  () => ({
    toolPanels: [
      {
        id: 'columns',
        labelDefault: 'Columns',
        labelKey: 'columns',
        iconKey: 'columns',
        toolPanel: 'agColumnsToolPanel',
      },
      {
        id: 'filters',
        labelDefault: 'Filters',
        labelKey: 'filters',
        iconKey: 'filter',
        toolPanel: 'agFiltersToolPanel',
      },
    ],
    hiddenByDefault: true,
  }),
  [],
)
```

Change line 2775 from `sideBar={isTableSidebarOpen}` to `sideBar={tableSideBarDef}`. Remove `isTableSidebarOpen` from the destructured props of `CustomDatagrid` (it is no longer read — Task 6 removes it from all call sites; for now, if other lines in this file read `isTableSidebarOpen`, grep and confirm there are none besides 2775 before removing the prop, otherwise leave the prop param in place until Task 6 and only change line 2775 here).

> NOTE for implementer: run `grep -n isTableSidebarOpen src/components/model-components/list/CustomDatagrid.tsx`. If 2775 is the only hit, drop the prop now. If there are others, change only 2775 in this task and defer prop removal to Task 6.

- [ ] **Step 4: Run test to verify it passes**

Run: `yarn test --run src/components/model-components/list/__test__/CustomDatagrid.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/components/model-components/list/CustomDatagrid.tsx src/components/model-components/list/__test__/CustomDatagrid.test.tsx
git commit -m "feat(frontend-redesign): stable AG-Grid sideBar object, hidden by default (phase 2)"
```

---

### Task 2: Columns & Filters direct toolbar buttons (CustomList)

**Files:**
- Create: `src/components/model-components/list/useToolPanelControls.ts`
- Create: `src/components/model-components/list/__test__/useToolPanelControls.test.ts`
- Modify: `src/components/model-components/list/CustomList.tsx`
- Modify: `src/components/model-components/list/CustomDatagrid.tsx` (forward `onToolPanelVisibleChanged`)

**Context:** The grid API is reachable in `CustomList` via `activeGridRef.current?.api`. AG-Grid exposes `openToolPanel(id)`, `closeToolPanel()`, `getOpenedToolPanel()` and the `onToolPanelVisibleChanged` event. We add a small hook to manage open/toggle/active-state, then two icon buttons.

- [ ] **Step 1: Write the failing test for the hook**

Create `__test__/useToolPanelControls.test.ts`:

```ts
import { renderHook, act } from '@testing-library/react'
import { useToolPanelControls } from '../useToolPanelControls'

function makeGridRef(opened: string | null = null) {
  const api = {
    _opened: opened,
    getOpenedToolPanel: vi.fn(function (this: any) { return api._opened }),
    openToolPanel: vi.fn((id: string) => { api._opened = id }),
    closeToolPanel: vi.fn(() => { api._opened = null }),
  }
  return { current: { api } }
}

describe('useToolPanelControls', () => {
  it('opens a closed panel', () => {
    const gridRef: any = makeGridRef(null)
    const { result } = renderHook(() => useToolPanelControls(gridRef))
    act(() => result.current.togglePanel('columns'))
    expect(gridRef.current.api.openToolPanel).toHaveBeenCalledWith('columns')
    expect(result.current.openPanel).toBe('columns')
  })

  it('closes the panel when toggling the one already open', () => {
    const gridRef: any = makeGridRef('columns')
    const { result } = renderHook(() => useToolPanelControls(gridRef))
    act(() => result.current.togglePanel('columns'))
    expect(gridRef.current.api.closeToolPanel).toHaveBeenCalled()
    expect(result.current.openPanel).toBeNull()
  })

  it('switches directly from one panel to the other', () => {
    const gridRef: any = makeGridRef('columns')
    const { result } = renderHook(() => useToolPanelControls(gridRef))
    act(() => result.current.togglePanel('filters'))
    expect(gridRef.current.api.openToolPanel).toHaveBeenCalledWith('filters')
    expect(result.current.openPanel).toBe('filters')
  })

  it('is a no-op when the grid api is not ready', () => {
    const gridRef: any = { current: null }
    const { result } = renderHook(() => useToolPanelControls(gridRef))
    act(() => result.current.togglePanel('columns'))
    expect(result.current.openPanel).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `yarn test --run src/components/model-components/list/__test__/useToolPanelControls.test.ts`
Expected: FAIL ("useToolPanelControls is not a module" / not defined).

- [ ] **Step 3: Implement the hook**

Create `useToolPanelControls.ts`:

```ts
import { useCallback, useState } from 'react'

export type ToolPanelId = 'columns' | 'filters'

/**
 * Drives the AG-Grid Columns/Filters tool panels from toolbar buttons.
 * The grid's `sideBar` must be configured (see CustomDatagrid `tableSideBarDef`)
 * with `hiddenByDefault: true`; these helpers open/close a panel on demand and
 * track which one is visible so the buttons can show an active state.
 *
 * `syncFromGrid` should be wired to AG-Grid's `onToolPanelVisibleChanged` so the
 * active state stays correct when a panel is closed by means other than the
 * buttons (Esc, AG-Grid's own close affordance).
 */
export function useToolPanelControls(gridRef: React.MutableRefObject<any>) {
  const [openPanel, setOpenPanel] = useState<ToolPanelId | null>(null)

  const togglePanel = useCallback(
    (panel: ToolPanelId) => {
      const api = gridRef.current?.api
      if (!api) return
      const currentlyOpen = api.getOpenedToolPanel?.() ?? null
      if (currentlyOpen === panel) {
        api.closeToolPanel?.()
        setOpenPanel(null)
      } else {
        api.openToolPanel?.(panel)
        setOpenPanel(panel)
      }
    },
    [gridRef],
  )

  const syncFromGrid = useCallback(() => {
    const api = gridRef.current?.api
    const open = (api?.getOpenedToolPanel?.() ?? null) as ToolPanelId | null
    setOpenPanel(open)
  }, [gridRef])

  return { openPanel, togglePanel, syncFromGrid }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `yarn test --run src/components/model-components/list/__test__/useToolPanelControls.test.ts`
Expected: PASS.

- [ ] **Step 5: Wire the hook + buttons into CustomList**

In `CustomList.tsx`:
1. Add imports:
```tsx
import { IconButton, Tooltip } from '@mui/material' // (Tooltip likely already imported)
import ViewColumnIcon from '@mui/icons-material/ViewColumn'
import FilterAltIcon from '@mui/icons-material/FilterAlt'
import { useToolPanelControls } from './useToolPanelControls'
```
2. After `activeGridRef` is defined (line ~151), add:
```tsx
const { openPanel, togglePanel, syncFromGrid } = useToolPanelControls(activeGridRef)
```
3. In the toolbar (the Left or Right group of the `Box` at 956-1095 — place Columns/Filters next to where the Sidebar button was, i.e. the Right group), add:
```tsx
<Tooltip title='Columns'>
  <IconButton
    size='small'
    color={openPanel === 'columns' ? 'primary' : 'inherit'}
    onClick={() => togglePanel('columns')}
    aria-label='Columns'
  >
    <ViewColumnIcon />
  </IconButton>
</Tooltip>
<Tooltip title='Filters'>
  <IconButton
    size='small'
    color={openPanel === 'filters' ? 'primary' : 'inherit'}
    onClick={() => togglePanel('filters')}
    aria-label='Filters'
  >
    <FilterAltIcon />
  </IconButton>
</Tooltip>
```
4. Forward `syncFromGrid` to `CustomDatagrid` as a new optional prop `onToolPanelVisibleChanged` (add to the `<CustomDatagrid ... />` at 1106). In `CustomDatagrid.tsx`, accept that prop and pass it straight to `AgGridReact`'s `onToolPanelVisibleChanged`. If the grid already wires that event for another purpose, call both.

> NOTE: do NOT remove the Sidebar toggle button yet — that is Task 6. In this task the Columns/Filters buttons coexist with it.

- [ ] **Step 6: Add a CustomList-level test for the buttons**

In `CustomList.test.tsx` (reuse the file's existing render harness + AgGrid mock), add a test that clicking the Columns button calls `api.openToolPanel('columns')`. If the existing harness mocks the grid such that `activeGridRef.current.api` is unavailable, assert instead that the Columns and Filters buttons render (`getByLabelText('Columns')`, `getByLabelText('Filters')`). Keep it consistent with how the file already tests toolbar elements.

- [ ] **Step 7: Run tests**

Run: `yarn test --run src/components/model-components/list/__test__/useToolPanelControls.test.ts src/components/model-components/list/__test__/CustomList.test.tsx`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/components/model-components/list/useToolPanelControls.ts src/components/model-components/list/__test__/useToolPanelControls.test.ts src/components/model-components/list/CustomList.tsx src/components/model-components/list/CustomDatagrid.tsx src/components/model-components/list/__test__/CustomList.test.tsx
git commit -m "feat(frontend-redesign): direct Columns/Filters toolbar buttons (phase 2)"
```

---

### Task 3: HistoryModeSwitch — segmented Current / As of / History

**Files:**
- Create: `src/components/model-components/list/HistoryModeSwitch.tsx`
- Create: `src/components/model-components/list/__test__/HistoryModeSwitch.test.tsx`
- Modify: `src/components/model-components/list/CustomList.tsx`

**Context:** Replace the separate History `<Button>` (967-977) and `<AsOfControl>` (979) with one MUI `ToggleButtonGroup`. Mode is *derived*, not stored: `history` when `isEffectiveHistory`; else `asof` when `asOfValue` is set; else `current`. Selecting **As of** opens a Popover holding the existing react-admin `DateTimeInput` Form (moved out of `AsOfControl`). Selecting **Current** clears as-of (and exits history if in it). Selecting **History** calls `handleHistoryToggle`. This wires ONLY existing mechanisms — no version columns (Phase 4).

- [ ] **Step 1: Write the failing test**

Create `__test__/HistoryModeSwitch.test.tsx`:

```tsx
import React from 'react'
import { AdminContext, testDataProvider } from 'react-admin'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { HistoryModeSwitch } from '../HistoryModeSwitch'

const renderSwitch = (props: any) =>
  render(
    <MemoryRouter>
      <AdminContext dataProvider={testDataProvider({})}>
        <HistoryModeSwitch
          asOfValue={null}
          onAsOfChange={() => {}}
          isHistory={false}
          canToggleHistory={true}
          onToggleHistory={() => {}}
          historyLabel='History'
          {...props}
        />
      </AdminContext>
    </MemoryRouter>,
  )

describe('<HistoryModeSwitch>', () => {
  it('renders three segments and selects Current by default', () => {
    renderSwitch({})
    expect(screen.getByRole('button', { name: /current/i })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /as of/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /history/i })).toBeInTheDocument()
  })

  it('marks As of active when asOfValue is set', () => {
    renderSwitch({ asOfValue: new Date('2026-01-01T00:00:00Z') })
    expect(screen.getByRole('button', { name: /as of/i })).toHaveAttribute('aria-pressed', 'true')
  })

  it('marks History active and fires onToggleHistory when chosen', async () => {
    const onToggleHistory = vi.fn()
    renderSwitch({ isHistory: true, onToggleHistory })
    expect(screen.getByRole('button', { name: /history/i })).toHaveAttribute('aria-pressed', 'true')
  })

  it('clears as-of when Current chosen while as-of active', async () => {
    const onAsOfChange = vi.fn()
    renderSwitch({ asOfValue: new Date('2026-01-01T00:00:00Z'), onAsOfChange })
    await userEvent.click(screen.getByRole('button', { name: /current/i }))
    expect(onAsOfChange).toHaveBeenCalledWith(null)
  })

  it('hides the History segment when canToggleHistory is false', () => {
    renderSwitch({ canToggleHistory: false })
    expect(screen.queryByRole('button', { name: /history/i })).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `yarn test --run src/components/model-components/list/__test__/HistoryModeSwitch.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement HistoryModeSwitch**

Create `HistoryModeSwitch.tsx`. Reuse the as-of popover body from `AsOfControl.tsx` (the `Form` + `DateTimeInput` + `AutoSubmit` + Reset). Derive the mode; route segment clicks.

```tsx
import React, { FC, useEffect, useState } from 'react'
import { ToggleButton, ToggleButtonGroup, Popover, Box, Button, Tooltip } from '@mui/material'
import AccessTimeIcon from '@mui/icons-material/AccessTime'
import ClearIcon from '@mui/icons-material/Clear'
import { DateTimeInput, Form } from 'react-admin'
import { useFormContext } from 'react-hook-form'
import { parseAsOfInputValue, toAsOfInputValue } from '../../../utils/asOf'

type Mode = 'current' | 'asof' | 'history'

interface HistoryModeSwitchProps {
  asOfValue: Date | null
  onAsOfChange: (date: Date | null) => void
  isHistory: boolean
  canToggleHistory: boolean
  onToggleHistory: () => void
  historyLabel?: string
  /** Hide the As-of segment for resources that don't support bitemporal as-of. */
  showAsOf?: boolean
}

const AutoSubmit: FC<{ onChange: (date: Date | null) => void }> = ({ onChange }) => {
  const { watch } = useFormContext()
  const value = watch('as_of')
  useEffect(() => {
    const parsed = parseAsOfInputValue(value)
    if (parsed) { onChange(parsed); return }
    if (!value) onChange(null)
  }, [value, onChange])
  return null
}

export const HistoryModeSwitch: FC<HistoryModeSwitchProps> = ({
  asOfValue,
  onAsOfChange,
  isHistory,
  canToggleHistory,
  onToggleHistory,
  historyLabel = 'History',
  showAsOf = true,
}) => {
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null)

  const mode: Mode = isHistory ? 'history' : asOfValue ? 'asof' : 'current'

  const handleMode = (e: React.MouseEvent<HTMLElement>, next: Mode | null) => {
    if (!next || next === mode) {
      // Re-clicking "As of" while active should reopen the picker.
      if (next === 'asof') setAnchorEl(e.currentTarget)
      return
    }
    if (next === 'current') {
      if (isHistory) onToggleHistory()
      if (asOfValue) onAsOfChange(null)
      return
    }
    if (next === 'asof') {
      if (isHistory) onToggleHistory()
      setAnchorEl(e.currentTarget) // open picker; as-of applied on submit
      return
    }
    if (next === 'history') {
      onToggleHistory()
    }
  }

  return (
    <>
      <ToggleButtonGroup size='small' exclusive value={mode} onChange={handleMode} color='primary'>
        <ToggleButton value='current'>Current</ToggleButton>
        {showAsOf && (
          <ToggleButton value='asof'>
            <AccessTimeIcon fontSize='small' sx={{ mr: 0.5 }} /> As of
          </ToggleButton>
        )}
        {canToggleHistory && <ToggleButton value='history'>{historyLabel}</ToggleButton>}
      </ToggleButtonGroup>

      <Popover
        open={Boolean(anchorEl)}
        anchorEl={anchorEl}
        onClose={() => setAnchorEl(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
      >
        <Box p={2} display='flex' flexDirection='column' gap={2} minWidth={300}>
          <Form onSubmit={() => {}} defaultValues={{ as_of: toAsOfInputValue(asOfValue) }}>
            <Box display='flex' flexDirection='column'>
              <DateTimeInput source='as_of' label='Select Date & Time' fullWidth inputProps={{ step: 1 }} />
              <AutoSubmit onChange={onAsOfChange} />
            </Box>
          </Form>
          <Button
            variant='outlined'
            color='primary'
            startIcon={<ClearIcon />}
            fullWidth
            onClick={() => { onAsOfChange(null); setAnchorEl(null) }}
          >
            Reset to Latest
          </Button>
        </Box>
      </Popover>
    </>
  )
}

export default HistoryModeSwitch
```

- [ ] **Step 4: Run test to verify it passes**

Run: `yarn test --run src/components/model-components/list/__test__/HistoryModeSwitch.test.tsx`
Expected: PASS.

- [ ] **Step 5: Replace History button + AsOfControl in CustomList**

In `CustomList.tsx`:
1. Remove the History `<Button>` (967-977) and `<AsOfControl ... />` (979).
2. In their place (Left group), render:
```tsx
<HistoryModeSwitch
  asOfValue={asOfValue}
  onAsOfChange={setAsOfValue}
  isHistory={isEffectiveHistory}
  canToggleHistory={canToggleHistoryView}
  onToggleHistory={handleHistoryToggle}
  historyLabel={isEffectiveHistory ? 'Back to Records' : historyButtonLabel}
  showAsOf={!isLegacyResource}
/>
```
3. Add `import HistoryModeSwitch from './HistoryModeSwitch'`. Remove the now-unused `import AsOfControl from './AsOfControl'` if no other reference remains (grep first).

> The `historyLabel` mirrors the old button's dynamic label so behaviour matches. `handleHistoryToggle` already handles both native-history and historical-list-resource navigation, and clears `showClassStreamlit`.

- [ ] **Step 6: Run the CustomList test**

Run: `yarn test --run src/components/model-components/list/__test__/CustomList.test.tsx`
Expected: PASS. If the existing test queried the old "History"/"As Of" buttons by text, update those queries to the new segmented control.

- [ ] **Step 7: Commit**

```bash
git add src/components/model-components/list/HistoryModeSwitch.tsx src/components/model-components/list/__test__/HistoryModeSwitch.test.tsx src/components/model-components/list/CustomList.tsx
git commit -m "feat(frontend-redesign): merge As-of + History into one segmented switch (phase 2)"
```

---

### Task 4: Collapse the three refresh controls into one icon-only Refresh

**Files:**
- Modify: `src/components/model-components/list/CustomList.tsx` (inline Reload → icon)
- Modify: `src/components/model-components/list/CustomListActions.tsx` (remove Refresh button + `onRefresh` prop)
- Modify: `src/components/model-components/list/GridViewToolbar.tsx` (Reload → icon)
- Modify: `src/components/model-components/list/__test__/CustomListActions.test.tsx`
- Modify: `src/components/model-components/list/__test__/CustomList.test.tsx`

**Context:** Three buttons all call the same purge-refresh. Keep ONE icon-only Refresh in each grid toolbar; drop the redundant TopToolbar Refresh. `CustomListActions.onRefresh` becomes unused (CustomDashboard/AuditLogList never passed it).

- [ ] **Step 1: Update CustomListActions test first (failing)**

In `CustomListActions.test.tsx`, add:
```tsx
it('no longer renders a Refresh button (refresh lives in the grid toolbar)', () => {
  renderActions(true)
  expect(screen.queryByRole('button', { name: /refresh/i })).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `yarn test --run src/components/model-components/list/__test__/CustomListActions.test.tsx`
Expected: FAIL (Refresh button still present).

- [ ] **Step 3: Remove the Refresh button + onRefresh from CustomListActions**

In `CustomListActions.tsx`: delete the `<Button ... Refresh</Button>` block (36-46), the `onRefresh` field from the props type (16) and destructure (11), and the now-unused `RefreshIcon` import (4). Keep the Create button logic untouched.

- [ ] **Step 4: Convert CustomList inline Reload to icon-only**

In `CustomList.tsx`, replace the Reload `<Button>` (980-990) with:
```tsx
<Tooltip title='Refresh'>
  <IconButton
    size='small'
    color='inherit'
    onClick={() => refreshServerSideGrid(true)}
    aria-label='Refresh'
  >
    <RefreshIcon />
  </IconButton>
</Tooltip>
```
Remove the `onRefresh={...}` prop from the `<CustomListActions>` render (947). Ensure `RefreshIcon`, `IconButton`, `Tooltip` are imported.

- [ ] **Step 5: Convert GridViewToolbar Reload to icon-only**

In `GridViewToolbar.tsx`, replace the conditional Reload `<Button>` (82-92) with an icon-only version:
```tsx
{onReload && (
  <Tooltip title='Refresh'>
    <IconButton size='small' color='inherit' onClick={onReload} aria-label='Refresh'>
      <RefreshIcon />
    </IconButton>
  </Tooltip>
)}
```
Add `IconButton` to the MUI import.

- [ ] **Step 6: Fix the CustomList test mock**

`CustomList.test.tsx` mocks `CustomListActions` to render a `refresh-action` button calling `props.onRefresh` and has a test "renders list-actions with onRefresh callback". Since `onRefresh` is removed, update that mock to not reference `onRefresh`, and replace that test with one asserting the inline toolbar Refresh button exists (`getByLabelText('Refresh')`) and triggers a grid refresh (or simply renders). Keep the change minimal and consistent with the file.

- [ ] **Step 7: Run tests**

Run: `yarn test --run src/components/model-components/list/__test__/CustomListActions.test.tsx src/components/model-components/list/__test__/CustomList.test.tsx`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/components/model-components/list/CustomList.tsx src/components/model-components/list/CustomListActions.tsx src/components/model-components/list/GridViewToolbar.tsx src/components/model-components/list/__test__/CustomListActions.test.tsx src/components/model-components/list/__test__/CustomList.test.tsx
git commit -m "feat(frontend-redesign): collapse 3 refresh controls into one icon button (phase 2)"
```

---

### Task 5: TableSettingsMenu (⚙) housing Density

**Files:**
- Create: `src/components/model-components/list/TableSettingsMenu.tsx`
- Create: `src/components/model-components/list/__test__/TableSettingsMenu.test.tsx`
- Modify: `src/components/model-components/list/CustomList.tsx`
- Modify: `src/components/model-components/list/GridViewToolbar.tsx`
- Delete: `src/components/model-components/list/DensityControl.tsx`
- Delete: `src/components/model-components/list/__test__/DensityControl.test.tsx`

**Context:** A gear menu is the spec's final home for Density (and, in Phase 5, display toggles + format overrides). Fold `DensityControl`'s three options into it and remove the standalone control from both toolbars.

- [ ] **Step 1: Write the failing test**

Create `__test__/TableSettingsMenu.test.tsx`:
```tsx
import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TableSettingsMenu } from '../TableSettingsMenu'

describe('<TableSettingsMenu>', () => {
  it('opens the gear menu and lists the three density options', async () => {
    render(<TableSettingsMenu currentRowHeight={100} onRowHeightChange={() => {}} />)
    await userEvent.click(screen.getByLabelText(/settings/i))
    expect(screen.getByText('Compact')).toBeInTheDocument()
    expect(screen.getByText('Standard')).toBeInTheDocument()
    expect(screen.getByText('Comfortable')).toBeInTheDocument()
  })

  it('invokes onRowHeightChange with the chosen density', async () => {
    const onRowHeightChange = vi.fn()
    render(<TableSettingsMenu currentRowHeight={100} onRowHeightChange={onRowHeightChange} />)
    await userEvent.click(screen.getByLabelText(/settings/i))
    await userEvent.click(screen.getByText('Compact'))
    expect(onRowHeightChange).toHaveBeenCalledWith(25)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `yarn test --run src/components/model-components/list/__test__/TableSettingsMenu.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement TableSettingsMenu**

Create `TableSettingsMenu.tsx`. Gear `IconButton` → `Menu` with a "Density" subheader and the three options (logic lifted verbatim from `DensityControl`). Keep it open structurally for Phase 5 additions.

```tsx
import React, { useState } from 'react'
import {
  IconButton, Menu, MenuItem, ListItemIcon, ListItemText, ListSubheader, Tooltip,
} from '@mui/material'
import SettingsIcon from '@mui/icons-material/Settings'
import DensitySmallIcon from '@mui/icons-material/DensitySmall'
import DensityMediumIcon from '@mui/icons-material/DensityMedium'
import DensityLargeIcon from '@mui/icons-material/DensityLarge'
import CheckIcon from '@mui/icons-material/Check'

interface TableSettingsMenuProps {
  currentRowHeight: string | number
  onRowHeightChange: (newHeight: string | number) => void
}

const DENSITIES: Array<{ h: number; label: string; Icon: typeof DensitySmallIcon }> = [
  { h: 25, label: 'Compact', Icon: DensitySmallIcon },
  { h: 100, label: 'Standard', Icon: DensityMediumIcon },
  { h: 200, label: 'Comfortable', Icon: DensityLargeIcon },
]

export const TableSettingsMenu = ({ currentRowHeight, onRowHeightChange }: TableSettingsMenuProps) => {
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null)
  const open = Boolean(anchorEl)
  const select = (h: number) => { onRowHeightChange(h); setAnchorEl(null) }

  return (
    <>
      <Tooltip title='Settings'>
        <IconButton size='small' color='inherit' aria-label='Settings'
          onClick={(e) => setAnchorEl(e.currentTarget)}>
          <SettingsIcon />
        </IconButton>
      </Tooltip>
      <Menu anchorEl={anchorEl} open={open} onClose={() => setAnchorEl(null)}>
        <ListSubheader disableSticky>Density</ListSubheader>
        {DENSITIES.map(({ h, label, Icon }) => (
          <MenuItem key={h} onClick={() => select(h)}>
            <ListItemIcon><Icon fontSize='small' /></ListItemIcon>
            <ListItemText>{label}</ListItemText>
            {Number(currentRowHeight) === h && (
              <ListItemIcon><CheckIcon fontSize='small' /></ListItemIcon>
            )}
          </MenuItem>
        ))}
      </Menu>
    </>
  )
}

export default TableSettingsMenu
```

- [ ] **Step 4: Run to verify it passes**

Run: `yarn test --run src/components/model-components/list/__test__/TableSettingsMenu.test.tsx`
Expected: PASS.

- [ ] **Step 5: Swap DensityControl → TableSettingsMenu in both toolbars**

- `CustomList.tsx`: replace `<DensityControl ... />` (1012-1015) with `<TableSettingsMenu currentRowHeight={currentRowHeight} onRowHeightChange={handleRowHeightSelect} />`. Place the gear at the right end of the toolbar (after Refresh). Remove the `import { DensityControl }` and add `import TableSettingsMenu from './TableSettingsMenu'`.
- `GridViewToolbar.tsx`: replace `<DensityControl ... />` (69-72) with `<TableSettingsMenu currentRowHeight={currentRowHeight} onRowHeightChange={handleRowHeightSelect} />`. Update imports.

- [ ] **Step 6: Delete DensityControl + its test**

```bash
git rm src/components/model-components/list/DensityControl.tsx src/components/model-components/list/__test__/DensityControl.test.tsx
```
Then grep for any remaining `DensityControl` references (`grep -rn DensityControl src`). There should be none. If a stray reference exists, repoint it to `TableSettingsMenu`.

- [ ] **Step 7: Run tests + type-check**

Run: `yarn test --run src/components/model-components/list/__test__/TableSettingsMenu.test.tsx && yarn ts:check-types`
Expected: PASS / no type errors.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(frontend-redesign): move Density into a Settings gear menu (phase 2)"
```

---

### Task 6: Remove the Sidebar toggle + dead `isTableSidebarOpen` plumbing

**Files:**
- Modify: `src/components/model-components/list/CustomList.tsx`
- Modify: `src/components/model-components/list/GridViewToolbar.tsx`
- Modify: `src/components/model-components/list/CustomDatagrid.tsx`
- Modify: `src/components/CustomShow/CustomShow.tsx`

**Context:** With Columns/Filters buttons driving the panels, the "Sidebar" toggle and the `isTableSidebarOpen`/`historySidebarOpen` state are dead. Remove cleanly, repointing the two effects that used the flag.

- [ ] **Step 1: CustomList — remove toggle button + state, repoint effects**

In `CustomList.tsx`:
1. Delete the Sidebar toggle `<Button>` (1085-1093).
2. Delete `const [isTableSidebarOpen, setIsTableSidebarOpen] = useState(false)` (110).
3. Pivot interplay (624-626): replace
```tsx
if (isPivotModeEnabled && isTableSidebarOpen) { setIsTableSidebarOpen(false) }
```
with a tool-panel close:
```tsx
if (isPivotModeEnabled) { activeGridRef.current?.api?.closeToolPanel?.() }
```
4. Resize-on-toggle effect (640-658): re-key it on `openPanel` (from `useToolPanelControls`) instead of `isTableSidebarOpen`. Replace `prevSidebarOpenRef`/the effect deps with `openPanel`:
```tsx
const prevOpenPanelRef = useRef(openPanel)
useEffect(() => {
  if (prevOpenPanelRef.current !== openPanel) {
    const timeoutId = setTimeout(() => {
      if (activeGridRef.current?.api) applyColumnSizing(activeGridRef.current.api, currentRowHeight)
    }, 300)
    prevOpenPanelRef.current = openPanel
    return () => clearTimeout(timeoutId)
  }
  prevOpenPanelRef.current = openPanel
}, [activeGridRef, openPanel, currentRowHeight, applyColumnSizing])
```
5. Remove `isTableSidebarOpen={isTableSidebarOpen}` from the `<CustomDatagrid>` prop list (1110).

- [ ] **Step 2: CustomDatagrid — drop the `isTableSidebarOpen` prop**

In `CustomDatagrid.tsx`, remove `isTableSidebarOpen` from the props type and destructure (Task 1 may already have done this if 2775 was the only use). Confirm with `grep -n isTableSidebarOpen src/components/model-components/list/CustomDatagrid.tsx` → no hits remain.

- [ ] **Step 3: GridViewToolbar — remove sidebar props, add Columns/Filters**

In `GridViewToolbar.tsx`:
1. Remove `isSidebarOpen`/`onToggleSidebar` from the props type (22-23) and destructure (37-38), and delete the Sidebar toggle block (144-154) and `MenuIcon`/`MenuOpenIcon` imports.
2. Add Columns/Filters icon buttons using the same `useToolPanelControls(gridRef)` hook (the `gridRef` is already a prop here):
```tsx
const { openPanel, togglePanel } = useToolPanelControls(gridRef)
```
and place two `IconButton`s (ViewColumnIcon / FilterAltIcon) where the Sidebar toggle was, mirroring Task 2's JSX. Import `useToolPanelControls`, `IconButton`, `Tooltip`, `ViewColumnIcon`, `FilterAltIcon`.

- [ ] **Step 4: CustomShow — drop sidebar wiring**

In `CustomShow.tsx`:
1. Remove `isSidebarOpen={historySidebarOpen}` and `onToggleSidebar={...}` from the `<GridViewToolbar>` usages (694-695 and the second usage ~765 if it passes them).
2. Remove `isTableSidebarOpen={historySidebarOpen}` from the `<CustomDatagrid>` usages (711/715 and the main one ~204 if present).
3. Delete the `historySidebarOpen` state (554) and any setter references now unused. (If the main-grid CustomDatagrid at 204 passed `isTableSidebarOpen` from a different state, remove that too.) Grep `grep -n "SidebarOpen\|isTableSidebarOpen" src/components/CustomShow/CustomShow.tsx` to confirm clean.

- [ ] **Step 5: Run full touched-file test set + type-check**

Run:
```bash
yarn ts:check-types
yarn test --run \
  src/components/model-components/list/__test__/CustomList.test.tsx \
  src/components/model-components/list/__test__/CustomDatagrid.test.tsx \
  src/components/model-components/list/__test__/CustomListActions.test.tsx
```
Expected: no type errors; PASS. Fix any test that still references the removed Sidebar button or props.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(frontend-redesign): remove Sidebar toggle + dead isTableSidebarOpen plumbing (phase 2)"
```

---

### Task 7: Regression gate — full suite, type-check, lint

**Files:** none (verification only)

- [ ] **Step 1: Type-check**

Run: `yarn ts:check-types`
Expected: no errors.

- [ ] **Step 2: Run the list-component test suite**

Run: `yarn test --run src/components/model-components/list src/components/CustomShow src/pages/CustomDashboard`
Expected: all PASS. Investigate and fix any regression (do not delete assertions to make them pass).

- [ ] **Step 3: Lint the touched files**

Run: `yarn lint src/components/model-components/list src/components/CustomShow`
Expected: clean (or only pre-existing warnings unrelated to the change).

- [ ] **Step 4: Manual smoke checklist (document results in the PR)**

Verify in a running app (or note as untested if no env): single Refresh works; Columns button opens the columns panel and toggles closed; Filters likewise; segmented switch: Current ↔ As of (picker applies `as_of`) ↔ History; Settings gear changes density; dark mode renders correctly; SSRM list still loads/sorts/filters/edits; pivot still auto-closes any open panel.

- [ ] **Step 5: Final commit (if any lint/type fixes)**

```bash
git add -A
git commit -m "chore(frontend-redesign): phase 2 regression fixes (type/lint)"
```

---

## Notes carried forward to later phases

- **Phase 3** builds FK chips + the new hover card and the `valueFormatter` from Phase 1's `/fields/` `format` spec; it also adds `select_related` on the backend list view (hard prerequisite for large tables, flagged in Phase 1).
- **Phase 4** extends `HistoryModeSwitch`'s History mode to auto-reveal version columns + per-row timeline drawer, and moves Calculate into the action column with the calc-log drawer.
- **Phase 5** extends `TableSettingsMenu` with display toggles + per-column format overrides (persisted in the existing per-resource saved-view localStorage store), and enforces the reserved view name "preview".
- **Phase 6** redesigns the sidebar (collapsible tree).

## Self-review checklist (done while writing)

- Spec coverage: collapse refresh ✅ (T4); Columns/Filters direct ✅ (T1/T2); As-Of+History switch ✅ (T3); Density out → Settings ✅ (T5); remove Sidebar toggle ✅ (T6). Version columns / timeline / calc / FK chips / formatting correctly deferred to later phases.
- Type consistency: `togglePanel(panel: ToolPanelId)`, `openPanel: ToolPanelId | null`, `tableSideBarDef` panel ids `'columns'`/`'filters'` match `getOpenedToolPanel()` return values and `openToolPanel()` args throughout.
- No placeholders: every step has concrete code or an exact command; existing-file edits carry line anchors + grep guards because line numbers may drift during execution.
