---
date: 2026-11-20
clusters: [1]
tests_added: 1
suite_tally: "1bd 1bm 1bx 1by 1ca 10ag 10o: 57 pass / 0 fail / 1 skip, 36 subtests pass"
---

# Local dashboards can sign out (1ca)

Sign out on a local Reflex dashboard ended on "Redirect target not allowed.": a local run trusted
only the React dev server and Django as `next=` targets. The dashboards' dev ports are trusted
now; deployed instances are unchanged. Batch [1ca](../../clusters/01-init/batches.md).
