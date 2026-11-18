---
date: 2026-11-19
clusters: [1]
tests_added: 0
suite_tally: "Reflex batch 1bu: 10 pass / 0 fail"
---

# A table's state pickles (1.714)

A project's dashboard running in a browser logged `StateSerializationError` for every `lex_table`:
the state class `_state_for` builds per table was not reachable by name, so Reflex's state manager
could not pickle it -- to disk in development, and to Redis across workers in production, where a
table would lose its page between events served by different workers. Each class is now set on
`lex.lex_app.reflex.data` under its name; 1.714 (batch 1bu, see
[batches](../../clusters/01-init/batches.md)) asserts it pickles as itself.
