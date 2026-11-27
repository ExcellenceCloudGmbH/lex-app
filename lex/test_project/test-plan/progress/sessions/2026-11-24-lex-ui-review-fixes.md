---
date: 2026-11-24
clusters: [1]
tests_added: 18
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1dz 1ea-1ez 4n 10ag 10d): 276 pass / 0 fail, 467 subtests pass"
---

# lex.ui: an independent review's findings on the new features, fixed

Eighteen scenarios appended to the batches that own the features, from the free numbers of
cluster 1 (1.911-1.919, 1.936-1.939, 1.953-1.957; `max_scenario` 957), each failing before its fix:

- [1dv](../../clusters/01-init/batches.md) `ui.private`: a computed var reading a private field is
  refused (1.911); a per-user allowance `LEX_UI_PRIVATE_MB_PER_USER`, and signing out empties it
  (1.913); an inherited private field is the parent's one value (1.938).
- [1ds](../../clusters/01-init/batches.md) searched choices: two fields never cancel each other and a
  search no longer holds the tab (1.912); a choice is the option's own value (1.917); offers leave
  with the user they were made to (1.918).
- [1dm](../../clusters/01-init/batches.md) `self.progress` never waits; the page gets the latest
  report (1.914).
- [1dw](../../clusters/01-init/batches.md) `@ui.job(limit=)`: per job, not per name (1.915); the
  refusal names the button and leaves with the visit (1.919).
- [1do](../../clusters/01-init/batches.md) a route argument named like a table's field says to rename
  the argument (1.916).
- [1dx](../../clusters/01-init/batches.md) a reconnect is not a new visit (1.936); one
  `ui.form(keep=True)` is enough (1.937); the docstrings say what the code does (1.939).
- [1dy](../../clusters/01-init/batches.md) `totals_of_all=True`, read once (1.953); `page_size=` 1-200
  and the pager only when needed (1.954); the first column always in place, `pin=` gone (1.955);
  the column choice lasts until the tab is closed (1.956); the built-table note is weak (1.957).
