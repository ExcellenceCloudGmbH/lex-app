---
date: 2026-11-17
clusters: [1, 10]
tests_added: 8
suite_tally: "see the batches; the init, api_layer and permissions clusters were run in full on this branch"
---

# Batches 1bw and 10af — consultant tooling, and which framework draws a dashboard

`lex reflex add` and `lex reflex check`, and a reference `_reflex_structure.py` that uses every
feature and compiles under RFDS's strict CI conformance
([batch 1bw](../../clusters/01-init/batches.md), 1.724–1.727). `api/project-info` tells
lex-app's frontend which framework draws each model's dashboards
([batch 10af](../../clusters/10-api_layer/batches.md), 10.190–10.193); the frontend half is
process-admin-general-client #650.

The shared compile (`tests/init/_reflex_compiled.py`) now defines its own example model: the
`lex_app` app has no models of its own, and the reference example needs one to be about.
Before, the compile only found models because 1aq's module defined some -- 1bw run alone failed.
