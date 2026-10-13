# Fleet metrics phase 1 — interface area events (process-admin-general-client) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a person opens a product area in the web interface, or performs its defining action, the interface tells lex-app once per browser tab, area, embed state and UTC day. It sends `POST api/usage/area` with the body `{area, embed}` and nothing else. lex-app records the person from the session on the `ui` or `ui_embed` channel, which is what K2 feature reach is read from.

**Architecture:** One small module, `src/usage/`:

- `UI_AREAS`, mirrored from lex-app;
- `recordUsage(area)`, a synchronous, never-throwing call. It deduplicates in memory and in `sessionStorage`, keyed on the UTC date, area and embed state. Otherwise it fires a `keepalive` POST through the existing `raHttpClient` wrapper, which brings the session cookie and `X-CSRFToken`;
- a pure route-to-area mapping, and a hook that applies it on every navigation.

Areas reach the module in three ways:

- **Route-driven areas** come from one listener in `CustomLayout`. It learns each resource's kind from the model hierarchy already in the RTK Query cache.
- **The home route** is reported by the page that renders it, because only that page knows what `/` shows.
- **Every other area** is one explicit call at the place spec §5.3.2 names.

**Tech Stack:** React 18, react-admin 5, react-router-dom 7, RTK Query, TypeScript 5.7, Vitest 3 with jsdom, MSW 2, Testing Library.

