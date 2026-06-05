"""
Cluster 10f: Global search — the ``/search/<query>/`` endpoint.

Targets ``lex.api.views.global_search_for_models.Search`` (28 stmts,
34.21% baseline). The endpoint walks every registered model, runs a
PostgreSQL ``SearchVector`` full-text query across text fields, and
returns per-match payloads that the frontend drops into the nav
search bar.

Intent (from the view + docs/features/api-layer/):

    * Text fields on LexModels are searched; system models
      (``user``, ``permission``, ``calculationlog``, …) are excluded.
    * Fields that can't be matched against a text query (float, FK,
      file) are silently skipped — a search for "100" must not try
      to ``search`` against a ``DecimalField``.
    * A match yields an object with ``id``, ``type``, ``model``,
      ``url``, and a ``content`` block the frontend renders.
    * Zero matches → a string sentinel response (``"No match found"``).

These scenarios drive ``Search.get`` with a synthetic ``model_collection``
to avoid coupling to the whole project-admin wiring — the view itself
has no state beyond ``model_collection``, so this is a fair surface to
test in isolation.

Scenario numbering matches
docs/test-plan/test-clusters.md § Planned Expansions → 10f.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lex.api.views.global_search_for_models.Search import (
    EXCLUDED_MODELS,
    EXCLUDED_TYPES,
    Search,
)
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, SchemaItem

import pytest

pytestmark = pytest.mark.api_layer


def _container(model_class, *, container_id=None, title=None):
    """Build a lightweight stand-in for :class:`ModelContainer`."""
    cid = container_id or model_class._meta.model_name
    return SimpleNamespace(
        id=cid,
        title=title or model_class.__name__,
        model_class=model_class,
    )


class TestCluster10f_GlobalSearch(E2ETestCase):
    """``Search.get`` customer-contract scenarios."""

    e2e_models = ALL_MODELS

    def _drive_search(self, query: str, containers):
        """Call ``Search.get`` with permissions forced open."""
        view = Search()
        view.model_collection = SimpleNamespace(all_containers=containers)
        view.kwargs = {"query": query}

        # Real request object (APIClient request) — the search view uses
        # it for the UserPermission check only. Patch UserPermission to
        # always allow; 10.16 (permission-gated) is its own scenario.
        request = self.client.get("/").wsgi_request
        with patch(
            "lex.api.views.global_search_for_models.Search.UserPermission"
        ) as P:
            P.return_value.has_permission.return_value = True
            P.return_value.has_object_permission.return_value = True
            return view.get(request)

    # -- 10.15 ---------------------------------------------------------
    def test_10_15_search_matches_across_registered_models(self) -> None:
        """
        Scenario 10.15: A query that matches a text field on a
        registered model returns a payload with ``id``, ``model``,
        ``url``, and ``content`` — the exact shape the frontend reads.
        """
        # Seed two rows; only one should match.
        SchemaItem.objects.create(name="artichoke", amount=1)
        SchemaItem.objects.create(name="broccoli", amount=2)

        containers = [_container(SchemaItem, container_id="schemaitem")]
        resp = self._drive_search("artichoke", containers)

        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(
            resp.data, dict,
            "A hit must return the `{data, total}` payload, not the "
            "`No match found` sentinel string",
        )
        self.assertEqual(resp.data["total"], 1)
        hit = resp.data["data"][0]
        self.assertEqual(hit["model"], "schemaitem")
        self.assertEqual(hit["content"]["description"], "artichoke")
        self.assertIn(
            f"/{hit['model']}/", hit["url"],
            "Hit URL must be prefixed with the container id so the "
            "frontend can route to the correct detail page",
        )

    # -- 10.15b --------------------------------------------------------
    def test_10_15b_no_match_returns_sentinel_string(self) -> None:
        """
        Scenario 10.15b: An unmatched query returns the documented
        ``"No match found"`` string, not a dict with ``total=0``. The
        frontend branches on the response type — a silent contract
        change here would surface as "search always says 'no match'".
        """
        SchemaItem.objects.create(name="artichoke", amount=1)
        containers = [_container(SchemaItem, container_id="schemaitem")]

        resp = self._drive_search("carrot_no_hit_xyz", containers)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.data, "No match found",
            msg=f"Empty-result contract broke: got {resp.data!r}",
        )

    # -- 10.16 ---------------------------------------------------------
    def test_10_16_system_models_are_excluded_from_search(self) -> None:
        """
        Scenario 10.16: A container whose ``id`` is in
        ``EXCLUDED_MODELS`` (``user``, ``permission``, …) must not
        appear in the search results — even if its rows contain the
        query term.

        We disguise our ``SchemaItem`` container as ``"user"`` so the
        registry sees a hit-worthy row, but the exclusion filter
        short-circuits before the query runs. If the exclusion ever
        drifts, a user's email or group name could leak into the
        global search.
        """
        SchemaItem.objects.create(name="nimble", amount=1)

        containers = [_container(SchemaItem, container_id="user")]
        self.assertIn(
            "user", EXCLUDED_MODELS,
            "Precondition: 'user' must be in EXCLUDED_MODELS",
        )

        resp = self._drive_search("nimble", containers)

        self.assertEqual(
            resp.data, "No match found",
            msg=(
                "EXCLUDED_MODELS contract broke: search returned "
                f"results for a container whose id is in the exclusion "
                f"set. resp={resp.data!r}"
            ),
        )


class TestCluster10f_ExclusionConstants(unittest.TestCase):
    """Sanity on the excluded-type list."""

    # -- 10.16b --------------------------------------------------------
    def test_10_16b_non_text_field_types_are_excluded(self) -> None:
        """
        Scenario 10.16b: Field types that cannot be full-text searched
        must be in ``EXCLUDED_TYPES``. A regression here would cause
        ``SearchVector`` to choke on a ``DecimalField`` or ``FileField``
        at query time — the customer sees a 500, not a clean "no match".
        """
        for required in ("FloatField", "BooleanField", "IntegerField",
                         "FileField", "ForeignKey"):
            self.assertIn(
                required, EXCLUDED_TYPES,
                msg=(
                    f"{required!r} must stay in EXCLUDED_TYPES — "
                    "SearchVector cannot index it and a query would 500"
                ),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

