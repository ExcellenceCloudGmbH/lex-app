"""Cluster 1q: repository migration files are release-ready.

Intent: ``lex Init`` should apply existing migrations, not generate new framework
migration files on customer machines. A release that ships with pending model
changes causes downstream duplicate migrations and upgrade failures.
Cluster 1q — scenarios 1.147–1.147. Type: U.
Covers: lex/lex_app/migrations, lex/authentication/migrations,
lex/audit_logging/migrations, lex/legacy_data/migrations.
Run: python -m lex pytest lex/test_project/tests/init/test_1q_migration_files_complete.py -v
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import TestCase

import pytest

pytestmark = pytest.mark.init


class TestCluster01q_MigrationFilesComplete(TestCase):
    """Cluster 1q: guard that framework migrations are fully committed."""

    def test_1_147_framework_apps_have_no_pending_makemigrations(self) -> None:
        """
        Scenario 1.147: Framework release ships with complete migration files.

        Given: The framework source tree in this repository
        When: We run ``lex makemigrations --check --dry-run`` for framework apps
        Then: Django reports no pending migration generation work
        """
        repo_root = Path(__file__).resolve().parents[4]
        env = os.environ.copy()
        env.setdefault("PROJECT_ROOT", str(repo_root))

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "lex",
                "makemigrations",
                "lex_app",
                "authentication",
                "audit_logging",
                "legacy_data",
                "--check",
                "--dry-run",
                "--verbosity",
                "1",
            ],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            0,
            "Framework release must not ship with pending migration generation. "
            "If this fails, commit the missing migration files before releasing.\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
        )
        self.assertIn(
            "No changes detected",
            result.stdout,
            "`makemigrations --check --dry-run` must confirm no model drift for framework apps.",
        )
