---
date: 2026-07-15
clusters: [1i]
tests_added: "2 (1.195–1.196)"
suite_tally: "1i 2 pass / 0 fail"
---

**Batch 1i landed — URL-routing coverage for the recovery-beat scale metric.**
PR #654 added `lex/api/views/calculations/RecoveryScaleMetric.py` and wired it
into `lex/lex_app/urls.py` as `api/recovery-scale-metric` (named route
`recovery-scale-metric`). The view logic is covered by batch 8c; this batch
covers the URL registration itself — the surface that batch 8c leaves out.

Two scenarios: 1.195 pins that `reverse("recovery-scale-metric")` resolves to
the correct path so the named route can't be silently renamed, and 1.196 pins
that `resolve("/api/recovery-scale-metric")` maps to `RecoveryScaleMetric` so
the URL can't be accidentally re-wired to a different handler. Both are pure
Django URL-resolver assertions (type U, no DB, no network).

See [batch 1i](../../clusters/01-init/batches.md).
