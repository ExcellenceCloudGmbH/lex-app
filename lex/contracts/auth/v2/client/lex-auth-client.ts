/**
 * LEX HTTP auth contract v2 — framework-free browser client.
 *
 * Canonical source: lex-app `lex/contracts/auth/v2/client/lex-auth-client.ts`.
 * Consumers (process-admin-general-client, ic-frontend-new, atrium) VENDOR this file with
 * `scripts/vendor-lex-auth-client.mjs`; never edit a vendored copy — change it here and re-vendor.
 *
 * What the contract says (see ../README.md for the full table):
 *   - 401 → the session is gone: re-authenticate (silently if possible), never sign the user out.
 *   - 403 → an authorization decision (or a CSRF failure): show it, never sign the user out.
 *     Servers that predate the contract answer a lost session with DRF's "not authenticated"
 *     403 or with a 302 to the login flow; both are recognised as "re-authenticate".
 *   - 503 {"error":"auth_unavailable"} → the auth backend is briefly unavailable: retry.
 *   - WebSocket close 4401 → re-authenticate, 4403 → stop, 4503 → reconnect with backoff.
 *
 * Nothing in this module reads or stores tokens: the session is an HttpOnly cookie owned by the
 * server (backend-for-frontend). Messages and URLs produced here never carry secrets.
 */

export const LEX_AUTH_CONTRACT_VERSION = 2

// ─────────────────────────────────────────────────────────────────────────────
// Classification
// ─────────────────────────────────────────────────────────────────────────────

export type ReauthReason =
  | 'not_authenticated'
  | 'session_expired'
  | 'legacy_redirect'
  | 'legacy_401'
  | 'legacy_403'

export interface OkOutcome {
  kind: 'ok'
  status: number
}
export interface ReauthOutcome {
  kind: 'reauth'
  status: number
  reason: ReauthReason
  /** Relative or same-origin login URL announced by the server, if any. */
  loginUrl: string | null
}
export interface ForbiddenOutcome {
  kind: 'forbidden'
  status: 403
  detail: string | null
  code: string | null
}
export interface CsrfOutcome {
  kind: 'csrf'
  status: 403
  detail: string | null
}
export interface AuthUnavailableOutcome {
  kind: 'auth_unavailable'
  status: 503
  retryAfterMs: number
}
export interface UnavailableOutcome {
  kind: 'unavailable'
  status: number
  retryAfterMs: number | null
}
export interface NetworkOutcome {
  kind: 'network'
  status: 0
}
export interface AbortedOutcome {
  kind: 'aborted'
  status: 0
}
export interface ErrorOutcome {
  kind: 'error'
  status: number
}

export type AuthOutcome =
  | OkOutcome
  | ReauthOutcome
  | ForbiddenOutcome
  | CsrfOutcome
  | AuthUnavailableOutcome
  | UnavailableOutcome
  | NetworkOutcome
  | AbortedOutcome
  | ErrorOutcome

export type HeadersLike = Headers | Record<string, string | undefined> | null | undefined

/** A transport-neutral description of one HTTP exchange (what the fixtures describe). */
export interface HttpExchange {
  status: number
  /** `Response.type` — `'opaqueredirect'` when a fetch with `redirect: 'manual'` saw a 3xx. */
  responseType?: string
  headers?: HeadersLike
  /** Parsed JSON body, or the raw text when the body was not JSON. */
  body?: unknown
  /** True when the request never produced a response (DNS, TCP, TLS, CORS, offline). */
  networkError?: boolean
}

/** Default wait before retrying an `auth_unavailable` answer that carries no `Retry-After`. */
export const DEFAULT_AUTH_RETRY_MS = 2000

// DRF's NotAuthenticated / AuthenticationFailed default details (rendered as 403 by servers whose
// first authentication class defines no `authenticate_header`).
const NOT_AUTHENTICATED_RE =
  /authentication credentials were not provided|incorrect authentication credentials|\bnot authenticated\b/i
// DRF SessionAuthentication ("CSRF Failed: …") and Django's CsrfViewMiddleware HTML page.
const CSRF_RE = /^\s*CSRF Failed|CSRF verification failed/i

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function stringOrNull(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null
}

/** The human-readable message of an error body: `detail`, then `error`, then `message`, else text. */
export function errorText(body: unknown): string | null {
  if (typeof body === 'string') return body === '' ? null : body
  const record = asRecord(body)
  if (record === null) return null
  return stringOrNull(record.detail) ?? stringOrNull(record.error) ?? stringOrNull(record.message)
}

/** Case-insensitive header lookup over `Headers` or a plain object. */
export function headerValue(headers: HeadersLike, name: string): string | null {
  if (headers === null || headers === undefined) return null
  if (typeof (headers as Headers).get === 'function') {
    return (headers as Headers).get(name)
  }
  const wanted = name.toLowerCase()
  for (const [key, value] of Object.entries(headers as Record<string, string | undefined>)) {
    if (key.toLowerCase() === wanted && typeof value === 'string') return value
  }
  return null
}

