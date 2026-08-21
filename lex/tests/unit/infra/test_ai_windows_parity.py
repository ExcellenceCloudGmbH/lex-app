"""Windows-only failure modes in the ``lex ai-*`` commands.

Every case here is a defect that reproduced on a standardized Windows client
and could not reproduce on the Linux machine the commands were developed on.
The assertions are written to hold on both platforms: where the behaviour is
inherently per-platform the test says which, rather than being skipped, so a
regression on Linux is caught too.
"""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from lex.tools import setup_with_ai


class ConsoleEncodingTests(unittest.TestCase):
    """``lex`` must be able to print a path it just wrote.

    A redirected stdout on Windows is opened with the locale code page, and
    cp1252 cannot encode Turkish ``ı ğ ş``, Cyrillic, or CJK. Printing the
    restored-file list under such a path raised UnicodeEncodeError and exited
    1 -- on the very first run of ``setup-with-ai`` in a fresh project.
    """

    def test_the_cli_forces_utf8_on_its_own_streams(self) -> None:
        from lex.bin import lex as cli

        self.assertTrue(hasattr(cli, "_force_utf8_console"))

    def test_a_non_cp1252_path_survives_a_redirected_stdout(self) -> None:
        script = (
            "import sys;"
            "sys.path.insert(0, r'%s');"
            "from lex.bin.lex import _force_utf8_console;"
            "_force_utf8_console();"
            "print('Kağıtçılık 项目 Проект')"
        ) % str(Path(setup_with_ai.__file__).parents[2])

        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            env={**os.environ, "PYTHONIOENCODING": "", "PYTHONUTF8": "0"},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
        self.assertNotIn(b"UnicodeEncodeError", completed.stderr)
        self.assertIn("Kağıtçılık", completed.stdout.decode("utf-8", "replace"))


class ProcessLivenessTests(unittest.TestCase):
    """``os.kill(pid, 0)`` is not a liveness probe on Windows.

    It answers "running" for a process that has already exited, for as long as
    any handle to it stays open -- and the IDE that spawned our server holds
    one. Every wait-for-exit loop therefore ran its full timeout and then took
    its give-up branch, which reached ``signal.SIGKILL``: a name that does not
    exist on Windows, guarded only by ``except OSError``.
    """

    def test_a_live_process_reads_as_running(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            time.sleep(1.0)
            self.assertTrue(setup_with_ai._is_process_running(child.pid))
        finally:
            child.kill()
            child.wait(timeout=10)

    def test_an_exited_process_does_not_read_as_running(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "raise SystemExit(0)"])
        child.wait(timeout=15)
        # The handle is deliberately still held by this process, which is the
        # condition that made the old probe answer True indefinitely.
        time.sleep(0.5)
        self.assertFalse(setup_with_ai._is_process_running(child.pid))

    def test_an_absurd_pid_does_not_read_as_running(self) -> None:
        self.assertFalse(setup_with_ai._is_process_running(999_999))
        self.assertFalse(setup_with_ai._is_process_running(0))
        self.assertFalse(setup_with_ai._is_process_running(-1))

    def test_sigkill_is_never_named_unguarded(self) -> None:
        """The crash was an AttributeError, which ``except OSError`` misses."""
        self.assertEqual(hasattr(signal, "SIGKILL"), os.name != "nt")

        from lex_mcp.ai_setup import force_kill_process

        victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            time.sleep(1.0)
            force_kill_process(victim.pid)  # must not raise on any platform
            self.assertIsNotNone(victim.wait(timeout=10))
        finally:
            if victim.poll() is None:
                victim.kill()
        # And a pid that is gone is reported, not raised.
        self.assertFalse(force_kill_process(999_999))


