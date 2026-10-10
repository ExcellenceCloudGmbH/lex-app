---
title: Running & Deploying
---

A Reflex dashboard runs as a process of its own beside the application, the way Streamlit does: `lex start` serves Lex App, `lex reflex` serves the dashboards.

## Running it locally

`lex setup` generates a **Reflex** run configuration for PyCharm (`.run/Reflex.run.xml`) and for VS Code (**LEX: Reflex** in `.vscode/launch.json`), beside the Streamlit one. Pick it and run. From a terminal:

```bash
set -a; source .env; set +a
lex reflex
```

`lex reflex` on its own is `lex reflex run`: the development server, with hot reload. The dashboards are at `http://localhost:8502`, and Reflex's backend listens on `8503` — beside Streamlit's `8501`, and clear of Lex App's `8000`.

Set `IS_REFLEX_ENABLED=true` in `.env` and restart `lex start`, and Lex App's sidebar gains a **Reflex** entry that frames them. `REFLEX_URL` tells it where they are, when that is not `http://localhost:8502`.

> [!important] Sign the machine in to Reflex, once
> Reflex Enterprise is free during development, but it refuses to start on a machine that is not signed in to a Reflex account. Run `lex reflex login` once; where nobody can, set `REFLEX_ACCESS_TOKEN` to a token from your Reflex account.

### What the first run does

- **Writes `rxconfig.py`** at the project root — the file Reflex reads its configuration from — and says so. It holds one call, `config = lex_config()`; commit it. Later runs leave it alone, so it is yours to edit: `lex_config()` takes any Reflex configuration option, e.g. `lex_config(show_built_with_reflex=False)`. Settings that differ between environments — the public URLs below — belong in environment variables instead.
- **Prepares the project for Reflex**, which is Reflex's own start-up rather than Lex App's: it downloads Reflex's JavaScript toolchain and packages — so the first run needs network access — builds the frontend into `.web/`, creates `.states/` and `assets/external/`, and adds those to `.gitignore`. Commit the `.gitignore` change; `.web/` is rebuilt whenever it is missing.
- **Appends `reflex==<version>` to `requirements.txt`**, when the file does not name Reflex. Lex App already requires the Reflex version it supports, and a second pin will conflict with it on the next upgrade. Replace the line with a bare `reflex`: Reflex only checks that the file names it.

### Hot reload

The development server restarts when a file of the project changes — models, `_reflex_structure.py`, dashboard modules, `rxconfig.py` — and ignores what is not source: hidden entries such as `.web/`, virtual environments, `migrations/`, `media/`, build output and caches. `REFLEX_HOT_RELOAD_OVERRIDE_PATHS` replaces the list, when you want to choose.

### Ports

Every argument after `lex reflex` goes to Reflex's own CLI, so ports are Reflex's options. `lex reflex` only supplies the defaults, and only the ones a run can use — Reflex refuses a frontend port on a backend-only run:

| Run | Gets |
|---|---|
| `lex reflex` / `lex reflex run` | frontend `8502`, backend `8503` |
| `lex reflex run --backend-only` | backend `8503` |
| `lex reflex run --frontend-only` | frontend `8502` |
| `lex reflex run --env prod` | one port for both, `8502` |

A port you choose always wins — `--frontend-port` / `--backend-port`, `REFLEX_FRONTEND_PORT` / `REFLEX_BACKEND_PORT`, or `frontend_port` / `backend_port` in `lex_config()` — and `lex reflex` then adds nothing for it. Leave them out of `rxconfig.py` unless you mean them for every run: a frontend port configured there makes `--backend-only` refuse to start.

## In production

```bash
lex reflex run --env prod
```

Production mode builds an optimised frontend and serves it and the backend on **one port**, `8502` unless you choose another. What it needs:

| What | Why |
|---|---|
| **A paid Reflex tier** — Pro, Team or Enterprise — and `REFLEX_ACCESS_TOKEN` | Reflex Enterprise refuses `--env prod` (and `reflex export`) on the free tier |
| **HTTPS** | the sign-in cookies are `Secure`; over plain HTTP a user is sent back to `/login` after every sign-in |
| **`REFLEX_API_URL`** and **`REFLEX_DEPLOY_URL`**, both the dashboards' public URL | the browser connects back to the backend on `REFLEX_API_URL`; it is built into the frontend, so set it before the run |
| **The public callback in Keycloak** | `https://<dashboards>/callback` and `https://<dashboards>/*` — see [[access-and-dashboards/reflex/sessions and authentication#Keycloak: two lists of URLs\|Keycloak: two lists of URLs]] |
| **`IS_REFLEX_ENABLED=true`** and **`REFLEX_URL`** on the Lex App backend | the sidebar entry, and the address it frames |

Behind a reverse proxy, forward the public scheme and host — the sign-in builds its callback from them — and let WebSocket upgrades through on `/_event`, the channel every event travels on.

Reflex keeps each user's page state on its backend. One replica needs nothing more; more than one needs a shared Redis, `REFLEX_REDIS_URL`, so that a user's next event can reach any of them. The sign-in itself keeps nothing on the server.

The *Built with Reflex* badge Reflex adds in production can be switched off (`lex_config(show_built_with_reflex=False)`) on the Team and Enterprise tiers.

## Related

- [[access-and-dashboards/reflex/sessions and authentication|Sessions & Authentication]] — HTTPS, Keycloak's URLs, and what renews the session
- [[reference/CLI Commands|CLI Commands]] — `lex reflex` among the rest
- [[reference/Environment Variables|Environment Variables]] — every setting named on this page
- [[ship-and-operate/deploying|Deploying]] — the other processes a deployment runs
