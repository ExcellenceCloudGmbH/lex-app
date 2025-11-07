# core/management/commands/keycloak_backup.py
import json
import os
from datetime import datetime
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings



class Command(BaseCommand):
    help = "Backup and manage Keycloak authorization configurations"

    def add_arguments(self, parser):
        parser.add_argument(
            '--backup-dir',
            default='keycloak_backups',
            help='Directory to store backups (default: keycloak_backups)',
        )
        parser.add_argument(
            '--restore',
            help='Path to backup file to restore from',
        )
        parser.add_argument(
            '--list-backups',
            action='store_true',
            help='List available backup files',
        )

    def handle(self, *args, **options):
        from lex.api.views.authentication.KeycloakManager import KeycloakManager
        kc_manager = KeycloakManager()
        backup_dir = Path(options['backup_dir'])

        if options.get('list_backups'):
            self.list_backups(backup_dir)
            return

        if options.get('restore'):
            self.restore_config(kc_manager, Path(options['restore']))
            return

        # Default: create backup
        self.create_backup(kc_manager, backup_dir)

    def create_backup(self, kc_manager, backup_dir):
        """Create a timestamped backup of current Keycloak authorization settings"""
        backup_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = backup_dir / f"keycloak_authz_{timestamp}.json"

        self.stdout.write("Exporting current Keycloak authorization settings...")

        auth_config = kc_manager.export_authorization_settings()
        if not auth_config:
            self.stdout.write("Failed to export authorization settings.")
            return

        # Add metadata
        backup_data = {
            "metadata": {
                "timestamp": timestamp,
                "realm": getattr(settings, 'KEYCLOAK_REALM', 'unknown'),
                "client_uuid": getattr(settings, 'OIDC_RP_CLIENT_UUID', 'unknown'),
                "backup_version": "1.0"
            },
            "authorization_config": auth_config
        }

        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)

        self.stdout.write(f"✓ Backup created: {backup_file}")
        self.stdout.write(f"  - Resources: {len(auth_config.get('resources', []))}")
        self.stdout.write(f"  - Policies: {len(auth_config.get('policies', []))}")
        self.stdout.write(f"  - Permissions: {len(auth_config.get('permissions', []))}")

    def restore_config(self, kc_manager, backup_file):
        """Restore Keycloak authorization settings from a backup file"""
        if not backup_file.exists():
            self.stdout.write(f"Backup file not found: {backup_file}")
            return

        self.stdout.write(f"Restoring from backup: {backup_file}")

        try:
            with open(backup_file, 'r', encoding='utf-8') as f:
                backup_data = json.load(f)

            # Check backup format
            if 'authorization_config' in backup_data:
                auth_config = backup_data['authorization_config']
                metadata = backup_data.get('metadata', {})

                self.stdout.write(f"Backup timestamp: {metadata.get('timestamp', 'unknown')}")
                self.stdout.write(f"Backup realm: {metadata.get('realm', 'unknown')}")
            else:
                # Assume direct auth config format
                auth_config = backup_data

            # Import the configuration
            success = kc_manager.import_authorization_settings(auth_config)

            if success:
                self.stdout.write("✓ Authorization settings restored successfully")
                self.stdout.write(f"  - Resources: {len(auth_config.get('resources', []))}")
                self.stdout.write(f"  - Policies: {len(auth_config.get('policies', []))}")
                self.stdout.write(f"  - Permissions: {len(auth_config.get('permissions', []))}")
            else:
                self.stdout.write("✗ Failed to restore authorization settings")

        except Exception as e:
            self.stdout.write(f"Error restoring backup: {e}")

    def list_backups(self, backup_dir):
        """List available backup files"""
        if not backup_dir.exists():
            self.stdout.write("No backup directory found.")
            return

        backup_files = list(backup_dir.glob("keycloak_authz_*.json"))

        if not backup_files:
            self.stdout.write("No backup files found.")
            return

        self.stdout.write("Available backups:")
        for backup_file in sorted(backup_files, reverse=True):
            try:
                with open(backup_file, 'r') as f:
                    data = json.load(f)

                if 'metadata' in data:
                    timestamp = data['metadata'].get('timestamp', 'unknown')
                    realm = data['metadata'].get('realm', 'unknown')
                else:
                    timestamp = 'unknown'
                    realm = 'unknown'

                auth_config = data.get('authorization_config', data)
                resource_count = len(auth_config.get('resources', []))

                self.stdout.write(f"  {backup_file.name}")
                self.stdout.write(f"    Timestamp: {timestamp}")
                self.stdout.write(f"    Realm: {realm}")
                self.stdout.write(f"    Resources: {resource_count}")

            except Exception as e:
                self.stdout.write(f"  {backup_file.name} (error reading: {e})")