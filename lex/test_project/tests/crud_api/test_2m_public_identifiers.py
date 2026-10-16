"""Records addressed by a public uuid (``lex_lookup_field``).

Intent
------
Integer primary keys are guessable and differ between systems, so a project
may address records by a public uuid instead. A model that sets
``lex_lookup_field = "uuid"`` (a unique ``UUIDField``) gets
``.../one/<uuid>`` and ``.../history/<uuid>`` beside the existing integer
routes, with the same read/edit/delete behaviour. ``lex_lookup_allow_pk =
False`` closes the integer routes for it. Response bodies — ids,
``id_field``, foreign keys — are unchanged, so the bundled frontend keeps
working. A misconfigured lookup field stops registration with an error.

Cluster 2m — scenarios 2.117–2.121. Type: E.
Covers: lex/process_admin/sites/process_admin_site.py (lookup routes),
lex/api/views/model_entries/One.py (get_object), History.py,
lex/api/views/file_operations/FileDownload.py (lookup=),
lex/process_admin/utils/model_registration.py (_validate_lookup_field).
Run: python -m lex pytest lex/test_project/tests/crud_api/test_2m_public_identifiers.py -v
"""

from __future__ import annotations

import uuid
from unittest import mock

import pytest
from django.urls import reverse

from lex.process_admin.utils.model_registration import ModelRegistration
from lex.test_project.tests._e2e_test_case import E2ETestCase, _MockContainer

from .models import PUBLIC_ID, PUBLIC_ID_MODELS, PublicIdItem

pytestmark = pytest.mark.crud_api


class TestCluster02m_PublicIdentifiers(E2ETestCase):
    """Cluster 2m: uuid routes."""

    e2e_models = PUBLIC_ID_MODELS

    def _by_uuid(self, item, view="model-one-entry-by-lookup"):
        return reverse(
            f"process_admin_rest_api:{view}",
            kwargs={"model_container": _MockContainer(PUBLIC_ID), "calculationId": "default", "lookup": item.uuid},
        )

    # -- 2.117 ---------------------------------------------------------
    def test_2_117_read_update_delete_by_uuid(self) -> None:
        """
        Scenario 2.117: a record is read, changed and deleted through its uuid.
        Given: a PublicIdItem
        When: it is fetched, patched and deleted at .../one/<uuid>
        Then: 200 with the same body as the integer route, the change applies,
              and the row is gone.
        """
        item = PublicIdItem.objects.create(name="first")
        by_uuid = self.client.get(self._by_uuid(item))
        self.assertEqual(by_uuid.status_code, 200)
        self.assertEqual(by_uuid.json(), self.client.get(self.url_detail(PUBLIC_ID, item.pk)).json())
        self.assertEqual(self.client.patch(self._by_uuid(item), {"name": "renamed"}, format="json").status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.name, "renamed")
        self.assertIn(self.client.delete(self._by_uuid(item)).status_code, (200, 204))
        self.assertFalse(PublicIdItem.objects.exists())

    # -- 2.118 ---------------------------------------------------------
    def test_2_118_unknown_uuid_is_not_found(self) -> None:
        """
        Scenario 2.118: an unknown uuid answers 404.
        Given: no row with that uuid
        When: it is fetched
        Then: HTTP 404.
        """
        ghost = PublicIdItem(name="ghost", uuid=uuid.uuid4())
        self.assertEqual(self.client.get(self._by_uuid(ghost)).status_code, 404)

    # -- 2.119 ---------------------------------------------------------
    def test_2_119_history_by_uuid(self) -> None:
        """
        Scenario 2.119: the history is available through the uuid too.
        Given: a PublicIdItem that was changed once
        When: .../history/<uuid> is requested
        Then: HTTP 200 with two versions.
        """
        item = PublicIdItem.objects.create(name="v1")
        item.name = "v2"
        item.save()
        response = self.client.get(self._by_uuid(item, view="model-history-list-by-lookup"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)

    # -- 2.120 ---------------------------------------------------------
    def test_2_120_integer_route_can_be_closed(self) -> None:
        """
        Scenario 2.120: lex_lookup_allow_pk = False closes the integer routes.
        Given: the model with lex_lookup_allow_pk = False
        When: the record is fetched by integer id and by uuid
        Then: the integer route answers 404, the uuid route 200; with the
              attribute unset both work (the default).
        """
        item = PublicIdItem.objects.create(name="only-uuid")
        self.assertEqual(self.client.get(self.url_detail(PUBLIC_ID, item.pk)).status_code, 200)
        PublicIdItem.lex_lookup_allow_pk = False
        self.addCleanup(delattr, PublicIdItem, "lex_lookup_allow_pk")
        self.assertEqual(self.client.get(self.url_detail(PUBLIC_ID, item.pk)).status_code, 404)
        self.assertEqual(self.client.get(self._by_uuid(item)).status_code, 200)

    # -- 2.121 ---------------------------------------------------------
    def test_2_121_lookup_field_must_be_a_unique_uuid(self) -> None:
        """
        Scenario 2.121: registration refuses a lookup field that is not a unique UUIDField.
        Given: the model with lex_lookup_field pointing at its CharField "name"
        When: lex validates the model definition
        Then: ValueError naming the attribute; the real uuid field validates.
        """
        ModelRegistration._validate_lookup_field(PublicIdItem)
        with mock.patch.object(PublicIdItem, "lex_lookup_field", "name"):
            with self.assertRaisesMessage(ValueError, "lex_lookup_field"):
                ModelRegistration._validate_lookup_field(PublicIdItem)
