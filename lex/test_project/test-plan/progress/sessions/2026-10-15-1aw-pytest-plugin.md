---
date: 2026-10-15
clusters: [1]
tests_added: "12 scenarios (1.393-1.404), 18 tests"
suite_tally: "1aw 18 pass / 0 fail; full cluster suite 1690 pass / 38 fail / 27 skip / 6 xfail / 5 collection errors, against 1672 / 38 / 27 / 6 / 5 on lex-app-v2 - the same failing tests (24 cluster-15, 11 Reflex without the package locally, 6 stress, the gate self-test) and the same 3 warnings; PyCharm's own runner on crud_api/test_2a_create.py: 5 started, 5 passed, where it showed one failed top_level_collect before"
---

# Batch 1aw — a plain pytest run is set up like `lex pytest`

PyCharm's test tree showed one collection error for every lex project, because its runner
calls pytest.main() directly and none of `lex pytest`'s setup ran. lex-app now ships that
setup as a pytest plugin, `lex pytest` uses the same module, and `lex setup` writes the
suite and each group as test configurations. Details:
[batch 1aw](../../clusters/01-init/batches.md).
