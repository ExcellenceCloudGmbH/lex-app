"""
Cluster 1o: lazy imports + sync-exclusion helpers + history-config helpers.

Intent
------

Three small but customer-relevant surfaces that the existing 1a–1n
sub-clusters never reached:

1. ``lex/process_admin/__init__.py`` — lazy ``__getattr__`` that lets
   ``from lex.process_admin import ProcessAdminSite`` work without
   pulling the heavy ``sites`` / ``settings`` / ``models`` / ``utils``
   modules at import time. Avoids circular-import storms and keeps
   the package's import cost near zero.

2. ``lex/lex_app/keycloak_exclusions.py`` — pure helper module that
   decides which models are excluded from Keycloak resource sync.
   Wrong answers here mean either (a) framework-internal models leak
   into the operator's Keycloak realm as resources, or (b) real
   business models are silently NOT protected by Keycloak. Both are
   customer-visible failures.

3. ``lex/lex_app/simple_history_config.py`` — pure helpers that decide
   which models get history tracking. Wrong answer = silent loss of
   audit trail (false negative) or HistoricalHistorical* recursion
   (false positive — the framework's own canary documented in
   ``should_track_model``).

4. ``lex/lex_app/__init__.py`` — top-level package alias. The package
   may be imported as either ``lex.lex_app`` (editable install layout)
   or ``lex_app`` (legacy layout); the alias guarantees both names
   resolve to the same module so ``from lex_app.celery import app`` and
   ``from lex.lex_app.celery import app`` are interchangeable.

All scenarios are pure-Python, no DB, no Keycloak, no Celery — they
run in single-digit milliseconds.

Scenario numbering picks up at **1.110** (1m ended at 1.109).
"""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from unittest import TestCase

# ---------------------------------------------------------------------
# 1.110–1.118 — process_admin lazy __getattr__
# ---------------------------------------------------------------------
class TestCluster01o_ProcessAdminLazyGetattr(TestCase):
    """``lex/process_admin/__init__.py`` lazy ``__getattr__``.

    Customer contract: every name in ``__all__`` must be importable
    from ``lex.process_admin`` directly, and unknown names must raise
    ``AttributeError`` (not silently return ``None``, which would
    mask typos until first use).
    """

    EXPECTED_NAMES = {
        "ProcessAdminSite",
        "processAdminSite",
        "adminSite",
        "ModelCollection",
        "ModelContainer",
        "ModelRegistration",
        "ModelStructure",
        "ModelStructureBuilder",
    }

    def test_1_110_all_advertises_eight_names(self):
        """1.110: ``__all__`` lists exactly the 8 lazy names.

        Drift here would silently break ``from lex.process_admin import *``.
        """
        import lex.process_admin as pa

        self.assertEqual(
            set(pa.__all__), self.EXPECTED_NAMES,
            f"`__all__` drift — missing: "
            f"{self.EXPECTED_NAMES - set(pa.__all__)} "
            f"unexpected: {set(pa.__all__) - self.EXPECTED_NAMES}",
        )

    def test_1_111_every_advertised_name_resolves(self):
        """1.111: every ``__all__`` entry resolves via ``__getattr__``.

        This is the per-branch coverage of the if/elif chain — one
        sub-test per name so a regression names the failing branch
        instead of producing a generic ``AttributeError``.
        """
        import lex.process_admin as pa

        for name in self.EXPECTED_NAMES:
            with self.subTest(name=name):
                resolved = getattr(pa, name)
                self.assertIsNotNone(
                    resolved,
                    f"lazy import for {name!r} returned None",
                )

    def test_1_112_unknown_name_raises_attributeerror(self):
        """1.112: unknown attribute → ``AttributeError`` (not ``None``).

        Returning ``None`` for typos is the silent-failure trap the
        explicit ``raise`` at the end of ``__getattr__`` exists to
        prevent.
        """
        import lex.process_admin as pa

        with self.assertRaises(AttributeError) as ctx:
            pa.ThisNameDoesNotExist
        self.assertIn(
            "ThisNameDoesNotExist", str(ctx.exception),
            "AttributeError message should name the missing attribute",
        )

    def test_1_113_repeated_access_is_idempotent(self):
        """1.113: re-importing the same name returns the same object.

        Lazy ``__getattr__`` is invoked on every access (Python does
        not cache `module.__getattr__` results), but each branch
        re-imports from a stable submodule, so the resolved object
        must be reference-stable. A regression that reconstructed the
        object per call would break ``isinstance`` checks downstream.
        """
        import lex.process_admin as pa

        a = pa.ProcessAdminSite
        b = pa.ProcessAdminSite
        self.assertIs(a, b, "lazy import must be reference-stable")