**Spec:** lex-app `docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md`, revision 2: §5.3.2 (the K2 measurement contract), §6 rule 2 (the UTC date), §7 (the interface's tests), §8.1 R3, §8.2.

**Release:** everything here is **R3**. It ships as a lex-app-frontend release (Task 5), which a lex-app release then pins together with the endpoint it calls (lex-app plan `2026-09-29-fleet-metrics-phase1-recorder.md`, Tasks 5 and 9).

**Estimate:** 5 hours, the interface-events line of spec §8.2. Each task states its size.

## Global Constraints

- Repository `process-admin-general-client`; base `origin/lex-app-v2-pac-latest` (`e24fed4b` when this plan was written); branch `feat/usage-area-events`. Work in a worktree under `.claude/worktrees/`, and add `.claude/` to `.git/info/exclude` first.
- **Local checks borrow the main checkout's install.** `yarn install` needs `NPM_MARMELAB_TOKEN`, so link `node_modules` instead:
  `ln -sfn <main checkout>/node_modules <worktree>/node_modules`
  - Test command: `./node_modules/.bin/vitest run <paths>`. Type check: `./node_modules/.bin/tsc --noEmit -p tsconfig.json`.
  - A borrowed install carries a fixed number of failures that have nothing to do with this change. Always compare a run with this branch against a run on the base branch: only the delta means anything.
  - `eslint` does not run on a borrowed install, and the `Run linters` CI job is red on the base branch itself (a `vite` type clash in `vite.config.mts`). `Run unit tests` is the signal.
- **Privacy boundary (spec §5.3.2).** The request body is exactly `{"area": <UsageArea>, "embed": <boolean>}`. `recordUsage` takes one argument, of type `UsageArea`. Never pass it, or derive from it, a URL, a model or resource name, a record id, search text, a file name or a user id. The request carries no query string. Task 1's F2.46 asserts all of this.
- **No third-party analytics library**, and no new dependency of any kind.
- **Never at the person's expense.** `recordUsage` returns `void` synchronously and swallows every error: storage, network, HTTP status, an older backend. It never retries, never notifies, never logs to the console, and is never awaited.
- **The areas** are lex-app's `UI_AREAS` (`lex/core/services/usage_areas.py`): `grid, record, create, history, audit_log, calculate, calculation_logs, processes, reports, analytics, dashboard, search, export, files, monitoring`. lex-app answers 400 to any other name. The two lists change in the same release pair.
- **Where tests live** (`docs/test-plan/clusters/README.md`, "Where Tests Live"): Vitest files sit beside the code in `__test__/` folders, with scenario IDs in the test titles. This work is cluster **F02** (shell and navigation), letter **d**, scenarios **F2.46–F2.58**. At `e24fed4b`, F02's `max_scenario` is 45 and its letters are `a`–`c`. Re-read `docs/test-plan/clusters/F02-shell_nav/allocation.yaml` before starting, and renumber if the letter or range is taken.
- **Tests and the network.** In Vitest, jsdom runs at `localhost:3000`, so `getApiHost()` returns `REACT_APP_API_HOST` from `.env`, `https://fake-url.com/` (`src/api/httpClients/essentials.ts:7-19`). MSW listens with `onUnhandledRequest: 'bypass'` (`vitest.setup.ts`). Task 1 therefore adds a default MSW handler for `api/usage/area`, so no component test ever reaches the network.
- Do not copy hostnames or names from `essentials.ts` or elsewhere into committed files. This plan lives in public lex-app. No customer or project names either.
- Commit subjects are conventional and lower-case (`feat(usage): …`). Every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

| Path | Responsibility |
|---|---|
| `src/usage/areas.ts` (create) | `UI_AREAS`, `UsageArea`, `isUsageArea` |
| `src/usage/recordUsage.ts` (create) | `recordUsage`, `isEmbedded`, `utcDay`, the per-tab dedupe, the fire-and-forget POST |
| `src/usage/routeArea.ts` (create) | `areaForRoute` (Task 2), `areaForShowTab` (Task 3); pure |
| `src/usage/useUsageRouteListener.ts` (create) | The route listener hook |
| `src/usage/index.ts` (create) | Re-exports for call sites |
| `src/usage/__test__/recordUsage.test.ts`, `routeArea.test.ts`, `useUsageRouteListener.test.tsx`, `callSites.test.ts` (create) | F2.46–F2.54, F2.57–F2.58 |
| `src/mockApiStore/handlers.ts` (modify) | Default 204 for `api/usage/area` |
| `src/components/CustomLayout/CustomLayout.tsx` (modify, `:122-124`) | Mount the listener |
| `src/pages/StreamlitHome.tsx`, `src/pages/CustomDashboard/CustomDashboard.tsx` (modify) | The home route's areas |
| `src/components/CustomShow/CustomShow.tsx`, `ExportToPdfButton.tsx` (modify) | Record tabs; PDF export |
| `src/components/model-components/list/CustomList.tsx`, `CustomDatagrid.tsx`, `useAgGridExport.ts`, `GCSField/GCSField.tsx` (modify) | Create drawer, as-of mode, Analytics toggle, edit drawer, grid export, GCS file |
| `src/components/model-components/CalculateFunctionality/CalculateFunctionality.tsx`, `CalculationLogPopupHost.tsx` (modify) | Calculate; calculation-log popup |
| `src/components/model-components/FileDownload/FileDownload.tsx`, `SharepointField/SharepointField.tsx` (modify) | Files |
| `src/components/CustomLayout/ContentHeaderBar.tsx`, `src/components/CustomSearch/GlobalSearchDialog.tsx` (modify) | Monitoring drawer; global search |
| `src/pages/__test__/StreamlitHome.test.tsx`, `src/components/CustomShow/__test__/ExportToPdfButton.test.tsx` (modify); `src/components/CustomLayout/__test__/ContentHeaderBar.usage.test.tsx` (create) | F2.53, F2.55, F2.56 |
| `docs/test-plan/clusters/F02-shell_nav/{allocation.yaml,batches.md,cluster.md}` (modify), `docs/test-plan/progress/sessions/<date>-usage-area-events.md` (create) | Plan shards |

---

### Task 1: The usage module: areas, `recordUsage`, the per-tab dedupe and the embed flag

Covers the brief's (a) and (d). **Size:** 1.5 h

**Files:**
- Create: `src/usage/areas.ts`, `src/usage/recordUsage.ts`, `src/usage/index.ts`
- Modify: `src/mockApiStore/handlers.ts`
- Test: `src/usage/__test__/recordUsage.test.ts`

**Interfaces:**
- Consumes:
  - the default export of `src/api/httpClients/raHttpClient.ts`, a plain function despite its callers' `use…` import name (`:11-29`). It adds `X-CSRFToken` from the `csrftoken` cookie (`:17-21`) and `credentials: 'include'` (`:24`), resolves the route against `getApiHost()` (`:15`, `:26`), and passes every other option through `fetchJson` to `fetch` (`src/api/customFetch.ts:57-68`), `keepalive` included;
  - `detectEmbed` from `src/utils/useEmbedContext.ts:145-151`, which reads the `embed=true` snapshot that survives client-side navigation, or `#embed`.
- Produces:
  - `UI_AREAS`, `type UsageArea`, `isUsageArea(value)`;
  - `recordUsage(area: UsageArea): void`, `isEmbedded(): boolean`, `utcDay(now?: Date): string`, `USAGE_ENDPOINT`;
  - `__resetUsageForTests(options?: { keepSessionStorage?: boolean })`.

**Why the dedupe key includes `embed`.** Spec §5.3.2 says "this browser tab already sent that area today". But a same-origin frame shares its tab's `sessionStorage`: an admin view embedded in a Streamlit page inside the Analytics tab, for example. If the key were only the date and the area, that embedded `grid` would be suppressed by the top-level tab's `grid`, and `ui_embed` would undercount. With `embed` in the key, each channel is deduplicated on its own. This saves the same requests the spec intends and loses no channel.

- [ ] **Step 1: Write the failing tests**

`src/usage/__test__/recordUsage.test.ts`:

```ts
/**
 * F2.46–F2.50 — the interface reports product areas: once per tab, area and UTC
 * day, as exactly {area, embed}, and never at the person's expense.
 *
 * Fleet metrics K2: lex-app spec 2026-09-29-fleet-metrics-phase1-design.md §5.3.2.
 * Real here: recordUsage, its dedupe, raHttpClient and fetchJson. Faked: fetch (a
 * spy, so the request a browser would send can be read field by field), the clock
 * (Date only) and detectEmbed.
 *
 * @group unit
 */
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'

vi.mock('../../utils/useEmbedContext', () => ({ detectEmbed: vi.fn(() => false) }))

import { detectEmbed } from '../../utils/useEmbedContext'
import { UI_AREAS, type UsageArea } from '../areas'
import { __resetUsageForTests, recordUsage, utcDay } from '../recordUsage'

/** lex-app's UI_AREAS (lex/core/services/usage_areas.py), name for name. */
const LEX_APP_UI_AREAS = [
  'grid', 'record', 'create', 'history', 'audit_log', 'calculate', 'calculation_logs',
  'processes', 'reports', 'analytics', 'dashboard', 'search', 'export', 'files', 'monitoring',
]

let fetchSpy: MockInstance<typeof fetch>

const requests = (): Array<{ url: string; init: RequestInit }> =>
  fetchSpy.mock.calls.map(([url, init]) => ({ url: String(url), init: init ?? {} }))
const bodies = (): unknown[] => requests().map(({ init }) => JSON.parse(String(init.body)))

beforeEach(() => {
  __resetUsageForTests()
  vi.mocked(detectEmbed).mockReturnValue(false)
  window.history.pushState({}, '', '/')
  document.cookie = 'csrftoken=test-csrf-token; path=/'
  fetchSpy = vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation(async () => new Response(null, { status: 204 }))
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('recordUsage', () => {
  it('F2.46: sends exactly {area, embed} to api/usage/area, with the session cookie, the CSRF header and keepalive', () => {
    window.history.pushState({}, '', '/fund/42/show?search=Alpha')
    recordUsage('record')

    expect(fetchSpy).toHaveBeenCalledTimes(1)
    const [{ url, init }] = requests()
    expect(new URL(url).pathname).toMatch(/\/api\/usage\/area$/)
    expect(new URL(url).search).toBe('')
    expect(init.method).toBe('POST')
    expect(init.keepalive).toBe(true)
    expect(init.credentials).toBe('include')
    expect(new Headers(init.headers).get('X-CSRFToken')).toBe('test-csrf-token')
    const body = JSON.parse(String(init.body)) as Record<string, unknown>
    expect(Object.keys(body).sort()).toEqual(['area', 'embed'])
    expect(body).toStrictEqual({ area: 'record', embed: false })
    // The privacy boundary: nothing of the page the person is on travels.
    expect(`${url} ${String(init.body)}`).not.toMatch(/fund|42|Alpha/)
  })

  it('F2.46 (supporting): UI_AREAS mirrors lex-app, name for name', () => {
    expect([...UI_AREAS].sort()).toEqual([...LEX_APP_UI_AREAS].sort())
  })

  it('F2.47: one request per tab, area and UTC day; sessionStorage carries it across a reload', () => {
    recordUsage('grid')
    recordUsage('grid')
    recordUsage('history')
    expect(bodies()).toEqual([
      { area: 'grid', embed: false },
      { area: 'history', embed: false },
    ])

    __resetUsageForTests({ keepSessionStorage: true }) // a reload: memory gone, tab storage kept
    recordUsage('grid')
    expect(fetchSpy).toHaveBeenCalledTimes(2)
  })

  it('F2.48: the day is the UTC date, so 00:30 in Berlin on 26 October is still the 25th', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-25T23:30:00Z'))
    expect(utcDay()).toBe('2026-10-25')
    recordUsage('grid')
    vi.setSystemTime(new Date('2026-10-25T23:59:59Z'))
    recordUsage('grid')
    vi.setSystemTime(new Date('2026-10-26T00:00:00Z'))
    recordUsage('grid')

    expect(fetchSpy).toHaveBeenCalledTimes(2)
  })

  it('F2.49: a network error, a server error, blocked storage or an unknown name never reach the caller', async () => {
    fetchSpy.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    expect(() => recordUsage('grid')).not.toThrow()

    fetchSpy.mockResolvedValueOnce(new Response('{"detail":"boom"}', { status: 500 }))
    expect(() => recordUsage('history')).not.toThrow()

    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError')
    })
    expect(() => recordUsage('export')).not.toThrow()

    expect(() => recordUsage('app' as UsageArea)).not.toThrow()

    // Let the failed sends settle. Vitest fails the run on an unhandled rejection,
    // so reaching the end is half of this test's assertion.
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(bodies()).toEqual([
      { area: 'grid', embed: false },
      { area: 'history', embed: false },
      { area: 'export', embed: false },
    ])
    recordUsage('grid') // dropped once, never retried
    expect(fetchSpy).toHaveBeenCalledTimes(3)
  })

  it('F2.50: an embedded frame says so, and is deduplicated apart from the top-level tab', () => {
    recordUsage('grid')
    vi.mocked(detectEmbed).mockReturnValue(true) // an admin view inside a Streamlit page, same tab
    recordUsage('grid')
    vi.mocked(detectEmbed).mockReturnValue(false)
    window.history.pushState({}, '', '/embed/widgets') // the widget host
    recordUsage('calculate')

    expect(bodies()).toEqual([
      { area: 'grid', embed: false },
      { area: 'grid', embed: true },
      { area: 'calculate', embed: true },
    ])
  })
})
```

- [ ] **Step 2: Run them and watch them fail**

Run: `./node_modules/.bin/vitest run src/usage/__test__/recordUsage.test.ts`
Expected: FAIL — `Failed to resolve import "../areas"`.

- [ ] **Step 3: Write the area list**

`src/usage/areas.ts`:

```ts
/**
 * The product areas the interface may report (fleet metrics, K2).
 *
 * Mirrors `UI_AREAS` in lex-app (`lex/core/services/usage_areas.py`). lex-app
 * answers 400 to any other name, so the two lists change in the same release
 * pair: a lex-app release that accepts the name, pinning the frontend release
 * that sends it.
 */
export const UI_AREAS = [
  'grid',
  'record',
  'create',
  'history',
  'audit_log',
  'calculate',
  'calculation_logs',
  'processes',
  'reports',
  'analytics',
  'dashboard',
  'search',
  'export',
  'files',
  'monitoring',
] as const

export type UsageArea = (typeof UI_AREAS)[number]

export const isUsageArea = (value: unknown): value is UsageArea =>
  typeof value === 'string' && (UI_AREAS as readonly string[]).includes(value)
```

- [ ] **Step 4: Write `recordUsage`**

`src/usage/recordUsage.ts`:

```ts
/**
 * Fleet metrics K2: tell lex-app which product area a person used.
 *
 * One fire-and-forget POST per browser tab, area, embed state and UTC day. The
 * body is `{ area, embed }` and nothing else: no URL, model, record id, search
 * text, file name or user id (lex-app spec 2026-09-29, §5.3.2). lex-app takes
 * the person from the session and the day from its own UTC clock. Storage there
 * is keyed by person, area, channel and day, so this tab's dedupe only saves
 * requests; it is not what makes the count correct.
 *
 * Nothing here can affect the interface. Every failure is swallowed: a blocked
 * sessionStorage, a network error, an error status, a backend without the
 * endpoint. A dropped event is never retried.
 */
import createHttpClient from '../api/httpClients/raHttpClient'
import { detectEmbed } from '../utils/useEmbedContext'
import { isUsageArea, type UsageArea } from './areas'

export const USAGE_ENDPOINT = 'api/usage/area'

const STORAGE_KEY = 'lex.usage.sent.v1'
/** The widget host (`App.tsx`) is an embed surface whatever its query string says. */
const WIDGET_HOST = /\/embed\/widgets\/?$/

let memoryDay: string | null = null
let sentInMemory = new Set<string>()

/** Today's date in UTC, `YYYY-MM-DD`. The browser's time zone never matters (spec §6). */
export const utcDay = (now: Date = new Date()): string => now.toISOString().slice(0, 10)

/**
 * Whether this frame runs embedded: the `embed=true` / `#embed` snapshot that
 * useEmbedContext keeps across client-side navigation, or the widget host.
 */
export function isEmbedded(): boolean {
  try {
    return detectEmbed('') || WIDGET_HOST.test(window.location.pathname)
  } catch {
    return false
  }
}

const keyOf = (area: UsageArea, embed: boolean): string => `${area}|${embed ? 'embed' : 'top'}`

function readStored(day: string): Set<string> {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY)
    if (raw === null) return new Set()
    const parsed = JSON.parse(raw) as { day?: unknown; sent?: unknown }
    if (parsed?.day !== day || !Array.isArray(parsed.sent)) return new Set()
    return new Set(parsed.sent.filter((key): key is string => typeof key === 'string'))
  } catch {
    return new Set()
  }
}

