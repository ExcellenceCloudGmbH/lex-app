---
date: 2026-11-24
clusters: [1]
tests_added: 5
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1dz 1ea-1ei 4n 10ag 10d): 316 pass / 0 fail, 473 subtests pass"
---

# lex.ui: the review fixes, the frame (1eb-1ee) and the calculation page (1ef-1ei) together

The frame and the calculation page were built beside the review's fixes and merged onto them. Their
specs both took section 6.19: the frame keeps it, the calculation page is now section 6.20 of
`docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`. Where the three meet, five scenarios were
added inside the existing batches; two found a gap, fixed here:

- 1.977 (1ee) -- a reconnect of the same page ran its loads again and called `ui.frame(opened=)` again; it is
  now called once a visit.
- 1.978 (1ef) -- a `@ui.rule` on a calculation page hid the page but left lex-app's own handlers of the page
  open to events sent by name; they now require what the page requires.
- 1.979 (1ec) -- a calculation page in an area, and its run's view (`?run=`) as one more crumb under it.
- 1.998 (1eh) -- the past runs after the table changes: the first column in place, the pager only when needed.
- 1.999 (1eg) -- a reconnect while a run is followed: the one following goes on, the page opened once.

The calculation page's history test reads the built table's state by the table (the review made that note
weak, keyed by the part) instead of by its address.