# ---------------------------------------------------------------------
# 1.114–1.119 — keycloak_exclusions
# ---------------------------------------------------------------------
class TestCluster01o_KeycloakExclusions(TestCase):
    """``lex/lex_app/keycloak_exclusions.py`` — pure helpers.

    Wrong answer here is customer-visible:
    - false negative on framework models (``AuditLog`` synced as a
      Keycloak resource → operator confused by phantom resources)
    - false positive on business models (real model not protected)
    """

    def test_1_114_constants_pin_known_exclusions(self):
        """1.114: pin the three exclusion constants.

        These are explicitly enumerated, not derived — a code change
        that drops one (e.g. removes ``AuditLog`` from the resource
        set) would silently start syncing the framework's own audit
        table to the customer's Keycloak realm.
        """
        from lex.lex_app import keycloak_exclusions as kx

        self.assertIn("legacy_data", kx.KEYCLOAK_SYNC_EXCLUDED_APPS)
        self.assertIn(
            "audit_logging.AuditLog",
            kx.KEYCLOAK_SYNC_EXCLUDED_RESOURCE_NAMES,
        )
        self.assertEqual(
            kx.KEYCLOAK_SYNC_EXCLUDED_MODEL_PREFIXES,
            ("historical", "metahistorical"),
            "history-table prefix tuple drift would silently start "
            "syncing simple_history's shadow tables",
        )

    def test_1_115_is_keycloak_syncable_app_table(self):
        """1.115: ``is_keycloak_syncable_app`` over the four classes
        of caller — Django built-ins, lex internals, third-party in
        site-packages, and a real user app.
        """
        from lex.lex_app.keycloak_exclusions import is_keycloak_syncable_app

        cases = [
            # (name, path, expected, reason)
            ("django.contrib.auth", "/usr/lib/django/contrib/auth", False, "Django built-in"),
            ("lex.lex_app", "/repo/lex/lex_app", False, "lex internal"),
            ("third_party_pkg", "/repo/.venv/site-packages/third_party_pkg", False, "site-packages"),
            ("my_business_app", "/repo/my_business_app", True, "real user app"),
        ]
        for name, path, expected, reason in cases:
            with self.subTest(name=name):
                cfg = types.SimpleNamespace(name=name, path=path)
                self.assertEqual(
                    is_keycloak_syncable_app(cfg), expected,
                    f"{reason}: {name} should be {expected}",
                )

    def test_1_116_is_keycloak_syncable_app_handles_missing_attrs(self):
        """1.116: missing ``name`` or ``path`` attributes default to
        empty strings — the helper must not raise. Operators sometimes
        pass shim AppConfigs in tests; a hard crash here would mask
        real Keycloak-sync errors.
        """
        from lex.lex_app.keycloak_exclusions import is_keycloak_syncable_app

        # No attributes at all — the getattr() defaults must kick in.
        empty_cfg = types.SimpleNamespace()
        # An app with no name and no path is treated as a user app
        # (not framework, not Django, not site-packages).
        self.assertTrue(is_keycloak_syncable_app(empty_cfg))

    def test_1_117_is_keycloak_sync_excluded_model_branches(self):
        """1.117: every branch of ``is_keycloak_sync_excluded_model``.

        Empty model_name → False; excluded app → True; excluded
        resource name → True; historical/metahistorical prefix → True
        (case-insensitive); plain user model → False.
        """
        from lex.lex_app.keycloak_exclusions import is_keycloak_sync_excluded_model

        for app_label, model_name, expected, reason in [
            ("my_app", "", False, "empty model name short-circuits"),
            ("legacy_data", "AnyModel", True, "excluded-app branch"),
            ("audit_logging", "AuditLog", True, "explicit resource-name"),
            ("my_app", "HistoricalThing", True, "historical prefix"),
            ("my_app", "MetaHistoricalThing", True, "metahistorical prefix"),
            ("my_app", "historicalthing", True, "case-insensitive prefix"),
            ("my_app", "Invoice", False, "plain user model"),
        ]:
            with self.subTest(case=f"{app_label}.{model_name}"):
                self.assertEqual(
                    is_keycloak_sync_excluded_model(app_label, model_name),
                    expected, reason,
                )

    def test_1_118_is_keycloak_sync_excluded_resource_name_branches(self):
        """1.118: ``is_keycloak_sync_excluded_resource_name`` covers
        bare names (no dot) AND fully-qualified names.
        """
        from lex.lex_app.keycloak_exclusions import is_keycloak_sync_excluded_resource_name

        for resource_name, expected, reason in [
            (None, False, "None short-circuits"),
            ("", False, "empty short-circuits"),
            ("HistoricalInvoice", True, "bare historical name"),
            ("Invoice", False, "bare user-model name"),
            ("audit_logging.AuditLog", True, "FQN excluded resource"),
            ("legacy_data.LegacyThing", True, "FQN excluded app"),
            ("my_app.Invoice", False, "FQN user model"),
        ]:
            with self.subTest(resource=resource_name):
                self.assertEqual(
                    is_keycloak_sync_excluded_resource_name(resource_name),
                    expected, reason,
                )


