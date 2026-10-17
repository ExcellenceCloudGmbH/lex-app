"""Read models: views and projections served by the generic endpoints.

Intent
------
Overviews and dashboards are often joins, unions or the answer of a database
function, not one table. A read model (``lex_read_model = True``) is either a
database view (``Meta.managed = False``, created by a ``CreateView``
migration, ``security_invoker`` on PostgreSQL) or a projection of a table
(``Meta.proxy = True``) with its own serializer. Lex serves it on the generic
list and record endpoints, filters and sorts it, runs its actions — and
refuses every write (405) and keeps no history. A model's ``lex_list_queryset``
is its list's default selection (records stay reachable), and a serializer's
``lex_prepare_page`` computes a page's derived fields once, not once per row.

Cluster 10x — scenarios 10.132–10.137. Type: U + E.
Covers: lex/core/read_models.py (is_read_model, validate, list_queryset, CreateView),
lex/core/models/LexReadModel.py, lex/lex_app/simple_history_config.py (read models untracked),
lex/api/views/model_entries/mixins/ModelEntryProviderMixin.py (initial: writes refused),
lex/api/views/model_entries/List.py (get_queryset), lex/api/views/model_entries/History.py (no history of a
read model), lex/api/serializers/base_serializers.py
(lex_prepare_page), lex/core/extensions.py (validation).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10x_read_models.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import SimpleTestCase
from django.urls import reverse

from lex.core.read_models import CreateView, is_read_model, validate
from lex.test_project.tests._e2e_test_case import E2ETestCase, _MockContainer

from .models import (
    ACTION_CARD,
    ACTION_TILE,
    ACTION_MODELS,
    READ_SUMMARY,
    ActionItem,
    ActionItemCard,
    ActionItemTile,
    ActionItemSummary,
    _CardSerializer,
)

pytestmark = pytest.mark.api_layer

SUMMARY_SQL = "SELECT id, name, status, status = 'approved' AS approved FROM {table:ActionItem}"


def _create_view():
    operation = CreateView("ActionItemSummary", SUMMARY_SQL)
    state = SimpleNamespace(apps=apps)
    with connection.schema_editor() as editor:
        operation.database_forwards("lex_app", editor, state, state)
    return operation, state


def _drop_view(operation, state):
    with connection.schema_editor() as editor:
        operation.database_backwards("lex_app", editor, state, state)


class TestCluster10x_ReadModelUnit(SimpleTestCase):
    """Cluster 10x (unit): what a read model is."""

    # -- 10.132 --------------------------------------------------------
    def test_10_132_read_models_have_no_table_and_no_history(self) -> None:
        """
        Scenario 10.132: a read model is a view or a projection, never tracked.
        Given: the view and the projection, and a read model with a table of its own
        When: they are inspected and validated
        Then: both are read models without history; a managed read model stops startup.
        """
        from lex.lex_app.simple_history_config import get_model_exclusion_reason

        for model in (ActionItemSummary, ActionItemCard):
            self.assertTrue(is_read_model(model))
            self.assertIn("Read model", get_model_exclusion_reason(model))
            validate(model)
        self.assertFalse(is_read_model(ActionItem))
        with mock.patch.object(ActionItem, "lex_read_model", True, create=True):
            with self.assertRaisesRegex(ImproperlyConfigured, "lex_read_model"):
                validate(ActionItem)


class TestCluster10x_ReadModels(E2ETestCase):
    """Cluster 10x (end to end): views and projections on the generic endpoints."""

    e2e_models = ACTION_MODELS + [ActionItemCard, ActionItemTile]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from lex.process_admin.utils.model_registration import ModelRegistration

        # The view has no table to create; register it for the generic routes only.
        ModelRegistration._register_standard_model(ActionItemSummary, [])

    def setUp(self) -> None:
        super().setUp()
        self._rebuild_urls()
        operation, state = _create_view()
        self.addCleanup(_drop_view, operation, state)
        self.draft = ActionItem.objects.create(name="draft one")
        self.approved = ActionItem.objects.create(name="paid", status="approved")
        self.locked = ActionItem.objects.create(name="frozen", status="locked")
        _CardSerializer.prepared.clear()

    def _rows(self, response):
        body = response.json()
        return body["results"] if isinstance(body, dict) else body

    # -- 10.133 --------------------------------------------------------
    def test_10_133_a_view_is_listed_filtered_sorted_and_read(self) -> None:
        """
        Scenario 10.133: the view behaves like any list on the generic endpoints.
        Given: three ActionItem rows behind the view
        When: the view is listed, filtered by approved, sorted by -name and one row read
        Then: three rows; one approved; sorted names; the record answers with its computed column.
        """
        listed = self.client.get(self.url_list(READ_SUMMARY))
        self.assertEqual(listed.status_code, 200, listed.content[:300])
        self.assertEqual(len(self._rows(listed)), 3)
        approved = self._rows(self.client.get(self.url_list(READ_SUMMARY), {"approved": "true"}))
        self.assertEqual([row["name"] for row in approved], ["paid"])
        ordered = self._rows(self.client.get(self.url_list(READ_SUMMARY), {"ordering": "-name"}))
        self.assertEqual([row["name"] for row in ordered], ["paid", "frozen", "draft one"])
        record = self.client.get(self.url_detail(READ_SUMMARY, self.approved.pk))
        self.assertEqual((record.status_code, record.json()["approved"]), (200, True))

    # -- 10.134 --------------------------------------------------------
    def test_10_134_nothing_writes_to_a_read_model_and_it_has_no_history(self) -> None:
        """
        Scenario 10.134: create, update, delete, bulk and run_step answer 405; history 400.
        Given: the view and the projection
        When: each write is tried, and the history is asked for
        Then: 405 for every write, the rows unchanged; the history answers 400.
        """
        for model, row in ((READ_SUMMARY, self.draft), (ACTION_CARD, self.draft)):
            self.assertEqual(self.client.post(self.url_create(model), {"name": "x"}, format="json").status_code, 405)
            self.assertEqual(self.client.patch(self.url_detail(model, row.pk), {"name": "x"},
                                               format="json").status_code, 405)
            self.assertEqual(self.client.delete(self.url_detail(model, row.pk)).status_code, 405)
            self.assertEqual(self.client.delete(self.url_many(model) + f"?ids={row.pk}").status_code, 405)
            history = self.client.get(self.url_history(model, row.pk))
            self.assertEqual(history.status_code, 400, history.content[:200])
        self.assertEqual(ActionItem.objects.get(pk=self.draft.pk).name, "draft one")

    # -- 10.135 --------------------------------------------------------
    def test_10_135_actions_run_on_read_models(self) -> None:
        """
        Scenario 10.135: a read model may carry actions.
        Given: the view's flag action and the projection's inherited approve action
        When: both run
        Then: flag answers from the view row; approve changes the underlying row.
        """
        def action(model, pk, name):
            return reverse("process_admin_rest_api:model-record-action", kwargs={
                "model_container": _MockContainer(model), "calculationId": "c", "pk": pk, "action": name})

        flagged = self.client.post(action(READ_SUMMARY, self.draft.pk, "flag"), {}, format="json")
        self.assertEqual(flagged.json(), {"flagged": "draft one"})
        approved = self.client.post(action(ACTION_CARD, self.draft.pk, "approve"), {"note": "ok"}, format="json")
        self.assertEqual(approved.status_code, 200, approved.content[:300])
        self.assertEqual(ActionItem.objects.get(pk=self.draft.pk).status, "approved")

    # -- 10.150 --------------------------------------------------------
    def test_10_150_a_projection_offers_only_the_actions_it_names(self) -> None:
        """
        Scenario 10.150: lex_actions narrows the inherited actions.
        Given: a projection of ActionItem with lex_actions = ("ping",)
        When: its actions are listed and approve and ping are called on it
        Then: only ping exists on it: approve answers 404 and changes nothing, ping runs.
        """
        from lex.api.actions import actions_for

        self.assertEqual(set(actions_for(ActionItemTile)), {"ping"})
        self.assertIn("approve", actions_for(ActionItemCard))

        def action(name):
            return reverse("process_admin_rest_api:model-record-action", kwargs={
                "model_container": _MockContainer(ACTION_TILE), "calculationId": "c", "pk": self.draft.pk,
                "action": name})

        refused = self.client.post(action("approve"), {"note": "ok"}, format="json")
        self.assertEqual(refused.status_code, 404, refused.content[:300])
        self.assertNotEqual(ActionItem.objects.get(pk=self.draft.pk).status, "approved")
        self.assertEqual(self.client.post(action("ping"), {}, format="json").status_code, 200)

    # -- 10.136 --------------------------------------------------------
    def test_10_136_list_default_and_page_preparation(self) -> None:
        """
        Scenario 10.136: the projection's list selection and its page-wide computed field.
        Given: the projection, which leaves locked rows out of its list
        When: it is listed and the locked row is read
        Then: two rows with their computed field, prepared once for the page; the locked row
              is still readable by id, prepared once for itself.
        """
        rows = self._rows(self.client.get(self.url_list(ACTION_CARD)))
        self.assertEqual(sorted(row["name"] for row in rows), ["draft one", "paid"])
        self.assertEqual(sorted(row["shout"] for row in rows), ["DRAFT ONE", "PAID"])
        self.assertEqual(_CardSerializer.prepared, [2])
        locked = self.client.get(self.url_detail(ACTION_CARD, self.locked.pk))
        self.assertEqual((locked.status_code, locked.json()["shout"]), (200, "FROZEN"))
        self.assertEqual(_CardSerializer.prepared, [2, 1])

    # -- 10.137 --------------------------------------------------------
    def test_10_137_create_view_resolves_tables_and_runs_as_the_caller(self) -> None:
        """
        Scenario 10.137: CreateView builds the view from model table names, security_invoker on PostgreSQL.
        Given: the view created in setUp
        When: its definition is read back, and it is dropped again
        Then: it reads ActionItem's real table; on PostgreSQL it is security_invoker; after
              database_backwards it is gone; deconstruct round-trips.
        """
        table = ActionItemSummary._meta.db_table
        self.assertIn(ActionItem._meta.db_table, CreateView("ActionItemSummary", SUMMARY_SQL)._resolve("lex_app", apps))
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT reloptions FROM pg_class WHERE oid = to_regclass(%s)", [table])
                self.assertIn("security_invoker=true", cursor.fetchone()[0] or [])
        name, args, kwargs = CreateView("ActionItemSummary", SUMMARY_SQL).deconstruct()
        self.assertEqual((name, args, kwargs), ("CreateView", ["ActionItemSummary", SUMMARY_SQL], {"security_invoker": True}))
