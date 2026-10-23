"""permission_create on the record as the request fills it (LEX_SEC_CREATE).

Intent
------
``POST .../create`` asked ``permission_create`` only on a blank ``model_class()``,
before any request value was on it. A rule like "a fund manager may add an
investor only to her own fund" cannot be decided there: the parent is not set.
Projects re-checked creation themselves in ``pre_validation``.

With ``LEX_SEC_CREATE`` (on by default) the create endpoint asks
``permission_create`` twice:

* on the blank record, first, as before -- "may this caller create one at
  all" (what a UI asks before it offers a form); a refusal answers 401/403
  ``{message}`` without validating anything;
* on the record filled with the request's validated values, foreign keys set,
  before the audit entry and ``pre_validation``; a refusal answers the same
  401/403 ``{message}`` and stores nothing. A hook that raises there refuses
  the create with 500 (``LEX_SEC_FAIL_CLOSED``), as on the blank record.

``LEX_SEC_CREATE=false`` (or ``LEX_LEGACY_SECURITY=true``) keeps the blank
check only.

Cluster 4q -- scenarios 4.111-4.115. Type: E.
Covers: lex/core/security_flags.py (CREATE), lex/api/security.py
(filled_record, ask_create), lex/api/views/model_entries/One.py (create,
perform_create).
Run: python -m lex pytest lex/test_project/tests/permissions/test_4q_create_on_filled_record.py -v
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

from lex.audit_logging.models.AuditLog import AuditLog
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import CREATE_MODELS, SCOPED_CREATE, CreateParent, ScopedCreateItem

pytestmark = pytest.mark.permissions


def _env(**extra):
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("LEX_SEC_", "LEX_LEGACY_SECURITY"))}
    clean.update(extra)
    return mock.patch.dict(os.environ, clean, clear=True)


class TestCluster04q_CreateOnFilledRecord(E2ETestCase):
    e2e_models = CREATE_MODELS

    def setUp(self) -> None:
        super().setUp()
        self._defaults = _env()
        self._defaults.start()
        self.addCleanup(self._defaults.stop)
        ScopedCreateItem.SEEN.clear()
        self.open_parent = CreateParent.objects.create(name="open", is_open=True)
        self.closed_parent = CreateParent.objects.create(name="closed", is_open=False)

    def create(self, **body):
        return self.client.post(self.url_create(SCOPED_CREATE), body, format="json")

    def created_audit_entries(self):
        return AuditLog.objects.filter(resource__iexact=SCOPED_CREATE, action="create").count()

    # -- 4.111 ---------------------------------------------------------
    def test_4_111_refused_on_the_filled_record(self) -> None:
        """
        Scenario 4.111: a create the hook refuses once the parent is set answers 403.
        Given: permission_create allows the blank record, refuses a closed parent
        When: a child is created under the closed and under the open parent
        Then: closed -> 403 with the create refusal's {message}, nothing stored and
              no audit entry; open -> 201 and stored.
        """
        audit_before = self.created_audit_entries()
        response = self.create(name="child", parent=self.closed_parent.pk)
        self.assertEqual(response.status_code, 403, response.content[:300])
        self.assertEqual(
            response.json(),
            {"message": "You are not authorized to create a record in ScopedCreateItem"},
        )
        self.assertFalse(ScopedCreateItem.objects.exists())
        self.assertEqual(self.created_audit_entries(), audit_before)

        response = self.create(name="child", parent=self.open_parent.pk)
        self.assertIn(response.status_code, (200, 201), response.content[:300])
        self.assertTrue(ScopedCreateItem.objects.filter(parent=self.open_parent).exists())

    # -- 4.112 ---------------------------------------------------------
    def test_4_112_hook_sees_blank_then_filled_unsaved_record(self) -> None:
        """
        Scenario 4.112: the hook is asked on the blank record, then on the filled one.
        Given: permission_create notes every record it is asked on
        When: a child is created under the open parent
        Then: two calls -- (None, "", None) then (None, "child", <parent id>):
              the filled record carries the values and the foreign key, unsaved.
        """
        response = self.create(name="child", parent=self.open_parent.pk)
        self.assertIn(response.status_code, (200, 201), response.content[:300])
        self.assertEqual(
            ScopedCreateItem.SEEN[:2],
            [(None, "", None), (None, "child", self.open_parent.pk)],
        )

    # -- 4.113 ---------------------------------------------------------
    def test_4_113_refusal_by_plain_value(self) -> None:
        """
        Scenario 4.113: the filled record is refused on a plain field value too.
        Given: permission_create refuses the name "forbidden" (no parent)
        When: it is created
        Then: 403 and nothing stored.
        """
        response = self.create(name="forbidden")
        self.assertEqual(response.status_code, 403, response.content[:300])
        self.assertFalse(ScopedCreateItem.objects.exists())

    # -- 4.114 ---------------------------------------------------------
    def test_4_114_raising_hook_on_filled_record(self) -> None:
        """
        Scenario 4.114: a hook that raises on the filled record refuses the create.
        Given: permission_create raises for the name "boom" (blank record allowed)
        When: it is created by default and with LEX_SEC_FAIL_CLOSED=false
        Then: by default 500 with the create check's error and nothing stored;
              with fail-closed off the row is stored (the legacy allow).
        """
        response = self.create(name="boom")
        self.assertEqual(response.status_code, 500, response.content[:300])
        self.assertIn("The permission check for creating a ScopedCreateItem failed.", response.content.decode())
        self.assertFalse(ScopedCreateItem.objects.exists())
        with _env(LEX_SEC_FAIL_CLOSED="false"):
            response = self.create(name="boom")
        self.assertIn(response.status_code, (200, 201), response.content[:300])
        self.assertTrue(ScopedCreateItem.objects.filter(name="boom").exists())

    # -- 4.115 ---------------------------------------------------------
    def test_4_115_opt_out_keeps_the_blank_check_only(self) -> None:
        """
        Scenario 4.115: LEX_SEC_CREATE=false and the legacy switch keep the old behaviour.
        Given: the closed parent
        When: a child is created with LEX_SEC_CREATE=false, then with
              LEX_LEGACY_SECURITY=true
        Then: both are stored; the hook was asked on the blank record only.
        """
        with _env(LEX_SEC_CREATE="false"):
            response = self.create(name="first", parent=self.closed_parent.pk)
        self.assertIn(response.status_code, (200, 201), response.content[:300])
        with _env(LEX_LEGACY_SECURITY="true"):
            response = self.create(name="second", parent=self.closed_parent.pk)
        self.assertIn(response.status_code, (200, 201), response.content[:300])
        self.assertEqual(ScopedCreateItem.objects.filter(parent=self.closed_parent).count(), 2)
        self.assertEqual(ScopedCreateItem.SEEN, [(None, "", None), (None, "", None)])
