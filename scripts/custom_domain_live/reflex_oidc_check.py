"""Reflex's own sign-in, against a real Keycloak, on a customer's hostname.

Reflex Enterprise signs users in itself -- there is no proxy in front of it --
so the hostname question lands on `LexKeycloakAuthState._redirect_uri()`, which
derives from the browser's own URL. This drives that method's real output
through a real Keycloak: the PKCE authorization request, the login form, the
code that comes back, and the token exchange.

**This check passes on the revision before custom domains were wired through,
and that is the point of it.** Reflex's sign-in was always host-derived, so
unlike the Streamlit proxy it needed no change -- what was broken for Reflex
was the event websocket (see `reflex_check.py`), not the sign-in. What this
pins is that the sign-in STAYS host-derived: a reflex-enterprise upgrade that
built the redirect URI from a configured URL instead would break every attached
domain silently, and the only symptom would be users landing on the platform
hostname after signing in.

Expects the Keycloak from `keycloak_roundtrip_check.py` to be running, and
registers its own client in that realm:

    python scripts/custom_domain_live/keycloak_roundtrip_check.py   # starts it
    python scripts/custom_domain_live/reflex_oidc_check.py
"""
import base64, hashlib, html, json, os, pathlib, re, secrets, shutil, subprocess, sys, tempfile, time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

CHECKOUT = sys.argv[1] if len(sys.argv) > 1 else str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, CHECKOUT)

KC = "https://127.0.0.1:18443"
REALM, CLIENT, SECRET = "lex", "lex-reflex", "reflex-secret"
USER, PASSWORD = "dash.user", "dash-password"
CUSTOM = "reflex.app.customer.com"
PLATFORM = "inst-reflex.lit.excellence-cloud.de"
STRANGER = "reflex.not-registered.example.net"

PROJ = tempfile.mkdtemp(prefix="lex-reflex-oidc-")
pathlib.Path(PROJ, "rxconfig.py").write_text(
    "from lex.lex_app.reflex.config import lex_config\n\nconfig = lex_config()\n")
os.chdir(PROJ)
os.environ.update({
    "PROJECT_ROOT": PROJ,
    "REFLEX_URL": f"https://{PLATFORM}",
    "LEX_REFLEX_HOSTS": f"{PLATFORM},{CUSTOM}",
    "REFLEX_ACCESS_TOKEN": "unused",
    "DOMAIN_HOSTED": "inst.lit.excellence-cloud.de",
    "KEYCLOAK_URL": KC, "KEYCLOAK_REALM": REALM,
    "OIDC_RP_CLIENT_ID": CLIENT, "OIDC_RP_CLIENT_SECRET": SECRET,
    "OIDC_VERIFY_SSL": "false",
})

import reflex as rx  # noqa: E402
rx.App()  # loads lex's auth plugin, as `lex reflex run` does

from lex.lex_app.reflex.auth import LexKeycloakAuthState  # noqa: E402

HTTP = httpx.Client(verify=False, timeout=60)
failures, checks = [], 0


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        failures.append(msg)


def redirect_uri_for(host: str) -> str:
    """What Reflex computes for a browser on `host`. The real method, real input."""
    stub = SimpleNamespace(
        router=SimpleNamespace(url=f"https://{host}/dashboard?model=fund&pk=42"),
        _authorization_code_endpoint=LexKeycloakAuthState._authorization_code_endpoint,
    )
    return LexKeycloakAuthState._redirect_uri(stub)


CUSTOM_URI, PLATFORM_URI = redirect_uri_for(CUSTOM), redirect_uri_for(PLATFORM)
callback_path = LexKeycloakAuthState._authorization_code_endpoint()
check(CUSTOM_URI == f"https://{CUSTOM}{callback_path}",
      f"Reflex must return a browser on {CUSTOM} to itself; got {CUSTOM_URI}")
check(PLATFORM_URI == f"https://{PLATFORM}{callback_path}",
      f"the platform host must return to itself; got {PLATFORM_URI}")

# A client registered exactly as the Instance Controller registers it.
token = HTTP.post(f"{KC}/realms/master/protocol/openid-connect/token", data={
    "grant_type": "password", "client_id": "admin-cli",
    "username": "admin", "password": "admin"}).json()["access_token"]
H = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
HTTP.post(f"{KC}/admin/realms/{REALM}/clients", headers=H, content=json.dumps({
    "clientId": CLIENT, "enabled": True, "protocol": "openid-connect",
    "publicClient": False, "secret": SECRET, "standardFlowEnabled": True,
    "redirectUris": [CUSTOM_URI, PLATFORM_URI],
    "webOrigins": [f"https://{CUSTOM}", f"https://{PLATFORM}"],
}))


def sign_in(redirect_uri: str):
    """The PKCE authorization code flow Reflex performs, end to end."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").strip("=")
    params = {
        "client_id": CLIENT, "redirect_uri": redirect_uri, "scope": "openid profile email",
        "state": secrets.token_urlsafe(16), "code_challenge": challenge,
        "code_challenge_method": "S256", "response_type": "code", "response_mode": "query",
    }
    url = f"{KC}/realms/{REALM}/protocol/openid-connect/auth?{urlencode(params)}"
    with httpx.Client(verify=False, timeout=60, follow_redirects=True) as browser:
        page = browser.get(url)
        if page.status_code != 200:
            return None, f"authorization request refused: {page.status_code}"
        form = re.search(r'action="([^"]+)"', page.text)
        if not form:
            text = re.sub(r"<[^>]+>", " ", page.text)
            return None, " ".join(text.split())[:160]
        posted = browser.post(html.unescape(form.group(1)),
                              data={"username": USER, "password": PASSWORD},
                              follow_redirects=False)
    back = posted.headers.get("location", "")
    code = parse_qs(urlparse(back).query).get("code", [""])[0]
    if not code:
        return None, f"no code: {back[:120] or posted.status_code}"
    exchanged = HTTP.post(f"{KC}/realms/{REALM}/protocol/openid-connect/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "client_id": CLIENT, "client_secret": SECRET, "code_verifier": verifier})
    return {"landed_on": urlparse(back).hostname, "tokens": exchanged.json(),
            "status": exchanged.status_code}, None


for host, uri in ((CUSTOM, CUSTOM_URI), (PLATFORM, PLATFORM_URI)):
    result, err = sign_in(uri)
    check(result is not None, f"{host}: the flow did not complete -- {err}")
    if not result:
        continue
    check(result["landed_on"] == host,
          f"{host}: Keycloak returned the browser to {result['landed_on']}")
    check(result["status"] == 200,
          f"{host}: the token exchange was {result['status']}: {result['tokens']}")
    check("id_token" in result["tokens"],
          f"{host}: no id_token came back: {list(result['tokens'])}")

# Keycloak, not lex-app, is what refuses a hostname nobody registered.
stranger = f"https://{STRANGER}{callback_path}"
result, err = sign_in(stranger)
check(result is None, "an unregistered hostname must be refused by the IdP")
print(f"  (unregistered hostname refused by Keycloak: {err!r:.90})")

shutil.rmtree(PROJ, ignore_errors=True)
print(f"ran {checks} Reflex-OIDC checks against real Keycloak")
print(f"  redirect_uri on {CUSTOM}: {CUSTOM_URI}")
if failures:
    print(f"\n{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("all Reflex-OIDC checks passed")
