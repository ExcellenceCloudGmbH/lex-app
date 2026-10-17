"""The public shape of the generic API: short paths, uuid relations, no lex fields, documented.

Intent
------
When a project's own clients and integrators use lex's generic endpoints, the
API must look like the project's, not like lex's internals: paths without a
``calculationId`` segment (lex assigns one per request), records and their
relations named by public uuid (``?folder=<uuid>`` filters, an unknown or
invisible uuid is 404), responses that are exactly the project's positive
list (``lex_api_internal_fields = False``: no ``id``, ``id_field``,
``short_description``, ``lex_reserved_scopes`` or ``__short_description``),
and an OpenAPI document with one operation per exposed model, operation and
action — stable operation ids, uuid path parameters, the action's input and
output, the project's error body — that a typed client is generated from.

Cluster 10z — scenarios 10.144–10.149, 10.151. Type: E.
Covers: lex/process_admin/sites/process_admin_site.py (short routes, _with_calculation_id),
lex/api/serializers/relations.py (LexLookupRelatedField), lex/api/views/model_entries/List.py
(_resolve_lookup by public id), lex/api/serializers/base_serializers.py (_wrap_without_internal_fields),
lex/api/generic_schema.py, lex/api/schema.py (include_configured_paths), lex/api/actions.py (many).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10z_public_generic_api.py -v
"""

from __future__ import annotations

import uuid

import pytest
from django.urls import reverse

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.test_project.tests._e2e_test_case import E2ETestCase, _MockContainer

from .models import (ACTION_ITEM, ACTION_MODELS, PUBLIC_API_MODELS, PUBLIC_DOCUMENT, PUBLIC_FOLDER, ActionItem,
                     PublicApiDocument, PublicApiFolder)

pytestmark = pytest.mark.api_layer

SCHEMA = {
    "title": "Public API",
    "version": "1.0.0",
    "generic": {"short_paths": True},
    "error_component": "lex.test_project.tests.api_layer.models.PublicErrorSerializer",
}


def _short(name, model, **kwargs):
    return reverse(f"process_admin_rest_api:{name}-short", kwargs={"model_container": _MockContainer(model), **kwargs})


