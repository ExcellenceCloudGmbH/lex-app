"""Who may pick which serializer, and strict input on the generic endpoints.

Intent
------
``?serializer=<name>`` lets a client choose the shape of a response. A model
can now decide per caller which names are available
(``permission_serializer``); a refused name answers 400 exactly like an unknown
one, and ``available`` lists only what the caller may use. A project that
overrides ``"default"`` can stop exposing the generated all-fields serializer
under its alias (``lex_config.EXPOSE_AUTO_SERIALIZER = False``). The field
metadata endpoint answers 400, not 500, for an unknown serializer.

A model (or serializer) that sets ``lex_strict_input = True`` refuses keys it
does not declare and the fields lex sets itself (``created_by`` …) with 400
``UNKNOWN_FIELDS``; the keys the bundled frontend sends with every request
(``calculate``, ``id``, ``lex_reserved_*``) stay accepted. Models that do not
opt in keep ignoring unknown keys.

Cluster 12l — scenarios 12.63–12.67. Type: E.
Covers: lex/api/views/model_entries/mixins/ModelEntryProviderMixin.py,
lex/api/views/model_info/Fields.py, lex/api/serializers/base_serializers.py
(EXPOSE_AUTO_SERIALIZER), lex/api/security.py (serializer_allowed,
strict_input_denial), lex/api/views/model_entries/One.py.
Run: python -m lex pytest lex/test_project/tests/serializers/test_12l_serializer_exposure.py -v
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from lex.api.serializers.base_serializers import get_serializer_map_for_model
from lex.core import extensions
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import CHOOSY, EXPOSURE_MODELS, STRICT_INPUT, ChoosySerializerItem, StrictInputItem

pytestmark = pytest.mark.serializers


class _Container:
    def __init__(self, model_id):
        self.id = model_id


class TestCluster12l_SerializerExposure(E2ETestCase):
    """Cluster 12l: serializer choice and strict input."""

    e2e_models = EXPOSURE_MODELS

    # -- 12.63 ---------------------------------------------------------
    def test_12_63_refused_serializer_looks_unknown(self) -> None:
        """
        Scenario 12.63: a serializer the caller may not use answers like an unknown one.
        Given: ChoosySerializerItem with "default" and "full"; "full" for superusers only
        When: a regular user lists with ?serializer=full, then a superuser does
        Then: the regular user gets 400 whose "available" omits "full"; the
              superuser gets 200 including the margin field.
        """
        ChoosySerializerItem.objects.create(name="deal", margin=42)
        response = self.client.get(self.url_list(CHOOSY), {"serializer": "full"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["available"], ["default"])
        self.user.is_superuser = True
        self.user.save()
        response = self.client.get(self.url_list(CHOOSY), {"serializer": "full"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        rows = body["results"] if isinstance(body, dict) else body
        self.assertEqual(rows[0]["margin"], 42)

    # -- 12.64 ---------------------------------------------------------
    def test_12_64_auto_serializer_alias_can_be_withheld(self) -> None:
        """
        Scenario 12.64: EXPOSE_AUTO_SERIALIZER = False drops the generated alias.
        Given: a model overriding "default" and a configured alias name
        When: its serializer map is built with the key at its default and set to False
        Then: by default the alias is registered next to "default" and "full";
              with False only the project's own serializers remain.
        """
        from lex.core import config

        config.reset_default_serializer_name_cache()
        self.addCleanup(config.reset_default_serializer_name_cache)
        config._default_serializer_name_cache = "lex_generated"
        default_fields = [f.name for f in ChoosySerializerItem._meta.fields]
        self.assertIn("lex_generated", get_serializer_map_for_model(ChoosySerializerItem, default_fields))
        with extensions.override_extensions(EXPOSE_AUTO_SERIALIZER=False):
            names = set(get_serializer_map_for_model(ChoosySerializerItem, default_fields))
        self.assertEqual(names, {"default", "full"})

    # -- 12.65 ---------------------------------------------------------
    def test_12_65_fields_endpoint_answers_400_for_unknown_serializer(self) -> None:
        """
        Scenario 12.65: field metadata for an unknown serializer is a client error.
        Given: ChoosySerializerItem
        When: its fields are requested with ?serializer=nope and, as a regular
              user, with ?serializer=full
        Then: both answer 400 (formerly 500).
        """
        url = reverse("process_admin_rest_api:model-info-fields", kwargs={"model_container": _Container(CHOOSY)})
        self.assertEqual(self.client.get(url, {"serializer": "nope"}).status_code, 400)
        self.assertEqual(self.client.get(url, {"serializer": "full"}).status_code, 400)
        self.assertEqual(self.client.get(url).status_code, 200)

    # -- 12.66 ---------------------------------------------------------
    def test_12_66_strict_input_refuses_unknown_and_server_set_keys(self) -> None:
        """
        Scenario 12.66: lex_strict_input refuses keys the model does not take.
        Given: StrictInputItem (lex_strict_input = True)
        When: a create sends an unknown key, then created_by, then only known
              keys plus the frontend's id and lex_reserved_scopes
        Then: 400 UNKNOWN_FIELDS naming the key, 400 naming created_by as set
              by the server, and finally 201.
        """
        url = self.url_create(STRICT_INPUT)
        response = self.client.post(url, {"name": "a", "colour": "red"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["fields"], {"colour": "Unknown field."})
        response = self.client.post(url, {"name": "a", "created_by": "mallory"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["fields"], {"created_by": "Set by the server."})
        response = self.client.post(url, {"name": "a", "id": None, "lex_reserved_scopes": {}}, format="json")
        self.assertIn(response.status_code, (200, 201), response.content[:300])
        self.assertTrue(StrictInputItem.objects.filter(name="a").exists())

    # -- 12.67 ---------------------------------------------------------
    def test_12_67_models_without_opt_in_ignore_unknown_keys(self) -> None:
        """
        Scenario 12.67: without lex_strict_input nothing changes.
        Given: ChoosySerializerItem (no opt-in)
        When: a create sends an unknown key
        Then: it is created and the key is ignored, as before.
        """
        response = self.client.post(self.url_create(CHOOSY), {"name": "b", "colour": "red"}, format="json")
        self.assertIn(response.status_code, (200, 201), response.content[:300])
