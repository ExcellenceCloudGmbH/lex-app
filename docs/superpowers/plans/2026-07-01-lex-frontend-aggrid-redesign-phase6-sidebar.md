# Frontend Redesign Phase 6 — Sidebar (collapsible tree)

> **Date:** 2026-07-01
> **Spec:** `docs/superpowers/specs/2026-06-30-lex-frontend-aggrid-redesign-design.md` §4.7
> **Frontend repo:** `process-admin-general-client`, branch `lex-app-v2-pac-latest`
> **Status:** Implemented (frontend commit `bd06a5d`)

**Goal:** Replace the sidebar's drill-down folder navigation with a collapsible
in-place tree, so the model hierarchy stays in view.

**Architecture:** Only the browse-mode render branch of `CustomMenu`
(`CustomSidebar.tsx`) changed. Search results and edit-mode drag-reorder keep
their existing drill-down/`Reorder` behavior — low blast radius.

## What was built

- **Collapsible tree** — a recursive `renderNode(key, node, keyPath, depth)`.
  Folders expand in place (children indented by `0.5 + depth * 0.85rem`) via an
  `expandedFolders: Set<string>` keyed by the full key path. `toggleFolder`
  flips membership. Collapsed children are conditionally unmounted (not just
  hidden) so behavior is deterministic.
- **Section headers** — top-level (`depth === 0`) folders render as uppercase
  `overline` labels with an expand/collapse chevron, no folder icon.
- **Nested folders** — chevron + `FolderRounded`/`FolderOpenRounded`, bold label.
- **Models** — `TableRowsRounded` icon; the active model (matched from
  `useLocation().pathname` first segment vs the node key) gets `selected`
  (`Mui-selected`), a primary-tinted background, primary text/icon, and a
  `3px` primary left border.
- **Default expansion** — a one-shot effect (`didInitExpand` ref) expands the
  top-level section folders when the hierarchy first loads; user toggles win
  afterward.

## Kept as-is

Search + results list, `MenuViewSelector`, `useMenuViews`, edit-mode
`Reorder` drag (still drill-down + breadcrumb), framer-motion in search
results, dark-mode via theme palette. Hierarchy source unchanged
(`useGetModelHierarchyQuery`).

## Tests

`CustomSidebar.test.tsx` updated: auto-expand visibility, collapse/re-expand
in place (siblings stay), top-level + nested model redirect, active-route
highlight (`Mui-selected`). `renderSidebar(theme, initialPath)` now seeds the
`MemoryRouter` route. **43/43 pass**; type-check clean (only the pre-existing
`ErrorMessages.test.ts` TS1149 casing error).

## Trial note

Per the spec this is a **trial** — revert to drill-down if the tree doesn't
work for us. Drill-down code still lives in the edit-mode branch, so reverting
is a localized change to the browse-mode branch only.