/** `Retry-After` (seconds or HTTP date) or the body's `retry_after` seconds, in milliseconds. */
export function retryAfterMs(
  headers: HeadersLike,
  body: unknown,
  now: number = Date.now(),
): number | null {
  const raw = headerValue(headers, 'retry-after')
  if (raw !== null) {
    const trimmed = raw.trim()
    if (/^\d+(\.\d+)?$/.test(trimmed)) return Math.round(Number(trimmed) * 1000)
    const at = Date.parse(trimmed)
    if (!Number.isNaN(at)) return Math.max(0, at - now)
  }
  const fromBody = asRecord(body)?.retry_after
  if (typeof fromBody === 'number' && Number.isFinite(fromBody) && fromBody >= 0) {
    return Math.round(fromBody * 1000)
  }
  return null
}

/**
 * Accept a server-announced login URL only when it cannot send the browser somewhere else:
 * a path-relative URL (`/oidc/authenticate`, not `//evil`), or an absolute URL on `origin`.
 */
export function safeLoginUrl(value: unknown, origin?: string): string | null {
  if (typeof value !== 'string' || value === '') return null
  if (value.startsWith('/') && !value.startsWith('//') && !value.startsWith('/\\')) return value
  if (origin === undefined) return null
  try {
    const url = new URL(value)
    return url.origin === origin && (url.protocol === 'https:' || url.protocol === 'http:')
      ? url.toString()
      : null
  } catch {
    return null
  }
}

/** Classify one HTTP exchange. Rules apply in order; see ../README.md. */
export function classify(exchange: HttpExchange, options: { origin?: string } = {}): AuthOutcome {
  if (exchange.networkError === true) return { kind: 'network', status: 0 }
  if (exchange.responseType === 'opaqueredirect') {
    return { kind: 'reauth', status: 0, reason: 'legacy_redirect', loginUrl: null }
  }
  const { status } = exchange
  const body = asRecord(exchange.body)
  const code = stringOrNull(body?.code)
  const error = stringOrNull(body?.error)
  const text = errorText(exchange.body)

  if (status === 401) {
    const reason: ReauthReason =
      error === 'session_expired'
        ? 'session_expired'
        : error === 'not_authenticated'
          ? 'not_authenticated'
          : 'legacy_401'
    return {
      kind: 'reauth',
      status,
      reason,
      loginUrl: safeLoginUrl(body?.login_url, options.origin),
    }
  }
  if (status === 403) {
    if (
      code === 'not_authenticated' ||
      code === 'authentication_failed' ||
      error === 'not_authenticated' ||
      (text !== null && NOT_AUTHENTICATED_RE.test(text))
    ) {
      return { kind: 'reauth', status, reason: 'legacy_403', loginUrl: null }
    }
    if (code === 'csrf_failed' || (text !== null && CSRF_RE.test(text))) {
      return { kind: 'csrf', status: 403, detail: text }
    }
    return { kind: 'forbidden', status: 403, detail: text, code }
  }
  if (status === 503 && error === 'auth_unavailable') {
    return {
      kind: 'auth_unavailable',
      status: 503,
      retryAfterMs: retryAfterMs(exchange.headers, exchange.body) ?? DEFAULT_AUTH_RETRY_MS,
    }
  }
  if (status === 502 || status === 503 || status === 504) {
    return {
      kind: 'unavailable',
      status,
      retryAfterMs: retryAfterMs(exchange.headers, exchange.body),
    }
  }
  if (status === 0) return { kind: 'network', status: 0 }
  if (status >= 200 && status < 400) return { kind: 'ok', status }
  return { kind: 'error', status }
}

