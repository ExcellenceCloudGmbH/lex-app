# LEX-app Frontend & AG-Grid Redesign — Design

> **Date:** 2026-06-30
> **Status:** Approved design (brainstorm complete) — pending implementation plan
> **Repos touched:** `process-admin-general-client` (frontend), `lex-app` (backend, under `lex/`)
> **Inspiration:** CEO design study at `DesignVorschlagLEX_app/ag_grid_table/index.html`

---

## 1. Overview & Goals

Redesign the AG-Grid table view and surrounding UI of the lex-app frontend to be cleaner,
more organized, and more professional, and add backend support for richer field/foreign-key
presentation. Ten improvement areas were brainstormed and grouped into six design sections plus
two backend mechanisms.

The work spans both repos: the frontend (`process-admin-general-client`) and the backend
framework (`lex-app`). It is delivered as **one cohesive design**, implemented in **phases on a
single feature branch** (backend foundations + toolbar first, then the richer UI features).

## 2. Cross-cutting constraints

These apply to **every** change below:

1. **Do not change the theme.** No new color palette, branding, or theme tokens. New components
   reuse the existing MUI theme palette. The greys in the mockups were placeholders only.
2. **Dark-mode compatible.** Every new/changed component must render correctly in dark mode via
   the existing theme palette (`theme.palette.mode`).
3. **Do not break existing functionality.** Server-side row model, filtering, sorting, inline
   editing, grouping/pivot, exports, websocket calc streaming, saved views, history/as-of,
   permissions — all must keep working. Changes are additive or replace like-for-like.
4. **Backend-driven where it makes sense.** Reuse the existing metadata pattern (`/fields/`
   endpoint, `?serializer=<name>` param, `api_serializers` registry) rather than inventing new
   transport.

## 3. Architecture decisions (from brainstorm)

- **Formatting/display config is HYBRID (decision: C).** The backend declares display *defaults*
  (number format, decimals, currency, percent; FK display label; FK preview field set). The
  frontend applies them via AG-Grid `valueFormatter`/renderers and lets a user *override per
  saved view*, persisted in the existing per-resource localStorage view store.
- **FK preview uses a serializer (decision: serializer-based).** A target model may register a
  lightweight `preview` serializer via the existing `api_serializers` registry. The hover fetches
  the related record with `?serializer=preview`. This gives curated, ordered, **permission-aware**
  fields for free. Fallback when no `preview` serializer exists: frontend shows the first N
  readable fields from the default serializer.
- **Reserved name "preview".** Because `preview` becomes a reserved serializer key consumed by FK
  hover, the frontend must **block users from saving a (grid or menu) view named "preview"**
  (case-insensitive) to avoid collision. Show a validation message on the save input.
- **Settings persistence is local per-browser for now.** Density, display toggles, and per-column
  format overrides persist with the existing per-resource saved views in localStorage. A
  backend per-user preferences store is explicitly out of scope (possible future add).

## 4. Section designs

### 4.1 Table shell — toolbar, columns/filters, FK chips, actions (areas 2, 8)

**Problem:** The toolbar above the grid is cluttered — three buttons (`CustomListActions.tsx`
TopToolbar "Refresh", `CustomList.tsx` "Reload", `GridViewToolbar.tsx` "Reload") all call the
same `refreshServerSideGrid(true)` purge. Columns/Filters are hidden behind a "Sidebar" toggle.

**Design:**
- Collapse the three refresh controls into **one icon-only refresh button**. Keep the single
  `refreshServerSideGrid` handler; remove the duplicate buttons.
- **Columns** and **Filters** become direct toolbar buttons that open AG-Grid's
  `agColumnsToolPanel` / `agFiltersToolPanel` respectively (via grid API `openToolPanel`). Remove
  the generic "Sidebar" toggle as the required entry point. The `sideBar` grid option stays
  configured with both tool panels; the buttons simply open/close the relevant one.
- **As-Of + History** merge into a single segmented switch (see 4.4).
- **Density** moves into the Settings gear (see 4.6).
- Toolbar final shape (left → right): saved-view search/combo · spacer · History switch ·
  Columns · Filters · Export · Refresh (icon) · Settings (gear).

**Files:** `CustomList.tsx`, `CustomListActions.tsx`, `GridViewToolbar.tsx`, `CustomDatagrid.tsx`
(`sideBar` config remains).

### 4.2 Foreign-key display & hover card (areas 3, 6)

**Backend (FK serialization):**
- A model declares the field used as an FK's human label, e.g. a `fk_display` hint (the field name
  on the *target* model, defaulting to `__str__`/`short_description`).
