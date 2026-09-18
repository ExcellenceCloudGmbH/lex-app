---
date: 2026-09-18
clusters: [1]
tests_added: 10
suite_tally: "tests/init: 2 failed, 589 passed, 14 skipped (1ap: 10 pass / 0 fail)"
---

# Batch 1ap — the flow names its own entry

Ten scenarios for `lex_view` taking its entry route from a sequence flow, allocated as
[batch 1ap](../../clusters/01-init/batches.md) (1.348–1.350). It follows directly from
[batch 1ao](../../clusters/01-init/batches.md), which gave a flow an order and then still
made the caller repeat where that order began:

```python
lex_view("investor/create", flow=Flow().create("investor").create("vehicle"))
```

Raised by the developer as "from the flow it's obvious that we will start from the
create form, so why can't we just use the flow". The answer was that nothing prevented
it — `path` and step 0 were simply two statements of one route with no check between
them, and when they disagreed the embed opened one place while the program walked from
the other. `Flow.entry_path()` derives the route from step 0 and `lex_view` uses it when
no path is given, which deletes one of the two statements rather than validating it.

Two things worth carrying forward:

* **A first `update` can only carry a literal id**, so step 0 is always resolvable
  without asking the frontend. An implicit id has no previous step to take from and a
  `ref` can only point backwards — both already refused by `_resolve_id` at author time,
  three scenarios back in 1.343. The derivation inherits that guarantee rather than
  re-deriving it, and still returns `None` for anything else: a guessed route is worse
  than the root, because the root is obviously not the flow and a wrong record looks
  right.
* **1.348's fourth scenario asserts the opened route and the shipped cursor name the same
  step.** The cursor ships at 0 (1.347), so a derivation that ever drifted from step 0
  would reintroduce the same desynchronisation from the other side. It is the one
  assertion here that would catch a future regression rather than a typo.

The two failures in the tally are pre-existing in
`test_1r_lex_view_embed_helper.py::TestCluster01r_LexViewComponentLazyDeclaration`
(1.157, 1.158) and reproduce with this change stashed.
