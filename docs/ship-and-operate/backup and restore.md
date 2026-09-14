---
title: "Backup & Restore"
---

Lex App keeps state in three places, and only one of them has a framework
command for backing it up. Knowing which is which is most of this page.

| What | Where it lives | Who backs it up |
|---|---|---|
| Your application data | PostgreSQL | **You**, with your platform's database tooling |
| Uploaded files | Local disk, GCS, or SharePoint — see `STORAGE_TYPE` | **You**, with that storage's tooling |
| Permission configuration | Keycloak | `lex keycloak_backup` |

> [!important]
> There is no `lex backup` that captures everything. A framework command that
> pretended to would be worse than none, because the only way to discover it
> had missed the database is to need the database.

## Permission configuration

`lex init` writes your models into Keycloak as resources, roles, policies and
permissions. That configuration is derived from your code, so in principle it
can be rebuilt — but only the parts that came from code. Anything a person
adjusted in the Keycloak admin console afterwards exists nowhere else.

Back it up before any change that touches it:

```bash
lex keycloak_backup
```

This writes a timestamped `keycloak_authz_<date>_<time>.json` into
`keycloak_backups/`, and prints how many resources, policies and permissions it
captured. Pass `--backup-dir` to put it somewhere else — a mounted volume,
rather than a container filesystem that disappears on the next deploy.

```bash
lex keycloak_backup --list-backups          # what you have, with realm and date
lex keycloak_backup --restore keycloak_backups/keycloak_authz_20260914_113000.json
```

> [!warning] Read the output, not the exit code
> `--restore` reports failures by printing them and then exits successfully
> anyway. A restore that failed looks like a restore that worked to any script
> checking `$?`. Confirm you saw `✓ Authorization settings restored
> successfully` and the resource, policy and permission counts you expected.

The backup records the realm and client UUID it came from. Restoring into a
different realm is not blocked, and not sensible — check the header that
`--list-backups` prints before you restore anywhere but where it came from.

## Before a migration

A schema migration is the change most likely to need undoing. Two snapshots
make that possible, and neither is a data backup:

```bash
lex capture_migration_state      # → .lex_migration_state_before.json
lex capture_db_tables            # → .lex_tables_before.json
```

The first records which migration each app is on, so
`lex rollback_migration_state` can put them back. The second records the
physical table names, so you can see what a migration actually created or
dropped — and feed it to `lex generate_legacy_freeze_manifest`.

```bash
lex rollback_migration_state --dry-run    # read this first
lex rollback_migration_state
```

> [!warning]
> Rolling migrations back reverses **schema** changes. It does not bring back a
> column's data, and a migration that transformed values on the way through has
> no inverse. Take a database backup as well — the snapshot is for the shape,
> the backup is for the contents.

## The database

This is the part the framework does not do for you, and the part that matters
most. Lex App is a standard Django application on PostgreSQL, so the standard
tooling applies: `pg_dump` and `pg_restore`, or whatever your platform's managed
PostgreSQL offers as scheduled snapshots and point-in-time recovery.

Two things about Lex App specifically are worth knowing when you plan it:

- **History is in the database too.** [[history-and-audit/bitemporal history|Bitemporal history]]
  and the [[history-and-audit/audit logs|audit log]] are ordinary tables. A
  restore to an earlier point loses the audit trail of everything after it — the
  record of the incident is inside the thing being rolled back.
- **Uploaded files are usually not.** Unless `STORAGE_TYPE` is unset or
  `LEGACY`, files live in GCS or SharePoint and a database restore will leave
  rows pointing at objects whose state does not match. Restore both to the same
  point, or expect broken links.

Which of `pg_dump`, managed snapshots or streaming replication is right for you
depends on how much data loss and how much downtime your installation can take.
That decision belongs to whoever operates your databases — this page will not
guess it for you.

## What a full restore looks like

In the order that avoids the failure modes above:

1. Stop the web, worker and dashboard processes. A running worker will write
   into a database you are in the middle of replacing.
2. Restore the database.
3. Restore file storage to the same point in time.
4. Restore Keycloak authorization with `lex keycloak_backup --restore`, or
   rebuild it with `lex init` if no one has adjusted it by hand.
5. Start the web process, confirm `/api/health` answers — see
   [[ship-and-operate/monitoring and health|Monitoring & health]] — then start
   the workers.

Step 1 is the one that gets skipped. Everything else is recoverable; a worker
writing into a half-restored database is not.