function writeStored(day: string, sent: Set<string>): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ day, sent: [...sent] }))
  } catch {
    // Blocked or full: the in-memory set still deduplicates until the next reload.
  }
}

function send(area: UsageArea, embed: boolean): void {
  const body: { area: UsageArea; embed: boolean } = { area, embed }
  createHttpClient()(USAGE_ENDPOINT, {
    method: 'POST',
    body: JSON.stringify(body),
    keepalive: true,
  }).catch(() => undefined)
}

/** Report that the person used `area`. Returns at once; never throws. */
export function recordUsage(area: UsageArea): void {
  try {
    if (!isUsageArea(area)) return
    const day = utcDay()
    if (day !== memoryDay) {
      memoryDay = day
      sentInMemory = new Set()
    }
    const embed = isEmbedded()
    const key = keyOf(area, embed)
    if (sentInMemory.has(key)) return
    sentInMemory.add(key)
    const stored = readStored(day)
    if (stored.has(key)) return
    stored.add(key)
    writeStored(day, stored)
    send(area, embed)
  } catch {
    // Usage reporting must never change what the person sees.
  }
}

/** Test-only. `keepSessionStorage: true` simulates a reload of the same tab. */
export function __resetUsageForTests(options: { keepSessionStorage?: boolean } = {}): void {
  memoryDay = null
  sentInMemory = new Set()
  if (options.keepSessionStorage === true) return
  try {
    window.sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    // ignore
  }
}
```

`src/usage/index.ts`:

```ts
export { UI_AREAS, isUsageArea, type UsageArea } from './areas'
export { recordUsage } from './recordUsage'
```

- [ ] **Step 5: Give component tests a default answer**

In `src/mockApiStore/handlers.ts`, add a group before `handlersList` and spread it into the list:

```ts
export const usageHandlers = [
  /** POST /api/usage/area — fleet-metrics area events; lex-app answers 204 */
  http.post(api('usage/area'), () => new HttpResponse(null, { status: 204 })),
]
```

```ts
export const handlersList = [
  ...modelStructureHandlers,
  ...modelEntriesHandlers,
  ...userInfoHandlers,
  ...permissionsHandlers,
  ...calculationLogHandlers,
  ...usageHandlers,
]
```

- [ ] **Step 6: Run the tests and the type check**

Run: `./node_modules/.bin/vitest run src/usage/__test__/recordUsage.test.ts`
Expected: `6 passed`.

Run: `./node_modules/.bin/tsc --noEmit -p tsconfig.json 2>&1 | grep -c "src/usage" || true`
Expected: `0`. The total error count of a borrowed install is meaningless; only errors in the new files count.

- [ ] **Step 7: Commit**

```bash
git add src/usage src/mockApiStore/handlers.ts
git commit -m "feat(usage): report product areas to lex-app, once per tab, area and utc day"
```

---

### Task 2: The route listener, and the home route

Covers the brief's (b). **Size:** 1 h

**Files:**
- Create: `src/usage/routeArea.ts`, `src/usage/useUsageRouteListener.ts`
- Modify: `src/usage/index.ts`, `src/components/CustomLayout/CustomLayout.tsx`, `src/pages/StreamlitHome.tsx`, `src/pages/CustomDashboard/CustomDashboard.tsx`
- Test: `src/usage/__test__/routeArea.test.ts`, `src/usage/__test__/useUsageRouteListener.test.tsx`, `src/pages/__test__/StreamlitHome.test.tsx` (add a case)

**Context, as read at `e24fed4b`:**

- **Where the listener goes.** `CustomLayout` (`src/components/CustomLayout/CustomLayout.tsx:122-197`) reads `useLocation()` at `:124`. It wraps every admin route, in both its embed branch (`:158-171`) and its normal branch (`:173-197`). It does not wrap `/embed/widgets`, which `App.tsx:30` serves through `EmbedStandalone`, outside the admin app. The widgets there report their own action (`calculate`, Task 3), and Task 1's `isEmbedded()` marks them as embedded.
- **Route shapes** are react-admin's: `/:resource` (list), `/:resource/create`, `/:resource/:id` (edit) and `/:resource/:id/show`. Custom routes are `/process-history/:model/step/:process_id`, `/calculation_log_tree` and `/delete-unused-files` (`CustomReactAdmin.tsx:182-197`). History is either `?history_view=true` on a list (`src/utils/historyViewLocation.ts:1`, `:19-21`) or a native `historical…` / `metahistorical…` resource (`:3-8`).
- **The resource kind.** The listener cannot tell a model list from a process or a report by path alone. `CustomReactAdmin.tsx:109-170` picks the component by each model's `type` from the model-structure endpoint: `Model` → `CustomList`, `Process` → `ProcessFlow`, anything else (`HTMLReport`) → `HTMLReportTemplate`. lex-app sets those types in `lex/process_admin/models/utils.py:53-58`.

  The listener reads the same `allModels` with `useGetModelHierarchyQuery(undefined, { selectFromResult })`, the same call `CustomShow.tsx:288-292` makes. `CustomReactAdmin`'s `RequiresApi` gate (`:87-89`) has already loaded that query before any layout renders, so this costs no request.
- **The home route.** `/` renders `StreamlitHome` (`CustomReactAdmin.tsx:76-81`, `:106`). It shows Streamlit only when the project has a Streamlit home and a `HEAD` probe reaches the server (`src/pages/StreamlitHome.tsx:28-70`, `:83-89`). Otherwise it shows the widget dashboard `CustomDashboard` when a widget structure exists, or the static `HomePage`. Only the page knows which it is. So `/` is reported by `StreamlitHome` (`analytics`) and by `CustomDashboard` (`dashboard`), and the listener leaves `/` alone.
  - `CustomDashboard` is also the list of the `custom_dashboard` resource (`CustomReactAdmin.tsx:172-180`). One mount effect covers both places spec §5.3.2 names for `dashboard`, and the listener leaves `/custom_dashboard` alone too.
  - Calling `useStreamlitHomeAvailable` in the layout instead would repeat the probe on every page load.

- [ ] **Step 1: Write the failing tests**

`src/usage/__test__/routeArea.test.ts`:

```ts
/**
 * F2.51 — which product area a route opens (fleet metrics K2, lex-app spec §5.3.2).
 *
 * @group unit
 */
