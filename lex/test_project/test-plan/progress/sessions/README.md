# Session Fragments

> **What this is:** the chronological narrative of test-plan work — one file per
> session/PR (the old `session-log.md` table, exploded; see the restructure spec).
>
> **To add a session:** create `YYYY-MM-DD-<short-slug>.md` (slug = batch id or
> branch name — never a counter). Front-matter: `date`, `clusters`, `tests_added`,
> `suite_tally`. Body: short prose leading with the batch touched — link the
> batch in `../../clusters/NN-<slug>/batches.md` rather than restating it.
> Adding a file never conflicts with another PR — that is the point.

> **Migration note (2026-07-07):** fragments migrated from the retired
> `session-log.md` are named `YYYY-MM-DD-sNNN.md` — their original session
> number, since no per-row batch slug was reliably recoverable. The
> "never a counter" rule applies to NEW fragments only.
