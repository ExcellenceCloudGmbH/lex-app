# core/management/commands/bootstrap_callback_server.py

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class CallbackHandler(BaseHTTPRequestHandler):
    """
    Single-POST handler: receives JSON payload with Keycloak credentials,
    writes .env, sets state=done, and terminates the server.
    """

    env_file: Path = None
    state: str = None
    state_file: Path = None
    shutdown_event: threading.Event = None

    def do_OPTIONS(self):
        # CORS preflight
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
        except Exception:
            self.send_response(400)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"detail":"Invalid JSON"}')
            return

        state = data.get("state")
        if state != self.state:
            self.send_response(400)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"detail":"State mismatch"}')
            return

        # Write .env
        env_lines = [
            f"KEYCLOAK_URL={data.get('keycloak_url', '')}",
            f"KEYCLOAK_REALM={data.get('realm', '')}",
            f"OIDC_RP_CLIENT_ID={data.get('client_id', '')}",
            f"OIDC_RP_CLIENT_SECRET={data.get('client_secret', '')}",
            f"OIDC_RP_CLIENT_UUID={data.get('client_uuid', '')}",
        ]
        self.env_file.write_text("\n".join(env_lines) + "\n", encoding="utf-8")

        # Flip state
        state_data = {}
        if self.state_file.exists():
            try:
                state_data = json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        state_data[self.state] = "done"
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state_data), encoding="utf-8")
        tmp.replace(self.state_file)

        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

        # Trigger shutdown
        self.shutdown_event.set()

    def log_message(self, format, *args):
        # suppress logs or customize
        pass


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


def start_callback_server(
    state: str,
    env_file: Path,
    state_file: Path,
    host: str = "127.0.0.1",
    port: int = 0,
) -> tuple[HTTPServer, int]:
    """
    Start a minimal HTTP server on host:port (0 = pick free port).
    Returns (server, actual_port).
    """
    if port == 0:
        port = find_free_port()

    shutdown_event = threading.Event()

    CallbackHandler.env_file = env_file
    CallbackHandler.state = state
    CallbackHandler.state_file = state_file
    CallbackHandler.shutdown_event = shutdown_event

    server = HTTPServer((host, port), CallbackHandler)

    def run_server():
        # Wait until shutdown_event is set by the handler
        while not shutdown_event.is_set():
            server.handle_request()
        server.server_close()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

    return server, port