class TestCluster10z_PublicGenericApi(E2ETestCase):
    """Cluster 10z: what a project's clients see of the generic API."""

    e2e_models = PUBLIC_API_MODELS + ACTION_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]

    def setUp(self) -> None:
        super().setUp()
        self.inbox = PublicApiFolder.objects.create(name="Inbox")
        self.archive = PublicApiFolder.objects.create(name="Archive")

    # -- 10.144 --------------------------------------------------------
    def test_10_144_short_paths(self) -> None:
        """
        Scenario 10.144: the record routes without calculationId.
        Given: the document model addressed by uuid
        When: a document is created, read, changed, renamed by action and its history read on short paths
        Then: 201, 200, 200, 200, 200; the audit entries carry calculation ids lex assigned (api_…).
        """
        created = self.client.post(_short("model-one-entry-create", PUBLIC_DOCUMENT),
                                   {"title": "Plan", "folder": str(self.inbox.uuid)}, format="json")
        self.assertEqual(created.status_code, 201, created.content[:300])
        doc = PublicApiDocument.objects.get(title="Plan")
        one = _short("model-one-entry-by-lookup", PUBLIC_DOCUMENT, lookup=doc.uuid)
        self.assertEqual(self.client.get(one).status_code, 200)
        self.assertEqual(self.client.patch(one, {"title": "Plan B"}, format="json").status_code, 200)
        rename = _short("model-record-action-by-lookup", PUBLIC_DOCUMENT, lookup=doc.uuid, action="rename")
        self.assertEqual(self.client.post(rename, {"title": "Plan C"}, format="json").json(), {"title": "Plan C"})
        history = self.client.get(_short("model-history-list-by-lookup", PUBLIC_DOCUMENT, lookup=doc.uuid))
        self.assertEqual(history.status_code, 200, history.content[:300])
        ids = set(AuditLog.objects.filter(resource="publicapidocument").values_list("calculation_id", flat=True))
        self.assertTrue(ids and all(value.startswith("api_") for value in ids), ids)

    # -- 10.145 --------------------------------------------------------
    def test_10_145_responses_are_the_projects_positive_list(self) -> None:
        """
        Scenario 10.145: no lex fields in what clients see.
        Given: lex_api_internal_fields = False and serializers naming uuid, title, folder
        When: a document is listed and read
        Then: each row has exactly uuid, title and folder — the folder as its uuid.
        """
        doc = PublicApiDocument.objects.create(title="Memo", folder=self.inbox)
        listed = self.client.get(self.url_list(PUBLIC_DOCUMENT)).json()
        rows = listed["results"] if isinstance(listed, dict) else listed
        self.assertEqual(rows, [{"uuid": str(doc.uuid), "title": "Memo", "folder": str(self.inbox.uuid)}])
        record = self.client.get(_short("model-one-entry-by-lookup", PUBLIC_DOCUMENT, lookup=doc.uuid)).json()
        self.assertEqual(set(record), {"uuid", "title", "folder"})

    # -- 10.146 --------------------------------------------------------
    def test_10_146_relations_by_public_id(self) -> None:
        """
        Scenario 10.146: a relation is written, read and filtered by uuid.
        Given: two folders with one document each
        When: the list is filtered by ?folder=<uuid>, a document is moved by uuid, and one is
              created with a folder uuid that does not exist
        Then: one row per folder; the move lands; the unknown folder answers 404 and nothing is created.
        """
        PublicApiDocument.objects.create(title="A", folder=self.inbox)
        moved = PublicApiDocument.objects.create(title="B", folder=self.archive)
        rows = self.client.get(self.url_list(PUBLIC_DOCUMENT), {"folder": str(self.inbox.uuid)}).json()
        rows = rows["results"] if isinstance(rows, dict) else rows
        self.assertEqual([row["title"] for row in rows], ["A"])
        one = _short("model-one-entry-by-lookup", PUBLIC_DOCUMENT, lookup=moved.uuid)
        self.assertEqual(self.client.patch(one, {"folder": str(self.inbox.uuid)}, format="json").status_code, 200)
        moved.refresh_from_db()
        self.assertEqual(moved.folder_id, self.inbox.pk)
        missing = self.client.post(_short("model-one-entry-create", PUBLIC_DOCUMENT),
                                   {"title": "C", "folder": str(uuid.uuid4())}, format="json")
        self.assertEqual(missing.status_code, 404, missing.content[:300])
        self.assertFalse(PublicApiDocument.objects.filter(title="C").exists())

    # -- 10.147 --------------------------------------------------------
    def test_10_147_one_operation_per_exposed_model_and_action(self) -> None:
        """
        Scenario 10.147: the OpenAPI document describes the exposed records.
        Given: SCHEMA["generic"], the folder exposed for list and read, the document for all five
               operations and two actions
        When: the schema is generated
        Then: exactly the exposed operations exist with stable ids on short paths with a {uuid}
              parameter; the GET action documents its query input, the POST action its body;
              404s use the project's error body; components carry no lex fields.
        """
        from lex.api.schema import generate_schema

        self.use_lex_extensions(SCHEMA=SCHEMA)
        schema = generate_schema()
        operations = {
            op["operationId"]: (path, method)
            for path, item in schema["paths"].items()
            for method, op in item.items()
            if isinstance(op, dict) and "operationId" in op
        }
        base = "/api/model_entries"
        expected = {
            "publicapifolder_list": (f"{base}/publicapifolder/list", "get"),
            "publicapifolder_retrieve": (f"{base}/publicapifolder/one/{{uuid}}", "get"),
            "publicapidocument_list": (f"{base}/publicapidocument/list", "get"),
            "publicapidocument_create": (f"{base}/publicapidocument/one", "post"),
            "publicapidocument_retrieve": (f"{base}/publicapidocument/one/{{uuid}}", "get"),
            "publicapidocument_update": (f"{base}/publicapidocument/one/{{uuid}}", "patch"),
            "publicapidocument_delete": (f"{base}/publicapidocument/one/{{uuid}}", "delete"),
            "publicapidocument_rename": (f"{base}/publicapidocument/one/{{uuid}}/action/rename", "post"),
            "publicapidocument_count": (f"{base}/publicapidocument/action/count", "get"),
            "publicapidocument_titles": (f"{base}/publicapidocument/action/titles", "get"),
        }
        for operation_id, location in expected.items():
            self.assertEqual(operations.get(operation_id), location, operation_id)
        self.assertNotIn("publicapifolder_create", operations)
        count = schema["paths"][f"{base}/publicapidocument/action/count"]["get"]
        self.assertIn("prefix", [p["name"] for p in count.get("parameters", [])])
        rename = schema["paths"][f"{base}/publicapidocument/one/{{uuid}}/action/rename"]["post"]
        self.assertIn("requestBody", rename)  # declared by dotted path, resolved on first use
        self.assertIn("RenamedOutput", rename["responses"]["200"]["content"]["application/json"]["schema"]["$ref"])
        self.assertEqual(rename["summary"], "Rename the document")
        retrieve = schema["paths"][f"{base}/publicapidocument/one/{{uuid}}"]["get"]
        self.assertIn("PublicError", retrieve["responses"]["404"]["content"]["application/json"]["schema"]["$ref"])
        document = schema["components"]["schemas"]["PublicDocument"]
        self.assertEqual(set(document["properties"]), {"uuid", "title", "folder"})
        list_params = [p["name"] for p in schema["paths"][f"{base}/publicapidocument/list"]["get"]["parameters"]]
        self.assertTrue({"folder", "title", "ordering"} <= set(list_params), list_params)

    # -- 10.148 --------------------------------------------------------
    def test_10_148_the_document_validates(self) -> None:
        """
        Scenario 10.148: a client can be generated from it.
        Given: the same configuration
        When: lex schema --validate --fail-on-warn writes the document
        Then: it succeeds without warnings.
        """
        import tempfile

        from django.core.management import call_command

        self.use_lex_extensions(SCHEMA=SCHEMA)
        with tempfile.NamedTemporaryFile(suffix=".yaml") as target:
            call_command("schema", "--validate", "--fail-on-warn", "--file", target.name)
            self.assertIn(b"publicapidocument_rename", open(target.name, "rb").read())

    # -- 10.149 --------------------------------------------------------
    def test_10_149_an_action_answers_with_a_list(self) -> None:
        """
        Scenario 10.149: an action whose answer is a list says so.
        Given: a collection action declared with output=_RenamedOutput and many=True
        When: the schema is generated and the action is called with two documents
        Then: its 200 response is an array of RenamedOutput; the call answers both titles in order.
        """
        from lex.api.schema import generate_schema

        self.use_lex_extensions(SCHEMA=SCHEMA)
        operation = generate_schema()["paths"]["/api/model_entries/publicapidocument/action/titles"]["get"]
        answer = operation["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(answer["type"], "array")
        self.assertIn("RenamedOutput", answer["items"]["$ref"])
        PublicApiDocument.objects.create(title="B", folder=self.inbox)
        PublicApiDocument.objects.create(title="A", folder=self.archive)
        listed = self.client.get(_short("model-collection-action", PUBLIC_DOCUMENT, action="titles"))
        self.assertEqual(listed.json(), [{"title": "A"}, {"title": "B"}])

    # -- 10.151 --------------------------------------------------------
    def test_10_151_a_model_without_a_lookup_field_on_the_short_paths(self) -> None:
        """
        Scenario 10.151: the short paths the document names exist for an integer key too.
        Given: ActionItem, without lex_lookup_field
        When: one row is read, its history listed and a record action run on the short paths
        Then: 200 each; the routes resolve to the -short names the generic gate maps.
        """
        from django.urls import resolve

        item = ActionItem.objects.create(name="draft one")
        base = f"/api/model_entries/{ACTION_ITEM}/one/{item.pk}"
        self.assertEqual(resolve(base).url_name, "model-one-entry-read-update-delete-short")
        self.assertEqual(resolve(f"{base}/history").url_name, "model-history-list-short")
        self.assertEqual(resolve(f"{base}/action/ping").url_name, "model-record-action-short")
        read = self.client.get(base)
        self.assertEqual(read.status_code, 200, read.content[:300])
        self.assertEqual(self.client.get(f"{base}/history").status_code, 200)
        self.assertEqual(self.client.post(f"{base}/action/ping", {}, format="json").json(), {"pong": True})
