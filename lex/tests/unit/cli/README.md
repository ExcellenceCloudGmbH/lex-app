# CLI Tests — `lex.tests.unit.cli`

> **Story:** *"The `lex` CLI is the single entry point for init, test, start,
> migrate, and every other management command. It must resolve project roots,
> construct `sys.path` correctly, and delegate to Django's management layer."*

## What Lives Here (1 file)

| File | Covers |
|------|--------|
| `test_lex_cli.py` | CLI argument parsing, command routing, project-root discovery, `sys.path` injection, `LEX_APP_PACKAGE_ROOT` resolution, env-var propagation, error handling for missing projects |

## How to Run

```bash
source ~/LUND_IT/ArmiraCashflowDB/.venv/bin/activate
lex test lex.tests.unit.cli
```
