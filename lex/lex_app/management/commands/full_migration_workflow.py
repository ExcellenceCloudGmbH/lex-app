import os
import inspect
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from lex.full_migration_workflow import (
    WorkflowCommandError,
    WorkflowOptions,
    WorkflowUsageError,
    run_full_migration_workflow,
)


class Command(BaseCommand):
    help = (
        "Run the full migration workflow from the lex CLI with a stable interface, "
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
        parser.add_argument(
            "--database-deployment-target",
            type=str,
            default=None,
            help=(
                "Value for DATABASE_DEPLOYMENT_TARGET for workflow subprocesses "
                "(for example: GCP, K8S, DOCKER-COMPOSE, local, default)."
            ),
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
        project_root = (
            Path(options["project_root"]).expanduser().resolve()
            if options["project_root"]
            else self._infer_project_root()
        )
        db_name = options["db_name"] or self._infer_db_name()
        v1_source = (
            Path(options["v1_source"]).expanduser().resolve()
            if options["v1_source"]
            else None
        )

        workflow_kwargs = dict(
            v2_root=project_root,
            db_name=db_name,
            v1_source=v1_source,
            migration_timestamp=options["migration_timestamp"],
            chunk_size=options["chunk_size"],
            dry_run_backfill=options["dry_run_backfill"],
            enable_sanitization=options["enable_sanitization"],
            backfill_only=options["backfill_only"],
            pre_clean_jsons=options["pre_clean_jsons"],
            rollback_on_failure=options["rollback_on_failure"],
            rollback_only=options["rollback_only"],
            rollback_state_file=options["rollback_state_file"],
            skip_auditlog_backfill=options["skip_auditlog_backfill"],
        )
        target_arg = options["database_deployment_target"]
        workflow_fields = inspect.signature(WorkflowOptions).parameters
        supports_target = "database_deployment_target" in workflow_fields
        if supports_target:
            workflow_kwargs["database_deployment_target"] = target_arg
        elif target_arg:
            raise CommandError(
                "This runtime has mismatched lex modules: the command accepts "
                "--database-deployment-target, but WorkflowOptions does not. "
                "Deploy a single version containing both "
                "lex/lex_app/management/commands/full_migration_workflow.py and "
                "lex/full_migration_workflow.py."
            )

        workflow_options = WorkflowOptions(**workflow_kwargs)

        try:
            run_full_migration_workflow(workflow_options)
        except WorkflowCommandError as exc:
            raise CommandError(
                f"full_migration_workflow failed with exit code {exc.exit_code}"
            ) from exc
        except WorkflowUsageError as exc:
            raise CommandError(str(exc)) from exc
