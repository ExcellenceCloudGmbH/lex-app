---
title: Timeline Tab
aliases:
  - "interface/record-detail/timeline tab"
---

Data doesn't exist in a vacuum — it has a story. The Timeline tab tells that story visually. Every version of a record, from the moment it was created to its latest update, is plotted on a time axis that you can explore, inspect, and compare.

## The Visual Timeline

A summary line at the top shows:

- **Status** — *Active* while one version is still open-ended, *No Active Version* when none is
- **Versions** — how many versions exist
- **Last Change** — the date of the latest change and who made it

The refresh button at the end of that line reloads the timeline.

Below it, **Valid Time Visualization** draws each version as a bar along a time axis, from
the moment it became valid to the moment the next version replaced it — how long each one
was the current one. The legend underneath names the colours: **Created**, **Updated**
and **Deleted**. The current version's bar carries a small pulsing dot, and a dashed line
marks *Now*.

- **Point at a bar** for the version's name and its *From* and *To* times. The current
  version's *To* reads *Current (Open-ended)*.
- **Zoom** with the magnifier buttons, or hold Ctrl (⌘ on a Mac) and scroll over the
  chart. The percentage shows the zoom level; the third button fits the whole timeline
  back on screen.
- **Page through** a long history with the arrows next to the zoom controls: the chart
  shows up to 80 versions at a time.

![The Timeline tab, showing each version as a bar on a time axis](images/record-detail/timeline-tab.svg)

## Time-Travel with As Of

This is where Lex App's [[history-and-audit/bitemporal history|bitemporal model]] comes
to life. The bars show *valid time* — when each version was true. **As Of** adds the
second dimension, *system time*: what the system knew at a moment in the past.

Click **As Of: Latest**, pick a date and time under **Select Date & Time**, and the chart
redraws the versions as they were recorded at that moment. A backdated correction made
later is not there yet; a version that was later moved to a different date shows at
the date it had then. The chip on the right turns from *Live* to *Historical View*, the
dashed line marks *As Of* instead of *Now*, **Visible Versions** counts what was known,
and the legend adds *System Time Context*. If nothing was recorded yet at that moment, the
chart says *No Versions Found*.

**Reset to Latest**, in the same picker, goes back to the live timeline.

This distinction matters in industries where backdated corrections are common — finance,
insurance, regulatory reporting. The bars show "what was actually true"; As Of shows
"what we had recorded" at any moment.

> [!tip]
> To see the whole table as it was on a date, use **As of date…** in the table's toolbar
> instead — see [[using-the-app/the-grid/index#Current, As of and Full history|Current, As of and Full history]].
> That one shows valid time: the records as they were true on the date you pick.

## Inspecting a Version

Click any bar to open the version's details in a panel on the right. You'll see:

- **Valid From** and **Valid To** — when this version was in force (*Present* for the
  current one)
- **Action** — *Created*, *Updated* or *Deleted*
- **Changed By** — who made the change, or *System*
- **Reason** — when the change was saved with one
- **Payload Data** — every field of the record in that version. Switch **Raw** to **Diff**
  to see only the fields that differ from the version before (the switch is greyed out on
  the first version). **Copy JSON** copies the whole version.

The panel shows the version; it has no way to change its dates or values. By default only
a superuser also sees a **Delete** button there, which removes the version from the
history; for everyone else history is read-only. A superuser changes a version's dates or
values in the [[using-the-app/record-detail/history tab|History tab]]'s grid instead.

This is invaluable for investigations: "Why does this number look wrong? When did it change? Who changed it?"

> [!tip]
> For the operations behind each change — who asked for it, whether it succeeded, and
> what was sent — switch to the [[using-the-app/record-detail/audit log tab|Audit Log tab]].
