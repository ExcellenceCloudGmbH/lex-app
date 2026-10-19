---
title: "Custom frontend"
---

A hosted instance serves the standard interface — the process admin client — from its own pod (see [[ship-and-operate/deploying#The interface|Deploying → The interface]]). That pod can serve **your own frontend** instead: a [Vite](https://vite.dev) app in a folder of your application's repository, installed with npm, yarn or bun.

The frontend ships with your application. Every build of a commit builds the backend and the frontend from that same commit, and a release puts both on the instance together, so they never drift apart. Each instance chooses whether to show your frontend or the standard interface, the way it chooses whether to run Streamlit.

Your frontend is served on the instance's own host, so it talks to the lex-app backend on the same origin, with the same session, the same CSRF protection and the same websockets — nothing in the backend needs configuring for it.

| Page section | Answers |
|---|---|
| [What the repository needs](#what-the-repository-needs) | Where the app goes and what it must contain |
| [Setting it up in the Instance Controller](#setting-it-up-in-the-instance-controller) | Turning on the frontend build, and choosing it on an instance |
| [Build secrets](#build-secrets) | Private registries and other tokens the build needs |
| [What your app is given at runtime](#what-your-app-is-given-at-runtime) | `window.__LEX_ENV__`, and the keys in it |
| [Talking to lex-app](#talking-to-lex-app) | Signing in, the REST API, websockets |
| [Releases](#releases) | How a release carries the frontend along |

## What the repository needs

- **The app in a folder of your lex-app repository**, for example `frontend/`. The backend code stays where it is.
- **A Vite app.** `vite` must be a dependency of the app's `package.json`; the build refuses anything else.
- **A lockfile for the package manager you choose**: `package-lock.json` (or `npm-shrinkwrap.json`) for npm, `yarn.lock` for yarn, `bun.lock` (or `bun.lockb`) for bun. Dependencies are installed from it exactly — `npm ci`, `yarn install --frozen-lockfile` (yarn 1) or `--immutable` (yarn 2+), `bun install --frozen-lockfile` — so a lockfile that is out of date fails the build rather than drifting.
- **A `build` script**, unless you give the build command yourself. `tsc -b && vite build` is fine.
- **`base: '/'`** (Vite's default). The app is served from the root of the instance's host.
- **A single-page app.** Any path that is not a file is answered with `index.html`, so client-side routing works on reload. There is no server-side rendering.

The lockfile can sit in a different folder from the app (a workspace monorepo): you tell the build where dependencies are installed and where the app is built. If `package.json` names a `packageManager`, it must be the one you chose; its version is used.

## Setting it up in the Instance Controller

1. **Turn on the frontend build.** In your project, open the *Frontend* tab and fill in the build settings: package manager, Node version (default 24), the folder with the lockfile, the folder with the app, and optionally the install command, build command (default `<package manager> run build`), output directory (default `dist`) and build variables. Leave *Enable frontend build* on.
2. **Build a commit.** From now on every build of the project — the *Build Image* workflow, or any workflow of yours with a build step — builds the frontend too. A build only succeeds when both the backend and the frontend built.
3. **Choose it on an instance.** When you create an instance, or later in its configuration under *Frontend*, choose *Project's custom frontend*. Only test and production instances can, and the commit the instance runs must have been built with the frontend.

You can switch an instance back to the standard interface at any time, and its runtime settings are kept for switching again. Turning the frontend build off stops new builds from including it; instances that show your frontend can then only stay on commits that were built with it.

Build variables and runtime settings are **public**. Anything a Vite build reads ends up in the bundle every visitor downloads, and runtime settings are published to the browser — never put a secret in either. Secrets the build itself needs go in [build secrets](#build-secrets).

## Build secrets

Some builds need a secret: a token for a private npm registry, a Sentry auth token to upload source maps, a licence key. Add them under *Frontend → Secrets*. A secret's value is stored in Secret Manager; it can be replaced or deleted, never read back.

Each secret is exposed in one of two ways, to the install step, the build step or both:

| Kind | What the build sees | Typical use |
|---|---|---|
| **Environment variable** | A variable named like the secret | `NPM_TOKEN` read by a committed `.npmrc` (`//npm.pkg.github.com/:_authToken=${NPM_TOKEN}`), `SENTRY_AUTH_TOKEN` |
| **File** | The value as a file at a path you choose: relative to the repository root, or `~/…` for the build user's home | `.npmrc`, `~/.npmrc`, `.yarnrc.yml`, `bunfig.toml` |

A file secret replaces any file of the same name for the duration of the step and is removed — or the original restored — before the step ends, so it is not left in the image. A secret never appears in the workflow's inputs, and its lines are masked in the build log.

> [!warning] Not `VITE_`
> Vite compiles every `VITE_` variable it sees into the public bundle, so a secret with that prefix would be published. The Instance Controller refuses to store one.

## What your app is given at runtime

One image is built per commit and served by every instance that shows it, so the instance-specific values cannot be compiled in. They are published when the pod starts, as a script the build adds to the `<head>` of every page:

```html
<script src="/lex-env.js"></script>
```

which defines `window.__LEX_ENV__` before your app's own scripts run. It is never cached.

| Key | Value |
|---|---|
| `API_BASE_PATH` | `/api/` — the backend, on the same origin |
| `WS_BASE_PATH` | `/api/ws/` — the backend's websockets |
| `INSTANCE_URL` | The instance's canonical URL (informational: build URLs from `location`, the instance can be reached on more than one host) |
| `INSTANCE_ID`, `INSTANCE_NAME` | Which instance this is |
| `KEYCLOAK_URL`, `KEYCLOAK_REALM`, `KEYCLOAK_CLIENT_ID` | The instance's identity provider and public client |
| `STORAGE_TYPE` | The instance's file storage |
| `DOMAIN_BASE` | The platform's base domain |
| `GRAFANA_DASHBOARD_URL` | The instance's monitoring dashboard |
| `STREAMLIT_URL`, `REFLEX_URL` | The instance's dashboards, or an empty string when they are off |
| `FRONTEND_VERSION`, `BACKEND_VERSION` | The commit the instance runs (the same for both) |
| *your own keys* | The instance's runtime settings (configuration → *Frontend*): upper-case names, up to 50 of them |

A small helper keeps the rest of the app independent of where the values come from, and lets `vite dev` fall back to `.env` files (`VITE_LEX_API_BASE_PATH=...`):

```ts
// src/lexEnv.ts
declare global {
  interface Window {
    __LEX_ENV__?: Readonly<Record<string, string>>
  }
}

export function lexEnv(key: string, fallback = ''): string {
  return window.__LEX_ENV__?.[key] ?? import.meta.env[`VITE_LEX_${key}`] ?? fallback
}
```

## Talking to lex-app

Your app is on the backend's own origin, so it uses lex-app exactly as the standard interface does:

- **REST** is under `/api/api/<route>` — the first `/api/` is the instance's backend, the second lex-app's own API prefix. `GET /api/api/user/` returns the signed-in user.
- **Signing in** is lex-app's: when a call answers 401 or 403, send the browser to `/api/oidc/authenticate?next=<where to come back to>`. Signing out is `/api/oidc/total_logout?next=<url>&fail=<url>`.
- **Requests** go with `credentials: 'include'`, and anything that changes data sends the `csrftoken` cookie's value as the `X-CSRFToken` header.
- **Websockets** connect to `wss://${location.host}/api/ws/<route>` (for example `calculations`), with the session cookie.

```ts
const api = (route: string) => new URL(`api/${route}`, new URL(lexEnv('API_BASE_PATH', '/api/'), location.origin))

export async function lexFetch(route: string, init: RequestInit = {}) {
  const csrf = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/)?.[1]
  const response = await fetch(api(route), {
    ...init,
    credentials: 'include',
    headers: { ...(init.headers ?? {}), ...(csrf ? { 'X-CSRFToken': decodeURIComponent(csrf) } : {}) },
  })
  if (response.status === 401 || response.status === 403) {
    location.href = `${lexEnv('API_BASE_PATH', '/api/')}oidc/authenticate?next=${encodeURIComponent(location.href)}`
  }
  return response
}
```

Files under `/assets/` — Vite's content-hashed output — are cached for a year; everything else, `index.html` included, is not cached, so a release is picked up on the next page load.

## Releases

A release works exactly as before: it puts one commit on an instance, through the release panel or a workflow, under the same compliance rules. For an instance that shows your frontend, that commit's backend and frontend go on together — so it can only be released to a commit whose build built the frontend too. Commits built before the frontend build was turned on, or while it was off, stay available to instances that show the standard interface.

## Related

- [[ship-and-operate/deploying|Deploying]] — the pods an installation runs
- [[ship-and-operate/upgrading|Upgrading]] — the interface and the backend move independently
