"""
Cluster 10d: Global search with the Reflex report registered.

Targets ``lex.api.views.global_search_for_models.Search``.

Intent (from the view, lex/lex_app/reflex/Reflex.py and
lex/utilities/config/generic_app_config.py):

    With ``IS_REFLEX_ENABLED=true`` lex-app registers a ``Reflex`` report
    beside ``Streamlit`` -- a page that frames the project's Reflex
    dashboards. Like ``Streamlit`` it is an ``HTMLReport``, not a model:
    it has no table, no fields and no rows. The global search walks every
    registered container, so it must skip the report the way it skips
    ``streamlit``; otherwise enabling Reflex would turn every search in
    the frontend into a server error.

Scenario numbering matches
docs/test-plan/test-clusters.md#10-api-layer.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lex.api.views.global_search_for_models.Search import Search
from lex.lex_app.reflex.Reflex import Reflex
from lex.process_admin.models.ModelContainer import ModelContainer
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, SchemaItem

pytestmark = pytest.mark.api_layer


class TestCluster10d_ReflexSearchExclusion(E2ETestCase):
    """``Search.get`` over a registry that holds the Reflex report."""

    e2e_models = ALL_MODELS

    def _drive_search(self, query: str, containers):
        """Call ``Search.get`` with permissions forced open (10.16 covers the gate)."""
        view = Search()
        view.model_collection = SimpleNamespace(all_containers=containers)
        view.kwargs = {"query": query}
        request = self.client.get("/").wsgi_request
        with patch("lex.api.views.global_search_for_models.Search.UserPermission") as P:
            P.return_value.has_permission.return_value = True
            P.return_value.has_object_permission.return_value = True
            return view.get(request)

    # -- 10.82 ---------------------------------------------------------
    def test_10_82_search_skips_the_reflex_report(self) -> None:
        """
        Scenario 10.82: the Reflex report never reaches the search query.
        Given: the registry lex-app builds with Reflex enabled -- the
               ``Reflex`` report's container beside a searchable model with a
               matching row.
        When:  the frontend's global search runs.
        Then:  it answers with the model's match alone: the report's
               container (id ``reflex``, as lex-app registers it) is skipped
               like ``streamlit``, not queried as if it had a table.
        """
        item = SchemaItem.objects.create(name="quince", amount=1)
        report = ModelContainer(Reflex, process_admin=SimpleNamespace(name="reflex"))
        self.assertEqual(report.id, "reflex", "the id lex-app registers the report under")
        searchable = SimpleNamespace(id="schemaitem", title="Schema Item", model_class=SchemaItem)

        resp = self._drive_search("quince", [report, searchable])

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["total"], 1, resp.data)
        self.assertEqual(
            [(m["model"], m["id"]) for m in resp.data["data"]], [("schemaitem", str(item.pk))]
        )
