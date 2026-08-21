---
date: 2026-08-17
clusters: [1ab]
tests_added: "7 (1.223-1.229) + source in 1 file (lex/lex_app/management/commands/init.py)"
suite_tally: "1ab 7 pass / 0 fail; init regression (1e/1g) 52 pass / 0 fail, 11 subtests pass (python -m lex pytest)"
---

**Batch 1ab landed — LEX-5 ignored-role self-cleanup.** `IGNORED_CLIENT_ROLES`
swaps the abandoned `release-manager` design for the new platform-internal
`client-admin` client role (see
[`local_wiki/projects/admin-role-separation-5/README.md`](../../../../../../local_wiki/projects/admin-role-separation-5/README.md)).
Because `lex init`'s Keycloak authz sync only ever ADDS `Policy - <role>`
entries via the `/authz/resource-server/import` endpoint — verified against
the existing `ensure_client_role_policies`/`sync_standard_client_role_permissions`
update-by-name pattern, it never deletes an entry missing from a re-imported
payload — a policy minted by an older lex-app for a role that later became
ignored would otherwise persist forever. Two new `KeycloakSyncManager`
methods close that gap: `strip_ignored_role_policies` (in-memory: drops the
ignored-role policy from `auth_config` and detaches its name from every
permission's `config.applyPolicies`, run before the final import) and
`delete_stale_ignored_role_policies` (live: finds `Policy - <ignored role>`
via `get_client_authz_policies` and deletes it via `delete_client_authz_policy`
once nothing references it, run *after* `import_authorization_settings`
succeeds so Keycloak's policy-delete referential-integrity check — confirmed
against Keycloak's documented policy/permission dependency behavior, not a
live cluster — passes). See batch [1ab](../../clusters/01-init/batches.md#batch-1ab--ignored-client-role-self-cleanup-client-admin-platform-role-lex-5).
