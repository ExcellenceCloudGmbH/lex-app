---
date: 2026-10-22
clusters: [10]
tests_added: "15 new scenarios (10.102-10.116)"
suite_tally: "api_layer 10s: 15 pass / 0 fail"
---

# Batch 10s — the usage payload in `api/user/`

Branch `feat/fleet-metrics-usage-payload`, from `origin/lex-app-v2`: the usage hash and the usage
payload the interface reads from `api/user/`. The event emitter and the heartbeat follow on
`feat/fleet-metrics-emitters`.

[10s](../../clusters/10-api_layer/batches.md) covers `lex/core/services/usage_config.py` (the
fail-closed `LEX_USAGE_METRICS` switch; the per-instance-salted `user_hash`, pinned against a
known-answer case computed independently in the test (10.114); and `usage_payload`, which
collapses to exactly `{"enabled": false}` as soon as the switch, the salt, the PostHog key or the
instance identifier is missing) and `lex/authentication/views/user_api.py`, which merges it into
`api/user/`'s existing response and answers `{"enabled": false}` when building it fails (10.116).
`user_hash`'s HMAC key is the literal UTF-8 bytes of `LEX_USAGE_SALT`, not `bytes.fromhex(salt)`:
the salt is an opaque string, like `LEX_API_KEY`, which the codebase never hex-decodes either.
