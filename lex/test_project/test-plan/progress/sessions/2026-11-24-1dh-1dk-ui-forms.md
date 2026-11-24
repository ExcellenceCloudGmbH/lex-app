---
date: 2026-11-24
clusters: [1]
tests_added: 19
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1dk 4n 10ag 10d): 203 pass / 0 fail, 354 subtests pass"
---

# lex.ui forms: `ui.form`, `ui.field`, `ui.step`, `confirm=`, `ask=` (1dh-1dk)

A form page kind sent once to one handler, which gets checked Python values; wizards; tools with a result;
buttons that confirm or ask, and that show their handler running. Batches
[1dh-1dk](../../clusters/01-init/batches.md). Existing tests 1.783, 1.793, 1.801 and 1.804 follow: the namespace
gains `form`, `field`, `step`; a dangerous button now asks first; a page kind may be `ui.form`.
