---
title: "Running it locally"
description: "Run your dashboards on your machine, next to your lex-app project."
---

The dashboards run as a process of their own beside lex-app. Locally that is two commands, each in
its own terminal, from your project:

```bash
lex start      # lex-app, on http://localhost:8000
lex reflex     # the dashboards, on http://localhost:8502
```

Open `http://localhost:8502`. The dashboards sign you in through lex-app, so lex-app must be
running too.

## What `lex reflex` starts

`lex reflex` on its own is `lex reflex run`: Reflex's development server, which restarts when a file
of the project changes. It runs two servers:

| Server | Port | What it serves |
| --- | --- | --- |
| the pages | `8502` | what you open in the browser |
| the backend | `8503` | your handlers, the event connection, and the sign-in routes |

Every argument after `lex reflex` goes to Reflex's own command line, so a port you choose wins:
`--frontend-port` / `--backend-port`, `REFLEX_FRONTEND_PORT` / `REFLEX_BACKEND_PORT`, or
`frontend_port` / `backend_port` in `lex_config()`. `lex reflex` only fills in the ports you left
out. Leave them out of `rxconfig.py` unless you mean them for every run: a pages port configured
there makes `lex reflex run --backend-only` refuse to start.

The first run in a project writes `rxconfig.py` at its root and says so. It holds
`config = lex_config()`; commit it. The first run also takes longer, while Reflex sets up its
frontend tools and builds the pages into `.web/`.

The development server watches the project's own files - models, `_reflex_structure.py`,
`_dashboards/`, `rxconfig.py` - and skips hidden folders such as `.web/`, virtual environments and
folders like `migrations/`, `media/` and `node_modules/`. `REFLEX_HOT_RELOAD_OVERRIDE_PATHS`
replaces that list when you want to choose.

## Signing in locally

On your machine the sign-in works with nothing configured. The dashboards accept `localhost`,
`127.0.0.1` and `[::1]` at any port, and send you to lex-app at `http://localhost:8000`, where
`lex start` serves it:

1. You open a page on `:8502`. Its first event has no session, so the browser goes to the
   backend's `/auth/start` on `:8503`.
2. That sends you to lex-app's `/oidc/handoff` on `:8000`. Signed in to lex-app, you come straight
   back; if not, lex-app's sign-in page comes first.
3. The backend's `/auth/handoff` keeps the session in a cookie and returns you to the page.

Both processes must use the same database: the backend redeems lex-app's sign-in in it. They do
when both run from the project with its `.env`.

That is the whole difference from production: no addresses to list, and plain http, where the
cookie is `lex_rdash` rather than `__Host-lex_rdash`. A setting of `DEPLOYMENT_ENVIRONMENT` in your
`.env` turns the `localhost` exception off. How the round trip works is on
[Sign-in](/ship-it/sign-in).

## Inside lex-app's frame

Set `IS_REFLEX_ENABLED=true` in the project's `.env` and restart `lex start`: lex-app's sidebar gets
a **Reflex** entry that shows the dashboards inside lex-app, at `http://localhost:8502` unless
`REFLEX_URL` says otherwise. There the dashboards hide their own header and navigation; lex-app's
show the user.

## One port instead of two

With `REFLEX_USE_SINGLE_PORT=true`, the backend passes every request that is not its own - the
pages, and the dev server's hot-reload connection - on to the pages' server. Open the backend's
port, `http://localhost:8503`, instead. It is meant for a development run where only one port can
be reached, such as a pod; on your machine, two ports work as well.

Next: [Pages](/pages/ui-page), where a page gets its address.
