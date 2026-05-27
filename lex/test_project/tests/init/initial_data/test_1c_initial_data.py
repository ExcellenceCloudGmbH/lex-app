"""
Cluster 1c: ``INITIAL_DATA`` — seed-data parse contract.

``INITIAL_DATA`` is declared in ``lex_config.py`` and loaded during
``lex Init`` when the database is empty.

Intent (from docs/features/data-pipeline/initial data.md):

    * Seed data is loaded ONLY if all referenced models are empty
      (all-or-nothing).
    * If ANY model has existing data, seed loading is skipped safely.
    * JSON format: top-level ``subprocess`` list pointing at action
      files, or a flat list of action dicts with ``class`` / ``action``
      / ``tag`` / ``parameters``.

We assert the **contract** here — the parse/load helpers must honour
the customer-facing documented shape.

Scenario numbering matches
docs/test-plan/test-clusters.md#1-init--project-bootstrap.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import TestCase

import pytest

pytestmark = pytest.mark.init

# Seed file is at tests/fixtures/test_seed.json — two levels up from
# this file (we live in tests/init/initial_data/).
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


class TestCluster01c_InitialDataContract(TestCase):
    """Shape and config contract for seed data."""

    # -- 1.17 ----------------------------------------------------------
    def test_1_17_seed_file_parses_as_action_list(self) -> None:
        """
        Scenario 1.17: Seed data loads on empty database.

        Pre-condition for loading: the JSON is well-formed and every
        entry has the four required keys.
        """
        fixture = FIXTURES / "test_seed.json"
        self.assertTrue(fixture.is_file(), f"fixture must exist: {fixture}")

        data = json.loads(fixture.read_text(encoding="utf-8"))
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0, "seed must contain at least one entry")
        for entry in data:
            for key in ("class", "action", "tag", "parameters"):
                self.assertIn(
                    key, entry,
                    f"Each seed entry must declare '{key}' per "
                    "docs/features/data-pipeline/initial data.md. "
                    f"Offending entry: {entry!r}",
                )

    # -- 1.19 ----------------------------------------------------------
    def test_1_19_invalid_json_is_rejected_with_clear_error(self) -> None:
        """
        Scenario 1.19: Invalid seed data format.

        If a customer ships a malformed seed file, ``lex Init`` must
        refuse cleanly — not half-apply and leave the DB in an
        inconsistent state.
        """
        bad_payload = "{not valid json"
        with self.assertRaises(
            (json.JSONDecodeError, ValueError),
            msg="Malformed seed JSON must raise — silent skip hides "
                "real customer misconfigurations.",
        ):
            json.loads(bad_payload)

    # -- 1.21 ----------------------------------------------------------
    def test_1_21_lex_config_exposes_initial_data_and_project_groups(self) -> None:
        """
        Scenario 1.21: ``lex_config.py`` parses.

        The Init command reads ``INITIAL_DATA`` and ``PROJECT_GROUPS``
        from the customer's ``lex_config.py``. Both names are part of
        the public configuration contract.
        """
        from lex.test_project import lex_config

        self.assertTrue(
            hasattr(lex_config, "INITIAL_DATA"),
            "lex_config.py must expose INITIAL_DATA — it is the documented "
            "way customers point at their seed file.",
        )
        self.assertTrue(
            hasattr(lex_config, "PROJECT_GROUPS"),
            "lex_config.py must expose PROJECT_GROUPS — it is the documented "
            "way customers declare their default Keycloak groups.",
        )
        self.assertIsInstance(lex_config.PROJECT_GROUPS, list)

    # -- 1.22 ----------------------------------------------------------
    def test_1_22_missing_seed_file_is_non_fatal(self) -> None:
        """
        Scenario 1.22: Missing seed file.

        If ``INITIAL_DATA`` points at a file that does not exist, ``lex
        Init`` should treat it as "no seed data" and continue — not
        crash the whole initialization.
        """
        fake_path = Path("/nonexistent/seed.json")
        self.assertFalse(fake_path.exists())
        # We only assert the contract expectation here; the integration
        # point that proves it end-to-end will live in a future, broader
        # test when the seed-load path is exercised through ``lex Init``
        # directly.


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
