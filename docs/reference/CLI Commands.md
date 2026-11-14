---
title: CLI Commands
---

Lex App ships with a `lex` CLI tool for managing your application. Here's every command at a glance.

![What `lex --help` prints — eleven commands, not the whole set](images/cli/lex-help.svg)

> [!important] This page is longer than `lex --help` on purpose
> `--help` lists only the commands the CLI implements itself. Everything below
> that is a Django management command works but does not appear there, because
> printing it would mean starting Django just to render help.

## Everyday Commands

| Command            | What It Does                                                                                                                                          |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `lex setup`        | Generate `.run/`, `.vscode/launch.json`, `.env`, and `migrations/` for a new project                                                                  |
| `lex init`         | Apply migrations + sync models/permissions to Keycloak — see [below](#lex-init) |
| `lex start`        | Start the development server                                                                                                                          |
| `lex streamlit`    | Start the [Streamlit](https://docs.streamlit.io/) dashboards behind their sign-in proxy — see [below](#lex-streamlit) |
| `lex reflex`       | Start the [Reflex](https://reflex.dev/docs/) dashboard server — arguments go to Reflex's own CLI; see [below](#lex-reflex)                            |
| `lex create_db`    | Create the PostgreSQL database if it doesn't exist yet; does nothing on SQLite — see [[start-here/installation#Choose a Database\|Choose a Database]] |
| `lex --version`    | Print the installed lex-app version (`lex, version 2.5.0`) without starting Django |
| `pip show lex-app` | Where the package is installed from, and the version pip recorded for it |

`lex --version` prints the version stamped into the package when it was built,
the one the instance reports to the platform with its health check. On an image
built from a git ref the build stamps it after `pip install`, so there it can
differ from the version `pip show lex-app` prints.

### `lex init`

`lex init` brings the database and the Keycloak realm in line with your models.
In order, it:

1. with `--bootstrap`, runs the browser bootstrap flow when Keycloak credentials
   are missing from the environment — the flow `lex bootstrap_keycloak` runs;
2. checks the Keycloak client: it must be confidential, and when
   `DEPLOYMENT_ENVIRONMENT` is not set it must also be a development client,
   one with a `localhost` redirect URI;
3. detects added, deleted and renamed models;
4. runs `lex create_db` (PostgreSQL only) and `createcachetable`, then
   `makemigrations`, then `migrate`;
5. adds the models Keycloak has no resource for, and syncs resources, scopes and
   permissions.

`makemigrations` runs for **your project's app only** — the project folder, which the
settings install as an app unless its name starts with `lex`; when they don't, it runs for
every app. lex-app and the other
installed packages ship their own migrations, and one generated for them inside
site-packages would clash with the next one the package ships. An app label in
`--makemigrations-args` replaces your project's app; name both if you need both.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--bootstrap` / `--no-bootstrap` | off | Step 1. It waits up to 15 minutes for the browser flow. |
| `--skip-client-preflight` | off | Skip step 2 — only when you mean to run against a public client, or against a non-development one from a developer machine. |
| `--no-makemigrations` | off | Skip `makemigrations`; apply the migration files that exist. |
| `--skip-migrations` | off | Skip step 4 entirely, `create_db` and `createcachetable` included. |
| `--migration-verbosity N` | `1` | Verbosity of `makemigrations` and `migrate`, `0` to `3`. |
| `--makemigrations-args "…"` | — | Further arguments for `makemigrations`, as one quoted string. |
| `--migrate-args "…"` | — | Further arguments for `migrate`, as one quoted string. A `--database ALIAS` here also points `create_db`, `createcachetable` and the check for pending migrations at that database. |
| `--dry-run` | off | Report the model changes and whether migrations are pending, then stop: nothing is migrated and nothing changes in Keycloak. `create_db` and `createcachetable` still run. |
| `--check-missing` / `--no-check-missing` | on | Look for models Keycloak has no resource for, and add them. |
| `--preserve-renamed-permissions` / `--no-preserve-renamed-permissions` | on | Carry a renamed model's permissions over to its new name. |
| `--ensure-default-authz` | off | Make sure the client has a default resource, a regex default policy and a resource-based default permission. |
| `--sync-retries N` | `3` | Attempts at each Keycloak step, waiting 2, 4, 8… seconds in between. The flag's own help text says the default is 1; it is 3. |

Inside the two `…-args` strings a flag followed by a word takes that word as
its value, so put app labels and migration names first:
`--migrate-args "myapp 0001 --fake"`, not `"--fake myapp 0001"`.

### `lex start` flags

`lex start` wraps the ASGI server. The Quick Start in [[getting started|Getting Started]] uses:

```bash
lex start --reload --loop asyncio lex_app.asgi:application
```

| Flag / argument            | Meaning                                                                                                                                 |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `--reload`                 | Restart the server when source files change. Use during development only.                                                               |
| `--loop asyncio`           | Force the standard library asyncio event loop instead of `uvloop`. Recommended for development on Windows or when debugging async code. |
| `lex_app.asgi:application` | The ASGI mount point. This is the framework's entry point — leave it as-is unless you have a custom ASGI app.                           |

For production runs, drop `--reload`.

### `lex streamlit`

`lex streamlit` is [Streamlit](https://docs.streamlit.io/)'s own command line,
run in a process that has Django set up and with the sign-in proxy in front of
it: every argument after `streamlit` goes to Streamlit unchanged. With none, it
is `streamlit run` on lex-app's own `streamlit_app.py`, which serves the
project's dashboards — see [[access-and-dashboards/streamlit/index|Streamlit Dashboards]].
The first argument that contains `streamlit_app.py`, when it is a relative path, is
resolved against the lex-app package's folder — `apps/streamlit_app.py` becomes
`<package>/apps/streamlit_app.py`. That is how the generated **Streamlit** run
configuration, `lex streamlit run streamlit_app.py`, runs the package's own app in any
project.

| Listener | Default | Moved by |
| --- | --- | --- |
| The proxy, on `0.0.0.0` — the address you open | `8501` | `--browser.serverPort`, or `LEX_PROXY_PORT`, which wins |
| Streamlit itself, behind the proxy | `8080` | `--server.port`; the proxy follows it unless `UPSTREAM` is set |

What it adds to running `streamlit` yourself:

- the **LEX theme**, as `--theme.*` flags. Command-line flags override the
  `[theme]` of a project's `.streamlit/config.toml`; pass a `--theme.<option>`
  yourself and lex-app leaves that one out, so yours wins;
- Streamlit's usage statistics off, and headless mode on, unless you set them;
- `--server.disconnectedSessionTTL` from `LEX_STREAMLIT_DISCONNECTED_SESSION_TTL`
  (600 seconds by default), so a dashboard that reconnects after a sign-in round
  trip keeps its session;
- a fresh `LEX_INTERNAL_AUTH_SECRET` shared by the proxy and Streamlit, unless
  you set one.

It refuses to start, with a message naming the setting, when:

- `LEX_STREAMLIT_AUTH` is not `legacy`, `handoff` or `dual`;
- `LEX_STREAMLIT_AUTH` is `handoff` or `dual` on a deployed instance that names
  no app host (`LEX_INSTANCE_HOSTS` or `DOMAIN_HOSTED`) or no dashboard host
  (`LEX_INSTANCE_HOSTS` or `STREAMLIT_URL`);
- `SESSION_SAMESITE` is not `lax`, `strict` or `none`;
- the cookies would be `SameSite=None` — the default over https — without
  `Secure` (`SESSION_HTTPS_ONLY` false), which browsers discard;
- `LEX_PROXY_REPLICAS` is above 1 with no `TOKEN_REDIS_URL` or `REDIS_URL`,
  except under `handoff`.

It warns and starts anyway when Streamlit is older than 1.58 (light/dark sync
with Lex App cannot work), when `websockets` is older than 13, when dashboard
sessions will not survive a restart (no `SESSION_SECRET` and no private
`DJANGO_SECRET_KEY`, or no Redis token store outside `handoff`), and when a
deployed hand-off has neither an https `STREAMLIT_URL` nor `SESSION_HTTPS_ONLY`.
When Streamlit stops, the proxy
shuts down cleanly, within `LEX_PROXY_SHUTDOWN_TIMEOUT` seconds. The variables
are listed under [[reference/Environment Variables#Streamlit|Streamlit]].

### `lex reflex`

`lex reflex` is [Reflex](https://reflex.dev/docs/)'s own command line, run for the project: every argument after `reflex` goes to it unchanged — `--help` included, which prints Reflex's help — and `lex reflex` with nothing after it is `lex reflex run`.

| Command                         | What It Does                                                                                      |
| ------------------------------- | ------------------------------------------------------------------------------------------------- |
| `lex reflex`                    | The development server, with hot reload: dashboards at `http://localhost:8502`, backend on `8503` |
| `lex reflex run --env prod`     | Production mode: an optimised build, frontend and backend on one port, `8502`                     |
| `lex reflex run --backend-only` | Only the backend, on `8503`                                                                       |
| `lex reflex login`              | Sign this machine in to Reflex — Reflex Enterprise refuses to start without it                    |
| `lex reflex --help`             | Reflex's own help, for every other subcommand and flag                                            |

What it adds to running `reflex` yourself:

- it runs from the **project root**, wherever you call it from — Reflex reads `rxconfig.py` from, and builds `.web/` into, the directory it runs in;
- it writes **`rxconfig.py`** there on first use (`config = lex_config()`) and never touches it again;
- it gives each run mode the **ports** it accepts, and nothing when you chose one — see [[access-and-dashboards/reflex/running and deploying#Ports|Ports]];
- it makes **hot reload** watch the project's own files rather than the installed `lex` package.

It does not set Django up in its own process: Reflex's workers import the app, and set Django up there. See [[access-and-dashboards/reflex/index|Reflex Dashboards]].

## Testing Commands

| Command             | What It Does                                                          |
| ------------------- | --------------------------------------------------------------------- |
| `lex pytest`        | Run your project's test suite with Django bootstrapped                |
| `lex pytest-groups` | List configured test groups and the tests they contain (no tests run) |

`lex pytest` behaves like plain `pytest` — any flags you pass are forwarded directly. Before running, it prepares Django's test environment and sets up the test database for your `default` alias. If that test DB already exists, `lex pytest` reuses it and runs migrations (`keepdb` behavior); if it's missing (common in CI), it creates it first. If that setup step fails, `lex pytest` stops early and shows the setup error instead of continuing with a partial run.

> [!warning] On a deployed database profile, the test database is the real one
> With `DATABASE_DEPLOYMENT_TARGET` set to `GCP`, `K8S` or `DOCKER-COMPOSE`, the
> test database's name is `DATABASE_NAME` itself. `lex pytest` reuses it as it
> reuses any existing test database, so the tests run against that database and
> its data: nothing is created or dropped, and whatever a test commits stays.
> Run tests with the `default` or `local` profile, or against a database you can
> lose.

Three extra flags are intercepted:

| Flag | What It Does |
| --- | --- |
| `--report` | Generate a branded PDF test report after the run |
| `--report-and-email` | Generate the PDF report and send it to the configured recipients. It shows who gets what and asks before sending; with no terminal to ask in, as in CI, it fails unless `--send-emails` is given |
| `--send-emails` | With `--report-and-email`: send without asking. The form for CI |

The emails go through Django's mail backend, which is SendGrid and needs
`SENDGRID_API_KEY`, unless `LEX_TEST_EMAIL_BACKEND` names another one as a
dotted path. With `django.core.mail.backends.filebased.EmailBackend` they are
written to files in `LEX_TEST_EMAIL_FILE_PATH`, by default `email-outbox` in the
report directory.

Use `-m` marker expressions to run only a subset of tests:

```bash
lex pytest -m creation                   # only tests in the "creation" group
lex pytest -m "creation or validation"   # union of two groups
lex pytest -m "not slow"                 # exclude a group
```

Test groups, report recipients and the tests entry point are configured in `lex_test_config.yaml` at your project root. Use `lex pytest-groups` to inspect what groups are registered and which tests belong to each.

Without that file `lex pytest` refuses to run. Its message suggests copying
`lex_test_config.yaml.example`, which lex-app does not ship; start from the
example [below](#lex_test_configyaml-at-a-glance) instead. In a pipeline,
`LEX_TEST_CONFIG_PAYLOAD` can carry the whole configuration as one JSON object
with the same keys; when it is set, the file is not read.

### Running tests in PyCharm or VS Code

In any project with a `lex_test_config.yaml` (or a `LEX_TEST_CONFIG_PAYLOAD`), a plain `pytest` run gets the same setup as `lex pytest`, so your IDE's own test runner works: PyCharm's test tree, the run button next to each test, **Run pytest in …** and rerunning failed tests, with nothing to add to your project. The setup comes from a pytest plugin that lex-app installs; pass `-p no:lex` to switch it off for a run. The plugin refuses to run alongside pytest-django, since both set up Django's test environment: add `-p no:django` to your pytest options, or uninstall pytest-django.

`lex setup` (or `lex-generate-configs`) writes a **Tests** run configuration for the whole suite and one for each group, named after it (**Tests: invoices**, for example), for PyCharm and for VS Code. They set `CELERY_ACTIVE=False`, so calculations run in the test process instead of waiting for a Celery worker.

Keep using `lex pytest` for `--report` and `--report-and-email`, and in CI.

> [!tip]
> If your project copied this setup into its own `conftest.py` to get PyCharm's test tree, it keeps working after you upgrade — the built-in setup runs first and your copy stands down — and you can delete it.

### `lex_test_config.yaml` at a glance

The file is a small YAML document at your project root:

| Key                 | Purpose                                                                                                                                                                                                                                                               |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests_entrypoint`  | Required. The directory `lex pytest` collects tests from when you name no target, relative to the project root. It must exist.                                                                                                                                        |
| `groups`            | Required, at least one. A list of `name` / `description` entries. Each name must be a Python identifier — letters, digits and underscores, not starting with a digit — and unique. Every name is a pytest marker: `@pytest.mark.<name>` puts a test in the group and `lex pytest -m <name>` runs it. A marker that is not a group stops the run. A group can list its own `receivers`. |
| `receivers`         | Who receives the PDF report from `--report-and-email`: a list of `name` / `email` entries.                                                                                                                                                                            |
| `email`             | The report email's `from_email` (required for `--report-and-email`), `from_name`, `reply_to` and `subject_prefix`.                                                                                                                                                    |
| `report.output_dir` | Where the PDF report and the coverage HTML are written, relative to the project root. Default `reports`.                                                                                                                                                              |
| `group_assignments` | Optional. Maps a test's node id (`tests/test_invoices.py::test_post`) to a group name. In the report, that test counts for that group alone whatever its markers say, and a marker on it that is not a group is no error. What `-m` selects still follows the markers. |

```yaml title="lex_test_config.yaml"
tests_entrypoint: tests
groups:
  - name: invoices
    description: Invoice creation and posting
  - name: year_end
    description: The year-end rollover
receivers: []
report:
  output_dir: reports
```

> [!note]
> `--report` (and `--report-and-email`) require coverage data. If coverage cannot be collected, the command stops with an error instead of producing a report with missing coverage.

## Keycloak Commands

| Command                           | What It Does                                                                                                                                                                                                   |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `lex init`                        | Sync models to Keycloak (also applies migrations)                                                                                                                                                              |
| `lex sync_keycloak`               | Sync models, fields and permissions to Keycloak without running migrations. Reads the JSON that `lex detect_model_changes` writes.                                                                             |
| `lex detect_model_changes`        | Detect model adds, deletes and renames via the migration autodetector, and check which models Keycloak is missing. Writes JSON for `lex sync_keycloak` — the two together are what `lex init` runs internally. |
| `lex bootstrap_keycloak`          | Run the first-time Keycloak realm/client bootstrap flow (same flow `lex init --bootstrap` opens)                                                                                                               |
| `lex register_keycloak_resources` | Register every Django model as a Keycloak UMA resource, create a client role per resource scope, and wire up the role policies and scope permissions                                                           |
| `lex delete_keycloak_resources`   | The inverse: remove the UMA resources, client roles and scope permissions previously registered for each model                                                                                                 |
| `lex keycloak_backup`             | Back up, list and restore Keycloak authorization configuration — see [[ship-and-operate/backup and restore]]                                                                                                   |
| `lex createprofiles`              | Create a `Profile` row for any user missing one. Run it after importing users directly into the database rather than through the normal login flow.                                                            |

Most projects only ever need `lex init`. The rest are the pieces it is built
from, useful when you want one step without the others — a permission sync with
no migration, or a re-register after editing the realm by hand.

### Running `lex init`'s steps one at a time

`lex detect_model_changes` writes what it found to `.model_changes.json` in the
**current directory**, and `lex sync_keycloak` reads it from there: run the two
from the same place, or pass the path.

| Command | Flag | Default | Meaning |
| --- | --- | --- | --- |
| `lex detect_model_changes` | `--output PATH` | `.model_changes.json` | Where to write the JSON. |
| | `--no-check-missing` | off | Do not ask Keycloak which models it lacks; write the model changes only. |
| `lex sync_keycloak` | `--input PATH` | `.model_changes.json` | The JSON to apply. |
| | `--dry-run` | off | Print the changes and stop. |
| | `--ensure-default-authz` | off | As for `lex init`. |
| | `--preserve-renamed-permissions` | on | As for `lex init`, except that here it cannot be switched off. |
| `lex bootstrap_keycloak` | `--timeout SECONDS` | `900` | How long to wait for the browser flow. |
| | `--poll-interval SECONDS` | `3` | How often to check whether it finished. |
| | `--no-browser` | off | Do not open a browser; the URL is printed either way. |

`lex sync_keycloak` reports a missing or unreadable file, and a sync that
failed, but exits 0 all the same — read what it printed.
`lex bootstrap_keycloak` does nothing when the Keycloak credentials are already
set; otherwise it writes them to `.env` in the current directory, so run it from
the project root.

> [!note]
> The standalone `lex-generate-configs` console script (note the hyphen, not `lex generate-configs`) regenerates the PyCharm run configurations under `.run/` and VS Code launch configurations under `.vscode/launch.json`. You usually don't need to call it directly — `lex setup` and `lex setup-with-ai` run it for you. There is no `lex generate-configs` subcommand.

## Sign-in Commands

These come with django-oauth2-codeflow, Django's session framework and lex-app; the CLI
runs them like any other management command. See
[[access-and-dashboards/sign-in and sessions#Operating it|Sign-in & Sessions]].

| Command | What It Does |
| ------- | ------------ |
| `lex oidc_check` | Check the sign-in configuration against Keycloak: the endpoints in use (public or `KEYCLOAK_INTERNAL_URL`), that the issuer is the public one, the signing keys, and that the token endpoint answers. Exits non-zero on a problem; `--json` for machines. |
| `lex oidc_cleanup` | End sign-ins whose refresh token expired or whose session is gone, and delete ended ones after `--ended-older-than` days (default 7). Run it daily, with `lex clearsessions`; `--dry-run` shows what it would do. |
| `lex clearsessions` | Django's own: delete expired sessions from the database. |
| `lex oidc_rotate_keys` | Re-encrypt every stored token with the first key of `OIDC_TOKEN_ENCRYPTION_KEYS`. Run it after putting a new key first, before removing the old one. |
| `lex oidc_urls <host> --secure` | Print the URLs to enter on the Keycloak client: redirect URI, post-logout redirect URI and back-channel logout URL. |
| `lex prune_dashboard_sessions` | Delete dashboard sign-in records that can never be used again: expired hand-off codes, and dashboard or embed sign-ins that were signed out, whose app sign-in ended, whose token expired, or whose cookie has expired in every browser. Safe at any time; run it daily with `lex oidc_cleanup`. `--dry-run` counts without deleting. |

## Database Commands

| Command                         | What It Does                                                                                                                                                                                                                                  |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `lex create_db`                 | Create the PostgreSQL database if it doesn't exist yet; does nothing on SQLite. When it cannot connect, it prints the error and still exits 0 |
| `lex flush`                     | Delete every row in the database, after asking you to confirm (`--noinput` skips the question). The next `lex start` loads your initial data again                                                                                            |
| `lex migrate`                   | Apply pending Django migrations, then create the cache tables if they are missing (no separate `createcachetable` needed on a fresh database)                                                                                                 |
| `lex makemigrations`            | Create new migration files from model changes                                                                                                                                                                                                 |
| `lex sqlflush`                  | Print SQL statements to flush the database                                                                                                                                                                                                    |
| `lex lex_migrate`               | `makemigrations` for every installed app — not only your project's, as `lex init` does — then, when something is pending, `migrate` and `createcachetable`. `--no-makemigrations` applies the existing migration files only: not safe on an upgrade that changes a framework field your models use, because the migration your app needs is then never generated — see [[ship-and-operate/upgrading#Migrations your application has to generate\|Upgrading]]. `--migration-verbosity N` (default `1`). It exits 0 even when a step fails, printing `✗ Migration failed: …` — read its output. |
| `lex rebase_incident_datetimes` | Re-anchor user-entered datetimes that were mis-stored during the TIME_ZONE incident (see below).                                                                                                                                              |

### Migration snapshots and rollback

Take a snapshot before a migration you are not sure about, and you have a way back.

| Command | What It Does |
| --- | --- |
| `lex capture_migration_state`         | Write the current migration target of every app to a JSON file (default `.lex_migration_state_before.json`).                        |
| `lex rollback_migration_state`        | Migrate every app back to the targets in that file. `--dry-run` prints the plan without applying it.                                |
| `lex capture_db_tables`               | Snapshot the physical table names in the database (default `.lex_tables_before.json`), for diffing after a migration.               |
| `lex generate_legacy_freeze_manifest` | Diff a `capture_db_tables` snapshot against the current V2 model tables to produce the list of legacy tables nothing owns any more. |
| `lex full_migration_workflow`         | Run the whole V1→V2 database migration sequence with one stable interface, from any working directory: it takes the table snapshot, prepares the V1 migration files, runs `makemigrations` and `migrate`, writes the legacy-freeze manifest and runs the three backfills below. See [[migrating-from-v1/migrating the database\|Migrating the database]]. |

> [!warning]
> `rollback_migration_state` reverses **schema** migrations. It does not restore
> data a migration deleted or transformed. Take a database backup as well — see
> [[ship-and-operate/backup and restore]].

### One-off data backfills

These exist for instances that predate a feature and need their historical rows
filled in. They are idempotent, chunked, and dry-run capable — run them with
`--dry-run` first and read the report.

| Command                           | What It Does                                                                                                                                                                                                                                  |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `lex backfill_bitemporal_history` | Build `History` and `MetaHistory` rows for records that existed before bitemporal history was switched on. `--timestamp` sets the single `valid_from`/`sys_from` used for every backfilled row. See [[history-and-audit/bitemporal history]]. |
| `lex backfill_audit_logging`      | Populate the audit-logging tables from the legacy V1 archive tables. Refuses to run if the audit tables already hold rows unless you pass `--force`. See [[history-and-audit/audit logs]].                                                    |
| `lex normalize_is_calculated`     | Convert boolean-ish `is_calculated` values on `CalculationModel` subclasses to the current `SUCCESS` / `NOT_CALCULATED` states. Needed once, on instances that predate the state machine.                                                     |

Common flags across all three: `--dry-run` (report, write nothing),
`--chunk-size` (rows per iteration, default `500`), and for the two backfills
`--reason`, which is recorded on every row they create so the backfill is
distinguishable from real user activity later.

### `lex rebase_incident_datetimes`

If your instance was running between the rc212 deployment and the v2.1.4 fix, user-entered datetime values may have been stored in UTC when they should have been stored in local time. This command corrects them.

It's a **dry-run by default** — it prints what it would change without writing anything. Pass `--apply` to write the correction.

```bash
# Check what would be corrected (dry-run)
lex rebase_incident_datetimes --cutoff 2026-07-10T00:00:00+00:00

# Apply the correction
lex rebase_incident_datetimes --cutoff 2026-07-10T00:00:00+00:00 --apply
```

| Flag            | Purpose                                                                                                                                                                    |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--cutoff`      | **Required.** ISO-8601 timestamp for when your instance upgraded to ≥rc212 (the moment the bug started). Rows created before this are left untouched.                      |
| `--until`       | ISO-8601 timestamp for when your instance deployed the v2.1.4 fix. Defaults to now, which is correct if you run the command at the same maintenance window as the upgrade. |
| `--source-zone` | IANA zone the users' wall-clocks were in (default: `Europe/Berlin`).                                                                                                       |
| `--models`      | Limit to specific models, e.g. `myapp.MyModel`. Default: all customer models.                                                                                              |
| `--apply`       | Write the correction. Without this flag the command is a dry-run.                                                                                                          |

> [!warning]
> Set `--cutoff` carefully. A cutoff that is too early will re-anchor rows that were already correct and corrupt good data. When in doubt, use a later cutoff and re-run — an uncorrected row is easier to fix than a double-corrected one. Run exactly once per instance.

> [!note]
> Only applies to PostgreSQL deployments. Framework-managed timestamps (`created_at`, `edited_at`) are never touched.

## Async / Celery Commands

| Command | What It Does |
| --- | --- |
| `lex celery`                  | Run a raw Celery command (forwards everything after it to `celery`) in a process that has Django set up. Unlike `lex celery-workers`, it sets no environment variables — see [[calculations/celery and async calculations\|Celery & async calculations]] for the full worker invocation. |
| `lex celery-workers N`        | Start N standard workers — see [below](#lex-celery-workers) |
| `lex flower`                  | Launch [Flower](https://flower.readthedocs.io/), the Celery monitoring dashboard, against the configured broker                                                                                                                                                                 |
| `lex run_recovery_supervisor` | Run the worker-recovery loop in the foreground: detect workers that stopped sending heartbeats and requeue the tasks they were holding. `--once` does a single sweep and exits, which is the form to use from cron. `--interval` overrides `LEX_TASK_SUPERVISOR_SCAN_INTERVAL`. |

> [!note]
> If you use worker recovery, two standalone console scripts live outside the `lex` command tree: `lex-recovery-supervisor` (the always-on sweep loop) and `lex-recovery-beat` (the admin-scheduled recovery worker). See [[calculations/celery and async calculations|Celery & async calculations]].

### `lex celery-workers`

```bash
lex celery-workers 4
```

The count is a plain argument, at least 1. Each worker is its own process,
started from the project root as

```bash
python -m lex celery -A lex_app worker --loglevel=info --concurrency=1 --prefetch-multiplier=1 -n worker<N>@%h
```

with `-P threads` added on macOS and Windows, and with
`IS_RUNNING_IN_CELERY=true` and `CELERY_ACTIVE=true` in its environment.
Anything after the count is appended to every worker's command line. The
workers live and die together: when one exits, the others are stopped and the
command exits with that worker's exit code. Ctrl-C stops them all. The
generated **Celery Workers** run configurations ask for the count.

## Scheduling Commands

| Command | What It Does |
|---|---|
| `lex run_scheduler` | Run the [[calculations/scheduled jobs\|scheduled-jobs]] clock in the foreground until stopped (SIGTERM or Ctrl-C). `--once` syncs the `@lex_job` schedules, starts what is due, waits for those runs and exits — the form for cron. `--interval N` sets the seconds between ticks (default `LEX_SCHEDULER_TICK_SECONDS`). `--no-sync` skips creating and updating the schedules declared with `@lex_job(schedule=...)`. |

The backend runs this clock by itself, so most instances never need the command. Running it next to the backend is safe: each slot still runs once.

## AI Commands

| Command               | What It Does                                                                                                                                |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `lex setup-with-ai`   | Set up the LEX AI integration for one or more agentic environments — GitHub Copilot in PyCharm or VS Code, Copilot CLI, Cursor, Claude Code, Codex, Windsurf — with the remote MCP server |
| `lex ai-update`       | Upgrade lex-mcp-local and apply all pending updates to an existing LEX AI setup                                                             |
| `lex ai-dashboard`    | Open a local web dashboard to switch MCP mode, update credentials (GitHub token, remote API key, remote MCP URL), and inspect server status |
| `lex ai-verify`       | Verify that required AI asset files are present and restore any that are missing or have drifted                                            |
| `lex ai-faq`          | Open the LEX AI FAQ page in your browser (including the Prompt Builder and the Lex AI behavior map)                                         |
| `lex ai-issue-report` | Generate a zip bundle of the current AI setup for support triage                                                                            |
| `lex ai-worktree`     | Manage a parallel git worktree for AI-assisted development                                                                                  |

`lex setup-with-ai` does what `lex setup` does, then asks for a GitHub token and a remote MCP API key on a local setup page in your browser, installs `lex-mcp-local`, writes the AI entries to your `.env` (including `LEX_MCP_ANALYTICS_BACKEND=remote`), registers the MCP server in the configuration of each environment you chose, and delivers the agent files for them. Last, it verifies that all required AI asset directories (`.github`, etc.) are present and restores any that are missing.

| Flag | Meaning |
| --- | --- |
| `-p`, `--project-root PATH` | The project folder. Default: the directory you run the command from. |
| `--github-token TOKEN` | The GitHub token. Give `--remote-mcp-api-key` as well and no page or prompt appears. |
| `--remote-mcp-api-key KEY` | The API key for the remote MCP server; it also authorises the download of `lex-mcp-local`. |
| `--remote-mcp-url URL` | The remote MCP endpoint. Default `https://mcp.excellence-cloud.de/mcp`. |
| `-e`, `--environment NAME` | An environment to set up; repeat the flag for several: `pycharm-copilot`, `vscode-copilot`, `copilot-cli`, `cursor`, `claude-code`, `codex`, `windsurf`, or `all`. Without it the setup page offers them all, with the tools it finds on this machine ticked; with `--no-browser` it is `pycharm-copilot`. An unknown name stops the command. |
| `--list-environments` | Print the supported environments, with the other names each accepts, and exit. |
| `--no-browser` | Ask in the terminal instead of opening the setup page. |
| `--mcp-mode MODE` | Accepted, but in 2.5.0 it does not decide the mode. The setup page's choice does — `brief` unless you unlock the others — and without the page (both credentials given as flags, or `--no-browser`) the mode is `forward`. Switch it afterwards with `lex ai-dashboard`. |

`lex setup-with-ai` and `lex ai-verify` use the directory you pass via `--project-root` (or your current directory) directly — they don't walk up to a parent folder automatically.

`lex ai-update` upgrades lex-mcp-local to the latest version, then hands off to the newly-installed package to apply any migration steps. What the update actually does — and what it reports — lives in lex-mcp-local, so new migrations reach you without needing a lex-app release. If the update step fails, the command exits non-zero and shows the error.

`lex ai-dashboard` opens a browser page where you can switch between MCP modes, update your GitHub token, remote MCP API key, and remote MCP URL, and see the current server status. Mode changes are instant — the server restarts itself and the IDE picks up the new tool surface automatically. Credential changes also restart the MCP server, so new values take effect immediately.

`lex ai-faq` opens an in-browser FAQ that now includes an interactive **Prompt Builder** — pick a scenario (add a feature, revise a plan, start a new project, or auto-generate docs for an existing project) and it crafts the prompt for you — alongside a visual **Lex AI behavior map** that walks through each workflow as a step-by-step timeline.

`lex ai-verify` checks the AI asset files for the active MCP mode and restores any that are missing or out of date. It resolves the active mode from the CLI `--mode` flag, then the override file, then your `.env`, then `mcp.json`, defaulting to `brief`. Pass `--silent` to suppress all output on success — useful in automated or MCP pre-flight contexts. Pass `--strict` to exit non-zero when verification reports a problem it recovered from or warned about — useful in CI, where a typo in `-e` should be a failure rather than a warning. Pass `--align-mcp-mode` to also reconcile the running MCP server with the mode recorded in your `.env` (the default for interactive runs; disabled automatically under `--silent` so it can't interrupt a live MCP tool call).

`lex ai-issue-report` captures MCP configs, logs, and private Copilot conversation artifacts into a zip bundle for LEX support triage. Credential values are masked before anything is written. Pass `--yes` to skip the confirmation prompt. The old underscore spelling (`lex ai_issue_report`) still works as a hidden alias.

> [!note]
> `lex ai-update`, `lex ai-verify`, `lex ai-dashboard`, `lex ai-faq`, and `lex ai-issue-report` require the `lex-mcp-local` package, which `lex setup-with-ai` installs for you. Run one of them before completing setup and it will tell you to run `lex setup-with-ai` first.

`lex ai-worktree` sets up a parallel git worktree so an AI agent can work on a branch without disturbing your current workspace.

All `lex ai-*` commands (other than `setup-with-ai` and `ai-update`) are implemented by the installed `lex-mcp-local` package, which owns their flags and help text. This means new AI commands become available as soon as you run `lex ai-update` — no `lex-app` upgrade required. If a command isn't available, `lex ai-update` is the first thing to try.

## Commands this page leaves out

Five management commands ship with the framework and are deliberately not
documented above. They are listed here so the omission is a decision rather
than a gap:

| Command                                               | Why not                                                                                                                                                                                                                                                                                |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `lex Init2`                                           | An older variant of `lex init`. Nothing in the framework calls it and it is not maintained. Use `lex init`.                                                                                                                                                                            |
| `lex keycloak_init_bak` / `lex keycloak_rollback_bak` | Superseded by `lex register_keycloak_resources` and `lex delete_keycloak_resources`. The `_bak` suffix is what it looks like.                                                                                                                                                          |
| `lex bootstrap_callback_server`                       | An internal helper that `lex bootstrap_keycloak` starts to receive the browser callback. Running it directly does nothing useful.                                                                                                                                                      |
| `lex _keycloak_env`                                   | Not a command at all. `_keycloak_env.py` is the shared helper that `lex register_keycloak_resources` and `lex delete_keycloak_resources` both import for their Keycloak connection; it sits in `management/commands/`, so Django's discovery lists it as one. Running it does nothing. |

> [!note]
> `lex --help` prints only the eleven commands the CLI implements itself, plus
> the `ai-*` commands of `lex-mcp-local` once that is installed; the
> Django passthrough commands do not appear there, because listing them would
> mean starting Django just to render help. This page is the complete list, and
> CI checks that it stays complete — a new management command with no entry here
> fails the build.

## Where the CLI looks for the project

The CLI takes the nearest folder at or above the current directory that holds `lex_config.py` as the project root, before falling back to the git top level. A Lex project in a subfolder of a larger repository (a monorepo) therefore runs with plain `lex <command>` from that subfolder — no `PROJECT_ROOT` or `GIT_CEILING_DIRECTORIES` needed.

The full order:

1. the nearest folder at or above the current directory that holds `lex_config.py`;
2. the git top level;
3. the nearest folder at or above the current directory that holds `.git`, `pyproject.toml`, `setup.cfg`, `manage.py`, `requirements.txt`, `.idea` or `.vscode`;
4. the current directory itself.

The searches upward stop at your home directory, and a git top level that is your home directory, or above it, does not count. Whatever is found, `lex` loads that folder's `.env` and sets `PROJECT_ROOT` to it for every process it starts, unless `PROJECT_ROOT` is already set. `lex setup-with-ai` and `lex ai-verify` are the exceptions: they use `--project-root` or the current directory as given.

## Usage Pattern

We recommend using the IDE run configurations generated by `lex setup` — both PyCharm (`.run/`) and VS Code (`.vscode/launch.json`). In a terminal, run commands from the project folder or any folder below it, on Linux, macOS and Windows alike:

```bash
lex init
lex start
```

There is nothing to load first. Every `lex` command loads `<project root>/.env` itself, before anything else runs, and a variable already set in your environment wins over the file. So do not `source .env` into your shell: a value exported that way stays in the shell and silently overrides later edits to the file. To change a value for one run, set it on the command line, as in `LOG_LEVEL=DEBUG lex start`. How the file is read is in [[reference/Environment Variables#Where these get set|Environment Variables]].
