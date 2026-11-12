---
date: 2026-11-06
clusters: [1, 11]
tests_added: 0
suite_tally: "01-init 1aq + 1at: 19 pass (were 4 fail where lex-app is not installed); 11-stress 11f: 1 pass, 2 skip, 1 xfail (BUG-011); legacy lex/tests: 81 pass, 11 skip, 1 fail (the calculation defect fixed on its own branch)"
---

Making the suite's verdict independent of how lex-app is installed, found while verifying the long-sessions work:

- **01-init 1aq (1.357c, 1.368) and 1at (1.381):** their child interpreters ran in a temporary directory and imported `lex` only where lex-app is installed (CI), so a checkout run failed them with `No module named 'lex'`. They now pass the parent's `lex` on `PYTHONPATH` (`_child_env()`); nothing they assert changed.
- **11-stress 11.14:** BUG-011 is partially resolved and its row says 11.14 still fails, but the test's `@unittest.expectedFailure` had been commented out, so the cluster ran red; the marker is back (an unexpected pass fails). 11f's counts are measured for the first time.
- **Legacy `lex/tests/unit/infra`:** the nine `test_ai_windows_parity.py` tests and the `test_ai_asset_mode_sync.py` module exercise lex-mcp-local (the `lex ai-*` commands moved there); they now skip where it is not installed, as cluster 1m's do.
