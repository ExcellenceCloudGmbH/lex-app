"""lex_config installs every npm package the RFDS browser code a lex.ui page loads imports.

Intent: a project's dashboards must build from `lex_config()` alone. A lex.ui table with
`search=True` draws RFDS's clearable search input, whose clear button imports `rfdsClearInput`
from RFDS's `rfds_form.js`; that file imports `react-number-format` when it loads, and RFDS
declares the package only on its form and number inputs. A project that uses neither got a
dev server that could not resolve the import, and a blank page. A regression is a project whose
dashboards stop building because RFDS's browser code imports a package nobody installs, or a
project that lists its own frontend packages and silently loses lex-app's.

Cluster 1dg -- scenario 1.832. Type: U.
Covers: lex/lex_app/reflex/config.py (lex_config).
Run: python -m lex pytest lex/test_project/tests/init/test_1dg_reflex_frontend_packages.py -v
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase

pytestmark = pytest.mark.init

#: Variables a developer's shell may carry that would change the configuration.
_NO_LEX_ENV = {key: "" for key in ("LEX_FORMAT_LOCALE", "LEX_UI_LANGUAGE", "LEX_BASE_CURRENCY",
                                   "LEX_TIME_ZONE", "LEX_RFDS_CONFORMANCE", "CI", "LEX_APP_HOSTS",
                                   "DOMAIN_HOSTED")}

_IMPORT = re.compile(r"""^\s*import\s+(?:[^;]*?\s+from\s+)?["']([^"'./$][^"']*)["']""", re.MULTILINE)


def _package(specifier: str) -> str:
    """The npm package an import names: ``@scope/name`` or ``name``, without a sub-path."""
    parts = specifier.split("/")
    return "/".join(parts[:2]) if specifier.startswith("@") else parts[0]


def _name(entry: str) -> str:
    """``react-number-format@5.4.5`` -> ``react-number-format`` (a scoped name keeps its ``@``)."""
    return entry.rsplit("@", 1)[0] if entry.rfind("@") > 0 else entry


class TestCluster01dg_ReflexFrontendPackages(SimpleTestCase):
    """1.832: lex_config installs what RFDS's browser code imports."""

    # -- 1.832 ---------------------------------------------------------
    def test_1_832_every_package_rfds_form_js_imports_is_installed(self) -> None:
        """
        Scenario 1.832: the packages behind a lex.ui table's search box are installed.
        Given: RFDS's rfds_form.js, which a lex.ui table's clearable search input imports from.
        When:  a project builds its configuration with lex_config(), with and without packages
               of its own.
        Then:  every npm package rfds_form.js imports is one Reflex installs, Radix (the theme
               plugin lex_config lists), or one of lex_config's frontend packages, pinned to
               RFDS's own version; and a project's own packages sit beside lex-app's instead of
               replacing them.
        """
        import rfds
        from reflex_base.constants.installer import PackageJson
        from rfds._vendor._npm import spec

        from lex.lex_app.reflex.config import lex_config

        source = (Path(rfds.__file__).parent / "_vendor" / "js" / "rfds_form.js").read_text()
        imported = {_package(found) for found in _IMPORT.findall(source)}
        self.assertIn("react-number-format", imported,
                      "rfds_form.js must still import react-number-format, or this scenario tests nothing")

        with patch.dict(os.environ, _NO_LEX_ENV):
            config = lex_config()
            own = lex_config(frontend_packages=["left-pad@1.3.0"])
        provided = set(PackageJson.DEPENDENCIES) | {"@radix-ui/themes"} | {_name(p) for p in config.frontend_packages}
        self.assertEqual(sorted(imported - provided), [],
                         "every package rfds_form.js imports must be installed, or a page with a lex.ui "
                         "search box fails to build")
        self.assertIn(spec("react-number-format"), config.frontend_packages,
                      "the package is pinned to the version RFDS ships against")
        self.assertIn("left-pad@1.3.0", own.frontend_packages, "a project's own package is kept")
        self.assertIn(spec("react-number-format"), own.frontend_packages,
                      "a project's own packages must not drop the one lex-app needs")
