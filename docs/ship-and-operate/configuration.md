---
title: Configuration
---

Lex App is configured by environment variables. [[reference/Environment Variables|Environment Variables]] is the exhaustive list; this page is about the handful that decide whether an installation starts at all, and what the framework does when one is missing.

## The rule the framework follows

**Derive before demanding.** Where a value can be worked out from something every deployment already has, the framework works it out rather than making you set another variable. Where it genuinely cannot, it says so and refuses to start.

That principle was learned the hard way: an earlier release added a required `SESSION_SECRET` and stopped running instances from booting. Degrading is right for a *degraded* configuration; refusing is right only for a *broken* one.

## What must be set

| Variable | Why it cannot be derived |
|---|---|
| `DATABASE_*` | Where your data is |
| Keycloak client settings | Who is allowed in. `lex init` synchronises models and permissions against it |
| `DJANGO_SECRET_KEY` | The root of every derived secret below |

## What is derived, and from what

**Session signing.** The cookie that keeps a dashboard session alive is signed with a key resolved in this order:

1. `SESSION_SECRET`, if you set it — an explicit choice always wins.
2. Otherwise, **derived from `DJANGO_SECRET_KEY`** (HMAC-SHA256 under a fixed label). Terraform already generates that key, keeps it in state and ships it to every replica, so it is stable across restarts and identical on each one — exactly what session cookies need, with nothing new to configure.
3. Otherwise a random per-process value, with a warning. The process starts; users are signed out once per restart.

Derived rather than reused verbatim, so that a leaked cookie-signing key does not hand over Django's secret, or the other way round.

> [!warning] The published fallback secret is excluded on purpose
> If `DJANGO_SECRET_KEY` is still the example value from the shipped
> `settings.py`, it is **not** used for derivation. Deriving from it would
> give every Lex App installation in the world the same signing key, which is
> worse than a random one rather than better.

## What still refuses to start

Two configurations are broken rather than degraded, and the framework will not pretend otherwise:

- **Multiple replicas with no shared token store.** Sessions would work or not depending on which replica answered.
- **A `SameSite=None` cookie that browsers will discard.** The session would be created and then silently dropped on the next request.

## Local versus deployed

`lex setup` writes a `.env` for local development. It is not a deployment mechanism — a deployed installation gets its environment from your platform's secret store, and the `.env` file should not travel with the image.

## Related

- [[reference/Environment Variables|Environment Variables]] — every variable, with defaults
- [[reference/lex_config|lex_config.py]] — the per-project settings module
- [[ship-and-operate/deploying|Deploying]] — which processes read which of these
