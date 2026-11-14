---
title: Table Settings
aliases:
  - "interface/the-grid/table settings"
---

Every table has a **gear** beside it. What's behind it is the set of choices that are
yours rather than the application's — which columns stay pinned,
how a number is formatted. Lex App remembers them in your browser, for that table, from
one visit to the next.

## Opening it

Click the gear (**Settings**) in the table's toolbar. The panel is titled **Table
settings** and says *Saved for you on this table*. "For you" means this browser: the
choices are stored in it, for this table only. Another browser or another computer
starts from the defaults, and anyone who uses the same browser profile sees your
choices.

![The table settings panel — display, column and format changes are kept the moment you make them; density is kept with the view](images/the-grid/table-settings.svg)

> [!tip] There is no Save button, and that's deliberate
> Display, column and format changes take effect and are stored the moment you make
> them. Earlier versions kept these choices in the
> [[using-the-app/the-grid/saved views|view]], which meant they needed an explicit save —
> and views like `default` are read-only, so in practice the settings could never stick
> at all. Keeping look-and-feel separate from view data is what lets shared views stay
> protected while your own preferences survive a reload. Density is the one exception:
> it is still kept with the view.

## Density

Three row heights — **Compact**, **Standard** and **Comfortable**. **Compact** is the
default. A density change belongs to the open view, so save the view to keep it. See
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
| **Hide ID column** | Leaves the record's ID column out of this table. Off by default. To copy an ID instead, see [[using-the-app/the-grid/index#The ID Column\|The ID Column]] |
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
| Display and column toggles, column formats | In this browser, per table | They stay on this table in this browser, whichever view is open. Another browser starts from the defaults |
| Density | With the [[using-the-app/the-grid/saved views\|view]] | A compact view for scanning and a comfortable one for review are two views you can switch between. A density picked on a read-only system view is lost on reload |
| Column format defaults | Declared by the application | The starting point for everyone, until a user overrides it |

## Selecting a range

With the status bar on, drag across cells the way you would in a spreadsheet. The bar
below the grid shows the **sum, average and count** of what you've selected — useful for
sanity-checking a column of figures without exporting anything.
