"""Guards the one thing `lex ai-update` still does inside lex-app.

The risk model: ``lex_mcp.ai_update`` binds its migration ladder at import time.
A process that starts on lex-mcp-local version N therefore iterates version N's
steps no matter what pip installs underneath it -- so a run that upgraded the
package in-process and then kept going would apply the *old* ladder and exit 0,
leaving the customer a release behind with nothing to show for it. That is why
the upgrade and the migrations are two processes, and it is the only reason the
bootstrap exists.

The failure is invisible from outside: same command, same exit code, same
"update complete". These tests pin the ordering and the handoff, because nothing
downstream would notice if either quietly went away.

No network and no pip: the runner is a stub, so the assertions are about the
commands lex-app decides to issue.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from lex.tools.setup_with_ai import SetupWithAIError, run_ai_update_bootstrap


class _RecordingRunner:
    """Stands in for ``subprocess.run`` and records every command issued."""

    def __init__(self, returncode: int = 0):
        self.commands: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, command, **kwargs):
        self.commands.append([str(part) for part in command])
        return subprocess.CompletedProcess(command, self.returncode)


class AiUpdateBootstrapTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmp.name)
        (self.project_root / ".env").write_text(
            "REMOTE_MCP_API_KEY=test-key\n", encoding="utf-8"
        )
        self.env = {"VIRTUAL_ENV": ""}

    def tearDown(self):
        self._tmp.cleanup()

    def test_upgrade_runs_before_the_migration_handoff(self):
        """Reverse these and the ladder that executes is the one being replaced."""
        runner = _RecordingRunner()

        run_ai_update_bootstrap(self.project_root, env=self.env, runner=runner)

        self.assertEqual(len(runner.commands), 2, runner.commands)
        install, handoff = runner.commands
        self.assertIn("pip", install)
        self.assertIn("--upgrade", install)
        self.assertIn("lex-mcp-local", install)
        self.assertIn("lex_mcp.ai_update", handoff)

    def test_the_handoff_is_a_separate_interpreter_run(self):
        """An in-process call would import the ladder the upgrade just replaced."""
        runner = _RecordingRunner()

        run_ai_update_bootstrap(self.project_root, env=self.env, runner=runner)

        handoff = runner.commands[1]
        self.assertEqual(handoff[1:3], ["-m", "lex_mcp.ai_update"])
        self.assertEqual(
            handoff[handoff.index("--project-root") + 1],
            str(self.project_root.resolve()),
        )

    def test_a_failing_migration_run_surfaces_as_a_nonzero_return(self):
        """The CLI turns this into a ClickException; swallowing it would report
        success over a project that was left half-migrated."""
        runner = _RecordingRunner(returncode=3)

        exit_code = run_ai_update_bootstrap(
            self.project_root, env=self.env, runner=runner
        )

        self.assertEqual(exit_code, 3)

    def test_a_missing_access_key_stops_before_pip_is_reached(self):
        """pip would fail anyway, but on an index URL built from an empty token --
        a Cloudsmith 401 rather than the one sentence that says what to run."""
        (self.project_root / ".env").write_text("LEX_MCP_MODE=forward\n", encoding="utf-8")
        runner = _RecordingRunner()

        with self.assertRaises(SetupWithAIError):
            run_ai_update_bootstrap(self.project_root, env=self.env, runner=runner)

        self.assertEqual(runner.commands, [])

    def test_the_bootstrap_holds_no_opinion_about_what_an_update_does(self):
        """The reason this stayed in lex-app at all is that it must run before the
        package it upgrades is importable. Anything beyond that belongs on the
        far side of the handoff, where it ships without a lex-app release."""
        source = Path(run_ai_update_bootstrap.__code__.co_filename).read_text(
            encoding="utf-8"
        )
        body_start = source.index("def run_ai_update_bootstrap(")
        body = source[body_start : source.index("\ndef ", body_start + 1)]

        for migration_concept in ("mcp.json", "payload", ".github", "environments"):
            self.assertNotIn(migration_concept, body, migration_concept)


if __name__ == "__main__":
    unittest.main()
