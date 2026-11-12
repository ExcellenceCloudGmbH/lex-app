"""The whole sign-in, against a REAL Keycloak, on a customer's hostname.

What this covers that nothing else does: the authorization code flow end to
end, with the identity provider that actually runs in production deciding
whether the `redirect_uri` is acceptable and where to send the user back to.
The proxy's `/auth/login`, Keycloak's own login form, the code exchange, the
session cookie that comes back -- and then a request carrying that cookie,
which must reach the dashboard rather than be sent to sign in again.

It starts its own Keycloak in Docker (over HTTPS with a throwaway certificate,
because Keycloak marks its auth cookies `Secure` and a plain-HTTP client cannot
send them back), creates the realm, the confidential client with BOTH redirect
URIs the Instance Controller registers, and a user. It stops the container
again on the way out. Nothing outside Docker is touched.

    python scripts/custom_domain_live/keycloak_roundtrip_check.py [path-to-checkout]

On the revision before custom domains were wired through to the pods, two
checks fail and they are the loop in full: a user who starts on
`streamlit.app.customer.com` is sent to Keycloak with the PLATFORM callback,
and Keycloak returns them to the platform host -- so the hostname they started
on still has no session, and the next visit signs in again.

Needs `docker`, `streamlit`, `uvicorn` and `httpx`. Skipped where Docker is not
available.
"""

import html
import json
import os
import pathlib
import re
import shutil
import tempfile
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse

import httpx

CHECKOUT = sys.argv[1] if len(sys.argv) > 1 else str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, CHECKOUT)

REALM, CLIENT, SECRET = "lex", "lex-instance", "instance-secret"
USER, PASSWORD = "dash.user", "dash-password"
CUSTOM = "streamlit.app.customer.com"
PLATFORM = "inst-streamlit.lit.excellence-cloud.de"

if shutil.which("docker") is None:
    print("skipped: docker is not available, and this check runs a real Keycloak")
    raise SystemExit(0)

ADMIN_HTTP = httpx.Client(verify=False, timeout=60)


# ---------------------------------------------------------------------------
# the Keycloak this check runs against
# ---------------------------------------------------------------------------
CONTAINER = "lex-custom-domain-keycloak"
KC_IMAGE = os.environ.get("LEX_KEYCLOAK_IMAGE", "quay.io/keycloak/keycloak:26.0")
KC_PORT = int(os.environ.get("LEX_KEYCLOAK_PORT", "18443"))
KC = f"https://127.0.0.1:{KC_PORT}"
ADMIN = "admin"


def start_keycloak(certs: str) -> None:
    """Run Keycloak over HTTPS with a throwaway certificate.

    HTTPS rather than plain HTTP because Keycloak marks `AUTH_SESSION_ID` and
    `KC_RESTART` `Secure`; a client on http cannot send them back and every
    login POST is answered "Cookie not found".
    """
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", f"{certs}/key.pem", "-out", f"{certs}/cert.pem",
                    "-days", "1", "-subj", "/CN=127.0.0.1",
                    "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost"],
                   check=True, capture_output=True)
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run([
        "docker", "run", "-d", "--name", CONTAINER,
        "-p", f"{KC_PORT}:8443", "-v", f"{certs}:/certs:ro",
        "-e", f"KC_BOOTSTRAP_ADMIN_USERNAME={ADMIN}",
        "-e", f"KC_BOOTSTRAP_ADMIN_PASSWORD={ADMIN}",
        "-e", "KC_HTTPS_CERTIFICATE_FILE=/certs/cert.pem",
        "-e", "KC_HTTPS_CERTIFICATE_KEY_FILE=/certs/key.pem",
        KC_IMAGE, "start-dev",
    ], check=True, capture_output=True)
    for _ in range(300):
        try:
            if ADMIN_HTTP.get(f"{KC}/realms/master/.well-known/openid-configuration").status_code == 200:
                return
        except Exception:
            time.sleep(1)
    raise SystemExit("Keycloak did not come up")


