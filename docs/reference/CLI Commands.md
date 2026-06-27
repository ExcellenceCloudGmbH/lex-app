---
title: CLI Commands
---

Lex App ships with a `lex` CLI tool for managing your application. Here's every command at a glance.

## Everyday Commands

| Command         | What It Does                                                  |
| --------------- | ------------------------------------------------------------- |
| `lex setup`     | Generate `.run/`, `.env`, and `migrations/` for a new project |
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

`lex pytest` behaves like plain `pytest` — any flags you pass are forwarded directly. Two extra flags are intercepted:

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
> The standalone `lex-generate-configs` console script (note the hyphen, not `lex generate-configs`) regenerates the PyCharm run configurations under `.run/`. You usually don't need to call it directly — `lex setup` and `lex setup-with-ai` run it for you. There is no `lex generate-configs` subcommand.

## Database Commands

| Command              | What It Does                                  |
| -------------------- | --------------------------------------------- |
| `lex create_db`      | Create the project database (from the env vars in your `.env`) |
| `lex migrate`        | Apply pending Django migrations               |
| `lex makemigrations` | Create new migration files from model changes |
| `lex sqlflush`       | Print SQL statements to flush the database    |

## Async / Celery Commands

| Command              | What It Does                                                       |
| -------------------- | ------------------------------------------------------------------ |
| `lex celery`         | Run a raw Celery command (forwards everything after it to `celery`). Used to start workers — see [[features/processing/celery and async calculations|Celery & async calculations]] for the full worker invocation. |
| `lex celery-workers` | Start the standard worker pool with the framework's default settings |
| `lex flower`         | Launch [Flower](https://flower.readthedocs.io/), the Celery monitoring dashboard, against the configured broker |

## AI Commands

| Command | What It Does |
|---|---|
| `lex setup-with-ai` | Configure LEX AI integration (GitHub Copilot MCP, remote MCP server) |
| `lex ai-update` | Apply incremental updates to an existing LEX AI setup (e.g. remove stale config keys) |
| `lex ai-dashboard` | Open a local web dashboard to switch MCP mode, update credentials, and inspect server status |
| `lex ai-verify` | Verify that required AI asset files are present and restore any that are missing or have drifted |
| `lex ai-faq` | Open the LEX AI FAQ page in your browser |

`lex setup-with-ai` prompts for a GitHub token and a remote MCP API key, then writes the necessary entries to your `.env` and `mcp.json` (including `LEX_MCP_ANALYTICS_BACKEND=remote`). It also verifies that all required AI asset directories (docs, `.github`, etc.) are present and restores any that are missing. If no project markers are found, it uses the directory you ran the command from (it won't jump up to your home folder). It also refreshes the AI docs folder in your project (`docs/`) from the version shipped with your installed `lex-app` package.

`lex setup-with-ai` and `lex ai-verify` use the directory you pass via `--project-root` (or your current directory) directly — they don't walk up to a parent folder automatically.

`lex ai-update` is safe to run at any time — it only removes keys that are no longer needed, restores missing AI asset folders, and reports exactly what it changed.

`lex ai-dashboard` opens a browser page where you can switch between forward and backward MCP mode, update your GitHub token and remote MCP API key, and see the current server status. With lex-mcp-local ≥ 0.2.3, mode changes are instant — the server restarts itself and the IDE picks up the new tool surface automatically. On save, the dashboard invokes the same `switch_to_mode` primitives the MCP server's tool uses (override marker + `.env` + `mcp.json` + IDE cache sync), then runs `lex ai-verify` for the new mode so every required asset is in place.

`lex ai-verify` checks the AI asset files for the active MCP mode and restores any that are missing or out of date. The project `.env` `LEX_MCP_MODE` is treated as the **source of truth**: if the running MCP server / `mcp.json` disagree, ai-verify invokes the in-server `switch_to_mode` behaviour (via the `lex_mcp.mode_switch` primitives) to realign them before verifying. This auto-align is enabled by default for interactive runs and disabled under `--silent` so MCP pre-flight calls cannot loop the server. Use `--no-align-mcp-mode` to opt out explicitly, or `--mode` to pin a target mode. Pass `--silent` to suppress all output on success.

## Usage Pattern

We recommend using PyCharm's run configurations (Init, Start, Streamlit) which auto-load `.env` for you. If you prefer the terminal:

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



