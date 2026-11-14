---
title: History Tab
aliases:
  - "interface/record-detail/history tab"
---

The [[using-the-app/record-detail/timeline tab|Timeline tab]] tells the story visually. The History tab gives you the raw data — a full AG Grid of every historical version of this record, sortable, filterable, and exportable. Because it's a standard table, this is the best place to explore historical values, compare versions, and verify changes.

## The History Grid

Every historical version of the current record is displayed as a row, newest first. Each row represents a snapshot of the record at a specific point in time, with all field values captured at that moment.

The columns include both history-tracking fields and all of the model's own fields:

| Column | What It Shows |
|---|---|
| **Valid From** / **Valid To** | When this version was in force. The current version has no Valid To |
| **Created By** | The user who originally created the record |
| **Edited By** | The user who made this particular change |
| **Calculation** | For `CalculationModel` records, the state at this version (`NOT_CALCULATED`, `IN_PROGRESS`, `SUCCESS`, `ERROR`, `CANCELLED`, `ABORTED`) |
| *All model fields* | The values at that point in time (e.g., Quarter, Total Expenses, Remaining Budget, etc.) |

You can [[using-the-app/the-grid/filtering and sorting|filter and sort]] this grid like any other — for example, filter by `Edited By` to see all changes made by a specific person, or sort by date to trace the evolution of the record.

The rows are past versions, so their **Edit** and **Delete** are greyed out. By default
only a superuser can change one, by double-clicking its cells — **Valid From** and
**Valid To** included. The eye icon opens a version — see [[#Jumping to Live Data]].

![One row per version, with the record's fields as they stood in that version; Edit and Delete are greyed out](images/record-detail/history-tab.svg)

## The Toolbar

Above the grid:

| Control | What It Does |
|---|---|
| **Refresh** | Reloads the versions |
| **Discard unsaved changes** (the curved arrow) | Goes back to the saved view. Greyed out when there is nothing to discard |
| **Save changes to current view** (the disk) | Saves the grid's filters, sorting and columns into the open view |
| **View selector** | Switches between saved views of the history grid, or creates one when you type a name and press Enter — see [[using-the-app/the-grid/saved views\|Saved Views]] |
| **Export** | Downloads an Excel file — but not of these versions; see the warning below |
| **Auto-fit columns** | Sizes every column to its content |
| **Settings** (the gear) | Row density: **Compact**, **Standard** or **Comfortable** |

> [!warning] Export doesn't export the versions
> In Lex App 2.5.0 the **Export** button on this tab asks for the model's live table, not
> its history. With nothing ticked you get the model's current records; ticking versions
> doesn't narrow it to those versions.

There is no time-travel control on this tab. To see the record as the system knew it at
a moment in the past, use **As Of** on the
[[using-the-app/record-detail/timeline tab#Time-Travel with As Of|Timeline tab]].

## Jumping to Live Data

The eye icon in a row opens that version in a side panel; **Open full view** there (or
⌘/Ctrl-click on the eye icon) takes you to the version's own page. That page has a
**Live Data** tab instead of History. Its **Go to Live Data** button takes you to the
current live version of this record — useful when you've found something interesting in
the history and want to check today's state.

## How History Is Created

You don't need to do anything to create history — it's automatic. Every `LexModel` in Lex App tracks its changes via [django-simple-history](https://django-simple-history.readthedocs.io/). Every save, update, or delete creates a new historical version with a complete snapshot of all field values.

This means:
- Every save adds a version rather than overwriting the last one
- Only a superuser may change a history row: in this grid or in **Full history**, a superuser can edit a version's cells in place, Valid From and Valid To included. Without that right, a correction that applies from an earlier date is made in code, as a backdated save — [[start-here/tutorial/Part 6 — History in Action|Part 6 of the tutorial]] shows how. Any correction is itself recorded in the system-time history, whose rows no one can edit (a superuser can only delete them, through the REST API)
- No data is ever lost — even deleted records are preserved in history
- You can always answer "what did this look like on date X?"

> [!note]
> For developers: see [[history-and-audit/bitemporal history]] for the backend implementation, including valid-time support and history querying.
