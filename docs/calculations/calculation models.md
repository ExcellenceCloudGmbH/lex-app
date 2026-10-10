---
title: Calculations
aliases:
  - "features/processing/calculations"
---

A `CalculationModel` is a model whose records can be "calculated" on demand. When a user clicks **Calculate** in the frontend, the framework transitions the record to `IN_PROGRESS`, kicks off your `calculate()` method, and then moves the record to the right terminal state for the outcome. The record can stay in progress while the work finishes — users don't need to keep the request open themselves. You only write the business logic — everything else is handled for you.

## Defining a Calculation Model

Inherit from `CalculationModel` and implement `calculate()`:

```python title="CalculateNAV.py"
from lex.core.models.CalculationModel import CalculationModel
from django.db import models


class CalculateNAV(CalculationModel):
    quarter = models.ForeignKey('Quarter', on_delete=models.CASCADE)
    nav_value = models.DecimalField(max_digits=19, decimal_places=2, null=True)

    def calculate(self):
        investments = Investment.objects.filter(quarter=self.quarter)
        total = sum(inv.market_value for inv in investments)
        self.nav_value = total
```

That's it. No decorators, no manual `self.save()`, no recursion guards. The framework handles state management, error capture, and saving automatically.

## The State Machine

The `is_calculated` field is a state machine with clear transitions:

```mermaid
stateDiagram-v2
    [*] --> NOT_CALCULATED : Record created
    NOT_CALCULATED --> IN_PROGRESS : User clicks "Calculate" (or the record was just created, with calculate_on_create)
    IN_PROGRESS --> SUCCESS : Completed without errors
    IN_PROGRESS --> ERROR : Exception occurred
    IN_PROGRESS --> CANCELLED : User cancels
    IN_PROGRESS --> ABORTED : Framework recovers stuck run
    ERROR --> IN_PROGRESS : Retry
    SUCCESS --> IN_PROGRESS : Recalculate
    CANCELLED --> IN_PROGRESS : Retry
    ABORTED --> IN_PROGRESS : Retry
```

| State | Meaning |
|---|---|
| `NOT_CALCULATED` | Record exists, no calculation run yet |
| `IN_PROGRESS` | Calculation is currently running |
| `SUCCESS` | Completed without errors |
| `ERROR` | An exception occurred (details stored in `calculation_error_message`) |
| `CANCELLED` | A user stopped a running calculation |
| `ABORTED` | The framework recovered a calculation that got stuck in progress |

If you're using Celery workers, cancelling is immediate: the framework revokes the running worker task and marks the record as `CANCELLED`. `ABORTED` is different — it's used when the framework finds an old `IN_PROGRESS` row that never finished cleanly, for example after a worker or app process died. Those terminal status updates are saved as normal model changes, so they show up in History/Timeline just like in-process runs.

## Closing a Record

Some results must never change once they've been used: a valuation posted to SAP, a period that's been signed off. To stop a record from being calculated again, override `calculation_closed_reason()` and return the reason it's closed:

```python title="CalculateNAV.py"
class CalculateNAV(CalculationModel):
    quarter = models.ForeignKey('Quarter', on_delete=models.CASCADE)
    nav_value = models.DecimalField(max_digits=19, decimal_places=2, null=True)
    sap_posted = models.BooleanField(default=False)

    def calculation_closed_reason(self):
        if self.sap_posted:
            return "Already posted to SAP, so it can't be calculated again."

    def calculate(self):
        ...
```

While the method returns a reason, the record is closed. It isn't calculated again, and its status stays exactly as it is:

- **The Calculate button is greyed out**, and hovering it shows your reason. If a click gets through anyway, from a page opened before the record was closed for instance, it's refused with the same message.
- **Calculations that start other calculations skip it.** Setting a closed record to `IN_PROGRESS` and saving it runs nothing: the record keeps its status, the rest of the save goes through, and the calling calculation's log notes which record was skipped and why.
- **Batch outputs can be closed too.** Give a [[calculations/batch calculations|batch model]] the same method, and its closed rows keep their values when the batch is generated again.
- **Its other fields stay editable.** Closing stops only the calculation. Editing a closed record keeps its status, and so does closing it: setting the field your `calculation_closed_reason()` reads (`sap_posted` above) leaves a calculated record at `SUCCESS`. An edit to an open record still resets it to `NOT_CALCULATED`, so that it's recalculated with the new values.

Return `None` (or nothing) while the record may still be calculated. Returning `True` instead of a reason closes the record with a generic message.

> [!tip] A record that calculates only once
> The method sees the record as it was before the run was asked for, so a record that should only ever be calculated once can close itself on its own status:
>
> ```python
> def calculation_closed_reason(self):
>     if self.is_calculated == self.SUCCESS:
>         return "Already calculated."
> ```

The grid asks every row it shows whether it's closed, so keep the method quick: read fields of the record rather than running queries.

## What You Get Automatically

You don't need to define or manage any of the following — they're inherited from `CalculationModel`:

- **`is_calculated`** — the state field
- **Recursion guard** — prevents re-entrant calculation loops
- **Error capture** — exceptions are caught, the traceback is written to your
  model's `calculation_error_message` (or `error_message`) field **if you declare
  one**, and `is_calculated` becomes `ERROR` either way. `CalculationModel` adds
  only `is_calculated`; without a message field the detail is in the logs alone
