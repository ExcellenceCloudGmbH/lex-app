---
title: Summary Tab
aliases:
  - "interface/record-detail/summary tab"
---

The Summary tab is the first thing you see when opening a record. Every field is displayed as a plain row — its label in small capitals above the value, a thin line below — in the layout you choose.

## Which Fields, in Which Order

Every field of the [[#Serializer Presets|view preset]] is on the tab, in the order the
preset lists them. Nothing is folded away: `Id`, `Created At`, `Edited At`, `Created By`
and `Edited By` are rows like any other, wherever the preset puts them — who changed a
record and when is often the reason you opened it.

![The Summary tab: every field as a plain row, in the order the view preset gives — here Created At, Edited At, Created By and Edited By come first](images/record-detail/summary-tab.svg)

## Field Rows

Each field gets its own row with a label and value. Fields are rendered intelligently based on their type:

| Field Type | How It's Displayed |
|---|---|
| **Text** | Plain text with line wrapping |
| **Number** | Formatted with locale-appropriate separators |
| **Date / DateTime** | In your browser's date format (e.g., "9/10/2026, 2:27:13 PM") |
| **Boolean** | Visual indicator (✓ / ✗) |
| **Foreign Key** | The related record's name; point at it for a card with **Open record** |
| **JSON** | Formatted, expandable view |

## Column Layout

Not all records have the same number of fields. Use the **column toggle** (1 · 2 · 3) in the toolbar to switch between:

- **1 column** — one field per row, full width; the default, and best for long text values
- **2 columns** — two fields side by side; works well for most models
- **3 columns** — compact layout; best for models with many short fields (IDs, dates, amounts)

The column setting is preserved in the URL, so you can share a link to a record with a specific layout — your colleague sees the same thing you do. On a narrow screen the fields stack in one column whatever you pick.

## Serializer Presets

The **View Preset** dropdown in the toolbar lets you switch which fields are visible. Each serializer preset defines a different set of fields — think of them as different lenses on the same record.

For example:
- **Default** — all fields
- **Detail** — key fields with additional computed properties
- **Summary** — just the essentials (name, status, amount)

Your developer defines these presets via [[model-your-data/serializers|serializers]]. As a user, you just pick the view that fits your task.


## Export to PDF

Click **Export Summary** in the toolbar to save the Summary tab as a PDF, named after the
record (`expense-42.pdf`, for example). It is a snapshot: a picture of the tab as it is on
screen, in its current layout, fitted to one A4 page. The text in it can't be selected or
searched, and what doesn't fit on the page isn't in it — pick more columns for a long
record. The button is there only while the Summary tab is open.

This is especially useful for:
- Compliance records that need to be archived
- Sharing a single record with someone who doesn't have access to the application
- Including in audit documentation

> [!tip]
> The PDF export uses the currently selected serializer preset, so switch to the view you want before exporting.

## Editing

Click the **Edit** button in the toolbar to switch to the edit form. All editable fields become input fields — text boxes, dropdowns, date pickers — pre-populated with the current values. Fields you don't have [[access-and-dashboards/permissions|permission]] to modify, and fields that can never be edited, are not on the form at all.

The full-page form has the same 1 · 2 · 3 column toggle at the top, beside **List** and
**Show**; the create form has it too. **Save** and **Delete** are at the bottom.

After saving, you're taken to the table, and a message says *Record is updated!* If you
changed nothing, the form says *No changes to save.* and you stay where you are.

### File fields

A file field shows the current attachment with controls to work with it:

- **Download** the existing file, or **replace** it by choosing a new one.
- **Remove** it by clearing the field and saving — the attachment is deleted and the field
  is left empty. (Leaving a file field untouched keeps the current file; only an explicit
  remove or replace changes it.)

A PDF field accepts only `.pdf` files, and an Excel field only `.xls` and `.xlsx` files.

### Large files

A file of 32 MiB or more is sent to the server in pieces, one after the other. You
choose and save it the same way as any other file, when you create a record or edit
one; saving just takes longer. Keep the
page open until the save finishes.

If the connection drops for a moment, the app sends the missing piece again by itself.
Files of several hundred megabytes, which used to fail with *Failed to fetch*, now go
through. There is still an upper limit of 5 GiB per file; a bigger file is refused, with a
message giving its size and the limit.