async function readBody(response: Response): Promise<unknown> {
  let text: string
  try {
    text = await response.clone().text()
  } catch {
    return undefined
  }
  if (text === '') return undefined
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

/** Classify a fetch `Response` (the body is read from a clone; the original stays readable). */
export async function classifyResponse(
  response: Response,
  options: { origin?: string } = {},
): Promise<AuthOutcome> {
  if (response.type === 'opaqueredirect')
    return classify({ status: 0, responseType: 'opaqueredirect' })
  return classify(
    {
      status: response.status,
      responseType: response.type,
      headers: response.headers,
      body: await readBody(response),
    },
    options,
  )
}

/** True for an `AbortController` cancellation (never retried, never an auth event). */
export function isAbortError(error: unknown): boolean {
  return asRecord(error)?.name === 'AbortError'
}

/**
 * Classify a thrown/rejected error from any client in use: `fetch` TypeErrors, react-admin
 * `HttpError`-like objects (`status`, `body`), RTK Query errors (`status`, `data`, `originalStatus`)
 * and axios errors (`response.status`, `response.data`, `response.headers`).
 */
export function classifyError(error: unknown, options: { origin?: string } = {}): AuthOutcome {
  if (isAbortError(error)) return { kind: 'aborted', status: 0 }
  if (error instanceof TypeError) return { kind: 'network', status: 0 }
  const record = asRecord(error)
  if (record === null) return { kind: 'error', status: 0 }

  const response = asRecord(record.response)
  if (response !== null && typeof response.status === 'number') {
    return classify(
      { status: response.status, headers: response.headers as HeadersLike, body: response.data },
      options,
    )
  }
  if (record.status === 'FETCH_ERROR') return { kind: 'network', status: 0 }
  if (record.status === 'PARSING_ERROR' && typeof record.originalStatus === 'number') {
    return classify({ status: record.originalStatus, body: record.data }, options)
  }
  if (typeof record.status === 'number') {
    const body = record.body !== undefined ? record.body : record.data
    return classify(
      { status: record.status, body, headers: record.headers as HeadersLike },
      options,
    )
  }
  return { kind: 'error', status: 0 }
}

/** A 401 standing in for an opaque redirect, so every caller sees one shape for "signed out". */
export function syntheticUnauthenticatedResponse(): Response {
  return new Response(JSON.stringify({ error: 'not_authenticated', login_url: null }), {
    status: 401,
    headers: { 'content-type': 'application/json', 'x-lex-auth-synthetic': 'opaqueredirect' },
  })
}

// ─────────────────────────────────────────────────────────────────────────────
// Retry policy
// ─────────────────────────────────────────────────────────────────────────────

export const SAFE_METHODS: readonly string[] = ['GET', 'HEAD', 'OPTIONS', 'TRACE']

export function isSafeMethod(method: string | undefined): boolean {
  return SAFE_METHODS.includes((method ?? 'GET').toUpperCase())
}

export interface RetryPolicy {
  /** Retries after the first attempt (6 → at most 7 requests). */
  maxRetries: number
  baseDelayMs: number
  maxDelayMs: number
  /** Fraction of the delay added as random jitter (0.2 → up to +20 %). */
  jitterRatio: number
}

export const DEFAULT_RETRY_POLICY: RetryPolicy = {
  maxRetries: 6,
  baseDelayMs: 1000,
  maxDelayMs: 30_000,
  jitterRatio: 0.2,
}

/**
 * How long to wait before retry number `retry` (1 = first retry), or `null` for "do not retry".
 *
 * - `auth_unavailable`: any method — the server guarantees it answered before the view ran.
 * - `unavailable` / `network`: safe methods only (an unsafe request may have been processed).
 * - `csrf`: once, immediately (the caller re-reads the CSRF cookie).
 * - everything else: never.
 */
export function retryDelayMs(
  outcome: AuthOutcome,
  method: string | undefined,
  retry: number,
  policy: RetryPolicy = DEFAULT_RETRY_POLICY,
  random: () => number = Math.random,
): number | null {
  if (outcome.kind === 'csrf') return retry === 1 ? 0 : null
  const retryable =
    outcome.kind === 'auth_unavailable' ||
    ((outcome.kind === 'unavailable' || outcome.kind === 'network') && isSafeMethod(method))
  if (!retryable || retry < 1 || retry > policy.maxRetries) return null
  const backoff = Math.min(policy.maxDelayMs, policy.baseDelayMs * 2 ** (retry - 1))
  const announced =
    outcome.kind === 'auth_unavailable' || outcome.kind === 'unavailable'
      ? (outcome.retryAfterMs ?? 0)
      : 0
  const base = Math.min(policy.maxDelayMs, Math.max(backoff, announced))
  return Math.round(base + base * policy.jitterRatio * random())
}

// ─────────────────────────────────────────────────────────────────────────────
// CSRF
// ─────────────────────────────────────────────────────────────────────────────

export const DEFAULT_CSRF_COOKIE = 'csrftoken'
export const DEFAULT_CSRF_HEADER = 'X-CSRFToken'

/** Read one cookie value (`document.cookie` by default). */
export function readCookie(name: string, cookieString?: string): string | null {
  const source = cookieString ?? (typeof document === 'undefined' ? '' : document.cookie)
  for (const part of source.split(';')) {
    const index = part.indexOf('=')
    if (index < 0) continue
    if (part.slice(0, index).trim() === name) {
      try {
        return decodeURIComponent(part.slice(index + 1).trim())
      } catch {
        return part.slice(index + 1).trim()
      }
    }
  }
  return null
}

/**
 * Normalise any `HeadersInit` (a `Headers`, an array of pairs or a plain object) into a fresh
 * `Headers` and set the CSRF header for unsafe methods. The caller's object is never mutated.
 *
 * The cookie is the source of truth: Django rotates the token at every sign-in, so a token read
 * from it replaces whatever the caller put there (a request replayed after re-authentication
 * still carries the token of the session that ended). Without a readable cookie (HttpOnly, or
 * none yet) the caller's header is kept.
 */
export function withCsrfHeader(
  headers: HeadersInit | undefined,
  method: string | undefined,
  token: string | null,
  headerName: string = DEFAULT_CSRF_HEADER,
): Headers {
  const result = new Headers(headers)
  if (!isSafeMethod(method) && token !== null && token !== '') result.set(headerName, token)
  return result
}

// ─────────────────────────────────────────────────────────────────────────────
// URLs
// ─────────────────────────────────────────────────────────────────────────────

function apiUrl(apiHost: string, path: string): URL {
  return new URL(path, apiHost.endsWith('/') ? apiHost : `${apiHost}/`)
}

/** `<apiHost>oidc/auth-complete?m=<mode>` — the page that reports a silent/popup sign-in. */
export function authCompleteUrl(apiHost: string, mode: 'silent' | 'popup', failed = false): string {
  const url = apiUrl(apiHost, 'oidc/auth-complete')
  url.searchParams.set('m', mode)
  if (failed) url.searchParams.set('error', '1')
  return url.toString()
}

/** `<apiHost>oidc/authenticate?next=…[&fail=…][&prompt=none]`. */
export function authenticateUrl(
  apiHost: string,
  params: { next: string; fail?: string; prompt?: 'none' | 'login' },
): string {
  const url = apiUrl(apiHost, 'oidc/authenticate')
  url.searchParams.set('next', params.next)
  if (params.fail !== undefined) url.searchParams.set('fail', params.fail)
  if (params.prompt !== undefined) url.searchParams.set('prompt', params.prompt)
  return url.toString()
}

/** `<apiHost>oidc/total_logout?next=…&fail=…` — explicit "Sign out" only. */
export function totalLogoutUrl(apiHost: string, next: string, fail: string = next): string {
  const url = apiUrl(apiHost, 'oidc/total_logout')
  url.searchParams.set('next', next)
  url.searchParams.set('fail', fail)
  return url.toString()
}

// ─────────────────────────────────────────────────────────────────────────────
// Redirect-loop guard (scoped; keys compatible with PAC's redirectLoopHelpers)
// ─────────────────────────────────────────────────────────────────────────────

export type LoopScope = 'shell' | 'embed' | 'streamlit'

export const LOOP_MAX_ATTEMPTS = 3
export const LOOP_WINDOW_MS = 30_000
export const LOOP_ATTEMPTS_KEY = 'auth_redirect_attempts'
export const LOOP_TIMESTAMP_KEY = 'auth_redirect_timestamp'

export interface StorageLike {
  getItem: (key: string) => string | null
  setItem: (key: string, value: string) => void
  removeItem: (key: string) => void
}

function defaultStorage(): StorageLike | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage
  } catch {
    return null
  }
}

