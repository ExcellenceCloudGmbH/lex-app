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
from unittest import mock

from lex.tools.setup_with_ai import SetupWithAIError, run_ai_update_bootstrap


class _RecordingRunner:
    """Stands in for ``subprocess.run`` and records every command issued.

    ``returncode`` applies to the migration handoff only. The two probes -- the
    installed version and whether the package answers to ``python -m`` -- always
    succeed here, so a test about the handoff's exit code is not quietly
    answering a question about the fallback instead.
    """

    def __init__(self, returncode: int = 0):
        self.commands: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, command, **kwargs):
        command = [str(part) for part in command]
        self.commands.append(command)
        joined = " ".join(command)
        if "importlib.metadata" in joined:
            return subprocess.CompletedProcess(command, 0, stdout="1.1.0")
        if "getattr(m, 'main'" in joined:
            return subprocess.CompletedProcess(command, 0)
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

        install = next(c for c in runner.commands if "install" in c)
        handoff = next(
            c for c in runner.commands if "-m" in c and "lex_mcp.ai_update" in c
        )
        self.assertIn("pip", install)
        self.assertIn("--upgrade", install)
        self.assertIn("lex-mcp-local", install)
        self.assertLess(
            runner.commands.index(install),
            runner.commands.index(handoff),
            runner.commands,
        )

    def test_the_handoff_is_a_separate_interpreter_run(self):
        """An in-process call would import the ladder the upgrade just replaced."""
        runner = _RecordingRunner()

        run_ai_update_bootstrap(self.project_root, env=self.env, runner=runner)

        handoff = next(
            c for c in runner.commands if "-m" in c and "lex_mcp.ai_update" in c
        )
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


class LegacyPackageCompatibilityTests(unittest.TestCase):
    """The upgrade a customer actually performs: new lex-app, old package.

    ``python -m lex_mcp.ai_update`` only does anything on a release that has a
    ``main``. Every release before the handoff existed has neither that nor a
    ``__main__`` guard, so the same command imports the module, runs nothing,
    ignores ``--project-root`` and **exits 0**. Handing off unconditionally
    therefore reports a successful update that never happened -- and the
    customer has no way to tell, because the exit code and the wording are the
    same as a real one.

    That window is not hypothetical. It is open from the moment lex-app is
    installed until the moment the new lex-mcp-local is fetched, and it stays
    open indefinitely if the access key has expired: pip exits 0 when it cannot
    enumerate the index, reporting "Requirement already satisfied".
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmp.name)
        (self.project_root / ".env").write_text(
            "REMOTE_MCP_API_KEY=test-key\n", encoding="utf-8"
        )
        self.env = {"VIRTUAL_ENV": ""}

    def tearDown(self):
        self._tmp.cleanup()

    def _runner(self, *, has_main: bool):
        """Stand in for pip and for the probe, without touching the network."""
        calls: list[list[str]] = []

        def run(command, **kwargs):
            command = [str(part) for part in command]
            calls.append(command)
            joined = " ".join(command)
            if "importlib.metadata" in joined:
                return subprocess.CompletedProcess(command, 0, stdout="1.1.0")
            if "getattr(m, 'main'" in joined:
                return subprocess.CompletedProcess(command, 0 if has_main else 3)
            return subprocess.CompletedProcess(command, 0)

        return run, calls

    def test_a_package_with_the_handoff_is_re_entered(self):
        runner, calls = self._runner(has_main=True)

        run_ai_update_bootstrap(self.project_root, env=self.env, runner=runner)

        self.assertTrue(
            any("lex_mcp.ai_update" in c and "-m" in c for c in calls),
            calls,
        )

    def test_a_package_without_it_is_never_handed_off_to(self):
        """The whole failure: `-m` against an older release is a silent no-op."""
        runner, calls = self._runner(has_main=False)
        applied = []

        with mock.patch(
            "lex.tools.setup_with_ai._apply_ai_update_in_process",
            side_effect=lambda root, reporter: applied.append(root) or 0,
        ):
            run_ai_update_bootstrap(self.project_root, env=self.env, runner=runner)

        self.assertEqual(applied, [self.project_root])
        self.assertFalse(
            any("-m" in c and "lex_mcp.ai_update" in c for c in calls),
            "handed off to a package that would silently do nothing",
        )

    def test_the_probe_runs_in_a_fresh_interpreter_after_pip(self):
        """This process cannot see what pip just installed -- pip records an
        install as a .pth file and those are only read at interpreter startup.
        Probing in-process would answer for the version that was already
        loaded."""
        runner, calls = self._runner(has_main=True)

        run_ai_update_bootstrap(self.project_root, env=self.env, runner=runner)

        probe_index = next(
            i for i, c in enumerate(calls) if "getattr(m, 'main'" in " ".join(c)
        )
        install_index = next(i for i, c in enumerate(calls) if "install" in c)
        self.assertLess(install_index, probe_index, "probed before pip ran")

    def test_falling_back_says_so(self):
        """pip exits 0 on an index it cannot read, so a failed upgrade and a
        successful one look identical unless the fallback announces itself."""
        runner, _ = self._runner(has_main=False)
        said: list[str] = []

        with mock.patch(
            "lex.tools.setup_with_ai._apply_ai_update_in_process", return_value=0
        ):
            run_ai_update_bootstrap(
                self.project_root, env=self.env, runner=runner, reporter=said.append
            )

        self.assertTrue(
            any("predates this lex-app" in line for line in said), said
        )

    def test_an_unusable_probe_falls_back_rather_than_guessing(self):
        """If the interpreter cannot be run at all, assuming the newer layout
        would hand off into the silent no-op."""

        def broken(command, **kwargs):
            joined = " ".join(str(p) for p in command)
            if "getattr(m, 'main'" in joined:
                raise OSError("cannot execute")
            return subprocess.CompletedProcess(command, 0, stdout="1.1.0")

        with mock.patch(
            "lex.tools.setup_with_ai._apply_ai_update_in_process", return_value=0
        ) as fallback:
            run_ai_update_bootstrap(
                self.project_root, env=self.env, runner=broken
            )

        fallback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
