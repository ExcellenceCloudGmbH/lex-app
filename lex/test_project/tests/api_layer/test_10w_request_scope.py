"""The scope a request acts in: ``lex_config.REQUEST_SCOPE``.

Intent
------
A user of a multi-tenant project often works *as* one of their organisations
(``?organisation=<id>``). That choice must hold on every generic endpoint the
same way: the project's resolver decides whether the caller may use the value
(otherwise 404, as for anything outside their access), the scope reaches the
database context so policies and views can use it, a model's
``lex_scope_queryset`` narrows its lists, records, history and action lookups
(a row outside the scope answers 404), and the parameter is never mistaken
for a list filter. Without the parameter the resolver is still asked, so it
can choose a default.

Cluster 10w — scenarios 10.128–10.131. Type: U + E.
Covers: lex/core/request_scope.py (config, resolve, scope_queryset, apply_context),
lex/core/db_context.py (apply with scope), lex/api/views/model_entries/List.py (reserved parameter),
lex/api/views/model_entries/mixins/ModelEntryProviderMixin.py (get_queryset, initial),
lex/api/views/model_entries/History.py (_record_readable), lex/api/actions.py (_resolve_instance),
lex/core/extensions.py (REQUEST_SCOPE).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10w_request_scope.py -v
"""

from __future__ import annotations

import os
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework.exceptions import NotFound

from lex.core import extensions, request_scope
from lex.test_project.tests._e2e_test_case import E2ETestCase, _MockContainer

from .models import ACTION_ITEM, ACTION_MODELS, ActionItem

# These scenarios are strict mode's behaviour: pinned, whatever LEX_LEGACY_SECURITY the run sets (a legacy run keeps
# the pre-3.0 behaviour, which other clusters cover).
_STRICT = mock.patch.dict(os.environ, {"LEX_LEGACY_SECURITY": "false"})


def setUpModule():
    _STRICT.start()


def tearDownModule():
    _STRICT.stop()

pytestmark = pytest.mark.api_layer

SCOPE = {"param": "workspace", "resolver": f"{__name__}.resolve_workspace"}
CONTEXT_CALLS = []


def resolve_workspace(request, value):
    """Test resolver: "forbidden" is refused; without a value the scope is "default"."""
    if value == "forbidden":
        raise NotFound()
    return value or "default"


def record_context(user, *, local=True, scope=None):
    CONTEXT_CALLS.append(scope)


def narrow(queryset, scope, request):
    """ActionItem rows belong to the workspace their name starts with."""
    if scope == "default":
        return queryset
    return queryset.filter(name__startswith=f"{scope}-")


class TestCluster10w_ScopeUnit(SimpleTestCase):
    """Cluster 10w (unit): configuration and the reserved parameter."""

    # -- 10.128 --------------------------------------------------------
    def test_10_128_configuration_and_reserved_parameter(self) -> None:
        """
        Scenario 10.128: the parameter is the scope's, not a filter.
        Given: REQUEST_SCOPE naming "workspace", and a malformed value
        When: the list filter reads ?workspace=acme&name=x, and validation sees the bad config
        Then: only name filters; the malformed REQUEST_SCOPE stops startup.
        """
        with extensions.override_extensions(REQUEST_SCOPE=SCOPE):
            self.assertEqual(request_scope.param(), "workspace")
        with extensions.override_extensions(REQUEST_SCOPE={"param": "workspace"}):
            with self.assertRaises(ImproperlyConfigured):
                extensions.validate_configuration()


