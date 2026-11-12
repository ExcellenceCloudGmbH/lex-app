"""A forwarded management command that fails answers like manage.py: one line and its exit code.

Intent
------
The ``lex`` CLI forwards every Django management command (``lex oidc_check``, ``lex migrate``,
``lex prune_dashboard_sessions``, ...). It ran them through ``call_command``, which lets a
``CommandError`` escape as an uncaught exception, so an expected answer -- ``lex oidc_check``
finding a problem, a missing argument -- printed a Python traceback (found while running the
documented sign-in operating commands end to end). ``manage.py`` prints
``CommandError: <message>`` and exits with the command's code; ``lex`` must do the same, and
still show the traceback when asked with ``--traceback``.

Cluster 01bi -- scenarios 1.607-1.608. Type: I.
Covers: lex/bin/lex.py (_forward_to_django).
Run: python -m lex pytest lex/test_project/tests/init/test_1bi_cli_command_errors.py -v
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from lex.bin.lex import _install_dynamic_commands, lex

pytestmark = pytest.mark.init


class TestCluster01bi_CliCommandErrors(SimpleTestCase):
    """``lex <management command>`` when the command raises ``CommandError``."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        _install_dynamic_commands()  # what `lex` does before dispatching

    def test_1_607_a_failing_command_prints_one_line_and_its_exit_code(self) -> None:
        """Scenario 1.607: a forwarded command fails the way manage.py fails.

        Given the forwarded `oidc_urls` command, run without its required host
        When `lex oidc_urls` runs
        Then it exits 1 and prints `CommandError: Error: the following arguments are required: host`
             on stderr, and no traceback
        """
        result = CliRunner().invoke(lex, ["oidc_urls"])

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIsInstance(result.exception, SystemExit)
        self.assertIn("CommandError: Error: the following arguments are required: host", result.stderr)
        self.assertNotIn("Traceback", result.output)

    def test_1_608_traceback_still_shows_the_exception(self) -> None:
        """Scenario 1.608: --traceback keeps the exception, for debugging.

        Given the same failing command, asked for its traceback
        When `lex oidc_urls --traceback` runs
        Then the CommandError itself reaches the caller, as with manage.py --traceback
        """
        result = CliRunner().invoke(lex, ["oidc_urls", "--traceback"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIsInstance(result.exception, CommandError)
