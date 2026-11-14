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
| [Talking to lex-app](#talking-to-lex-app) | Signing in, the REST API, websockets, large files, framing dashboards |
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

- **REST** is under `/api/api/<route>` — the first `/api/` is the instance's backend, the second lex-app's own API prefix. `GET /api/api/user/` returns the signed-in user. [[reference/REST API|REST API]] lists the routes, what they take and what they answer.
- **Signing in** is lex-app's. Only a `401` means the sign-in is gone: send the browser to `/api/oidc/authenticate?next=<where to come back to>`. A `403` never does — with `"code": "permission_denied"` the user stays signed in and you show the refusal; with `"code": "csrf_failed"` read the `csrftoken` cookie again and retry once. A `503` with `{"error": "auth_unavailable"}` means Keycloak cannot answer right now: wait `Retry-After` seconds and retry, and sign nobody out. Signing out is `/api/oidc/total_logout?next=<url>&fail=<url>`, and only when the user asks for it. The full table is in [[access-and-dashboards/sign-in and sessions#For frontends and API clients|Sign-in & Sessions]].
- **Requests** go with `credentials: 'include'`, and anything that changes data sends the `csrftoken` cookie's value as the `X-CSRFToken` header.
- **Websockets** connect to `wss://${location.host}/api/ws/<route>` (for example `calculations`), with the session cookie — the only thing that signs a socket in.

```ts
const api = (route: string) => new URL(`api/${route}`, new URL(lexEnv('API_BASE_PATH', '/api/'), location.origin))

export async function lexFetch(route: string, init: RequestInit = {}, retries = 3): Promise<Response> {
  const headers = new Headers(init.headers)
  const csrf = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/)?.[1] // read again on every attempt
  if (csrf) headers.set('X-CSRFToken', decodeURIComponent(csrf))
  const response = await fetch(api(route), { ...init, credentials: 'include', headers })

  if (response.status === 401) {
    // The sign-in is gone: sign in, and come back here.
    location.href = `${lexEnv('API_BASE_PATH', '/api/')}oidc/authenticate?next=${encodeURIComponent(location.href)}`
  } else if (retries > 0 && (response.status === 403 || response.status === 503)) {
    const body = await response.clone().json().catch(() => ({}))
    if (body.code === 'csrf_failed') return lexFetch(route, init, 0) // once, with the cookie read again
    if (body.error === 'auth_unavailable') {
      // Keycloak is briefly unreachable: wait as told, then retry.
      const seconds = Number(response.headers.get('Retry-After')) || 5
      await new Promise((resolve) => setTimeout(resolve, seconds * 1000))
      return lexFetch(route, init, retries - 1)
    }
  }
  return response // a 403 permission_denied ends here: the user stays signed in; show the refusal
}
```

A retried request is sent again with the same `init`, so its body must be one that can be sent twice: a string, `FormData`, a `Blob` or `URLSearchParams`, not a stream.

Files under `/assets/` — Vite's content-hashed output — are cached for a year; everything else, `index.html` included, is not cached, so a release is picked up on the next page load.

### Large files

A request body above about 100 MB never reaches the backend. Cloudflare, the Ingress and the web application firewall each stop it, and the browser reports only `Failed to fetch`. From 2.5, lex-app takes a large file in pieces first. The standard interface does this for every file of 32 MiB or more; your frontend has to do it itself. The routes sit with the other REST routes, so `lexFetch('chunked-uploads/', …)` above reaches the first one:

| Route | Does |
|---|---|
| `POST chunked-uploads/` with JSON `{filename, size}`, and optionally `content_type` | Starts an upload. Answers `201` with `upload_id`, `token`, `chunk_size` and `chunk_count`, or `413` for a file above the limit. `content_type` is the media type the joined file gets; without it, `application/octet-stream`. |
| `POST chunked-uploads/<upload_id>/chunks/<index>`, multipart field `chunk` | Stores one piece. Pieces may arrive in any order; sending one again replaces it. |
| `GET chunked-uploads/<upload_id>` | Lists the pieces received (`received`, `complete`), so an upload can resume. |
| `DELETE chunked-uploads/<upload_id>` | Discards the upload. |

Cut the file into pieces of exactly `chunk_size` bytes; only the last may be shorter, and a piece of any other size is refused with `400`. Then send the create or update request as usual, with `token` (`lex-chunked-upload:<upload_id>`) as the file field's value in place of the file. The backend joins the pieces, and the record saves as with a direct upload. Delete the upload when the request has finished, whether it succeeded or not.

An upload belongs to the user who started it; anyone else gets `404`. A file may be at most 5 GiB. An upload nobody deletes is removed once it is more than 24 hours old, by the next upload anyone starts; until then it keeps working. These numbers — 32 MiB pieces, 5 GiB, 24 hours — are fixed in 2.5.0: no environment variable or `lex_config.py` setting changes them.

### Framing a dashboard

To show one of the instance's dashboards inside your app, frame its address — `STREAMLIT_URL` or `REFLEX_URL` above — with the query parameters the standard interface adds:

| Parameter | Value | What the dashboard does with it |
|---|---|---|
| `model`, `pk` | A model's name, and a record's primary key | Picks the dashboard: with both, the record's; with `model` alone, the model's; with neither, the project's own |
| `lex_embed` | `true` | Leaves out its own header: the account bar with its sign-out, and on Streamlit the logo. A Streamlit dashboard also opens with its sidebar collapsed |
| `is_logout_enabled` | `false` | Hides the sign-out control, on a dashboard that still draws its account bar |
| `lex_app_origin` | `location.origin` | Streamlit only: the origin its `lex_view` tables are framed from, and where it sends its sign-in messages. Ignored unless it is one of the instance's application hosts (as your app's is), its configured frontend (`REACT_APP_URL` / `LEX_FRONTEND_URL`), or — on an instance that is not deployed — a loopback origin |
| `embed_options` | `light_theme` or `dark_theme` | Streamlit only: opens in that theme |

```
https://<dashboard host>/?model=fund&pk=42&lex_embed=true&is_logout_enabled=false&lex_app_origin=https%3A%2F%2Fapp.customer.com
```

The frame carries no credential when dashboards sign in with `LEX_STREAMLIT_AUTH=handoff` or `dual` — `GET api/auth/session` then lists `"streamlit"` in `capabilities.dashboard_handoff` — and a Streamlit dashboard signs itself in through the application; a Reflex dashboard keeps its own Keycloak sign-in. On an instance with a custom domain, frame the dashboard hostname on your page's own domain: see [[ship-and-operate/custom domains#Why the dashboards get their own hostnames|Custom domains]].

## Releases

A release works exactly as before: it puts one commit on an instance, through the release panel or a workflow, under the same compliance rules. For an instance that shows your frontend, that commit's backend and frontend go on together — so it can only be released to a commit whose build built the frontend too. Commits built before the frontend build was turned on, or while it was off, stay available to instances that show the standard interface.

## Related

- [[ship-and-operate/deploying|Deploying]] — the pods an installation runs
- [[ship-and-operate/upgrading|Upgrading]] — the interface and the backend move independently
