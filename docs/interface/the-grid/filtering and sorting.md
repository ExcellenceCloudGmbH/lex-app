---
title: Filtering & Sorting
---

Finding the right data shouldn't require a database query. Lex App's grid lets you filter and sort visually — click, type, select — and the results update instantly.

## Column Filters

Every column header has a built-in filter. Click the menu icon on any column to open its filter panel. The filter type adapts to the data:

| Column Type | Filter Style | Example |
|---|---|---|
| **Text** | Contains, starts with, equals, not equals | Find expenses where description *contains* "travel" |
| **Number** | Contains, equals, not equals, blank, not blank | Find amounts matching "5000" |
| **Date** | Before, after, between, exact date, blank, not blank | Expenses *after* January 1, 2026 |
| **Boolean** | True / False / All | Show only *approved* expenses |
| **Foreign Key** | Dropdown of related records | Filter by a specific team or employee |

> [!example]- 🎬 Video — Column filtering in action
> <video controls width="100%">
>   <source src="../../videos/grid-column-filter.mp4" type="video/mp4">
> </video>
> Open a column filter on the Amount column, type a value to filter, see the grid update instantly.

### Text Filters

For text columns, simply start typing in the filter input. The grid filters as you type — no need to press Enter. You can also use the condition dropdown for more specific matches:

- **Contains** — the default; matches anywhere in the text
- **Starts with** — useful for codes or identifiers
- **Equals** — exact match only
- **Not equal** — exclude specific values
- **Blank / Not blank** — find missing data

### Date Filters

Date columns use a calendar picker for precision. Select a range with "between" to isolate a specific period — like all expenses from Q1 2026. The built-in date picker handles timezones automatically.

<!-- 📸 SCREENSHOT: Date filter open showing a date range selection -->

### Foreign Key Filters

When a column references another model (like an Employee's Team), the filter shows a searchable dropdown of all related records. Select one or more to narrow the view.

<!-- 📸 SCREENSHOT: Foreign key filter dropdown showing a list of teams -->

## Multi-Column Sorting

Click any column header to sort by that column. Click again to reverse the order. A small arrow indicator shows the current sort direction.

For **multi-column sorting**, hold `Shift` and click additional columns. This creates a sort priority chain — for example, sort by Team first, then by Amount within each team.

| Sort Order | Column | Direction |
|---|---|---|
| 1st | Team | A → Z |
| 2nd | Amount | High → Low |
| 3rd | Date | Newest first |

The sort indicators show the priority number next to each sorted column header.

> [!tip]
> To clear all sorting, right-click any column header and select **Reset Columns** from the context menu.

## Combining Filters

Filters stack. Apply a date filter, then a category filter, then sort by amount — each filter narrows the dataset further. The grid shows the total visible row count in the status bar, so you always know how much data you're looking at.

This is especially powerful with [[interface/the-grid/saved views|saved views]]: set up a complex filter combination once, save it, and switch to it instantly whenever you need it.

> [!example]- 🎬 Video — Building a multi-filter view
> <video controls width="100%">
>   <source src="../../videos/grid-multi-filter.mp4" type="video/mp4">
> </video>
> Build up a view step by step: apply a date range, then category filter, then sort by amount.
