# Infrastructure Tests — `lex.tests.unit.infra`

> **Story:** *"Before any business logic runs, the runtime must boot correctly —
> Celery workers must propagate context, health checks must respond, Keycloak
> connections must respect timeouts, and the init sequence must retry on
> transient failures."*

## What Lives Here (7 files, 26 tests)

| File | Tests | Covers |
|------|------:|--------|
| `test_init_retry.py` | 2 | Init command retry — `migrate` phase failure retry and `init` retry on transient Keycloak errors |
| `test_celery_callbacks.py` | 5 | Celery callback task — success updates only status fields, failure recreates missing terminal audit log, failure updates only error fields, missing-row retries after bitemporal resync |
| `test_celery_context.py` | 4 | Context propagation across Celery task boundaries — `ModelLoggingContext` snapshot/restore, `UserContext` replacement/restore, `CeleryContextTask` wrapping |
| `test_fast_health.py` | 2 | ASGI health endpoint — path matching (`/health`, `/healthz`) and JSON response with correct content-type |
| `test_keycloak_manager_timeout.py` | 7 | Keycloak HTTP timeout configuration — default read timeout, connect+read override from settings, single-value override, gateway-timeout error recording, client UUID resolution with special characters |
| `test_runtime_config.py` | 5 | Runtime config helpers — repo name derivation from Windows/POSIX paths, project-root resolution via marker file, SQLite path construction, Unicode DB error formatting |
| `test_user_model_registration.py` | 1 | ⚠️ Built-in `User` model auto-registration — **pre-existing failure** (`Converter 'model' is already registered`) |

## Key Concepts Tested

- **Boot resilience** — transient Keycloak/DB failures don't crash the init sequence
- **Celery context** — user and audit context survives serialisation across worker boundaries
- **Health probes** — Kubernetes-style `/health` endpoint for liveness checks
- **Timeout discipline** — Keycloak HTTP calls respect configurable connect + read timeouts
- **Runtime discovery** — project root, repo name, and DB path resolved from environment

## Known Issues

| Test | Status | Details |
|------|--------|---------|
| `test_user_model_registration` | ⚠️ ERROR | `Converter 'model' is already registered` — pre-existing issue, same at old location |

## How to Run

```bash
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate
lex test lex.tests.unit.infra              # all 26 tests (1 pre-existing error)
lex test lex.tests.unit.infra.test_celery_callbacks  # 5 tests
```
