---
title: Saved Views
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

## Two Kinds of Views

The view selector in the toolbar organizes views into two groups:

### System Views

These are created by your developers via [[features/data-pipeline/serializers|serializers]]. They define which fields are visible and in what order. System views appear at the top of the dropdown and **cannot be deleted** by users.

Common examples:
- **Default** — the standard view showing all fields
- **Detail** — a focused view with key fields highlighted
- **Summary** — a condensed view for high-level review

### My Presets

These are views **you** create. Type a name in the view selector search bar and press **Enter** — a new preset is created with your current grid configuration saved to it.

> [!example]- 🎬 Video — Creating a saved view
> <video controls width="100%">
>   <source src="../../videos/grid-create-saved-view.mp4" type="video/mp4">
> </video>
> Type "Q1 Travel Expenses" into the view selector, press Enter, see the new preset appear in "My Presets".

## Creating a View

1. Set up the grid exactly how you want it — filters, sorting, grouping, column layout
2. Open the **View Selector** dropdown in the toolbar
3. Type a name for your view (e.g., "Monthly Reconciliation")
4. Press **Enter**

That's it. Your view appears under **My Presets** and is available from now on.

<!-- 📸 SCREENSHOT: View selector dropdown showing "System Views" and "My Presets" groups, with a "Press Enter to create" prompt -->

## Switching Views

Open the view selector and click any view to load it. The grid instantly reconfigures — columns shift, filters apply, grouping activates. It's like switching between different lenses on the same data.

This is particularly useful when different people on a team use the same model differently:
- An **analyst** might have a pivot view comparing quarterly figures
- A **manager** might have a grouped view organized by team
- A **data entry** operator might have a minimal view with only the editable columns

Each person saves their own presets and switches between them as needed.

## Deleting a View

Hover over any user-created preset in the dropdown and click the **trash icon** to delete it. You'll be asked to confirm.

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
