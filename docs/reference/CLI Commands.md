---
title: CLI Commands
---

Lex App ships with a `lex` CLI tool for managing your application. Here's every command at a glance.

![What `lex --help` prints — ten commands, not the whole set](../images/cli/lex-help.svg)

> [!important] This page is longer than `lex --help` on purpose
> `--help` lists only the commands the CLI implements itself. Everything below
> that is a Django management command works but does not appear there, because
> printing it would mean starting Django just to render help.

## Everyday Commands

| Command         | What It Does                                                  |
| --------------- | ------------------------------------------------------------- |
| `lex setup`     | Generate `.run/`, `.vscode/launch.json`, `.env`, and `migrations/` for a new project |
| `lex Init`      | Apply migrations + sync models/permissions to Keycloak        |
| `lex start`     | Start the development server                                  |
| `lex streamlit` | Start the [Streamlit](https://docs.streamlit.io/) dashboard server |
| `lex create_db` | Create the project database from the configured `DATABASE_*` env vars |
| `lex --version` | Print the installed `lex-app` version                         |

`lex Init` has two setup-focused flags worth knowing:

- `--bootstrap` — open the browser bootstrap flow if Keycloak credentials are missing
- `--skip-client-preflight` — bypass the local Keycloak client safety check when you're intentionally managing that setup yourself

### `lex start` flags

`lex start` wraps the ASGI server. The Quick Start in [[getting started|Getting Started]] uses:

```bash
lex start --reload --loop asyncio lex_app.asgi:application
```

| Flag / argument | Meaning |
|---|---|
| `--reload` | Restart the server when source files change. Use during development only. |
| `--loop asyncio` | Force the standard library asyncio event loop instead of `uvloop`. Recommended for development on Windows or when debugging async code. |
| `lex_app.asgi:application` | The ASGI mount point. This is the framework's entry point — leave it as-is unless you have a custom ASGI app. |

For production runs, drop `--reload`.

## Testing Commands

| Command | What It Does |
|---|---|
| `lex pytest` | Run your project's test suite with Django bootstrapped |
| `lex pytest-groups` | List configured test groups and the tests they contain (no tests run) |

`lex pytest` behaves like plain `pytest` — any flags you pass are forwarded directly. Before running, it prepares Django's test environment and sets up the test database for your `default` alias. If that test DB already exists, `lex pytest` reuses it and runs migrations (`keepdb` behavior); if it's missing (common in CI), it creates it first. If that setup step fails, `lex pytest` stops early and shows the setup error instead of continuing with a partial run.

Two extra flags are intercepted:

| Flag | What It Does |
|---|---|
| `--report` | Generate a branded PDF test report after the run |
| `--report-and-email` | Generate the PDF report and send it to configured recipients |

Use `-m` marker expressions to run only a subset of tests:

```bash
lex pytest -m creation                   # only tests in the "creation" group
lex pytest -m "creation or validation"   # union of two groups
lex pytest -m "not slow"                 # exclude a group
```

Test groups, recipients, and the tests entry point are configured in `lex_test_config.yaml` at your project root. Use `lex pytest-groups` to inspect what groups are registered and which tests belong to each.

### `lex_test_config.yaml` at a glance

The file is a small YAML document at your project root. The most useful keys:

| Key | Purpose |
|---|---|
| `tests_root` | Directory pytest discovers tests in (relative to project root) |
| `groups` | Mapping of group name → list of test files / nodeids. The group name is what you pass to `pytest -m` and what `lex pytest-groups` lists. |
| `report.recipients` | List of email addresses that receive the PDF when `--report-and-email` is passed |
| `report.sender` | "From" address on the report email (falls back to your SendGrid sender) |

> [!note]
> `--report` (and `--report-and-email`) require coverage data. If coverage cannot be collected, the command stops with an error instead of producing a report with missing coverage.

## Keycloak Commands

| Command                  | What It Does                                              |
| ------------------------ | --------------------------------------------------------- |
| `lex Init`               | Sync models to Keycloak (also applies migrations)         |
| `lex sync_keycloak`      | Sync models, fields and permissions to Keycloak without running migrations |
| `lex bootstrap_keycloak` | Run the first-time Keycloak realm/client bootstrap flow (same flow `lex Init --bootstrap` opens) |

> [!note]
> The standalone `lex-generate-configs` console script (note the hyphen, not `lex generate-configs`) regenerates the PyCharm run configurations under `.run/` and VS Code launch configurations under `.vscode/launch.json`. You usually don't need to call it directly — `lex setup` and `lex setup-with-ai` run it for you. There is no `lex generate-configs` subcommand.

## Database Commands

| Command              | What It Does                                  |
| -------------------- | --------------------------------------------- |
| `lex create_db`      | Create the project database (from the env vars in your `.env`) |
| `lex migrate`        | Apply pending Django migrations               |
| `lex makemigrations` | Create new migration files from model changes |
| `lex sqlflush`       | Print SQL statements to flush the database    |
| `lex rebase_incident_datetimes` | Re-anchor user-entered datetimes that were mis-stored during the TIME_ZONE incident (see below). |

### `lex rebase_incident_datetimes`

If your instance was running between the rc212 deployment and the v2.1.4 fix, user-entered datetime values may have been stored in UTC when they should have been stored in local time. This command corrects them.

It's a **dry-run by default** — it prints what it would change without writing anything. Pass `--apply` to write the correction.

```bash
# Check what would be corrected (dry-run)
lex rebase_incident_datetimes --cutoff 2026-07-10T00:00:00+00:00

# Apply the correction
lex rebase_incident_datetimes --cutoff 2026-07-10T00:00:00+00:00 --apply
```

| Flag | Purpose |
|---|---|
| `--cutoff` | **Required.** ISO-8601 timestamp for when your instance upgraded to ≥rc212 (the moment the bug started). Rows created before this are left untouched. |
| `--until` | ISO-8601 timestamp for when your instance deployed the v2.1.4 fix. Defaults to now, which is correct if you run the command at the same maintenance window as the upgrade. |
| `--source-zone` | IANA zone the users' wall-clocks were in (default: `Europe/Berlin`). |
| `--models` | Limit to specific models, e.g. `myapp.MyModel`. Default: all customer models. |
| `--apply` | Write the correction. Without this flag the command is a dry-run. |

> [!warning]
> Set `--cutoff` carefully. A cutoff that is too early will re-anchor rows that were already correct and corrupt good data. When in doubt, use a later cutoff and re-run — an uncorrected row is easier to fix than a double-corrected one. Run exactly once per instance.

> [!note]
> Only applies to PostgreSQL deployments. Framework-managed timestamps (`created_at`, `edited_at`) are never touched.

## Async / Celery Commands

| Command              | What It Does                                                       |
| -------------------- | ------------------------------------------------------------------ |
| `lex celery`         | Run a raw Celery command (forwards everything after it to `celery`). Used to start workers — see [[calculations/celery and async calculations|Celery & async calculations]] for the full worker invocation. |
| `lex celery-workers` | Start the standard worker pool with the framework's default settings |
| `lex flower`         | Launch [Flower](https://flower.readthedocs.io/), the Celery monitoring dashboard, against the configured broker |

> [!note]
> If you use worker recovery, two standalone console scripts live outside the `lex` command tree: `lex-recovery-supervisor` (the always-on sweep loop) and `lex-recovery-beat` (the admin-scheduled recovery worker). See [[calculations/celery and async calculations|Celery & async calculations]].

## AI Commands

| Command | What It Does |
|---|---|
| `lex setup-with-ai` | Configure LEX AI integration (GitHub Copilot MCP, remote MCP server) |
| `lex ai-update` | Upgrade lex-mcp-local and apply all pending updates to an existing LEX AI setup |
| `lex ai-dashboard` | Open a local web dashboard to switch MCP mode, update credentials (GitHub token, remote API key, remote MCP URL), and inspect server status |
| `lex ai-verify` | Verify that required AI asset files are present and restore any that are missing or have drifted |
| `lex ai-faq` | Open the LEX AI FAQ page in your browser (including the Prompt Builder and the Lex AI behavior map) |
| `lex ai-issue-report` | Generate a zip bundle of the current AI setup for support triage |
| `lex ai-worktree` | Manage a parallel git worktree for AI-assisted development |

`lex setup-with-ai` prompts for a GitHub token and a remote MCP API key, then writes the necessary entries to your `.env` and `mcp.json` (including `LEX_MCP_ANALYTICS_BACKEND=remote`). It also verifies that all required AI asset directories (`.github`, etc.) are present and restores any that are missing. If no project markers are found, it uses the directory you ran the command from (it won't jump up to your home folder).

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

## Usage Pattern

We recommend using the IDE run configurations generated by `lex setup` — both PyCharm (`.run/`) and VS Code (`.vscode/launch.json`) — which auto-load `.env` for you. If you prefer the terminal:

**Linux / macOS:**

```bash
# Load environment variables first
set -a; source .env; set +a

# Then run any lex command
lex Init
lex start
```

**Windows PowerShell:**

```powershell
# Load environment variables first
Get-Content .env | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') {
        [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2])
    }
}

# Then run any lex command
lex Init
lex start
```


