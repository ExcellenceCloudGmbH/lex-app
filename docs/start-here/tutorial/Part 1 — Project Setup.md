---
title: "Part 1 — Project Setup"
aliases:
  - "tutorial/Part 1 — Project Setup"
---

In this first part, you'll create a new Lex App project, configure your environment, and verify everything works. By the end, you'll have a running (empty) Lex App application — ready for your models.

## Create a Project Folder

```bash
mkdir -p ~/Projects/TeamBudget && cd ~/Projects/TeamBudget
```

> [!note]- Windows alternative
> ```powershell
> mkdir C:\Projects\TeamBudget
> cd C:\Projects\TeamBudget
> ```

## Create a Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

You should see `(.venv)` at the start of your prompt.

> [!note]- Windows alternative
> ```powershell
> python -m venv .venv
> .venv\Scripts\activate
> ```
> On some installations you may need to use `py` instead of `python`.

## Create `requirements.txt`

Create a file called `requirements.txt` in your project root:

```
lex-app
pandas
openpyxl
```

Then install:

```bash
pip install -r requirements.txt
```

> [!note]- Windows alternative
> ```powershell
> pip install -r requirements.txt
> ```
> If you encounter any problem in windows try using `python -m pip` on Windows to ensure you're using the virtual-environment pip.

## Run `lex setup`

```bash
lex setup
```

> [!note]- Windows alternative
> ```powershell
> lex setup
> ```

`lex setup` asks nothing — it writes these files and exits:

```
TeamBudget/
├── .env
├── .run/
│   ├── Celery_Worker.run.xml
│   ├── Create_DB.run.xml
│   ├── Flower.run.xml
│   ├── Flush_DB.run.xml
│   ├── Init.run.xml
│   ├── Make_migrations.run.xml
│   ├── Migrate.run.xml
│   ├── Reflex.run.xml
│   ├── Setup_With_AI.run.xml
│   ├── Start.run.xml
│   └── Streamlit.run.xml
└── migrations/
```

The `.run/` folder contains PyCharm run configurations — these are pre-configured and will be the primary way to interact with your project.

> [!note]
> Notice there's no `manage.py` or nested app folder. Lex App uses a **flat layout** — no [Django](https://docs.djangoproject.com/) boilerplate. Your models are organized into folders following the [[start-here/project structure|ETL convention]].

## Open in PyCharm

We recommend using [PyCharm](https://www.jetbrains.com/pycharm/) as your primary IDE — `lex setup` generates ready-made run configurations that handle environment variables automatically.

1. Open PyCharm → **File → Open** → select your TeamBudget folder
2. When prompted, set the Python interpreter to the `python` inside your `.venv`

You'll see the run configurations appear in the top-right dropdown:

> [!example]- 🎬 Video — The run configurations `lex setup` generates
> <video controls width="100%">
>   <source src="../../videos/pycharm-configs.mp4" type="video/mp4">
> </video>
> Opening the dropdown in the top-right toolbar and picking a configuration.

This tutorial uses five of the eleven:

| Run Configuration | What It Does |
|---|---|
| **Create DB** | Creates the PostgreSQL database if it doesn't exist yet |
| **Init** | Applies migrations and syncs your models' permissions to [Keycloak](https://www.keycloak.org/documentation) |
| **Start** | Runs the development server |
| **Streamlit** | Starts the [Streamlit](https://docs.streamlit.io/) dashboard server |
| **Flush DB** | Deletes every row in the database, after asking — you'll meet it in Part 2 |

[[start-here/running your app#Using PyCharm|Running Your App]] describes all eleven.

## Set Up the ETL Folders

In PyCharm, right-click your project root → **New → Directory** and create three folders: `Input`, `Upload`, and `Reports`. Then create an empty `__init__.py` in each (right-click the folder → **New → Python File** → name it `__init__`).

> [!note]- Terminal alternative
> **Linux / macOS:**
> ```bash
> mkdir Input Upload Reports
> touch Input/__init__.py Upload/__init__.py Reports/__init__.py
> ```
> **Windows PowerShell:**
> ```powershell
> mkdir Input, Upload, Reports
> New-Item Input\__init__.py, Upload\__init__.py, Reports\__init__.py
> ```

Your project now reflects the ETL pattern:

```
TeamBudget/
├── .env
├── .run/
├── migrations/
├── Input/           ← Transform: core business entities will go here
│   └── __init__.py
├── Upload/          ← Extract: data ingestion will go here
│   └── __init__.py
└── Reports/         ← Load: calculations will go here
    └── __init__.py
```

## Create the Database

The app expects PostgreSQL on `localhost:5432`, reached as the user `django`
with the password `lundadminlocal`. Create that user once if you haven't — see
[[start-here/installation#Choose a Database|Choose a Database]]. Then select
**"Create DB"** from the run configuration dropdown → click ▶️. It creates
`db_teambudget`, or tells you it already exists.

> [!note]- Terminal alternative
> ```bash
> lex create_db
> ```

> [!tip]- No PostgreSQL? Use SQLite
> Add `DATABASE_DEPLOYMENT_TARGET=local` to `.env`. The database becomes a
> file, `TeamBudget.sqlite3`, which the next step creates; **Create DB** has
> nothing to do and says so.

## Initialize

Select **"Init"** from the run configuration dropdown in PyCharm → click ▶️.

This runs [Django](https://docs.djangoproject.com/) migrations and syncs your models' permissions to [Keycloak](https://www.keycloak.org/documentation). The output is long; these are the lines to look for:

```
Django Migration + Keycloak Authorization Sync
...
Applying unapplied migrations...
  Applying contenttypes.0001_initial... OK
  Applying auth.0001_initial... OK
  ...
✓ Django migrations completed successfully
```

> [!note]- Terminal alternative
> **Linux / macOS:**
> ```bash
> set -a; source .env; set +a
> lex init
> ```
> **Windows PowerShell:**
> ```powershell
> Get-Content .env | ForEach-Object {
>     if ($_ -match '^([^=]+)=(.*)$') {
>         [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2])
>     }
> }
> lex init
> ```
> PyCharm's run configurations auto-load `.env` for you — this is why we recommend using PyCharm.

## Verify It Works

Select **"Start"** from the run configuration dropdown → click ▶️.

Open `http://localhost:8000` in your browser. You're sent to Keycloak first: sign in with your Excellence Cloud account and you land in the Lex App interface — none of your own models in it yet, but working. The frontend uses [AG Grid](https://www.ag-grid.com/) for data tables, which you'll see populated once you add models.

> [!note]- Terminal alternative
> **Linux / macOS:**
> ```bash
> set -a; source .env; set +a
> lex start --reload --loop asyncio lex_app.asgi:application
> ```
> **Windows PowerShell:**
> ```powershell
> lex start --reload --loop asyncio lex_app.asgi:application
> ```
> Press `Ctrl+C` to stop.

## Checkpoint

At this point you have:
- A working Lex App project with `Input/`, `Upload/`, and `Reports/` folders
- PyCharm run configurations ready
- Database created and migrations applied
- Server starts, and you can sign in

Next up: [[start-here/tutorial/Part 2 — Data Models|Part 2 — Data Models]] where you'll define the Team, Employee, and Expense models in `Input/`, plus upload models for CSV ingestion in `Upload/`.