# ---------------------------------------------------------------------
# 1.119–1.122 — simple_history_config helpers
# ---------------------------------------------------------------------
class TestCluster01o_SimpleHistoryConfig(TestCase):
    """``lex/lex_app/simple_history_config.py`` — pure helpers.

    The helpers decide whether a model gets history tracking. Wrong
    answer = silent loss of audit trail OR a HistoricalHistorical*
    recursion that the ``Historical`` prefix guard explicitly exists
    to prevent.
    """

    def _make_model(self, *, name, app_label, abstract=False, has_history=False):
        """Build a synthetic model class for the helpers to inspect."""
        meta = types.SimpleNamespace(app_label=app_label, abstract=abstract)
        attrs = {"_meta": meta, "__name__": name}
        if has_history:
            attrs["history"] = object()  # presence is what matters
        # We use type() to give it a real ``__name__`` attribute.
        cls = type(name, (), attrs)
        return cls

    def test_1_119_should_track_model_historical_prefix_blocked(self):
        """1.119: any class whose name starts with ``Historical`` is
        blocked. This is the canary against simple_history's own
        shadow tables being re-tracked → infinite ``HistoricalHistorical*``
        chain.
        """
        from lex.lex_app.simple_history_config import should_track_model

        m = self._make_model(name="HistoricalInvoice", app_label="my_app")
        self.assertFalse(
            should_track_model(m),
            "Historical-prefixed models must never be re-tracked",
        )

    def test_1_120_should_track_model_django_apps_blocked(self):
        """1.120: models from Django built-in apps are blocked when
        the auto-exclusion setting is on (the default).
        """
        from lex.lex_app.simple_history_config import should_track_model

        for app_label in ("admin", "auth", "contenttypes", "sessions"):
            with self.subTest(app=app_label):
                m = self._make_model(name="Thing", app_label=app_label)
                self.assertFalse(
                    should_track_model(m),
                    f"Django built-in app {app_label!r} should be blocked",
                )

    def test_1_121_should_track_model_already_has_history(self):
        """1.121: a model that already has a ``history`` attribute is
        not re-tracked — prevents double-registration when an app's
        ``ready()`` runs twice (e.g. test reload).
        """
        from lex.lex_app.simple_history_config import should_track_model

        m = self._make_model(
            name="AlreadyTracked", app_label="my_app", has_history=True,
        )
        self.assertFalse(should_track_model(m))

    def test_1_122_should_track_model_normal_user_model_passes(self):
        """1.122: a vanilla user-app model (non-historical, non-Django,
        non-abstract, no existing history) IS tracked — the positive
        path that the negatives are guarding.
        """
        from lex.lex_app.simple_history_config import should_track_model

        m = self._make_model(name="Invoice", app_label="my_app")
        self.assertTrue(
            should_track_model(m),
            "vanilla user model must be tracked — silent false here "
            "would lose audit trail across the customer's data",
        )

    def test_1_123_get_model_exclusion_reason_table(self):
        """1.123: ``get_model_exclusion_reason`` returns a non-None
        human-readable reason for every blocked case AND ``None`` for
        the tracked case. The string content is asserted loosely
        (substring) so doc copy-edits don't break the gate.
        """
        from lex.lex_app.simple_history_config import get_model_exclusion_reason

        cases = [
            (self._make_model(name="HistoricalInvoice", app_label="my_app"),
             "historical"),
            (self._make_model(name="LogEntry", app_label="admin"),
             "django"),
            (self._make_model(name="Abstract", app_label="my_app", abstract=True),
             "abstract"),
            (self._make_model(name="Tracked", app_label="my_app", has_history=True),
             "history"),
        ]
        for model, expected_substring in cases:
            with self.subTest(model=model.__name__):
                reason = get_model_exclusion_reason(model)
                self.assertIsNotNone(
                    reason,
                    f"{model.__name__} is blocked but reason is None",
                )
                self.assertIn(
                    expected_substring, reason.lower(),
                    f"reason {reason!r} should mention {expected_substring!r}",
                )

        # Positive: a vanilla user model has no exclusion reason.
        ok_model = self._make_model(name="Invoice", app_label="my_app")
        self.assertIsNone(
            get_model_exclusion_reason(ok_model),
            "vanilla user model should have no exclusion reason",
        )


