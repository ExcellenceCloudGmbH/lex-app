---
date: 2026-09-28
clusters: [1]
tests_added: 6
suite_tally: "init + permissions + api_layer: 803 pass / 0 fail / 16 skip (all environmental) — 1ar 6 pass"
---

# Batch 1ar — Reflex dashboards sign in by themselves

A user signed in to Lex App had to click **Login with Keycloak** on the Reflex dashboards, and again
after every restart of the Reflex server. Reflex Enterprise's `/login` waits for that click on
purpose; lex-app has one provider and the user a Keycloak session, so lex-app's `/login` now starts
the sign-in itself — the plugin's own redirect at the top level, a silent `prompt=none` request in a
frame — and shows the button only when a click is the one way left
([batch 1ar](../../clusters/01-init/batches.md), 1.370–1.375).

What the work taught, beyond the scenarios themselves:

* **Why a restart lost the session.** After the restart the browser still sent the ID token but no
  access token, and the plugin found nothing to refresh. The likeliest reason is an access token
  over the 4096 bytes a browser keeps in a cookie — Keycloak puts every role the user holds in the
  realm into it while the client allows full scope. Signing in by itself turns that into a silent
  bounce through Keycloak; 1.375's one-time warning names the cookie and the Keycloak setting.
* **React's development mode mounts `/login` twice**, and the second call fell into the retry window
  that stops redirect loops: the button flashed before every redirect. Found by watching the
  websocket in a browser; the page now sends one visit's identity, and 1.371 pins the repeat.
* **Chaining the plugin's `redirect_to_login` hid its failures** — a redirect that could not be
  built left the page spinning. `start_login` calls it inline; 1.372 pins the button that follows.
* **The browser checks had two false failures of their own.** The mock provider refuses
  `prompt=none` outright, so the wrapper that gives it a Keycloak-like session had to answer those
  itself; and Playwright turns off Chromium's third-party storage partitioning, so a frame shared the
  top-level tab's Reflex session and storage while its cookies stayed partitioned — the plugin's
  cross-tab token sync then fought itself. Neither was the app; both are noted so the next run does
  not chase them.

Thirteen mutations of the sign-in code each fail a 1ar test.
