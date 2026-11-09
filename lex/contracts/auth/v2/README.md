# LEX HTTP auth contract v2

The agreement between LEX servers (lex-app, the Instance Controller, django-oauth2-codeflow 3.x)
and their browser clients (process-admin-general-client, ic-frontend-new, atrium) about what an
authentication or authorization problem looks like on the wire, and what a client does about it.

The session is an HttpOnly cookie owned by the server (backend-for-frontend). Nothing here reads,
stores, or forwards tokens.

## Rules

1. **401 means "the session is gone".** The client re-authenticates — silently if it can — and
   never signs the user out of Keycloak because of it.
2. **403 means "you may not do this"** (or a CSRF failure). The client shows it and never
   re-authenticates or signs out because of it.
3. **503 `{"error":"auth_unavailable"}` means "auth is briefly unavailable".** The client retries;
   the user stays signed in.
4. **Only an explicit "Sign out" calls `oidc/total_logout`.**

## Server responses

| Case | Background request (fetch/XHR, any `^/api/` path, any `Authorization`) | Page navigation | Frame navigation |
|---|---|---|---|
| No session, or session ended | **401** `{"error": "not_authenticated" \| "session_expired", "login_url": "/oidc/authenticate"}` + `WWW-Authenticate: Session realm="<client_id>"` | 302 `/oidc/authenticate?next=` | 401 HTML with a `target=_top` sign-in link |
| Auth backend unavailable (refresh, permissions lookup) | **503** `{"error": "auth_unavailable", "retry_after": <s>}` + `Retry-After`, always **before** the view runs | 503 HTML auto-retry page | 503 HTML |
| Permission denied | **403** `{"detail": "…", "code": "permission_denied"}` | — | — |
| CSRF failure | **403** `{"detail": "CSRF Failed: …", "code": "csrf_failed"}` | — | — |

Every auth response carries `Cache-Control: no-store`. `login_url` is relative.

`GET api/auth/session` → 200 `{"authenticated": true, "user": {…}, "capabilities": {"auth_contract": 2,
"dashboard_handoff": ["streamlit"], "ws_close_codes": true}}`, or the 401/503 bodies above. A 404
means the server predates the contract.

`GET oidc/auth-complete?m=silent|popup` posts the result of a silent (`prompt=none`) or popup
sign-in to its opener/parent (same origin only) and on `BroadcastChannel('lex-auth')`:
`{"type":"lex-auth","v":2,"event":"result","mode":"silent"|"popup","ok":true|false}`.

### Servers that predate the contract

The client also understands the older answers, so a new client works against an old server:

| Old answer | Read as |
|---|---|
| 302 to the login flow (seen as an opaque redirect with `redirect: 'manual'`) | 401 |
| 401 `{"error": "<keycloak text>", "token_type": "access_token"}` (django-oauth2-codeflow ≤ 2.x) | 401 |
| 403 `{"detail": "Authentication credentials were not provided."}` (DRF NotAuthenticated) | 401 |
| 403 `{"detail": "CSRF Failed: …"}` / Django's CSRF HTML page | CSRF |

## WebSocket close codes

| Code | Meaning | Client |
|---|---|---|
| 4401 | Not authenticated, or the session ended | stop reconnecting, re-authenticate |
| 4403 | Forbidden | stop |
| 4503 | Auth unavailable | reconnect with backoff |
| anything else | as before | reconnect |

Servers `accept()` before `close(code)`; closing during the handshake reaches the browser as 1006.

## Messages between frames and tabs

Frames (`postMessage`, pinned target origins, schema-validated on receipt):

```json
{"type": "lex-auth", "v": 2, "event": "required|restored|signed-out", "surface": "pac-embed|streamlit|reflex", "reason": "…", "id": "<uuid>"}
```

The pre-v2 `{"type": "lex-auth-required", "login": "<url>"}` is still accepted.

Tabs (`BroadcastChannel('lex-auth')`): `{"type":"lex-auth","v":2,"event":"restored|signed-out|logout|reauth-started","tab":"<uuid>","at":<ms>}`.

No message or URL ever carries a token.

## Client behaviour (`client/lex-auth-client.ts`)