import { describe, expect, it } from 'vitest'
import { areaForRoute } from '../routeArea'

const KINDS: Record<string, string> = {
  fund: 'Model',
  historicalfund: 'Model',
  nightly_import: 'Process',
  summary_report: 'HTMLReport',
}
const kindOf = (resource: string): string | undefined => KINDS[resource]

describe('areaForRoute', () => {
  it.each([
    ['/fund', '', 'grid'],
    ['/fund', '?history_view=true', 'history'],
    ['/fund/create', '', 'create'],
    ['/fund/7', '', 'record'],
    ['/fund/7/show', '', 'record'],
    ['/historicalfund', '', 'history'],
    ['/historicalfund/3/show', '', 'history'],
    ['/nightly_import', '', 'processes'],
    ['/process-history/nightly_import/step/12', '', 'processes'],
    ['/summary_report', '', 'reports'],
    ['/calculation_log_tree', '', 'calculation_logs'],
    ['/', '', null],
    ['/custom_dashboard', '', null],
    ['/embed/widgets', '?embed=true', null],
    ['/delete-unused-files', '', null],
    ['/not_a_model', '', null],
  ])('F2.51: %s%s opens %s', (pathname, search, area) => {
    expect(areaForRoute(pathname, search, kindOf)).toBe(area)
  })
})
```

`src/usage/__test__/useUsageRouteListener.test.tsx`:

```tsx
/**
 * F2.52 — the layout's route listener reports the area each navigation opens.
 *
 * Real here: the router, the hook and areaForRoute. Faked: the model hierarchy
 * (the RTK Query cache CustomReactAdmin has already filled) and recordUsage,
 * whose own dedupe is F2.47's subject.
 *
 * @group integration
 * @group component
 */
import React from 'react'
import { act, render } from '@testing-library/react'
import { MemoryRouter, useNavigate, type NavigateFunction } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { models } = vi.hoisted(() => ({
  models: [
    { original_name: 'fund', readable_name: 'Fund', type: 'Model' },
    { original_name: 'historicalfund', readable_name: 'Fund history', type: 'Model' },
    { original_name: 'nightly_import', readable_name: 'Nightly import', type: 'Process' },
    { original_name: 'summary_report', readable_name: 'Summary', type: 'HTMLReport' },
  ],
}))

vi.mock('../../store/model-hierarchy', () => ({
  useGetModelHierarchyQuery: (
    _arg: unknown,
    options: { selectFromResult: (result: unknown) => unknown },
  ) => options.selectFromResult({ data: { allModels: models } }),
}))
vi.mock('../recordUsage', () => ({ recordUsage: vi.fn() }))

import { recordUsage } from '../recordUsage'
import { useUsageRouteListener } from '../useUsageRouteListener'

let navigate: NavigateFunction

function Harness(): null {
  useUsageRouteListener()
  navigate = useNavigate()
  return null
}

const reported = (): unknown[] => vi.mocked(recordUsage).mock.calls.map(([area]) => area)

describe('useUsageRouteListener', () => {
  beforeEach(() => {
    vi.mocked(recordUsage).mockClear()
  })

  it('F2.52: each navigation reports the area its route opens', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <Harness />
      </MemoryRouter>,
    )
    expect(reported()).toEqual([]) // `/` is the page's to report (F2.53)

    for (const to of [
      '/fund',
      '/fund/7/show',
      '/fund/create',
      '/fund?history_view=true',
      '/nightly_import',
      '/summary_report',
      '/calculation_log_tree',
    ]) {
      act(() => {
        void navigate(to)
      })
    }

    expect(reported()).toEqual([
      'grid',
      'record',
      'create',
      'history',
      'processes',
      'reports',
      'calculation_logs',
    ])
  })

  it('F2.52 (supporting): a resource the hierarchy does not name reports nothing', () => {
    render(
      <MemoryRouter initialEntries={['/not_a_model']}>
        <Harness />
      </MemoryRouter>,
    )
    act(() => {
      void navigate('/not_a_model/3/show')
    })

    expect(reported()).toEqual([])
  })
})
```

In `src/pages/__test__/StreamlitHome.test.tsx`, add this beside the file's other `vi.mock` calls:

```tsx
vi.mock('../../usage', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../usage')>()),
  recordUsage: vi.fn(),
}))
```

Then add `import { recordUsage } from '../../usage'` and this case inside `describe('StreamlitHome', …)`:

```tsx
  it('F2.53: the home page reports analytics only when it shows the Streamlit home', async () => {
    vi.mocked(recordUsage).mockClear()
    mockProjectInfo = { data: { streamlit_enabled: true }, isFetching: false }
    global.fetch = vi.fn().mockResolvedValue({}) as any
    const shown = render(<StreamlitHome fallback={Fallback} />)
    await waitFor(() => {
      expect(screen.getByTestId('streamlit-home')).toBeInTheDocument()
    })
    expect(vi.mocked(recordUsage).mock.calls).toEqual([['analytics']])
    shown.unmount()

    vi.mocked(recordUsage).mockClear()
    mockProjectInfo = { data: { streamlit_enabled: false }, isFetching: false }
    render(<StreamlitHome fallback={Fallback} />)
    expect(screen.getByTestId('fallback-home')).toBeInTheDocument()
    expect(recordUsage).not.toHaveBeenCalled() // the fallback reports its own area, if it has one
  })
```

- [ ] **Step 2: Run them and watch them fail**

Run: `./node_modules/.bin/vitest run src/usage/__test__/routeArea.test.ts src/usage/__test__/useUsageRouteListener.test.tsx src/pages/__test__/StreamlitHome.test.tsx`
Expected: FAIL — `Failed to resolve import "../routeArea"` and `"../useUsageRouteListener"`. F2.53 fails because `recordUsage` was never called.

- [ ] **Step 3: Write the mapping**

`src/usage/routeArea.ts`:

```ts
/**
 * Which product area a route opens (fleet metrics K2, lex-app spec §5.3.2).
 *
 * Pure. The resource kind comes from the caller: the model-structure `type`
 * (`Model`, `Process`, `HTMLReport`), the same value CustomReactAdmin uses to
 * pick a resource's components.
 */