class LongPathTests(unittest.TestCase):
    """MAX_PATH. ``LongPathsEnabled`` is off on the standardized image, and an
    installer cannot turn it on, so the code has to use extended-length paths.
    The failure was ``[WinError 3] The system cannot find the path specified``,
    which reads as a missing file rather than a path 20 characters too long.
    """

    def test_a_path_past_max_path_round_trips(self) -> None:
        from lex_mcp import fsutil

        tmp = Path(tempfile.mkdtemp())
        deep_root = tmp / "a-long-directory-name"
        try:
            source = tmp / "source.md"
            source.write_text("CONTENT\n", encoding="utf-8")
            deep = tmp.joinpath(*(["a-long-directory-name"] * 14)) / "target.md"
            self.assertGreater(len(str(deep)), 260, "fixture is not actually long")

            fsutil.copy_file(source, deep)
            self.assertTrue(fsutil.exists(deep))
            self.assertTrue(fsutil.files_match(source, deep))
            # Idempotence matters as much as the write: a comparison that could
            # not see the file would re-restore it on every single run.
            fsutil.copy_file(source, deep)
            self.assertTrue(fsutil.files_match(source, deep))
            fsutil.remove_file(deep)
            self.assertFalse(fsutil.exists(deep))
        finally:
            # Torn down by hand: shutil.rmtree (and so TemporaryDirectory) walks
            # these directories with plain paths and fails with "the directory is
            # not empty" once they pass MAX_PATH.
            self._remove_deep_tree(deep_root)
            source.unlink(missing_ok=True)
            tmp.rmdir()

    @staticmethod
    def _remove_deep_tree(root: Path) -> None:
        from lex_mcp import fsutil

        if not fsutil.exists(root):
            return
        stack = [root]
        while stack:
            current = stack[-1]
            children = []
            try:
                children = list(os.scandir(fsutil.fs_path(current)))
            except OSError:
                pass
            pending = []
            for entry in children:
                child = current / entry.name
                if entry.is_dir():
                    pending.append(child)
                else:
                    fsutil.remove_file(child)
            if pending:
                stack.extend(pending)
                continue
            stack.pop()
            try:
                os.rmdir(fsutil.fs_path(current))
            except OSError:
                pass

    def test_the_error_names_the_length_rather_than_blaming_the_file(self) -> None:
        from lex_mcp import fsutil

        if os.name != "nt":
            self.skipTest("the misleading message is a Windows-only error code")
        with tempfile.TemporaryDirectory() as tmp:
            absurd = Path(tmp) / ("x" * 200) / ("y" * 200) / "z.md"
            try:
                open(str(absurd), "w").close()
            except OSError as exc:
                message = fsutil.describe_os_error(exc, absurd)
                self.assertIn("260-character limit", message)
            else:  # pragma: no cover - would mean the limit is not in force
                self.fail("expected the path length to be refused")


class ReadOnlyAndLockedAssetTests(unittest.TestCase):
    """A read-only or momentarily locked asset must not be fatal.

    On POSIX, replacing a file needs write permission on the *directory*, so a
    read-only asset is rewritten silently. On Windows the attribute on the file
    denies it, and ``ai-verify --silent`` is the MCP pre-flight on every tool
    call -- so one such file failed every tool call in the project.
    """

    def test_a_read_only_destination_is_rewritten(self) -> None:
        from lex_mcp import fsutil

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.md"
            source.write_text("NEW\n", encoding="utf-8")
            target = Path(tmp) / "target.md"
            target.write_text("OLD\n", encoding="utf-8")
            os.chmod(target, stat.S_IREAD)
            try:
                fsutil.copy_file(source, target)
                self.assertEqual(target.read_text(encoding="utf-8"), "NEW\n")
            finally:
                os.chmod(target, stat.S_IWRITE | stat.S_IREAD)

    def test_an_unwritable_asset_is_collected_not_raised(self) -> None:
        """Reported per file so the rest of the tree still gets restored."""
        from lex_mcp.ai_assets import DirectoryVerificationResult

        result = DirectoryVerificationResult(
            directory_name=".github",
            source_directory=None,
            destination_directory=Path("."),
            failed_files=("could not write x.md",),
        )
        # Surfaced to --strict, which is what CI reads.
        self.assertFalse(result.ok)


