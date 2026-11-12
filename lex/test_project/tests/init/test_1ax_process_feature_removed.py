"""The ``Process`` model feature is gone from the framework's public surface.

Intent
------
``Process`` was an abstract model a project subclassed to describe a workflow:
registration picked the subclass up, ``processAdminSite.registerProcess`` put
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

Cluster 1ax — scenarios 1.406–1.409. Type: I.
Covers: lex/core/models/__init__.py (``Process`` dropped from ``__all__``),
lex/core/models/Process.py (deleted),
lex/process_admin/sites/process_admin_site.py (process registry),
lex/process_admin/utils/model_registration.py (Process branch and reserved
name), lex/process_admin/models/utils.py (structure node types).
Run: python -m lex pytest lex/test_project/tests/init/test_1ax_process_feature_removed.py -v
"""

from __future__ import annotations

import importlib

import pytest
from django.db import models
from django.test import TestCase

from lex.core.models.HTMLReport import HTMLReport
from lex.core.models.LexModel import LexModel
from lex.process_admin.models.ModelCollection import ModelCollection
from lex.process_admin.models.ModelProcessAdmin import ModelProcessAdmin
from lex.process_admin.utils.model_registration import ModelRegistration

pytestmark = pytest.mark.init


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
        Scenario 1.407: ``ProcessAdminSite`` has no process registry left.
        Given: the live ``processAdminSite`` singleton
        When: its process registry and registration hooks are looked up
        Then: ``processes``, ``registerProcess`` and
              ``get_process_structure_func`` are all absent, while the
              HTMLReport equivalents (``html_reports``, ``registerHTMLReport``,
              ``get_html_report_func``) are still there.
        """
        from lex.process_admin.settings import processAdminSite

        for gone in ("processes", "registerProcess", "get_process_structure_func"):
            self.assertFalse(
                hasattr(processAdminSite, gone),
                f"processAdminSite still exposes {gone}",
            )
        self.assertFalse(hasattr(ModelRegistration, "_register_process_model"))

        for kept in ("html_reports", "registerHTMLReport", "get_html_report_func"):
            self.assertTrue(
                hasattr(processAdminSite, kept),
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
                StructureProbeModel: ModelProcessAdmin(),
                StructureProbeReport: ModelProcessAdmin(),
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
