"""``lex.core.signals`` exports exactly what it provides.

Intent: the package re-exports the calculation-signal API --
``update_calculation_status``, ``do_post_save`` and ``custom_post_save`` -- so a
project can import it from one place. A star import binds every name in
``__all__``, so a single name advertised there but never defined makes
``from lex.core.signals import *`` raise ``AttributeError`` and takes the whole
API down with it. That is what the two profile handlers removed in February did:
the functions and their import went, their names stayed in ``__all__``.

Cluster 9g -- scenario 9.43. Type: U.
Covers: lex/core/signals/__init__.py.
Run: python -m lex pytest lex/test_project/tests/signals_ws/test_9g_signals_package_exports.py -v
"""

from __future__ import annotations

import pytest
from django.test import SimpleTestCase

from lex.core.signals import CalculationSignals

pytestmark = pytest.mark.signals_ws


class TestCluster09g_SignalsPackageExports(SimpleTestCase):
    """Cluster 9g: what ``from lex.core.signals import *`` gives a project."""

    def test_9_43_star_import_binds_the_calculation_signal_api(self):
        """
        Scenario 9.43: the package's star import works.
        Given: a module that wants the calculation-signal API
        When: it runs ``from lex.core.signals import *``
        Then: the import succeeds and binds ``update_calculation_status``,
            ``do_post_save`` and ``custom_post_save`` -- the functions
            ``CalculationSignals`` defines, not stand-ins
        """
        namespace: dict = {}
        try:
            exec("from lex.core.signals import *", namespace)
        except AttributeError as exc:
            self.fail(
                "`from lex.core.signals import *` raised AttributeError -- "
                f"`__all__` names something the package does not provide: {exc}"
            )

        for name in ("update_calculation_status", "do_post_save", "custom_post_save"):
            with self.subTest(name=name):
                self.assertIs(
                    namespace.get(name),
                    getattr(CalculationSignals, name),
                    f"the star import should bind CalculationSignals.{name}",
                )
