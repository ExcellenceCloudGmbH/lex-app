"""A real Reflex backend, served, with a real websocket opened on each hostname.

What this covers that the test suite does not: `cors_allowed_origins` is
enforced by the socket.io server inside a running backend, and
`config.transport` is "websocket" -- so the only honest check is to open one.
This builds a real `rx.App` from an `rxconfig.py` that calls `lex_config()`,
serves its backend with uvicorn, and opens the event websocket on each
hostname an instance with a custom domain answers on.

It also reproduces the configuration this change replaced -- CORS set to the
canonical host alone, as `start_reflex.sh` used to export it -- and shows the
attached hostname being refused at the handshake.

Needs `reflex`, `reflex-enterprise`, `uvicorn`, `httpx` and `websockets`; binds
two loopback ports and writes an `rxconfig.py` into a temporary directory. No
cluster, no Keycloak, no Reflex account:

    python scripts/custom_domain_live/reflex_check.py [path-to-checkout]
"""
import asyncio
import os
import pathlib
import socket
import sys
import tempfile
import threading
import time

CHECKOUT = sys.argv[1] if len(sys.argv) > 1 else str(pathlib.Path(__file__).resolve().parents[2])
sys.path.insert(0, CHECKOUT)

# Reflex reads its configuration from the rxconfig.py of the directory it runs
# in, so the project is the fixture: this is what `lex reflex` writes.
PROJ = tempfile.mkdtemp(prefix="lex-reflex-live-")
pathlib.Path(PROJ, "rxconfig.py").write_text(
    "from lex.lex_app.reflex.config import lex_config\n\nconfig = lex_config()\n"
)
os.chdir(PROJ)
os.environ.update({
    "PROJECT_ROOT": PROJ,
    "REFLEX_URL": "https://inst-reflex.lit.excellence-cloud.de",
    "LEX_REFLEX_HOSTS": "inst-reflex.lit.excellence-cloud.de,reflex.app.customer.com",
    "REFLEX_ACCESS_TOKEN": "unused", "DOMAIN_HOSTED": "inst.lit.excellence-cloud.de",
})
import httpx, uvicorn, reflex as rx
from reflex_base.config import get_config
from websockets.asyncio.client import connect

cfg = get_config()
app = rx.App()

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

PORT = free_port()
srv = uvicorn.Server(uvicorn.Config(app._api, host="127.0.0.1", port=PORT,
                                    log_level="error", proxy_headers=True,
                                    forwarded_allow_ips="*"))
threading.Thread(target=srv.run, daemon=True).start()
for _ in range(300):
    try: httpx.get(f"http://127.0.0.1:{PORT}/ping", timeout=0.5); break
    except Exception: time.sleep(0.1)

failures, checks = [], 0
def check(c, m):
    global checks
    checks += 1
    if not c: failures.append(m)

CUSTOM, PLATFORM = "reflex.app.customer.com", "inst-reflex.lit.excellence-cloud.de"
check(cfg.transport == "websocket", f"transport is {cfg.transport!r}, not websocket")

async def open_socket(host, origin):
    """The engine.io websocket a Reflex page opens; returns the server's first frame."""
    url = f"ws://127.0.0.1:{PORT}/_event/?EIO=4&transport=websocket"
    async with connect(url, additional_headers={
        "Host": host, "X-Forwarded-Host": host,
        "X-Forwarded-Proto": "https", "Origin": origin,
    }, open_timeout=20) as ws:
        return await asyncio.wait_for(ws.recv(), timeout=20)

for host in (CUSTOM, PLATFORM):
    origin = f"https://{host}"
    try:
        frame = asyncio.run(open_socket(host, origin))
        check(b'"sid"' in (frame.encode() if isinstance(frame, str) else frame),
              f"{origin}: the server's opening frame carried no session id: {frame!r:.80}")
    except Exception as exc:
        check(False, f"{origin}: the websocket did not open: {type(exc).__name__}: {exc}")

try:
    asyncio.run(open_socket(CUSTOM, "https://attacker.example.net"))
    check(False, "a foreign origin must not be able to open the event websocket")
except Exception as exc:
    check(True, "")
    print(f"  (foreign origin refused: {type(exc).__name__})")

# The duplicate Access-Control-Allow-Origin on the POLLING path is Reflex's own
# (engine.io adds one, its CORSMiddleware another) and is identical with the
# stock `*` config -- recorded here so it is not mistaken for ours.
r = httpx.get(f"http://127.0.0.1:{PORT}/_event/", params={"EIO": "4", "transport": "polling"},
              headers={"origin": f"https://{CUSTOM}"}, timeout=10)
print(f"  (polling transport: {r.status_code}, "
      f"{len(r.headers.get_list('access-control-allow-origin'))} allow-origin headers "
      f"-- not the transport Reflex uses)")

# And what the configuration before this change produced: the start script
# exported REFLEX_CORS_ALLOWED_ORIGINS=$REFLEX_URL, i.e. the canonical host
# alone. A page on the attached hostname is then refused at the handshake --
# the dashboard renders and never connects.
old_cors = [f"https://{PLATFORM}"]
import socketio
old_sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=old_cors,
                               cors_credentials=True)
from starlette.applications import Starlette
from starlette.routing import Mount
old_app = Starlette(routes=[Mount("/_event", socketio.ASGIApp(old_sio, socketio_path=""))])
OLD_PORT = free_port()
old_srv = uvicorn.Server(uvicorn.Config(old_app, host="127.0.0.1", port=OLD_PORT,
                                        log_level="error"))
threading.Thread(target=old_srv.run, daemon=True).start()
time.sleep(1.5)

async def open_old(origin):
    url = f"ws://127.0.0.1:{OLD_PORT}/_event/?EIO=4&transport=websocket"
    async with connect(url, additional_headers={"Origin": origin}, open_timeout=20) as ws:
        return await asyncio.wait_for(ws.recv(), timeout=20)

try:
    asyncio.run(open_old(f"https://{PLATFORM}"))
    check(True, "")
except Exception as exc:
    check(False, f"the old config must still serve its canonical host: {exc}")

try:
    asyncio.run(open_old(f"https://{CUSTOM}"))
    check(False, "the OLD config should have refused the attached hostname, but did not")
except Exception as exc:
    check(True, "")
    print(f"  (old config refuses https://{CUSTOM}: {type(exc).__name__} -- the bug)")

old_srv.should_exit = True
srv.should_exit = True
print(f"ran {checks} live Reflex websocket checks")
if failures:
    print(f"\n{len(failures)} FAILED:")
    for f in failures:
        if f: print(f"  - {f}")
    sys.exit(1)
print("all live Reflex websocket checks passed")
