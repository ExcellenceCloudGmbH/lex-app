"""Cluster 15a — LexLogger builder API and content shape."""
from __future__ import annotations

from . import _CalcLogTestCase


class TestCluster15a_BuilderAPI(_CalcLogTestCase):
    def test_smoke_setup_works(self):
        """Sanity: setUp seeded an AuditLog and a calculation_id."""
        self.assertTrue(self.calc_id.startswith("calc_15_"))
        self.assertEqual(self.audit_log.calculation_id, self.calc_id)
