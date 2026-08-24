---
date: 2026-08-25
clusters: [1]
tests_added: 8
suite_tally: "8 pass / 0 fail (batch 1ac in isolation)"
---

Batch **1ac** — the pure spec/manifest layer for `lex_widgets()`, the Streamlit widget host that
supersedes closed PR #692.

The design decision this batch guards: one iframe per *page* rather than per widget. A dashboard
of thirteen calculations was thirteen React runtimes with four WebSockets each; the manifest
exists so they share one. That makes the manifest the Python↔React contract, and a manifest that
silently drops an entry produces the worst failure mode available — a clean-looking page missing a
widget.

Validation therefore refuses unknown options, unknown types and duplicate ids, and does it at the
call site rather than at render time.

Letter `ac` and range 1.251–1.258 were chosen over the nominally-next `ab`/1.223 because open PRs
#723 and #726 both claim `1ab`/1.223–1.239 and closed #692 claimed 1.223–1.250.

Dashboard not regenerated in this change — run
`python .github/scripts/test_plan_aggregates.py build` before merge.
