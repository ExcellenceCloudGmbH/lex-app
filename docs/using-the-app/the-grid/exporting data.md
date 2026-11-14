---
title: Exporting Data
aliases:
  - "interface/the-grid/exporting data"
---

Data that lives only inside an application isn't fully useful. Stakeholders need spreadsheets. Auditors need files. Reports need attachments. Lex App's export system is designed around one principle: **what you see is what you get**.

## How Export Works

Click **Export** (the download arrow) above the table. (It used to live only in the right-click
menu, which meant it was reachable only by people who already knew it was there.) The system generates a file that mirrors your current grid view:

- **Filters applied?** Only filtered rows are exported.
- **Columns grouped?** The group hierarchy is preserved in the file.
- **Pivot mode active?** The cross-tabulated layout is exported as-is.
- **Rows selected?** Exactly those rows are exported — nothing else.

![One row ticked, and the export button counting it](images/the-grid/export-selection.svg)

Selection is the whole contract, and there is no mode to switch: with nothing selected
you get the table as the grid is currently showing it, filters, sort, grouping and pivot
carried in; with rows selected you get precisely those rows. The button's tooltip names
which of the two is about to happen, which matters at the moment of the click — a file
that turns out to hold the wrong rows is discovered far too late.

The export runs server-side, so even large datasets (tens of thousands of rows) export reliably without browser memory issues. A message says *Preparing export…* while the file is built, and *Export ready* once your browser has it.

## Export Format

Every export is an Excel workbook (`.xlsx`). **Export to Excel** in the grid's
right-click menu makes the same file as the toolbar button.

## What Gets Exported

The export captures the grid exactly as you're viewing it:

| Grid Feature | Reflected in Export? |
|---|---|
| Visible columns (order & selection) | ✅ |
| Active filters | ✅ Filtered data only |
| Sort order | ✅ |
| Row grouping hierarchy | ✅ Group labels and nested rows |
| Pivot mode cross-tabulation | ✅ Pivoted columns as-is |
| Aggregation totals (group rows) | ✅ |
| Ticked rows | ✅ Only those, whenever any are ticked |
| Column widths | ❌ Auto-fit in export |
| Cell formatting (colors, fonts) | ❌ Plain data |

## Exporting Grouped Data

When rows are grouped, the export preserves the hierarchy. Group headers appear as labeled rows with aggregate values, and child rows are nested beneath them:

```
▼ Engineering                          Total: €24,500
    Software license renewal           €3,200
    Conference travel                  €1,800
    ...
▼ Marketing                           Total: €15,200
    Ad campaign Q1                     €8,000
    ...
```

This makes the file immediately useful for reporting — no rearranging needed.

## Exporting Pivoted Data

When pivot mode is active, the exported file reflects the pivoted layout:

| Team | Q1 2026 | Q2 2026 | Q3 2026 | Q4 2026 |
|---|---|---|---|---|
| Engineering | €8,200 | €6,100 | €5,400 | €4,800 |
| Marketing | €4,500 | €3,800 | €3,200 | €3,700 |

This is the same cross-tabulation you see on screen — ready to paste into a presentation or share with a stakeholder.

## Exporting Selected Rows

If you need a subset, click **Select rows** in the toolbar and tick the rows you want.
**Export** then exports just those; its tooltip reads *Export 3 selected rows*. The
checkbox in the header ticks a whole page — or, when you ask for it, every row in the
table (see [[using-the-app/the-grid/index#Selecting and Deleting Rows|Selecting and Deleting Rows]]).
**Clear** in the selection bar unticks them, and **Export** goes back to the whole table.

> [!tip]
> Combine selection with [[using-the-app/the-grid/filtering and sorting|filters]] for precision: filter down to the relevant records, select a handful, and export just those.

## Datetimes in Excel exports

Excel has no timezone type. When your data contains datetime values, the exported workbook shows them in your **browser's local timezone** — the same time you see in the grid — rather than UTC. If the exported file is opened by someone in a different timezone, the digits they see represent the original exporter's local time.
