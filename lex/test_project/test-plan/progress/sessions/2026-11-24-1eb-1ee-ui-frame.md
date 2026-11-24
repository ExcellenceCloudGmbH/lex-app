---
date: 2026-11-24
clusters: [1]
tests_added: 17
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1dz 1ea-1ee 4n 10ag 10d): 275 pass / 0 fail, 462 subtests pass"
---

# lex.ui's frame, configured with generic parts (1eb-1ee)

`ui.frame(nav=, denied=, request_access=, opened=, sign_out=)`, once in the dashboard module, and `@ui.rule`:
navigation in areas in the project's order, raw pages in an area, breadcrumbs derived from the area and the page
that opens a page, the project's no-access sentence and way out, a project function when a page has opened, and
a project's own access rule in `requires=` on pages and handlers. Design: section 6.19 of
`docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`. Batches [1eb, 1ec, 1ed, 1ee](../../clusters/01-init/batches.md).

One earlier test changed its given: 1.803 refused a list in a page's `requires=`; a page now takes a list as a
handler does, so it refuses a raw permission string instead (same intent: a wrong `requires=` is refused when the
page is declared, naming the route).
