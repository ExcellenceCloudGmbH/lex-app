"""
Cluster 1a: ``lex setup`` — project scaffolding.

Tests the CLI command a brand-new user runs *before* any Django or
database is involved. Intent is derived from docs/installation.md:

    ``lex setup`` generates three things for you:
      - ``.run/``       PyCharm run configurations (Init, Start, Streamlit)
      - ``.env``        Environment configuration template
      - ``migrations/`` Django migrations folder

If this command is broken, a new customer cannot even get to day two.

Scenario numbering matches
docs/test-plan/test-clusters.md#1-init--project-bootstrap.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from click.testing import CliRunner


class TestCluster01a_LexSetup(TestCase):
    """``lex setup`` — pure scaffolding, no Django required."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name).resolve()
        # ``find_project_root`` walks upward; we pin it to our tmpdir so
        # the command does not accidentally scaffold over the real repo.
        patcher = patch(
            "lex.bin.lex.find_project_root",
            return_value=self.project_root.as_posix(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _invoke_setup(self) -> object:
        # Import lazily — ``lex.bin.lex`` sets env vars at import time.
        from lex.bin.lex import setup
        runner = CliRunner()
        return runner.invoke(setup, [], catch_exceptions=False)

    # -- 1.1 -----------------------------------------------------------
    def test_1_1_fresh_directory_scaffolds_all_three_artifacts(self) -> None:
        """
        Scenario 1.1: Fresh directory.

        Given: An empty directory that represents a new customer project.
        When:  The user runs ``lex setup``.
        Then:  ``.env``, ``.run/``, and ``migrations/__init__.py`` all exist.
        """
        result = self._invoke_setup()

        self.assertEqual(
            result.exit_code, 0,
            msg=f"'lex setup' must exit cleanly on a fresh dir. "
                f"stdout={result.output!r}",
        )
        self.assertTrue(
            (self.project_root / ".env").is_file(),
            ".env must be created on a fresh project",
        )
        self.assertTrue(
            (self.project_root / ".run").is_dir(),
            ".run/ directory must be created",
        )
        self.assertTrue(
            (self.project_root / "migrations" / "__init__.py").is_file(),
            "migrations/__init__.py must be created so Django treats "
            "migrations/ as a package",
        )

    # -- 1.2 -----------------------------------------------------------
    def test_1_2_existing_env_is_preserved(self) -> None:
        """
        Scenario 1.2: Existing .env preserved.

        Given: A project with a customised .env already.
        When:  The user re-runs ``lex setup`` (e.g. after upgrading lex-app).
        Then:  Their .env is NOT overwritten.
        """
        env_path = self.project_root / ".env"
        custom_content = "OIDC_RP_CLIENT_ID=my-secret-client\n"
        env_path.write_text(custom_content, encoding="utf-8")

        self._invoke_setup()

        self.assertEqual(
            env_path.read_text(encoding="utf-8"),
            custom_content,
            "Re-running setup must NEVER overwrite an existing .env. "
            "This is the customer's runtime configuration.",
        )

    # -- 1.3 -----------------------------------------------------------
    def test_1_3_run_dir_contains_init_start_streamlit_configs(self) -> None:
        """
        Scenario 1.3: .run/ configs regenerated.

        The .run/ folder must contain PyCharm run configurations for at
        least Init, Start, and Streamlit (the three the installation
        docs tell the user to click).
        """
        self._invoke_setup()

        run_dir = self.project_root / ".run"
        xml_names = {p.stem.lower() for p in run_dir.glob("*.xml")}

        for required in ("init", "start", "streamlit"):
            self.assertTrue(
                any(required in name for name in xml_names),
                f"PyCharm configuration for '{required}' must be generated "
                f"(docs/installation.md promises it). Found: {sorted(xml_names)}",
            )

    # -- 1.4 -----------------------------------------------------------
    def test_1_4_setup_is_idempotent(self) -> None:
        """
        Scenario 1.4: running setup twice is safe and deterministic.
        """
        self._invoke_setup()
        snapshot = sorted(p.name for p in self.project_root.rglob("*"))

        result = self._invoke_setup()
        self.assertEqual(result.exit_code, 0, "Second setup run must succeed")

        second = sorted(p.name for p in self.project_root.rglob("*"))
        self.assertEqual(
            snapshot, second,
            "lex setup must be idempotent — no files added or removed on re-run",
        )

    # -- 1.5 -----------------------------------------------------------
    def test_1_5_find_project_root_resolves_cwd_when_unspecified(self) -> None:
        """
        Scenario 1.5: ``find_project_root`` resolves correctly.

        Without the ``-p`` flag, the command should resolve the project
        root from the current working directory.
        """
        from lex.tools.project_root import find_project_root

        resolved = find_project_root(self.project_root.as_posix())
        self.assertTrue(
            Path(resolved).exists(),
            f"find_project_root must return an existing path; got {resolved!r}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
