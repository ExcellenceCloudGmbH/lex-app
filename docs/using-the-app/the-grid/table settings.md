---
title: Table Settings
aliases:
  - "interface/the-grid/table settings"
---

Every table has a **gear** beside it. What's behind it is the set of choices that are
yours rather than the application's — how tight the rows are, which columns stay pinned,
how a number is formatted. Lex App remembers them for you, on that table, and they follow
you from one visit to the next.

## Opening it

Click the gear in the table's toolbar. The panel says *Saved for you on this table*, which
is exactly what it does: your choices apply to this model's table and nobody else's
account.

![The table settings panel: density, display and column options, and per-column number formats](../../images/the-grid/table-settings.svg)

> [!tip] There is no Save button, and that's deliberate
> Changes take effect and are stored the moment you make them. Earlier versions kept these
> choices in the [[using-the-app/the-grid/saved views|view]], which meant they needed an
> explicit save — and views like `default` are read-only, so in practice the settings
> could never stick at all. Keeping look-and-feel separate from view data is what lets
> shared views stay protected while your own preferences survive a reload.

## Density

Three row heights — **Compact**, **Standard** and **Comfortable**. See
[[using-the-app/the-grid/density and display|Density & Display]] for what each is good for.

## Display

| Setting | What it does |
|---|---|
| **Status bar** | Shows the bar under the grid that carries the live sum, average and count for a selected range. On by default |
| **Wrap header text** | Lets long column headers wrap onto a second line instead of being cut off. On by default |
| **Time in date columns** | Shows the time alongside the date. Off by default — a date column reads more cleanly without it, and the full timestamp is still there on hover |

## Columns

| Setting | What it does |
|---|---|
| **Column filter button** | Shows the filter control in each column header. On by default — filtering is always available from the column menu, but a control nobody can see is a control nobody uses |
| **Row-index column** | Adds a narrow numbered column at the far left. Off by default |
| **Pin selection column** | Keeps the row-selection checkboxes pinned to the left edge while you scroll sideways. On by default |
| **Pin calculation status** | Keeps the calculation status pill pinned to the left. On by default, and only appears for models that run calculations |

## Column formats

Any numeric column can be displayed the way you want to read it:

| Format | Shows |
|---|---|
| **Number** | A plain number |
| **Currency** | An amount with a currency symbol — pick the currency alongside it |
| **Percentage** | A percentage |

**Decimals** sets how many decimal places to show, or **Auto** to leave it to the value.

An application can declare a format for a column itself, and where it has, the dropdown
names it — *Default (currency)* — so you can see what you're overriding. Your own choice
sits on top of that. **Reset to backend defaults** clears your overrides and hands every
column back to whatever the application declares.

## What is saved where

Worth knowing, because it explains why some choices follow you and others follow the view:

| Choice | Stored | Consequence |
|---|---|---|
| Display and column toggles, column formats | Per user, per table | They follow you, on this table, and are not affected by which view you're in |
| Density | With the [[using-the-app/the-grid/saved views\|view]] | A compact view for scanning and a comfortable one for review are two views you can switch between |
| Column format defaults | Declared by the application | The starting point for everyone, until a user overrides it |

## Selecting a range

With the status bar on, drag across cells the way you would in a spreadsheet. The bar
below the grid shows the **sum, average and count** of what you've selected — useful for
sanity-checking a column of figures without exporting anything.
