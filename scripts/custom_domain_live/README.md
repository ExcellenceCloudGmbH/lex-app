# Live custom-domain checks

An instance with a customer-owned domain attached answers on more than one
hostname per role, and the parts that decide what to do with the hostname live
at the edges of the process: the Streamlit proxy's OIDC callback, Reflex's
websocket origin check, and the backend URL Reflex compiles into its frontend.
The cluster tests (1ay) drive those through the ASGI apps. These three scripts
drive them over real sockets, with the servers actually running — the layer the
tests cannot reach.

Nothing here needs a cluster, a Keycloak, a Reflex account or a deployed
instance. Each binds loopback ports and cleans up after itself.

| Script | What it runs | Needs |
| --- | --- | --- |
| `streamlit_check.py` | a real Streamlit server behind `lex/proxy.py` under uvicorn; health, the frame-breakout page and the sign-in redirect on both hostnames | `streamlit`, `uvicorn`, `httpx` |
| `reflex_check.py` | a real `rx.App` backend under uvicorn; the event websocket opened on both hostnames, a foreign origin refused, and the old canonical-only CORS refusing the attached hostname | `reflex`, `reflex-enterprise`, `uvicorn`, `httpx`, `websockets` |
| `get_backend_url_check.mjs` | Reflex's own `getBackendURL`, lifted verbatim out of the installed `state.js` and executed per hostname | `node` |
| `keycloak_roundtrip_check.py` | the whole authorization code flow against a **real Keycloak** it starts in Docker: the proxy's `/auth/login`, Keycloak's own login form, the code exchange, and a signed-in request that reaches the dashboard | `docker`, `openssl`, `streamlit`, `uvicorn`, `httpx` |

```bash
python scripts/custom_domain_live/keycloak_roundtrip_check.py
python scripts/custom_domain_live/streamlit_check.py
python scripts/custom_domain_live/reflex_check.py
node scripts/custom_domain_live/get_backend_url_check.mjs \
  "$(python -c 'import reflex_base,pathlib;print(pathlib.Path(reflex_base.__file__).parent/".templates/web/utils/state.js")')" \
  http://localhost:8000 reflex.app.customer.com inst-reflex.lit.excellence-cloud.de
```

## What they catch

Run against the revision before custom domains were wired through to the pods,
`streamlit_check.py` fails three of its twelve checks, and they are the bug in
full: the frame-breakout page sends the top window to the **platform** host's
sign-in and the `redirect_uri` is the platform callback, so a browser on the
attached hostname returns without a session cookie and signs in again, forever.

`reflex_check.py` carries its own before-and-after: it stands up a second
backend configured the way `start_reflex.sh` used to export it — CORS set to
the canonical host alone — and shows the attached hostname refused at the
websocket handshake.

`keycloak_roundtrip_check.py` is the one that leaves no room for doubt, because
the identity provider deciding what is acceptable is the real one. On the base
commit a user who starts on `streamlit.app.customer.com` is sent to Keycloak
with the **platform** callback, and Keycloak returns them to the platform host —
so the hostname they started on still has no session, and the next visit signs
in again. On this branch the `redirect_uri` is the customer's, the code is
exchanged there, the session cookie is set host-only, and a request carrying it
returns Streamlit's own document.

It starts Keycloak over HTTPS with a throwaway certificate, because Keycloak
marks its auth cookies `Secure` and a plain-HTTP client cannot send them back —
every login POST is answered "Cookie not found". The container is removed again
on the way out.

`get_backend_url_check.mjs` shows the third failure mode directly: with
`api_url` left on localhost, a page on either hostname opens
`wss://<its own hostname>/_event`; with a public URL pinned in, a page on the
customer's domain opens a websocket back to the platform host — cross-origin,
carrying no cookies.
