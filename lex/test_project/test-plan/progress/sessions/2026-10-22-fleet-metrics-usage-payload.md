---
date: 2026-10-22
clusters: [10]
tests_added: "13 new scenarios (10.102-10.114)"
suite_tally: "api_layer: 13 pass / 0 fail"
---

# Batch 10s — fleet-metrics push build, Task 11, PR F part 1 (usage payload)

Branch `feat/fleet-metrics-usage-payload`, from `origin/lex-app-v2`. Scope: the usage hash and
the usage payload in `api/user/` -- half of lex-app's fleet-metrics phase-1 push build
(source-plan.md section "lexapp"). Split out of `feat/fleet-metrics-emitters` to keep each
branch under the 1,500-changed-line review budget; the event emitter and the heartbeat are
`feat/fleet-metrics-emitters`.

[10s](../../clusters/10-api_layer/batches.md) covers `lex/core/services/usage_config.py` (the
fail-closed `LEX_USAGE_METRICS` switch, ported from the closed, unmerged `usage-recorder`
branch; the per-instance-salted `user_hash`, pinned against a known-answer case computed
independently in the test (10.114) rather than only "deterministic" and "salt-sensitive"
assertions; and `usage_payload`, which collapses to exactly `{"enabled": false}` the moment any
one of the switch/salt/PostHog-key trio is missing) and the one-line
`lex/authentication/views/user_api.py` change that merges it into `api/user/`'s existing
response. `user_hash`'s HMAC key is the literal UTF-8 bytes of `LEX_USAGE_SALT`, not
`bytes.fromhex(salt)`: the salt is delivered "like `LEX_API_KEY`" (source-plan.md 3.1), an
opaque string the rest of the codebase never hex-decodes either.
