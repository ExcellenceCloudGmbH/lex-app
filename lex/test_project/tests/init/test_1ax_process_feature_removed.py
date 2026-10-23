"""The ``Process`` model feature is gone from the framework's public surface.

Intent
------
``Process`` was an abstract model a project subclassed to describe a workflow:
registration picked the subclass up, ``lexAdminSite.registerProcess`` put
it in a registry, and ``api/process/<name>`` served its ``get_structure()`` to
the frontend. The whole feature has been removed, and a removal is only
finished when every door into it is shut:

* the class is no longer importable from ``lex.core.models`` — a project that
  still subclasses it must fail loudly at import, not register a model the
  framework will never route to;
* the admin site keeps no process registry — a leftover ``registerProcess``
  would collect classes nothing reads, which looks like a working feature;
* ``Process`` is no longer a **reserved** class name. This is the part that
  reaches a customer: while the framework owned the name, a project's own
  ``Process`` model was rejected outright at registration. Giving the name
  back is the point of removing the feature, and a stale entry in the
  reserved set would keep punishing projects for a class that no longer
  exists;
* the model-structure payload the frontend reads knows only ``Model``,
  ``HTMLReport`` and ``Folder`` node types — a ``"Process"`` type would send
  the frontend looking for an endpoint that is gone.

``HTMLReport`` is the sibling feature that shares all of this machinery and
was deliberately **kept**, so each scenario checks it still works — that is
what separates "the Process feature was removed" from "registration was
broken".

The same sweep took the *name* out of the admin backbone, which was never the
removed feature — it is the framework's routing and registration core — but
carried ``Process`` through its package, classes and public test base. The
machinery is unchanged; only the names moved (``lex.process_admin`` →
``lex.model_admin``, ``ProcessAdminSite`` → ``LexAdminSite``,
``processAdminSite`` → ``lexAdminSite``, ``ModelProcessAdmin`` →
``LexModelAdmin``, ``ProcessAdminTestCase`` → ``LexAppTestCase``). The last of
those is a **breaking change for downstream projects**, taken deliberately and
without a compatibility alias — an alias would keep the old name alive, which
is the thing being removed — so 1.412 pins that the old import is gone rather
than quietly re-exported.

Cluster 1ax — scenarios 1.406–1.412. Type: I.
Covers: lex/core/models/__init__.py (``Process`` dropped from ``__all__``),
lex/core/models/Process.py (deleted),
lex/model_admin/sites/lex_admin_site.py (process registry),
lex/model_admin/settings/lex_admin_settings.py (renamed from
lex/process_admin/settings/process_admin_settings.py; 1.413 imports it),
lex/model_admin/utils/model_registration.py (Process branch and reserved
name), lex/model_admin/models/utils.py (structure node types).
Run: python -m lex pytest lex/test_project/tests/init/test_1ax_process_feature_removed.py -v
"""

from __future__ import annotations

import importlib
import pathlib
import unittest

import pytest
from django.db import models
from django.test import TestCase

from lex.core.models.HTMLReport import HTMLReport
from lex.core.models.LexModel import LexModel
from lex.model_admin.models.ModelCollection import ModelCollection
from lex.model_admin.models.LexModelAdmin import LexModelAdmin
from lex.model_admin.utils.model_registration import ModelRegistration

pytestmark = pytest.mark.init

#: The installed framework package — the tree the rename had to reach.
_LEX_ROOT = pathlib.Path(importlib.import_module("lex").__file__).resolve().parent


class Process(LexModel):
    """A project's own model, named after the class the framework gave back.

    Declared exactly as a customer would declare it. Before the removal this
    name was reserved and registration refused it.
    """

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


class StructureProbeModel(LexModel):
    """A plain business model — the ``Model`` node type."""

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


class RegistrationProbe(LexModel):
    """A model registered on the live site in 1.411, then unregistered."""

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


class StructureProbeReport(HTMLReport):
    """The surviving sibling feature — the ``HTMLReport`` node type."""

    def get_html(self, user):
        return "<p>probe</p>"


