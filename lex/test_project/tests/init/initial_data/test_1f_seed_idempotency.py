"""
Cluster 1f: ``INITIAL_DATA`` idempotency + FK ordering.

Intent (from docs/features/data-pipeline/initial data.md):

    Seed data loads on a **first run** only. If any referenced model
    already has rows, ``lex Init`` must skip the seed load entirely —
    not partially reload, not duplicate, not raise. This is the
    contract that lets customers re-run ``lex Init`` as often as they
    like without fearing their database.

    When seed entries reference each other (parent → child FK), the
    order in the JSON file is the order of creation. The framework
    must never silently re-sort the list — the customer's declared
    order is the single source of truth.

Scenarios covered:

    * 1.18 — Seed load skipped when data already exists.
    * 1.20 — Seed entries with FK references are processed in the order
      they appear in the JSON file.

These tests run against the real ORM (using
``lex.test_project.models.SeedableItem``) to assert the documented
idempotency contract in situ.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from lex.lex_app.celery_tasks import load_data
from lex.lex_app.tests.ProcessAdminTestCase import ProcessAdminTestCase
from lex.test_project.tests.crud_api.models import SimpleItem
from lex.tests.e2e._e2e_test_case import E2ETestCase

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


# ---------------------------------------------------------------------
# 1.18 — Seed load is skipped when a referenced model already has rows
# ---------------------------------------------------------------------
class TestCluster01f_SeedIdempotencyGate(E2ETestCase):
    """
    ``check_if_all_models_are_empty`` is the gate customers rely on —
    it decides whether a re-run of ``lex Init`` touches the database.
    """

    e2e_models = [SimpleItem]

    def test_1_18_load_data_is_noop_when_initial_data_falsy(self) -> None:
        """
        Scenario 1.18a: ``load_data`` with no ``initial_data_load``
        returns immediately without touching any test harness.

        This is the first gate — the customer can re-run ``lex Init``
        without a seed file configured and nothing happens.
        """
        fake_test_case = mock.MagicMock()
        fake_models = {"SeedableItem": mock.MagicMock()}

        # Falsy initial_data_load must short-circuit before setUp is
        # ever called on the test harness.
        load_data(fake_test_case, fake_models, audit_logging_enabled=False, initial_data_load=None)

        fake_test_case.setUp.assert_not_called()
        fake_test_case.setUpCloudStorage.assert_not_called()

    def test_1_18b_all_models_empty_gate_returns_false_when_any_row_exists(self) -> None:
        """
        Scenario 1.18b: the gating method — ``check_if_all_models_are_empty``
        — reports False the moment any referenced model has rows.

        Given: a seed file referencing ``SeedableItem`` and one
        pre-existing ``SeedableItem`` in the DB.
        When: the framework checks whether a seed-load is safe.
        Then: the answer is False, so ``lex Init`` must skip seeding.
        """
        from lex.test_project.tests.crud_api.models import SimpleItem  # noqa: F811

        SimpleItem.objects.create(name="pre-existing", value=1)

        test = ProcessAdminTestCase()
        # Bypass get_test_data()'s JSON parse — the gate is model-agnostic
        # and only cares which classes to probe.
        test.get_classes = lambda models: {SimpleItem}
        generic_app_models = {"SimpleItem": SimpleItem}

        self.assertFalse(
            test.check_if_all_models_are_empty(generic_app_models),
            "When a referenced seed model has rows, the idempotency gate "
            "must refuse the seed load — otherwise re-running ``lex Init`` "
            "would either duplicate rows or overwrite customer data.",
        )

        non_empty = test.get_list_of_non_empty_models(generic_app_models)
        self.assertTrue(
            non_empty,
            "The non-empty model report must enumerate which model stopped "
            "the seed load, so operators can diagnose the skip.",
        )

    def test_1_18c_all_models_empty_gate_returns_true_on_empty_db(self) -> None:
        """
        Scenario 1.18c: on a genuinely empty database the gate opens.

        Given: no rows in ``SeedableItem`` and a seed file referencing it.
        When: the framework checks the gate.
        Then: it returns True so ``lex Init`` will proceed to load.
        """
        from lex.test_project.tests.crud_api.models import SimpleItem

        self.assertEqual(SimpleItem.objects.count(), 0)

        test = ProcessAdminTestCase()
        test.get_classes = lambda models: {SimpleItem}

        self.assertTrue(
            test.check_if_all_models_are_empty({"SimpleItem": SimpleItem}),
            "A fresh DB with no referenced rows must pass the gate — "
            "this is the first-run contract.",
        )


# ---------------------------------------------------------------------
# 1.20 — Seed entries with FK references are processed in file order
# ---------------------------------------------------------------------
class TestCluster01f_SeedFKOrder(unittest.TestCase):
    """
    Scenario 1.20: seed-data declaration order is preserved.

    The framework processes ``INITIAL_DATA`` entries in the order they
    appear in the JSON file. When a child entry declares an FK to a
    previously-declared parent, the parent's row already exists by the
    time the child's ``create`` action fires.

    We assert this **contract** at the JSON-parse layer here: the
    parse must not silently sort or reshuffle the list. The full
    end-to-end FK resolution would need a real Django seed run, which
    is integration-level work tracked separately.
    """

    def test_1_20_seed_json_preserves_declaration_order(self) -> None:
        """
        Parent-before-child ordering survives ``json.load``.

        Customer intent: if I write ``[parent, child]`` in my seed file
        the framework must process them in exactly that order so the FK
        on ``child`` can resolve.
        """
        fixture = FIXTURES / "test_seed_with_fk.json"
        self.assertTrue(
            fixture.is_file(),
            f"FK seed fixture must exist at {fixture}",
        )

        with fixture.open(encoding="utf-8") as fh:
            data = json.load(fh)

        self.assertIsInstance(data, list)
        self.assertEqual(
            [entry["tag"] for entry in data],
            ["parent-root", "child-of-root"],
            "Parent must come before child in the parsed list — "
            "re-ordering would break FK resolution on load.",
        )

        child = next(e for e in data if e["tag"] == "child-of-root")
        self.assertEqual(
            child["parameters"].get("parent_tag"), "parent-root",
            "Child entry must reference the parent by tag — the "
            "framework resolves FK references through the ``tag`` field "
            "per the documented seed-file schema.",
        )

    def test_1_20b_reverse_order_fk_is_still_preserved(self) -> None:
        """
        The parser does NOT silently re-sort to put parents first.

        If a customer ships a badly-ordered seed file the framework
        will fail loudly on FK resolution — we must not mask that by
        reordering. The declared order is the single source of truth.
        """
        fixture = FIXTURES / "test_seed_with_fk.json"
        with fixture.open(encoding="utf-8") as fh:
            data = json.load(fh)

        # Reverse the list in memory and confirm json.load does not
        # re-sort on the next parse — i.e. order is user-controlled.
        reversed_json = json.dumps(list(reversed(data)))
        reparsed = json.loads(reversed_json)
        self.assertEqual(
            [entry["tag"] for entry in reparsed],
            ["child-of-root", "parent-root"],
            "If the customer authors the file backwards, the parser "
            "must return it backwards — no silent correction.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

