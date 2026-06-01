---
title: Installation
---

Install the `lex-app` package. This gives you the `lex` CLI tool and all framework dependencies.

```bash
pip install lex-app
```

Verify it worked:

```bash
lex --version
```

> [!tip]
> Make sure you're using **Python 3.12**. Check with `python3.12 --version`.

## Run the Setup Wizard

Navigate to your project directory and run:

```bash
lex setup
```

This generates three things for you:

- `.run/` — PyCharm run configurations (Init, Start, Streamlit)
- `.env` — Environment configuration template
- `migrations/` — Django migrations folder

## Configure Your Environment

The `.env` file is the **single source of truth** for runtime configuration.

### Option A: Automatic (Recommended)

Run `lex setup` and follow the prompts. The wizard will open [Excellence Cloud](https://excellence-cloud.de) for you, guide you through creating a new Client, and auto-populate your `.env` file.

### Option B: Manual

1. Log in to [Excellence Cloud](https://excellence-cloud.de)
2. Go to Clients and Click Create a new Client
3. Select client type → **development**
4. Enable **confidential**
5. Copy the credentials into your `.env`:

```env
KEYCLOAK_URL=keycloak_url
KEYCLOAK_REALM=keycloak_realm
OIDC_RP_CLIENT_ID=your_client_id
OIDC_RP_CLIENT_SECRET=your_client_secret
OIDC_RP_CLIENT_UUID=your_client_uuid
```

## Initialize the Application

`lex Init` is the primary initialization command. It does three things:

1. **Applies migrations** — creates/updates database tables from your models
2. **Syncs to Keycloak** — registers your project models as "Resources" and permissions as "Scopes"
3. **Enables access management** — you can now manage permissions on [Excellence Cloud](https://excellence-cloud.de)

### Via PyCharm (easiest)

1. Open the **Run Configuration** dropdown (top-right toolbar)
2. Select **"Init"**
3. Click the green ▶️ Run button (or press `Shift+F10`)

PyCharm automatically loads your `.env` file.

### Via Terminal

```bash
# Load environment variables first
set -a; source .env; set +a

# Then initialize
lex Init
```

> [!tip]
> If your Keycloak credentials are still missing, run `lex Init --bootstrap` instead. It opens the browser-based setup flow and then continues with the normal init steps.

> [!note]- Windows (PowerShell)
>
> ```powershell
> # Load environment variables
> Get-Content .env | ForEach-Object {
>     if ($_ -match '^([^=]+)=(.*)$') {
>         [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2])
>     }
> }
>
> # Initialize
> lex Init
> ```

> [!important]
> **When to run `lex Init` again:** Whenever you add a new model, new field, or change permission methods — any change that creates a new migration file.

## Start the Dev Server

### Via PyCharm

Select **"Start"** from the Run Configuration dropdown → click ▶️.

### Via Terminal

```bash
set -a; source .env; set +a
lex start --reload --loop asyncio lex_app.asgi:application
```

Your application is now running at `http://localhost:8000`.

## Troubleshooting

> [!warning]- "Environment variable not set" errors
>
> - **PyCharm:** Make sure `.env` is in your project root
> - **Terminal:** Always run `set -a; source .env; set +a` before any `lex` command

> [!warning]- "ModuleNotFoundError: lex" errors
>
> - Make sure `lex-app` is installed: `pip install lex-app`
> - Verify you're using Python 3.12
> - Check that your virtual environment is activated

> [!warning]- Keycloak connection errors
>
> - Verify `KEYCLOAK_URL` in your `.env`
> - Check that `OIDC_RP_CLIENT_ID` and `OIDC_RP_CLIENT_SECRET` are correct
> - Confirm your client exists on [Excellence Cloud](https://excellence-cloud.de)

> [!warning]- "`lex Init` refuses to sync this client"
>
> - In local development, `lex Init` expects a **confidential** Keycloak client with a `localhost` redirect URI
> - If you're intentionally using a different setup, rerun with `lex Init --skip-client-preflight`

Got everything set up? Learn about the [[project structure]] or jump straight to [[running your app]].