class ShortPathTests(unittest.TestCase):
    """8.3 short names. ``%TEMP%`` is ``C:\\Users\\ATAKAN~1\\...`` for any
    username over eight characters, and ``resolve()`` expands it to the long
    form -- so a resolved path and an unresolved one compare unequal for the
    same directory.
    """

    def test_the_project_resolver_is_stable_under_short_names(self) -> None:
        from lex.tools.project_root import resolve_llm_working_directory

        with tempfile.TemporaryDirectory() as tmp:
            once = resolve_llm_working_directory(tmp)
            twice = resolve_llm_working_directory(str(once))
            self.assertEqual(once, twice, "resolution must be a fixed point")


class CommandSurfaceTests(unittest.TestCase):
    """Both spellings of ai-issue-report reach the same implementation."""

    def test_hyphenated_and_underscored_names_are_both_registered(self) -> None:
        from lex.bin import lex as cli

        self.assertIn("ai-issue-report", cli.lex.commands)
        self.assertIn("ai_issue_report", cli.lex.commands)

    def test_only_the_canonical_spelling_is_listed(self) -> None:
        from lex.bin import lex as cli

        self.assertTrue(cli.lex.commands["ai_issue_report"].hidden)
        self.assertFalse(cli.lex.commands["ai-issue-report"].hidden)

    def test_ai_worktree_can_actually_be_invoked(self) -> None:
        """--help is not proof that a command runs.

        `--mode` was given `default=""` so it could mean "undecided", but
        click validates a default against its Choice and the empty string is not
        a member -- so every invocation died with "'' is not one of ...". The
        help text rendered perfectly, which is exactly why checking the help and
        the registration caught nothing. This drives the command far enough to
        reach its own argument handling.
        """
        from click.testing import CliRunner

        from lex.bin import lex as cli

        result = CliRunner().invoke(cli.lex, ["ai-worktree", "-p", "/nonexistent-xyz"])
        self.assertNotIn("is not one of", result.output)
        # Reaching the implementation and being told there is no repository there
        # is the success condition; the option parsing is what is under test.
        self.assertIn("No worktree was prepared", result.output)

    def test_every_ai_command_skips_the_django_bootstrap(self) -> None:
        """Derived from the registered commands, not a list kept by hand.

        An AI command runs before there is a project to bootstrap -- several of
        them exist to create one -- so one left out of _SKIP_BOOTSTRAP_COMMANDS
        fails on a directory that is not a Lex app yet. A hand-kept roster here
        would not have noticed: it named four of the six that existed when it was
        written.
        """
        from lex.bin import lex as cli

        ai_commands = [name for name in cli.lex.commands if name.startswith("ai")]
        self.assertGreater(len(ai_commands), 4, "no AI commands were discovered")
        for name in ai_commands:
            self.assertTrue(cli._should_skip_django_bootstrap(name), name)


