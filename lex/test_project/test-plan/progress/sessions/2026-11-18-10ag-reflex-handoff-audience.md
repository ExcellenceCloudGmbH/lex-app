---
date: 2026-11-18
clusters: [10]
tests_added: 4
suite_tally: "Hand-off, auth-contract and embed batches (10aa, 10ab, 10ag, 1be, 1bf): 83 pass / 0 fail"
---

# Reflex dashboards get a hand-off audience (10ag)

First half of running Reflex dashboards without Reflex Enterprise: `oidc/handoff` now serves the
`reflex` audience, always, to the instance's Reflex hosts only. Batch
[10ag](../../clusters/10-api_layer/batches.md) covers the view, the origin rules and the
capability; the Reflex backend that uses it lands in the next batch of cluster 1.
