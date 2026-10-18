---
date: 2026-10-18
clusters: [1, 4, 10, 12]
tests_added: "28 new scenarios (1.386-1.393, 4.101-4.110, 10.83-10.90, 12.64-12.65); 10.61-10.66 and 15.32-15.38 now read calculation logs as a superuser"
suite_tally: "new batches 28 pass / 0 fail; permissions or api_layer or serializers or init or calculation_logging: 960 pass / 9 fail (15b-15e, pre-existing) / 16 skip / 4 xfail; not stress (without test_1aq_reflex_dashboards, 16 pass on its own): 1740 pass / 26 fail / 24 skip / 6 xfail, the same 26 failures as unmodified lex-app-v2"
---

# Batches 1av, 4p, 10o, 12o — v2 security hardening, internal tools and configuration

Branch `fix/v2-security-hardening`, part 2, on top of [4o/12n/14f](2026-10-18-v2-security-hardening-permissions.md).
Ported reduced from #815, #821, #823, #825, #826 and #830 without the 3.0 extension keys; the
history-write guard and the automatic cache tables are new.

[4p](../../clusters/04-permissions/batches.md): `LEX_SEC_INTERNAL` closes the operator endpoints
(delete-unused-files for superusers and the `admin` client role, `POST api/logs` and Django
user edits for superusers, calculation-log tree and PDF only for logs of readable records),
`permission_list` false makes the list 404, and `LEX_SEC_HISTORY_WRITE` refuses every write to
`Historical*`/`MetaHistorical*` rows for non-superusers with 400 `EDIT_NOT_ALLOWED`.
[12o](../../clusters/12-serializers/batches.md) makes those rows' `lex_reserved_scopes` say so.
[10o](../../clusters/10-api_layer/batches.md): no tracebacks in error bodies by default, the OIDC
redirect guard (also `total_logout`, CORS allow-list and `CSRF_TRUSTED_ORIGINS` accepted), JSON 404
for unknown `api/` paths, `api/global-search/?q=`, one KeycloakManager per process.
[1av](../../clusters/01-init/batches.md): nested project root by `lex_config.py`, a broken
`lex_config.py` stops startup, cache tables after every migrate and before the initial-data load,
`LEX_CORS_ALLOWED_ORIGINS`.

10.61–10.66 and 15.32–15.38 created calculation logs that belong to no record and read them as a
plain user; they now read them as a superuser, the only caller that may.

Full suite (`-m "not stress"`) compared with unmodified `lex-app-v2` (c22d24d7) on the same
machine: the same 26 failures on both (10.10, calculation-log rows in 15a/15b/15c/15d/15e/15g/15j,
the gate self-test). Both trees hang in `test_1aq_reflex_dashboards.py` (1.360, then 1.362) when it
runs after the rest of the suite; the file passes on its own (16 pass) on both, so the full runs
deselected it with `-k "not test_1aq_reflex_dashboards"`.

