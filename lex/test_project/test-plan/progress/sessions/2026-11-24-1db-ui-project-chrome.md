---
date: 2026-11-24
clusters: [1]
tests_added: 2
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da 1db 4n 10ag 10d): 178 pass / 0 fail, 237 subtests pass"
---

# lex.ui pages in a project's own chrome, in a project's own app (1db)

A project with its own frame, gate and app module can now write pages with `@ui.page`:
`ui.chrome(frame, first=)` and `ui.install(app)`. Batch [1db](../../clusters/01-init/batches.md);
1.778 and 1.805 read `create_app`'s call of `ui.install`, and 1.783's namespace gains both names.
Design: `docs/superpowers/specs/2026-11-24-reflex-ui-project-chrome-design.md`.
