---
title: Summary Tab
---

The Summary tab is the first thing you see when opening a record. Every field is displayed as a labeled card — clean, readable, and organized by the layout you choose.

## Field Cards

Each field gets its own card with a label and value. Fields are rendered intelligently based on their type:

| Field Type | How It's Displayed |
|---|---|
| **Text** | Plain text with line wrapping |
| **Number** | Formatted with locale-appropriate separators |
| **Date / DateTime** | Human-readable format (e.g., "Mar 4, 2026, 12:30 PM") |
| **Boolean** | Visual indicator (✓ / ✗) |
| **Foreign Key** | Linked name of the related record, clickable |
| **JSON** | Formatted, expandable view |

> [!example]- 📸 Screenshot — Summary tab with field cards
> ![Summary tab showing field cards in a 2-column layout](../images/record-detail/summary-tab.jpeg)

## Column Layout

Not all records have the same number of fields. Use the **column toggle** in the toolbar to switch between:

- **1 column** — full-width cards, one per row; best for records with few fields or long text values
- **2 columns** — balanced layout; the default, works well for most models
- **3 columns** — compact layout; best for models with many short fields (IDs, dates, amounts)

> [!example]- 🎬 Video — Column layout toggle
> <video controls width="100%">
>   <source src="../../videos/record-column-toggle.mp4" type="video/mp4">
> </video>
> Click the column toggle to switch between 1, 2, and 3 columns, see the layout reflow.

The column setting is preserved in the URL, so you can share a link to a record with a specific layout — your colleague sees the same thing you do.

## Serializer Presets

The **View Preset** dropdown in the toolbar lets you switch which fields are visible. Each serializer preset defines a different set of fields — think of them as different lenses on the same record.

For example:
- **Default** — all fields
- **Detail** — key fields with additional computed properties
- **Summary** — just the essentials (name, status, amount)

Your developer defines these presets via [[features/data-pipeline/serializers|serializers]]. As a user, you just pick the view that fits your task.


## Export to PDF

Click the **PDF** button in the toolbar to generate a PDF of the Summary tab. The export captures all visible field cards in their current layout — ready for filing, emailing, or printing.

This is especially useful for:
- Compliance records that need to be archived
- Sharing a single record with someone who doesn't have access to the application
- Including in audit documentation

> [!tip]
> The PDF export uses the currently selected serializer preset, so switch to the view you want before exporting.

## Editing

Click the **Edit** button in the toolbar to switch to the edit form. All editable fields become input fields — text boxes, dropdowns, date pickers — pre-populated with the current values. Fields you don't have [[features/access-and-ui/permissions|permission]] to modify appear as read-only.

After saving, you return to the Summary tab with the updated values reflected immediately.