import { isHistoryViewRequested, isNativeHistoryResource } from '../utils/historyViewLocation'
import { type UsageArea } from './areas'

export type KindOf = (resource: string) => string | undefined

/** Routes that are not an area by themselves, or whose page reports its own. */
const NOT_A_ROUTE_AREA = new Set(['embed', 'custom_dashboard', 'delete-unused-files'])

const decode = (segment: string): string => {
  try {
    return decodeURIComponent(segment)
  } catch {
    return segment
  }
}

/**
 * The area `pathname` + `search` opens, or null.
 *
 * Null for:
 * - `/`, which StreamlitHome or CustomDashboard reports, because only they know
 *   what the home page shows;
 * - `/custom_dashboard`, which CustomDashboard reports;
 * - the widget host, an admin page and any resource the hierarchy does not name.
 */
export function areaForRoute(pathname: string, search: string, kindOf: KindOf): UsageArea | null {
  const [first, second] = pathname.split('/').filter(Boolean).map(decode)
  if (first === undefined || NOT_A_ROUTE_AREA.has(first)) return null
  if (first === 'calculation_log_tree') return 'calculation_logs'
  if (first === 'process-history') return 'processes'
  if (isNativeHistoryResource(first)) return 'history'

  const kind = kindOf(first)
  if (kind === 'Process') return 'processes'
  if (kind === 'HTMLReport') return 'reports'
  if (kind !== 'Model') return null

  if (second === undefined) return isHistoryViewRequested(search) ? 'history' : 'grid'
  if (second === 'create') return 'create'
  return 'record' // /:resource/:id (edit) and /:resource/:id/show
}
```

`src/usage/useUsageRouteListener.ts`:

```ts
/**
 * Report the product area each admin route opens. Mounted once, in CustomLayout.
 *
 * The resource kind comes from the model hierarchy that CustomReactAdmin's
 * RequiresApi gate has already loaded; reading the same RTK Query cache entry
 * costs no request. Repeated routes call recordUsage again; its per-tab dedupe
 * decides what is sent.
 */
import { useEffect, useMemo } from 'react'
import { useLocation } from 'react-router-dom'
import { useGetModelHierarchyQuery } from '../store/model-hierarchy'
import { recordUsage } from './recordUsage'
import { areaForRoute, type KindOf } from './routeArea'

export function useUsageRouteListener(): void {
  const location = useLocation()
  const { allModels } = useGetModelHierarchyQuery(undefined, {
    selectFromResult: ({ data }) => ({ allModels: data?.allModels }),
  })

  const kindOf = useMemo<KindOf>(() => {
    const kinds = new Map((allModels ?? []).map((model) => [model.original_name, model.type]))
    return (resource) => kinds.get(resource)
  }, [allModels])

  useEffect(() => {
    const area = areaForRoute(location.pathname, location.search, kindOf)
    if (area !== null) recordUsage(area)
  }, [location.pathname, location.search, kindOf])
}
```

Add to `src/usage/index.ts`:

```ts
export { areaForRoute } from './routeArea'
```

- [ ] **Step 4: Mount it, and report the home route from its pages**

`src/components/CustomLayout/CustomLayout.tsx`: import the hook and call it right after `useLocation()` (`:124`). It must sit before the embed branch's early return (`:158`), so both branches run it:

```tsx
import { useUsageRouteListener } from '../../usage/useUsageRouteListener'
```

```tsx
  const location = useLocation()
  // Fleet metrics K2: report the area each admin route opens (src/usage/routeArea.ts).
  useUsageRouteListener()
```

The existing `CustomLayout.test.tsx` mocks `useGetModelHierarchyQuery` without applying `selectFromResult` (`:83-88`). So `allModels` is `undefined` there, the hook falls back to `[]`, and `/` reports nothing. That test needs no change.

`src/pages/StreamlitHome.tsx`: import `recordUsage` and add the effect after `useFillViewportHeight` (`:81`), before the early returns (`:83-84`):

```tsx
import { recordUsage } from '../usage'
```

```tsx
  // The home route is the one place the route listener cannot read: only this
  // probe knows whether `/` shows Streamlit. The fallback reports its own area.
  useEffect(() => {
    if (available === true) recordUsage('analytics')
  }, [available])
```

`src/pages/CustomDashboard/CustomDashboard.tsx`: import `recordUsage` and add the effect after `useGetModelHierarchyQuery` (`:36`), before the early return (`:38-39`):

```tsx
import { recordUsage } from '../../usage'
```

```tsx
  // A widget dashboard opened: at `/` (the fallback home) or at /custom_dashboard.
  React.useEffect(() => {
    recordUsage('dashboard')
  }, [])
```

- [ ] **Step 5: Run the tests and watch them pass**

Run: `./node_modules/.bin/vitest run src/usage src/pages/__test__/StreamlitHome.test.tsx src/components/CustomLayout src/pages/CustomDashboard`
Expected: the new cases pass. `CustomLayout.test.tsx` and `CustomDashboard.test.tsx` show the same result as on the base branch.

- [ ] **Step 6: Commit**

```bash
git add src/usage src/components/CustomLayout/CustomLayout.tsx src/pages/StreamlitHome.tsx src/pages/CustomDashboard/CustomDashboard.tsx src/pages/__test__/StreamlitHome.test.tsx
git commit -m "feat(usage): every admin route reports the area it opens"
```

---

### Task 3: The explicit calls at every non-route place

Covers the brief's (c). **Size:** 1.5 h

**Files:**
- Modify: `src/usage/routeArea.ts`, `src/usage/index.ts`, and the twelve component files listed in the table below
- Test: `src/usage/__test__/routeArea.test.ts` (add F2.54), `src/components/CustomShow/__test__/ExportToPdfButton.test.tsx` (add F2.55), `src/components/CustomLayout/__test__/ContentHeaderBar.usage.test.tsx` (create, F2.56)

Each place gets `import { recordUsage } from '<relative path to src>/usage'`. The calls follow spec §5.3.2's table. Line numbers are at `e24fed4b`:

| Area | Place | Change |
|---|---|---|
| `record` | `CustomDatagrid.tsx:2598-2601`, `openEditDrawer` | after `setEditDrawerRecordId(…)`: `recordUsage('record')` |
| `create` | `CustomList.tsx:1368`, the create-drawer button | `onClick={() => { setCreateDrawerOpen(true); recordUsage('create') }}` |
| `history` | `CustomList.tsx:1175-1183`, `HistoryModeSwitch` as-of | `onAsOfChange={handleAsOfChange}` (`:1177`); see below |
| `history`, `audit_log`, `analytics` | `CustomShow.tsx:625`, `TrackingTabs` (`:235-274`) | `tabs={<TrackingTabs onActiveTabLabelChange={reportActiveTab} />}`; see below |
| `analytics` | `CustomList.tsx:1185-1196`, the Analytics toggle | `onClick` (`:1190`): `if (!showClassStreamlit) recordUsage('analytics')` before the toggle |
| `calculate` | `CalculateFunctionality.tsx:278-283` | after the `await dataProvider.update(…)` resolves: `recordUsage('calculate')` |
| `calculation_logs` | `CalculationLogPopupHost.tsx:33-40` | effect on `target`; see below |
| `export` | `useAgGridExport.ts:361-386` | after `notify('Export ready', …)` (`:386`): `recordUsage('export')` |
| `export` | `ExportToPdfButton.tsx:46` (the button at `CustomShow.tsx:162`) | after `pdf.save(…)`: `recordUsage('export')` |
| `files` | `FileDownload.tsx:37-44` (`FieldView.tsx:452`) | after `link.click()`: `recordUsage('files')` |
| `files` | `SharepointField.tsx:45-58` and `:87-95` (`FieldView.tsx:450`) | after the download's `link.click()` and the preview's `window.open(…)`: `recordUsage('files')` |
| `files` | `GCSField/GCSField.tsx:25-28` (`FieldView.tsx:448`) | after `window.open(…)`: `recordUsage('files')` |
| `monitoring` | `ContentHeaderBar.tsx:66-83` | `onClick` (`:71-73`): `if (!isDrawerOpen) recordUsage('monitoring')` before the toggle |
| `search` | `GlobalSearchDialog.tsx:188-240`, the fetch effect | after `setError(null)` (`:201`), once a query of at least `MIN_QUERY_LEN` runs: `recordUsage('search')` |

`calculate` from the widget host goes through the same `CalculateFunctionality`, because `CalculationWidget` renders it. Task 1's `isEmbedded()` marks it `embed: true`.

Every call sits on the success path or the opening edge. A failed export, a closed drawer or a cancelled dialog reports nothing. SharePoint's edit-in-place link (`:59-86`) is neither a download nor a preview, so spec §5.3.2 does not count it.

- [ ] **Step 1: Write the failing tests**

Add to `src/usage/__test__/routeArea.test.ts`:

```ts
import { areaForShowTab } from '../routeArea'

