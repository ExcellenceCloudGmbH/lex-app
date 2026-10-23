"""The Process and ProcessFlow endpoints are gone from the route table.

Intent
------
``Process`` (an abstract workflow a project subclassed to describe its steps)
and the ProcessFlow step endpoint were removed from the framework. Two public
routes went with them:

* ``api/run_step/<model>/<pk>`` — the ProcessFlow ``CreateOrUpdate`` view, a
  second write path into every model that carried its own copy of the
  permission checks the generic CRUD endpoints apply. A route that comes back
  by accident is a write surface nobody is auditing any more, so its absence
  is worth pinning.
* ``api/process/<process_name>`` — the ``ProcessStructure`` view, which called
  ``get_structure()`` on a registered ``Process`` class. With no ``Process``
  class left the route can only 500.

The removal must also be *surgical*: the neighbouring routes in the same
``LexAdminSite`` url list are what the bundled frontend loads a project
with, and taking one of those out with the ProcessFlow cascade would break
every screen. A customer upgrading across this change expects exactly two
endpoints to disappear and nothing else.

The same sweep renamed the URL *namespace* — ``process_admin_rest_api`` →
``lex_rest_api`` — and 10.100 pins that it took no **served route** with it. The
namespace is server-side only (it is what ``reverse()`` takes); the ``api/…``
paths are the contract with the bundled frontend and are unchanged. The one
path-shaped string that did follow the rename is ``LOGIN_REDIRECT_URL``, and
it is dead configuration: nothing in lex-app reads it, the OIDC library does
not either, and the shipped frontend bundle contains no route by either
spelling — so 10.100 pins its new value rather than pretending it was load
bearing.

Cluster 10q — scenarios 10.97–10.100. Type: U.
Covers: lex/model_admin/sites/lex_admin_site.py (the url list),
lex/api/views/process_flow/ (deleted), lex/model_admin/views/__init__.py and
lex/api/views/ModelStructureObtainView.py (``ProcessStructure`` deleted).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10q_process_routes_removed.py -v
"""

from __future__ import annotations

import importlib

import pytest
from django.test import SimpleTestCase
from django.urls import NoReverseMatch, get_resolver, reverse
from django.urls.resolvers import URLResolver

pytestmark = pytest.mark.api_layer


def _routes():
    """Every ``(name, path)`` the project's URLconf serves, namespaces included."""
    names, paths = set(), set()
    stack = list(get_resolver().url_patterns)
    while stack:
        entry = stack.pop()
        if isinstance(entry, URLResolver):
            stack.extend(entry.url_patterns)
            continue
        if entry.name:
            names.add(entry.name)
        paths.add(str(entry.pattern))
    return names, paths


class TestCluster10q_ProcessRoutesRemoved(SimpleTestCase):
    """Cluster 10q: the Process and ProcessFlow endpoints no longer exist."""

    # -- 10.97 ---------------------------------------------------------
    def test_10_97_run_step_is_not_routed(self) -> None:
        """
        Scenario 10.97: the ProcessFlow step endpoint is gone.
        Given: the project's URLconf after the ProcessFlow removal
        When: ``run_step`` is reversed, the route table is read, and the view
              module is imported
        Then: reversing raises NoReverseMatch, no route is named ``run_step``
              or sits under ``api/run_step/``, and
              ``lex.api.views.process_flow`` no longer imports.
        """
        with self.assertRaises(NoReverseMatch):
            reverse("lex_rest_api:run_step",
                    kwargs={"model_container": "x", "pk": 1})

        names, paths = _routes()
        self.assertNotIn("run_step", names, "the ProcessFlow route came back")
        self.assertEqual(
            [p for p in paths if p.startswith("api/run_step")], [],
            "no path may be served under api/run_step/",
        )

        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("lex.api.views.process_flow")
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("lex.api.views.process_flow.CreateOrUpdate")

    # -- 10.98 ---------------------------------------------------------
    def test_10_98_process_structure_is_not_routed(self) -> None:
        """
        Scenario 10.98: the process-structure endpoint is gone.
        Given: the same URLconf
        When: ``process`` is reversed, the route table is read, and
              ``ProcessStructure`` is looked up on both modules that defined it
        Then: reversing raises NoReverseMatch, no route is named ``process`` or
              sits under ``api/process/``, and neither module exports
              ``ProcessStructure`` any more.
        """
        with self.assertRaises(NoReverseMatch):
            reverse("lex_rest_api:process",
                    kwargs={"process_name": "datapipeline"})

        names, paths = _routes()
        self.assertNotIn("process", names, "the process-structure route came back")
        self.assertEqual(
            [p for p in paths if p.startswith("api/process/")], [],
            "no path may be served under api/process/",
        )

        views = importlib.import_module("lex.model_admin.views")
        structure = importlib.import_module("lex.api.views.ModelStructureObtainView")
        for module in (views, structure):
            self.assertFalse(
                hasattr(module, "ProcessStructure"),
                f"{module.__name__} still exposes ProcessStructure",
            )
        self.assertNotIn("ProcessStructure", views.__all__)

    # -- 10.99 ---------------------------------------------------------
    def test_10_99_the_surrounding_routes_are_untouched(self) -> None:
        """
        Scenario 10.99: only the two Process routes went.
        Given: the same URLconf
        When: the endpoints the bundled frontend loads a project with are
              reversed
        Then: every one of them still resolves — the removal took out the
              ProcessFlow cascade and nothing around it.
        """
        for name in (
            "model-structure",
            "project-info",
            "calculationlog-tree",
            "init-calculation-logs",
            "clean-calculations",
            "widget-structure",
            "global-search-query",
            "user-permissions",
            "log",
        ):
            with self.subTest(route=name):
                self.assertTrue(
                    reverse(f"lex_rest_api:{name}"),
                    f"{name} must still be routed",
                )

        # The HTML report endpoint is the sibling feature of the deleted
        # process endpoint — it takes the same shape of argument and must
        # have survived the removal.
        self.assertTrue(
            reverse("lex_rest_api:htmlreports",
                    kwargs={"report_name": "anyreport"})
        )

    # -- 10.100 --------------------------------------------------------
    def test_10_100_the_namespace_was_renamed_but_no_url_moved(self) -> None:
        """
        Scenario 10.100: the namespace lost the Process name; no route moved.
        Given: the URLconf after ``process_admin_rest_api`` became ``lex_rest_api``
        When: a route is reversed under both namespaces, the served paths are
              read, and ``LOGIN_REDIRECT_URL`` is read from settings
        Then: the old namespace raises NoReverseMatch, the new one reverses to
              the same ``/api/model-structure`` it always did, no served path
              carries the app name under either spelling, and the dead
              ``LOGIN_REDIRECT_URL`` follows the new one.
        """
        from django.conf import settings

        with self.assertRaises(NoReverseMatch):
            reverse("process_admin_rest_api:model-structure")
        self.assertEqual(reverse("lex_rest_api:model-structure"), "/api/model-structure")

        _, paths = _routes()
        stale = [p for p in paths if "process_admin" in p or "model_admin" in p]
        self.assertEqual(stale, [], f"no served path may carry the app name: {stale}")

        self.assertEqual(settings.LOGIN_REDIRECT_URL, "/model_admin/all")
