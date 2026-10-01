---
date: 2026-10-02
clusters: [1]
tests_added: 0
suite_tally: "Reflex files (1ap–1at): 41 pass / 0 fail — 1at 3 pass"
---

# Batch 1at follow-up — the proxy behind middleware

v2.3.3's repair handed Reflex Enterprise's proxy the Starlette app, and production still answered
every page with 404: the proxy mounts on the outermost app, behind Reflex's catch-all mount of a
backend that an `api_transformer` had wrapped in middleware. It now mounts on the Reflex backend
itself ([batch 1at](../../clusters/01-init/batches.md), follow-up).
