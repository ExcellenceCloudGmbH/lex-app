---
title: Filtering & Sorting
aliases:
  - "interface/the-grid/filtering and sorting"
---

Finding the right data shouldn't require a database query. Lex App's grid lets you filter and sort visually — pick, type, click — and the table refreshes as soon as you apply a filter.

## Column Filters

Every column header has a built-in filter. Click the menu icon on any column to open its filter panel. The filter type adapts to the data:

| Column Type | Filter Style | Example |
|---|---|---|
| **Text** | A [value list](#value-lists), plus contains, begins with, equals, does not equal, blank, not blank | Pick *North* and *South* in a region column |
| **Long text** | Contains, begins with, equals, does not equal, blank, not blank | Find invoices whose notes *contain* "reminder" |
| **Whole number** | A value list, plus equals, greater than, less than, between, blank, not blank | Pick the years *2025* and *2026* |
| **Decimal number** | Equals, greater than, less than, between, blank, not blank | Find amounts *greater than* 5,000 |
| **Date** | A value list, plus before, after, between, exact date, blank, not blank | Invoices dated *after* January 1, 2026 |
| **Date and time** | Before, after, between, exact date, blank, not blank | Orders placed *between* two dates |
| **Yes / No** | A value list: Yes, No, (Blank) | Show only *paid* invoices |
| **Choice** | A value list of the choices the column holds | Pick *Open* and *Overdue* |
| **Foreign Key** | A value list of the related records, by name | Filter by two or three customers |

![The grid with a filter in force — five funds narrowed to one](images/the-grid/filter-applied.svg)

A value list applies as you pick: each value shows as a tag with its own **×**, and picking a value, removing a tag, or clearing them all refreshes the table at once — the list stays open the whole time. A condition waits for **Apply**: type it, then click **Apply** (or press Enter) and the table refreshes — nothing reloads while you're still typing. **Reset** clears it. The date-and-time filter needs no Apply either: it applies as soon as you pick a date, and has its own **Clear Filter** button.

The **Filters** tab on the grid's right edge holds the same filters, column by column, so you can set several without opening each column's menu.

### Text Filters

For text columns, type in the filter input and apply. The condition dropdown chooses how the text has to match:

- **Contains** — the default; matches anywhere in the text
- **Begins with** — useful for codes or identifiers
- **Equals** — exact match only
- **Does not equal** — exclude specific values
- **Blank / Not blank** — find missing data

### Date Filters

Date columns use a calendar picker for precision. Select a range with "between" to isolate a specific period — like all invoices from Q1 2026. When you enter a date without a time, Lex App treats it as the whole day rather than a single instant. The built-in date picker handles timezones automatically.


### Foreign Key Filters

When a column references another model (like an invoice's customer), its filter is a value list of the related records, shown by name. It lists the records the column actually refers to — not every record of that model — so pick one or more to narrow the view.

## Value Lists

Most columns list their own values, each with the number of rows that have it. Pick the values you want, and the table keeps the rows that have any of them.

- **Nothing picked means no filter.** A list opens with nothing picked, and there is no *Select All* button. Typing in the search box and pressing **Enter** picks the highlighted match — normally the first one shown — not every value the search shows.
- **Every value shows its row count**, so you can see how many invoices are *Overdue* before you pick it.
- **The lists narrow each other.** The counts follow every other filter in force: once a region is picked, the customer list only offers that region's customers. A column's own filter never narrows its own list, so you can always pick one more value.
- **A picked value stays.** If the other filters leave no rows with a value you picked, it stays in the list — as its tag — with a count of 0, so you can still remove it.
- **Values appear by label.** Related records appear by name, choices by their label, yes/no columns as *Yes* and *No*, and dates as `YYYY-MM-DD`. Values are sorted by label — numbers in numeric order — with *(Blank)* last.
- **(Blank) is a value too.** It stands for rows with nothing in the column. On a text column, empty text counts as blank, so *(Blank)* finds both.
- **Up to 200 at a time — but searchable past it.** A list shows up to 200 values per screen. Typing in the search box asks the server, so it can find any value the other filters leave, not just the ones already on screen. To browse further without searching, narrow the other filters first — or, on text, whole-number and date columns, use the condition instead: it checks every row.

The lists only count rows you're allowed to see, and never offer a value you couldn't see in the grid.

On text, whole-number and date columns the filter has two parts: the condition on top and the value list below. Use either or both — with both, a row has to match both. The condition has its own **Apply** and **Reset**; the value list has no buttons of its own — remove one tag to drop a single pick, or clear them all at once with the list's own **×**.

### Which Columns Have a Value List

| Value list | Conditions only |
|---|---|
| Text, whole numbers, dates, yes/no, choices, foreign keys | Long text, decimal numbers, date and time, JSON, files, many-to-many, the ID column |

A few columns keep their conditions even though their type would get a list: a column the [[model-your-data/serializers#Value lists|serializer]] computes or reformats, for example. And a developer can turn value lists off for a whole table — the audit log has none.

### Saved Views Keep Working

Views saved before value lists existed still open as they did. When the view loads, its filters are converted to the column's new filter: a foreign-key or yes/no filter becomes a value list with that one value picked, and a text, number or date condition carries on as the column's condition. A saved filter that can't be shown that way is dropped rather than half-applied.

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

This is especially powerful with [[using-the-app/the-grid/saved views|saved views]]: set up a complex filter combination once, save it, and switch to it instantly whenever you need it.

> [!example]- 🎬 Video — Building a multi-filter view
> <video controls width="100%">
>   <source src="../../videos/grid-multi-filter.mp4" type="video/mp4">
> </video>
> Build up a view step by step: apply a date range, then category filter, then sort by amount.
