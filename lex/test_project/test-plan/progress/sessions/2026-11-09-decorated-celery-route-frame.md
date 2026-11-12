---
date: 2026-11-09
clusters: [15]
tests_added: "6 new scenarios (15.72-15.77, batch 15p)"
suite_tally: "calculation_logging 79 pass / 0 fail / 0 xfail (was 73 / 0 / 0); celery_async 149 pass / 7 skip; calculations 218 pass; audit_logging 153 pass / 1 xfail; signals_ws 49 pass (the last four the same as the 15o head); 15f + 15n + 15o + 15p 18 pass, three runs in a row"
---

# Batch 15p — a CalculationModel dispatched to Celery runs in a frame of its own

Follow-up to the fix for BUG-055 (`2026-11-09-sync-route-own-frame.md`), which gave a synchronously
run `CalculationModel` a frame of its own and left the decorated Celery route as a follow-up. Recorded
as [BUG-056](../../known-bugs.md); the batch is [15p](../../clusters/15-calculation_logging/batches.md).

**The bug.** BUG-055's frame was opened only in `calculate_hook`'s synchronous branch. On Celery the
task was shipped a copy of the caller's stack, with the caller on top. An eager-Celery probe on the
15o head found three defects:

- a nested model whose `calculate()` is a `@lex_shared_task` filed its lines in its caller's node,
  because the `lex_shared_task` wrapper runs `calculate()` on the stack it is shipped and pushes
  nothing (the generic `calc_and_save` task pushes the model, which is why 15.71 passed);
- a decorated root saved with nothing on the stack filed its lines in a node that belongs to no
  record;
- on both Celery routes, a nested model whose task failed was taken for the root by `calculate_hook`'s
  failure handling, which runs in the caller's thread and found the caller on top of its stack. It
  purged the run's live log while the root was still running.

**The fix (design chosen by the developer).** `calculate_hook` opens
`CalculationModel.child_calculation_context` before it either dispatches the run or runs it, and
closes it in its `finally`: the 15o design, applied to the Celery branch. The task is shipped a copy
of the stack with the model on top, the stack the docs' manual `model_logging_context` wrap already
produced. A decorated `calculate()` runs on it as it is, the generic task skips the model already on
top, and `CallbackTask`'s root check (`_is_root_calculation`, which reads the shipped stack) gives the
same answers as before. The developer was also offered a push of `args[0]` in the `lex_shared_task`
wrapper, which keeps the messages as they were. It fixes the tree but not the failure handling, which
never sees a frame pushed on the worker; 15.76 and 15.77 fail with it.

**What changes for the generic task.** Its message now carries the model on top of the shipped stack.
The task's `has_on_top` guard (BUG-052) skips the second push, so its tree is unchanged (15.71, 15.75).
A message queued by an older version still carries the caller on top; the generic task pushes the
model as before, and a decorated `calculate()` logs under its caller, as before.

**Checks.** Each scenario was checked to fail with its part of the fix undone (see the 15p batch).
Every cluster in the tally ran from its own `lex*` copy. Eager mode lets a task see the caller's
dispatch scope (`celery-create-args-claims.md`, item 3, in the shared notes): no 15p scenario routes a
decorated `calculate()` through `calc_and_save`, which is where that changes the route.

**Follow-up for `lex-app-docs`** (mirrored read-only here): the Nested Calculations sections of
`calculations/logging.md` and `reference/CalculationModel Internals.md` tell callers to wrap a nested
save in `model_logging_context`. Nesting is now automatic on every route: synchronous, the generic
Celery task and a `@lex_shared_task` `calculate()`. The wrap stays harmless, because a model already
on top is not pushed again.
