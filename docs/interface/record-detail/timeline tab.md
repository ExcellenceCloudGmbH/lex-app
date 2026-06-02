---
title: Timeline Tab
---

Data doesn't exist in a vacuum — it has a story. The Timeline tab tells that story visually. Every change to a record, from the moment it was created to its latest update, is plotted on a chronological timeline that you can explore, inspect, and compare.

## The Visual Timeline

The timeline displays a horizontal bar visualization of all versions of the record — plotted along a time axis. Each version is represented as a colored bar showing its validity period:

- 🟢 **Green bars** — the record was first created
- 🔵 **Blue bars** — one or more fields were modified
- 🔴 **Red markers** — a record was deleted

Above the visualization, a summary strip shows:
- **Current Status** (Active / Deleted)
- **Total Versions** (how many snapshots exist)
- **Last Change** (date and author)

The **As Of** control and **Visible Versions** badge let you adjust which versions are displayed. Use the zoom controls to focus on a specific time range.

> [!example]- 📸 Screenshot — Timeline with version bars
> ![Timeline tab showing Valid Time Visualization with version bars](../images/record-detail/timeline-tab.jpeg)

## Two Time Dimensions

This is where Lex App's [[features/tracking/bitemporal history|bitemporal model]] comes to life. The timeline can show changes along two independent dimensions:

### System Time (When We Learned It)

This is the default view. It shows events in the order the system recorded them — the sequence of database writes. This is what happened from the system's perspective.

### Valid Time (When It Was True)

Switch to the **Effective Time** view to see when data was *actually valid* in the real world. A salary change recorded today but effective from January 1st would appear at January 1st in this view.

This distinction matters in industries where backdated corrections are common — finance, insurance, regulatory reporting. The timeline shows both "what we recorded" and "what was actually true."

> [!tip]
> To see the As-Of time-travel in action on a familiar grid view, check the [[interface/record-detail/history tab|History tab]] — it uses the same control over a full AG Grid table.

## Inspecting a Version

Click any event on the timeline to open the **Version Details Drawer** — a slide-out panel showing the full state of the record at that point in time. You'll see:

- All field values at that moment
- Which specific fields changed compared to the previous version
- The author who made the change
- The exact system-time and valid-time timestamps


This is invaluable for investigations: "Why does this number look wrong? When did it change? Who changed it?"

## Audit Feed

Below the timeline, the **Audit Feed** shows a chronological list of API operations that affected this record — create, update, delete events captured by the [[features/tracking/audit logs|audit log system]]. Each entry shows the author, timestamp, operation type, and status.

The timeline and audit feed are complementary: the timeline shows *what the data looked like*, the audit feed shows *what operations were performed*.

> [!tip]
> For full audit log details with payloads and error tracebacks, switch to the dedicated [[interface/record-detail/audit log tab|Audit Log tab]].
