"""A real Streamlit server behind the real proxy, reached on a custom hostname.

What this covers that the test suite does not: an instance with a
customer-owned domain attached answers on more than one hostname, and the
Streamlit proxy has to complete a sign-in on *whichever one the browser used*.
The cluster tests (1ay) drive that through the ASGI app; this drives it through
real sockets, with Streamlit actually running behind the proxy and uvicorn
honouring the `X-Forwarded-*` headers the ingress sets -- the layer the tests
cannot reach.

Needs `streamlit`, `uvicorn` and `httpx`; binds three loopback ports and
nothing else. No cluster, no Keycloak, no instance:

    python scripts/check_custom_domain_live.py [path-to-checkout]

On the revision before custom domains were wired through to the pods, three
checks fail: the frame-breakout page sends the top window to the PLATFORM
host's sign-in, and the sign-in's `redirect_uri` is the platform callback --
so the browser lands back on the attached hostname with no session cookie and
signs in again, forever.
"""
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse

import httpx

PROJECT = sys.argv[1] if len(sys.argv) > 1 else str(pathlib.Path(__file__).resolve().parents[1])
sys.path.insert(0, PROJECT)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


ST_PORT, PROXY_PORT, KC_PORT = free_port(), free_port(), free_port()
ISSUER = f"http://127.0.0.1:{KC_PORT}/realms/lex"


def _start_keycloak():
    """An OIDC discovery document, so the proxy can build a real sign-in."""
    import json
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.endswith("/.well-known/openid-configuration"):
                body = json.dumps({
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
                    "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
                    "userinfo_endpoint": f"{ISSUER}/protocol/openid-connect/userinfo",
                    "end_session_endpoint": f"{ISSUER}/protocol/openid-connect/logout",
                    "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
                    "response_types_supported": ["code"],
                    "subject_types_supported": ["public"],
                    "id_token_signing_alg_values_supported": ["RS256"],
                }).encode()
            elif self.path.endswith("/certs"):
                body = json.dumps({"keys": []}).encode()
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", KC_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


keycloak = _start_keycloak()
APP = "/tmp/lex_live_dashboard.py"
open(APP, "w").write("import streamlit as st\nst.title('Live dashboard')\n")

streamlit = subprocess.Popen(
    [sys.executable, "-m", "streamlit", "run", APP,
     "--server.port", str(ST_PORT), "--server.headless", "true",
     "--browser.gatherUsageStats", "false"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

for _ in range(300):
    try:
        if httpx.get(f"http://127.0.0.1:{ST_PORT}/_stcore/health", timeout=1).status_code == 200:
            break
    except Exception:
        time.sleep(0.2)
else:
    streamlit.kill()
    sys.exit("streamlit never became healthy")

os.environ.update({
    "DOMAIN_HOSTED": "inst.lit.excellence-cloud.de",
    "STREAMLIT_URL": "https://inst-streamlit.lit.excellence-cloud.de",
    "LEX_APP_HOSTS": "inst.lit.excellence-cloud.de,app.customer.com",
    "LEX_STREAMLIT_HOSTS": "inst-streamlit.lit.excellence-cloud.de,streamlit.app.customer.com",
    "SESSION_SECRET": "fixed-for-this-check",
    "UPSTREAM": f"http://127.0.0.1:{ST_PORT}",
    "KEYCLOAK_URL": f"http://127.0.0.1:{KC_PORT}",
    "KEYCLOAK_REALM": "lex",
    "OIDC_RP_CLIENT_ID": "lex-instance",
    "OIDC_RP_CLIENT_SECRET": "secret",
})

import uvicorn  # noqa: E402

import lex.proxy as proxy  # noqa: E402


server = uvicorn.Server(uvicorn.Config(
    proxy.app, host="127.0.0.1", port=PROXY_PORT, log_level="error",
    proxy_headers=True, forwarded_allow_ips="*",
))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    try:
        httpx.get(f"http://127.0.0.1:{PROXY_PORT}/_stcore/health", timeout=0.5)
        break
    except Exception:
        time.sleep(0.1)

failures, checks = [], 0


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        failures.append(msg)


def via(host, path, extra=None):
    """A request as the ingress delivers it; a transport error is reported, not raised."""
    headers = {"host": host, "x-forwarded-host": host, "x-forwarded-proto": "https"}
    headers.update(extra or {})
    try:
        return httpx.get(
            f"http://127.0.0.1:{PROXY_PORT}{path}",
            headers=headers, follow_redirects=False, timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  !! {host}{path} raised {type(exc).__name__}: {exc}")
        raise


CUSTOM = "streamlit.app.customer.com"
PLATFORM = "inst-streamlit.lit.excellence-cloud.de"

# Streamlit's own health, proxied, on the attached hostname.
for host in (CUSTOM, PLATFORM):
    r = via(host, "/_stcore/health")
    check(r.status_code == 200, f"{host}: /_stcore/health was {r.status_code}")
    check("ok" in r.text.lower(), f"{host}: health body was {r.text[:60]!r}")

# The dashboard document itself, unauthenticated: the proxy must break out to a
# top-level sign-in rather than proxy the page.
# A browser's document load: Accept names HTML, and the frame says it is one.
# Without the Accept header the proxy answers 401 JSON, which is right for an
# XHR and is not the path under test.
r = via(CUSTOM, "/", {
    "sec-fetch-dest": "iframe",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})
# The breakout page is served WITH a 401 on purpose, so an XHR that happens to
# accept HTML still sees the failure; the navigation is done by its script.
check(r.status_code in (200, 401, 302, 303, 307),
      f"an unauthenticated dashboard load was {r.status_code}")
check("window.top" in r.text,
      "an iframe document load must break out of the frame, never redirect inside it")
body_or_location = r.headers.get("location", "") + r.text[:4000]
check("/auth/login" in body_or_location,
      "an unauthenticated load must lead to this proxy's own sign-in")
check(f"https://{CUSTOM}/auth/login" in body_or_location,
      f"the breakout must send the top window to {CUSTOM}'s own sign-in")
check(PLATFORM not in body_or_location,
      f"the breakout must stay on {CUSTOM}, but it named the platform host")

# And the sign-in it leads to really does name the hostname the browser is on.
r = via(CUSTOM, "/auth/login")
check(r.status_code in (302, 303, 307), f"/auth/login on the attached host was {r.status_code}")
redirect_uri = parse_qs(urlparse(r.headers.get("location", "")).query).get("redirect_uri", [""])[0]
check(redirect_uri == f"https://{CUSTOM}/auth/callback",
      f"the live sign-in must return to {CUSTOM}; got {redirect_uri!r}")

r = via(PLATFORM, "/auth/login")
redirect_uri = parse_qs(urlparse(r.headers.get("location", "")).query).get("redirect_uri", [""])[0]
check(redirect_uri == f"https://{PLATFORM}/auth/callback",
      f"the platform host must still return to itself; got {redirect_uri!r}")

streamlit.kill()
server.should_exit = True
keycloak.shutdown()

print(f"ran {checks} live Streamlit checks (real streamlit {ST_PORT} behind real proxy {PROXY_PORT})")
if failures:
    print(f"\n{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all live Streamlit checks passed")