export function loopKeys(scope: LoopScope): { attempts: string; timestamp: string } {
  return scope === 'shell'
    ? { attempts: LOOP_ATTEMPTS_KEY, timestamp: LOOP_TIMESTAMP_KEY }
    : { attempts: `${LOOP_ATTEMPTS_KEY}_${scope}`, timestamp: `${LOOP_TIMESTAMP_KEY}_${scope}` }
}

export function isRedirectLoop(
  scope: LoopScope = 'shell',
  storage: StorageLike | null = defaultStorage(),
  now: number = Date.now(),
): boolean {
  if (storage === null) return false
  const keys = loopKeys(scope)
  const timestamp = Number(storage.getItem(keys.timestamp) ?? 0)
  const attempts = Number(storage.getItem(keys.attempts) ?? 0)
  if (now - timestamp > LOOP_WINDOW_MS) {
    storage.removeItem(keys.attempts)
    storage.removeItem(keys.timestamp)
    return false
  }
  return attempts >= LOOP_MAX_ATTEMPTS
}

export function trackRedirectAttempt(
  scope: LoopScope = 'shell',
  storage: StorageLike | null = defaultStorage(),
  now: number = Date.now(),
): void {
  if (storage === null) return
  const keys = loopKeys(scope)
  const timestamp = Number(storage.getItem(keys.timestamp) ?? 0)
  const current =
    now - timestamp <= LOOP_WINDOW_MS ? Number(storage.getItem(keys.attempts) ?? 0) : 0
  storage.setItem(keys.attempts, String(current + 1))
  storage.setItem(keys.timestamp, String(now))
}

export function clearRedirectTracking(
  scope: LoopScope = 'shell',
  storage: StorageLike | null = defaultStorage(),
): void {
  if (storage === null) return
  const keys = loopKeys(scope)
  storage.removeItem(keys.attempts)
  storage.removeItem(keys.timestamp)
}

// ─────────────────────────────────────────────────────────────────────────────
// postMessage (frames) and BroadcastChannel (tabs)
// ─────────────────────────────────────────────────────────────────────────────

export type LexAuthEvent =
  | 'required'
  | 'restored'
  | 'signed-out'
  | 'result'
  | 'logout'
  | 'reauth-started'
export type LexAuthSurface = 'shell' | 'pac-embed' | 'streamlit' | 'reflex'

export interface LexAuthMessage {
  type: 'lex-auth'
  v: 2
  event: LexAuthEvent
  surface?: LexAuthSurface
  reason?: string
  id?: string
  mode?: 'silent' | 'popup'
  ok?: boolean
  error?: string
  /** BroadcastChannel only: the sending tab. */
  tab?: string
  at?: number
}

const EVENTS: readonly string[] = [
  'required',
  'restored',
  'signed-out',
  'result',
  'logout',
  'reauth-started',
]
const SURFACES: readonly string[] = ['shell', 'pac-embed', 'streamlit', 'reflex']

/** Schema check for a v2 auth message (rejects anything carrying unexpected types). */
export function isLexAuthMessage(data: unknown): data is LexAuthMessage {
  const record = asRecord(data)
  if (record === null || record.type !== 'lex-auth' || record.v !== 2) return false
  if (typeof record.event !== 'string' || !EVENTS.includes(record.event)) return false
  if (
    record.surface !== undefined &&
    !(typeof record.surface === 'string' && SURFACES.includes(record.surface))
  ) {
    return false
  }
  for (const key of ['reason', 'id', 'error', 'tab'] as const) {
    if (record[key] !== undefined && typeof record[key] !== 'string') return false
  }
  if (record.mode !== undefined && record.mode !== 'silent' && record.mode !== 'popup') return false
  if (record.ok !== undefined && typeof record.ok !== 'boolean') return false
  if (record.at !== undefined && typeof record.at !== 'number') return false
  return true
}

