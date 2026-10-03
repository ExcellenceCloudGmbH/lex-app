"""Cluster 1au: ``lex --version`` names the lex-app release a project runs.

Intent
------
"Which version are you on?" is the first thing support asks and the first check
the installation guide gives, so the answer has to come from the command every
project already has. ``lex --version`` prints it.

It must agree with the version an instance reports on ``/health``, which reads
``lex._version.__version__`` rather than the package metadata: an image built
from a git ref stamps that module after ``pip install``, while the metadata stays
frozen at what was installed. And like ``lex --help`` it must answer from any
directory without setting Django up, because Django setup fails in a directory
whose name is not a valid Python identifier.

A regression reads as ``Error: No such option '--version'``, as a version that
disagrees with ``/health``, or as a crash outside a project.

Cluster 1au -- scenarios 1.384-1.385. Type: U.
Covers: lex/bin/lex.py (the ``lex`` group's ``--version`` option, ``main()``).
Run: python -m lex pytest lex/test_project/tests/init/test_1au_lex_version.py -v
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest import TestCase

import pytest

import lex

pytestmark = pytest.mark.init

#: The checkout this suite is testing. The child process is pointed at it, or it
#: would import whichever lex the interpreter has installed.
_CHECKOUT = Path(lex.__file__).resolve().parent.parent

#: What the image build writes into ``lex/_version.py`` for a git-ref image
#: (``0.0.0.dev0+<ref>.<sha>``). The package metadata of the same install still
#: says ``0.0.0.dev0``.
_STAMPED_VERSION = "0.0.0.dev0+feat.flow.1a2b3c4"

#: Variables that would tie the child process to the test project. A user
#: typing ``lex --version`` in a fresh shell has none of them.
_PROJECT_ENV = ("PROJECT_ROOT", "DJANGO_SETTINGS_MODULE", "LEX_APP_PACKAGE_ROOT")


class TestCluster01au_LexVersion(TestCase):
    """Cluster 1au: ``lex --version``, run the way a user runs it."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # Not a project, not inside a git checkout, and not a valid Python
        # identifier -- the directory ``main()`` guards ``--help`` against.
        self.cwd = Path(tmp.name) / "release-smoke"
        self.cwd.mkdir()
        self.env = {k: v for k, v in os.environ.items() if k not in _PROJECT_ENV}
        self.env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(_CHECKOUT), self.env.get("PYTHONPATH")) if p
        )

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, *args],
            cwd=self.cwd,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_1_384_version_is_the_one_health_reports(self):
        """
        Scenario 1.384: ``lex --version`` prints ``lex._version.__version__``.
        Given: an install whose ``lex/_version.py`` was stamped after
            ``pip install``, as a git-ref image is, so the package metadata
            still says ``0.0.0.dev0``
        When: ``lex --version`` runs through the console script's entry point
        Then: it prints ``lex, version <stamped>`` and exits 0 -- the version
            ``/health`` reports, not the metadata's
        """
        script = textwrap.dedent(
            f"""
            import sys
            import lex._version
            lex._version.__version__ = {_STAMPED_VERSION!r}
            sys.argv = ["lex", "--version"]
            from lex.__main__ import main
            main()
            """
        )
        result = self._run("-c", script)

        self.assertEqual(
            result.returncode,
            0,
            f"`lex --version` should exit 0; stderr was:\n{result.stderr}",
        )
        self.assertEqual(
            result.stdout,
            f"lex, version {_STAMPED_VERSION}\n",
            "`lex --version` should print the stamped lex._version.__version__ "
            "(what /health reports), not the frozen package metadata",
        )

    def test_1_385_answers_outside_a_project_without_django(self):
        """
        Scenario 1.385: ``lex --version`` works anywhere.
        Given: a shell in a directory that is no project and whose name,
            ``release-smoke``, Django cannot load as an app
        When: the user runs ``lex --version`` (``python -m lex`` is the same
            entry point as the ``lex`` console script)
        Then: it exits 0 with one ``lex, version ...`` line -- it never sets
            Django up, which would fail in this directory
        """
        result = self._run("-m", "lex", "--version")

        self.assertEqual(
            result.returncode,
            0,
            "`lex --version` should exit 0 outside a project; "
            f"stderr was:\n{result.stderr}",
        )
        self.assertRegex(
            result.stdout,
            re.compile(r"\Alex, version \S+\n\Z"),
            "`lex --version` should print exactly one `lex, version X` line",
        )
