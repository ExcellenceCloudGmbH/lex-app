---
title: "Part 6 — History in Action"
aliases:
  - "tutorial/Part 6 — History in Action"
---

Your app already has full [[history-and-audit/bitemporal history|bitemporal history]] — you didn't write a single line for it. Powered by [django-simple-history](https://django-simple-history.readthedocs.io/) under the hood, the framework records every change with two time dimensions: *when it happened* (valid time) and *when we learned about it* (system time). In this final part, you'll see it in action.

## Scenario: Correcting an Expense After Quarter Close

> It's February 5th. Anna from the Design team notices that her "Flight to Munich" expense was entered as **€450**, but the actual receipt shows **€380**. The quarter has already closed, but the correction needs to be recorded properly.

### Edit the Expense

1. Navigate to **Expense** in the frontend
2. Find Anna's "Flight to Munich" row
3. Double-click its **Amount** cell, type **380** and press Enter — or click the pencil
   (**Edit**) in the row, change the amount in the panel that opens, and click **Save**

> [!note]
> If the Expense grid is empty, Part 4's `permission_read` is hiding the rows
> from your login. Put yourself in the `cfo` group as shown in
> [[start-here/tutorial/Part 4 — Validation & Permissions#Try It Yourself|Part 4]].

### Open the Record's History

Rows have no history button: a row's **Actions** column holds the eye icon (**Show**),
the pencil (**Edit**) and the bin (**Delete**). History lives on the record's own page:

1. Hold ⌘ (Mac) or Ctrl (Windows) and click the eye icon in Anna's row — or click it, and
   then **Open full view** in the panel that opens
2. Choose the **History** tab

![The History tab on a record: one row per version, newest first](images/record-detail/history-tab.svg)

Each save added a version. Newest first, the expense's two versions read like this —
with the story's dates; on your screen they are the moments you loaded the sample data
and saved your edit:

| Version | Amount | Valid From | Valid To |
|---|---|---|---|
| v2 *(current)* | €380.00 | Feb 5, 10:15 AM | — |
| v1 *(superseded)* | €450.00 | Jan 20, 2:30 PM | Feb 5, 10:15 AM |

The **Timeline** tab draws the same two versions as bars on a time axis. Click one to see
who changed it and, with **Diff**, what changed.

> [!tip]
> Both values are preserved. The original €450 entry is never deleted — it's superseded. This is what auditors need: a complete trail. Even the correction you're about to make to *when* a value applied is recorded, in a system-time history whose rows no one can edit (a superuser can only delete them, through the REST API).

## Backdating the Correction

Now suppose Anna realizes the correct amount (€380) was actually valid **since January 20** (when the flight happened), not since today. She needs to backdate the correction.

Anna can't do that in the interface. The Timeline tab shows each version's **Valid From** but has
no way to change it, and only a superuser may change a history row, which they do in the
History tab's grid. A backdated
correction is made in code instead: set `_history_date` on the record before you save
it, and the version that save creates is valid from that moment rather than from now.

### Backdate It in Code

Run `lex shell` and:

```python
from Input.Expense import Expense

expense = Expense.objects.get(description="Flight to Munich")
first = expense.history.earliest()  # the version from when the expense was first recorded

expense.amount = 380
expense._history_date = first.valid_from  # valid from then, not from now
expense._history_change_reason = "Receipt shows €380"
expense.save()
```

`_history_date` sets the new version's **Valid From**, and `_history_change_reason` is the
**Reason** the Timeline tab shows for it. In the story, `first.valid_from` is January 20,
2:30 PM. To pick a moment yourself, assign a timezone-aware datetime instead — for
example `timezone.make_aware(datetime(2026, 1, 20, 14, 30))`, after
`from datetime import datetime` and `from django.utils import timezone`.

Reload the record's page. The system recalculates the timeline:

**Before:**

```
Jan 20                              Feb 5
├── v1: €450.00 ────────────────────┤── v2: €380.00 ──────────►
```

**After backdating:**

```
Jan 20                              Feb 5
├── v3: €380.00 ────────────────────┤── v2: €380.00 ──────────►
```

v3 is the backdated correction. It starts at the same moment as v1, so v1 now ends the
instant it began: €450 was never in force.

The v1 entry still exists in the **system history** (Level 2) — you can always answer: "On February 4th, what did the system think this expense was?" Answer: **€450**, because the correction hadn't been entered yet. To see it, open the **Timeline** tab, click **As Of: Latest** and pick a moment before your edit: the chart shows the versions as the system knew them then.

## What the Two Levels Show

### Level 1 — Valid Time ("What was true?")

After the backdating, valid time shows:

| Version | Amount | Valid From | Valid To |
|---|---|---|---|
| v1 | €450.00 | Jan 20, 2:30 PM | Jan 20, 2:30 PM — never in force |
| v3 | €380.00 | Jan 20, 2:30 PM | Feb 5, 10:15 AM |
| v2 | €380.00 | Feb 5, 10:15 AM | *current* |

This is the business reality: the flight always cost €380.

### Level 2 — System Time ("When did we know?")

System time tells the complete knowledge story — here with the backdating done a few
minutes after the edit, at 10:20 AM:

| What the system knew | Sys From | Sys To |
|---|---|---|
| "It's €450" | Jan 20, 2:30 PM | Feb 5, 10:15 AM |
| "It was €450, and €380 from Feb 5" | Feb 5, 10:15 AM | Feb 5, 10:20 AM |
| "It was €380 all along" | Feb 5, 10:20 AM | *current* |

> [!important]
> You never lose information. Even after the backdating correction, both the original belief AND the correction are preserved in the system history.

## Recalculate After the Correction

After correcting the expense:

1. Navigate to **Reports → BudgetSummary**, to the Design team's Q1 2026 row
2. Click ▶ in that row

The calculation will reflect the corrected amount (€380 instead of €450). Click the log icon beside the status pill to see the updated numbers. Earlier runs keep their logs: the record's **Audit Log** tab lists each run with its **Calculation Log** button.

## You're Done!

Congratulations! You've built a complete business application following the ETL pattern:

| Layer | What You Built |
|---|---|
| **Extract** (`Upload/`) | Three upload models for CSV ingestion |
| **Transform** (`Input/`) | Three input models with validation and permissions |
| **Load** (`Reports/`) | Budget calculations with rich logging and dashboards |

And here's how much code that took:

| Feature | What You Wrote |
|---|---|
| **3 input models** | Three files in `Input/` |
| **3 upload models** | Three files in `Upload/` |
| **1 serializer** | One `serializers.py` for API validation |
| **Automatic CRUD + API** | Zero lines — powered by [Django REST Framework](https://www.django-rest-framework.org/) |
| **Budget calculations** | One `calculate()` method |
| **Rich logging** | A few `LexLogger` calls |
| **Validation** | One `pre_validation()` method |
| **Role-based permissions** | Two `permission_*()` methods |
| **Interactive dashboards** | Two [Streamlit](https://docs.streamlit.io/) methods |
| **Full bitemporal history** | Zero lines — powered by [django-simple-history](https://django-simple-history.readthedocs.io/) |
| **Frontend data grid** | Zero lines — powered by [AG Grid](https://www.ag-grid.com/) |
| **History and timeline views** | Zero lines — built into the frontend |

**Total code: ~400 lines** (including whitespace, docstrings, and imports). Everything else — the web UI, API, authentication, history, real-time updates — is provided by the framework. Lex App is [open source](https://github.com/ExcellenceCloudGmbH/lex-app) — we recommend reading the source for deeper understanding.

## What's Next?

Now that you've completed the tutorial, explore the rest of the documentation:

- [[home|All building blocks]] — everything Lex App gives you out of the box
- [[calculations/calculation models|Calculations]] — deep-dive into the state machine and [Celery](https://docs.celeryq.dev/) support
- [[history-and-audit/bitemporal history|Bitemporal History]] — understand the two-level architecture
- [[access-and-dashboards/streamlit/index|Streamlit Dashboards]] — build more complex visualizations
- [[model-your-data/serializers|Serializers]] — advanced API validation and multiple views
- [[reference/CLI Commands|CLI Commands]] — every `lex` command at a glance

> [!tip]
> Lex App is [open source](https://github.com/ExcellenceCloudGmbH/lex-app). We recommend browsing the source code for deeper understanding of how the framework works under the hood.
