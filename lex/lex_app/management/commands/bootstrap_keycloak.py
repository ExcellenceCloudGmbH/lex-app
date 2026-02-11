"""
Interactive Keycloak client creation via browser-based bootstrap flow.

Usage:
    python manage.py bootstrap_keycloak

Flow:
    1. Checks if Keycloak env vars are already configured.
    2. If not, starts a local callback server, builds the instance-controller URL,
       and opens it in the user's browser.
    3. Waits for the browser-based setup to POST credentials back to the callback server.
    4. Credentials are written to .env and the command completes.
"""
import uuid
import webbrowser

from django.core.management.base import BaseCommand

from lex_app.management.commands.Init import (
    ENV_FILE,
    STATE_FILE,
    build_instance_controller_url,
    get_missing_keycloak_env,
    wait_for_keycloak_setup,
)
from core.management.commands.bootstrap_callback_server import start_callback_server


class Command(BaseCommand):
    help = (
        "Bootstrap Keycloak client credentials interactively via a browser. "
        "Opens the instance-controller setup page and waits for credentials to be "
        "delivered via a local callback server."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=int,
            default=900,
            help="Timeout in seconds to wait for browser-based setup (default: 900).",
        )
        parser.add_argument(
            "--poll-interval",
            type=int,
            default=3,
            help="Poll interval in seconds while waiting for setup (default: 3).",
        )
        parser.add_argument(
            "--no-browser",
            action="store_true",
            default=False,
            help="Do not attempt to auto-open the URL in a browser.",
        )

    def handle(self, *args, **options):
        timeout = options["timeout"]
        poll_interval = options["poll_interval"]
        no_browser = options["no_browser"]

        # ── 1. Check if credentials are already present ──────────────────
        missing = get_missing_keycloak_env()
        if not missing:
            self.stdout.write(
                self.style.SUCCESS("Keycloak credentials are already configured. Nothing to do.")
            )
            return

        # ── 2. Generate state token & start local callback server ────────
        state = str(uuid.uuid4())

        server, port = start_callback_server(
            state=state,
            env_file=ENV_FILE,
            state_file=STATE_FILE,
            host="127.0.0.1",
            port=0,  # auto-pick a free port
        )

        callback_url = f"http://127.0.0.1:{port}/callback"

        # ── 3. Build instance-controller URL ─────────────────────────────
        try:
            url = build_instance_controller_url(state, callback_url)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Missing Keycloak env: {', '.join(missing)}"))
            self.stderr.write(self.style.ERROR(f"Cannot build instance-controller URL: {e}"))
            return

        # ── 4. Display instructions ──────────────────────────────────────
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Keycloak credentials are not configured."))
        self.stdout.write(f"Missing: {', '.join(missing)}")
        self.stdout.write("")
        self.stdout.write(f"Local callback server started on 127.0.0.1:{port}")
        self.stdout.write("Open this URL in your browser to complete setup:")
        self.stdout.write(f"  {url}")
        self.stdout.write(f"State token: {state}")
        self.stdout.write("Waiting for setup to complete (Ctrl+C to abort)...")
        self.stdout.write("")

        # ── 5. Auto-open browser if allowed ──────────────────────────────
        if not no_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass

        # ── 6. Block until credentials arrive or timeout ─────────────────
        ok = wait_for_keycloak_setup(
            state,
            timeout_seconds=timeout,
            poll_interval=poll_interval,
        )

        if ok:
            self.stdout.write(self.style.SUCCESS("✓ Keycloak credentials configured successfully."))
        else:
            self.stderr.write(
                self.style.ERROR("✗ Keycloak bootstrap aborted or timed out. Credentials not configured.")
            )
