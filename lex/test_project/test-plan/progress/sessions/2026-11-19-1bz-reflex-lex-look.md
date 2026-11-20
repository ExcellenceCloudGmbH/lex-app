---
date: 2026-11-19
clusters: [1]
tests_added: 5
suite_tally: "every Reflex batch (1ap 1aq 1bt 1bu 1bv 1bw 1bx 1by 1bz 4n 10ag 10d), plain Reflex: 85 pass / 0 fail, 102 subtests pass"
---

# The LEX look for Reflex dashboards (1bz)

Reflex dashboards now look like lex-app (decision E13): `lex/lex_app/reflex/theme.py` generates
their stylesheet from the design tokens, light and dark. Batch
[1bz](../../clusters/01-init/batches.md) covers the token mapping, the specificity and
re-declaration guard against RFDS's own sheets, the teal accent scale, lex-app's components, and
the light default. 1.704 (1bt) reads the chrome navy from the theme now.
