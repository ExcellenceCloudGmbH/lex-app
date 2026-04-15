# Auth & Permissions Tests — `lex.tests.unit.auth`

> **Story:** *"A user authenticates via Keycloak, their token is decoded into
> roles and scopes, and every API request is checked against model-level and
> field-level permissions — before any data is touched."*

## What Lives Here (6 files, 154 tests)

| File | Tests | Covers |
|------|------:|--------|
| `test_keycloak_permissions_middleware.py` | 25 | Django middleware — UMA permission/userinfo attachment, failure resilience (no crash on Keycloak errors), client-role extraction, stale-token cleanup |
| `test_permission_enforcement.py` | 39 | `PermissionResult` normalisation, `enforce_permissions` (new API, legacy fallback, exception safety), and convenience helpers (superuser, groups, owner, keycloak, sensitive/public/basic fields) |
| `test_permission_result.py` | 32 | Every `PermissionResult` factory method (`allow`, `deny`, `from_bool`, `from_exception`, `merge`), edge cases, and `__str__` representation |
| `test_streamlit_token_views.py` | 19 | JWT token lifecycle for Streamlit dashboards — status checks (valid/expired/revoked/wrong-user), generation with payload/expiry/permissions, cache-based revocation |
| `test_user_context.py` | 37 | `UserContext` construction — anonymous factory, `from_request` resolution, two-phase build, Keycloak scope resolution (including historical-model unwrap), permission/role normalisation |
| `test_api_key_user_context.py` | 2 | API-key-based `UserContext` construction — currently skipped due to hash-length issue |

## Key Concepts Tested

- **Token flow** — raw JWT → decoded claims → `UserContext` with roles/scopes
- **Middleware chain** — Keycloak permissions injected before any view runs
- **Permission result algebra** — `allow ∧ deny = deny`, merge rules, exception handling
- **Field-level access** — sensitive, public, basic field classifications
- **Streamlit tokens** — separate JWT flow for dashboard embedding with revocation

## How to Run

```bash
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate
lex test lex.tests.unit.auth               # all 154 tests
lex test lex.tests.unit.auth.test_permission_enforcement  # 39 tests
```
