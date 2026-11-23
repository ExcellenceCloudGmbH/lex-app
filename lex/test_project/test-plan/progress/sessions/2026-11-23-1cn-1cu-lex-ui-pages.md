---
date: 2026-11-23
clusters: [1]
tests_added: 22
suite_tally: "every Reflex batch (1ap 1aq 1bt 1bu 1bv 1bw 1bx 1by 1bz 1ca 1cb 1cc-1cu 4n 10ag 10d): 159 pass / 0 fail, 194 subtests pass"
---

# lex.ui pages, step 1: the record and list kinds (1cn-1cu)

A dashboard page is declared with `@ui.page` and returns a page kind -- `ui.record` or `ui.list` --
whose named slots hold lex components that only describe: no styling argument, no layout, no
loading branch, no route string, no translation key. lex-app builds the page on RFDS's floorplans,
checks every slot, and registers the page's loads. Batches
[1cn to 1cu](../../clusters/01-init/batches.md) cover each module. Design:
`docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.