- **Auto-save** — the record is saved automatically after `calculate()` returns
- **Non-blocking trigger** — clicking **Calculate** returns the record in `IN_PROGRESS`, then the UI updates again when the run finishes. With [[#Calculating New Records Automatically|calculate_on_create]], creating a record does the same
- **Cancellation handling** — running Celery-backed calculations can be stopped cleanly from the UI/API
- **[[calculations/celery and async calculations|Celery support]]** — dispatch to [Celery](https://docs.celeryq.dev/) workers for parallel execution
- **System-save attribution** — any records you save inside `calculate()` won't have their `edited_by` / `edited_at` stamped with the triggering user; those saves are treated as system-triggered, not direct user edits

> [!tip] Need to generate many records at once?
> If your calculation creates one output per combination (e.g., one liability per award per upload), see [[calculations/batch calculations|Batch Calculations]] — `CalculatedModelMixin` handles the combination generation, deduplication, and parallel dispatch for you.

> [!info]- How the state machine works internally
> `calculate_hook()` carries **two** hooks — `@hook(AFTER_UPDATE)` and
> `@hook(AFTER_CREATE)`, both conditioned on `is_calculated == IN_PROGRESS`. The
> create hook is why a record that is *born* in `IN_PROGRESS` calculates too,
> rather than only one edited into it. It calculates inside the save that
> created it, so a create request waits for the whole run; to start new
> records' runs in the background, use
> [[#Calculating New Records Automatically|calculate_on_create]].
>
> `IN_PROGRESS` is already committed before the hook runs — `save()` persists it
> in its own transaction and then calls the hook outside that transaction, so
> the state is visible to other readers while the work happens. The hook then:
>
> 1. Returns immediately if `save()` deferred it — that deferral is what stops a save made inside `calculate()` from re-entering the hook
> 2. Broadcasts the status over the websocket
> 3. Decides whether to hand the work to Celery or run it in-process (`should_use_celery()`)
> 4. Calls your `calculate()` method
> 5. On success: sets `is_calculated = SUCCESS` and saves
> 6. On cancellation: sets `is_calculated = CANCELLED`
> 7. On other exceptions: sets `is_calculated = ERROR`, writes the traceback to `calculation_error_message` if your model has one, and saves
>
> You never need to manage this yourself.

## Calculating New Records Automatically

To have new records calculate without anyone clicking **Calculate**, set
`calculate_on_create`:

```python title="CalculateNAV.py"
class CalculateNAV(CalculationModel):
    calculate_on_create = True

    quarter = models.ForeignKey('Quarter', on_delete=models.CASCADE)
    ...
```

A record created through the app (the create form, the REST API or an embedded
form) then starts its calculation as soon as it exists, the same way a click on
**Calculate** does:

- The create answers straight away, with the record already `IN_PROGRESS`. The
  form doesn't wait for the run.
- The run goes to Celery when it's on, and the status pill and the log update
  as they do after a click.
- The audit log shows two entries: the create, and the run, as an update with
  its own calculation id. If the run fails, the record stays created, in
  `ERROR`, and the create's entry is still a success.

Records created in code (scripts, [[model-your-data/initial data|initial data]],
uploads, another calculation's `calculate()`) don't start a run. Code that
creates records decides for itself when they calculate: a calculation that needs
a new record's result can create it already `IN_PROGRESS`, which calculates it
on the spot, before the code carries on.

To decide per record, make it a property:

```python
    @property
    def calculate_on_create(self):
        return self.quarter.is_open
```

> [!warning] Don't start the calculation from an `AFTER_CREATE` hook
> A hook that sets `is_calculated = IN_PROGRESS` runs the calculation inside the
> request that created the record, so the create form waits for the whole run.
> `calculate_on_create` starts the same run in the background instead.

> [!note] Needs a lex-app release newer than 2.3.4.

## Another Example

```python title="CalculateBalanceSheet.py"
class CalculateBalanceSheet(CalculationModel):
    quarter = models.ForeignKey('Quarter', on_delete=models.CASCADE)
    total_assets = models.DecimalField(max_digits=19, decimal_places=2, null=True)

    def calculate(self):
        assets = Asset.objects.filter(quarter=self.quarter)
        self.total_assets = assets.aggregate(Sum('value'))['value__sum']
```

> [!note]- Migrating from V1?
> If you're coming from `ConditionalUpdateMixin`, here's what changes:
>
> | Aspect | V1 (Old) | Current |
> |---|---|---|
> | Base class | `ConditionalUpdateMixin` | `CalculationModel` |
> | Method name | `update()` | `calculate()` |
> | Decorator | `@ConditionalUpdateMixin.conditional_calculation` | Not needed |
> | State field | Boolean `is_calculated` | Enum with 6 states |
> | Recursion guard | Manual `dont_update` flag | Automatic |
> | Error handling | Manual `try/catch` | Automatic (stored in `calculation_error_message`) |
> | Save | Manual `self.save()` | Automatic after method returns |
>
> ### Migration Checklist
>
> - [ ] Change base class: `ConditionalUpdateMixin` → `CalculationModel`
> - [ ] Remove the `@conditional_calculation` decorator
> - [ ] Rename method: `update()` → `calculate()`
> - [ ] Remove `is_calculated = IsCalculatedField(...)` (inherited automatically)
> - [ ] Remove `calculate = CalculateField(...)` (inherited automatically)
> - [ ] Remove the `dont_update` recursion guard
> - [ ] Remove manual `self.save()` calls
