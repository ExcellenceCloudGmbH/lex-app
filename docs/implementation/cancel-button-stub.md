# Cancel-Calculation Button — Frontend Stub

> **Status:** Internal-only design stub. `docs/features/` is mirror-owned from
> `lex-app-docs`, so the customer-facing reference doc for this feature ships
> from upstream — add a "Cancelling a calculation" section to
> `docs/features/processing/calculations.md` in `lex-app-docs` once the React
> component below ships in `process-admin-general-client`.
>
> **Backend contract:** see
> [`lex/api/views/model_entries/One.py`](../../lex/api/views/model_entries/One.py)
> (PATCH short-circuit) and
> [`lex/core/models/CalculationModel.py`](../../lex/core/models/CalculationModel.py)
> (`CalculationModel.cancel()`).

## What the user sees

Anywhere a long-running calculation can be triggered (the calculate button on
a `CalculationModel` record), an **Abort** button appears next to it while
`is_calculated === "IN_PROGRESS"`. Pressing it sends a cancel request to the
backend. On success the row immediately transitions to `ABORTED` and the
spinner stops; on a refusal a toast surfaces a precise reason
("Calculation is no longer running" / "This calculation cannot be cancelled —
it is running synchronously").

## HTTP contract

```
PATCH /api/<model>/{calculation_id}/{pk}/
Content-Type: application/json

{ "cancel": "true" }
```

| Status | Body shape | When |
| --- | --- | --- |
| `202 Accepted` | `{ "cancelled": true, "cancellable": true, "status": "ABORTED", "revoked_tasks": ["<task_id>", ...], "descendants_cancelled": <int> }` | Row was IN_PROGRESS and had a registered Celery `task_id`. The task is revoked with SIGTERM; the row + every descendant sharing the same `calculation_id` persist `ABORTED`. |
| `409 Conflict` | `{ "cancelled": false, "cancellable": false, "status": "<current>", "reason": "not_in_progress" }` | Row already terminated (SUCCESS / ERROR / ABORTED). The cancel is a clean no-op. |
| `409 Conflict` | `{ "cancelled": false, "cancellable": false, "status": "IN_PROGRESS", "reason": "sync_calculation_not_cancellable" }` | Row is IN_PROGRESS but was dispatched synchronously — no Celery task to revoke. The framework's design is **Celery-only** cancel; show the user "this calculation cannot be cancelled". |

The cancel short-circuit returns **before** any other field on the PATCH body is
applied — so sending `{"cancel": "true", "name": "x"}` will not write `name`.
The button does not need to clear sibling fields defensively.

## React stub — wire to React Admin record context

Drop this into `process-admin-general-client/src/components/model-components/`
alongside `CalculateFunctionality.tsx`. It uses React Admin's `useRecordContext`
+ `useDataProvider` so it picks up the model + record automatically wherever
it's mounted next to the calculate button.

```tsx
// AbortCalculationButton.tsx
import { Button, useNotify, useRecordContext, useDataProvider, useRefresh } from 'react-admin';
import { useState } from 'react';

type CancelReport = {
  cancelled: boolean;
  cancellable: boolean;
  status: string;
  reason?: 'not_in_progress' | 'sync_calculation_not_cancellable';
  revoked_tasks?: string[];
  descendants_cancelled?: number;
};

export function AbortCalculationButton({ resource }: { resource: string }) {
  const record = useRecordContext();
  const dataProvider = useDataProvider();
  const notify = useNotify();
  const refresh = useRefresh();
  const [busy, setBusy] = useState(false);

  // Only render while the calculation is actually running.
  if (!record || record.is_calculated !== 'IN_PROGRESS') return null;

  const onClick = async () => {
    setBusy(true);
    try {
      // React Admin's update() will PATCH /<resource>/<id>/ with the data.
      // The backend short-circuits on data.cancel === "true".
      const { data } = await dataProvider.update<CancelReport>(resource, {
        id: record.id,
        data: { cancel: 'true' },
        previousData: record,
      });
      if (data.cancelled) {
        notify(
          `Cancelled — ${1 + (data.descendants_cancelled ?? 0)} task(s) stopped`,
          { type: 'success' }
        );
        refresh();
      }
    } catch (err: any) {
      // 409 responses arrive here. The body carries the reason.
      const report: CancelReport | undefined = err?.body ?? err?.response?.data;
      if (report?.reason === 'not_in_progress') {
        notify('Calculation is no longer running', { type: 'info' });
        refresh();
      } else if (report?.reason === 'sync_calculation_not_cancellable') {
        notify(
          'This calculation cannot be cancelled — it is running synchronously',
          { type: 'warning' }
        );
      } else {
        notify(`Cancel failed: ${err?.message ?? 'unknown error'}`, { type: 'error' });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Button
      label="Abort"
      onClick={onClick}
      disabled={busy}
      color="warning"
    />
  );
}
```

Mount it where the calculate button currently lives — for example, inside
`CalculateFunctionality.tsx`'s render — so the two buttons appear side-by-side
and only one of them is visible at a time (Calculate while
`NOT_CALCULATED`/`SUCCESS`/`ERROR`/`ABORTED`; Abort while `IN_PROGRESS`).

## Recursive cancel — what the user should expect

Cancel is **recursive by default**: cancelling a parent calculation also
revokes every active child the parent dispatched. The backend reports the
count as `descendants_cancelled`. The button's success toast surfaces it
("Cancelled — 4 task(s) stopped") so the user knows the whole tree is gone,
not just the one they clicked.

To cancel only the parent without touching children (rare — power-user
case), the backend supports `recursive=false`; expose this only if a real
customer asks. The default UX is "abort the whole thing".

## Follow-ups when this component lands

- [ ] Move this stub into `process-admin-general-client/src/components/model-components/`
      and remove this file once the upstream component is shipped.
- [ ] Open a docs PR in `lex-app-docs` adding the "Cancelling a calculation"
      section to `content/features/processing/calculations.md`. Cross-link it
      from the state-machine diagram so the `IN_PROGRESS → ABORTED` edge is
      documented as user-triggerable, not just startup-cleanup.
- [ ] Update the playbook for sync-dispatched calcs: customers who want
      cancellable calculations must run them via Celery (`CELERY_ACTIVE=true`
      and `@lex_shared_task`-decorated `calculate`). This is a design choice,
      not a limitation — document it as such.

