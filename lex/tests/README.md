# Backend Tests — `lex.tests`

> **~1 920 tests** across **132 test files** in **3 top-level directories**

## Directory Structure

```
lex/tests/
├── unit/            ← 125 files, 11 topic subdirectories (pure unit tests, no DB)
├── integration/     ← 6 files (real SQLite DB, multi-module interactions)
└── e2e/             ← end-to-end journey tests (full DRF stack + real models)
```

| Directory | Files | What it covers |
|-----------|------:|----------------|
| [unit/](unit/) | 125 | Isolated module/class tests — every external call is mocked |
| [integration/](integration/) | 6 | Multi-module tests against a real SQLite DB |
| [e2e/](e2e/) | 3 | Full user-journey tests through the REST API with real models |

## How to Run

```bash
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate

# Everything
lex test lex.tests --noinput

# Just unit tests (~40 s)
lex test lex.tests.unit --noinput

# Just integration tests
lex test lex.tests.integration --noinput

# Just e2e tests
lex test lex.tests.e2e --noinput

# One topic cluster
lex test lex.tests.unit.calculation --noinput

# One file
lex test lex.tests.unit.audit.test_cache_manager --noinput
```

## Backward Compatibility (Shims)

All original test file locations still work:

```
lex.core.tests.test_X              → lex.tests.unit.<cluster>.test_X
lex.audit_logging.tests.test_Y     → lex.tests.unit.<cluster>.test_Y
lex.process_admin.tests.test_Z     → lex.tests.unit.<cluster>.test_Z
lex.lex_app.tests.test_W           → lex.tests.unit.<cluster>.test_W
```

Each old file is a 2-line re-export shim (`from lex.tests.unit.X.Y import *`)
that preserves backward compatibility for CI labels and import paths.

## Coverage

```bash
coverage run --source=lex --omit="*/tests/*,*/migrations/*,*/frontend/*,*/__pycache__/*" \
    -m lex test lex.tests --noinput
coverage report --rcfile=.coveragerc
```
