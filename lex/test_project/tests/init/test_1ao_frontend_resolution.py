"""Cluster 1ao — where the single-page app is served from.

The frontend is a separate distribution, ``lex-app-frontend``, pinned in
requirements.txt and installed by pip. ``settings._resolve_react_build_path``
decides which copy Django serves, and the decision has three branches worth
pinning: the installed package wins, a source checkout still works, and a
broken package must not stop the instance booting.

Run:
    lex test lex.test_project.tests.init.test_1ao_frontend_resolution
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import TestCase

import pytest

pytestmark = pytest.mark.init


class TestCluster01ao_FrontendResolution(TestCase):
    """``lex/lex_app/settings.py`` — resolving the bundle to serve."""

    def test_1_342_the_installed_package_wins_over_the_in_tree_bundle(self):
        """1.342: an installed ``lex-app-frontend`` is preferred.

        The pin in requirements.txt is what the release declared, and what a
        project can deliberately override. If the committed copy won instead,
        an override would install and then be silently ignored — the failure
        mode with no error and nothing in the logs.
        """
        from lex.lex_app import settings as lex_settings

        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp)
            (sentinel / "index.html").write_text("<!doctype html>")

            class _Package:
                @staticmethod
                def build_path():
                    return sentinel

            resolved = lex_settings._resolve_react_build_path(package=_Package)
            self.assertEqual(Path(resolved), sentinel)

    def test_1_343_a_source_checkout_falls_back_to_the_in_tree_bundle(self):
        """1.343: no installed package means the committed bundle is used.

        This is what lets the resolution land before the pin does, and what
        keeps a source checkout runnable.
        """
        from lex.lex_app import settings as lex_settings

        resolved = lex_settings._resolve_react_build_path(package=None)
        self.assertTrue(
            resolved.endswith("react/build"),
            f"expected the in-tree bundle, got {resolved}",
        )

    def test_1_344_a_broken_package_falls_back_rather_than_crashing(self):
        """1.344: a package that cannot find its own bundle must not stop boot.

        This runs at import time, so an exception takes the whole instance
        down. Serving the committed copy is strictly better than not starting.
        """
        from lex.lex_app import settings as lex_settings

        class _Broken:
            @staticmethod
            def build_path():
                raise FileNotFoundError("the frontend bundle is missing")

        resolved = lex_settings._resolve_react_build_path(package=_Broken)
        self.assertTrue(resolved.endswith("react/build"))

    def test_1_345_no_bundle_anywhere_says_so_instead_of_serving_nothing(self):
        """1.345: with neither source available, warn at boot.

        Once the committed bundle is gone, an instance with no frontend
        package resolves to a directory that does not exist and 404s every
        page with nothing in the logs. One loud line at startup is the
        difference between a five-minute diagnosis and a long one.
        """
        from unittest import mock

        from lex.lex_app import settings as lex_settings

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "definitely-not-here"
            with mock.patch.object(
                lex_settings.Path, "is_file", return_value=False
            ), mock.patch("sys.stderr") as stderr:
                lex_settings._resolve_react_build_path(package=None)

            printed = "".join(
                str(call.args[0]) for call in stderr.write.call_args_list if call.args
            )
            self.assertIn("No frontend bundle found", printed)