describe('areaForShowTab', () => {
  const defaults = { history: 'History', auditLog: 'Audit Log' }
  // TAB_DISPLAY_NAMES in a project's lex_config.py can rename both tabs.
  const renamed = { history: 'Changes', auditLog: 'Who did what' }

  it.each([
    ['Summary', defaults, null],
    ['Analytics', defaults, 'analytics'],
    ['Timeline', defaults, 'history'],
    ['History', defaults, 'history'],
    ['Audit Log', defaults, 'audit_log'],
    ['Live Data', defaults, null],
    ['Changes', renamed, 'history'],
    ['Who did what', renamed, 'audit_log'],
    ['History', renamed, null],
  ])('F2.54: the %s tab (labels %o) opens %s', (label, labels, area) => {
    expect(areaForShowTab(label, labels)).toBe(area)
  })
})
```

In `src/components/CustomShow/__test__/ExportToPdfButton.test.tsx`, add a mock beside the others:

```tsx
vi.mock('../../../usage', () => ({ recordUsage: vi.fn() }))
```

Then add `import { recordUsage } from '../../../usage'`, and these cases inside `describe('ExportToPdfButton', …)`. Its `beforeEach` already calls `vi.clearAllMocks()`.

```tsx
  it('F2.55: reports export once the PDF is saved', async () => {
    const el = document.createElement('div')
    el.className = 'RaTabbedShowLayout-content'
    document.body.appendChild(el)

    render(<ExportToPdfButton />)
    fireEvent.click(screen.getByTestId('pdf-button'))

    await waitFor(() => {
      expect(recordUsage).toHaveBeenCalledWith('export')
    })
    expect(mockSave.mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(recordUsage).mock.invocationCallOrder[0],
    )
    document.body.removeChild(el)
  })

  it('F2.55 (supporting): no summary on screen, no PDF, no report', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(<ExportToPdfButton />)
    fireEvent.click(screen.getByTestId('pdf-button'))
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(recordUsage).not.toHaveBeenCalled()
  })
```

`src/components/CustomLayout/__test__/ContentHeaderBar.usage.test.tsx`:

```tsx
/**
 * F2.56 — the monitoring drawer reports `monitoring` when it opens, not when it closes.
 *
 * @group unit
 * @group component
 */
import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

vi.mock('react-admin', () => ({
  LoadingIndicator: () => null,
  ToggleThemeButton: () => null,
  UserMenu: () => null,
}))
vi.mock('../../CustomBreadcrumb/CustomBreadcrumb', () => ({ default: () => null }))
vi.mock('../../CustomSearch/CustomSearch', () => ({ default: () => null }))
vi.mock('../../MonitoringDrawer/MonitoringDrawer', () => ({
  default: ({ isDrawerOpen }: { isDrawerOpen: boolean }) => (
    <div data-testid='monitoring-drawer' data-open={String(isDrawerOpen)} />
  ),
}))
vi.mock('../../../store/hooks', () => ({
  useAppSelector: (select: (state: unknown) => unknown) =>
    select({ user: { client_roles: [] } }),
}))
vi.mock('../../../usage', () => ({ recordUsage: vi.fn() }))

import { recordUsage } from '../../../usage'
import ContentHeaderBar from '../ContentHeaderBar'

describe('ContentHeaderBar', () => {
  it('F2.56: opening the monitoring drawer reports monitoring; closing it does not', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <ContentHeaderBar />
      </MemoryRouter>,
    )
    const button = screen.getByRole('button', { name: 'Monitoring' })

    await user.click(button)
    expect(screen.getByTestId('monitoring-drawer')).toHaveAttribute('data-open', 'true')
    await user.click(button)
    expect(screen.getByTestId('monitoring-drawer')).toHaveAttribute('data-open', 'false')

    expect(vi.mocked(recordUsage).mock.calls).toEqual([['monitoring']])
  })
})
```

- [ ] **Step 2: Run them and watch them fail**

Run: `./node_modules/.bin/vitest run src/usage/__test__/routeArea.test.ts src/components/CustomShow/__test__/ExportToPdfButton.test.tsx src/components/CustomLayout/__test__/ContentHeaderBar.usage.test.tsx`
Expected: F2.54 fails (`areaForShowTab` is not exported); F2.55 and F2.56 fail (`recordUsage` is never called). The file's other cases pass.

- [ ] **Step 3: Add the tab mapping**

Append to `src/usage/routeArea.ts`:

```ts
/**
 * The area a record's show tab opens, by label, or null.
 *
 * The History and Audit Log labels can be renamed per model (TAB_DISPLAY_NAMES),
 * so the caller passes the labels it rendered. Summary is null: the record's route
 * already counted `record`. Live Data is null: it is a link back, not an area.
 */
export function areaForShowTab(
  label: string,
  labels: { history: string; auditLog: string },
): UsageArea | null {
  if (label === 'Timeline' || label === labels.history) return 'history'
  if (label === labels.auditLog) return 'audit_log'
  if (label === 'Analytics') return 'analytics'
  return null
}
```

Change the `routeArea` line in `src/usage/index.ts` to:

```ts
export { areaForRoute, areaForShowTab } from './routeArea'
```

- [ ] **Step 4: Make the calls**

The table above gives the one-line calls. The three places that need more than one line:

`CustomShow.tsx`, in `CustomTabShowLayout`, right after `auditLogTabLabel` (`:320`), before any return:

```tsx
  // Fleet metrics K2: the History, Timeline, Audit Log and Analytics tabs are areas.
  const reportActiveTab = useCallback(
    (label: string) => {
      onActiveTabLabelChange(label)
      const area = areaForShowTab(label, { history: historyTabLabel, auditLog: auditLogTabLabel })
      if (area !== null) recordUsage(area)
    },
    [onActiveTabLabelChange, historyTabLabel, auditLogTabLabel],
  )
```

Then, at `:625`:

```tsx
            tabs={<TrackingTabs onActiveTabLabelChange={reportActiveTab} />}
```

Import with `import { areaForShowTab, recordUsage } from '../../usage'`.

`CustomList.tsx`, beside `handleHistoryToggle` (`:1070-1087`):

```tsx
  // As-of mode is history (spec §5.3.2); returning to "Current" is not.
  const handleAsOfChange = (value: Date | null): void => {
    setAsOfValue(value)
    if (value !== null) recordUsage('history')
  }
```

`CalculationLogPopupHost.tsx`, after `const target = …` (`:33`), before `if (!shown) return null` (`:40`):

```tsx
  // The popup opening is the calculation-log area; closing clears `target`.
  useEffect(() => {
    if (target !== null) recordUsage('calculation_logs')
  }, [target])