- The serializer emits, alongside the existing FK primary key, a sibling label field
  `"<field>_label"` (string). **The FK field value itself stays the PK** so filter/sort/edit/group
  are unaffected (non-breaking). Optionally a second field for the chip's secondary meta.
- `/fields/` metadata for the FK column gains the display hint + existing `target` model name.

**Frontend (chip):**
- FK cells render as a **chip**: a dot + the `<field>_label` + optional secondary meta. The
  underlying cell value remains the PK.
- **Hover card** replaces the current horizontal-table tooltip
  (`ForeignKeyTooltip.tsx`): a compact vertical **label → value** card. Title = the FK label;
  a "kind" line names the target model; values are right-aligned with tabular numerals and use
  each field's own backend format. Special fields keep their renderers (e.g. `calculation_log`
  shows View/Download).
- **Preview field set:** the card fetches `/api/<target>/<id>/?serializer=preview` (permission-aware,
  curated, ordered). Fallback: first N readable fields from the default serializer.
- **Footer actions:** **Open record ↗** and **Filter by this value** (filters the grid to rows
  sharing this FK). **Copy id is removed.**
- Hover stays interactive (`tooltipInteraction`-style) so users can click inside.

**Files:** backend `lex/api/serializers/base_serializers.py`, `lex/api/views/model_info/Fields.py`,
`LexModel` (display hint declaration); frontend `FieldView.tsx`, `ForeignKeyTooltip.tsx`.

### 4.3 Value formatting (area 5)

- Backend `/fields/` metadata gains a per-field **format spec** (e.g.
  `{ "format": "currency", "currency": "EUR", "decimals": 2 }`, or `percent`, `number` with
  decimals, thousands separators). Declared on the model (hybrid defaults).
- Frontend builds an AG-Grid `valueFormatter` per column from the format spec (using
  `Intl.NumberFormat` with the app locale). Applies to grid cells, hover-card values, and is
  honored by Excel/CSV export where possible.
- A user can override a column's format **per saved view** via the Settings panel (4.6); overrides
  layer on top of backend defaults and Reset returns to defaults.

**Files:** backend `Fields.py` + model declaration; frontend `CustomDatagrid.tsx` (column build),
settings store.

### 4.4 History & As-of (area 9)

- Replace the separate As-Of popover (`AsOfControl.tsx`) and History button with **one segmented
  switch** in the toolbar: **Current / As of / History**.
  - *Current* — latest effective rows (default).
  - *As of* — reveals a date/time picker; sends `as_of=<iso>` query param (existing mechanism).
  - *History* — shows every version; **auto-reveals version columns** (Version, Valid from, Valid
    to). Superseded versions are visually muted; current version flagged. Columns hide on leaving
    History mode.
- **Per-row history** — a 🕑 row action opens a **timeline drawer** (reusing `HistoryTimeline.tsx`)
  showing versions with field-level diffs vs. the previous version; clearer than a wide table for
  a single record.

**Files:** `CustomList.tsx` (switch), `AsOfControl.tsx` (folded into switch), `HistoryTimeline.tsx`,
`CustomDatagrid.tsx` (conditional version columns + row styling).

### 4.5 Calculation & calc-log (area 7)

- The **Calculate** button moves into the **action column, left of Show**, on CalculationModel
  rows only (`CalculateFunctionality.tsx` rendered in the actions cell rather than the
  `calculation_id` column).
- Each row shows an inline **status pill** — Calculating / Success / Error — with **colors +
  icons**, dark-mode compatible. This replaces hunting for a separate calculation-log column.
- Running a calculation opens a **right-side drawer** (non-blocking) that **streams the log live**
  via the existing websocket calc stream. It renders LexLogger output faithfully (INFO/WARN/ERROR
  text lines *and* tables, in order), with a progress header (start time, elapsed, who triggered)
  and footer links to the **full log tree** and **PDF download** (nothing lost).
- Drawer is the chosen approach; may be refined (or swapped for a docked console/modal) later if
  the feel is off — without breaking functionality.

**Files:** `CalculateFunctionality.tsx`, `FieldView.tsx` (actions cell), new calc-log drawer
component, `CalculationLogFieldView.tsx`/`CalcLogMethodModal.tsx` (reuse log rendering),
`calculationSlice` (status), web-sockets layer.

### 4.6 Settings panel (area 10)

