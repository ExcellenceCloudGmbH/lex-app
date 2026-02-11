"""
Execute Django migrations with optional makemigrations step.

Usage:
    python manage.py lex_migrate                  # makemigrations + migrate + createcachetable
    python manage.py lex_migrate --no-makemigrations  # only migrate + createcachetable

This is a thin wrapper around Django's built-in ``makemigrations`` and
``migrate`` commands, adding ``createcachetable`` at the end.
"""
import logging
import traceback

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Run Django migrations. By default, runs makemigrations first; "
        "use --no-makemigrations to skip generating new migration files."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-makemigrations",
            action="store_true",
            default=False,
            help="Skip makemigrations; only apply existing migration files.",
        )
        parser.add_argument(
            "--migration-verbosity",
            type=int,
            default=1,
            help="Verbosity level passed to makemigrations / migrate (default: 1).",
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _check_unapplied_migrations() -> bool:
        """Return True if there are unapplied migration files."""
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        return len(plan) > 0

    def _execute_migrations(self, verbosity: int = 1, create_new: bool = True) -> bool:
        """
        Run the full migration workflow:
          1. (optionally) makemigrations
          2. check for unapplied migrations
          3. migrate
          4. createcachetable
        """
        try:
            # Step 1 – generate new migration files
            if create_new:
                self.stdout.write("Creating new migrations for model changes...")
                call_command(
                    "makemigrations",
                    verbosity=verbosity,
                    interactive=False,
                    stdout=self.stdout,
                    stderr=self.stderr,
                    no_input=True,
                )
                self.stdout.write("✓ New migrations created successfully")

            # Step 2 – check for pending migrations
            if not self._check_unapplied_migrations():
                self.stdout.write("No unapplied migrations found.")
                return True

            # Step 3 – apply migrations
            self.stdout.write("Applying unapplied migrations...")
            call_command(
                "migrate",
                verbosity=verbosity,
                interactive=False,
                stdout=self.stdout,
                stderr=self.stderr,
            )

            # Step 4 – ensure the cache table exists
            call_command(
                "createcachetable",
                verbosity=verbosity,
            )

            self.stdout.write("✓ Django migrations completed successfully")
            return True

        except Exception as e:
            self.stderr.write(f"✗ Migration failed: {e}")
            logger.error(f"Migration execution failed: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False

    # ── Main ──────────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        no_makemigrations = options["no_makemigrations"]
        verbosity = options["migration_verbosity"]

        self.stdout.write("-" * 80)
        self.stdout.write("Executing Django Migrations")
        self.stdout.write("-" * 80)

        if no_makemigrations:
            self.stdout.write("Skipping makemigrations (--no-makemigrations).")

        success = self._execute_migrations(
            verbosity=verbosity,
            create_new=not no_makemigrations,
        )

        if success:
            self.stdout.write(self.style.SUCCESS("✓ Migration step completed successfully."))
        else:
            self.stderr.write(self.style.ERROR("✗ Migration step failed. See logs for details."))