/** The pre-v2 message dashboards post: `{type: 'lex-auth-required', login}` (http(s) login only). */
export function readLegacyAuthRequired(data: unknown): { login: string } | null {
  const record = asRecord(data)
  if (record === null || record.type !== 'lex-auth-required' || typeof record.login !== 'string') {
    return null
  }
  try {
    const url = new URL(record.login)
    return url.protocol === 'https:' || url.protocol === 'http:' ? { login: url.toString() } : null
  } catch {
    return null
  }
}

function randomId(): string {
  const cryptoObject = typeof crypto === 'undefined' ? undefined : crypto
  if (cryptoObject !== undefined && typeof cryptoObject.randomUUID === 'function') {
    return cryptoObject.randomUUID()
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

export function createLexAuthMessage(
  event: LexAuthEvent,
  fields: Omit<Partial<LexAuthMessage>, 'type' | 'v' | 'event'> = {},
): LexAuthMessage {
  return { type: 'lex-auth', v: 2, event, id: randomId(), ...fields }
}

/** Exact-origin allow-list check (`'*'` and opaque origins are never allowed). */
export function isAllowedOrigin(origin: string, allowed: readonly string[]): boolean {
  if (origin === '' || origin === 'null' || origin === '*') return false
  return allowed.some((candidate) => candidate !== '*' && candidate === origin)
}

/** Post a v2 message to another window. Returns false when there is no usable target. */
export function postLexAuthMessage(
  target: Window | null | undefined,
  message: LexAuthMessage,
  targetOrigin: string,
): boolean {
  if (target === null || target === undefined || targetOrigin === '') return false
  try {
    target.postMessage(message, targetOrigin)
    return true
  } catch {
    return false
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// WebSocket close codes
// ─────────────────────────────────────────────────────────────────────────────

export const WS_CLOSE_NOT_AUTHENTICATED = 4401
export const WS_CLOSE_FORBIDDEN = 4403
export const WS_CLOSE_AUTH_UNAVAILABLE = 4503

export type WsCloseAction = 'reauth' | 'stop' | 'backoff' | 'reconnect'

/** 4401 → re-authenticate (stop reconnecting), 4403 → stop, 4503 → back off, else reconnect. */
export function wsCloseAction(code: number): WsCloseAction {
  if (code === WS_CLOSE_NOT_AUTHENTICATED) return 'reauth'
  if (code === WS_CLOSE_FORBIDDEN) return 'stop'
  if (code === WS_CLOSE_AUTH_UNAVAILABLE) return 'backoff'
  return 'reconnect'
}

// ─────────────────────────────────────────────────────────────────────────────
// Session probe and capabilities
// ─────────────────────────────────────────────────────────────────────────────

export interface SessionCapabilities {
  auth_contract?: number
  dashboard_handoff?: string[]
  ws_close_codes?: boolean
}

export interface SessionStatus {
  authenticated: boolean
  user?: unknown
  capabilities: SessionCapabilities
}

/**
 * `GET <apiHost>api/auth/session` (contract v2). Returns `null` when the server predates the
 * endpoint (404/405) so callers can fall back to legacy behaviour; `{authenticated:false}` on 401.
 */
export async function fetchSessionStatus(
  apiHost: string,
  fetchImpl: typeof fetch = fetch,
): Promise<SessionStatus | null> {
  let response: Response
  try {
    response = await fetchImpl(apiUrl(apiHost, 'api/auth/session').toString(), {
      credentials: 'include',
      redirect: 'manual',
      headers: { Accept: 'application/json' },
    })
  } catch {
    return null
  }
  if (response.status === 404 || response.status === 405) return null
  if (response.type === 'opaqueredirect' || response.status === 401) {
    return { authenticated: false, capabilities: {} }
  }
  if (!response.ok) return null
  const body = asRecord(await readBody(response))
  if (body === null) return null
  return {
    authenticated: body.authenticated === true,
    user: body.user,
    capabilities: (asRecord(body.capabilities) ?? {}) as SessionCapabilities,
  }
}

/**
 * True when the server says this browser is signed in. Tries `api/auth/session` (v2) and falls
 * back to `api/user/` for older servers.
 */
export async function probeSession(
  apiHost: string,
  fetchImpl: typeof fetch = fetch,
): Promise<boolean> {
  const status = await fetchSessionStatus(apiHost, fetchImpl)
  if (status !== null) return status.authenticated
  try {
    const response = await fetchImpl(apiUrl(apiHost, 'api/user/').toString(), {
      credentials: 'include',
      redirect: 'manual',
      headers: { Accept: 'application/json' },
    })
    return response.ok
  } catch {
    return false
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Drafts (save typed work before a full-page re-authentication)
// ─────────────────────────────────────────────────────────────────────────────

export interface DraftStore {
  save: (key: string, value: unknown) => void
  load: <T = unknown>(key: string) => T | null
  clear: (key: string) => void
  keys: () => string[]
}

/** JSON drafts in `sessionStorage` (per tab, never files or secrets). */
export function createDraftStore(
  storage: StorageLike | null = defaultStorage(),
  prefix = 'lex-auth-draft:',
): DraftStore {
  const all = (): string[] => {
    const raw = storage?.getItem(`${prefix}__keys`)
    if (raw === null || raw === undefined) return []
    try {
      const parsed: unknown = JSON.parse(raw)
      return Array.isArray(parsed) ? parsed.filter((k): k is string => typeof k === 'string') : []
    } catch {
      return []
    }
  }
  const setAll = (keys: string[]): void => {
    if (storage === null) return
    if (keys.length === 0) storage.removeItem(`${prefix}__keys`)
    else storage.setItem(`${prefix}__keys`, JSON.stringify(keys))
  }
  return {
    save: (key, value) => {
      if (storage === null) return
      try {
        storage.setItem(prefix + key, JSON.stringify(value))
        if (!all().includes(key)) setAll([...all(), key])
      } catch {
        /* quota or unserialisable value: drafts are best-effort */
      }
    },
    load: <T = unknown>(key: string): T | null => {
      const raw = storage?.getItem(prefix + key)
      if (raw === null || raw === undefined) return null
      try {
        return JSON.parse(raw) as T
      } catch {
        return null
      }
    },
    clear: (key) => {
      if (storage === null) return
      storage.removeItem(prefix + key)
      setAll(all().filter((k) => k !== key))
    },
    keys: all,
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Fetch wrapper: manual redirects, CSRF, retries, re-auth + single replay
// ─────────────────────────────────────────────────────────────────────────────

export interface AuthFetchOptions {
  fetch?: typeof fetch
  csrfCookieName?: string
  csrfHeaderName?: string
  retryPolicy?: Partial<RetryPolicy>
  sleep?: (ms: number) => Promise<void>
  random?: () => number
  /** This page's origin, to accept absolute same-origin `login_url`s. */
  origin?: string
  /**
   * Called when the session is gone. Resolve once it is restored — the request is then replayed
   * exactly once with a fresh CSRF header. Reject (or leave undefined) to return the 401 as-is.
   */
  onReauthRequired?: (outcome: ReauthOutcome) => Promise<void>
  /** Observability: every classified outcome (never contains tokens). */
  onOutcome?: (outcome: AuthOutcome, info: { url: string; method: string; attempt: number }) => void
  /** Optional: resolves when the browser is back online (used before retrying network errors). */
  waitForOnline?: () => Promise<void>
}

const defaultSleep = async (ms: number): Promise<void> =>
  await new Promise((resolve) => setTimeout(resolve, ms))

function requestMethod(input: RequestInfo | URL, init: RequestInit | undefined): string {
  if (init?.method !== undefined) return init.method.toUpperCase()
  if (typeof Request !== 'undefined' && input instanceof Request) return input.method.toUpperCase()
  return 'GET'
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.toString()
  return input.url
}

/**
 * A `fetch` with the contract's behaviour built in. It always resolves with a `Response` for any
 * HTTP answer (callers keep reading status/body as before); it rejects only for network errors
 * that are not retried, and for aborts. An opaque redirect (pre-v2 servers) becomes a 401.
 */
export function createAuthFetch(
  options: AuthFetchOptions = {},
): (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> {
  const policy: RetryPolicy = { ...DEFAULT_RETRY_POLICY, ...options.retryPolicy }
  const sleep = options.sleep ?? defaultSleep
  const random = options.random ?? Math.random
  const csrfCookie = options.csrfCookieName ?? DEFAULT_CSRF_COOKIE
  const csrfHeader = options.csrfHeaderName ?? DEFAULT_CSRF_HEADER

  return async function authFetch(
    input: RequestInfo | URL,
    init: RequestInit = {},
  ): Promise<Response> {
    const fetchImpl = options.fetch ?? fetch
    const method = requestMethod(input, init)
    const url = requestUrl(input)
    let replayed = false
    let retry = 0
    for (;;) {
      const isRequest = typeof Request !== 'undefined' && input instanceof Request
      // A Request carries its own headers and a one-shot body: merge the former and send a fresh
      // clone each attempt, so a replay after re-authentication still has its body.
      const attemptInput = isRequest ? (input as Request).clone() : input
      const headers = withCsrfHeader(
        init.headers ?? (isRequest ? (input as Request).headers : undefined),
        method,
        readCookie(csrfCookie),
        csrfHeader,
      )
      let response: Response
      try {
        response = await fetchImpl(attemptInput, {
          ...init,
          headers,
          credentials: init.credentials ?? 'include',
          redirect: init.redirect ?? 'manual',
        })
      } catch (error) {
        if (isAbortError(error)) throw error
        const outcome: AuthOutcome = { kind: 'network', status: 0 }
        options.onOutcome?.(outcome, { url, method, attempt: retry })
        retry += 1
        const delay = retryDelayMs(outcome, method, retry, policy, random)
        if (delay === null) throw error
        if (options.waitForOnline !== undefined) await options.waitForOnline()
        await sleep(delay)
        continue
      }
      if (response.type === 'opaqueredirect') response = syntheticUnauthenticatedResponse()
      const outcome: AuthOutcome = response.ok
        ? { kind: 'ok', status: response.status }
        : await classifyResponse(response, { origin: options.origin })
      options.onOutcome?.(outcome, { url, method, attempt: retry })
      if (outcome.kind === 'ok') return response
      if (outcome.kind === 'reauth') {
        if (options.onReauthRequired === undefined || replayed) return response
        try {
          await options.onReauthRequired(outcome)
        } catch {
          return response
        }
        replayed = true
        continue
      }
      retry += 1
      const delay = retryDelayMs(outcome, method, retry, policy, random)
      if (delay === null) return response
      await sleep(delay)
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Re-authentication ladder
// ─────────────────────────────────────────────────────────────────────────────

export type ReauthState = 'ok' | 'silent' | 'signed_out'
export type PopupResult = 'restored' | 'blocked' | 'closed'

export interface ReauthCoordinatorOptions {
  /** Absolute API base, e.g. `https://app.example.com/api/` (oidc/* and api/* hang off it). */
  apiHost: string
  /** Server says "signed in" (defaults to `probeSession(apiHost)`). */
  probe?: () => Promise<boolean>
  scope?: LoopScope
  surface?: LexAuthSurface
  /** Inside an iframe: never navigate (Keycloak refuses framing); tell the host instead. */
  embedded?: boolean
  /** Origins allowed to receive our `required`/`signed-out` messages (embedded only). */
  hostOrigins?: readonly string[]
  /** Persist typed work before a full-page navigation ("continue in this tab"). */
  beforeNavigate?: () => void
  silentTimeoutMs?: number
  popupPollMs?: number
  window?: Window
  storage?: StorageLike | null
  lockName?: string
  channelName?: string
}

export interface ReauthCoordinator {
  getState: () => ReauthState
  subscribe: (listener: (state: ReauthState) => void) => () => void
  /** Ask for re-authentication; resolves when the session is restored (by any tab/path). */
  requireReauth: (reason?: string) => Promise<void>
  /** User clicked "Sign in": open a popup. */
  signInWithPopup: () => Promise<PopupResult>
  /** User chose "continue in this tab" (or the popup was blocked). False when not allowed. */
  continueInThisTab: () => boolean
  /** Something else proved the session is back (e.g. a successful request). */
  markRestored: () => void
  /** Ask the server now; restores (and resolves waiters) when it says we are signed in. */
  recheck: () => Promise<boolean>
  dispose: () => void
}

/**
 * Where an embedded frame may send its auth events when the app did not pin host origins: the
 * embedding page's origin (`ancestorOrigins`, else the referrer). Falls back to `'*'` — safe only
 * because these messages never carry secrets.
 */
export function defaultHostOrigins(win: Window): string[] {
  try {
    const ancestors = win.location.ancestorOrigins
    if (ancestors !== undefined && ancestors.length > 0) return [ancestors[0]]
  } catch {
    /* not supported */
  }
  try {
    if (win.document.referrer !== '') return [new URL(win.document.referrer).origin]
  } catch {
    /* no usable referrer */
  }
  return ['*']
}

interface LockManagerLike {
  request: (
    name: string,
    options: { ifAvailable: boolean },
    callback: (lock: unknown) => Promise<void>,
  ) => Promise<void>
}

export function createReauthCoordinator(options: ReauthCoordinatorOptions): ReauthCoordinator {
  const win: Window = options.window ?? window
  const scope: LoopScope = options.scope ?? (options.embedded === true ? 'embed' : 'shell')
  const storage = options.storage === undefined ? defaultStorage() : options.storage
  const silentTimeoutMs = options.silentTimeoutMs ?? 8000
  const popupPollMs = options.popupPollMs ?? 500
  const apiOrigin = new URL(options.apiHost, win.location.href).origin
  const probe = options.probe ?? (async () => await probeSession(options.apiHost))
  const tab = randomId()

  let state: ReauthState = 'ok'
  let waiters: Array<() => void> = []
  const listeners = new Set<(state: ReauthState) => void>()
  let disposed = false
  let silentIframe: HTMLIFrameElement | null = null

  let channel: BroadcastChannel | null = null
  try {
    channel =
      typeof BroadcastChannel === 'undefined'
        ? null
        : new BroadcastChannel(options.channelName ?? 'lex-auth')
  } catch {
    channel = null
  }

  const setState = (next: ReauthState): void => {
    if (state === next) return
    state = next
    listeners.forEach((listener) => {
      listener(next)
    })
  }

  const broadcast = (event: LexAuthEvent): void => {
    try {
      channel?.postMessage(createLexAuthMessage(event, { tab, at: Date.now() }))
    } catch {
      /* channel closed */
    }
  }

  const tellHost = (event: LexAuthEvent, reason?: string): void => {
    if (options.embedded !== true) return
    const message = createLexAuthMessage(event, { surface: options.surface ?? 'pac-embed', reason })
    const targets = new Set<Window>()
    try {
      if (win.parent !== win) targets.add(win.parent)
      if (win.top !== null && win.top !== win) targets.add(win.top)
    } catch {
      /* cross-origin access to top is fine for postMessage; ignore anything else */
    }
    const origins = options.hostOrigins ?? defaultHostOrigins(win)
    for (const origin of origins) {
      targets.forEach((target) => postLexAuthMessage(target, message, origin))
    }
  }

  const restore = (): void => {
    if (state === 'ok') return
    clearRedirectTracking(scope, storage)
    setState('ok')
    const pending = waiters
    waiters = []
    pending.forEach((resolve) => {
      resolve()
    })
    broadcast('restored')
    tellHost('restored')
  }

  const signedOut = (reason?: string): void => {
    if (state === 'ok') return
    setState('signed_out')
    broadcast('signed-out')
    tellHost('required', reason ?? 'login_required')
  }

  const confirmWithProbe = async (): Promise<boolean> => {
    try {
      if (await probe()) {
        restore()
        return true
      }
    } catch {
      /* treat as not signed in */
    }
    return false
  }

  const removeSilentIframe = (): void => {
    silentIframe?.remove()
    silentIframe = null
  }

  /** Hidden same-origin iframe through `prompt=none`; resolves true when a session came back. */
  const runSilentAttempt = async (): Promise<boolean> =>
    await new Promise<boolean>((resolve) => {
      let settled = false
      const finish = (ok: boolean): void => {
        if (settled) return
        settled = true
        win.removeEventListener('message', onMessage)
        win.clearTimeout(timer)
        removeSilentIframe()
        resolve(ok)
      }
      const onMessage = (event: MessageEvent): void => {
        if (event.origin !== apiOrigin || !isLexAuthMessage(event.data)) return
        if (event.data.event === 'result' && event.data.mode === 'silent')
          finish(event.data.ok === true)
      }
      win.addEventListener('message', onMessage)
      const timer = win.setTimeout(() => {
        finish(false)
      }, silentTimeoutMs)
      try {
        const iframe = win.document.createElement('iframe')
        iframe.setAttribute('aria-hidden', 'true')
        iframe.setAttribute('tabindex', '-1')
        iframe.title = 'session renewal'
        iframe.style.display = 'none'
        iframe.src = authenticateUrl(options.apiHost, {
          next: authCompleteUrl(options.apiHost, 'silent'),
          fail: authCompleteUrl(options.apiHost, 'silent', true),
          prompt: 'none',
        })
        silentIframe = iframe
        win.document.body.appendChild(iframe)
      } catch {
        finish(false)
      }
    })

  const runLadder = async (reason?: string): Promise<void> => {
    await runSilentAttempt()
    if (disposed) return
    // The silent attempt's own report is advisory (older servers have no auth-complete page and
    // report nothing): the server's answer to the probe decides.
    if (await confirmWithProbe()) return
    signedOut(reason)
  }

  const follow = (reason?: string): void => {
    // Another tab leads. If it disappears without broadcasting, decide for ourselves.
    win.setTimeout(() => {
      if (disposed || state !== 'silent') return
      void confirmWithProbe().then((ok) => {
        if (!ok) signedOut(reason)
      })
    }, silentTimeoutMs + 2000)
  }

  const lead = async (reason?: string): Promise<void> => {
    const locks = (win.navigator as unknown as { locks?: LockManagerLike }).locks
    if (locks === undefined || typeof locks.request !== 'function') {
      await runLadder(reason)
      return
    }
    try {
      await locks.request(options.lockName ?? 'lex-auth', { ifAvailable: true }, async (lock) => {
        if (lock === null) {
          follow(reason)
          return
        }
        await runLadder(reason)
      })
    } catch {
      await runLadder(reason)
    }
  }

  const onChannelMessage = (event: MessageEvent): void => {
    if (!isLexAuthMessage(event.data) || event.data.tab === tab) return
    if (event.data.event === 'restored' && state !== 'ok') void confirmWithProbe()
    if (event.data.event === 'signed-out' && state === 'silent') signedOut('login_required')
  }
  channel?.addEventListener('message', onChannelMessage)

  // A popup's auth-complete page may only be able to reach us through the channel (COOP).
  const onWindowMessage = (event: MessageEvent): void => {
    if (event.origin !== apiOrigin || !isLexAuthMessage(event.data)) return
    if (event.data.event === 'result' && event.data.mode === 'popup' && event.data.ok === true) {
      void confirmWithProbe()
    }
  }
  win.addEventListener('message', onWindowMessage)

  return {
    getState: () => state,
    subscribe: (listener) => {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    requireReauth: async (reason?: string): Promise<void> => {
      const done = new Promise<void>((resolve) => {
        waiters.push(resolve)
      })
      if (state === 'ok') {
        setState('silent')
        broadcast('reauth-started')
        void lead(reason)
      }
      await done
    },
    signInWithPopup: async (): Promise<PopupResult> => {
      const popup = win.open(
        authenticateUrl(options.apiHost, {
          next: authCompleteUrl(options.apiHost, 'popup'),
          fail: authCompleteUrl(options.apiHost, 'popup', true),
        }),
        'lex-auth',
        'popup,width=520,height=720',
      )
      if (popup === null) return 'blocked'
      return await new Promise<PopupResult>((resolve) => {
        const unsubscribe = (): void => {
          win.clearInterval(poll)
          listeners.delete(onState)
        }
        const onState = (next: ReauthState): void => {
          if (next === 'ok') {
            unsubscribe()
            resolve('restored')
          }
        }
        listeners.add(onState)
        const poll = win.setInterval(() => {
          if (!popup.closed) return
          unsubscribe()
          void confirmWithProbe().then((ok) => {
            resolve(ok ? 'restored' : 'closed')
          })
        }, popupPollMs)
      })
    },
    continueInThisTab: (): boolean => {
      if (options.embedded === true) {
        tellHost('required', 'login_required')
        return false
      }
      if (isRedirectLoop(scope, storage)) return false
      options.beforeNavigate?.()
      trackRedirectAttempt(scope, storage)
      win.location.assign(authenticateUrl(options.apiHost, { next: win.location.href }))
      return true
    },
    markRestored: restore,
    recheck: confirmWithProbe,
    dispose: () => {
      disposed = true
      removeSilentIframe()
      channel?.removeEventListener('message', onChannelMessage)
      win.removeEventListener('message', onWindowMessage)
      try {
        channel?.close()
      } catch {
        /* already closed */
      }
      listeners.clear()
    },
  }
}
