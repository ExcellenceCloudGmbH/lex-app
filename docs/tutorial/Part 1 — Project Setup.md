---
title: "Part 1 — Project Setup"
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
> venv .venv
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

## Run the Setup Wizard

```bash
lex setup
```

> [!note]- Windows alternative
> ```powershell
> lex setup
> ```

After the wizard completes, you'll have:

```
TeamBudget/
├── .env
├── .run/
│   ├── Init.run.xml
│   ├── Start.run.xml
│   └── Streamlit.run.xml
└── migrations/
```

The `.run/` folder contains PyCharm run configurations — these are pre-configured and will be the primary way to interact with your project.

> [!note]
> Notice there's no `manage.py` or nested app folder. Lex App uses a **flat layout** — no [Django](https://docs.djangoproject.com/) boilerplate. Your models are organized into folders following the [[project structure|ETL convention]].

## Open in PyCharm

We recommend using [PyCharm](https://www.jetbrains.com/pycharm/) as your primary IDE — the setup wizard generates ready-made run configurations that handle environment variables automatically.

1. Open PyCharm → **File → Open** → select your TeamBudget folder
2. When prompted, set the Python interpreter to the `python` inside your `.venv`

You'll see the run configurations appear in the top-right dropdown:

| Run Configuration | What It Does |
|---|---|
| **Init** | Creates/updates the database and syncs [Keycloak](https://www.keycloak.org/documentation) |
| **Start** | Runs the development server |
| **Streamlit** | Starts the [Streamlit](https://docs.streamlit.io/) dashboard server |

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

In PyCharm's integrated terminal (**View → Tool Windows → Terminal**), run:

```bash
lex create_db
```

> [!note]- Windows alternative
> ```powershell
> lex create_db
> ```

## Initialize

Select **"Init"** from the run configuration dropdown in PyCharm → click ▶️.

This runs [Django](https://docs.djangoproject.com/) migrations and sets up [Keycloak](https://www.keycloak.org/documentation). You should see:

```
Running migrations...
  Applying contenttypes.0001_initial... OK
  Applying auth.0001_initial... OK
  ...
Syncing models to Keycloak... OK
```

> [!note]- Terminal alternative
> **Linux / macOS:**
> ```bash
> set -a; source .env; set +a
> lex Init
> ```
> **Windows PowerShell:**
> ```powershell
> Get-Content .env | ForEach-Object {
>     if ($_ -match '^([^=]+)=(.*)$') {
>         [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2])
>     }
> }
> lex Init
> ```
> PyCharm's run configurations auto-load `.env` for you — this is why we recommend using PyCharm.

## Verify It Works

Select **"Start"** from the run configuration dropdown → click ▶️.

Open `http://localhost:8000` in your browser. You should see the Lex App interface — empty for now, but working. The frontend uses [AG Grid](https://www.ag-grid.com/) for data tables, which you'll see populated once you add models.

> [!note]- Terminal alternative
> **Linux / macOS:**
> ```bash
> set -a; source .env; set +a
> lex start
> ```
> **Windows PowerShell:**
> ```powershell
> lex start
> ```
> Press `Ctrl+C` to stop.

## Checkpoint

At this point you have:
- A working Lex App project with `Input/`, `Upload/`, and `Reports/` folders
- PyCharm run configurations ready (Init, Start, Streamlit)
- Database created and migrations applied
- Server starts without errors

Next up: [[tutorial/Part 2 — Data Models|Part 2 — Data Models]] where you'll define the Team, Employee, and Expense models in `Input/`, plus upload models for CSV ingestion in `Upload/`.
