---
date: 2026-09-04
clusters: [1]
tests_added: 40
suite_tally: "1ad: 40 pass / 0 fail, 31 subtests; init cluster: 353 pass / 13 skip / 119 subtests on a clean run (BUG-029 makes it intermittent)"
---

# Batch 1ad — Streamlit's asset bundle served ungated, and session durability

Fourth batch on this auth path, and the one that explains the other three.
[Batch 1z](../../clusters/01-init/batches.md) made expiry graceful,
[1aa](../../clusters/01-init/batches.md) let the embedded caller renew,
[1ac](../../clusters/01-init/batches.md) stopped the session expiring at all — and this
one addresses why the same auth path was also producing a 401 flood, a four-times-heavier
cold start, and `TypeError` dialogs in front of customers.

## What made it one bug rather than three

Three reports arrived separately: an idle dashboard resetting to the first page, Streamlit
taking forever to start "because it is getting all the JS libraries", and TypeErrors
appearing from nowhere. They are one causal chain, and the arithmetic is the whole
explanation. Streamlit 1.61 ships **365** code-split JS chunks and names **107** of them in
eager `modulepreload` tags; the proxy authenticated every one through a single catch-all
route whose deny ran *before* it looked at the path.

So one credential-less moment is a hundred simultaneous 401s. A **lazily** imported chunk
that 401s reaches the browser as `TypeError: Failed to fetch dynamically imported module`,
because Vite's dynamic `import()` has no other vocabulary for an HTTP error — which is why
the screenshots name `DownloadButton` and `DataFrame`, both lazy chunks. And the proxy read
the *decoded* upstream body, so it had to strip `Content-Encoding` to stay honest, silently
disabling compression for everything Streamlit serves.

## What the measurements changed

Two numbers redirected the work. The eager preload set is **1.77 MB** plaintext against
**0.42 MB** gzipped — a 4.17x saving that had simply been switched off, and the reason the
fix is "keep the encoded bytes" rather than "compress harder". And JWT validation, the
intuitive suspect for the slow start, is **0.045 ms per request** — 16 ms across all 365
chunks. It is recorded in the batch note as explicitly *not* a cause, so the next person
does not optimise it.

The real event-loop hazard was next door: `_get_jwks_sync` fetched with a **sync** client on
the loop (up to 10s of total stall, on boot and every hourly TTL lapse, which could drop a
live dashboard's WebSocket) *and* returned `None` on failure while holding valid keys — so
one Keycloak blip rejected every token in the cluster.

## The half that was not a performance problem

`SESSION_SECRET` and `TOKEN_STORE` both defaulted to per-process values. A random secret is
not a weak secret, it is a *different* secret in every process: every restart and every
extra replica silently logs all users out, which is indistinguishable from the expiry bug
1ac had just fixed. Those configurations now refuse to boot, because degrading into the
symptom is what made them hard to find.

## Notes for whoever reads this next

- Scenarios 1.254, 1.255 and 1.271 pass against the pre-fix tree **by design**. They assert
  what must not change — the authenticated boundary, the WebSocket deny, and that
  legitimate configurations still start — and would be first to break if the public
  allowlist ever widened. The other 27 fail pre-fix.
- 1.257 found a latent bug while being written: the first stub was a buffered
  `httpx.Response(content=...)`, which cannot exercise the encoding passthrough because
  `.content` is decoded. That mismatch existed in `_iter_upstream` too, for any already-read
  response. A stub that cannot take the production path proves nothing about it.
- **BUG-029** was recorded, not fixed: `test_1i_rebase_incident_datetimes.py` is flaky
  (4 failed / 6 failed / 6 passed over three consecutive runs of that file alone) with a
  missing `lex_app_historicalsimpleitem` table. It reproduces with this batch's file
  excluded and predates it. Left unmarked deliberately — an xfail would hide a flake that
  currently stops cluster 1 gating a release.

## The review found ten more defects, all in the new code

Worth writing down, because the shape repeats. Three independent finder angles over
`lex/proxy.py`, `lex/bin/lex.py` and the React shell turned up ten further bugs — **every one
of them in the fix**, not in what it replaced. Scenarios 1.277–1.286 pin them; the
[batch entry](../../clusters/01-init/batches.md) lists each.

Four would have reproduced one of the three original symptoms by a different route:

- the `auth_token` strip redirect set only the session cookie, so it handed over *fewer*
  credentials than the response it replaced — and the deployments where that matters (Safari
  ITP, third-party-cookie blocking) are exactly the ones the change targets;
- the `/static` mount ignored `--server.baseUrlPath`, which would drop every asset back into
  the authenticated catch-all and restore the 401 storm, with the bundle present so nothing
  warned;
- a mid-body upstream failure became a truncated `200`, i.e. a silent `SyntaxError` in a Vite
  chunk — the same class of error, now unreportable;
- and in the shell, `authError` was left ungated where `isAuthLoading` had been gated, so a
  transient renewal failure unmounted a live dashboard every 15 s.

Two lessons for the next pass:

1. **A test double that cannot take the production path proves nothing about it.** 1.278 (an
   empty chunk on a 304) was reachable *only* through the buffered branch every stub takes;
   real `aiter_raw()` yields zero chunks. 1.257 had the same problem in the first pass, and the
   fix there was to make the stub stream.
2. **Mirrored logic drifts immediately.** `_warn_if_sessions_are_not_durable` was written to
   duplicate proxy.py's import-time rules deliberately, and shipped covering two of five — with
   a case-sensitive https test where proxy.py's normalises. 1.285 and 1.286 exist to keep the
   two in step, because the docstring saying "keep these in sync" did not.