# ---------------------------------------------------------------------
# 1.124 — lex_app package alias
# ---------------------------------------------------------------------
class TestCluster01o_LexAppPackageAlias(TestCase):
    """``lex/lex_app/__init__.py`` — sys.modules aliasing.

    The package may be imported as either ``lex.lex_app`` (editable
    install / repo layout) or ``lex_app`` (legacy customer-project
    layout). Both names must resolve to the same module object —
    otherwise two parallel registrations of every model would happen
    on Django startup.
    """

    def test_1_124_both_names_resolve_to_same_module(self):
        """1.124: ``lex.lex_app`` and ``lex_app`` are the same module.

        A regression where the alias was dropped would cause
        ``django.apps.AppConfig`` instances loaded under one name to
        be unable to find models registered under the other —
        observed historically as ``LookupError: No installed app with
        label 'lex_app'``.
        """
        # Force the import — the alias is set up at first import.
        import lex.lex_app  # noqa: F401

        # Both names should be in sys.modules and point at the same
        # module object.
        self.assertIn(
            "lex.lex_app", sys.modules,
            "primary import name missing from sys.modules",
        )
        self.assertIn(
            "lex_app", sys.modules,
            "legacy alias 'lex_app' missing — was the setdefault dropped?",
        )
        self.assertIs(
            sys.modules["lex.lex_app"], sys.modules["lex_app"],
            "the two names must resolve to the same module object",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