- A **⚙ Settings** gear in the toolbar opens a panel scoped to the current grid containing:
  - **Density** — Compact / Standard / Comfortable (moved out of the toolbar; reuses
    `DensityControl.tsx` logic / row height).
  - **Display toggles** — show status bar (totals/selection), wrap header text, show row-index
    column.
  - **Column format overrides** — per-column format editor (the per-view layer of the hybrid);
    **Reset to backend defaults**.
- Persisted with the existing per-resource saved **views** in localStorage (per-user, this
  browser). No backend user-prefs store (out of scope).

**Files:** new Settings panel component, `DensityControl.tsx` (folded in), `CustomList.tsx`
(gear button), saved-view store.

### 4.7 Sidebar redesign (area 1)

**Problem:** Current `CustomSidebar.tsx` uses drill-down folders that replace the whole list,
folders/models look alike, no grouping, no icons, no active highlight.

**Design:**
- **Collapsible tree** — folders expand in place (chevron), children indented; the structure stays
  in view instead of being replaced on each drill. (Deep nodes still scroll; drill-down may remain
  a fallback for very deep trees.) **Trial — revert to drill-down if it doesn't work for us.**
- **Section headers** — top-level folders of the backend hierarchy become uppercase group labels.
- **Icons** distinguish folders vs. models; the **active model** is highlighted with a
  primary-colored pill + left bar.
- **Kept as-is:** search + results list, saved **menu views** selector (`MenuViewSelector.tsx`,
  `useMenuViews.ts`), **drag-reorder** in edit mode, framer-motion animations, dark-mode via theme.
- Hierarchy source unchanged (`useGetModelHierarchyQuery`).

**Files:** `CustomSidebar.tsx`, `MenuViewSelector.tsx`, `useMenuViews.ts`.

## 5. Phasing (single branch)

1. **Phase 1 — Backend foundations:** FK label serialization (`<field>_label`), `fk_display`
   hint, per-field format spec in `/fields/`, `preview` serializer support, reserved-name note.
2. **Phase 2 — Toolbar de-clutter:** collapse refresh buttons, Columns/Filters direct buttons,
   merge As-Of/History into the switch, move Density out.
3. **Phase 3 — Data presentation:** value formatting (`valueFormatter`), FK chips + new hover card.
4. **Phase 4 — Features:** Calculate-in-actions + status pills + calc-log drawer; History mode
   version columns + per-row timeline drawer.
5. **Phase 5 — Settings panel** (density + display toggles + format overrides).
6. **Phase 6 — Sidebar** collapsible tree + section headers.

Order is a guide; phases 2–6 are largely independent once Phase 1 lands.

## 6. Out of scope

- Theme/color/branding changes.
- Backend per-user preferences store (settings stay local per-browser).
- Streamlit dashboard surface from the CEO study.
- New export/import formats beyond what exists.

## 7. Risks & notes

- **FK serialization must stay non-breaking** — keep the FK cell value as the PK; only *add*
  `<field>_label`. Verify filter/sort/edit/group/SSRM still work.
- **Reserved "preview"** — enforce in both grid-view and menu-view name validation.
- **`lex.lex_app.tests` loader quirk** and existing skipped tests remain as-is; new backend code
  needs paired cluster tests per the lex-testing workflow.
- **Calc-log drawer** reuses the websocket stream; ensure multiple concurrent calcs render to the
  correct row/drawer.
- Two-repo change — coordinate backend metadata shape with frontend consumption; land Phase 1
  (backend) first so the frontend can rely on the new metadata.

## 8. Reference — current code map

- Frontend list/grid: `CustomList.tsx`, `CustomDatagrid.tsx`, `BareDatagridAGClient.tsx`
  (AG-Grid 33.0.4).
- Refresh dups: `CustomListActions.tsx:36-46`, `CustomList.tsx:980-990`, `GridViewToolbar.tsx:82-92`.
- FK: `FieldView.tsx` (`ForeignKeyReferenceContent`), `ForeignKeyTooltip.tsx`.
- Calc: `CalculateFunctionality.tsx`, `CalculationLogFieldView.tsx`, `CalcLogMethodModal.tsx`.
- History/as-of: `AsOfControl.tsx`, `HistoryTimeline.tsx`.
- Density: `DensityControl.tsx`. Sidebar: `CustomSidebar.tsx`, `MenuViewSelector.tsx`,
  `useMenuViews.ts`.
- Backend serializer: `lex/api/serializers/base_serializers.py` (FK → PK only today). Metadata:
  `lex/api/views/model_info/Fields.py` (`?serializer=` supported; FK `target` emitted; no display
  hints today). Per-model serializers: `api_serializers` registry.