class TestCluster10w_RequestScope(E2ETestCase):
    """Cluster 10w (end to end): the scope on lists, records, history and actions."""

    e2e_models = ACTION_MODELS

    def setUp(self) -> None:
        super().setUp()
        CONTEXT_CALLS.clear()
        self.use_lex_extensions(REQUEST_SCOPE=SCOPE, DB_CONTEXT=f"{__name__}.record_context")
        patch = mock.patch.object(ActionItem, "lex_scope_queryset", staticmethod(narrow), create=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.acme = ActionItem.objects.create(name="acme-invoice")
        self.globex = ActionItem.objects.create(name="globex-invoice")

    def _action(self, item):
        return reverse(
            "process_admin_rest_api:model-record-action",
            kwargs={"model_container": _MockContainer(ACTION_ITEM), "calculationId": "c", "pk": item.pk, "action": "approve"},
        )

    def _names(self, response):
        body = response.json()
        rows = body["results"] if isinstance(body, dict) else body
        return sorted(row["name"] for row in rows)

    def test_10_128b_the_parameter_filters_nothing(self) -> None:
        """
        Scenario 10.128 (end to end): ?workspace=acme&name=x filters by name only.
        Given: REQUEST_SCOPE naming "workspace"
        When: apply_query_param_filters reads both parameters
        Then: the query filters on "name" and never on the scope's value.
        """
        from django.http import QueryDict

        from lex.api.views.model_entries.List import apply_query_param_filters

        filtered = apply_query_param_filters(ActionItem.objects.all(), QueryDict("workspace=acme&name=x"), ActionItem)
        sql = str(filtered.query)
        self.assertIn('"name"', sql)
        self.assertNotIn("acme", sql)

    # -- 10.129 --------------------------------------------------------
    def test_10_129_resolver_decides_and_the_context_hears_it(self) -> None:
        """
        Scenario 10.129: a refused value is 404; an accepted one reaches DB_CONTEXT.
        Given: the resolver refusing "forbidden"
        When: the list is read with workspace=forbidden, then workspace=acme
        Then: 404 for the first; the second answers and DB_CONTEXT received scope "acme".
        """
        self.assertEqual(self.client.get(self.url_list(ACTION_ITEM), {"workspace": "forbidden"}).status_code, 404)
        self.assertEqual(self.client.get(self.url_list(ACTION_ITEM), {"workspace": "acme"}).status_code, 200)
        self.assertIn("acme", CONTEXT_CALLS)

    # -- 10.130 --------------------------------------------------------
    def test_10_130_the_scope_narrows_every_way_to_a_row(self) -> None:
        """
        Scenario 10.130: outside the scope a row does not exist.
        Given: an acme and a globex row, workspace=acme
        When: the list, both records, both histories and the action on both are requested
        Then: the list holds acme only; the globex record, its history and its action answer 404;
              the acme ones answer.
        """
        scope = {"workspace": "acme"}
        self.assertEqual(self._names(self.client.get(self.url_list(ACTION_ITEM), scope)), ["acme-invoice"])
        self.assertEqual(self.client.get(self.url_detail(ACTION_ITEM, self.acme.pk), scope).status_code, 200)
        self.assertEqual(self.client.get(self.url_detail(ACTION_ITEM, self.globex.pk), scope).status_code, 404)
        self.assertEqual(self.client.get(self.url_history(ACTION_ITEM, self.globex.pk), scope).status_code, 404)
        self.assertEqual(self.client.get(self.url_history(ACTION_ITEM, self.acme.pk), scope).status_code, 200)
        self.assertEqual(
            self.client.post(self._action(self.globex) + "?workspace=acme", {"note": "x"}, format="json").status_code, 404
        )
        self.assertEqual(
            self.client.post(self._action(self.acme) + "?workspace=acme", {"note": "x"}, format="json").status_code, 200
        )

    # -- 10.131 --------------------------------------------------------
    def test_10_131_without_the_parameter_the_resolver_chooses(self) -> None:
        """
        Scenario 10.131: no parameter, the resolver's default.
        Given: the resolver's default "default", which narrows nothing
        When: the list is read without the parameter
        Then: both rows, and DB_CONTEXT received "default".
        """
        self.assertEqual(self._names(self.client.get(self.url_list(ACTION_ITEM))), ["acme-invoice", "globex-invoice"])
        self.assertIn("default", CONTEXT_CALLS)
