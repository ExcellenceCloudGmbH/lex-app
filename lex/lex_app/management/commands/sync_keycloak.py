"""
Sync Keycloak authorization resources & permissions with Django model changes.

Usage:
    python manage.py sync_keycloak
    python manage.py sync_keycloak --input /tmp/changes.json
    python manage.py sync_keycloak --dry-run
    python manage.py sync_keycloak --ensure-default-authz

Reads the JSON file produced by ``detect_model_changes`` and applies all
adds / deletes / renames / missing-model additions to Keycloak using the
``KeycloakSyncManager``.
"""
import json
import logging
import os
from pathlib import Path

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

DEFAULT_INPUT = Path(os.getcwd()) / ".model_changes.json"


class Command(BaseCommand):
    help = (
        "Sync Keycloak authorization resources and permissions with Django models. "
        "Reads the JSON output of detect_model_changes and applies changes to Keycloak."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            type=str,
            default=str(DEFAULT_INPUT),
            help=f"Path to the model-changes JSON file (default: {DEFAULT_INPUT}).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Show what would be changed without making actual changes.",
        )
        parser.add_argument(
            "--preserve-renamed-permissions",
            action="store_true",
            default=True,
            help="Preserve permissions when renaming models (default: True).",
        )
        parser.add_argument(
            "--ensure-default-authz",
            action="store_true",
            default=False,
            help=(
                "Ensure a default resource, regex default policy and "
                "resource-based default permission exist."
            ),
        )

    # ── Main ──────────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        input_path = Path(options["input"])
        dry_run = options["dry_run"]
        preserve_permissions = options["preserve_renamed_permissions"]
        ensure_default_authz = options["ensure_default_authz"]

        # ── 1. Read model-changes JSON ───────────────────────────────────
        if not input_path.exists():
            self.stderr.write(
                self.style.ERROR(
                    f"Input file not found: {input_path}\n"
                    "Run 'detect_model_changes' first to generate it."
                )
            )
            return

        try:
            data = json.loads(input_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Failed to parse {input_path}: {e}"))
            return

        adds = [tuple(a) for a in data.get("adds", [])]
        deletes = [tuple(d) for d in data.get("deletes", [])]
        renames = [tuple(r) for r in data.get("renames", [])]
        missing_models = [tuple(m) for m in data.get("missing_models", [])]

        # ── 2. Display summary ───────────────────────────────────────────
        self.stdout.write("=" * 80)
        self.stdout.write("Keycloak Authorization Sync")
        self.stdout.write("=" * 80)

        if adds:
            for app, name in adds:
                self.stdout.write(f"  ADD    {app}.{name}")
        if deletes:
            for app, name in deletes:
                self.stdout.write(f"  DELETE {app}.{name}")
        if renames:
            for app, old, new in renames:
                self.stdout.write(f"  RENAME {app}.{old} -> {new}")
        if missing_models:
            self.stdout.write(f"\n  {len(missing_models)} missing model(s) to add:")
            for app, name in missing_models:
                self.stdout.write(f"    MISSING: {app}.{name}")

        if not (adds or deletes or renames or missing_models):
            self.stdout.write("Nothing to sync — no changes detected.")
            return

        # ── 3. Handle dry-run ────────────────────────────────────────────
        if dry_run:
            self.stdout.write(self.style.WARNING("\nDry run mode — no changes will be made."))
            return

        # ── 4. Initialize KeycloakSyncManager ────────────────────────────
        from lex_app.management.commands.Init import KeycloakSyncManager

        try:
            sync_manager = KeycloakSyncManager()
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Failed to initialize Keycloak manager: {e}"))
            return

        # ── 5. Merge missing_models into the adds list ───────────────────
        # The detect step already excluded rename-targets from missing_models,
        # so we can safely merge them with migration-level adds.
        all_adds = list(set(missing_models) | set(adds))

        # ── 6. Execute sync ──────────────────────────────────────────────
        self.stdout.write("\nSyncing changes to Keycloak...")

        success = sync_manager.process_model_changes(
            adds=all_adds,
            deletes=deletes,
            renames=renames,
            preserve_permissions=preserve_permissions,
            ensure_default_authz=ensure_default_authz,
        )

        if success:
            self.stdout.write(self.style.SUCCESS("✓ All model changes successfully synced to Keycloak!"))
        else:
            self.stderr.write(self.style.ERROR("✗ Some operations failed. Check logs for details."))