```

Relative import paths from each file to `src/usage`:

| Path | Files |
|---|---|
| `'../../usage'` | `CustomShow.tsx`, `ExportToPdfButton.tsx`, `ContentHeaderBar.tsx`, `GlobalSearchDialog.tsx` |
| `'../../../usage'` | `CustomList.tsx`, `CustomDatagrid.tsx`, `useAgGridExport.ts`, `CalculateFunctionality.tsx`, `CalculationLogPopupHost.tsx`, `FileDownload.tsx`, `SharepointField.tsx` |
| `'../../../../usage'` | `GCSField/GCSField.tsx` |

- [ ] **Step 5: Run the new cases, then every test file of a touched component**

Run: `./node_modules/.bin/vitest run src/usage src/components/CustomShow src/components/CustomLayout src/components/CustomSearch src/components/model-components src/api/httpClients`
Expected: F2.54–F2.56 pass. Every other file shows the same result as the same command on the base branch.

The calls go through MSW's default 204 from Task 1, so nothing reaches the network. A test that counts `fetch` calls could see one more, and only through a global `fetch` stub. If that happens, add `vi.mock('<path>/usage', async (o) => ({ ...(await o()), recordUsage: vi.fn() }))` to that file and say why in a comment. Never loosen its assertion.

- [ ] **Step 6: Commit**

```bash
git add src/usage src/components src/pages
git commit -m "feat(usage): the record tabs, drawers, exports, files, search and monitoring report their areas"
```

---

### Task 4: The call-site contract, the test-plan and the full suite

Covers the brief's (e), together with the tests in Tasks 1–3. **Size:** 0.5 h

**Files:**
- Test: `src/usage/__test__/callSites.test.ts` (create)
- Modify: `docs/test-plan/clusters/F02-shell_nav/{allocation.yaml,batches.md,cluster.md}`
- Create: `docs/test-plan/progress/sessions/<date>-usage-area-events.md`

Spec §7 asks that the interface's tests assert the call made at each emitting place. Spec §5.3.2 says foreign-key dropdowns, hover cards and background refreshes emit nothing. Behaviour is tested where position matters: F2.52, F2.53, F2.55 and F2.56. This test pins the rest from the source:

- every place in the table calls `recordUsage` with its area;
- no other file calls it;
- every area in `UI_AREAS` is reachable;
- the foreign-key and background-refresh files stay silent.

- [ ] **Step 1: Write the test**

`src/usage/__test__/callSites.test.ts`:

```ts
/**
 * F2.57–F2.58 — where the interface reports usage, and where it must not.
 *
 * Reads src/: each emitting place of lex-app spec §5.3.2 calls recordUsage with
 * its area, nothing else calls it, and the foreign-key components and background
 * refreshes never do. A call moved, added or lost fails here with the file named.
 *
 * @group unit
 */
import { readFileSync, readdirSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { UI_AREAS } from '../areas'

const SRC = fileURLToPath(new URL('../../', import.meta.url))

/** The spec's places with a literal area, and the areas each names (sorted). */
const LITERAL_CALLS: Record<string, string[]> = {
  'components/CustomLayout/ContentHeaderBar.tsx': ['monitoring'],
  'components/CustomSearch/GlobalSearchDialog.tsx': ['search'],
  'components/CustomShow/ExportToPdfButton.tsx': ['export'],
  'components/model-components/CalculateFunctionality/CalculateFunctionality.tsx': ['calculate'],
  'components/model-components/CalculateFunctionality/CalculationLogPopupHost.tsx': ['calculation_logs'],
  'components/model-components/FileDownload/FileDownload.tsx': ['files'],
  'components/model-components/SharepointField/SharepointField.tsx': ['files', 'files'],
  'components/model-components/list/CustomDatagrid.tsx': ['record'],
  'components/model-components/list/CustomList.tsx': ['analytics', 'create', 'history'],
  'components/model-components/list/GCSField/GCSField.tsx': ['files'],
  'components/model-components/list/useAgGridExport.ts': ['export'],
  'pages/CustomDashboard/CustomDashboard.tsx': ['dashboard'],
  'pages/StreamlitHome.tsx': ['analytics'],
}

/** Places that pass a computed area: the route listener and the record's tabs. */
const COMPUTED_CALLS = ['components/CustomShow/CustomShow.tsx', 'usage/useUsageRouteListener.ts']
/** What areaForRoute (F2.51) and areaForShowTab (F2.54) can return. */
const ROUTE_AREAS = ['grid', 'record', 'create', 'history', 'processes', 'reports', 'calculation_logs']
const TAB_AREAS = ['history', 'audit_log', 'analytics']

/** Spec §5.3.2: foreign-key dropdowns, hover cards and background refreshes emit nothing. */
const MUST_STAY_SILENT = [
  'components/CustomTooltips/ForeignKeyHoverCard/ForeignKeyHoverCard.tsx',
  'components/CustomTooltips/ForeignKeyTooltip/ForeignKeyTooltip.tsx',
  'components/model-components/forms/ForeignkeyFieldInput.tsx',
  'components/model-components/list/AGForeignKeyFilter.tsx',
  'components/MonitoringDrawer/MonitoringDrawer.tsx',
  'utils/ssrmRefresh.ts',
  'web-sockets/CalculationStatusUpdate.tsx',
  'web-sockets/GenericSocket.tsx',
  'web-sockets/ModelDataUpdate.tsx',
]

const SKIPPED = new Set(['usage/recordUsage.ts', 'usage/index.ts'])

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) {
      return entry.name === '__test__' || entry.name === '__tests__' ? [] : sourceFiles(path)
    }
    return /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [path] : []
  })
}

