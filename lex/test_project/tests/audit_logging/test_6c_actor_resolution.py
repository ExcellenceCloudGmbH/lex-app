"""
Cluster 6c: Actor resolution on ``created_by`` / ``edited_by``.

Intent (from docs/reference/LexModel Internals.md):

    The framework stamps every :class:`LexModel` with an actor:
      - authenticated user → user email (or username fallback)
      - API-key caller     → ``"Technical User"``
      - no context         → ``"Initial Data Upload"``

    These fields are the observable audit trail on every record, even
    without the ``AuditLog`` table. Tests here assert the fallback and
    authenticated paths directly on the model.

Scenario numbering matches
docs/test-plan/test-clusters.md#6-audit-logging.
"""

from __future__ import annotations

import unittest

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, AUDIT_SIMPLE, AuditSimpleItem

import pytest

pytestmark = pytest.mark.audit_logging


class TestCluster06c_ActorResolution(E2ETestCase):
    """Actor resolution for created_by / edited_by."""

    e2e_models = ALL_MODELS

    # -- 6.7 -----------------------------------------------------------
    @unittest.expectedFailure  # BUG-007 (open): actor not populated via API create — sibling of BUG-004; revisit once the authenticated-context stamping step is reviewed
    def test_6_7_authenticated_user_becomes_created_by(self) -> None:
        """
        Scenario 6.7: Authenticated API caller → ``created_by`` is
        the user email (or username).

        Linked to BUG-004 (edited_at not populated on create via POST) —
        actor resolution on the API path is part of the same stamping
        step and may regress similarly.
        """
        resp = self.client.post(
            self.url_create(AUDIT_SIMPLE),
            data={"name": "a6-7", "value": 1}, format="json",
        )
        self.assertIn(resp.status_code, (200, 201))

        item = AuditSimpleItem.objects.get(name="a6-7")
        self.assertTrue(
            item.created_by,
            "created_by must be resolved from the authenticated user — "
            f"got {item.created_by!r}",
        )
        self.assertIn(
            "e2e", (item.created_by or "").lower(),
            "created_by should derive from the e2e test user's email/username",
        )

    # -- 6.8 -----------------------------------------------------------
    def test_6_8_api_key_becomes_technical_user(self) -> None:
        """
        Scenario 6.8: API-key caller → ``created_by = 'Technical User'``.

        Same contract as Scenario 2.7, asserted from Cluster-6's
        perspective (the audit-actor chain). We drive the real REST
        path with the API-key fixture so the view, DRF permissions,
        ``UserContext._api_key_context`` and
        :meth:`LexModel._resolve_audit_actor` all participate — no
        direct model save, no mock on actor resolution.
        """
        self.authenticate_as_api_key(name="Technical User")

        resp = self.client.post(
            self.url_create(AUDIT_SIMPLE),
            data={"name": "a6-8", "value": 8}, format="json",
        )
        self.assertIn(
            resp.status_code, (200, 201),
            f"API-key POST must succeed; got {resp.status_code}: "
            f"{getattr(resp, 'data', resp.content)!r}",
        )

        item = AuditSimpleItem.objects.get(name="a6-8")
        self.assertEqual(
            item.created_by, "Technical User",
            "API-key actor must land on created_by as the configured "
            f"key name — got {item.created_by!r}.",
        )

    # -- 6.9 -----------------------------------------------------------
    def test_6_9_no_context_falls_back_to_initial_data_upload(self) -> None:
        """
        Scenario 6.9: No request context (ORM save outside request) →
        ``created_by`` falls back to ``"Initial Data Upload"``.

        This is the seed-loader / management-command path and must be
        stable.
        """
        item = AuditSimpleItem(name="a6-9", value=1)
        item.save()

        self.assertEqual(
            item.created_by, "Initial Data Upload",
            "Without request context, created_by must fall back to the "
            f"documented 'Initial Data Upload' string; got {item.created_by!r}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