def seed_realm() -> None:
    """The realm, client and user an instance's dashboard signs in with.

    Both redirect URIs are registered, exactly as the Instance Controller
    registers them for an instance with a domain attached -- so Keycloak itself,
    not this script, decides whether the callback the proxy asks for is allowed.
    """
    token = ADMIN_HTTP.post(f"{KC}/realms/master/protocol/openid-connect/token", data={
        "grant_type": "password", "client_id": "admin-cli",
        "username": ADMIN, "password": ADMIN}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    ADMIN_HTTP.post(f"{KC}/admin/realms", headers=headers,
                    content=json.dumps({"realm": REALM, "enabled": True}))
    ADMIN_HTTP.post(f"{KC}/admin/realms/{REALM}/clients", headers=headers, content=json.dumps({
        "clientId": CLIENT, "enabled": True, "protocol": "openid-connect",
        "publicClient": False, "secret": SECRET, "standardFlowEnabled": True,
        "redirectUris": [f"https://{CUSTOM}/*", f"https://{PLATFORM}/*"],
        "webOrigins": [f"https://{CUSTOM}", f"https://{PLATFORM}"],
    }))
    ADMIN_HTTP.post(f"{KC}/admin/realms/{REALM}/users", headers=headers, content=json.dumps({
        "username": USER, "enabled": True, "emailVerified": True,
        "email": f"{USER}@example.com",
        # Keycloak interrupts the flow with a VERIFY_PROFILE action for a user
        # missing either name, which is not what this check is about.
        "firstName": "Dash", "lastName": "User",
        "credentials": [{"type": "password", "value": PASSWORD, "temporary": False}],
    }))


CERTS = tempfile.mkdtemp(prefix="lex-kc-certs-")
start_keycloak(CERTS)
seed_realm()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


ST_PORT, PROXY_PORT = free_port(), free_port()

# A real Streamlit behind the proxy, so a signed-in request has somewhere to land.
APP = "/tmp/lex_oidc_dashboard.py"
open(APP, "w").write("import streamlit as st\nst.title('Signed in')\n")
streamlit = subprocess.Popen(
    [sys.executable, "-m", "streamlit", "run", APP, "--server.port", str(ST_PORT),
     "--server.headless", "true", "--browser.gatherUsageStats", "false"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(300):
    try:
        if httpx.get(f"http://127.0.0.1:{ST_PORT}/_stcore/health", timeout=1).status_code == 200:
            break
    except Exception:
        time.sleep(0.2)

os.environ.update({
    "DOMAIN_HOSTED": "inst.lit.excellence-cloud.de",
    "STREAMLIT_URL": f"https://{PLATFORM}",
    "LEX_APP_HOSTS": "inst.lit.excellence-cloud.de,app.customer.com",
    "LEX_STREAMLIT_HOSTS": f"{PLATFORM},{CUSTOM}",
    "SESSION_SECRET": "fixed-for-this-check",
    "UPSTREAM": f"http://127.0.0.1:{ST_PORT}",
    "KEYCLOAK_URL": KC,
    "KEYCLOAK_REALM": REALM,
    "OIDC_VERIFY_SSL": "false",  # the harness Keycloak is self-signed
    "OIDC_RP_CLIENT_ID": CLIENT,
    "OIDC_RP_CLIENT_SECRET": SECRET,
})

import uvicorn  # noqa: E402

import lex.proxy as proxy  # noqa: E402

server = uvicorn.Server(uvicorn.Config(
    proxy.app, host="127.0.0.1", port=PROXY_PORT, log_level="error",
    proxy_headers=True, forwarded_allow_ips="*"))
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


def ingress(host, cookies=None):
    h = {"host": host, "x-forwarded-host": host, "x-forwarded-proto": "https",
         "accept": "text/html,application/xhtml+xml"}
    if cookies:
        h["cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return h


def collect(response, jar):
    """What a browser would keep from Set-Cookie, and whether it is host-only."""
    for raw in response.headers.get_list("set-cookie"):
        name, _, rest = raw.partition("=")
        value = rest.split(";")[0]
        jar[name.strip()] = value
        if "domain=" in raw.lower():
            jar.setdefault("__domain_seen__", raw)
    return jar


def sign_in_on(host):
    """Drive the full round trip as a browser on `host`. Returns (jar, notes)."""
    jar, notes = {}, {}

    r = httpx.get(f"http://127.0.0.1:{PROXY_PORT}/auth/login",
                  headers=ingress(host), follow_redirects=False, timeout=30)
    collect(r, jar)
    authorize = r.headers.get("location", "")
    notes["redirect_uri"] = parse_qs(urlparse(authorize).query).get("redirect_uri", [""])[0]

    # Keycloak's own login page and form. One Client for the whole visit, so
    # the AUTH_SESSION_ID and KC_RESTART cookies it sets across its redirects
    # are all carried back on the POST -- Keycloak answers 400 without them.
    with httpx.Client(follow_redirects=True, timeout=30, verify=False) as browser:
        page = browser.get(authorize)
        form = re.search(r'action="([^"]+)"', page.text)
        notes["login_form"] = bool(form)
        if not form:
            return jar, notes
        posted = browser.post(html.unescape(form.group(1)),
                              data={"username": USER, "password": PASSWORD},
                              follow_redirects=False)
    back = posted.headers.get("location", "")
    notes["callback_url"] = back
    query = parse_qs(urlparse(back).query)
    notes["code"] = query.get("code", [""])[0]

    # The browser now visits that callback. It is on whatever host Keycloak was
    # told to return to -- which is the point of the whole exercise.
    callback_host = urlparse(back).hostname or host
    r = httpx.get(f"http://127.0.0.1:{PROXY_PORT}{urlparse(back).path}",
                  params=urlparse(back).query, headers=ingress(callback_host, jar),
                  follow_redirects=False, timeout=30)
    notes["callback_status"] = r.status_code
    notes["callback_host"] = callback_host
    collect(r, jar)
    return jar, notes


jar, notes = sign_in_on(CUSTOM)

check(notes.get("redirect_uri") == f"https://{CUSTOM}/auth/callback",
      f"the IdP must be told to return to {CUSTOM}; got {notes.get('redirect_uri')!r}")
check(notes.get("login_form"), "Keycloak did not present a login form")
check(bool(notes.get("code")), f"no authorization code came back: {notes.get('callback_url')!r}")
check(notes.get("callback_host") == CUSTOM,
      f"the browser must land back on {CUSTOM}; it landed on {notes.get('callback_host')!r}")
check(notes.get("callback_status") in (302, 303, 307),
      f"the callback must redirect onward after exchanging the code; "
      f"got {notes.get('callback_status')}")
check("session" in jar, f"no session cookie was established; jar={list(jar)}")
check("__domain_seen__" not in jar,
      f"the session cookie must stay host-only: {jar.get('__domain_seen__')!r}")

# Now the signed-in request: it must reach the dashboard, not sign in again.
r = httpx.get(f"http://127.0.0.1:{PROXY_PORT}/", headers=ingress(CUSTOM, jar),
              follow_redirects=False, timeout=30)
check(r.status_code == 200,
      f"a signed-in request on {CUSTOM} was {r.status_code}, not the dashboard")
check("/auth/login" not in r.text[:4000],
      "a signed-in request must not be sent to sign in again")
check("streamlit" in r.text.lower() or "stApp" in r.text,
      "the response does not look like Streamlit's own document")

# The platform hostname must keep working in exactly the same way.
jar2, notes2 = sign_in_on(PLATFORM)
check(notes2.get("redirect_uri") == f"https://{PLATFORM}/auth/callback",
      f"the platform host must return to itself; got {notes2.get('redirect_uri')!r}")
r = httpx.get(f"http://127.0.0.1:{PROXY_PORT}/", headers=ingress(PLATFORM, jar2),
              follow_redirects=False, timeout=30)
check(r.status_code == 200, f"a signed-in request on {PLATFORM} was {r.status_code}")

streamlit.kill()
server.should_exit = True
subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
shutil.rmtree(CERTS, ignore_errors=True)

print(f"ran {checks} real-Keycloak round-trip checks")
print(f"  redirect_uri on {CUSTOM}: {notes.get('redirect_uri')}")
print(f"  code exchanged, session cookie: {[k for k in jar if not k.startswith('__')]}")
if failures:
    print(f"\n{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all real-Keycloak round-trip checks passed")