function scan(): { literal: Record<string, string[]>; computed: string[] } {
  const literal: Record<string, string[]> = {}
  const computed: string[] = []
  for (const file of sourceFiles(SRC)) {
    const name = relative(SRC, file).split(sep).join('/')
    if (SKIPPED.has(name)) continue
    const text = readFileSync(file, 'utf8')
    const areas = [...text.matchAll(/recordUsage\(\s*'([a-z_]+)'\s*\)/g)].map((m) => m[1]).sort()
    if (areas.length > 0) literal[name] = areas
    if (/recordUsage\(\s*[A-Za-z_]/.test(text)) computed.push(name)
  }
  return { literal, computed: computed.sort() }
}

describe('recordUsage call sites', () => {
  it('F2.57: every emitting place in the spec calls recordUsage with its area, and no other file does', () => {
    const { literal, computed } = scan()

    expect(literal).toEqual(LITERAL_CALLS)
    expect(computed).toEqual([...COMPUTED_CALLS].sort())
    const reachable = new Set([...Object.values(literal).flat(), ...ROUTE_AREAS, ...TAB_AREAS])
    expect([...reachable].sort()).toEqual([...UI_AREAS].sort())
  })

  it('F2.58: foreign-key dropdowns, hover cards and background refreshes never report', () => {
    const { literal, computed } = scan()

    for (const file of MUST_STAY_SILENT) {
      expect(() => readFileSync(join(SRC, file)), `${file} moved: update this list`).not.toThrow()
      expect(literal[file], file).toBeUndefined()
      expect(computed, file).not.toContain(file)
    }
  })
})
```

- [ ] **Step 2: Run it**

Run: `./node_modules/.bin/vitest run src/usage/__test__/callSites.test.ts`
Expected: `2 passed`. If F2.57 names a file, the call there was moved, lost or added. Fix the call, not the table, unless the spec's table changed.

- [ ] **Step 3: Run the whole suite on both branches**

Run on this branch, then in a worktree of the base branch with the same borrowed install: `./node_modules/.bin/vitest run 2>&1 | tail -5`
Expected: the same failures on both. The new scenarios pass on this branch only. Record both tallies for the session fragment.

- [ ] **Step 4: Bring the test-plan into sync**

In `docs/test-plan/clusters/F02-shell_nav/allocation.yaml`, set `max_scenario: 58` and add:

```yaml
  d:
    title: Usage area events for fleet metrics K2 (U/C tier)
    scenarios: F2.46-F2.58
    tier: C
    status: complete
    tests: {pass: <the count vitest reports for the files below>, skip: 0, xfail: 0}
    note: >-
      Release R3 of fleet metrics (lex-app spec 2026-09-29-fleet-metrics-phase1-design.md
      §5.3.2). recordUsage sends exactly {area, embed} to api/usage/area, once per tab,
      area, embed state and UTC day, through raHttpClient with keepalive, and swallows
      every failure. The route listener in CustomLayout reads resource kinds from the
      cached model hierarchy; the home route is reported by StreamlitHome and
      CustomDashboard; every other place is one explicit call, pinned by F2.57.
```

Append a `## Batch 2d — Usage area events for fleet metrics (<date>)` section to `batches.md`, in the shape of Batch 2c. It lists the test files:

- `src/usage/__test__/recordUsage.test.ts`
- `src/usage/__test__/routeArea.test.ts`
- `src/usage/__test__/useUsageRouteListener.test.tsx`
- `src/usage/__test__/callSites.test.ts`
- `src/pages/__test__/StreamlitHome.test.tsx`
- `src/components/CustomShow/__test__/ExportToPdfButton.test.tsx`
- `src/components/CustomLayout/__test__/ContentHeaderBar.usage.test.tsx`

Add one row per scenario, F2.46–F2.58, to `cluster.md`.

Create `docs/test-plan/progress/sessions/<date>-usage-area-events.md`:

```markdown
---
date: <date>
clusters: [F02]
tests_added: "13 scenarios (F2.46-F2.58)"
suite_tally: "<this branch> vs <base branch>, borrowed install: <same failures on both>"
---

The interface half of fleet metrics R3: [batch 2d](../../clusters/F02-shell_nav/batches.md).
The lex-app half (the `api/usage/area` endpoint, cluster 10o) lands in the lex-app release
that pins this frontend release.
```

- [ ] **Step 5: Commit**

```bash
git add src/usage/__test__/callSites.test.ts docs/test-plan
git commit -m "test(usage): pin where the interface reports usage, and where it must not"
```

---

### Task 5: The pull request and the lex-app-frontend release

Covers the brief's (f). **Size:** 0.5 h

**How a lex-app-frontend release is cut** (`.github/workflows/publish-frontend.yml` at `e24fed4b`; lex-app `docs/ci-cd/frontend-versioning.md:73-94`):

- **The trigger is a full GitHub Release.** The workflow runs `on: release: types: [released]` (`:21-23`). That event fires only for full releases: drafts fire nothing, and pre-releases fire `prereleased` (`:7-10`). `workflow_dispatch` takes `dry_run` (default `true`) and `version` (`:24-34`).
- **The version is the tag.** The workflow strips one leading `v` and refuses anything that is not `X.Y.Z…` (`:57-76`). `package.json` holds only a placeholder.
- **Then it publishes:**
  - it refuses to start a real publish without `PYPI_API_TOKEN_FRONTEND` (`:101-113`);
  - it runs the unit tests under the coverage gate, `yarn test --run --coverage --coverage.thresholds.lines=74` (`:120-123`);
  - it builds (`:125-129`) and wraps the build in a Python wheel (`:131-138`);
  - it publishes the wheel to PyPI (`:141-146`). PyPI rejects a version that already exists, and a version can never be reused.
- **The same release also starts `release.yml`.** It runs on `release: published` and provisions the staging environment with this tag (`.github/workflows/release.yml:1-14`).
- **lex-app must pin it.** lex-app's `requirements.txt:65` holds `lex-app-frontend~=2.2.0`. `~=2.2.0` accepts any `2.2.x` and nothing newer, so a new minor reaches customers only through a lex-app release that pins it. The pin bump is one line (`frontend-versioning.md:84-94`). lex-app's `check_frontend_pin.py` (run by `pip_publish.yml:202`) refuses to publish a lex-app pinned to a frontend version that is not on PyPI.

**Release this as a minor, never a patch.** `v2.2.0` is the latest tag in the local clone at the time of writing; confirm the latest with `git ls-remote --tags origin` and PyPI, then take the next minor. A `2.2.x` patch would reach every lex-app release that already pins `~=2.2.0` and has no endpoint. Its interface would then POST events that no route there records. Harmless, because `recordUsage` drops them, but pointless. A minor reaches no backend until lex-app pins it, together with the endpoint.

- [ ] **Step 1: Push and open the pull request against `lex-app-v2-pac-latest`**

```bash
git push -u origin feat/usage-area-events
gh pr create --base lex-app-v2-pac-latest --title "feat(usage): report the product areas a person opens (fleet metrics K2)" --body "$(cat <<'EOF'
Fleet metrics phase 1, release R3, the interface half. Design: lex-app docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md, revision 2, §5.3.2.

- `src/usage`: `recordUsage(area)` sends exactly `{area, embed}` to `api/usage/area` through `raHttpClient` (session cookie, `X-CSRFToken`), with `keepalive`. It sends at most once per tab, area, embed state and UTC day, and swallows every failure.
- `CustomLayout` reports the area each admin route opens, reading resource kinds from the cached model hierarchy. The home page reports `analytics` or `dashboard` itself.
- One call at each other place in the spec's table: record tabs, edit and create drawers, as-of mode, the Analytics toggle, calculate, the calculation-log popup, exports, files, global search, the monitoring drawer.
- No URL, model name, record id, search text, file name or user id is ever sent; F2.46 asserts the body, and F2.58 that foreign-key dropdowns and hover cards never report.
- No new dependency.

Tests: F02 batch 2d, F2.46–F2.58. `Run linters` is red on the base branch itself; `Run unit tests` is the signal.

The lex-app endpoint is lex-app batch 10o; lex-app pins the frontend release that carries this.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: After the merge, rehearse the publish**

```bash
gh workflow run publish-frontend.yml --repo ExcellenceCloudGmbH/process-admin-general-client \
  --ref lex-app-v2-pac-latest -f dry_run=true -f version=<next minor, e.g. 2.3.0>
```

Expected: the run is green. Its summary says `DRY RUN — nothing was published` and shows the line to pin (`:148-167`).

- [ ] **Step 3: Cut the release (ask the user first)**

A published version can never be reused, so the user confirms the version before this step.

```bash
gh release create v<next minor> --repo ExcellenceCloudGmbH/process-admin-general-client \
  --target lex-app-v2-pac-latest --title "v<next minor>" --notes-file <release notes>
```

Do not pass `--draft` or `--prerelease`. Either one fires no `released` event, and nothing is published.

- [ ] **Step 4: Confirm it is on PyPI**

```bash
curl -s https://pypi.org/pypi/lex-app-frontend/json | python -c "import json,sys; print(json.load(sys.stdin)['info']['version'])"
```

Expected: the new version.

- [ ] **Step 5: Hand over to lex-app**

The pin bump and the lex-app release are lex-app plan `2026-09-29-fleet-metrics-phase1-recorder.md`, Task 9, Step 2.

After the fleet upgrade, the §5.3.2 end-to-end verification shows these events in an instance's `lex_usage_daily` on channel `ui`. That verification is stage R3.1's evidence and is budgeted separately. A test user who signs in lands on `/`, which reports `dashboard` or `analytics` when the project has a widget dashboard or a Streamlit home. To get exactly the four rows that §5.3.2 step 2 expects, open the list by its URL.
