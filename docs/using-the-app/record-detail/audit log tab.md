---
title: Audit Log Tab
aliases:
  - "interface/record-detail/audit log tab"
---

The Timeline and History tabs show *what changed*. The Audit Log tab shows *who asked for the change*, *whether it succeeded*, and *exactly what was sent*.

This is the compliance and security layer — designed for auditors, security teams, and anyone who needs to answer the question: "Can you prove that this change was authorized and executed correctly?"

## What You See

The Audit Log tab displays a grid of all API operations that affected the current record. Each row represents a single operation:

| Column | What It Shows |
|---|---|
| **Id** | Unique audit log entry identifier |
| **Date** | Exact timestamp of the operation |
| **Author** | The user who performed the action |
| **Resource** | The model name (e.g., `budgetsummary`) |
| **Action** | `create`, `update`, or `delete` |
| **Payload** | The full data that was sent or modified (expandable JSON) |
| **Calculation Log** | A button that opens the log of that calculation run — an icon with that tooltip in compact rows. Greyed out when the run left no log. See [[using-the-app/the-grid/calculations#The Log Page\|The Log Page]] |
| **Calculation Record** | The related record (e.g., "Marketing — 1") |
| **Status** | Where the operation got to: `pending`, `success`, `failure`, `cancelled` or `aborted` — see [[#The Status Lifecycle]] |
| **Error Traceback** | Why the operation failed — filled only when the status is `failure` |
| **Status Created At** / **Status Updated At** / **Duration** | When the status was first written and last changed, and how long the operation took |
| **Actions** | Quick view link to inspect the full entry |

![Every attempted operation, not just the ones that succeeded — the entry is written before the operation runs, so a delete that failed is still on record](images/record-detail/audit-log-tab.svg)

## The Status Lifecycle

Every audit log entry goes through a status lifecycle. The **Status** column shows it as
the lower-case value the server stores:

```mermaid
flowchart LR
    A["pending"] -->|operation succeeds| B["success"]
    A -->|operation fails| C["failure"]
    A -->|someone stops the calculation| D["cancelled"]
    A -->|server restarts mid-calculation| E["aborted"]
```

- **`pending`** — the operation has been requested but not yet completed (for HTTP `202` calculations, this can remain until background work finishes)
- **`success`** — the operation completed successfully; payload updated to reflect final state
- **`failure`** — the operation failed; the error traceback is stored for investigation
- **`cancelled`** — a calculation was stopped by a person before it finished
- **`aborted`** — a calculation was still running when the server restarted, so it never
  finished

Only `failure` keeps a traceback: a cancelled or aborted run raised no error.

The key insight: the audit log is created **before** the operation executes. This means even operations that fail due to validation errors, database issues, or permission denials are recorded with full context.

## Reading the Payload

The **Payload** cell holds the full JSON body of the operation, folded; click its arrow to expand it. For updates, this shows the values that were sent:

```json
{
  "id": 42,
  "amount": 1500.00,
  "category": "travel",
  "description": "Conference registration",
  "approved": true
}
```

For deletions, the payload captures the record's state at the moment of deletion — so you can always inspect what was removed.

## Filtering and Sorting

The Audit Log tab uses the same [[using-the-app/the-grid/index|AG Grid]] as everything else, so you can:

- **Filter by author** — see all changes made by a specific user
- **Filter by action** — show only deletions, or only creations
- **Spot failed operations** — their **Status** reads `failure`
- **Sort by date** — latest first, or trace from the beginning

> [!warning]
> In Lex App 2.5.0 the **Export** button above this grid downloads rows from the record's
> own table, not these log entries.

## Relationship to Other Tabs

| Question | Answer With |
|---|---|
| "What did the data look like before this change?" | [[using-the-app/record-detail/history tab\|History Tab]] — find the version before this audit entry's timestamp |
| "Who requested this change and was it successful?" | **Audit Log Tab** — this tab |
| "How did this field change over time?" | [[using-the-app/record-detail/timeline tab\|Timeline Tab]] — visual diff across versions |
| "What charts or KPIs are affected by this change?" | [[using-the-app/record-detail/analytics tab\|Analytics Tab]] — live dashboards |

Together, these tabs give you complete observability: from the operational "who and when" to the historical "what and how."

> [!note]
> For developers: the audit log is powered by `AuditLogMixin` which hooks into the Django REST Framework view layer. See [[history-and-audit/audit logs]] for the backend implementation.