class SetupFormTests(unittest.TestCase):
    """The environment cards live outside the credentials form.

    HTML only submits a control outside its form when the control names its
    owner, so the whole selection was dropped and the handler fell back to the
    auto-detected set: unchecking everything but VS Code onboarded VS Code plus
    Claude Code plus Codex plus whatever else was installed.
    """

    def test_every_environment_checkbox_names_its_owning_form(self) -> None:
        document = setup_with_ai._build_setup_form_html(
            state="token",
            project_root=Path("."),
            env_file_path=Path(".env"),
            selected_environments=("vscode-copilot",),
        )
        self.assertIn(f'id="{setup_with_ai.SETUP_FORM_ID}"', document)
        for key in setup_with_ai.SUPPORTED_AI_ENVIRONMENTS:
            marker = f'value="{key}"'
            self.assertIn(marker, document, key)
        self.assertEqual(
            document.count(f'form="{setup_with_ai.SETUP_FORM_ID}"'),
            len(setup_with_ai.SUPPORTED_AI_ENVIRONMENTS),
        )

    def test_an_empty_selection_renders_nothing_checked(self) -> None:
        """Re-asking with the cleared answer refilled is not re-asking."""
        document = setup_with_ai._build_setup_form_html(
            state="token",
            project_root=Path("."),
            env_file_path=Path(".env"),
            selected_environments=(),
        )
        # Scoped to the environment checkboxes: the mode radio group legitimately
        # renders one `checked`, so a bare search for the word matches that and
        # proves nothing.
        checked_box = f'form="{setup_with_ai.SETUP_FORM_ID}" checked'
        self.assertNotIn(checked_box, document)

    def test_a_supplied_selection_is_rendered_checked(self) -> None:
        """The counterpart, so the assertion above cannot pass vacuously."""
        document = setup_with_ai._build_setup_form_html(
            state="token",
            project_root=Path("."),
            env_file_path=Path(".env"),
            selected_environments=("cursor",),
        )
        checked_box = f'form="{setup_with_ai.SETUP_FORM_ID}" checked'
        self.assertEqual(document.count(checked_box), 1)


class EnvironmentAliasTests(unittest.TestCase):
    """Aliases must resolve without the lex-mcp-local registry too.

    That is the path taken before ``setup-with-ai`` has installed the package,
    and it had no alias table at all -- so ``-e claude`` silently became
    pycharm-copilot.
    """

    def test_aliases_resolve_through_the_local_mirror(self) -> None:
        cases = {
            "claude": "claude-code",
            "claude_code": "claude-code",
            "vscode": "vscode-copilot",
            "vs-code": "vscode-copilot",
            "jetbrains": "pycharm-copilot",
            "openai-codex": "codex",
            "codeium": "windsurf",
        }
        for raw, expected in cases.items():
            self.assertEqual(setup_with_ai._resolve_environment_alias(raw), expected, raw)

    def test_the_mirror_covers_every_supported_environment(self) -> None:
        for key in setup_with_ai.SUPPORTED_AI_ENVIRONMENTS:
            self.assertEqual(setup_with_ai.AI_ENVIRONMENT_ALIASES.get(key), key, key)

    def test_an_unknown_environment_is_refused_not_defaulted(self) -> None:
        with self.assertRaises(setup_with_ai.SetupWithAIError) as caught:
            setup_with_ai.normalize_ai_environments("emacs")
        self.assertIn("emacs", str(caught.exception))

    def test_reading_back_a_drifted_value_does_not_explode(self) -> None:
        self.assertEqual(
            setup_with_ai.normalize_ai_environments("emacs", strict=False),
            (setup_with_ai.DEFAULT_AI_ENVIRONMENT,),
        )


class OnboardingInterpreterTests(unittest.TestCase):
    """Onboarding runs in the project interpreter, not ours.

    ``lex setup-with-ai -e codex`` failed with "No module named 'lex_mcp'" on
    the line after it printed "Installed lex-mcp-local": pip records the install
    as a ``.pth`` file, and ``.pth`` files are only read by ``site`` at
    interpreter startup, so the process that ran pip cannot see it.
    """

    def test_same_interpreter_detection(self) -> None:
        self.assertTrue(setup_with_ai._same_interpreter(None))
        self.assertTrue(setup_with_ai._same_interpreter(sys.executable))
        self.assertFalse(setup_with_ai._same_interpreter("/definitely/not/python"))

    def test_a_foreign_interpreter_is_driven_by_subprocess(self) -> None:
        response, error = setup_with_ai.invoke_onboarding(
            "/definitely/not/python", "describe"
        )
        self.assertIsNone(response)
        self.assertIsNotNone(error)

    def test_this_interpreter_answers_in_process(self) -> None:
        response, error = setup_with_ai.invoke_onboarding(sys.executable, "describe")
        self.assertIsNone(error, error)
        self.assertTrue(response["ok"])
        self.assertTrue(response["environments"])


if __name__ == "__main__":
    unittest.main()