def _clash(name):
    """A model class carrying a name the framework still owns.

    Abstract on purpose: validation reads the class name and its fields, and
    an abstract model never enters Django's app registry, so probing a
    reserved name leaves nothing behind for the rest of the suite.
    """
    return type(name, (LexModel,), {
        "__module__": __name__,
        "Meta": type("Meta", (), {"abstract": True, "app_label": "lex_app"}),
    })


class TestCluster01ax_ProcessFeatureRemoved(TestCase):
    """Cluster 1ax: nothing of the Process feature is reachable any more."""

    # -- 1.406 ---------------------------------------------------------
    def test_1_406_process_is_not_importable_from_core_models(self) -> None:
        """
        Scenario 1.406: ``lex.core.models`` no longer offers ``Process``.
        Given: the framework after the Process removal
        When: the module is imported and ``__all__`` is read
        Then: ``lex.core.models.Process`` does not import, ``Process`` is not
              advertised in ``__all__``, and the names kept beside it
              (``HTMLReport``, ``LexModel``, ``UserContext``) still are.
        """
        core_models = importlib.import_module("lex.core.models")

        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("lex.core.models.Process")
        self.assertNotIn("Process", core_models.__all__)
        self.assertFalse(hasattr(core_models, "Process"))

        for kept in ("LexModel", "UserContext", "PermissionResult",
                     "CalculationModel", "HTMLReport"):
            self.assertIn(kept, core_models.__all__, f"{kept} must survive the removal")

    # -- 1.407 ---------------------------------------------------------
    def test_1_407_the_admin_site_keeps_no_process_registry(self) -> None:
        """
        Scenario 1.407: ``LexAdminSite`` has no process registry left.
        Given: the live ``lexAdminSite`` singleton
        When: its process registry and registration hooks are looked up
        Then: ``processes``, ``registerProcess`` and
              ``get_process_structure_func`` are all absent, while the
              HTMLReport equivalents (``html_reports``, ``registerHTMLReport``,
              ``get_html_report_func``) are still there.
        """
        from lex.model_admin.settings import lexAdminSite

        for gone in ("processes", "registerProcess", "get_process_structure_func"):
            self.assertFalse(
                hasattr(lexAdminSite, gone),
                f"lexAdminSite still exposes {gone}",
            )
        self.assertFalse(hasattr(ModelRegistration, "_register_process_model"))

        for kept in ("html_reports", "registerHTMLReport", "get_html_report_func"):
            self.assertTrue(
                hasattr(lexAdminSite, kept),
                f"the HTMLReport feature lost {kept} in the removal",
            )

    # -- 1.408 ---------------------------------------------------------
    def test_1_408_a_project_may_name_its_own_model_process(self) -> None:
        """
        Scenario 1.408: ``Process`` is no longer a reserved class name.
        Given: a project model declared as ``class Process(LexModel)``
        When: registration validates it
        Then: validation passes and ``Process`` is not in the reserved set,
              while the names the framework still owns (``LexModel``,
              ``CalculationModel``, ``HTMLReport``) are rejected as before and
              the ``Historical`` prefix is still refused.
        """
        self.assertNotIn("Process", ModelRegistration._RESERVED_CLASS_NAMES)
        # Does not raise — the name belongs to the project again.
        ModelRegistration._validate_model_definition(Process, will_track_history=True)

        for still_reserved in ("LexModel", "CalculationModel", "HTMLReport"):
            with self.subTest(name=still_reserved):
                self.assertIn(still_reserved, ModelRegistration._RESERVED_CLASS_NAMES)
                with self.assertRaises(ValueError):
                    ModelRegistration._validate_model_definition(_clash(still_reserved))

        with self.assertRaises(ValueError):
            ModelRegistration._validate_model_definition(_clash("HistoricalThing"))

    # -- 1.409 ---------------------------------------------------------
    def test_1_409_the_model_structure_has_no_process_node_type(self) -> None:
        """
        Scenario 1.409: the structure the frontend reads types only what exists.
        Given: a collection holding a plain model and an HTMLReport
        When: ``model_structure_with_readable_names`` — the payload
              ``api/model-structure`` serves — is built
        Then: the model types as ``Model``, the report as ``HTMLReport``, the
              branch as ``Folder``, and no node anywhere carries the retired
              ``Process`` type.
        """
        collection = ModelCollection(
            {
                StructureProbeModel: LexModelAdmin(),
                StructureProbeReport: LexModelAdmin(),
            },
            {"Probe": {"structureprobemodel": None, "structureprobereport": None}},
            {},
            auto_include_missing_models=False,
        )

        structure = collection.model_structure_with_readable_names
        self.assertEqual(structure["Probe"]["type"], "Folder")
        children = structure["Probe"]["children"]
        self.assertEqual(children["structureprobemodel"]["type"], "Model")
        self.assertEqual(children["structureprobereport"]["type"], "HTMLReport")

        def types(node):
            yield node["type"]
            for child in node.get("children", {}).values():
                yield from types(child)

        every_type = {t for node in structure.values() for t in types(node)}
        self.assertNotIn("Process", every_type, f"a Process node survived: {every_type}")

    # -- 1.410 ---------------------------------------------------------
    def test_1_410_the_package_is_lex_model_admin(self) -> None:
        """
        Scenario 1.410: the admin backbone lost the Process name.
        Given: the framework after the rename
        When: the package is imported under both names and its lazy
              ``__getattr__`` is asked for the old and new spellings
        Then: ``lex.model_admin`` imports and offers all eight lazy names,
              ``lex.process_admin`` raises ModuleNotFoundError, and the two
              retired spellings raise AttributeError rather than resolving.
        """
        model_admin = importlib.import_module("lex.model_admin")

        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("lex.process_admin")

        self.assertEqual(
            set(model_admin.__all__),
            {"LexAdminSite", "lexAdminSite", "adminSite", "ModelCollection",
             "ModelContainer", "ModelRegistration", "ModelStructure",
             "ModelStructureBuilder"},
        )
        for name in model_admin.__all__:
            with self.subTest(name=name):
                self.assertIsNotNone(getattr(model_admin, name))

        for retired in ("ProcessAdminSite", "processAdminSite"):
            with self.subTest(name=retired):
                with self.assertRaises(AttributeError):
                    getattr(model_admin, retired)

    # -- 1.411 ---------------------------------------------------------
    def test_1_411_the_model_admin_class_lost_the_process_name(self) -> None:
        """
        Scenario 1.411: ``ModelProcessAdmin`` is now ``LexModelAdmin``.
        Given: ``lex.model_admin.models``
        When: the admin class is looked up under both names, and the site is
              asked to register a model without an explicit admin
        Then: only ``LexModelAdmin`` is exported, and the default admin the
              site attaches is an instance of it — the rename reached the
              registration path, not just the export list.
        """
        models_pkg = importlib.import_module("lex.model_admin.models")
        self.assertIn("LexModelAdmin", models_pkg.__all__)
        self.assertNotIn("ModelProcessAdmin", models_pkg.__all__)
        self.assertFalse(hasattr(models_pkg, "ModelProcessAdmin"))

        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("lex.model_admin.models.ModelProcessAdmin")

        from django.db.models.signals import post_save

        from lex.core.signals import do_post_save
        from lex.model_admin.settings import lexAdminSite

        # The site is a singleton the whole suite shares, so put back what
        # registering a probe model changes.
        self.assertNotIn(RegistrationProbe, lexAdminSite.registered_models)

        def unregister():
            lexAdminSite.registered_models.pop(RegistrationProbe, None)
            post_save.disconnect(do_post_save, sender=RegistrationProbe)

        self.addCleanup(unregister)

        lexAdminSite.register([RegistrationProbe])
        self.assertIsInstance(
            lexAdminSite.registered_models[RegistrationProbe],
            models_pkg.LexModelAdmin,
        )

    # -- 1.412 ---------------------------------------------------------
    def test_1_412_the_public_test_base_is_lex_app_test_case(self) -> None:
        """
        Scenario 1.412: ``ProcessAdminTestCase`` is now ``LexAppTestCase``.
        Given: the public base class a project's own tests subclass
        When: it is imported under the new name and the old module path
        Then: ``lex.lex_app.tests.LexAppTestCase.LexAppTestCase`` is a
              TestCase subclass and the old module is gone — no compatibility
              alias, because an alias would keep the retired name alive.
        """
        module = importlib.import_module("lex.lex_app.tests.LexAppTestCase")
        self.assertTrue(issubclass(module.LexAppTestCase, unittest.TestCase))

        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("lex.lex_app.tests.ProcessAdminTestCase")
        self.assertFalse(hasattr(module, "ProcessAdminTestCase"))

    # -- 1.413 ---------------------------------------------------------
    def test_1_413_nothing_still_points_at_the_old_names(self) -> None:
        """
        Scenario 1.413: the rename reached every importer, not just the package.
        Given: a rename that rewrote 98 files across the framework
        When: each module that referenced the old package is imported, and the
              whole `lex/` tree is read for the retired spellings
        Then: every one of them imports, and no source file under `lex/` names
              any retired spelling — the backbone's old ones (the package, the
              site, the model admin, the public test base, the config, the URL
              namespace) or the removed feature's (the structure view, the
              flow package, the step route, the registrar). A half-finished
              rename raises at *some* importer and which one depends on load
              order, so the source sweep is the gate and the imports are the
              proof.
        """
        for dotted in (
            "lex.api.filter_policy",
            "lex.api.views.model_info.Widgets",
            "lex.api.views.rbac.RBACInfo",
            "lex.core.calculated_updates.update_handler",
            "lex.core.tests._bitemporal_test_case",
            "lex.model_admin.settings.lex_admin_settings",
            "lex.model_admin.tests.django_test_settings",
            "lex.tools.pytest_plugin",
            "lex.utilities.import_system.module_finder",
        ):
            with self.subTest(module=dotted):
                self.assertIsNotNone(importlib.import_module(dotted))

        # Both the feature's names and the admin backbone's old spellings. Each
        # is assembled rather than written out, so this file does not match its
        # own search and the ban can be absolute everywhere else.
        _p = "Pro" + "cess"
        _lower = _p.lower()
        retired = (
            f"lex.{_lower}_admin", f"lex/{_lower}_admin", f"{_p}AdminSite",
            f"{_lower[0]}{_p[1:]}AdminSite", f"Model{_p}Admin", f"{_p}AdminTestCase",
            f"{_p}AdminConfig", f"{_lower}_admin_rest_api", f"{_p}Structure",
            f"{_p}Flow", f"{_lower}_flow", "run_step", f"register{_p}",
        )
        # Three files may name it: the two that assert the absence (this one
        # and 10q, which pins that the routes and the old namespace are gone),
        # and 4o, whose header records why scenario 4.90 was retired. Naming a
        # retired surface in prose that explains its retirement is the record,
        # not residue. Everything else under lex/ must be clean.
        allowed = {
            pathlib.Path(__file__).resolve(),
            (_LEX_ROOT / "test_project/tests/api_layer/test_10q_process_routes_removed.py").resolve(),
            (_LEX_ROOT / "test_project/tests/permissions/test_4o_secure_defaults.py").resolve(),
        }
        offenders = []
        for path in _LEX_ROOT.rglob("*.py"):
            if path.resolve() in allowed or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            hits = [name for name in retired if name in text]
            if hits:
                offenders.append(f"{path.relative_to(_LEX_ROOT)}: {', '.join(hits)}")
        self.assertEqual(offenders, [], "retired names still in the tree:\n" + "\n".join(offenders))
