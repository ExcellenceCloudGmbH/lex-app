import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Run lex/full_migration_workflow.sh from the lex CLI with a stable interface, "
        "usable from any working directory."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--db-name",
            required=False,
            default=None,
            type=str,
            help="Target database name. If omitted, inferred from settings.DATABASES['default']['NAME'].",
        )
        parser.add_argument(
            "--project-root",
            type=str,
            default=None,
            help=(
                "Path to V2 project root. If omitted, inferred from PROJECT_ROOT or "
                "settings (NEW_BASE_DIR + repo_name), then falls back to cwd."
            ),
        )
        parser.add_argument(
            "--v1-source",
            type=str,
            default=None,
            help="Optional external V1 migration source path.",
        )
        parser.add_argument("--migration-timestamp", type=str, default=None)
        parser.add_argument("--chunk-size", type=int, default=500)
        parser.add_argument("--dry-run-backfill", action="store_true")
        parser.add_argument("--enable-sanitization", action="store_true")
        parser.add_argument("--backfill-only", action="store_true")
        parser.add_argument("--pre-clean-jsons", action="store_true")
        parser.add_argument("--rollback-on-failure", action="store_true")
        parser.add_argument("--rollback-only", action="store_true")
        parser.add_argument(
            "--rollback-state-file",
            type=str,
            default=".lex_migration_state_before.json",
        )
        parser.add_argument("--skip-auditlog-backfill", action="store_true")

    def _infer_project_root(self) -> Path:
        env_project_root = os.getenv("PROJECT_ROOT")
        if env_project_root:
            return Path(env_project_root).expanduser().resolve()

        settings_project_root = getattr(settings, "PROJECT_ROOT", None)
        if settings_project_root:
            return Path(str(settings_project_root)).expanduser().resolve()

        new_base_dir = getattr(settings, "NEW_BASE_DIR", None)
        repo_name = getattr(settings, "repo_name", None)
        if new_base_dir and repo_name:
            return (Path(str(new_base_dir)).expanduser() / str(repo_name)).resolve()

        return Path(os.getcwd()).resolve()

    def _infer_db_name(self) -> str:
        databases = getattr(settings, "DATABASES", None)
        if isinstance(databases, dict):
            default_db = databases.get("default") or {}
            db_name = default_db.get("NAME")
            if db_name:
                return str(db_name)
        raise CommandError(
            "Could not infer database name from settings.DATABASES['default']['NAME']. "
            "Pass --db-name explicitly."
        )

    def handle(self, *args, **options):
        script_path = (
            Path(__file__).resolve().parents[3] / "full_migration_workflow.sh"
        ).resolve()
        if not script_path.exists():
            raise CommandError(f"Workflow script not found: {script_path}")

        project_root = (
            Path(options["project_root"]).expanduser().resolve()
            if options["project_root"]
            else self._infer_project_root()
        )
        db_name = options["db_name"] or self._infer_db_name()
        v1_source = options["v1_source"]

        cmd = [str(script_path)]
        if v1_source:
            cmd.extend([str(Path(v1_source).expanduser().resolve()), str(project_root), db_name])
        else:
            cmd.extend([str(project_root), db_name])

        if options["migration_timestamp"]:
            cmd.extend(["--migration-timestamp", options["migration_timestamp"]])
        if options["chunk_size"] is not None:
            cmd.extend(["--chunk-size", str(options["chunk_size"])])
        if options["dry_run_backfill"]:
            cmd.append("--dry-run-backfill")
        if options["enable_sanitization"]:
            cmd.append("--enable-sanitization")
        if options["backfill_only"]:
            cmd.append("--backfill-only")
        if options["pre_clean_jsons"]:
            cmd.append("--pre-clean-jsons")
        if options["rollback_on_failure"]:
            cmd.append("--rollback-on-failure")
        if options["rollback_only"]:
            cmd.append("--rollback-only")
        if options["rollback_state_file"]:
            cmd.extend(["--rollback-state-file", options["rollback_state_file"]])
        if options["skip_auditlog_backfill"]:
            cmd.append("--skip-auditlog-backfill")

        result = subprocess.run(cmd, cwd=str(project_root), check=False)
        if result.returncode != 0:
            raise CommandError(
                f"full_migration_workflow failed with exit code {result.returncode}"
            )
