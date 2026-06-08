# Log cleanup & lex-only debug control — Design

> **Date:** 2026-06-07
> **Ticket:** EXC-1787 — "Avoid Warning Message on local computer"
> **Reporter:** Joos Sauer (via Melih Sunbul)
> **Status:** Design — pending implementation

## Problem

Two related log-noise issues when running LEX App V2 locally:

1. **`InsecureRequestWarning` spam.** Running the app against the Keycloak auth host
   (`auth.excellence-cloud.de`) prints, on every request:

   ```
   urllib3/connectionpool.py:1110: InsecureRequestWarning: Unverified HTTPS request
   is being made to host 'auth.excellence-cloud.de'. Adding certificate verification
   is strongly advised.
   ```

   Root cause: `lex/api/views/authentication/KeycloakManager.py:98` reads
   `verify_ssl = getattr(settings, "KEYCLOAK_VERIFY_SSL", False)` — TLS verification
   defaults to **off**, so urllib3 emits the warning. Nothing in the codebase calls
   `urllib3.disable_warnings()` today.

2. **No targeted debug control.** The only log knob is `LOG_LEVEL`
   (`lex/lex_app/settings.py:697-707`). Setting `LOG_LEVEL=DEBUG` raises `ROOT_LEVEL`
   to DEBUG, which floods the console with debug output from every third-party library.
   There is no `lex` logger in the `LOGGING` dict — `lex.*` modules just propagate to
   root — so there is no way to see `logger.debug(...)` from the framework alone.

## Goals

- Suppress the `InsecureRequestWarning` so local logs are clean out of the box.
- Give users a way to enable DEBUG logging for the **lex framework only**, without
  pulling in third-party library debug noise.
- Do not change existing behavior of `LOG_LEVEL`.
- Keep both behaviors env-var controlled and reversible.

## Non-goals

- **Not** changing the `verify=False` TLS default in `KeycloakManager` or the Keycloak
  management commands. That is a separate security decision, out of scope for this ticket.
- Not adding per-module/per-logger override syntax (considered, rejected as YAGNI).

## Design

### Part A — Suppress `InsecureRequestWarning`

In `lex/lex_app/settings.py`, alongside the existing
`warnings.simplefilter("ignore", CacheKeyWarning)` (~line 66), add a gated suppression:

```python
import urllib3

if os.getenv("LEX_SUPPRESS_INSECURE_WARNING", "True").lower() == "true":
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
```

- Settings is imported exactly once per process (web server **and** Celery workers),
  so the suppression applies process-wide and persists for the process lifetime.
- Default = suppressed (matches the ticket's "cleaner logs" request).
- Escape hatch: `LEX_SUPPRESS_INSECURE_WARNING=False` restores the warning for anyone
  debugging TLS.

### Part B — `lex`-only debug logging

1. New env var, independent of `LOG_LEVEL`, in `settings.py`:

   ```python
   LEX_LOG_LEVEL = os.getenv("LEX_LOG_LEVEL", "INFO").upper()
   ```

2. Add a `lex` logger to the `LOGGING` dict so the whole `lex.*` namespace is controlled
   in one place:

   ```python
   "lex": {
       "handlers": ["console"],
       "level": LEX_LOG_LEVEL,
       "propagate": False,
   },
   ```

3. Lower the **console handler** level so it does not swallow `lex` DEBUG records.
   Logger-level gating still keeps third-party output quiet because the **root logger
   stays at `INFO`** — any third-party DEBUG record is dropped at its logger before it
   ever reaches a handler:

   ```python
   # console handler level = the more verbose of the two, computed safely
   _console_handler_level = min(
       logging.getLevelName(CONSOLE_LEVEL),
       logging.getLevelName(LEX_LOG_LEVEL),
   )
   ```

   (`logging.getLevelName("DEBUG")` → `10`; lower number = more verbose.)

#### Why this works

| Logger | Level | Effect |
|---|---|---|
| `lex` (new) | `LEX_LOG_LEVEL` | `DEBUG` here surfaces all `lex.*` debug logs |
| `root` | `ROOT_LEVEL` (INFO) | Third-party DEBUG dropped at logger level, never reaches console |
| console handler | `min(CONSOLE_LEVEL, LEX_LOG_LEVEL)` | Allows lex DEBUG through; root still gates everything else |

Result: `LEX_LOG_LEVEL=DEBUG` shows `logger.debug(...)` from `lex.lex_app.celery_tasks`,
`lex.api.*`, etc., and nothing else. `LOG_LEVEL` retains its current meaning for users
who want everything.

#### Interaction with existing loggers

- `lex.calclog` already has an explicit entry (`propagate: False`) and is unaffected —
  child loggers with their own config take precedence over the new `lex` parent entry.
- `oauth2_authcodeflow`, `django_lifecycle`, `opentelemetry`, `azure` keep their explicit
  levels; none are under the `lex.*` namespace.

### Documentation

There is currently **no** `.env.example` or doc that lists env vars. Add a short reference
documenting the logging/warning env vars (`LOG_LEVEL`, `LEX_LOG_LEVEL`,
`LEX_SUPPRESS_INSECURE_WARNING`) — location to be decided during implementation
(likely a new `docs/features/` or settings reference page).

## Env vars summary

| Var | Default | Purpose |
|---|---|---|
| `LEX_SUPPRESS_INSECURE_WARNING` | `True` | Suppress urllib3 `InsecureRequestWarning`. Set `False` to restore it. |
| `LEX_LOG_LEVEL` | `INFO` | Log level for the `lex.*` framework namespace only. Set `DEBUG` for framework debug logs without third-party noise. |
| `LOG_LEVEL` (existing) | `INFO` | App-wide level (console + root + oauth2). Unchanged. |

## Testing

Framework source under `lex/` is changing, so this needs paired cluster tests per the
lex-testing workflow. Coverage targets:

- **Warning suppression:** with `LEX_SUPPRESS_INSECURE_WARNING` default/true, the
  `InsecureRequestWarning` filter is installed; with `False`, it is not.
- **Logging config:** `LEX_LOG_LEVEL=DEBUG` yields a `lex` logger at DEBUG and a console
  handler level low enough to emit it, while `root` stays at `INFO`; default leaves `lex`
  at INFO.

## Files touched (anticipated)

- `lex/lex_app/settings.py` — warning suppression + `lex` logger + console handler level.
- New docs page for env vars.
- New/updated tests per lex-testing.