- **Classifier** `classify` / `classifyResponse` / `classifyError`. The rules are applied in order:
  1. Opaque redirect → re-auth.
  2. 401 → re-auth.
  3. 403 lost-session → re-auth.
  4. 403 CSRF → CSRF.
  5. Any other 403 → forbidden.
  6. 503 `auth_unavailable` → auth unavailable.
  7. 502/503/504 → unavailable.
  8. No response → network.
- **Retry policy** `retryDelayMs`:
  - `auth_unavailable`: any method, with backoff 1, 2, 4, 8, 16, 30 s (at most 6 retries, plus jitter), honouring `Retry-After`.
  - `unavailable` and `network`: safe methods (GET/HEAD/OPTIONS/TRACE) only.
  - CSRF: one immediate retry with a re-read cookie.
  - Anything else: no retry.
- **`createAuthFetch`:**
  - Requests are sent with `redirect: 'manual'` and `credentials: 'include'`.
  - Every unsafe method gets the CSRF header, whatever shape the caller passed its headers in. Each attempt reads the cookie again and replaces a stale header (Django rotates the token at sign-in), so a request replayed after re-authentication passes the CSRF check.
  - Retries follow the policy above.
  - On re-auth it waits for `onReauthRequired`, then replays the request once.
  - Request bodies must be re-sendable (string, `FormData`, `Blob`, `URLSearchParams` — not streams).
- **`createReauthCoordinator`** (the re-auth ladder, one per page):
  1. A hidden same-origin iframe goes through `oidc/authenticate?prompt=none` to `oidc/auth-complete`.
  2. The server is probed: `api/auth/session`, falling back to `api/user/`.
  3. If still signed out, the state becomes `signed_out`; the app shows a blocking dialog.
  4. "Sign in" opens a popup.
  5. "Continue in this tab" saves drafts and does a loop-guarded full-page sign-in.

  Tabs coordinate through Web Locks (`lex-auth`) and the BroadcastChannel: one tab leads, the others follow. Embedded frames never navigate; they tell their host (`required` / `restored`).
- **Loop guard:** at most 3 automatic sign-in navigations per 30 s, per scope (`shell`, `embed`, `streamlit`). The storage keys are compatible with PAC's `redirectLoopHelpers`.
- **Drafts:** `createDraftStore` keeps JSON drafts in `sessionStorage` (no files, no secrets).

## Fixtures

`fixtures/http/*.json` hold cases of the form `{id, server, server_case, request, response, expect}`:
- `server` is `v2`, `legacy` or `any`.
- `expect` holds the classifier outcome, plus whether a first retry happens for GET and POST.

`fixtures/ws/close-codes.json` maps each close code to its action.

Who runs them:
- **Clients** run every fixture through their vendored client (e.g. PAC `src/auth/__test__/lexAuthContract.fixtures.test.ts`).
- **Servers** look up the cases they own by `server_case` and assert that their real response has the same status, the same body keys, and the headers listed:

| `server_case` | Owner |
|---|---|
| `session_missing`, `session_expired`, `auth_unavailable` | django-oauth2-codeflow 3.x middleware, lex-app, IC |
| `permission_denied`, `csrf_failed` | lex-app / IC DRF exception handler |
| `legacy_*` | older servers; kept so clients stay compatible |

## Vendoring

Consumers keep a byte-identical copy in `src/vendor/lex-auth-client/`. A `VENDORED.json` beside it records the source, the version and SHA-256 hashes.

```bash
node scripts/vendor-lex-auth-client.mjs --from ../lex-app --ref <lex-app tag>   # update
node scripts/vendor-lex-auth-client.mjs --check                                 # CI drift check
```

Exclude `src/vendor/` from the consumer's Prettier and ESLint runs: the canonical file is formatted here.

Versioning: `VERSION` follows semver for this contract. 2.0.1: `createAuthFetch` keeps a `Request` input's headers and clones its body per attempt (RTK Query passes `Request`s); `ReauthCoordinator.recheck()`. 2.0.2: the CSRF header is taken from the current cookie on every attempt, replacing a stale one — without this, every unsafe request replayed after re-authentication failed the CSRF check. A breaking wire change is a new `vN` directory.
