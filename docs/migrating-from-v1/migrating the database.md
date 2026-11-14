---
title: "Migrating the Database"
---

The [[migrating-from-v1/refactoring/index|refactoring series]] moves a V1 project's code. This page moves its data: `lex full_migration_workflow` takes a database a V1 (`generic_app`) installation wrote, brings it to the schema of your V2 models, keeps the tables V2 no longer has models for as a read-only archive, and fills in the history and audit tables V1 never had.

> [!important] Back up first, rehearse on a copy
> Take a database backup before you start, and run the workflow against a copy of the database before the real one. Its rollback returns the *migrations* to where they were; it is not a backup. See [[ship-and-operate/backup and restore#Before a migration|Backup & Restore]].

## Before You Run It

- The V2 code is in place: the refactored models in the project folder, `lex-app` installed.
- Nothing else has to be run first. The workflow takes the table snapshot and builds the freeze manifest itself.
- Each `lex` step runs as `python -m lex <command>` — steps 3 and 6 run lex's own scripts with `python` — in the project's root, with the interpreter of the project's `.venv` or `venv` when there is one, otherwise with the one running the workflow. For every step it sets `PROJECT_ROOT` to the project and `DATABASE_DEPLOYMENT_TARGET` to the connection profile.
- The steps work on the database the settings select: the profile in `DATABASE_DEPLOYMENT_TARGET` and, for the deployed profiles, `DATABASE_NAME`. Check those before you start — `--db-name` does not change it.

```bash
lex full_migration_workflow --v1-source ../my-v1-project --enable-sanitization --rollback-on-failure
```

## What It Does

The steps run in this order. The files they write go to the project's root.

| Step | What runs | What it does |
|---|---|---|
| first | `lex capture_migration_state` | Records which migration every app is on, in the rollback state file. Not with `--backfill-only` |
| 1 | `lex capture_db_tables` | Snapshots the database's table names into `.lex_tables_before.json`, before anything changes |
| 2 | — | With `--v1-source`, deletes the migration files in the project's `migrations/` (all but `__init__.py`) and copies in the V1 ones. Without it, uses the files already there, and stops if there are none |
| 3 | `fix_v1_migration.py` | Rewrites the V1 imports in those files — `generic_app…` becomes `lex…` |
| 4 | `lex generate_legacy_freeze_manifest --before .lex_tables_before.json` | Only with `--enable-sanitization`: the list step 8 makes — tables nothing in V2 owns — taken before migrating, in `.lex_legacy_freeze_manifest.pre_migrate.json` |
| 5 | `lex makemigrations <app>` | Generates the migrations from V1's state to your V2 models. `<app>` is the name of the project's folder |
| 6 | `sanitize_v2_migrations.py` | Only with `--enable-sanitization`: removes from the migration files step 5 wrote the top-level operations on the project's own `<app>_…` tables in the step 4 list: every operation that names the model as `model_name` (field, index and constraint changes), and the `CreateModel`, `DeleteModel`, `AlterModelTable`, `AlterModelOptions` and `AlterModelManagers` that name it. `migrate` then leaves those V1-only tables as they are, except for the operations this step leaves in — an `AlterUniqueTogether`, `AlterOrderWithRespectTo` or `RenameModel` on one still runs. Tables in the list without the project's prefix, `generic_app_…` ones say, are not protected |
| 7 | `lex migrate` | Applies the migrations |
| 8 | `lex generate_legacy_freeze_manifest --before .lex_tables_before.json` | Lists the tables that existed before, still exist, have a primary key, and that no V2 model and no system table owns — the legacy archive — in `.lex_legacy_freeze_manifest.json` |
| 9.1 | `lex normalize_is_calculated` | Turns V1's boolean `is_calculated` values into `SUCCESS` and `NOT_CALCULATED` |
| 9.2 | `lex backfill_bitemporal_history` | Writes history and meta-history rows, with the reason `V1 migration snapshot`, for every record that has none |
| 9.3 | `lex backfill_audit_logging` | Fills the audit log and the calculation log from V1's archive tables. It skips itself, with a warning, when either already holds rows |

At the end it prints a JSON summary between the lines `MIGRATION_WORKFLOW_SUMMARY_START` and `MIGRATION_WORKFLOW_SUMMARY_END`. A step that fails stops the workflow, which exits with `full_migration_workflow failed with exit code …`.

Without `--enable-sanitization`, step 7 applies the migrations exactly as `makemigrations` wrote them — including operations that change or delete V1 tables your V2 models no longer define.

## Flags

| Flag | Default | What it does |
|---|---|---|
| `--db-name` | the name of the `default` database in the settings | The name the workflow reports, and passes to every step as `DB_NAME`. The steps do not read it: they use the database the settings select |
| `--project-root` | `PROJECT_ROOT`, then the settings, then the current directory | The V2 project |
| `--v1-source` | none | Where the V1 migrations are: the workflow uses `<path>/migrations`, else the first `<path>/*/migrations`, else `<path>` itself |
| `--database-deployment-target` | `DATABASE_DEPLOYMENT_TARGET`, else `default` | The connection profile for every step: `local`, `default`, `GCP`, `DOCKER-COMPOSE` or `K8S` |
| `--migration-timestamp` | the time of the backfill | The ISO 8601 time every backfilled history row gets as `valid_from` and `sys_from`. Without an offset, it is read in the project's time zone |
| `--chunk-size` | `500` | Batch size of the three backfills; a positive whole number |
| `--dry-run-backfill` | off | Runs the three backfills with `--dry-run`: they report and write nothing |
| `--enable-sanitization` | off | Runs steps 4 and 6 |
| `--backfill-only` | off | Runs step 9 alone: no rollback state, no snapshot, no schema change |
| `--pre-clean-jsons` | off | Deletes the files of an earlier run first: the table snapshots, both manifests and the rollback state file |
| `--rollback-on-failure` | off | When a step fails, rolls back before exiting with the error |
| `--rollback-only` | off | Rolls back, and does nothing else |
| `--rollback-state-file` | `.lex_migration_state_before.json` | Where the migration state is written and the rollback reads it. A relative path is under the project root |
| `--skip-auditlog-backfill` | off | Leaves out step 9.3 |

## Dry Runs

The workflow has no dry run of its own. `--dry-run-backfill` covers the three backfills only: steps 1 to 8 still run, and their schema changes are real. To rehearse the whole migration, run it on a copy of the database.

To see what a rollback would undo, ask the rollback command itself:

```bash
lex rollback_migration_state --input .lex_migration_state_before.json --dry-run
```

## Rolling Back

`--rollback-on-failure` rolls back as soon as a step fails; `--rollback-only` does it later, on its own:

```bash
lex full_migration_workflow --rollback-only
```

Both run `lex rollback_migration_state --input <rollback state file>`, which migrates every app back to the migration it was on when the workflow started. That reverses migrations and nothing else: rows the workflow wrote into tables that survive the rollback stay, data a migration dropped does not come back, and the migration files replaced in step 2 are not restored — take those from version control, and the data from your backup.

> [!warning]
> Don't combine `--rollback-only` with `--pre-clean-jsons`: the clean-up runs first and deletes the state file the rollback needs.

## After the Migration

At startup, lex-app reads the legacy freeze manifest — from `LEX_LEGACY_FREEZE_MANIFEST_PATH` when that is set, otherwise the first `.lex_legacy_freeze_manifest.json` it finds in `PROJECT_ROOT`, beside the framework's `BASE_DIR`, or in the working directory or a folder above it. Each table it lists that still exists and has a primary key becomes a read-only model: you can read its rows, and the framework refuses to create, change or delete them.

Without a `model_structure`, these tables appear in the sidebar under **Legacy Generic App (Archive)**. With one, a table appears only where you list it, by its model name: `legacydynamic` followed by the table name without its underscores — `myapp_fund` is `legacydynamicmyappfund`. See [[model-your-data/model structure|Model Structure]].

Keep the manifest with the project. Why it is a manifest rather than code: [[migrating-from-v1/legacy registration|Legacy Registration]].
