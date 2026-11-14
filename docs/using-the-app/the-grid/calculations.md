---
title: Running Calculations
---

A table whose records calculate — a [[calculations/calculation models|calculation model]] —
has two extra things in the grid: a **Calculation** column with each record's status, and
a ▶ button in each row's **Actions** column. This page covers starting a run, following
it, and reading its log.

## Running a Calculation

Click ▶ in a row's **Actions** column, left of the eye icon. Its tooltip reads *Start
your custom calculation*. Each row runs its own record; there is no button that runs
several rows at once.

The button is greyed out while a run of that record is going, and in these cases its
tooltip says why:

- **The record can't be calculated again.** The tooltip gives the reason your
  application sets, for example a period that has been signed off.
- **You may not calculate it:** *You do not have permission to calculate this record*.
- **You are looking at past versions:** *Calculation is unavailable in history view* —
  see [[using-the-app/the-grid/index#Current, As of and Full history|Current, As of and Full history]].

There is no control in the interface to stop a run once it has started.

## The Status Pill

The **Calculation** column shows one pill per record:

| Pill | What it means |
|---|---|
| `NOT_CALCULATED` | Not run yet, or edited since the last run: an edit in the app usually resets the status, so that the result is calculated again with the new values |
| `IN_PROGRESS` | Running now. ▶ stays greyed out until the run ends |
| `SUCCESS` | The last run finished |
| `ERROR` | The last run raised an error |
| `ABORTED` | The last run was cut off: the server restarted while it was running |
| `CANCELLED` | The last run was stopped by a person |

The column stays at the left edge while you scroll sideways, unless you turn off **Pin
calculation status** in [[using-the-app/the-grid/table settings|Table Settings]].

## When a Run Starts and Ends

Whenever a calculation on this instance starts or ends — yours or a colleague's — a
short message names the record:

- *… calculation is in progress!*
- *… calculation is completed!*
- *… calculation failed!* — with a **Show Traceback** button when the failure came with
  one
- *… calculation was aborted (server restarted).*
- *… calculation was cancelled.*

The grid refreshes by itself when a run ends, so the pill and the calculated values are
current without a reload.

If the request to start a run is refused — the record failed validation, say — the
message says why, and the record stays as it was.

## Opening the Log

Beside the pill sits the log icon (a page). Its tooltip reads *See the calculation log*,
or *See why this calculation failed* when the last run ended in `ERROR`; then the icon is
red. It opens the log of the record's last run in a popup, without leaving the table.

The icon is hidden on a record that has never run and has no log, on `ABORTED` and
`CANCELLED` rows, and in Full history.

> [!note] The log does not open by itself
> Starting a run opens nothing. Click the log icon to watch the run or to read its log
> afterwards.

## The Log Popup

The popup is titled **Calculation log**, with the model and the record's number beside
it. **Expand to full screen** makes it fill the window, and the popup remembers that
choice the next time; **Exit full screen** turns it back. On a phone-sized screen it is
always full screen.

What it shows depends on the run:

- **While the run is going**, a **Live log** that fills in as the calculation writes it.
  The download icon beside it (*Download the full log as PDF*) saves what there is so
  far.
- **When the run has finished**, the log itself: the execution tree and the consolidated
  log described [[#The Log Page|below]].
- **When the run failed and the failure was recorded**, two tabs. **Traceback** opens
  first, with the exception and its stack trace, a search box and *Copy the whole
  traceback*. **Log** shows what the run wrote before it failed.
- **When the run failed and nothing was recorded**, a red heading: *This calculation
  failed* — *No failure details are available for this run.*

If a run you are watching is cut off or stopped, the popup says so: *The worker stopped
before this calculation finished* (*Nothing raised an error, so there is no traceback.*),
or *Stopped by a person*. If the run wrote no log, the popup says that instead, for
example *This run left no log before it failed.*

The popup stays open when the run ends and switches from the live view to the finished
log. It closes when you go to another page.

## The Log Page

The same log also has a page of its own. Open it from a **Calculation Log** button — the
audit log has one for each calculation entry, in the **Calculation Log** column (an
icon with that tooltip in compact rows). The button is greyed out when the run left no
log. The page carries a **Back** arrow and the run's ID.

On the page and in the popup, the log has two parts:

- **Execution tree**, on the left. Every step of the run, the run as a whole at the top.
  Click a step to jump to its part of the log.
- **Consolidated log**, on the right. The whole log as one document, section by section.
  Each section shows its time and, when it has any, a count of *errors* and *warnings*.
  The arrow beside a section folds it; folding a heading hides the sections under it
  and says how many.

Above the consolidated log:

| Control | What it does |
|---|---|
| **Download PDF** (the download icon) | One PDF of *This step*, *This step and everything under it*, or the *Whole calculation*. See [[calculations/logging#Downloading the log as PDF\|Downloading the log as PDF]] |
| **Copy full log** | Copies the whole log as text, section by section. The tooltip turns to *Copied!* |
| **Log** / **Details** | **Log** is the consolidated log. **Details** shows the fields of the step selected in the tree; it opens on the run as a whole |

If the log can't be loaded, the page says *This log could not be loaded.* with a
**Retry** button.

> [!tip]
> Earlier runs keep their logs. A record's
> [[using-the-app/record-detail/audit log tab|Audit Log tab]] lists every run, each with
> its **Calculation Log** button.

For developers: what goes into the log is written with
[[calculations/logging|LexLogger]].
