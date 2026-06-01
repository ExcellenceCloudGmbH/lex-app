---
title: History Tab
---

The [[interface/record-detail/timeline tab|Timeline tab]] tells the story visually. The History tab gives you the raw data — a full AG Grid of every historical version of this record, sortable, filterable, and equipped with the **As-Of** time-travel control. Because it's a standard table, this is the best place to explore historical values, compare versions, and verify changes.

## The History Grid

Every historical version of the current record is displayed as a row. Each row represents a snapshot of the record at a specific point in time, with all field values captured at that moment.

The columns include both history-tracking fields and all of the model's own fields:

| Column | What It Shows |
|---|---|
| **Created By** | The user who originally created the record |
| **Edited By** | The user who made this particular change |
| **Calculation** | For `CalculationModel` records, the state at this version (`NOT_CALCULATED`, `IN_PROGRESS`, `SUCCESS`, `ERROR`) |
| *All model fields* | The values at that point in time (e.g., Quarter, Total Expenses, Remaining Budget, etc.) |

You can [[interface/the-grid/filtering and sorting|filter and sort]] this grid like any other — for example, filter by `Edited By` to see all changes made by a specific person, or sort by date to trace the evolution of the record.

> [!example]- 📸 Screenshot — History grid with version rows
> ![History tab showing an AG Grid of all historical versions](../images/record-detail/history-tab.jpeg)

## The As-Of Control

At the top of the History tab, the **As-Of** button lets you time-travel. Click it, pick a date and time, and the grid filters to show only the versions that were **active at that moment**.

This answers questions like:
- "What did this record look like on March 1st?"
- "What values were in place at the end of last quarter?"
- "Was this field already updated before the audit?"

> [!example]- 🎬 Video — As-Of time-travel in the History grid
> <video controls width="100%">
>   <source src="../../videos/record-history-as-of.mp4" type="video/mp4">
> </video>
> Click the As-Of button, pick a date from last month, and the grid updates to show the historical state at that point in time.

The As-Of control uses the [[features/tracking/bitemporal history|system time dimension]] — it shows you what the system believed to be true at the timestamp you selected. To see when a change was *actually effective* in the business sense, use the [[interface/record-detail/timeline tab|Timeline tab's]] effective-time view.

> [!note]
> The same As-Of control is also available at the [[interface/the-grid/index|table level]], where it filters the entire model's grid — not just one record's history.

### Clearing the Time Travel

Click the **Reset to Latest** button to exit time-travel mode and return to the current live data. The button is always visible when an As-Of date is active, so you never get stuck in the past.

## Jumping to Live Data

When viewing a historical record, a **Go to Live Data** button appears. Click it to navigate directly to the current live version of this record — useful when you've found something interesting in the history and want to check today's state.

## How History Is Created

You don't need to do anything to create history — it's automatic. Every `LexModel` in Lex App tracks its changes via [django-simple-history](https://django-simple-history.readthedocs.io/). Every save, update, or delete creates a new historical version with a complete snapshot of all field values.

This means:
- History cannot be tampered with — it's append-only
- No data is ever lost — even deleted records are preserved in history
- You can always answer "what did this look like on date X?"

> [!note]
> For developers: see [[features/tracking/bitemporal history]] for the backend implementation, including valid-time support and history querying.
