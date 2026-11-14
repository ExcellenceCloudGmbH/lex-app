---
title: Saved Views
aliases:
  - "interface/the-grid/saved views"
---

You've spent ten minutes getting the grid exactly right: columns reordered, two levels of grouping, a date filter for Q1, amounts sorted descending. Then you navigate away. When you come back tomorrow, do you want to rebuild that from scratch?

No. You save it.

## How Saved Views Work

A saved view captures the complete state of your grid:

- Column order and widths
- Active filters and filter values
- Sorting (columns and direction)
- Grouping and pivot configuration
- Row height / density setting

When you load a saved view, all of these settings are restored instantly — the grid reconfigures itself to exactly how you left it.

![A saved view named Q1 review, active in the toolbar](images/the-grid/saved-views.svg)

## Two Kinds of Views

The view selector in the toolbar organizes views into two groups:

### System Views

These are created by your developers via [[model-your-data/serializers|serializers]]. They define which fields are visible and in what order. System views appear at the top of the dropdown and **cannot be deleted** by users.

System views are also read-only: you can change the grid while one is open, but you
can't save the change into it. **Save changes to current view** stays greyed out, and its
tooltip says *System views are read-only — type a name in the view box to create your
own*.

Common examples:
- **Default** — the standard view showing all fields
- **Detail** — a focused view with key fields highlighted
- **Summary** — a condensed view for high-level review

### My Presets

These are views **you** create. Type a name in the view selector search bar and press **Enter** — a new preset is created with your current grid configuration saved to it.

## Creating a View

1. Set up the grid exactly how you want it — filters, sorting, grouping, column layout
2. Open the **View Selector** dropdown in the toolbar
3. Type a name for your view (e.g., "Monthly Reconciliation")
4. Press **Enter**

That's it. Your view appears under **My Presets** and is available from now on.

## Saving and Discarding Changes

As soon as you change the grid — a filter, the sorting, the grouping, a column, the
density — the open view counts as changed. Two buttons left of the view selector deal
with that:

- **Save changes to current view** (the disk) writes the grid as it is now into the open
  preset. It is greyed out when nothing changed, and on a system view.
- **Clear all changes** (the curved arrow) goes back to the view as it was saved.

Changes you don't save are gone when you leave the table: coming back — from a record's
page, say — opens the saved view again.


## Switching Views

Open the view selector and click any view to load it. The grid instantly reconfigures — columns shift, filters apply, grouping activates. It's like switching between different lenses on the same data.

This is particularly useful when different people on a team use the same model differently:
- An **analyst** might have a pivot view comparing quarterly figures
- A **manager** might have a grouped view organized by team
- A **data entry** operator might have a minimal view with only the editable columns

Presets live in your browser, one set per table. A colleague doesn't see yours, and you
won't find them in another browser or on another computer; anyone who uses the same
browser profile shares them.

## Deleting a View

Click the **trash icon** next to one of your presets in the dropdown. A **Delete View** dialog asks you to confirm; a deleted view can't be brought back.

> [!important]
> System Views cannot be deleted — they are defined in code and are consistent for everyone. Only your personal presets can be removed.

## What Gets Saved

| Grid Setting | Saved? |
|---|---|
| Column order | ✅ |
| Column widths | ✅ |
| Column visibility | ✅ |
| Filters and filter values | ✅ |
| Sort order | ✅ |
| Row grouping | ✅ |
| Pivot mode configuration | ✅ |
| Density (row height) | ✅ |
| Selected rows | ❌ |
| Scroll position | ❌ |

Because density travels with the view, a density you pick while a system view such as
**Default** is open lasts only until you reload or leave the table. To keep it, create a
preset of your own.
